"""Build docs/'Forecast Data catalog.xlsx' — one row per dataset, flat.

Mirrors the vocabulary of the existing docs/'Global Hazard Data catalog.xlsx'
(Hazards / Type / Resolution / Units / Coverage / Source / GEE Asset / Naming
Convention) and adds the columns that only matter for early warning: latency,
update cadence, forecast horizon, actionable lead time.

Both workbooks live in docs/ rather than app/: they are reference material for
people, never read at runtime, and app/ is what gets deployed.

Status column separates Implemented / Rejected / Candidate so a single sheet
carries the whole evaluation record.

Every value in the Implemented rows was verified against the live GEE catalog
(see FINDINGS.md). Reference thresholds are cited to their published source;
where no directly comparable standard exists (satellite column density vs WHO
surface concentration) the cell says so instead of inventing an equivalence.
"""
import os
import sys
import datetime as dt
import xlsxwriter

# Override with FORECAST_CATALOG_OUT to write elsewhere (e.g. when the real
# workbook is open in Excel and therefore locked).
OUT = os.environ.get(
    "FORECAST_CATALOG_OUT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "docs", "Forecast Data catalog.xlsx"))

COLUMNS = [
    ("Status", 12),
    ("Active", 9),
    ("Hazard Topic", 18),
    ("Layer Name", 26),
    ("Layer ID (app)", 20),
    ("Type", 30),
    ("EW Role", 15),
    ("GEE Asset ID", 38),
    ("Band(s) Used", 34),
    ("Native Resolution", 16),
    ("Temporal Resolution", 18),
    ("Forecast Horizon", 16),
    ("Data Latency", 14),
    ("Update Cadence", 15),
    ("Actionable Lead Time", 20),
    ("Default Window (days)", 14),
    ("Max Window (days)", 14),
    ("Aggregation Options", 22),
    ("Default Aggregation", 18),
    ("Units (app)", 14),
    ("App Default Threshold", 16),
    ("Slider Min", 10),
    ("Slider Max", 10),
    ("Reference Threshold", 30),
    ("Reference Source", 34),
    ("Threshold Basis", 18),
    ("Processing / Transform", 46),
    ("Collection Filters", 30),
    ("Observed Range (probe)", 34),
    ("Coverage", 14),
    ("Provider", 16),
    ("Licence / Access", 26),
    ("Known Caveats", 60),
    ("Status Notes", 46),
    ("Catalog URL", 44),
    ("Verified On", 12),
]

V  = "2026-08-19"  # probe date — original 12 datasets verified against live GEE
V2 = "2026-08-31"  # CAMS probe date (bands, forecast horizon, PM value ranges)

# ---------------------------------------------------------------------------
# Rows. Order: Implemented (by topic) -> Candidate -> Rejected.
# ---------------------------------------------------------------------------
ROWS = [
    # ── IMPLEMENTED ─────────────────────────────────────────────────────────
    dict(
        Status="Implemented", **{
        "Hazard Topic": "River Flood",
        "Layer Name": "Rainfall forecast (NOAA GFS)",
        "Layer ID (app)": "gfs_precip",
        "Type": "Numerical weather prediction",
        "EW Role": "Forecast",
        "GEE Asset ID": "NOAA/GFS0P25",
        "Band(s) Used": "total_precipitation_surface",
        "Native Resolution": "27.8 km",
        "Temporal Resolution": "1-hourly steps",
        "Forecast Horizon": "16 days",
        "Data Latency": "~5 h",
        "Update Cadence": "4x daily (6-hourly)",
        "Actionable Lead Time": "1-10 days",
        "Default Window (days)": 3, "Max Window (days)": 10,
        "Aggregation Options": "sum, max", "Default Aggregation": "sum",
        "Units (app)": "mm", "App Default Threshold": 50,
        "Slider Min": 0, "Slider Max": 400,
        "Reference Threshold": "WMO heavy rain: >=50 mm / 24 h",
        "Reference Source": "WMO Severe Weather Information Centre",
        "Threshold Basis": "Matches WMO",
        "Processing / Transform": "Sum of per-interval accumulations over window",
        "Collection Filters": "forecast_hours > 0",
        "Observed Range (probe)": "3d sum 0.6-188 mm (Kenya)",
        "Coverage": "Global", "Provider": "NOAA NCEP",
        "Licence / Access": "Public domain",
        "Known Caveats": "forecast_hours=0 (analysis step) carries NO precipitation band and is "
                         "filtered out. Values are per-interval, not run-cumulative (verified "
                         "non-monotonic across steps), so summing is correct. Coarse 28 km grid "
                         "under-resolves convective cells and small catchments.",
        "Status Notes": "Primary rainfall forecast layer.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "River Flood",
        "Layer Name": "Observed rainfall (GPM IMERG)",
        "Layer ID (app)": "imerg_precip",
        "Type": "Satellite precipitation estimate",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "NASA/GPM_L3/IMERG_V07",
        "Band(s) Used": "precipitation",
        "Native Resolution": "11.1 km",
        "Temporal Resolution": "30-minute",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~2 days",
        "Update Cadence": "Half-hourly",
        "Actionable Lead Time": "Nowcast / post-event",
        "Default Window (days)": 7, "Max Window (days)": 30,
        "Aggregation Options": "sum, max", "Default Aggregation": "sum",
        "Units (app)": "mm", "App Default Threshold": 100,
        "Slider Min": 0, "Slider Max": 800,
        "Reference Threshold": "WMO heavy rain: >=50 mm / 24 h",
        "Reference Source": "WMO Severe Weather Information Centre",
        "Threshold Basis": "Probed default (multi-day window)",
        "Processing / Transform": "Band is mm/hr on half-hourly images: x0.5 per image, then sum",
        "Collection Filters": "-",
        "Observed Range (probe)": "7d sum 0-249 mm (Kenya)",
        "Coverage": "60N-60S", "Provider": "NASA / JAXA",
        "Licence / Access": "Open",
        "Known Caveats": "V07 band is 'precipitation' — V06's 'precipitationCal' does not exist "
                         "here. ~2 day latency makes this a monitoring rather than warning layer. "
                         "Satellite estimate, not gauge-calibrated in real time; under-detects "
                         "orographic and light rain.",
        "Status Notes": "Complements the GFS forecast with what actually fell.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V07",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Tropical Storm",
        "Layer Name": "Wind speed forecast (NOAA GFS)",
        "Layer ID (app)": "gfs_wind",
        "Type": "Numerical weather prediction",
        "EW Role": "Forecast",
        "GEE Asset ID": "NOAA/GFS0P25",
        "Band(s) Used": "u_component_of_wind_10m_above_ground + v_component_of_wind_10m_above_ground",
        "Native Resolution": "27.8 km",
        "Temporal Resolution": "1-hourly steps",
        "Forecast Horizon": "16 days",
        "Data Latency": "~5 h",
        "Update Cadence": "4x daily (6-hourly)",
        "Actionable Lead Time": "1-10 days",
        "Default Window (days)": 3, "Max Window (days)": 10,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "m/s", "App Default Threshold": 17.5,
        "Slider Min": 0, "Slider Max": 80,
        "Reference Threshold": "WMO tropical storm: 34-63 kt (17.5-32.4 m/s); "
                               "hurricane force >64 kt (>33 m/s)",
        "Reference Source": "WMO Classification of Tropical Cyclones",
        "Threshold Basis": "Matches WMO + static catalog",
        "Processing / Transform": "Derived wind speed = hypot(u10, v10) per image, then max",
        "Collection Filters": "forecast_hours > 0",
        "Observed Range (probe)": "u10 3d max -9.2 to 8.7 m/s (Kenya, calm period)",
        "Coverage": "Global", "Provider": "NOAA NCEP",
        "Licence / Access": "Public domain",
        "Known Caveats": "GFS0P25 has NO gust band — this is sustained 10 m wind, so peak gusts "
                         "are underestimated. 28 km grid substantially under-resolves tropical "
                         "cyclone core wind maxima; use for footprint, not intensity grading.",
        "Status Notes": "Threshold aligns with the static tropical_storm_100yr layer (17.5 m/s).",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Extreme Heat",
        "Layer Name": "Air temperature forecast (NOAA GFS)",
        "Layer ID (app)": "gfs_temp",
        "Type": "Numerical weather prediction",
        "EW Role": "Forecast",
        "GEE Asset ID": "NOAA/GFS0P25",
        "Band(s) Used": "temperature_2m_above_ground",
        "Native Resolution": "27.8 km",
        "Temporal Resolution": "1-hourly steps",
        "Forecast Horizon": "16 days",
        "Data Latency": "~5 h",
        "Update Cadence": "4x daily (6-hourly)",
        "Actionable Lead Time": "1-10 days",
        "Default Window (days)": 3, "Max Window (days)": 10,
        "Aggregation Options": "max, mean, min", "Default Aggregation": "max",
        "Units (app)": "degC", "App Default Threshold": 35,
        "Slider Min": -20, "Slider Max": 55,
        "Reference Threshold": "No single global standard — WMO heatwave definitions are "
                               "local/percentile-based; 35 degC is a common fixed criterion",
        "Reference Source": "WMO Extreme Heat topic; UNDRR HIPS MH0501",
        "Threshold Basis": "Matches static catalog",
        "Processing / Transform": "Direct — already degC in this collection (no K conversion)",
        "Collection Filters": "forecast_hours > 0",
        "Observed Range (probe)": "3d max 13.7-38.9 degC (Kenya)",
        "Coverage": "Global", "Provider": "NOAA NCEP",
        "Licence / Access": "Public domain",
        "Known Caveats": "WMO defines heatwaves relative to LOCAL climatology and duration "
                         "(2-3+ consecutive days), not an absolute cutoff — a fixed 35 degC "
                         "threshold over-flags hot climates and under-flags temperate ones. "
                         "Dry-bulb only: no humidity, so no heat-index/WBGT effect.",
        "Status Notes": "Threshold matches the static extreme_heat layer (days over 35 degC).",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Heatwave",
        "Layer Name": "Land surface temperature (MODIS)",
        "Layer ID (app)": "modis_lst",
        "Type": "Satellite thermal observation",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "MODIS/061/MOD11A1",
        "Band(s) Used": "LST_Day_1km",
        "Native Resolution": "1 km",
        "Temporal Resolution": "Daily",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~2-3 days",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Post-event / situational",
        "Default Window (days)": 8, "Max Window (days)": 30,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "degC", "App Default Threshold": 40,
        "Slider Min": -20, "Slider Max": 70,
        "Reference Threshold": "No direct standard — LST is not air temperature; "
                               "health thresholds are defined on 2 m air temperature",
        "Reference Source": "WMO Extreme Heat topic",
        "Threshold Basis": "Probed default — needs expert review",
        "Processing / Transform": "Scaled uint16 Kelvin: raw x0.02 - 273.15 -> degC",
        "Collection Filters": "-",
        "Observed Range (probe)": "raw 13932-16112 = 5.5-48.1 degC (Kenya)",
        "Coverage": "Global", "Provider": "NASA LP DAAC",
        "Licence / Access": "Open",
        "Known Caveats": "Land SURFACE temperature runs well above 2 m air temperature (often "
                         "+10-20 degC on bare ground), so the 40 degC default is NOT comparable "
                         "to an air-temperature heat warning. Clear-sky pixels only — cloudy "
                         "days are masked, which biases windows during overcast heat events.",
        "Status Notes": "Situational awareness layer; not a warning trigger on its own.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD11A1",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Fire",
        "Layer Name": "Active fire brightness (FIRMS)",
        "Layer ID (app)": "firms_brightness",
        "Type": "Satellite active-fire detection",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "FIRMS",
        "Band(s) Used": "T21",
        "Native Resolution": "1 km",
        "Temporal Resolution": "Daily composite",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~2-3 days (GEE ingest)",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "K", "App Default Threshold": 330,
        "Slider Min": 300, "Slider Max": 500,
        "Reference Threshold": "MODIS C6 algorithm uses 360 K day / 320 K night in its "
                               "internal fire tests (not a user-facing severity threshold)",
        "Reference Source": "MODIS Collection 6 Active Fire Product User Guide",
        "Threshold Basis": "Probed default — needs expert review",
        "Processing / Transform": "Direct T21 brightness temperature, max over window",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d max 300-392 K (Kenya)",
        "Coverage": "Global", "Provider": "NASA FIRMS",
        "Licence / Access": "Open",
        "Known Caveats": "FIRMS pixels are ALREADY algorithm-confirmed detections, so a T21 "
                         "threshold grades intensity among confirmed fires — it does not detect "
                         "fire presence. NRT feed is near-real-time at source but GEE ingest lags "
                         "~2-3 days. No FRP band exists in this collection.",
        "Status Notes": "Replaces the originally planned FRP layer — no FRP band exists.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/FIRMS",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Fire",
        "Layer Name": "Active fire days (FIRMS)",
        "Layer ID (app)": "firms_fire_days",
        "Type": "Satellite active-fire detection (derived count)",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "FIRMS",
        "Band(s) Used": "T21 (presence mask)",
        "Native Resolution": "1 km",
        "Temporal Resolution": "Daily composite",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~2-3 days (GEE ingest)",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast / trend",
        "Default Window (days)": 30, "Max Window (days)": 60,
        "Aggregation Options": "sum", "Default Aggregation": "sum",
        "Units (app)": "days", "App Default Threshold": 1,
        "Slider Min": 0, "Slider Max": 60,
        "Reference Threshold": "n/a — derived persistence metric",
        "Reference Source": "-",
        "Threshold Basis": "Derived metric (>=1 day = any fire)",
        "Processing / Transform": "Per image: mask presence -> 1/0, summed over window = days with fire",
        "Collection Filters": "-",
        "Observed Range (probe)": "31 images in a 30d window (Kenya)",
        "Coverage": "Global", "Provider": "NASA FIRMS",
        "Licence / Access": "Open",
        "Known Caveats": "Counts DAYS WITH A DETECTION, not fire count or burned area. Cloud "
                         "cover and overpass timing cause missed days, so this under-counts. "
                         "Derived because FIRMS has no native count band.",
        "Status Notes": "Fire persistence — distinguishes a one-day flare from a sustained burn.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/FIRMS",
        "Verified On": V}),

    # ── Air quality: CAMS (surface concentrations, forecast) ────────────────
    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "PM2.5 forecast (CAMS)",
        "Layer ID (app)": "cams_pm25",
        "Type": "Atmospheric composition model (assimilated)",
        "EW Role": "Forecast",
        "GEE Asset ID": "ECMWF/CAMS/NRT",
        "Band(s) Used": "particulate_matter_d_less_than_25_um_surface",
        "Native Resolution": "44 km",
        "Temporal Resolution": "3-hourly steps",
        "Forecast Horizon": "5 days",
        "Data Latency": "~0 (forecast)",
        "Update Cadence": "2 runs/day (00z, 12z)",
        "Actionable Lead Time": "1-5 days",
        "Default Window (days)": 3, "Max Window (days)": 5,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "ug/m3", "App Default Threshold": 15,
        "Slider Min": 0, "Slider Max": 250,
        "Reference Threshold": "WHO 2021 AQG PM2.5: 15 ug/m3 (24-h mean)",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Matches WHO",
        "Processing / Transform": "kg/m3 x 1e9 -> ug/m3",
        "Collection Filters": "-",
        "Observed Range (probe)": "3d max p50 10.8 / p98 54.4 ug/m3 (Kenya); "
                                  "p50 57.3 / p98 173.8 (N. India)",
        "Coverage": "Global", "Provider": "ECMWF / Copernicus (CAMS)",
        "Licence / Access": "Open (Copernicus); attribution required",
        "Known Caveats": "SURFACE concentration, so unlike the Sentinel-5P columns it IS "
                         "directly comparable to WHO guidelines. Model output rather than a "
                         "direct measurement — it assimilates observations but inherits model "
                         "error, and 44 km cannot resolve street- or city-scale gradients.",
        "Status Notes": "Supersedes the S5P column layers for air quality; first catalog entry "
                        "whose threshold is a real health guideline.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
        "Verified On": V2}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "PM10 forecast (CAMS)",
        "Layer ID (app)": "cams_pm10",
        "Type": "Atmospheric composition model (assimilated)",
        "EW Role": "Forecast",
        "GEE Asset ID": "ECMWF/CAMS/NRT",
        "Band(s) Used": "particulate_matter_d_less_than_10_um_surface",
        "Native Resolution": "44 km",
        "Temporal Resolution": "3-hourly steps",
        "Forecast Horizon": "5 days",
        "Data Latency": "~0 (forecast)",
        "Update Cadence": "2 runs/day (00z, 12z)",
        "Actionable Lead Time": "1-5 days",
        "Default Window (days)": 3, "Max Window (days)": 5,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "ug/m3", "App Default Threshold": 45,
        "Slider Min": 0, "Slider Max": 600,
        "Reference Threshold": "WHO 2021 AQG PM10: 45 ug/m3 (24-h mean)",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Matches WHO",
        "Processing / Transform": "kg/m3 x 1e9 -> ug/m3",
        "Collection Filters": "-",
        "Observed Range (probe)": "3d max p50 15.3 / p98 55.8 ug/m3 (Kenya); "
                                  "p50 147.1 / p98 1183.6 (N. India)",
        "Coverage": "Global", "Provider": "ECMWF / Copernicus (CAMS)",
        "Licence / Access": "Open (Copernicus); attribution required",
        "Known Caveats": "Includes wind-blown dust, so it spikes far above PM2.5 during dust "
                         "episodes (probed p98 of 1184 ug/m3 over N. India). Same model and "
                         "resolution caveats as PM2.5.",
        "Status Notes": "Pairs with cams_dust_aod for dust-driven air quality events.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
        "Verified On": V2}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Sand and Dust Storm",
        "Layer Name": "Dust forecast (CAMS)",
        "Layer ID (app)": "cams_dust_aod",
        "Type": "Atmospheric composition model (assimilated)",
        "EW Role": "Forecast",
        "GEE Asset ID": "ECMWF/CAMS/NRT",
        "Band(s) Used": "dust_aerosol_optical_depth_at_550nm_surface",
        "Native Resolution": "44 km",
        "Temporal Resolution": "3-hourly steps",
        "Forecast Horizon": "5 days",
        "Data Latency": "~0 (forecast)",
        "Update Cadence": "2 runs/day (00z, 12z)",
        "Actionable Lead Time": "1-5 days",
        "Default Window (days)": 3, "Max Window (days)": 5,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "AOD", "App Default Threshold": 0.5,
        "Slider Min": 0, "Slider Max": 2.5,
        "Reference Threshold": "No formal warning standard; probed Sahara/Sahel median 0.58, "
                               "p90 1.02 vs Kenya median 0.016",
        "Reference Source": "Probed against known dust source regions",
        "Threshold Basis": "Probed default",
        "Processing / Transform": "Direct AOD value, max over window",
        "Collection Filters": "-",
        "Observed Range (probe)": "3d max: Sahara/Sahel p50 0.58 p98 1.37; Arabian p50 0.42; "
                                  "Kenya p50 0.016 (~36x contrast)",
        "Coverage": "Global", "Provider": "ECMWF / Copernicus (CAMS)",
        "Licence / Access": "Open (Copernicus); attribution required",
        "Known Caveats": "Models dust as its own aerosol species, so unlike the Sentinel-5P "
                         "aerosol index it separates dust from smoke. Optical depth is a "
                         "column measure — high AOD does not always mean high ground-level "
                         "dust concentration; pair with cams_pm10 for surface impact.",
        "Status Notes": "Forward-looking dust layer; s5p_aer_ai retained alongside it for "
                        "higher-resolution (1.1 km) observed dust.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
        "Verified On": V2}),

    # ── Air quality: Sentinel-5P columns (deactivated, superseded by CAMS) ───
    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "Nitrogen dioxide (Sentinel-5P)",
        "Layer ID (app)": "s5p_no2",
        "Type": "Satellite trace-gas column",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "COPERNICUS/S5P/NRTI/L3_NO2",
        "Band(s) Used": "NO2_column_number_density",
        "Native Resolution": "1.1 km (resampled)",
        "Temporal Resolution": "Daily overpass",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~5 h",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "mean, max", "Default Aggregation": "mean",
        "Units (app)": "umol/m2", "App Default Threshold": 60,
        "Slider Min": 0, "Slider Max": 400,
        "Reference Threshold": "WHO 2021 AQG NO2: 25 ug/m3 (24-h mean) — SURFACE concentration, "
                               "NOT directly convertible to a satellite total column",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Probed default — no column-equivalent standard",
        "Processing / Transform": "mol/m2 x 1e6 -> umol/m2 (raw ~5e-5 is unusable on a slider)",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d mean 35-103 umol/m2 (Kenya)",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "TOTAL COLUMN density, not surface concentration — WHO guideline values "
                         "are not directly comparable and no fixed conversion exists (it depends "
                         "on boundary-layer height and vertical profile). Cloud-free retrievals "
                         "only; persistent cloud leaves gaps.",
        "Status Notes": "DEACTIVATED (active=False): superseded by cams_pm25/cams_pm10 — surface concentration, WHO-comparable, and forward-looking. Entry retained with verified metadata. "
                        "Traffic/combustion marker; best used for relative change, not compliance.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_NO2",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "Sulphur dioxide (Sentinel-5P)",
        "Layer ID (app)": "s5p_so2",
        "Type": "Satellite trace-gas column",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "COPERNICUS/S5P/NRTI/L3_SO2",
        "Band(s) Used": "SO2_column_number_density",
        "Native Resolution": "1.1 km (resampled)",
        "Temporal Resolution": "Daily overpass",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~20 h",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "mean, max", "Default Aggregation": "mean",
        "Units (app)": "umol/m2", "App Default Threshold": 200,
        "Slider Min": -500, "Slider Max": 2000,
        "Reference Threshold": "WHO 2021 AQG SO2: 40 ug/m3 (24-h mean) — SURFACE concentration, "
                               "NOT directly convertible to a satellite total column",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Probed default — no column-equivalent standard",
        "Processing / Transform": "mol/m2 x 1e6 -> umol/m2",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d mean -883 to 943 umol/m2 (Kenya)",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "Low values are genuinely NEGATIVE (retrieval noise) — verified down to "
                         "-883 umol/m2 — hence the negative slider minimum. Weak signal except "
                         "near volcanic plumes and large point sources. Total column, not surface.",
        "Status Notes": "DEACTIVATED (active=False): superseded by cams_pm25/cams_pm10 — surface concentration, WHO-comparable, and forward-looking. Entry retained with verified metadata. "
                        "Most useful for volcanic degassing and industrial plumes.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_SO2",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "Carbon monoxide (Sentinel-5P)",
        "Layer ID (app)": "s5p_co",
        "Type": "Satellite trace-gas column",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "COPERNICUS/S5P/NRTI/L3_CO",
        "Band(s) Used": "CO_column_number_density",
        "Native Resolution": "1.1 km (resampled)",
        "Temporal Resolution": "Daily overpass",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~3 h",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "mean, max", "Default Aggregation": "mean",
        "Units (app)": "mmol/m2", "App Default Threshold": 40,
        "Slider Min": 0, "Slider Max": 200,
        "Reference Threshold": "WHO 2021 AQG CO: 4 mg/m3 (24-h mean) — SURFACE concentration, "
                               "NOT directly convertible to a satellite total column",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Probed default — no column-equivalent standard",
        "Processing / Transform": "mol/m2 x 1e3 -> mmol/m2",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d mean 16-49 mmol/m2 (Kenya)",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "Total column; strongly influenced by biomass burning plumes transported "
                         "from far upwind, so a high value does not imply a local source. "
                         "Not comparable to WHO surface guidelines.",
        "Status Notes": "DEACTIVATED (active=False): superseded by cams_pm25/cams_pm10 — surface concentration, WHO-comparable, and forward-looking. Entry retained with verified metadata. "
                        "Good biomass-burning tracer; pairs with the FIRMS layers.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_CO",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "Ozone column (Sentinel-5P)",
        "Layer ID (app)": "s5p_o3",
        "Type": "Satellite trace-gas column",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "COPERNICUS/S5P/NRTI/L3_O3",
        "Band(s) Used": "O3_column_number_density",
        "Native Resolution": "1.1 km (resampled)",
        "Temporal Resolution": "Daily overpass",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~20 h",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "mean, max", "Default Aggregation": "mean",
        "Units (app)": "mmol/m2", "App Default Threshold": 130,
        "Slider Min": 80, "Slider Max": 200,
        "Reference Threshold": "WHO 2021 AQG O3: 100 ug/m3 (8-h mean) — SURFACE concentration; "
                               "this band is the TOTAL column, dominated by stratospheric ozone",
        "Reference Source": "WHO Global Air Quality Guidelines 2021",
        "Threshold Basis": "Probed default — not a health metric",
        "Processing / Transform": "mol/m2 x 1e3 -> mmol/m2",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d mean 126-133 mmol/m2 (Kenya)",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "TOTAL ozone column is ~90% stratospheric — it is NOT a surface "
                         "air-quality measure and should not be read as ground-level ozone "
                         "exposure. Very narrow natural range (126-133 in probe), so thresholding "
                         "is near-binary. Included for completeness; low EW value.",
        "Status Notes": "DEACTIVATED (active=False): superseded by cams_pm25/cams_pm10 — surface concentration, WHO-comparable, and forward-looking. Entry retained with verified metadata. "
                        "Was the weakest early-warning layer of the set (total column is ~90% stratospheric).",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_O3",
        "Verified On": V}),

    dict(Status="Implemented", **{
        "Hazard Topic": "Sand and Dust Storm",
        "Layer Name": "Absorbing aerosol index (Sentinel-5P)",
        "Layer ID (app)": "s5p_aer_ai",
        "Type": "Satellite aerosol index",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "COPERNICUS/S5P/NRTI/L3_AER_AI",
        "Band(s) Used": "absorbing_aerosol_index",
        "Native Resolution": "1.1 km (resampled)",
        "Temporal Resolution": "Daily overpass",
        "Forecast Horizon": "n/a (observed)",
        "Data Latency": "~3 h",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "Nowcast",
        "Default Window (days)": 14, "Max Window (days)": 30,
        "Aggregation Options": "max, mean", "Default Aggregation": "max",
        "Units (app)": "index", "App Default Threshold": 1.0,
        "Slider Min": -2, "Slider Max": 6,
        "Reference Threshold": "UVAI > ~1.0 commonly used to indicate absorbing aerosols "
                               "(dust / smoke)",
        "Reference Source": "ESA Sentinel-5P L2 Aerosol Index technical guide; EUMETSAT dust guide",
        "Threshold Basis": "Matches published practice",
        "Processing / Transform": "Direct index value, max over window",
        "Collection Filters": "-",
        "Observed Range (probe)": "14d max -0.76 to 2.43 (Kenya)",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "Does not separate DUST from SMOKE — both are UV-absorbing, so a fire "
                         "plume raises this alongside a dust event. Sensitive to aerosol layer "
                         "height; low-altitude dust is under-detected. Column measure: high AAI "
                         "does not always mean high ground-level concentration.",
        "Status Notes": "Kept ACTIVE alongside cams_dust_aod: at 1.1 km it is ~40x finer than "
                        "CAMS (44 km) and is an observation rather than a model, so the two "
                        "complement each other for dust.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_AER_AI",
        "Verified On": V}),

    # ── CANDIDATES ──────────────────────────────────────────────────────────
    dict(Status="Candidate", **{
        "Hazard Topic": "Multi (temp / precip / wind)",
        "Layer Name": "WeatherNext 2 (Google DeepMind)",
        "Layer ID (app)": "-",
        "Type": "AI/ML numerical weather prediction",
        "EW Role": "Forecast",
        "GEE Asset ID": "projects/gcp-public-data-weathernext/assets/weathernext_2_0_0",
        "Band(s) Used": "100+ surface & pressure-level fields (temp, U/V wind, precip, humidity)",
        "Native Resolution": "~25 km",
        "Temporal Resolution": "6-hourly init & lead steps",
        "Forecast Horizon": "15 days",
        "Data Latency": "Unknown (no access)",
        "Update Cadence": "4x daily (00/06/12/18z)",
        "Actionable Lead Time": "1-15 days",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-",
        "Threshold Basis": "-",
        "Processing / Transform": "TBD — would follow the gfs_* pattern once access is granted",
        "Collection Filters": "TBD (init time / lead time selection required)",
        "Observed Range (probe)": "Not readable — access denied",
        "Coverage": "Global", "Provider": "Google DeepMind",
        "Licence / Access": "RESTRICTED — data request form required; historic data CC BY 4.0, "
                            "real-time under GDM Experimental Terms",
        "Known Caveats": "Access is gated behind a WeatherNext Data Request form; the "
                         "unicef-ccri service account is currently denied (asset id verified "
                         "correct, error is authorization). Google states the data is "
                         "'intended for experimental modelling only and is not intended, "
                         "validated, or approved for real world use' — which is a material "
                         "constraint for an operational early-warning tool.",
        "Status Notes": "Add once the request form is approved for the service account. "
                        "Note the experimental-use disclaimer before operational reliance.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/"
                       "projects_gcp-public-data-weathernext_assets_weathernext_2_0_0",
        "Verified On": "2026-08-20"}),

    dict(Status="Candidate", **{
        "Hazard Topic": "River Flood",
        "Layer Name": "GloFAS river discharge forecast",
        "Layer ID (app)": "-",
        "Type": "Hydrological forecast",
        "EW Role": "Forecast",
        "GEE Asset ID": "Not in GEE public catalog (Copernicus CEMS / ECMWF API)",
        "Band(s) Used": "river discharge (dis24)",
        "Native Resolution": "~5 km",
        "Temporal Resolution": "Daily",
        "Forecast Horizon": "30 days",
        "Data Latency": "~1 day",
        "Update Cadence": "Daily",
        "Actionable Lead Time": "1-30 days",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "m3/s", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "GloFAS uses 2/5/20-year return period exceedance for alerts",
        "Reference Source": "Copernicus Emergency Management Service — GloFAS",
        "Threshold Basis": "Would use published return-period alerts",
        "Processing / Transform": "N/A — would require ingest outside GEE",
        "Collection Filters": "-",
        "Observed Range (probe)": "Not probed — not a GEE asset",
        "Coverage": "Global", "Provider": "Copernicus EMS / ECMWF",
        "Licence / Access": "Open, but requires CDS API ingest",
        "Known Caveats": "Would be the single most operationally relevant flood-warning layer "
                         "(actual river discharge vs rainfall proxy), but it is NOT in the GEE "
                         "public catalog. Adding it means an ingest pipeline into a GEE asset, "
                         "which breaks this tab's 'read live from the catalog' design.",
        "Status Notes": "Highest-value future addition for flood EW. Requires a scripts/ "
                        "ingest pipeline similar to the infrastructure one.",
        "Catalog URL": "https://global-flood.emergency.copernicus.eu/",
        "Verified On": "2026-08-20"}),

    # ── REJECTED ────────────────────────────────────────────────────────────
    dict(Status="Rejected", **{
        "Hazard Topic": "River Flood",
        "Layer Name": "CHIRPS-GEFS rainfall forecast",
        "Layer ID (app)": "-", "Type": "Bias-corrected precipitation forecast",
        "EW Role": "Forecast",
        "GEE Asset ID": "No such public GEE collection",
        "Band(s) Used": "-", "Native Resolution": "-", "Temporal Resolution": "-",
        "Forecast Horizon": "-", "Data Latency": "-", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "UCSB-CHG/CHIRPS/DAILY: newest image 461 h old, 0 future images",
        "Coverage": "50N-50S", "Provider": "UCSB Climate Hazards Center",
        "Licence / Access": "Open",
        "Known Caveats": "The GEFS-forced CHIRPS forecast product is not exposed in the GEE "
                         "public catalog. Plain CHIRPS DAILY is observational and ~19 days "
                         "stale — presenting it as a forecast would mislead.",
        "Status Notes": "REJECTED: no forecast collection available in GEE.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "Fire",
        "Layer Name": "FIRMS Fire Radiative Power / detection count",
        "Layer ID (app)": "-", "Type": "Satellite active-fire metric",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "FIRMS (bands do not exist)",
        "Band(s) Used": "-", "Native Resolution": "1 km", "Temporal Resolution": "Daily",
        "Forecast Horizon": "-", "Data Latency": "-", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "Available bands verified: T21, confidence, line_number only",
        "Coverage": "Global", "Provider": "NASA FIRMS",
        "Licence / Access": "Open",
        "Known Caveats": "The GEE FIRMS collection exposes ONLY T21, confidence and line_number. "
                         "There is no FRP band and no detection-count band.",
        "Status Notes": "REJECTED as specified; replaced by firms_brightness (T21) and the "
                        "derived firms_fire_days.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/FIRMS",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "Tropical Storm",
        "Layer Name": "GFS wind gust",
        "Layer ID (app)": "-", "Type": "Numerical weather prediction",
        "EW Role": "Forecast",
        "GEE Asset ID": "NOAA/GFS0P25 (band does not exist)",
        "Band(s) Used": "-", "Native Resolution": "27.8 km", "Temporal Resolution": "-",
        "Forecast Horizon": "-", "Data Latency": "-", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "9 bands verified; no gust band present",
        "Coverage": "Global", "Provider": "NOAA NCEP",
        "Licence / Access": "Public domain",
        "Known Caveats": "GFS0P25 in GEE carries 9 bands, none of them a gust field.",
        "Status Notes": "REJECTED: replaced by gfs_wind (derived sustained speed from U/V).",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "River Flood",
        "Layer Name": "GPM IMERG V06",
        "Layer ID (app)": "-", "Type": "Satellite precipitation estimate",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "NASA/GPM_L3/IMERG_V06",
        "Band(s) Used": "precipitationCal", "Native Resolution": "11.1 km",
        "Temporal Resolution": "30-minute",
        "Forecast Horizon": "-", "Data Latency": "-", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "Newest image 2024-06-02 (19,378 h stale)",
        "Coverage": "60N-60S", "Provider": "NASA / JAXA",
        "Licence / Access": "Open",
        "Known Caveats": "Superseded by V07. Note the band rename: V06 'precipitationCal' -> "
                         "V07 'precipitation'.",
        "Status Notes": "REJECTED: superseded; V07 implemented instead.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V06",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "Fire",
        "Layer Name": "VIIRS VNP14A1 active fire",
        "Layer ID (app)": "-", "Type": "Satellite active-fire detection",
        "EW Role": "Monitoring (NRT)",
        "GEE Asset ID": "NOAA/VIIRS/001/VNP14A1",
        "Band(s) Used": "FireMask, MaxFRP", "Native Resolution": "1 km",
        "Temporal Resolution": "Daily",
        "Forecast Horizon": "-", "Data Latency": "-", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "Newest image 2024-06-16 (19,061 h stale); GEE-deprecated",
        "Coverage": "Global", "Provider": "NASA / NOAA",
        "Licence / Access": "Open",
        "Known Caveats": "Flagged deprecated by GEE (superseded by NASA/VIIRS/002/VNP14A1) and "
                         "no longer updating. Has a MaxFRP band, which FIRMS lacks — worth "
                         "re-evaluating the 002 collection later.",
        "Status Notes": "REJECTED: stale/deprecated. Re-evaluate NASA/VIIRS/002/VNP14A1 "
                        "as a future FRP source.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_VIIRS_001_VNP14A1",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "Multi",
        "Layer Name": "NOAA CFSv2 6-hourly",
        "Layer ID (app)": "-", "Type": "Seasonal forecast model output",
        "EW Role": "Forecast",
        "GEE Asset ID": "NOAA/CFSV2/FOR6H",
        "Band(s) Used": "22 bands", "Native Resolution": "~22 km",
        "Temporal Resolution": "6-hourly",
        "Forecast Horizon": "-", "Data Latency": "~47 h", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "0 future-dated images in a 16-day window; 47 h latency",
        "Coverage": "Global", "Provider": "NOAA NCEP",
        "Licence / Access": "Public domain",
        "Known Caveats": "Contains no future-dated imagery in GEE and lags ~2 days, so it is "
                         "neither a usable forecast nor a live monitoring source.",
        "Status Notes": "REJECTED: GFS covers the same variables with real forecast steps.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_CFSV2_FOR6H",
        "Verified On": V}),

    dict(Status="Rejected", **{
        "Hazard Topic": "Air Pollution",
        "Layer Name": "Sentinel-5P OFFL (offline) products",
        "Layer ID (app)": "-", "Type": "Satellite trace-gas column",
        "EW Role": "Monitoring",
        "GEE Asset ID": "COPERNICUS/S5P/OFFL/L3_*",
        "Band(s) Used": "same as NRTI", "Native Resolution": "1.1 km",
        "Temporal Resolution": "Daily",
        "Forecast Horizon": "-", "Data Latency": "~222 h", "Update Cadence": "-",
        "Actionable Lead Time": "-",
        "Default Window (days)": "-", "Max Window (days)": "-",
        "Aggregation Options": "-", "Default Aggregation": "-",
        "Units (app)": "-", "App Default Threshold": "-",
        "Slider Min": "-", "Slider Max": "-",
        "Reference Threshold": "-", "Reference Source": "-", "Threshold Basis": "-",
        "Processing / Transform": "-", "Collection Filters": "-",
        "Observed Range (probe)": "OFFL NO2 newest image 222 h old vs NRTI 4.6 h",
        "Coverage": "Global", "Provider": "ESA / Copernicus",
        "Licence / Access": "Open (Copernicus)",
        "Known Caveats": "Higher retrieval quality than NRTI but ~9 days latency, which "
                         "defeats the purpose of a live tab. Worth using for retrospective "
                         "analysis if that is ever added.",
        "Status Notes": "REJECTED for this tab: NRTI chosen for latency.",
        "Catalog URL": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_OFFL_L3_NO2",
        "Verified On": V}),
]

