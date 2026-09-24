# =============================================================================
# elnino_config.py — El Niño / SEAS5 seasonal outlook catalog
#
# Separate from forecast_config.py for the same reason that one is separate from
# config.HAZARDS: the layers here are neither a frozen return-period asset nor a
# date-windowed live collection. They are a SINGLE forecast issue (one init
# month) whose images are selected by period, not by date range — so the date
# picker, reducer and lag machinery of the Forecast tab do not apply at all.
#
# No GEE import — same rule as config.py and forecast_config.py, so this loads
# instantly and can be imported by tooling that has no Earth Engine session.
#
# Produced by scripts/el_nino/download_ecmwf_prec.py. That script is the single
# authority for what is in these assets; everything below must agree with it.
# =============================================================================

# ── The uploaded collections ────────────────────────────────────────────────
ELNINO_ROOT = "projects/unicef-ccri/assets/el_nino"

TERCILES_COLLECTION    = f"{ELNINO_ROOT}/terciles"
RAW_FORECAST_COLLECTION = f"{ELNINO_ROOT}/raw_forecast"
RAW_HINDCAST_COLLECTION = f"{ELNINO_ROOT}/raw_hindcast"

# The forecast issue currently published. Bumping this (after re-running the
# download script for a new init month) is the only change needed here.
INIT_TAG   = "202609"
INIT_LABEL = "September 2026"
HINDCAST   = "1993-2016"
SYSTEM     = "51"

# ── Band order contract ─────────────────────────────────────────────────────
# geeup's ingestion manifest carries no `bands` key, so Earth Engine names every
# band b1..b8 in file order. Band ORDER is the contract; the names live in each
# image's `band_names` property and are re-applied with .rename().
#
# This list must stay byte-identical to the terciles() return order in
# scripts/el_nino/download_ecmwf_prec.py. elnino_core verifies it against the
# live asset on first use rather than trusting this copy.
TERCILE_BANDS = [
    "p_below", "p_normal", "p_above", "cat",
    "fc_mean_mm", "clim_mean_mm", "anom_mm", "dry_mask",
]

# ── Periods ─────────────────────────────────────────────────────────────────
# `lead` is numeric across the whole collection (0 = the aggregate window):
# Earth Engine requires one type per property, and a string "window" alongside
# integer months makes ingestion of the odd one out fail.
PERIODS = [
    {"period": "window_L1_L4", "lead": 0, "label": "Sep–Dec 2026 (whole season)",
     "short": "Sep–Dec", "dry_mask_mm": 50, "default": True,
     "note": "The four-month total. The team's reference period."},
    {"period": "L1_Sep", "lead": 1, "label": "September 2026", "short": "Sep",
     "dry_mask_mm": 10, "default": False,
     "note": "The initialisation month — partly determined by conditions "
             "already observed when the forecast was issued, so it behaves "
             "differently from the later lead months."},
    {"period": "L2_Oct", "lead": 2, "label": "October 2026", "short": "Oct",
     "dry_mask_mm": 10, "default": False, "note": ""},
    {"period": "L3_Nov", "lead": 3, "label": "November 2026", "short": "Nov",
     "dry_mask_mm": 10, "default": False, "note": ""},
    {"period": "L4_Dec", "lead": 4, "label": "December 2026", "short": "Dec",
     "dry_mask_mm": 10, "default": False, "note": ""},
]

PERIOD_MAP = {p["period"]: p for p in PERIODS}
DEFAULT_PERIOD = next(p["period"] for p in PERIODS if p.get("default"))


def period_label(period):
    return PERIOD_MAP.get(period, {}).get("label", period)


# ── Signals ─────────────────────────────────────────────────────────────────
# What "exposed" means. Each maps to a probability band and a direction.
#
# These are risk PROXIES, not predictions of drought or flooding: a wet signal
# means this model puts the season in the wettest third of its own climatology,
# which is a reason to look closer, not a forecast of a flood.
SIGNALS = [
    {"name": "dry", "label": "Drier than normal", "band": "p_below",
     "other": "p_above", "cat": 1, "color": "#d2913c",
     "palette": ["#f7f0e4", "#e8d3a9", "#d2913c", "#a8641f", "#6b3a0a"],
     "blurb": "Drought risk proxy — the season falls in the driest third."},
    {"name": "wet", "label": "Wetter than normal", "band": "p_above",
     "other": "p_below", "cat": 3, "color": "#1d7bff",
     "palette": ["#cfe8ff", "#8fcbff", "#1d7bff", "#0654be", "#00357d"],
     "blurb": "Flood risk proxy — the season falls in the wettest third."},
]

