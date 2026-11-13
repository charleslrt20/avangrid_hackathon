"""The tool's per-customer output: appliance inventory + program and battery eligibility, one row per customer.

Joins what the pipeline already computed (no new modeling):
  outputs/classify.parquet       appliance flags (05_classify)
  outputs/segments.parquet       business segment, shape archetype, anomaly (06_segment)
  outputs/bess_results.parquet   battery tier, size, relief, avoided $ (08_bess; battery candidates only)
  data_processed/features.parquet  annual kWh, peak, coincident-peak kW (04_features)
  insight_text.PLAYBOOK          segment -> recommended program (09_insights)

Writes
  outputs/customer_results.parquet and .csv   one row per customer (customer-level: gitignored, never shared)
  outputs/summary/program_eligibility.csv      counts per appliance / eligibility flag (aggregate: committed)

Eligibility flags are screening rules that restate the program playbook per home:
  battery_tier_1_2          Tier 1 or 2 of the battery priority ranking (the playbook's battery target)
  managed_ev_charging       home EV charging detected (L1 or L2)
  ac_thermostat_dr          central AC detected
  heat_dr                   electric heat detected (any type)
  heat_pump_conversion      resistive electric heat (largest per-home savings from a heat pump)
  pv_plus_storage           solar PV detected (exports show as zero midday readings)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from avangrid import config as C  # noqa: E402
from avangrid.insight_text import PLAYBOOK  # noqa: E402

TIER12 = ("Tier 1", "Tier 2")


def build() -> pd.DataFrame:
    cl = pd.read_parquet(C.OUTPUTS / "classify.parquet")
    sg = pd.read_parquet(C.OUTPUTS / "segments.parquet")
    bs = pd.read_parquet(C.OUTPUTS / "bess_results.parquet")
    ft = pd.read_parquet(C.DATA_PROC / "features.parquet",
                         columns=["customer_id", "annual_kwh", "peak_kw", "cp_kw_summer", "cp_kw_winter"])
    pb = pd.DataFrame(PLAYBOOK)[["segment", "program", "when"]].rename(columns={"when": "program_timing"})

    # every join is on customer_id: row order differs between these files
    df = (cl.merge(sg[["customer_id", "segment", "archetype_name", "anomaly"]], on="customer_id", how="left")
            .merge(ft, on="customer_id", how="left")
            .merge(pb, on="segment", how="left")
            .merge(bs[["customer_id", "tier", "size", "priority", "relief_kw_summer", "relief_kw_winter",
                       "firm_kw_summer", "firm_kw_winter", "avoided_usd_yr_mid", "dr_relief_kw_summer",
                       "dr_relief_kw_winter"]], on="customer_id", how="left"))
    assert len(df) == len(cl) and df.customer_id.is_unique

    out = pd.DataFrame({
        "customer_id": df.customer_id,
        "in_model": df.in_model,
        "segment": df.segment,
        "archetype": df.archetype_name,
        "anomaly": df.anomaly,
        "annual_kwh": df.annual_kwh.round(0),
        "peak_kw": df.peak_kw.round(2),
        "summer_peak_kw": df.cp_kw_summer.round(2),       # mean load in the system's top 88 summer hours
        "winter_peak_kw": df.cp_kw_winter.round(2),       # same for winter
        # heating and cooling inventory
        "heat_type": df.heat_type,
        "heat_kw_at_0f": df.heat_kw_design_0f.round(2),
        "ac_type": df.ac_type,
        "cool_kw_at_90f": df.cool_kw_design_90f.round(2),
        # other appliances
        "ev_class": df.ev_class,
        "ev_charger_kw": df.ev_charger_kw.round(1),
        "ev_confidence": df.ev_conf.replace("", pd.NA),
        "ev_rank_pct": df.ev_pu_pct.round(3),             # PU ranking percentile (1 = most EV-like), not a probability
        "hp_confidence": df.hp_conf.replace("", pd.NA),
        "hp_rank_pct": df.hp_pu_pct.round(3),
        "solar_pv": df.pv,
        "pv_with_battery": df.pv_battery,
        "pool_pump": df.pool,
        "water_heater_lowconf": df.water_heater_lowconf,
        "hot_tub_lowconf": df.hot_tub_lowconf,
        "label_ev": df.label_ev, "label_hp": df.label_hp, "label_pv": df.label_pv,
        # programs
        "program": df.program,
        "program_timing": df.program_timing,
        "battery_tier_1_2": df.tier.isin(TIER12),
        "managed_ev_charging": df.ev_class.isin(["L1 detectable", "L2 detectable"]),
        "ac_thermostat_dr": df.ac_type.eq("central"),
        "heat_dr": df.electric_heat.fillna(False).astype(bool),
        "heat_pump_conversion": df.heat_type.eq("resistive"),
        "pv_plus_storage": df.pv.fillna(False).astype(bool),
        # battery screening (blank = not a battery candidate)
        "battery_candidate": df.tier.notna(),
        "battery_tier": df.tier,
        "battery_size": df["size"],
        "battery_priority": df.priority.round(4),
        "battery_relief_kw_summer": df.relief_kw_summer.round(2),
        "battery_relief_kw_winter": df.relief_kw_winter.round(2),
        "battery_firm_kw_summer": df.firm_kw_summer.round(2),
        "battery_firm_kw_winter": df.firm_kw_winter.round(2),
        "avoided_usd_yr_mid": df.avoided_usd_yr_mid.round(0),   # placeholder $/kW-yr, see config.py
        "dr_relief_kw_summer": df.dr_relief_kw_summer.round(2),
        "dr_relief_kw_winter": df.dr_relief_kw_winter.round(2),
    })
    # program flags apply to the modeling population only (excluded meters get no offer)
    flags = ["battery_tier_1_2", "managed_ev_charging", "ac_thermostat_dr", "heat_dr", "heat_pump_conversion",
             "pv_plus_storage"]
    out.loc[~out.in_model.astype(bool), flags] = False
    return out.sort_values(["battery_priority", "customer_id"], ascending=[False, True], na_position="last")


def summary(out: pd.DataFrame) -> pd.DataFrame:
    m = out[out.in_model.astype(bool)]
    rows = [("customers (modeling population)", len(m))]
    rows += [(f"heat_type: {k}", v) for k, v in m.heat_type.value_counts().items()]
    rows += [(f"ac_type: {k}", v) for k, v in m.ac_type.value_counts().items()]
    rows += [(f"ev_class: {k}", v) for k, v in m.ev_class.value_counts().items()]
    for c in ["solar_pv", "pv_with_battery", "pool_pump", "water_heater_lowconf", "hot_tub_lowconf",
              "battery_tier_1_2", "managed_ev_charging", "ac_thermostat_dr", "heat_dr", "heat_pump_conversion",
              "pv_plus_storage", "battery_candidate"]:
        rows.append((c, int(m[c].fillna(False).astype(bool).sum())))
    rows += [(f"battery_tier: {k}", v) for k, v in m.battery_tier.value_counts().sort_index().items()]
    s = pd.DataFrame(rows, columns=["item", "customers"])
    s["share"] = (s.customers / len(m)).round(4)
    return s


def main() -> None:
    out = build()
    out.to_parquet(C.CUSTOMER_RESULTS, index=False)
    out.to_csv(C.CUSTOMER_RESULTS.with_suffix(".csv"), index=False)
    s = summary(out)
    s.to_csv(C.OUTPUTS_SUMMARY / "program_eligibility.csv", index=False)
    print(f"wrote {C.CUSTOMER_RESULTS} (+ .csv): {len(out):,} customers x {out.shape[1]} columns")
    print(s.to_string(index=False))


if __name__ == "__main__":
    main()
