"""Segment cards and program playbook text (task 2.4), written by Claude at build time from aggregate facts.

Rules (checked by scripts/09_insights.py):
  * No literal numbers in the prose. Every number is a {placeholder} filled from insights.py, so it traces
    to outputs/. The only literal digits allowed are names and fixed specs (L1/L2, Tier 1-3, 1 unit /
    2 units, 3 kVA, office-hours Q1-Q5).
  * Every qualitative claim ("higher in winter", "batteries beat DR here") has a check: a Python expression
    over the same facts that must be True, so a rerun cannot leave a claim the data no longer supports.

Placeholders: segment facts (insights.segment_facts columns) and global facts (insights.global_facts keys).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- segment cards
CARDS: dict[str, dict] = {
    "Electrified: EV + electric heat": {
        "headline": "The flagship battery segment: {share_customers:.0%} of homes, {share_cp_w:.0%} of the winter peak.",
        "who": ("{customers:,} homes with both a home EV charger ({l2:.0%} L2) and electric space heat "
                "({hp:.0%} heat pump, {resistive:.0%} resistive, the rest type unclear). Median use "
                "{median_kwh:,.0f} kWh/yr."),
        "why": ("Each home adds {cp_w_kw:.1f} kW to the winter system peak and {cp_s_kw:.1f} kW to the summer one: "
                "{idx_w:.1f}x its share of customers in winter. {also_winter_peak:.0%} are also in the top {peak_q_pct:.0%} of "
                "winter peak contributors."),
        "program": ("Battery + managed charging bundle. {tier1:,} of these homes are Tier 1 ({tier1_pct:.0%} of the "
                    "segment), giving {relief_w_kw:.2f} kW of realistic winter relief per home; managed EV charging "
                    "and thermostat DR add {dr_w_kw:.2f} kW in winter, more than the battery alone. Avoided buildout "
                    "≈ ${usd_mid:,.0f}/home/yr at mid placeholder values."),
        "watch": ("{two_units_dr:.0%} need 2 units plus DR to hold their own peak through the full event window: "
                  "a battery alone does not cover a long winter evening here."),
        "checks": ["idx_w > idx_s", "dr_w_kw > relief_w_kw", "tier1 / bess_candidates > 0.4", "two_units_dr > 0.3",
                   "idx_w == max_idx_w_residential"],
    },
    "Electric-heat home": {
        "headline": "The winter peak today: {share_customers:.0%} of homes carry {share_cp_w:.0%} of it.",
        "who": ("{customers:,} homes heated with electricity: {hp:.0%} heat pump, {resistive:.0%} resistive, "
                "{eheat_unclear:.0%} electric but type unclear at hourly resolution. Median use {median_kwh:,.0f} kWh/yr."),
        "why": ("They add {cp_w_kw:.1f} kW per home to the winter peak vs {cp_s_kw:.1f} kW in summer. The NMF "
                "fingerprint confirms it: a winter daytime heating part ({nmf_top_desc}) is {nmf_top_share:.0%} of "
                "their energy, {nmf_top_delta_pts:.0f} points above the average home."),
        "program": ("Largest Tier 1 pool in absolute numbers ({tier1:,} homes; {tier12:,} in Tier 1+2, "
                    "{relief_w_mw_t12:.1f} MW winter relief). Pair batteries with heat-pump conversion of "
                    "resistive homes and weatherisation: thermostat DR alone gives only {dr_w_kw:.2f} kW per home "
                    "net of rebound."),
        "watch": ("Their share of the winter peak falls to {wcp_30:.0%} in a {hp_top:.0%} heat-pump future, not because "
                  "they shrink but because new heat-pump load elsewhere grows faster."),
        "checks": ["idx_w > 1.5 * idx_s", "share_cp_w > 2 * share_customers", "dr_w_kw < relief_w_kw",
                   "tier1 == max_tier1", "wcp_30 < wcp_today",
                   "abs(wcp_30 * wcp_mw_30 / (wcp_today * wcp_mw_today) - 1) < 0.1", "nmf_top_desc.startswith('winter')"],
    },
    "EV home": {
        "headline": "Summer-peaking EV homes: managed charging first, batteries second.",
        "who": ("{customers:,} homes with a home-charging signature ({l2:.0%} L2, {l1:.0%} L1) and no electric "
                "heat; {central_ac:.0%} have central AC. {labeled_ev:,} carry an EV label."),
        "why": ("{cp_s_kw:.1f} kW per home at the summer peak, {idx_s:.1f}x their customer share; "
                "{also_summer_peak:.0%} are also top-{peak_q_pct:.0%} summer peak contributors."),
        "program": ("Managed charging / EV TOU: moving charging out of the peak window gives {dr_s_kw:.2f} kW per "
                    "home in summer, as much as a battery's {relief_s_kw:.2f} kW (2 units), at a fraction of the cost. "
                    "Offer batteries to the {tier1:,} Tier 1 homes, where both stack."),
        "watch": ("L2 charging blocks are short and irregular, so relief depends on enrolment rules (charging "
                  "windows), not on forecasting. Only part of the labeled EVs show home charging at all."),
        "checks": ["idx_s > idx_w", "dr_s_kw >= 0.95 * relief_s_kw", "l2 > l1"],
    },
    "Summer peak contributor": {
        "headline": "Air-conditioning load: {share_customers:.0%} of homes, {share_cp_s:.0%} of the summer peak.",
        "who": ("{customers:,} homes in the top {peak_q_pct:.0%} at the summer peak with no EV or electric heat; "
                "{central_ac:.0%} have central AC."),
        "why": ("{cp_s_kw:.1f} kW per home at the summer peak ({idx_s:.1f}x their customer share) but only "
                "{cp_w_kw:.1f} kW in winter: they matter today, less in a winter-peaking future "
                "({wcp_30:.0%} of the winter peak at {hp_top:.0%} heat pumps)."),
        "program": ("Batteries outperform AC cycling here: {relief_s_kw:.2f} kW vs {dr_s_kw:.2f} kW per home "
                    "after the post-event rebound. {tier2:,} are Tier 2 (no Tier 1: none are electrified). "
                    "Run AC thermostat DR as the low-cost mass offer and target batteries where a feeder is summer-constrained."),
        "watch": ("{two_units_dr:.0%} need 2 units plus DR for a full heat-wave evening; pre-cooling rebound must be "
                  "staggered or it sets a new evening peak."),
        "checks": ["idx_s > 1.5", "idx_w < 1.2", "wcp_30 > wcp_today", "relief_s_kw > 2 * dr_s_kw", "tier1 == 0", "central_ac > 0.9"],
    },
    "Winter peak contributor": {
        "headline": "High winter load with no electric heat detected: big homes, not heat pumps.",
        "who": ("{customers:,} homes in the top {peak_q_pct:.0%} at the winter peak without a detected EV or electric-heat "
                "signature; {also_summer_peak:.0%} are also top summer contributors. Median {median_kwh:,.0f} kWh/yr."),
        "why": ("{cp_w_kw:.1f} kW at the winter peak and {cp_s_kw:.1f} kW at the summer peak: large, evenly "
                "loaded homes whose evening load lines up with both system peaks."),
        "program": ("Year-round battery value: {relief_w_kw:.2f} kW winter and {relief_s_kw:.2f} kW summer relief per "
                    "home, {tier2:,} in Tier 2. Thermostat DR does nothing here in winter (no electric heat), so the "
                    "battery is the only lever. Good heat-pump-ready audits: they likely heat with fuel."),
        "watch": "Why their winter load is high (fuel-fired air handlers, home office, unknown) is not resolved by hourly data.",
        "checks": ["idx_w > 1.5", "idx_s > 1.5", "dr_w_kw == 0", "median_kwh > 2 * typical_median_kwh", "tier2 > tier1 + tier3"],
    },
    "Solar PV": {
        "headline": "PV homes still peak in winter evenings: {idx_w:.1f}x their share of the winter peak.",
        "who": ("{customers:,} homes whose midday readings drop to zero in sunny months ({labeled_pv:,} on the utility's PV list). "
                "{eheat:.0%} also heat with electricity and {ev:.0%} charge an EV."),
        "why": ("Solar cuts their summer-peak load to {cp_s_kw:.1f} kW ({idx_s:.1f}x share) but the winter peak "
                "falls after dark: {cp_w_kw:.1f} kW per home."),
        "program": ("PV + storage retrofit aimed at winter evenings: battery relief is higher in winter "
                    "({relief_w_kw:.2f} kW) than summer ({relief_s_kw:.2f} kW). {tier12:,} are in Tier 1+2."),
        "watch": ("Meters are delivered-only, so we see net import, not generation or true load; storage "
                  "self-consumption estimates are a lower bound (office-hours Q2)."),
        "checks": ["idx_w > 1", "idx_s < 1", "relief_w_kw > relief_s_kw", "cp_w_kw > cp_s_kw"],
    },
    "Emerging load": {
        "headline": "Load growing month over month: tomorrow's EV and heat-pump homes.",
        "who": ("{customers:,} homes whose weather-normalised load rose steadily through the year without a "
                "detected EV or electric-heat signature yet."),
        "why": ("Small today ({share_cp_s:.1%} of the summer peak) but their NMF mix has {nmf_top_delta_pts:.0f} points more "
                "year-round daytime load ({nmf_top_desc}) than the average home: new occupancy or new equipment."),
        "program": ("Watch-list, not battery program: none are Tier 1. Flag their transformers for a load check, "
                    "offer EV / heat-pump readiness outreach (the 3 kVA rule) and re-screen in six months."),
        "watch": "A trend over one year can also be a change of occupants; confirm before acting on any single home.",
        "checks": ["tier1 == 0", "share_cp_s < 0.01", "nmf_top_delta > 0.05"],
    },
    "Very high load / non-residential": {
        "headline": "A handful of accounts that behave like small businesses.",
        "who": ("{customers:,} accounts reviewed one by one in task 1.0b and kept as plausible; median "
                "{median_kwh:,.0f} kWh/yr. {anomaly:.0%} are also flagged as statistical anomalies."),
        "why": ("{cp_w_kw:.1f} kW each at the winter peak ({idx_w:.1f}x their customer share) and "
                "{cp_s_kw:.1f} kW in summer."),
        "program": ("Rate-class and account review before any residential offer; {two_units_dr:.0%} would need "
                    "2 units plus DR even for their own peak, so they belong in a commercial storage or C&I DR program."),
        "watch": "Too few to move the system ({share_cp_w:.1%} of the winter peak); value is per-feeder, not system-wide.",
        "checks": ["idx_w > 3", "two_units_dr > 0.5", "share_cp_w < 0.01"],
    },
    "Low use / seasonal": {
        "headline": "Vacation homes and very small loads: no program.",
        "who": "{customers:,} homes using under the low-use threshold or with long zero-load stretches; median {median_kwh:,.0f} kWh/yr.",
        "why": "{share_customers:.0%} of homes but {share_cp_s:.1%} of the summer and {share_cp_w:.1%} of the winter peak.",
        "program": "Excluded from battery and DR screening. Useful only to the vacancy and meter-health teams.",
        "watch": "Some may be stuck or misread meters that passed the task 1.0b checks.",
        "checks": ["bess_candidates == 0", "share_cp_s < 0.2 * share_customers", "share_cp_w < 0.2 * share_customers"],
    },
    "Typical": {
        "headline": "The silent majority: {share_customers:.0%} of homes, and where the future winter peak lands.",
        "who": ("{customers:,} homes with no EV, electric heat, PV or top-{peak_q_pct:.0%} peak load; {central_ac:.0%} have "
                "central AC. Median use {median_kwh:,.0f} kWh/yr."),
        "why": ("Individually small ({cp_s_kw:.1f} kW summer, {cp_w_kw:.1f} kW winter) but together "
                "{share_cp_s:.0%} of the summer and {share_cp_w:.0%} of the winter peak. Most future heat-pump "
                "adopters come from here ({adopters_typical_share:.0%}): at {hp_top:.0%} adoption they hold {wcp_30:.0%} of the winter peak, "
                "{new_hp_wcp_30_pts:.0f} points of it new heat-pump load."),
        "program": ("Mass-market TOU and behavioural DR now; heat-pump programs should ship with managed controls "
                    "from day one. Only {tier2:,} reach Tier 2 for batteries."),
        "watch": ("Battery value per home is below the candidate average (${usd_mid:,.0f} vs ${usd_mid_all:,.0f}/home/yr "
                  "at mid values): batteries here only make sense on a constrained feeder."),
        "checks": ["share_cp_s > 0.4", "wcp_30 > wcp_today", "adopters_typical_share > 0.5", "tier1 == 0", "usd_mid < usd_mid_all"],
    },
}

CARD_FIELDS = ("headline", "who", "why", "program", "watch")

# --------------------------------------------------------------------------- program playbook
# One row per segment: program, timing, and the counts / MW that size it.
PLAYBOOK: list[dict] = [
    {"segment": "Electrified: EV + electric heat", "when": "Now",
     "program": "Battery + managed EV charging + heat DR bundle",
     "target": "{tier12:,} Tier 1+2 homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Electric-heat home", "when": "Now",
     "program": "Batteries for Tier 1; resistive → heat-pump conversion; weatherisation",
     "target": "{tier12:,} Tier 1+2 homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "EV home", "when": "Now",
     "program": "Managed charging / EV TOU; batteries for Tier 1",
     "target": "{customers:,} homes ({tier1:,} Tier 1)", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Summer peak contributor", "when": "Next",
     "program": "AC thermostat DR (mass); batteries on summer-constrained feeders",
     "target": "{tier2:,} Tier 2 homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Winter peak contributor", "when": "Next",
     "program": "Batteries (year-round value); heat-pump-ready audits",
     "target": "{tier2:,} Tier 2 homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Solar PV", "when": "Next",
     "program": "PV + storage retrofit for winter evenings",
     "target": "{tier12:,} Tier 1+2 homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Typical", "when": "Next",
     "program": "TOU + behavioural DR; heat pumps sold with managed controls",
     "target": "{customers:,} homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Emerging load", "when": "Watch",
     "program": "Transformer watch-list; EV / heat-pump readiness outreach",
     "target": "{customers:,} homes", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Very high load / non-residential", "when": "Watch",
     "program": "Rate-class review; commercial storage / C&I DR",
     "target": "{customers:,} accounts", "mw": "{relief_s_mw_t12:.2f} / {relief_w_mw_t12:.2f}"},
    {"segment": "Low use / seasonal", "when": "None",
     "program": "No program (vacancy / meter-health only)",
     "target": "{customers:,} homes", "mw": "n/a"},
]

PLAYBOOK_INTRO = [
    ("Today the system peaks in summer ({peak_summer_mw:.1f} MW on {peak_summer_date}, {peak_summer_temp:.0f} °F); "
     "the winter peak is {winter_over_summer_peak:.0%} of it ({peak_winter_mw:.1f} MW on {peak_winter_date})."),
    ("It flips to winter-peaking at about {crossover_B:.0%} heat-pump adoption (range {crossover_B:.0%}-{crossover_A:.0%} "
     "across donor groups). At {hp_top:.0%} the winter peak reaches {hp30_winter_mw:.1f} MW."),
    ("Batteries must recharge as a coordinated fleet: uncoordinated overnight recharge in a {hp_top:.0%} heat-pump winter "
     "creates a {hp30_peak_after_uncoord_mw:.0f} MW night peak; coordinated recharge brings the annual peak to "
     "{hp30_peak_after_coord_mw:.1f} MW."),
    ("Avoided buildout alone does not pay for a battery: a Tier 1 home avoids ≈ ${tier1_usd:,.0f}/yr "
     "(NPV ≈ ${tier1_npv:,.0f}) at mid placeholder $/kW-yr. Programs should target constrained feeders, "
     "electrification headroom and customer value (backup, bills) on top."),
    ("3 kVA electrification lens: {kva_exceed:.0%} of simulated heat-pump adopters exceed their old peak + 3 kVA "
     "(median {kva_hours:.0f} h/yr); 1 unit brings {kva_fix_1:.0%} of them within the cap, 2 units {kva_fix_2:.0%}."),
]

PLAYBOOK_CHECKS = ["peak_summer_mw > peak_winter_mw", "crossover_B < crossover_A",
                   "hp30_peak_after_uncoord_mw > hp30_winter_mw > hp30_peak_after_coord_mw",
                   "tier1_npv < 10000", "kva_fix_2 >= kva_fix_1"]

CAVEATS = [
    "Avoided-cost $/kW-yr and the TOU tariff are placeholders pending Avangrid's values (office-hours Q3).",
    "EV / HP labels are not a random sample (SCAR fails), so appliance counts come from physics signatures; "
    "PU scores are rankings only (office-hours Q1).",
    "Segment → peak links are associations, not causes; weather → load is the only causal claim.",
    "Hourly data blurs water heaters, hot tubs and heat-pump cycling (low-confidence tags).",
]