FIELD_DEFS = [
    ("Status", "Implemented = live in the Forecast tab. Candidate = evaluated, worth adding, "
               "blocked on access or ingest. Rejected = evaluated and excluded (reason in Known Caveats)."),
    ("Active", "For Implemented rows only: whether the layer is currently shown in the app. "
               "Read live from forecast_config.py's `active` flag at generation time, so this "
               "column cannot drift from the code. Set active=False to retire a layer from the "
               "UI while keeping its verified metadata and the record of why it existed."),
    ("Hazard Topic", "Maps to the topic vocabulary in config.HAZARD_TOPICS so forecast layers "
                     "sit under the same hazard names as the static catalog."),
    ("Layer Name", "Display label shown in the app's dataset dropdown."),
    ("Layer ID (app)", "Stable key in forecast_config.FORECAST_DATASETS['name'] — used in element "
                       "ids, stores and the JSON export."),
    ("Type", "Nature of the product: model output, satellite observation, or derived metric."),
    ("EW Role", "Forecast = carries future-dated imagery. Monitoring (NRT) = recent observations only. "
                "This drives whether the date picker allows future dates."),
    ("GEE Asset ID", "Earth Engine collection id, read live from the public catalog."),
    ("Band(s) Used", "Band selected, or the bands combined by a derived transform."),
    ("Native Resolution", "Nominal pixel size of the source, verified via projection().nominalScale(). "
                          "NOTE: exposure is always reduced at the 100 m population grid regardless."),
    ("Temporal Resolution", "Spacing of images within the collection."),
    ("Forecast Horizon", "How far ahead the product predicts. 'n/a (observed)' for monitoring layers."),
    ("Data Latency", "Measured age of the newest available image at probe time — the practical "
                     "limit on how current an analysis can be."),
    ("Update Cadence", "How often new imagery is published."),
    ("Actionable Lead Time", "Realistic warning lead time this layer supports operationally."),
    ("Default Window (days)", "Initial date-range width in the picker. NRT layers end at "
                              "today minus latency, or the window returns zero images."),
    ("Max Window (days)", "Hard cap enforced by the date picker (forecast_core.clamp_window)."),
    ("Aggregation Options", "Temporal reducers offered for this layer."),
    ("Default Aggregation", "Reducer preselected — chosen to match how the variable is "
                            "physically meaningful (accumulate rain, peak wind/heat)."),
    ("Units (app)", "Units AFTER the processing transform — what the threshold slider shows."),
    ("App Default Threshold", "Preset exposure threshold. Children are counted where the "
                              "aggregated value is strictly greater than this."),
    ("Slider Min / Slider Max", "Threshold slider bounds, set from probed value ranges."),
    ("Reference Threshold", "Published warning level from a recognised body, where a comparable "
                            "one exists. Where the satellite quantity is not comparable to the "
                            "standard (column density vs surface concentration), the cell says so."),
    ("Reference Source", "Organisation and document behind the reference threshold."),
    ("Threshold Basis", "Provenance of the app default: Matches WMO / Matches static catalog / "
                        "Matches published practice / Probed default / Needs expert review."),
    ("Processing / Transform", "Unit conversion or derivation applied, implemented in "
                               "forecast_core._PRE_TRANSFORMS / _POST_TRANSFORMS."),
    ("Collection Filters", "ee.Filter applied before reduction (e.g. dropping GFS analysis steps)."),
    ("Observed Range (probe)", "Actual values measured during verification, with the test AOI."),
    ("Coverage", "Spatial extent of the product."),
    ("Provider", "Data producer."),
    ("Licence / Access", "Licence terms and any access gate."),
    ("Known Caveats", "Limitations that affect interpretation — read before using a layer "
                      "operationally."),
    ("Status Notes", "Why the layer holds its status, and what would change it."),
    ("Catalog URL", "Earth Engine catalog page or provider documentation."),
    ("Verified On", "Date the asset id, bands and ranges were checked against live GEE."),
]