SIGNAL_MAP = {s["name"]: s for s in SIGNALS}
DEFAULT_SIGNAL = "dry"

# ── Threshold contract ──────────────────────────────────────────────────────
# The probability slider. 1/3 is the no-information baseline: with terciles, a
# forecast carrying no signal puts ~33% of members in each third.
#
# 51 ensemble members means probabilities move in steps of 1/51 ≈ 0.0196, so
# cells near any cutoff flip on essentially arbitrary grounds. That is why the
# UI offers a sensitivity readout at several thresholds rather than one number.
PROB_MIN     = 0.34
PROB_MAX     = 0.80
PROB_DEFAULT = 0.50
PROB_STEP    = 0.01
PROB_MARKS   = [0.34, 0.40, 0.50, 0.60, 0.70, 0.80]

# Thresholds reported side by side with the headline figure.
SENSITIVITY_THRESHOLDS = [0.40, 0.50, 0.60]

CHANCE_LEVEL = 1.0 / 3.0

# ── Explore layers ──────────────────────────────────────────────────────────
# Every band is visualisable, because the probabilities alone say how LIKELY a
# signal is and nothing about how LARGE it is. `anom_mm` is what separates
# "confidently slightly wetter" from "confidently much wetter".
#
#   band      band to display (or None when `derived` builds it)
#   derived   key into elnino_core._DERIVED
#   diverging palette runs low->high through a neutral midpoint
EXPLORE_LAYERS = [
    {"name": "signal_prob", "label": "Signal probability",
     "band": None, "derived": "signal_prob",
     "min": 0.0, "max": 1.0, "units": "probability",
     "palette": ["#f7f7f7", "#d9e8f5", "#92c5de", "#2166ac", "#0b2c56"],
     "desc": "Probability that the selected signal occurs — the fraction of "
             "the 51 ensemble members falling in that tercile. Follows the "
             "signal selector above.",
     "default": True},
    {"name": "cat", "label": "Category at the current threshold",
     "band": None, "derived": "category",
     "min": 0, "max": 3, "units": "",
     "palette": ["#e9e9e9", "#d2913c", "#e9e9e9", "#1d7bff"],
     "desc": "Dry / no signal / wet, recomputed live at the threshold on the "
             "slider. Not the stored `cat` band, which is fixed at 0.5.",
     "default": False},
    {"name": "anom_mm", "label": "Rainfall anomaly",
     "band": "anom_mm", "derived": None,
     "min": -200, "max": 200, "units": "mm", "diverging": True,
     "palette": ["#8c510a", "#dfc27d", "#f5f5f5", "#80cdc1", "#01665e"],
     "desc": "Forecast mean minus the model's climatological mean, in mm. "
             "This is the magnitude the probabilities do not carry: a cell can "
             "be 90% likely to be wetter while only 3 mm above normal.",
     "default": False},
    {"name": "anom_pct", "label": "Rainfall anomaly (% of normal)",
     "band": None, "derived": "anom_pct",
     "min": -60, "max": 60, "units": "%", "diverging": True,
     "palette": ["#8c510a", "#dfc27d", "#f5f5f5", "#80cdc1", "#01665e"],
     "desc": "The anomaly as a share of the climatological mean. +50 mm is "
             "trivial in Kerala and enormous in the Sahel — this is what makes "
             "regions comparable.",
     "default": False},
    {"name": "signal_strength", "label": "Signal strength (wet − dry)",
     "band": None, "derived": "signal_strength",
     "min": -1, "max": 1, "units": "", "diverging": True,
     "palette": ["#d2913c", "#e8d3a9", "#f5f5f5", "#8fcbff", "#1d7bff"],
     "desc": "p_above − p_below as one signed layer: brown where a dry signal "
             "dominates, blue where a wet one does, pale where the ensemble is "
             "split.",
     "default": False},
    {"name": "fc_mean_mm", "label": "Forecast rainfall",
     "band": "fc_mean_mm", "derived": None,
     "min": 0, "max": 1500, "units": "mm",
     "palette": ["#f7f7d7", "#c7e9b4", "#41b6c4", "#2c7fb8", "#253494"],
     "desc": "Ensemble-mean total rainfall forecast for the period.",
     "default": False},
    {"name": "clim_mean_mm", "label": "Climatological rainfall",
     "band": "clim_mean_mm", "derived": None,
     "min": 0, "max": 1500, "units": "mm",
     "palette": ["#f7f7d7", "#c7e9b4", "#41b6c4", "#2c7fb8", "#253494"],
     "desc": f"What the model normally predicts for this period, pooled over "
             f"the {HINDCAST} hindcast. The baseline the terciles are cut from.",
     "default": False},
    {"name": "dry_mask", "label": "Too dry to interpret",
     "band": "dry_mask", "derived": None,
     "min": 0, "max": 1, "units": "",
     "palette": ["#c94040", "#f0f0f0"],
     "desc": "Red where the climatology is below the cutoff and a tercile is "
             "rounding noise rather than signal — a 'wet third' on 0.3 mm of "
             "normal rainfall means nothing. Excluded from exposure by default.",
     "default": False},
]

