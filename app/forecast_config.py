# =============================================================================
# forecast_config.py — Forecast & near-real-time dataset catalog
#
# Deliberately separate from config.HAZARDS. Those are frozen return-period
# assets folded into gee_core.build_core_images(), which is @lru_cache(maxsize=1)
# and must stay date-independent: a date-parameterised layer inside it would
# either poison that cache or force a rebuild per date range. Everything here is
# loaded on demand with an explicit time window instead.
#
# No GEE import — same rule as config.py, so this loads instantly.
#
# Every id, band name, scale and value range below was verified against the live
# catalog with the unicef-ccri service account (Aug 2026). Datasets that did not
# resolve were dropped rather than shipped broken — see DROPPED at the bottom for
# what was rejected and why, so nobody re-adds them from memory.
# =============================================================================

# Reducers offered in the UI. Keys match ee.ImageCollection method names.
REDUCERS = {
    "sum":    "Total over period",
    "mean":   "Average over period",
    "max":    "Peak over period",
    "min":    "Minimum over period",
    "median": "Median over period",
}

# ── Entry schema ─────────────────────────────────────────────────────────────
#   id                GEE ImageCollection id
#   name              stable key (element ids, stores, export columns)
#   label             UI display name
#   topic             an existing config.HAZARD_TOPICS key, so forecast results
#                     sit under the same hazard vocabulary as the static catalog
#   kind              "forecast" (future-dated imagery) | "nrt" (recent past)
#   band              band to select, or None when `transform` builds it
#   reducers          allowed reducers; first is the default
#   max_span_days     hard cap on the date-range picker
#   default_span_days initial window width
#   lag_days          data latency — the default window ends this many days back,
#                     otherwise NRT collections silently return zero images
#   threshold/min/max slider contract, identical to config.HAZARDS
#   units             shown next to the threshold input and in the export
#   palette           5 stops, same convention as config.HAZARD_VIS_PALETTES
#   scale             nominalScale in m (verified, used for tile stretch only —
#                     the exposure reduction always runs at the population grid)
#   filters           [{"prop","op","value"}] applied before reduction
#   transform         key into forecast_core._TRANSFORMS for derived bands
#   note              caveat shown under the dataset in the panel