SOURCES = [
    ("WMO Classification of Tropical Cyclones",
     "https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/"
     "tropical-cyclone-programme-tcp/classification-of-tropical-cyclones",
     "Tropical storm 34-63 kt (17.5-32.4 m/s); hurricane force >64 kt (>33 m/s)"),
    ("WMO Severe Weather Information Centre",
     "https://severeweather.wmo.int/",
     "Heavy rain defined as >=50 mm in 24 h"),
    ("WMO Extreme Heat",
     "https://wmo.int/topics/heatwave",
     "Heatwave = 2-3+ consecutive days above locally-defined thresholds; no single global cutoff"),
    ("UNDRR Hazard Information Profiles — Heatwave (MH0501)",
     "https://www.undrr.org/understanding-disaster-risk/terminology/hips/mh0501",
     "Heatwave hazard definition and characterisation"),
    ("WHO Global Air Quality Guidelines 2021",
     "https://www.ncbi.nlm.nih.gov/books/NBK574591/table/ch3.tab26/",
     "NO2 25 ug/m3, SO2 40 ug/m3, CO 4 mg/m3 (24-h means); O3 100 ug/m3 (8-h). "
     "SURFACE concentrations — not directly comparable to satellite total columns"),
    ("ESA Sentinel-5P L2 Aerosol Index technical guide",
     "https://sentinel.esa.int/web/sentinel/technical-guides/sentinel-5p/level-2/aerosol-index",
     "UVAI > ~1.0 indicates absorbing aerosols (dust / smoke)"),
    ("EUMETSAT Dust Monitoring — Sentinel-5P UVAI",
     "https://dust.trainhub.eumetsat.int/docs/sentinel5p_ai.html",
     "Operational use of UVAI for dust storm detection"),
    ("MODIS Collection 6 Active Fire Product User Guide",
     "https://modis-fire.umd.edu/files/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf",
     "T21 internal algorithm tests: 360 K day / 320 K night"),
    ("Copernicus Emergency Management Service — GloFAS",
     "https://global-flood.emergency.copernicus.eu/",
     "River discharge forecasts with 2/5/20-year return period alert levels"),
    ("WeatherNext 2 (GEE catalog)",
     "https://developers.google.com/earth-engine/datasets/catalog/"
     "projects_gcp-public-data-weathernext_assets_weathernext_2_0_0",
     "Access requires the WeatherNext Data Request form; experimental-use terms apply"),
]

