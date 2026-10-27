# El Niño seasonal outlook — children under forecast rainfall and temperature signals

## What this is
The dashboard's El Niño tab estimates, per country or admin area, how many children (under
18) live where the ECMWF SEAS5 seasonal forecast leans towards an unusually **dry** or **wet**
season, optionally combined with unusual **heat** or **cool**. It is updated monthly: each
forecast issue (init month) adds one 3-month season, leads 2–4 (Sep → OND, Oct → NDJ,
Nov → DJF …).

These are risk **proxies**, not impact predictions. "Wet" means the season falls in the
wettest third of the model's own climate — a reason to look closer, not a flood forecast.
Every download carries that disclaimer, which the user must accept.

## Monthly update runbook
From the repo root, `PY = scripts/el_nino/.venv/Scripts/python`, once the new issue is on CDS
(~5th of the month). Example: the November 2026 issue, `202611` (season DJF).

1. **Download, build, upload** → `el_nino/202611/`:
   `PY scripts/el_nino/download_ecmwf_prec.py --init 202611 --var prec --var t2m`
   If one collection fails (geeup sometimes asks for cookies mid-run), rerun only that one,
   e.g. `--var t2m --only terciles`. GRIBs and GeoTIFFs are cached.
2. **Skill**, rainfall then temperature (stage observations, then correlate):
   `PY scripts/el_nino/build_skill_mask.py --init 202611 --var prec --stage-obs --wait`
   `PY scripts/el_nino/build_skill_mask.py --init 202611 --var prec --wait`
   …and the same two with `--var t2m`.
3. **ENSO mask and years** for the issue's season:
   `PY scripts/el_nino/build_enso_mask.py --init 202611`
   `PY scripts/el_nino/build_enso_mask.py --init 202611 --classify-years`
   Paste the printed dict into `ENSO_YEARS` in `app/elnino_config.py`.
4. **Confirm everything landed.** geeup's "Upload complete" means *submitted*, not ingested.
   List `el_nino/202611/*` and `el_nino/enso_mask/`, or look for FAILED tasks.
5. **Go live**: append `"202611"` to `ISSUES` in `app/elnino_config.py` and restart the app.

Nothing else needs touching. Ended months and seasons leave the period selector on their
own. Keep an issue in `ISSUES` as long as its season should stay visible.

## Data
Copernicus CDS, dataset `seasonal-monthly-single-levels`: ECMWF SEAS5 (system 51),
`monthly_mean` (all members — never `ensemble_mean`, which has no spread), 1° global grid.
- Variables: `total_precipitation` (`tprate`, m/s → mm/month) and `2m_temperature` (K → °C).
- Forecast: 51 members. Hindcast: 1993–2016, 25 members per year, same init month and leads.
- Lead 1 (the init month) is excluded: it is partly fixed by conditions at launch.

## Method
**Terciles** (per cell, per period; `download_ecmwf_prec.py`)
1. Pool the hindcast (24 years × 25 members = 600 values) → 33rd/67th percentiles. The model is
   compared with its own climate, so its bias cancels.
2. Probabilities = fraction of the 51 forecast members below / above those thresholds.
3. Periods per issue: each lead month and the season (`window_L2_L4`). Precipitation **sums**
   over the season, temperature **averages** (`window_agg` in `VARS`).
4. `dry_mask` = 0 where normal rainfall is under 10 mm (month) or 50 mm (season) — too arid for
   a tercile to mean anything. Shipped as a band; applied by default when counting.

**Exposure** (dashboard): a cell counts when the signal's probability ≥ the threshold (default
**70%**; compound default 25%). Children come from the project's own 1 km WorldPop 2025
under-18 grid, the same as the other tabs. The stored `cat` band (fixed at 50%) is never used
for counts.

**Compound signals** (hot-dry, hot-wet, cool-dry, cool-wet) are counted **per member**: the
fraction of members where rainfall and temperature are both in their own extreme third. Never
`p_dry × p_hot` — dry and hot are physically correlated (drier soil, less evaporative cooling),
so the product understates risk where it matters. `dependence_ratio` ships as a band to show
the difference. Two consequences to state when presenting:
- The no-information baseline is 1/9 (11.1%), not 1/3, so compound and single-variable
  thresholds are not comparable.
