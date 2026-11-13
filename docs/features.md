# Feature dictionary (task 1.2)

Built by `scripts/04_features.py` in 46s for 34,238 customers (33,927 in the modeling population). Thresholds come from equipment ratings in `src/avangrid/config.py`, never from labels.

Confidence reflects what hourly data can resolve: fast cycling loads (water heater, hot tub) average into small hourly bumps, so those scores are tagged low.

## System peaks

- **Summer**: top 88 hours span 18 days (2026-06-11 to 2026-08-09), hour-ending 11-24 (mode 18), 74.6-91.6 °F; mean system load 65,365 kWh/h.
- **Winter**: top 88 hours span 20 days (2025-12-09 to 2026-02-08), hour-ending 10-22 (mode 19), -3.4-24.2 °F; mean system load 51,694 kWh/h.

Winter coincident peak is 79% of the summer coincident peak today.

## Features

| feature | group | confidence | definition | non-null (model pop.) | median | AUC EV | AUC HP | AUC PV |
|---|---|---|---|---|---|---|---|---|
| `base_kwh_day` | weather | high | Weather-independent daily kWh (change-point intercept) | 100.0% | 13.4 | 0.70 | 0.68 | 0.52 |
| `heat_slope` | weather | high | kWh/day per °F below the heating balance point | 100.0% | 0.132 | 0.54 | 0.71 | 0.69 |
| `cool_slope` | weather | high | kWh/day per °F above the cooling balance point | 100.0% | 1.24 | 0.60 | 0.50 | 0.39 |
| `cold_slope_extra` | weather | medium | Extra kWh/day per °F below 20 °F (HP COP drop / strip backup) | 100.0% | -0.0063 | 0.50 | 0.57 | 0.42 |
| `heat_break_ratio` | weather | medium | (heat_slope + cold_slope_extra) / heat_slope; >1 = steepening in deep cold | 70.1% | 0.688 | 0.50 | 0.59 | 0.49 |
| `wind_heat_coef` | weather | medium | Heating kWh per HDD per wind s.d. ('leaky home') | 100.0% | 0.00266 | 0.49 | 0.42 | 0.60 |
| `hum_cool_coef` | weather | medium | Cooling kWh per CDD per humidity s.d. | 100.0% | 0.188 | 0.46 | 0.51 | 0.56 |
| `weekend_kwh_day` | weather | high | Weekend minus weekday daily kWh | 100.0% | 0.485 | 0.52 | 0.48 | 0.44 |
| `heat_bp_f` | weather | medium | Heating balance point, °F | 100.0% | 52.5 | 0.51 | 0.43 | 0.51 |
| `cool_bp_f` | weather | medium | Cooling balance point, °F | 100.0% | 62.5 | 0.54 | 0.52 | 0.55 |
| `weather_r2` | weather | high | R² of the change-point model | 100.0% | 0.527 | 0.36 | 0.61 | 0.47 |
| `heat_slope_rel` | weather | high | heat_slope / mean daily kWh | 100.0% | 0.0089 | 0.49 | 0.67 | 0.70 |
| `cool_slope_rel` | weather | high | cool_slope / mean daily kWh | 100.0% | 0.0644 | 0.47 | 0.34 | 0.37 |
| `resid_trend_per30d` | trend | medium | Trend of weather-normalised daily kWh, kWh/day per 30 days | 100.0% | 0.0212 | 0.47 | 0.48 | 0.37 |
| `resid_max_step` | trend | medium | Max minus min monthly weather-normalised residual | 100.0% | 7.75 | 0.63 | 0.62 | 0.63 |
| `ev_l2_per_week` | ev | high | L2 charging blocks per week of mild hours | 100.0% | 0 | 0.69 | 0.57 | 0.61 |
| `ev_l2_week_share` | ev | high | Share of weeks with at least one L2 block | 100.0% | 0 | 0.69 | 0.57 | 0.61 |
| `ev_l2_block_kwh` | ev | high | Median kWh above baseline per L2 block | 28.6% | 9.96 | 0.76 | 0.49 | 0.62 |
| `ev_l2_block_kw` | ev | high | Median peak hourly excess in an L2 block (≈ charger kW) | 28.6% | 6.02 | 0.76 | 0.61 | 0.65 |
| `ev_l2_block_hours` | ev | high | Median L2 block length, h | 28.6% | 2 | 0.70 | 0.45 | 0.59 |
| `ev_l2_evening_share` | ev | high | Share of L2 blocks starting 18:00-02:00 | 28.6% | 0.286 | 0.66 | 0.45 | 0.60 |
| `ev_l1_night_share` | ev | medium | Share of mild nights with a flat 1.0-2.2 kWh overnight plateau | 100.0% | 0 | 0.64 | 0.56 | 0.55 |
| `night_quiet_kwh` | ev | high | Customer's quiet-night hourly load (20th pct of mild nights) | 100.0% | 0.316 | 0.64 | 0.67 | 0.67 |
| `pool_timer_score` | pool | medium | Same-hour on and off steps on summer days, minus winter | 100.0% | 0.0566 | 0.62 | 0.52 | 0.59 |
| `pool_run_hours` | pool | medium | Hours between the timer on and off steps | 100.0% | 5 | 0.51 | 0.50 | 0.56 |
| `summer_base_lift_kwh` | pool | medium | Jul-Aug minus Apr-May 10th-pct hourly load | 99.8% | 0.0377 | 0.48 | 0.44 | 0.30 |
| `wh_bump_freq` | water_heater | low | Share of mild days with a ≥1.5 kWh morning bump (hourly data blurs 20-40 min bursts) | 100.0% | 0.025 | 0.56 | 0.63 | 0.54 |
| `wh_morning_bump_kwh` | water_heater | low | Median morning bump over the overnight minimum | 100.0% | 0.28 | 0.56 | 0.67 | 0.46 |
| `hot_tub_cycle_share` | hot_tub | low | Share of mild nights with isolated heater-cycling bumps | 100.0% | 0.0167 | 0.64 | 0.61 | 0.62 |
| `pv_midday_zero_summer` | pv | high | Share of Apr-Aug 10-15h hours at exactly 0 kWh (exports clipped) | 100.0% | 0 | 0.51 | 0.57 | 0.97 |
| `pv_night_zero_summer` | pv | high | Share of Apr-Aug 00-05h hours at 0 kWh (vacancy check) | 100.0% | 0 | 0.49 | 0.49 | 0.49 |
| `pv_midday_zero_winter` | pv | high | Share of Dec-Jan 10-15h hours at 0 kWh | 100.0% | 0 | 0.54 | 0.52 | 0.88 |
| `pv_zero_season_contrast` | pv | high | Summer minus winter midday zero share | 100.0% | 0 | 0.50 | 0.57 | 0.97 |
| `pv_midday_dip` | pv | medium | 1 - midday load / mean of 7-9h and 17-19h load, Apr-Aug | 99.9% | 0.042 | 0.60 | 0.52 | 0.94 |
| `annual_kwh` | size | high | Annual kWh (mean × 8,760) | 100.0% | 6.72e+03 | 0.67 | 0.73 | 0.58 |
| `peak_kw` | peak | high | Max hourly kWh | 100.0% | 5.41 | 0.72 | 0.68 | 0.67 |
| `p99_kw` | peak | high | 99th percentile hourly kWh | 100.0% | 3.19 | 0.72 | 0.68 | 0.64 |
| `load_factor` | peak | high | Mean / peak | 100.0% | 0.149 | 0.47 | 0.66 | 0.40 |
| `zero_share` | size | high | Share of hours at exactly 0 | 100.0% | 0 | 0.51 | 0.55 | 0.97 |
| `base_kw_p05` | size | high | 5th percentile hourly kWh | 100.0% | 0.217 | 0.60 | 0.63 | 0.07 |
| `winter_summer_ratio` | season | high | Mean winter / mean summer load | 100.0% | 0.978 | 0.52 | 0.68 | 0.71 |
| `summer_shoulder_ratio` | season | high | Mean summer / mean shoulder load | 100.0% | 1.24 | 0.46 | 0.34 | 0.33 |
| `weekend_weekday_ratio` | shape | high | Mean weekend / weekday load | 100.0% | 1.02 | 0.50 | 0.50 | 0.48 |
| `evening_share` | shape | high | Mean 16-21h load / mean load | 100.0% | 1.23 | 0.54 | 0.40 | 0.60 |
| `peak_hod_mode` | shape | high | Most common hour of the daily maximum (hour starting) | 100.0% | 17 | 0.55 | 0.52 | 0.62 |
| `cp_kw_summer` | peak | high | Mean load in the system's top 88 summer hours | 99.8% | 1.73 | 0.64 | 0.57 | 0.35 |
| `cp_kw_winter` | peak | high | Mean load in the system's top 88 winter hours | 100.0% | 1.1 | 0.64 | 0.76 | 0.66 |
| `cp_share_summer` | peak | high | Customer's share of the summer coincident-peak load | 99.8% | 2.65e-05 | 0.64 | 0.57 | 0.35 |
| `cp_share_winter` | peak | high | Customer's share of the winter coincident-peak load | 100.0% | 2.14e-05 | 0.64 | 0.76 | 0.66 |
| `cp_ratio_summer` | peak | high | Summer CP load / own mean load | 99.8% | 2.18 | 0.48 | 0.34 | 0.28 |
| `cp_ratio_winter` | peak | high | Winter CP load / own mean load | 100.0% | 1.47 | 0.50 | 0.69 | 0.70 |
| `peak_day_persistence_summer` | peak | high | Share of the top 10 summer system days where own max ≥ own p95 | 100.0% | 0.9 | 0.45 | 0.37 | 0.41 |
| `peak_day_persistence_winter` | peak | high | Share of the top 10 winter system days where own max ≥ own p95 | 100.0% | 0.6 | 0.47 | 0.67 | 0.61 |