# ---------------------------------------------------------------------------

wb = xlsxwriter.Workbook(OUT)

f_title = wb.add_format({"bold": True, "font_size": 14, "font_color": "#1CABE2"})
f_sub   = wb.add_format({"font_size": 9, "font_color": "#666666", "italic": True})
f_hdr   = wb.add_format({"bold": True, "bg_color": "#1CABE2", "font_color": "white",
                         "text_wrap": True, "valign": "vcenter", "border": 1,
                         "font_size": 9})
f_cell  = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "font_size": 9})
f_impl  = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "font_size": 9,
                         "bold": True, "bg_color": "#E8F6EC", "font_color": "#1B7F3B"})
f_cand  = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "font_size": 9,
                         "bold": True, "bg_color": "#FFF6E0", "font_color": "#9A6700"})
f_rej   = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "font_size": 9,
                         "bold": True, "bg_color": "#FDECEC", "font_color": "#B42318"})
f_defk  = wb.add_format({"bold": True, "valign": "top", "border": 1, "font_size": 9,
                         "bg_color": "#F5F7FA"})
f_link  = wb.add_format({"font_color": "#1CABE2", "underline": 1, "font_size": 9,
                         "text_wrap": True, "valign": "top", "border": 1})

STATUS_FMT = {"Implemented": f_impl, "Candidate": f_cand, "Rejected": f_rej}

