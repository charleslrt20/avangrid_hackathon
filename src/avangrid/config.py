"""Single source of truth for paths, constants and assumptions.

Every number that a judge could question lives here, with its source. Values marked
PLACEHOLDER are assumptions to confirm with Avangrid at office hours (PLAN.md §9) and are
always reported as a low / mid / high range.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "Data"                       # raw zips / xlsx, never committed
DATA_PROC = ROOT / "data_processed"            # Parquet, matrix (gitignored)
CHECKS = DATA_PROC / "checks"                  # task 1.0b per-customer reviews + decisions.json
OUTPUTS = ROOT / "outputs"                     # customer-level results (gitignored)
OUTPUTS_SUMMARY = OUTPUTS / "summary"          # aggregate tables (committed)
MODELS = ROOT / "models"                       # fitted models (gitignored: trained on restricted data)
DOCS = ROOT / "docs"
FIGURES = DOCS / "figures"
INSIGHTS = DOCS / "insights"

LOAD_MATRIX = DATA_PROC / "load_matrix.npy"            # float32 [n_customers, 8760], NaN = no reading
CUSTOMER_INDEX = DATA_PROC / "customer_index.parquet"  # row order of the matrix + coverage + labels
HOUR_INDEX = DATA_PROC / "hour_index.parquet"          # column order of the matrix + aligned weather
DECISIONS = CHECKS / "decisions.json"                  # exclusions / flags from scripts/03b_checks.py
FEATURES = DATA_PROC / "features.parquet"              # task 1.2 output

SEED = 20261112

# --------------------------------------------------------------------------- time
START = pd.Timestamp("2025-09-01")
N_DAYS = 365
N_HOURS = N_DAYS * 24
# Matrix column = (usage_date - START).days * 24 + (hour_ending - 1).
# Hour-of-day below is hour *starting* (0-23), i.e. hour_ending - 1.

SUMMER_MONTHS = (6, 7, 8, 9)
WINTER_MONTHS = (12, 1, 2)
SHOULDER_MONTHS = (4, 5, 10, 11)

# --------------------------------------------------------------------------- system peaks
# Coincident peak = the hours that size the system. Same number of hours in each season so
# summer and winter contributions are comparable: 88 h ~= 1% of the year.
PEAK_TOP_HOURS = 88
# Customer-level persistence: a "high-peak day" is a day in the season's top-N system days.
PEAK_TOP_DAYS = 10

# --------------------------------------------------------------------------- data quality (task 1.0b)
HIGH_LOAD_KWH = 25.0            # brief's outlier line; reviewed account by account, not dropped wholesale
STUCK_FLAT_SHARE = 0.5          # share of hours equal to the previous hour
MIN_DAYS_FOR_MODEL = 180        # shorter histories stay in lookup, out of model fits
METERING = "delivered_only"     # no negatives in 298M readings; PV exports clipped to 0 (docs/data_checks.md)

# --------------------------------------------------------------------------- appliance physics
# Thresholds come from rated equipment, never from label counts (PLAN.md §5 rule).
EV_L2_RUN_KWH = 3.0             # hour counts as charging if >= this above the customer's own baseline
EV_L2_PEAK_KWH = 5.5            # block must reach a full hour of a 6-11.5 kW charger
EV_L2_MAX_HOURS = 6
EV_L2_DETECT_PER_WEEK = 0.5
EV_L1_KW = (1.0, 2.2)           # 120 V, 12-16 A
EV_L1_DETECT_SHARE = 0.25       # share of mild nights with an L1 plateau
BASELINE_QUANTILE = 20          # own month x hour percentile; median would absorb frequent charging
MILD_TEMP_F = (40.0, 80.0)      # hours with little HVAC, used for appliance event detection

HEAT_BALANCE_RANGE_F = (45.0, 65.0)   # search range for heating balance point
COOL_BALANCE_RANGE_F = (60.0, 75.0)
HP_SLOPE_BREAK_F = 20.0               # below this, HP COP falls and strip backup engages

# --------------------------------------------------------------------------- PU learning
# Brief: EV and HP lists are ~25% random samples of "likely" owners. SCAR is not assumed
# to hold for true ownership, so PU scores are rankings; c is varied as a sensitivity.
PU_C = 0.25
PU_C_SENSITIVITY = (0.15, 0.25, 0.40)
IMPLIED_COUNTS = {"ev": 186 / PU_C, "hp": 88 / PU_C}   # 744 / 352: a check that is allowed to fail
CV_FOLDS = 5
BENCHMARK_AUC = {"ev": 0.736, "hp": 0.738}             # crude-feature baseline to beat


# --------------------------------------------------------------------------- battery (brief)
@dataclass(frozen=True)
class Battery:
    """Enphase IQ Battery 10C, from the Challenge Statement."""

    units: int = 2
    kwh_per_unit: float = 10.08
    kw_per_unit: float = 3.84            # 7.68 kW continuous for 2 units
    transient_kw_per_unit: float = 5.76  # 11.52 kW for 10 s; never used for peak relief
    rte: float = 0.894                   # round trip; applied on charge (losses taken from the grid)
    soc_min_frac: float = 0.0            # ASSUMPTION: program dispatch may use full usable capacity;
                                         # sensitivity at 0.2 (customer backup reserve)
    soc_max_frac: float = 1.0

    @property
    def kwh(self) -> float:
        return self.units * self.kwh_per_unit

    @property
    def kw(self) -> float:
        return self.units * self.kw_per_unit

    def with_units(self, units: int) -> "Battery":
        return Battery(units=units, kwh_per_unit=self.kwh_per_unit, kw_per_unit=self.kw_per_unit,
                       transient_kw_per_unit=self.transient_kw_per_unit, rte=self.rte,
                       soc_min_frac=self.soc_min_frac, soc_max_frac=self.soc_max_frac)


BATTERY = Battery()
SOC_MIN_SENSITIVITY = (0.0, 0.2)
RECHARGE_HOURS = tuple(range(0, 6))      # hour-starting 00-05: off-peak in both seasons
DR_REBOUND_SHARE = 1.0                   # shifted kWh fully reappears in adjacent hours (conservative)

# Realistic dispatch: the utility calls a budget of events on the hottest / coldest *forecast* days and
# discharges flat over a fixed window, not knowing the exact peak hours. Windows cover the most system
# top hours (outputs/summary/system_peaks.csv): summer HE15-20 (63/88), winter HE16-21 (69/88).
EVENT_BUDGET_PER_SEASON = 15             # typical DR program call budget
FORECAST_SD_F = 2.0                      # day-ahead daily-mean temperature forecast error (s.d.)
WINDOW_HOURS = {"summer": tuple(range(14, 20)), "winter": tuple(range(15, 21))}   # hour-starting
CANDIDATE_MIN_CP_KW = 1.5                # below this, a battery has too little coincident load to offset
# Demand flexibility (no battery): thermostat shed during events, shifted energy rebounds afterwards
DR_AC_SHED_FRAC = 0.30                   # share of cooling load shed by a setback / pre-cool event
DR_HEAT_SHED_FRAC = 0.20                 # share of electric heating load shed (smart thermostat)
DR_REBOUND_HOURS = 2                     # rebound lands in the hours right after the window

# --------------------------------------------------------------------------- service headroom (brief)
HEADROOM_KVA = 3.0                # 200 A service: <= ~3 kVA incremental load without further evaluation
POWER_FACTOR = 1.0                # ASSUMPTION: residential load ~unity PF, so kVA ~= kW on hourly data
SERVICE_KVA_200A = 48.0           # 200 A x 240 V


# --------------------------------------------------------------------------- value (PLACEHOLDER)
@dataclass(frozen=True)
class Range:
    low: float
    mid: float
    high: float

    def as_dict(self) -> dict[str, float]:
        return {"low": self.low, "mid": self.mid, "high": self.high}


# Headline metric = avoided buildout cost per kW of *sustained* coincident-peak relief.
# PLACEHOLDER: order-of-magnitude ranges for New England (capacity market, regional
# transmission, utility distribution deferral). Replace with Avangrid's values (office hours Q3).
AVOIDED_COST_PER_KW_YR = {
    "capacity": Range(30.0, 60.0, 100.0),
    "transmission": Range(50.0, 100.0, 150.0),
    "distribution_deferral": Range(25.0, 75.0, 150.0),
}
ANALYSIS_YEARS = 15               # matches the 15-year program warranty in the brief
DISCOUNT_RATE = 0.07              # PLACEHOLDER real discount rate for NPV


@dataclass(frozen=True)
class TouTariff:
    """PLACEHOLDER TOU tariff for the secondary bill-savings sidebar only."""

    on_peak_rate: float = 0.32         # $/kWh
    off_peak_rate: float = 0.18        # $/kWh
    on_peak_hours: tuple[int, ...] = tuple(range(14, 19))   # hour-starting, weekdays
    weekdays_only: bool = True


TARIFF = TouTariff()

# --------------------------------------------------------------------------- future scenario
HP_ADOPTION_SCENARIOS = (0.05, 0.15, 0.30)   # share of customers adding a heat pump
# Coordinated fleet recharge (task 2.2): overnight plus the winter midday valley between the morning and
# evening peaks; fleet charging is throttled so it never sets a new system peak (one limit per season, found by
# bisection so that the event-hour peak equals the recharge ceiling).
FLEET_RECHARGE_HOURS = tuple(range(0, 6)) + tuple(range(10, 14))
FLEET_LIMIT_ITERS = 8                        # bisection steps on [0.7, 1.0] x the season's pre-battery peak

# --------------------------------------------------------------------------- outputs
CUSTOMER_RESULTS = OUTPUTS / "customer_results.parquet"


def ensure_dirs() -> None:
    for d in (OUTPUTS, OUTPUTS_SUMMARY, MODELS, FIGURES, INSIGHTS):
        d.mkdir(parents=True, exist_ok=True)