- The hot tercile is near-certain over much of the tropics, so hot-dry ≈ dry there. Compound
  signals add most in the subtropics (southern Africa, Australia).

**Skill** (`build_skill_mask.py`): Pearson correlation between the hindcast ensemble-mean
season and observations, 1993–2016 — CHIRPS for rainfall (land, 50°N–50°S), ERA5-Land for
temperature. Reported next to each figure as the share of the signal area with usable skill
(r ≥ 0.4, `SKILL_USABLE_MIN`), never applied to the count. Correlation is the generous
measure: it grades the mean, not the probabilities. ERA5-Land shares model lineage with SEAS5,
so temperature skill is optimistically biased. A Brier skill score (`build_brier_skill.py`,
`el_nino/<init>/brier`) is stricter but is not shown — reviewers found it harder to read; it can
be restored by re-adding two `EXPLORE_LAYERS` entries.

**El Niño attribution** — reported alongside, never applied:
- **Mask** (`build_enso_mask.py`): where El Niño has historically pushed the season's rainfall
  one way (Lenssen et al. 2020 teleconnection probabilities via IRI; significant at 90% and
  ≥ 50% → +1 wetter / −1 drier). Method of `github.com/dohyung-kim/ENSO`, which has no licence,
  so it is reimplemented, not copied. A forecast dry signal counts as El Niño-consistent only
  on −1 cells, wet only on +1. Each period uses its **issue's** season mask, months included
  (November from the October issue → NDJ); a caveat says monthly attribution is approximate.
- **Fingerprint layer**: the model's own El Niño-minus-neutral season rainfall from its
  hindcast, with years classified per issue by observed Niño 3.4 (`ENSO_YEARS`).
- **Live Niño 3.4** from NOAA OISST, independent of the forecast.
None of this proves causation: SEAS5 is the response to all initial conditions, and true
attribution would need ENSO-neutral model runs, which do not exist in its archive.

## Asset layout (Earth Engine, `projects/unicef-ccri/assets/el_nino/`)
One folder per issue: `<init>/<collection>/<collection>_<init>_<period>`; paths come from
`issue_asset()` in `app/elnino_config.py`.

| Collection | Images | Bands |
|---|---|---|
| `<init>/raw_forecast`, `raw_forecast_t2m` | `fc[_t2m]_<init>` | 153: `L{lead}_m{member}` |
| `<init>/raw_hindcast`, `raw_hindcast_t2m` | `hc[_t2m]_<year><MM>` × 24 | 75, same naming |
| `<init>/terciles` | 4 periods | 8: `p_below p_normal p_above cat fc_mean_mm clim_mean_mm anom_mm dry_mask` |
| `<init>/terciles_t2m` | 4 periods | 7: as above in °C, no `dry_mask` |
| `<init>/compound` | 4 periods | 9: `p_hotdry p_coolwet p_hotwet p_cooldry cat_compound p_dry_marginal p_hot_marginal dependence_ratio dry_mask` |
| `<init>/skill`, `skill_t2m` | 4 periods | `skill_r skill_p` |
| `chirps_obs`, `era5_obs` (shared) | `obs[_t2m]_m<first>x<n>_<year>` | observations staged for skill |
| `enso_mask` (shared) | `enso_mask_<SEASON>_sig90_p50` | `enso_dir` (−1/0/+1) |

- **geeup drops band names** — bands arrive as `b1…bN`. Band order is the contract; names are
  in each image's `band_names` property. Always `.rename()` before selecting.
- Observations are keyed by calendar months (`m11x3` = 3 months from November; 13 = January
  next year), so issues share them. The 202609 ones keep older names (`obs_<period>_<year>`).
- The 7-vs-8 band counts of the two tercile collections are deliberate: renaming with the
  wrong list fails loudly instead of mislabelling.
- Flat copies of the 202609 collections directly under `el_nino/` are unused leftovers from
  before the folder layout and can be deleted.