# ── Sheet 1: Forecast Layers ────────────────────────────────────────────────
ws = wb.add_worksheet("Forecast Layers")
ws.write(0, 0, "GCHD — Forecast & Early Warning Data Catalog", f_title)
ws.write(1, 0, f"Layers behind the Forecast & Live tab. Verified against the live GEE catalog "
               f"on {V}. Objective: early warning / forecasting of child hazard exposure. "
               f"Status column separates implemented layers from candidates and rejected ones.",
         f_sub)

HDR_ROW = 3
for c, (name, width) in enumerate(COLUMNS):
    ws.write(HDR_ROW, c, name, f_hdr)
    ws.set_column(c, c, width)
ws.set_row(HDR_ROW, 34)

# `Active` is read from the live forecast_config rather than hand-maintained
# here, so the workbook can never disagree with what the app actually shows.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "app"))
try:
    from forecast_config import FORECAST_MAP as _LIVE
except ImportError as _e:
    # Narrow: only a genuinely missing module is tolerable. Anything else is a
    # real bug and should not be masked into a column full of "?".
    print(f"WARNING: forecast_config not importable ({_e}); "
          f"Active column will show '?'")
    _LIVE = {}


def _active_of(row):
    if row["Status"] != "Implemented":
        return "—"
    cfg = _LIVE.get(row.get("Layer ID (app)"))
    if cfg is None:
        return "?"
    return "Yes" if cfg.get("active", True) else "No"


