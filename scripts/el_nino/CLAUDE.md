# El Niño precipitation outlook — child exposure by country

## Goal
Estimate, per country (land only), how many children live in areas with a forecast
**dry** or **wet** precipitation signal for **Oct–Dec 2026 (OND)**, using the ECMWF SEAS5
seasonal forecast.
Keep it simple: precipitation only, no temperature, no flood/drought models.
"Wet signal" = elevated flood risk proxy, not flooding. "Dry signal" = drought risk proxy.

Reference product from the team: ECMWF chart `seasonal_system5_standard_rain`, stats `tsum`
(tercile summary), base time 2026-09-01. Outputs should broadly match that chart.

## Data (all from Copernicus CDS, dataset `seasonal-monthly-single-levels`)
- originating_centre `ecmwf`, system `51` (SEAS5), variable `total_precipitation`,
  product_type `monthly_mean` (all ensemble members), init month `09`, leadtime_month **2–4**
  (Oct, Nov, Dec). Lead is months-after-init, so October is always lead 2 — dropping September
  does NOT renumber the rest. Lead 1 (September) is excluded: it is the init month, partly fixed
  by conditions already present at launch, so it behaves unlike a forecast month.
- Forecast: year 2026, 51 members.
- Hindcast: years 1993–2016 (same request, only years change), 25 members/year.
- Grid 1°, global (181 × 360). Variable `tprate` = mean rate in m/s.
- Do NOT use `ensemble_mean` (no spread → no probabilities) or monthly max/min/std.

## Method (decided)
1. Convert `tprate` → mm/month: × 86400 × 1000 × days in the valid month.
2. Longitudes 0–360 → −180–180.
3. Per grid cell and period, pool hindcast (24 yrs × 25 members = 600 values) →
   33rd / 67th percentiles = tercile thresholds (model's own climatology, removes bias).
4. Forecast probabilities = fraction of the 51 members below / above those thresholds.
5. `cat`: 1 = dry (p_below ≥ 0.5 and > p_above), 3 = wet (mirror), 0 = no clear signal.
6. `dry_mask` band = 0 where hindcast mean < 10 mm/month (single month) or < 50 mm (OND window).
   Shipped as a band, NOT applied — `cat` and the probabilities stay unmasked so downstream work
   can choose its own cutoff. Apply it by default when counting exposure.
7. Periods: each lead month (Oct, Nov, Dec) + full OND total.

## Current code — RUN AND UPLOADED
`download_ecmwf_prec.py` — download (cdsapi) → load (cfgrib,
`time_dims=("forecastMonth","time")`) → terciles → GeoTIFFs (rioxarray, nodata −9999) →
upload via **geeup**. No GCS bucket: this project has none, so geeup stages internally,
matching `scripts/upload_infra.py`.

Own virtualenv: `scripts/el_nino/.venv`, pinned in `requirements.txt`. Run it with that
interpreter directly (no activate). The GRIBs are cached — `download()` skips files that exist,
so a re-run after a config change costs minutes, not a re-download.

GEE layout under `projects/unicef-ccri/assets/el_nino/`:
- `raw_forecast`: 1 image, 153 bands `L{lead}_m{member:02d}`, mm/month
- `raw_hindcast`: 24 images (one per year), 75 bands, same naming
- `terciles`: 4 images (Oct, Nov, Dec + OND window), 8 bands
  `p_below, p_normal, p_above, cat, fc_mean_mm, clim_mean_mm, anom_mm, dry_mask`
- Image properties: system, init, init_ym, kind, year, lead, period, prob_threshold,
  dry_mask_mm, hindcast, cat_legend, lead_legend, **band_names**.

**geeup drops band names** — every asset arrives as `b1..bN`. Band ORDER is the contract; the
names live in the `band_names` property. Always `.rename()` before selecting by name, hindcast
images included. See the geeup memory note for the other two traps.

## Exposure — DONE, in the dashboard
Built as an app section, not a batch script: `app/elnino_config.py` + `app/elnino_core.py`,
wired into `app/app.py` as the El Niño tab. Uses the project's own under-18 grid via
`forecast_core._forecast_pop` (1 km) so figures reconcile with the other tabs — not WorldPop
direct — and GCHD adm0/1/2 boundaries rather than GAUL.

Exposure is re-derived from `p_below`/`p_above` at a user-set threshold, never from the stored
`cat` band (which is frozen at 50%). `dry_mask` is applied by default.

## Open items
- **Probability threshold**: 0.5 default; ECMWF charts shade from ~0.4. The tab reports 40/50/60%
  side by side rather than one figure, because 51 members quantise probability in ~2-point steps.
  Ethiopia spanned 44.6M → 19.3M children across that range.
- **No skill mask.** SEAS5 has real skill where ENSO teleconnections are strong and near none
  elsewhere, and the tab renders both identically. Biggest remaining caveat on any number here.
- **October initialisation** publishes ~5 Oct and would give a sharper OND (leads 1–3, 1–3 months
  ahead instead of 2–4). That is a genuine re-download — new initial conditions AND an
  October-initialised hindcast — landing under a `202610` tag. `elnino_config.INIT_TAG` currently
  assumes one initialisation; showing both would need a small change.
- QGIS visual check against the ECMWF `seasonal_system5_standard_rain` tsum chart: still not done.

## Conventions
- Python: xarray, cfgrib, rioxarray, earthengine-api, geeup (NOT google-cloud-storage —
  there is no bucket).
- Concise, practical code; config constants at top of scripts.
- Validate outputs visually (QGIS) against the ECMWF chart before computing exposure.