## Code map
`scripts/el_nino/` (own venv `.venv`, pinned in `requirements.txt`; run its python directly):
- `download_ecmwf_prec.py` — CDS download → terciles/compound GeoTIFFs → geeup upload.
- `build_skill_mask.py` — skill correlation (`--var prec|t2m`), via staged observations.
- `build_brier_skill.py` — Brier skill score (not shown in the app).
- `build_enso_mask.py` — ENSO mask per season; `--classify-years` for `ENSO_YEARS`.
- `export_exposure_table.py`, `export_reliability_table.py` — offline per-country workbooks.
- `clip_anomalies.py` — per-country anomaly GeoTIFFs from local files.
Every script takes `--init YYYYMM`.

`app/` (the dashboard):
- `elnino_config.py` — single source: `ISSUES`, periods, signals, layers, thresholds, copy.
  No Earth Engine import. The scripts import it too.
- `elnino_core.py` — Earth Engine logic (exposure, skill, anomalies, attribution, tiles).
- `app.py` — the tab, results panel, Excel/JSON downloads behind the disclaimer.

## Dashboard behaviour worth knowing
- **Periods**: one season per issue, plus each month from the **latest** issue covering it. A
  season always stays with its own issue (OND = September issue). Months and seasons leave the
  selector once they have ended; the list is rebuilt when the tab opens. Default: newest season.
- **Map**: the signal is always drawn (`elnino-layer-tile`); one optional extra layer can be
  added on top (`elnino-extra-tile`). The legend leads with the signal and states the counts.
- **Threshold sensitivity**: results always show 50/60/70/80% (compound 20/25/33) plus the
  active value. 51 members quantise probability in ~2-point steps, so quote the range. A zero
  result says which lower threshold would return children.
- **Downloads**: the Excel workbook (Read me first, Summary, Sensitivity, Historical, Method)
  and JSON are built on demand after the disclaimer is accepted, and carry the disclaimer and
  a suggested sentence to quote.

## Traps and checks
- **Sum vs mean**: a lead-*averaged* rainfall total is ⅓ too small and not obvious. The
  regression test catches it.
- **Member alignment**: the compound signal is meaningless unless member *i* is the same
  realisation in both variables. `assert_aligned()` stops the build on a mismatch;
  `dependence_ratio` is the second check. Do not read its global mean (Simpson's paradox: 0.91
  globally while every populated latitude band is ≥ 1.0).
- **Property types**: every image in a collection needs the same property types
  (`dry_mask_mm` is 0, not empty, for temperature). geeup literal-evals metadata values.
- **int8 uploads** (masks): `MODE` pyramids and no nodata — geeup's −9999 fails ingestion.
- **PROJ**: a machine-wide PostGIS `PROJ_LIB` breaks CRS writing; the scripts unset it.
- **Staged observations are a prerequisite** for skill: regridding 5 km CHIRPS inside the
  24-year loop runs out of memory.
- **Regression tests** after changing script code:
  - Copy `data/seas5_202609/fc_202609.grib` and `hc_09_1993_2016.grib` to a scratch folder;
    `download_ecmwf_prec.py --init 202609 --var prec --no-upload --out <scratch>`; the four
    `terciles_202609_*.tif` must be byte-identical to `data/seas5_202609/`.
  - `build_skill_mask.py --init 202609 --period window_L2_L4 --dry-run` must print Horn of
    Africa r = `0.4720394992725713`.
  - `build_enso_mask.py --season OND --no-upload` must equal
    `data/ElNino_combined_OND_sig90_p50.tif` cell for cell.

## Reference figures (September 2026 issue, OND)
- Global headline: 122M children dry, 215M wet (published).
- Skill over the Horn of Africa: rainfall r = 0.47; temperature r = 0.60.
- Fingerprint: maritime continent −161 mm, east Pacific +88, Horn +58, southern Africa −17.
- Ethiopia, wet at 70%: 100% El Niño-consistent; dry at 70%: 0% (El Niño wets the Horn).

## Open
- Visual check against ECMWF's `seasonal_system5_standard_rain` tercile chart (QGIS) not done.