for r, row in enumerate(ROWS, start=HDR_ROW + 1):
    for c, (name, _) in enumerate(COLUMNS):
        val = _active_of(row) if name == "Active" else row.get(name, "")
        if name == "Status":
            ws.write(r, c, val, STATUS_FMT.get(val, f_cell))
        elif name == "Catalog URL" and isinstance(val, str) and val.startswith("http"):
            ws.write_url(r, c, val, f_link, string=val)
        else:
            ws.write(r, c, val, f_cell)
    ws.set_row(r, 58)

ws.freeze_panes(HDR_ROW + 1, 4)
ws.autofilter(HDR_ROW, 0, HDR_ROW + len(ROWS), len(COLUMNS) - 1)

# ── Sheet 2: Field Definitions ──────────────────────────────────────────────
ws2 = wb.add_worksheet("Field Definitions")
ws2.write(0, 0, "Field Definitions", f_title)
ws2.write(1, 0, "What each column in 'Forecast Layers' means.", f_sub)
ws2.write(3, 0, "Field", f_hdr)
ws2.write(3, 1, "Definition", f_hdr)
ws2.set_column(0, 0, 26)
ws2.set_column(1, 1, 110)
for i, (k, v) in enumerate(FIELD_DEFS, start=4):
    ws2.write(i, 0, k, f_defk)
    ws2.write(i, 1, v, f_cell)
    ws2.set_row(i, 30)

