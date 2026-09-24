# El Niño precipitation outlook — child exposure by country

## Goal
Estimate, per country (land only), how many children live in areas with a forecast
**dry** or **wet** precipitation signal for **Sep–Dec 2026**, using the ECMWF SEAS5 seasonal forecast.
Keep it simple: precipitation only, no temperature, no flood/drought models.
"Wet signal" = elevated flood risk proxy, not flooding. "Dry signal" = drought risk proxy.

Reference product from the team: ECMWF chart `seasonal_system5_standard_rain`, stats `tsum`
(tercile summary), base time 2026-09-01. Outputs should broadly match that chart.

## Data (all from Copernicus CDS, dataset `seasonal-monthly-single-levels`)
- originating_centre `ecmwf`, system `51` (SEAS5), variable `total_precipitation`,
  product_type `monthly_mean` (all ensemble members), init month `09`, leadtime_month 1–4 (Sep–Dec).
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
6. Mask `cat` where hindcast mean < 10 mm/month (single month) or < 50 mm (Sep–Dec window).
7. Periods: each lead month (Sep, Oct, Nov, Dec) + full Sep–Dec total.

## Current code
`seas5_precip_terciles_to_gee.py` — download (cdsapi) → load (cfgrib, `time_dims=("forecastMonth","time")`)
→ terciles → GeoTIFFs (rioxarray, nodata −9999) → upload to GCS → GEE ingestion via `ee.data.startIngestion`.
Not yet run; expect to debug cfgrib dimension names on first run.

GEE layout under `projects/<project>/assets/seas5_202609/`:
- `raw_forecast`: 1 image, 204 bands `L{lead}_m{member:02d}`, mm/month
- `raw_hindcast`: 24 images (one per year), 100 bands, same naming
- `terciles`: 5 images (per lead month + window), bands
  `p_below, p_normal, p_above, cat, fc_mean_mm, clim_mean_mm, anom_mm`
- Image properties: system, init, type, year, lead, period, prob_threshold, dry_mask_mm, hindcast.

## Next step: exposure script (to write)
- Signal: `terciles` image, band `cat` (start with the Sep–Dec window).
- Children: `WorldPop/GP/100m/pop_age_sex_cons_unadj`, year 2020, sum of
  M/F_0, _1, _5, _10 + 0.6 × (M/F_15) ≈ under-18. Swap for GCHD's standard child layer if preferred.
- Boundaries: `FAO/GAUL/2015/level0` (or GCHD boundaries for consistency).
- Outputs per country: children_dry, children_wet, children_total, and shares.
- `reduceRegions` with `Reducer.sum()` at `scale=100` (coarser scale samples instead of summing
  → undercounts). Run as batch `Export.table.toDrive` (global, heavy).

## Open items
- Confirm with team which window their chart shows (3-month period, e.g. OND) — may want an OND
  variant (lead months 2–4) to match it.
- Confirm probability threshold (0.5 here; ECMWF chart shades from ~0.4) and dry-mask values.
- Optional: hindcast skill mask (e.g. correlation/ROC vs ERA5) to flag low-skill regions.

## Conventions
- Python: xarray, cfgrib, rioxarray, earthengine-api, google-cloud-storage.
- Concise, practical code; config constants at top of scripts.
- Validate outputs visually (QGIS) against the ECMWF chart before computing exposure.