AUC = how well the single raw feature ranks labeled vs unlabeled customers (0.5 = no signal, <0.5 = signal in the other direction). Unlabeled customers include unlabeled owners (PU setting), so these understate true separability.

Detector confidence tags: {'ev_l2': 'high', 'ev_l1': 'medium', 'pool': 'medium', 'water_heater': 'low', 'hot_tub': 'low'}.

Also stored: 12 monthly weather-normalised residuals (`resid_mXX_mon`), seasonal means/peaks, and 72 shape-profile columns in `shape_profiles.parquet`.

## Missing values (modeling population)

- `heat_break_ratio`: 10,142 NaN (heat slope ≈ 0 → ratio undefined)
- `resid_m01_sep`: 276 NaN
- `resid_m02_oct`: 170 NaN
- `resid_m03_nov`: 109 NaN
- `resid_m04_dec`: 51 NaN
- `resid_m05_jan`: 16 NaN
- `resid_m06_feb`: 14 NaN
- `resid_m07_mar`: 18 NaN
- `resid_m08_apr`: 51 NaN
- `resid_m09_may`: 56 NaN
- `resid_m10_jun`: 66 NaN
- `resid_m11_jul`: 75 NaN
- `resid_m12_aug`: 78 NaN
- `ev_l2_block_kwh`: 24,221 NaN (no L2 blocks → no block statistics)
- `ev_l2_block_hours`: 24,221 NaN (no L2 blocks → no block statistics)
- `ev_l2_block_kw`: 24,221 NaN (no L2 blocks → no block statistics)
- `ev_l2_evening_share`: 24,221 NaN (no L2 blocks → no block statistics)
- `summer_base_lift_kwh`: 79 NaN
- `mean_kw_winter`: 2 NaN
- `peak_kw_winter`: 2 NaN
- `winter_summer_ratio`: 5 NaN
- `cp_kw_summer`: 56 NaN
- `cp_share_summer`: 56 NaN
- `cp_ratio_summer`: 56 NaN
- `cp_kw_winter`: 13 NaN
- `cp_share_winter`: 13 NaN
- `cp_ratio_winter`: 13 NaN
- `pv_midday_dip`: 39 NaN