start = 4 + len(FIELD_DEFS) + 2
ws2.write(start, 0, "Reference sources", f_title)
ws2.write(start + 1, 0, "Source", f_hdr)
ws2.write(start + 1, 1, "URL / What it defines", f_hdr)
for i, (nm, url, what) in enumerate(SOURCES, start=start + 2):
    ws2.write(i, 0, nm, f_defk)
    ws2.write_url(i, 1, url, f_link, string=f"{url}  —  {what}")
    ws2.set_row(i, 30)

note = start + 2 + len(SOURCES) + 1
ws2.write(note, 0, "Important", f_defk)
ws2.write(note, 1,
          "Satellite trace-gas layers (Sentinel-5P NO2/SO2/CO/O3) measure TOTAL COLUMN density "
          "(mol/m2). WHO air quality guidelines are SURFACE concentrations (ug/m3). There is no "
          "fixed conversion between them — it depends on boundary layer height and vertical "
          "profile. The WHO values are listed for context only; the app's thresholds are "
          "probed defaults and should be reviewed by an air quality specialist before any "
          "operational alerting.", f_cell)
ws2.set_row(note, 76)

wb.close()
print("wrote:", OUT)
print("rows:", len(ROWS),
      "| implemented:", sum(1 for r in ROWS if r["Status"] == "Implemented"),
      "| candidate:",   sum(1 for r in ROWS if r["Status"] == "Candidate"),
      "| rejected:",    sum(1 for r in ROWS if r["Status"] == "Rejected"))
print("columns:", len(COLUMNS))