EXPLORE_MAP = {l["name"]: l for l in EXPLORE_LAYERS}
DEFAULT_EXPLORE = next(l["name"] for l in EXPLORE_LAYERS if l.get("default"))

# ── Methodology, shown in the tab ───────────────────────────────────────────
# Written for a user who has not read the script. Each step states what was
# decided AND why, because every one of them is a defensible-but-arguable call.
METHOD_STEPS = [
    ("The forecast",
     f"ECMWF SEAS5 (system {SYSTEM}), initialised {INIT_LABEL}, 51 ensemble "
     f"members, lead months 1–4. Each member is one plausible version of the "
     f"season; their spread is the uncertainty."),
    ("The baseline",
     f"The same model re-run over {HINDCAST} — 24 years × 25 members = 600 "
     f"values per grid cell. Terciles are cut from THIS, the model's own "
     f"climatology, not from observations, so the model's biases cancel out."),
    ("The probabilities",
     "For each cell, the fraction of the 51 members below the 33rd percentile "
     "(drier third) and above the 67th (wetter third). With terciles, 33% is "
     "the no-information baseline."),
    ("The threshold",
     "A cell carries a signal when its probability reaches the slider value "
     "and exceeds the opposite tercile. 50% is the default; ECMWF's own charts "
     "shade from about 40%. Because 51 members quantise probability in steps "
     "of about 2 points, the figure is reported at several thresholds."),
    ("The dry mask",
     "Cells whose climatology is under the cutoff are excluded: where normal "
     "rainfall is a fraction of a millimetre, a 'wettest third' is rounding "
     "noise at any threshold. This is not a tuning choice — leave it off and "
     "desert children are counted as flood-exposed."),
    ("The exposure",
     "Child population is summed wherever the signal holds, using the same "
     "under-18 grid as every other tab, so a figure here reconciles with an "
     "Analysis-tab figure for the same region."),
]

# Caveats that must travel with any number this tab produces.
CAVEATS = [
    "A tercile signal is relative to THIS MODEL's climatology. “Wetter than "
    "normal” means wetter than SEAS5 usually predicts — not wet in absolute "
    "terms, and not a flood forecast.",
    "Seasonal skill is regional. SEAS5 has genuine predictive skill where "
    "ENSO teleconnections are strong and close to none elsewhere, and this "
    "tab carries no skill mask yet — two equal-looking signals are not "
    "equally trustworthy.",
    "The forecast grid is 1° (~111 km). A national total is a sum over coarse "
    "cells and should not be read at sub-district precision.",
    "This is one forecast issue, not a monitored product. It does not update; "
    "it is replaced when a new initialisation is published.",
]

SOURCE_URL = ("https://cds.climate.copernicus.eu/datasets/"
              "seasonal-monthly-single-levels")