FORECAST_DATASETS = [
    # ── Rainfall / flood ────────────────────────────────────────────────────
    {
        "id": "NOAA/GFS0P25", "name": "gfs_precip",
        "label": "Rainfall forecast (NOAA GFS)", "topic": "River Flood",
        "kind": "forecast", "band": "total_precipitation_surface",
        "reducers": ["sum", "max"], "max_span_days": 10, "default_span_days": 3,
        "lag_days": 0,
        "threshold": 50, "min": 0, "max": 400, "units": "mm",
        "palette": ["#cfe8ff", "#8fcbff", "#1d7bff", "#0654be", "#00357d"],
        "scale": 27830,
        # forecast_hours=0 is the analysis image and carries NO precipitation
        # band; selecting across it fails outright. Values are per-interval
        # amounts (verified non-monotonic across forecast hours), so summing the
        # window is correct and does not double-count a running total.
        "filters": [{"prop": "forecast_hours", "op": "gt", "value": 0}],
        "note": "GFS runs 4x daily out to 16 days. Rainfall is accumulated over "
                "the selected window.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
    },
    {
        "id": "NASA/GPM_L3/IMERG_V07", "name": "imerg_precip",
        "label": "Observed rainfall (GPM IMERG)", "topic": "River Flood",
        "kind": "nrt", "band": "precipitation",
        "reducers": ["sum", "max"], "max_span_days": 30, "default_span_days": 7,
        "lag_days": 3,
        "threshold": 100, "min": 0, "max": 800, "units": "mm",
        "palette": ["#cfe8ff", "#8fcbff", "#1d7bff", "#0654be", "#00357d"],
        "scale": 11132,
        # Band is mm/hr on half-hourly images -> x0.5 per image to reach mm.
        "transform": "imerg_mm",
        "note": "Half-hourly satellite rainfall, ~2 day latency. Converted from "
                "mm/hr to total mm over the window.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V07",
    },

    # ── Wind / storm ────────────────────────────────────────────────────────
    {
        "id": "NOAA/GFS0P25", "name": "gfs_wind",
        "label": "Wind speed forecast (NOAA GFS)", "topic": "Tropical Storm",
        "kind": "forecast", "band": None,
        "reducers": ["max", "mean"], "max_span_days": 10, "default_span_days": 3,
        "lag_days": 0,
        "threshold": 17.5, "min": 0, "max": 80, "units": "m/s",
        "palette": ["#e4eff2", "#c3dce7", "#9ebdd2", "#7e8ab0", "#6d6c91"],
        "scale": 27830,
        # GFS0P25 has no gust band (verified) — wind speed is derived as
        # hypot(u10, v10) from the two components.
        "transform": "wind_speed",
        "filters": [{"prop": "forecast_hours", "op": "gt", "value": 0}],
        "note": "10 m wind speed derived from U/V components. 17.5 m/s matches "
                "the tropical-storm threshold used in the static catalog.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
    },

    # ── Heat ────────────────────────────────────────────────────────────────
    {
        "id": "NOAA/GFS0P25", "name": "gfs_temp",
        "label": "Air temperature forecast (NOAA GFS)", "topic": "Extreme Heat",
        "kind": "forecast", "band": "temperature_2m_above_ground",
        "reducers": ["max", "mean", "min"], "max_span_days": 10,
        "default_span_days": 3, "lag_days": 0,
        "threshold": 35, "min": -20, "max": 55, "units": "°C",
        "palette": ["#f8e593", "#f8cc14", "#F89800", "#F86800", "#F83000"],
        "scale": 27830,
        # Already °C in this collection (verified 13.7-38.9 over Kenya) — no
        # Kelvin conversion, unlike MODIS LST below.
        "filters": [{"prop": "forecast_hours", "op": "gt", "value": 0}],
        "note": "2 m air temperature, already in °C. 35 °C matches the static "
                "extreme-heat threshold.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25",
    },
    {
        "id": "MODIS/061/MOD11A1", "name": "modis_lst",
        "label": "Land surface temperature (MODIS)", "topic": "Heatwave",
        "kind": "nrt", "band": "LST_Day_1km",
        "reducers": ["max", "mean"], "max_span_days": 30, "default_span_days": 8,
        "lag_days": 3,
        "threshold": 40, "min": -20, "max": 70, "units": "°C",
        "palette": ["#f8e593", "#f8cc14", "#F89800", "#F86800", "#F83000"],
        "scale": 1000,
        # Stored as scaled uint16: raw x0.02 - 273.15 -> °C (raw 13932-16112
        # over Kenya = 5.5-48.1 °C). Without this the threshold is meaningless.
        "transform": "modis_lst_c",
        "note": "Daytime land-surface temperature (not air temperature) — runs "
                "hotter than a 2 m reading. Clear-sky pixels only.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD11A1",
    },

    # ── Fire ────────────────────────────────────────────────────────────────
    {
        "id": "FIRMS", "name": "firms_brightness",
        "label": "Active fire brightness (FIRMS)", "topic": "Fire",
        "kind": "nrt", "band": "T21",
        "reducers": ["max", "mean"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 3,
        "threshold": 330, "min": 300, "max": 500, "units": "K",
        "palette": ["#F0F0DC", "#FFD282", "#FF8C3C", "#DC3C1E", "#5A0000"],
        "scale": 1000,
        "note": "Fire-pixel brightness temperature. FIRMS carries no FRP or "
                "detection-count band, so brightness is the intensity measure.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/FIRMS",
    },
    {
        "id": "FIRMS", "name": "firms_fire_days",
        "label": "Active fire days (FIRMS)", "topic": "Fire",
        "kind": "nrt", "band": "T21",
        "reducers": ["sum"], "max_span_days": 60, "default_span_days": 30,
        "lag_days": 3,
        "threshold": 1, "min": 0, "max": 60, "units": "days",
        "palette": ["#F0F0DC", "#FFD282", "#FF8C3C", "#DC3C1E", "#5A0000"],
        "scale": 1000,
        # Counts images where a fire pixel was detected, since there is no
        # native count band: each image -> 1 where T21 is present, else 0.
        "transform": "fire_day_count",
        "note": "Number of days with a detected fire pixel in the window.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/FIRMS",
    },

    # ── Air quality (CAMS — SURFACE concentrations, and a real forecast) ─────
    # Preferred over the Sentinel-5P columns below: CAMS assimilates satellite
    # observations into a model and outputs surface mass concentration, which is
    # what health guidelines are written against and what "children exposed"
    # actually means. The S5P entries measure total column and are retained but
    # inactive — see the note on each.
    {
        "id": "ECMWF/CAMS/NRT", "name": "cams_pm25",
        "label": "PM2.5 forecast (CAMS)", "topic": "Air Pollution",
        "kind": "forecast", "band": "particulate_matter_d_less_than_25_um_surface",
        "reducers": ["max", "mean"], "max_span_days": 5, "default_span_days": 3,
        "lag_days": 0,
        "threshold": 15, "min": 0, "max": 250, "units": "µg/m³",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 44453,
        # Native kg/m³ (~1e-8) is unreadable on a slider; x1e9 -> µg/m³, the
        # unit every air-quality guideline is expressed in.
        "transform": "kgm3_to_ugm3",
        "note": "Surface PM2.5, directly comparable to the WHO guideline of "
                "15 µg/m³ (24-hour mean). Two 5-day forecasts per day.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
    },
    {
        "id": "ECMWF/CAMS/NRT", "name": "cams_pm10",
        "label": "PM10 forecast (CAMS)", "topic": "Air Pollution",
        "kind": "forecast", "band": "particulate_matter_d_less_than_10_um_surface",
        "reducers": ["max", "mean"], "max_span_days": 5, "default_span_days": 3,
        "lag_days": 0,
        "threshold": 45, "min": 0, "max": 600, "units": "µg/m³",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 44453,
        "transform": "kgm3_to_ugm3",
        "note": "Surface PM10, comparable to the WHO guideline of 45 µg/m³ "
                "(24-hour mean). Includes coarse dust, so it spikes far above "
                "PM2.5 during dust events.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
    },
    {
        "id": "ECMWF/CAMS/NRT", "name": "cams_dust_aod",
        "label": "Dust forecast (CAMS)", "topic": "Sand and Dust Storm",
        "kind": "forecast", "band": "dust_aerosol_optical_depth_at_550nm_surface",
        "reducers": ["max", "mean"], "max_span_days": 5, "default_span_days": 3,
        "lag_days": 0,
        "threshold": 0.5, "min": 0, "max": 2.5, "units": "AOD",
        "palette": ["#faf0dc", "#e6d2b4", "#c8aa82", "#a0785a", "#261f28"],
        "scale": 44453,
        "note": "Dust-specific aerosol optical depth — unlike the Sentinel-5P "
                "aerosol index it separates dust from smoke, and it forecasts "
                "ahead rather than reporting the last overpass.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_CAMS_NRT",
    },

    # ── Air quality (Sentinel-5P NRTI — total column, superseded by CAMS) ─────
    {
        "id": "COPERNICUS/S5P/NRTI/L3_NO2", "name": "s5p_no2",
        "label": "Nitrogen dioxide (Sentinel-5P)", "topic": "Air Pollution",
        "kind": "nrt", "band": "NO2_column_number_density",
        "reducers": ["mean", "max"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 1,
        "threshold": 60, "min": 0, "max": 400, "units": "µmol/m²",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 1113,
        # Native mol/m² is ~5e-5 — unusable on a slider. x1e6 -> µmol/m².
        "transform": "mol_to_umol",
        "note": "Superseded by cams_pm25/cams_pm10 (surface concentration, WHO-comparable, forecast) — inactive. "
                "Tropospheric NO₂ column, a traffic and combustion marker. "
                "Cloud-free retrievals only.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_NO2",
    },
    {
        "id": "COPERNICUS/S5P/NRTI/L3_SO2", "name": "s5p_so2",
        "label": "Sulphur dioxide (Sentinel-5P)", "topic": "Air Pollution",
        "kind": "nrt", "band": "SO2_column_number_density",
        "reducers": ["mean", "max"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 1,
        "threshold": 200, "min": -500, "max": 2000, "units": "µmol/m²",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 1113,
        "transform": "mol_to_umol",
        # Retrieval noise makes low values genuinely negative (verified
        # -8.8e-4 mol/m² over Kenya), hence the negative slider minimum.
        "note": "Superseded by cams_pm25/cams_pm10 (surface concentration, WHO-comparable, forecast) — inactive. "
                "Volcanic and industrial SO₂. Low values can be negative — this "
                "is retrieval noise, not absence of gas.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_SO2",
    },
    {
        "id": "COPERNICUS/S5P/NRTI/L3_CO", "name": "s5p_co",
        "label": "Carbon monoxide (Sentinel-5P)", "topic": "Air Pollution",
        "kind": "nrt", "band": "CO_column_number_density",
        "reducers": ["mean", "max"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 1,
        "threshold": 40, "min": 0, "max": 200, "units": "mmol/m²",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 1113,
        # ~0.03 mol/m² -> x1000 for mmol/m² (µmol would be unwieldy here).
        "transform": "mol_to_mmol",
        "note": "Superseded by cams_pm25/cams_pm10 (surface concentration, WHO-comparable, forecast) — inactive. "
                "CO column — a biomass-burning and combustion tracer.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_CO",
    },
    {
        "id": "COPERNICUS/S5P/NRTI/L3_O3", "name": "s5p_o3",
        "label": "Ozone column (Sentinel-5P)", "topic": "Air Pollution",
        "kind": "nrt", "band": "O3_column_number_density",
        "reducers": ["mean", "max"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 1,
        "threshold": 130, "min": 80, "max": 200, "units": "mmol/m²",
        "palette": ["#d0dde5", "#99a1b4", "#7c6e87", "#5a4b5e", "#261f27"],
        "scale": 1113,
        "transform": "mol_to_mmol",
        "note": "Superseded by cams_pm25/cams_pm10 (surface concentration, WHO-comparable, forecast) — inactive. "
                "Total ozone column — dominated by stratospheric ozone, so it "
                "is not a surface air-quality measure on its own.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_O3",
    },
    {
        "id": "COPERNICUS/S5P/NRTI/L3_AER_AI", "name": "s5p_aer_ai",
        "label": "Absorbing aerosol index (Sentinel-5P)",
        "topic": "Sand and Dust Storm",
        "kind": "nrt", "band": "absorbing_aerosol_index",
        "reducers": ["max", "mean"], "max_span_days": 30, "default_span_days": 14,
        "lag_days": 1,
        "threshold": 1.0, "min": -2, "max": 6, "units": "index",
        "palette": ["#faf0dc", "#e6d2b4", "#c8aa82", "#a0785a", "#261f28"],
        "scale": 1113,
        "note": "Positive values indicate UV-absorbing aerosols — desert dust "
                "and smoke. Values above ~1 suggest an active dust episode.",
        "source_url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_NRTI_L3_AER_AI",
    },
]

# =============================================================================
# Presentation + provenance metadata
#
# Kept in one table rather than inlined above so the verified technical fields
# (id / band / scale / threshold) stay visually separate from wording that can
# be edited freely. `active` is the switch: set False to hide a dataset from the
# UI without deleting its verified entry or the record of why it exists.
#
# `provider` and `description` are lifted from docs/'Forecast Data catalog.xlsx';
# `note` above stays what it is — a caveat, not a description.
# =============================================================================

_META = {
    "gfs_precip": dict(active=True, provider="NOAA NCEP", temporal="1-hourly steps, 4x daily runs",
        description="Total accumulated precipitation predicted by the NOAA Global Forecast "
                    "System, a physics-based numerical weather model run four times a day out "
                    "to 16 days. Values are summed across the selected window."),
    "imerg_precip": dict(active=True, provider="NASA / JAXA", temporal="30-minute",
        description="Satellite-estimated rainfall that has already fallen, from the GPM "
                    "constellation. Use it to see what actually happened; it is not a forecast "
                    "and lags roughly two days."),
    "gfs_wind": dict(active=True, provider="NOAA NCEP", temporal="1-hourly steps, 4x daily runs",
        description="Sustained 10 m wind speed predicted by NOAA GFS, derived from the U and V "
                    "wind components. The 17.5 m/s default matches the WMO tropical-storm "
                    "threshold and the static hazard catalog."),
    "gfs_temp": dict(active=True, provider="NOAA NCEP", temporal="1-hourly steps, 4x daily runs",
        description="Predicted air temperature 2 m above ground from NOAA GFS — the standard "
                    "height for weather observations, so this is comparable to a thermometer "
                    "reading rather than a surface temperature."),
    "modis_lst": dict(active=True, provider="NASA LP DAAC", temporal="Daily",
        description="Daytime temperature of the ground surface measured by MODIS. This runs "
                    "considerably hotter than air temperature and is observed, not forecast."),
    "firms_brightness": dict(active=True, provider="NASA FIRMS", temporal="Daily composite",
        description="Brightness temperature of pixels where a fire has already been detected. "
                    "FIRMS pixels are algorithm-confirmed detections, so this grades how intense "
                    "confirmed fires are — it does not detect whether fire is present."),
    "firms_fire_days": dict(active=True, provider="NASA FIRMS", temporal="Daily composite",
        description="Count of days within the window on which a fire was detected at each pixel. "
                    "Distinguishes a single flare-up from a sustained burn. Cloud cover and "
                    "overpass timing cause missed days, so this under-counts."),
    # CAMS — surface concentrations, and forward-looking.
    "cams_pm25": dict(active=True, provider="ECMWF / Copernicus (CAMS)",
        temporal="3-hourly steps, 2 runs/day",
        description="Fine particulate matter at the surface, forecast up to 5 days ahead by the "
                    "Copernicus Atmosphere Monitoring Service. PM2.5 penetrates deep into the "
                    "lungs and is the pollutant most strongly linked to child respiratory harm. "
                    "Unlike satellite column measurements this is a ground-level concentration, "
                    "so it can be read directly against health guidelines."),
    "cams_pm10": dict(active=True, provider="ECMWF / Copernicus (CAMS)",
        temporal="3-hourly steps, 2 runs/day",
        description="Coarse particulate matter at the surface, forecast up to 5 days ahead. "
                    "PM10 includes wind-blown dust and so rises sharply during dust events, "
                    "often reaching many times the PM2.5 level."),
    "cams_dust_aod": dict(active=True, provider="ECMWF / Copernicus (CAMS)",
        temporal="3-hourly steps, 2 runs/day",
        description="Forecast optical thickness of airborne desert dust. Because CAMS models "
                    "dust as its own species, this isolates dust from smoke and other aerosols — "
                    "and it looks days ahead rather than reporting a past satellite overpass."),

    # Sentinel-5P — retained but inactive; CAMS supersedes them (see notes).
    "s5p_no2": dict(active=False, provider="ESA / Copernicus", temporal="Daily overpass",
        description="Nitrogen dioxide measured as a TOTAL COLUMN through the atmosphere — a "
                    "traffic and combustion marker. This is not a surface concentration and is "
                    "not directly comparable to WHO air quality guidelines."),
    "s5p_so2": dict(active=False, provider="ESA / Copernicus", temporal="Daily overpass",
        description="Sulphur dioxide total column, strongest near volcanic plumes and heavy "
                    "industry. Low values are genuinely negative because of retrieval noise. "
                    "Not a surface concentration."),
    "s5p_co": dict(active=False, provider="ESA / Copernicus", temporal="Daily overpass",
        description="Carbon monoxide total column, a good biomass-burning tracer. Plumes travel "
                    "far, so a high value need not indicate a local source. Not a surface "
                    "concentration."),
    "s5p_o3": dict(active=False, provider="ESA / Copernicus", temporal="Daily overpass",
        description="TOTAL ozone column, which is roughly 90% stratospheric. This is not a "
                    "ground-level air quality measure and should not be read as ozone exposure."),
    "s5p_aer_ai": dict(active=True, provider="ESA / Copernicus", temporal="Daily overpass",
        description="Index of UV-absorbing aerosols — desert dust and smoke. Values above about "
                    "1 suggest an active dust episode. It does not separate dust from smoke, and "
                    "measures the column rather than ground-level concentration."),
}

# User-facing word for each `kind`. `kind` stays the functional field (it drives
# whether the date picker allows future dates); this is presentation only, so
# the two can never be conflated.
KIND_LABELS = {"forecast": "Forecast", "nrt": "Observation"}

KIND_BLURBS = {
    "forecast": "Predicted conditions — looks ahead",
    "nrt":      "Recently observed — not a forecast",
}

for _d in FORECAST_DATASETS:
    _m = _META.get(_d["name"], {})
    _d["active"]      = _m.get("active", True)
    _d["provider"]    = _m.get("provider", "")
    _d["temporal"]    = _m.get("temporal", "")
    _d["description"] = _m.get("description", _d.get("note", ""))
    _d["kind_label"]  = KIND_LABELS.get(_d["kind"], _d["kind"])

FORECAST_MAP = {d["name"]: d for d in FORECAST_DATASETS}


def active_datasets():
    """Catalog entries with active=True, in config order."""
    return [d for d in FORECAST_DATASETS if d.get("active", True)]


def forecast_groups():
    """{kind_label: [entries]} over active datasets, forecasts first.

    Drives the dataset dropdown: grouping by KIND before topic is what stops a
    GFS forecast and an observed IMERG image sitting side by side under one
    'River Flood' heading with nothing to tell them apart.
    """
    groups = {}
    for kind in ("forecast", "nrt"):                 # forecast first, always
        rows = [d for d in active_datasets() if d["kind"] == kind]
        if rows:
            groups[KIND_LABELS[kind]] = rows
    return groups


# Topic -> [dataset names], ordered to match config.HAZARD_TOPICS.
# Retained for the catalog generator and docs; the dropdown groups by kind.
_TOPIC_ORDER = ["River Flood", "Tropical Storm", "Extreme Heat", "Heatwave",
                "Fire", "Air Pollution", "Sand and Dust Storm"]

FORECAST_TOPICS = {
    t: [d["name"] for d in FORECAST_DATASETS if d["topic"] == t]
    for t in _TOPIC_ORDER
    if any(d["topic"] == t for d in FORECAST_DATASETS)
}

# Fallback palette for a pasted custom asset (no catalog entry to style it).
CUSTOM_PALETTE = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]

# Sentinel name used in element ids / stores for the paste-an-id path.
CUSTOM_NAME = "__custom__"


def forecast_units(name, custom_units=""):
    """Units string for a dataset name, or the caller's units for a custom id."""
    cfg = FORECAST_MAP.get(name)
    return cfg["units"] if cfg else custom_units


def is_forecast_kind(name):
    """True when the dataset carries future-dated imagery (allows future dates
    in the picker). Custom assets are treated as observational."""
    return (FORECAST_MAP.get(name, {}).get("kind") == "forecast")


# =============================================================================
# DROPPED — verified unavailable or misleading. Do not re-add without probing.
#
#   Weather Next (projects/gcp-public-data-weathernext/...)
#       Both candidate asset paths return "does not exist or doesn't allow this
#       operation" for the unicef-ccri service account. Restricted collection.
#   CHIRPS-GEFS
#       No such public GEE collection. UCSB-CHG/CHIRPS/DAILY is observational,
#       ~19 days stale, and carries no future-dated imagery — not a forecast.
#   NASA/GPM_L3/IMERG_V06
#       Superseded; newest image 2024-06-02. V07 is current (band renamed from
#       precipitationCal to precipitation).
#   NOAA/VIIRS/001/VNP14A1
#       Deprecated by GEE; newest image 2024-06-16. FIRMS covers active fire.
#   FIRMS FRP / detection count
#       FIRMS exposes only T21, confidence and line_number — there is no FRP
#       band and no count band. Covered instead by firms_brightness and the
#       derived firms_fire_days above.
#   GFS wind gust
#       NOAA/GFS0P25 has no gust band; gfs_wind derives speed from U/V instead.
#   NOAA/CFSV2/FOR6H
#       No future-dated imagery and ~47 h latency — neither forecast nor live.
#   COPERNICUS/S5P/OFFL/*
#       ~222 h latency versus 3-20 h for the NRTI variants used above.
# =============================================================================
