#!/usr/bin/env python
"""
download_ecmwf_prec.py — SEAS5 tercile forecast -> GeoTIFFs -> GEE.

Handles precipitation and 2 m temperature. Pick with --var (default prec, so a
bare invocation does exactly what it always did); VARS holds everything that
differs between them. Temperature is the SAME CDS dataset and request shape, so
it is a one-variable addition rather than a second pipeline.

Init Sep 2026, lead months 2-4 (Oct-Nov-Dec). Hindcast 1993-2016, same init
month and the same leads, so forecast and baseline are compared like for like.
Terciles come from the model's own hindcast climatology, so model bias cancels.

The one asymmetry worth knowing before reading VARS: precipitation ACCUMULATES
over the OND window and temperature AVERAGES. Summing three monthly means would
give an ~80 C "temperature", and every threshold downstream would be nonsense.
That is `window_agg`, and it is the easiest thing here to get wrong.

Upload path: geeup (https://github.com/samapriya/geeup), the same tool
upload_infra.py uses. The Earth Engine ingestion APIs stage through a Google
Cloud Storage bucket this project has no access to; geeup stages internally.
That choice has one consequence worth knowing before you read the code:

    geeup's ingestion manifest carries no `bands` key, so Earth Engine names
    every band b1, b2, b3... in file order. Band NAMES cannot survive the
    upload; band ORDER is the contract instead.

So each image records its band names in two places, both written by this script:
  - image property `band_names` — a comma-joined string, readable from GEE
  - bands.json next to the GeoTIFFs — the same mapping, readable offline
The exposure script rebuilds real names with .rename(), e.g.
    ee.Image(asset).rename(props['band_names'].split(','))
Keep BAND ORDER STABLE. Reordering the bands of an existing collection silently
mislabels every consumer that already renamed by position.

Outputs (all uploaded). Temperature lands in parallel collections with a _t2m
suffix, so the precipitation assets the dashboard already reads are untouched
and temperature can be absent without breaking anything:
  raw_forecast      1 image,   153 bands (3 leads x 51 members), mm/month
  raw_hindcast      24 images, 75 bands each (3 leads x 25 members), mm/month
  terciles          4 images (one per lead month + the full OND window), bands
                    p_below, p_normal, p_above, cat, fc_mean_mm, clim_mean_mm,
                    anom_mm, dry_mask
  raw_forecast_t2m  1 image,   153 bands, degC
  raw_hindcast_t2m  24 images, 75 bands each, degC
  terciles_t2m      4 images, 7 bands — as above but fc_mean_c, clim_mean_c,
                    anom_c, and NO dry_mask (there is no "too cold to
                    interpret" analogue of an arid cell)

The 7-vs-8 band asymmetry is deliberate and protective: a .rename() against the
wrong name list fails loudly on a count mismatch instead of silently mislabeling
every band, so `terciles` and `terciles_t2m` can never be confused.

Nothing in the terciles images is masked or thresholded away — every band is
written for every pixel, so downstream work is not locked to the choices made
here. In particular:
  p_below/p_normal/p_above  raw ensemble fractions, no threshold applied.
                            Re-derive categories from these at any cutoff.
  cat                       convenience only, at PROB_THR (see CONFIG):
                            1 = dry, 3 = wet, 0 = no clear signal.
  dry_mask                  1 = usable, 0 = climatology below the dry cutoff,
                            where a tercile is noise rather than signal. Apply
                            it (or roll your own from clim_mean_mm) in the
                            consumer; it is NOT applied to cat here.
The thresholds used are recorded per image as `prob_threshold` and
`dry_mask_mm`, so a consumer can reproduce or override them.

Requirements — these scripts have their OWN virtualenv, separate from the
dashboard's root .venv, because the GRIB/raster stack pulls its own GDAL and
PROJ binaries and nothing the app serves needs them:

    py -3.11 -m venv scripts/el_nino/.venv
    scripts/el_nino/.venv/Scripts/python -m pip install -r scripts/el_nino/requirements.txt

Run it with that interpreter directly — no `activate` needed:

    scripts/el_nino/.venv/Scripts/python scripts/el_nino/download_ecmwf_prec.py --help

Auth (one-off):
    ~/.cdsapirc with your CDS key, and accept the dataset licence on the CDS
        website once — the first retrieve 403s until you do.
    geeup auth --cred app/credentials/service_account.json
    earthengine authenticate
    geeup runs as your PERSONAL Earth Engine login (bare ee.Initialize()), not
        as the service account, so ~/.config/earthengine/credentials must carry
        {"project": "unicef-ccri", ...} or the upload fails with "Not signed up
        for Earth Engine", which reads like a permissions problem but is not.
        Assets land under your account; the app's service account needs read
        access to them. Same caveat as upload_infra.py.

Usage (PY = scripts/el_nino/.venv/Scripts/python):
    PY scripts/el_nino/download_ecmwf_prec.py --dry-run    # build, validate upload
    PY scripts/el_nino/download_ecmwf_prec.py --no-upload  # build and stop
    PY scripts/el_nino/download_ecmwf_prec.py              # precipitation
    PY scripts/el_nino/download_ecmwf_prec.py --only terciles --overwrite
    PY scripts/el_nino/download_ecmwf_prec.py --var t2m    # temperature
    PY scripts/el_nino/download_ecmwf_prec.py --var prec --var t2m
"""
import argparse
import calendar
import csv
import json
import os
import sys
from pathlib import Path

# A machine-wide PROJ_LIB/GDAL_DATA (PostgreSQL/PostGIS sets both on Windows)
# points PROJ at a PROJ-6 era proj.db, which rasterio 1.4 / PROJ 9 rejects:
#     proj_create_from_database: ... DATABASE.LAYOUT.VERSION.MINOR = 2
#                                    whereas a number >= 5 is expected
# The CRS then degrades to a bare WGS 84 string with no AUTHORITY["EPSG","4326"],
# which Earth Engine ingestion will not accept. Drop the inherited values so the
# wheels' own bundled data wins. Must happen before rasterio/pyproj import.
for _stale in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA"):
    if os.environ.get(_stale) and ".venv" not in os.environ[_stale]:
        os.environ.pop(_stale)

import numpy as np
import pandas as pd
import xarray as xr
import rioxarray  # noqa: F401  (registers the .rio accessor used by write_tif)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # scripts/ — for _common
from _common import load_env                  # noqa: E402

# ---------------- CONFIG ----------------
INIT_YEAR, INIT_MONTH = 2026, 9
# Lead = months after the INIT month, so lead 1 is always September here and
# October is always lead 2 — leads cannot be renumbered by dropping one.
# [2, 3, 4] = Oct-Nov-Dec (OND), the season the team asked for. September (lead
# 1) is excluded deliberately: it is the initialisation month, partly
# determined by conditions already present at launch, so it behaves unlike a
# true forecast month and dilutes the seasonal signal.
#
# A sharper OND becomes available once the October initialisation publishes
# (~5 Oct): set INIT_MONTH = 10 and LEADS = [1, 2, 3]. That needs a fresh
# download — new initial conditions AND an October-initialised hindcast — and
# lands under a separate 202610 asset tag, so it will not collide with this.
LEADS = [2, 3, 4]
HC_YEARS = list(range(1993, 2017))       # 1993-2016 (range stops before 2017)
SYSTEM = "51"                            # current ECMWF system in the CDS form
# Both of these only LABEL pixels (cat, dry_mask); neither drops data. Change
# them freely without invalidating the probability bands.
PROB_THR = 0.5                           # cat's dry/wet cutoff, nothing else
# cat_compound's cutoff. Lower than PROB_THR on purpose: a compound event needs
# BOTH variables in their extreme tercile, so for two independent terciles the
# no-information baseline is 1/9 = 11.1%, not 1/3. 0.25 sits 2.25x above that
# baseline, the same margin 0.5 has over 1/3 for a single variable. Reading a
# compound probability against 33% would make every map look empty.
PROB_THR_COMPOUND = 0.25
DRY_MASK_MM_MONTH = 10                   # dry_mask=0 below this clim (one month)
# Kept at 50 mm for the 3-month OND total rather than scaled down from the old
# 4-month window: it is a round "too arid to interpret" floor, not a figure
# derived from the window length, and holding it steady keeps this comparable
# with what has already been shown.
DRY_MASK_MM_WINDOW = 50
NODATA = -9999.0

# ---------------- VARIABLES ----------------
# Everything that differs between precipitation and temperature. The tercile
# maths itself is variable-agnostic — pooled quantiles and member fractions care
# nothing about units — so this table is the whole of the difference.
#
# window_agg is the trap: precipitation ACCUMULATES over the OND window (sum of
# three monthly totals), temperature AVERAGES (mean of three monthly means).
# Summing temperature gives ~80 C and silently invalidates every threshold.
#
# convert() takes the raw GRIB DataArray and the days-per-month weights, because
# precipitation needs them (a rate becomes a total) and temperature does not (a
# mean is already a mean). Temperature ignores the argument rather than having a
# separate signature.
VARS = {
    "prec": dict(
        cds_variable="total_precipitation",
        grib_band="tprate",             # mean precipitation rate, m/s
        units="mm_per_month",
        suffix="mm",
        convert=lambda da, days: da * 86400 * 1000 * days,
        window_agg="sum",               # precipitation accumulates
        mask_band="dry_mask",
        mask_thr_month=DRY_MASK_MM_MONTH,
        mask_thr_window=DRY_MASK_MM_WINDOW,
        low_name="dry", high_name="wet",
        # The GRIBs downloaded before this script handled more than one variable
        # carry no _prec in the name. Checked before the suffixed name so hours
        # of CDS queue are not spent re-fetching what is already on disk.
        legacy_fc="fc_{tag}.grib",
        legacy_hc="hc_{month:02d}_{y0}_{y1}.grib",
    ),
    "t2m": dict(
        cds_variable="2m_temperature",
        grib_band="t2m",                # instantaneous mean, K
        units="degC",
        suffix="c",
        convert=lambda da, days: da - 273.15,
        window_agg="mean",              # temperature averages
        # No aridity analogue: a cell is never "too cold to interpret" the way a
        # desert cell makes a precipitation tercile meaningless, so temperature
        # terciles ship 7 bands and consumers take the mask from the
        # precipitation terciles when they need one.
        mask_band=None,
        mask_thr_month=None,
        mask_thr_window=None,
        low_name="cool", high_name="hot",
        legacy_fc=None, legacy_hc=None,
    ),
}
DEFAULT_VAR = "prec"

GEE_PROJECT = "unicef-ccri"
GEE_ROOT = f"projects/{GEE_PROJECT}/assets/el_nino"

INIT_TAG = f"{INIT_YEAR}{INIT_MONTH:02d}"
OUT = HERE / "data" / f"seas5_{INIT_TAG}"

# Collection -> the images that belong in it. Also the --only choices. Bare
# names are precipitation's, for backwards compatibility with every existing
# asset path and runbook; other variables get a suffix.
COLLECTIONS = ["raw_forecast", "raw_hindcast", "terciles"]

# Built from two variables at once rather than per-variable, so it is not in
# COLLECTIONS and takes no suffix. Requires --var prec --var t2m.
COMPOUND_COLLECTION = "compound"
ALL_COLLECTIONS = COLLECTIONS + [COMPOUND_COLLECTION]


def collection_for(col, var):
    """Collection name for a base name and variable.

    Precipitation keeps the unsuffixed names the dashboard already reads.
    """
    return col if var == DEFAULT_VAR else f"{col}_{var}"


# ---------------- HELPERS ----------------
def valid_year_month(lead):
    """Calendar year/month a lead month refers to (lead 1 = the init month)."""
    m0 = INIT_MONTH - 1 + (lead - 1)
    return INIT_YEAR + m0 // 12, m0 % 12 + 1


def days_in_lead(lead):
    y, m = valid_year_month(lead)
    return calendar.monthrange(y, m)[1]


def lead_label(lead):
    y, m = valid_year_month(lead)
    return f"L{lead}_{calendar.month_abbr[m]}"


def iso(year, month):
    return f"{year}-{month:02d}-01T00:00:00Z"


# ---------------- 1. DOWNLOAD ----------------
def grib_paths(out, var):
    """Where this variable's GRIBs live, honouring the pre-_var filenames.

    Returns (forecast, hindcast). For precipitation, a legacy file that predates
    multi-variable support wins if it exists: those downloads cost hours of CDS
    queue and there is no reason to re-fetch them under a new name.
    """
    cfg = VARS[var]
    names = {
        "fc": (cfg["legacy_fc"], f"fc_{var}_{INIT_TAG}.grib"),
        "hc": (cfg["legacy_hc"],
               f"hc_{var}_{INIT_MONTH:02d}_{HC_YEARS[0]}_{HC_YEARS[-1]}.grib"),
    }
    paths = []
    for legacy, current in (names["fc"], names["hc"]):
        if legacy:
            cached = out / legacy.format(tag=INIT_TAG, month=INIT_MONTH,
                                         y0=HC_YEARS[0], y1=HC_YEARS[-1])
            if cached.exists():
                paths.append(cached)
                continue
        paths.append(out / current)
    return tuple(paths)


def download(out, var):
    """Fetch forecast and hindcast GRIBs, skipping any already on disk."""
    import cdsapi

    cfg = VARS[var]
    base = {
        "originating_centre": "ecmwf",
        "system": SYSTEM,
        "variable": [cfg["cds_variable"]],
        "product_type": ["monthly_mean"],   # all ensemble members, not the mean
        "month": [f"{INIT_MONTH:02d}"],
        "leadtime_month": [str(l) for l in LEADS],
        "data_format": "grib",
    }
    fc_path, hc_path = grib_paths(out, var)

    client = None
    for path, years in [(fc_path, [str(INIT_YEAR)]),
                        (hc_path, [str(y) for y in HC_YEARS])]:
        if path.exists():
            print(f"  {path.name} already downloaded")
            continue
        client = client or cdsapi.Client()
        print(f"  requesting {path.name} ({cfg['cds_variable']}; "
              f"CDS queues can take a while) ...")
        client.retrieve("seasonal-monthly-single-levels",
                        {**base, "year": years}, str(path))
    return fc_path, hc_path


# ---------------- 2. LOAD ----------------
def load(path, var):
    """One variable in its output units, dims (time?, number, fM, lat, lon).

    mm/month for precipitation, degC for temperature — see VARS[var]["convert"].
    """
    cfg = VARS[var]
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"time_dims": ("forecastMonth", "time"), "indexpath": ""},
    )
    da = ds[cfg["grib_band"]]
    # Keep only the configured leads. A cached GRIB may hold leads that LEADS
    # no longer lists — the file was downloaded when the window was Sep-Dec and
    # still carries lead 1 — and without this the raw images would ship a month
    # the terciles deliberately exclude.
    have = [int(l) for l in da.forecastMonth.values]
    want = [l for l in LEADS if l in have]
    missing = [l for l in LEADS if l not in have]
    if missing:
        sys.exit(f"ERROR: {path.name} has leads {have}, but LEADS wants "
                 f"{LEADS} (missing {missing}).\n"
                 f"       Delete the file and re-run to re-download it.")
    da = da.sel(forecastMonth=want)
    # 0..360 -> -180..180, so the GeoTIFF is a normal global raster
    da = da.assign_coords(longitude=((da.longitude + 180) % 360) - 180)
    da = da.sortby("longitude")
    # Into output units. Precipitation needs the length of each valid month
    # (a rate becomes a total); temperature ignores it.
    days = xr.DataArray([days_in_lead(int(l)) for l in da.forecastMonth.values],
                        dims="forecastMonth",
                        coords={"forecastMonth": da.forecastMonth})
    return cfg["convert"](da, days).rename(f"{var}_{cfg['suffix']}")


# ---------------- 3. TERCILES ----------------
def terciles(fc, hc, var, mask_thr):
    """fc: (number, lat, lon); hc: (time, number, lat, lon) for one period.

    Thresholds pool all hindcast years and members (24 x 25 = 600 values per
    cell), so they describe the model's own climate rather than the observed one.

    The maths below is variable-agnostic — only the band NAMES carry units, and
    only precipitation gets a dry_mask. Band order is the contract, so
    precipitation's eight bands stay in exactly the order they have always been
    uploaded in; temperature appends nothing and simply omits the mask.
    """
    cfg = VARS[var]
    sfx = cfg["suffix"]
    hs = hc.stack(s=("time", "number"))
    lo = hs.quantile(1 / 3, "s").drop_vars("quantile")
    hi = hs.quantile(2 / 3, "s").drop_vars("quantile")

    p_below = (fc < lo).mean("number")
    p_above = (fc > hi).mean("number")
    p_normal = 1 - p_below - p_above

    clim = hs.mean("s")
    fc_mean = fc.mean("number")

    # `cat` is a CONVENIENCE band at PROB_THR, deliberately left unmasked: every
    # pixel keeps a value so downstream work is not locked to this threshold.
    # Anything quantitative should re-derive from p_below/p_above instead, which
    # are the raw ensemble fractions and carry no threshold at all.
    cat = xr.where((p_below >= PROB_THR) & (p_below > p_above), 1,
          xr.where((p_above >= PROB_THR) & (p_above > p_below), 3, 0))

    out = {
        "p_below": p_below, "p_normal": p_normal, "p_above": p_above, "cat": cat,
        f"fc_mean_{sfx}": fc_mean, f"clim_mean_{sfx}": clim,
        f"anom_{sfx}": fc_mean - clim,
    }

    # Where a tercile is meaningless rather than merely uncertain: in a desert
    # cell the 33rd/67th percentiles may be 0.1 and 0.4 mm, so "above normal" is
    # rounding noise at ANY threshold. Shipped as a band, not applied as a mask,
    # so consumers can choose their own cutoff (clim_mean_mm is here too).
    #   1 = usable, 0 = too dry to interpret
    # Precipitation only — temperature has no analogue, so its images are one
    # band shorter and consumers take the mask from the precipitation terciles.
    if cfg["mask_band"]:
        out[cfg["mask_band"]] = xr.where(clim >= mask_thr, 1, 0)

    return xr.Dataset(out)


# ---------------- 3b. COMPOUND ----------------
def assert_aligned(a, b, what):
    """Every shared coordinate must match element-for-element, or exit.

    SEAS5 members are the SAME physical model runs across variables: member 7 of
    the temperature retrieval is member 7 of the precipitation one. The compound
    signal counts members that are jointly extreme, so if the two orderings ever
    disagreed we would pair member 3's rainfall with member 7's temperature and
    produce a map that LOOKS plausible but is noise. A silently wrong map is the
    worst failure available here, so this is a hard exit rather than a warning.

    CDS returns the two variables as separate retrievals, and nothing in the API
    contract promises an ordering — "they should match" is not verification.
    """
    for dim in ("number", "forecastMonth", "latitude", "longitude"):
        if dim not in a.dims and dim not in a.coords:
            continue
        av, bv = a[dim].values, b[dim].values
        if av.shape != bv.shape or not np.array_equal(av, bv):
            sys.exit(
                f"ERROR: {what} coordinate '{dim}' differs between variables.\n"
                f"       prec: {av[:5]}...{av[-3:]} (n={av.size})\n"
                f"       t2m : {bv[:5]}...{bv[-3:]} (n={bv.size})\n"
                f"       The compound signal requires element-wise identical\n"
                f"       member indices and grids. Do not proceed.")


def compound(fc_p, hc_p, fc_t, hc_t, dry_mm):
    """Joint hot-dry / cool-wet / hot-wet probabilities for one period.

    All four inputs are one period already: fc_* are (number, lat, lon) and hc_*
    are (time, number, lat, lon).

    THE METHOD: count ensemble MEMBERS that are jointly extreme. A member is one
    physically self-consistent realisation of the season, so it either is both
    dry and hot or it is not — the member is the only unit that preserves the
    correlation between the two variables.

    Do NOT multiply marginals. p_dry * p_hot assumes independence, and dry and
    hot are positively correlated through land-atmosphere feedback: drier soil
    means less evaporative cooling means higher temperature. The product
    therefore UNDERSTATES compound risk exactly where it matters most, over
    semi-arid land. `dependence_ratio` below ships the size of that error as a
    band so the choice is auditable rather than asserted.

    Thresholds are each variable's OWN pooled-hindcast terciles, the same ones
    terciles() computes. So a compound event is "both variables simultaneously
    in their own extreme third", which stays directly comparable with the
    single-variable layers instead of needing a joint distribution.
    """
    assert_aligned(fc_p, fc_t, "forecast")
    assert_aligned(hc_p, hc_t, "hindcast")

    # Pooled 24 x 25 = 600 values per cell, per variable.
    hsp = hc_p.stack(s=("time", "number"))
    hst = hc_t.stack(s=("time", "number"))
    lo_p = hsp.quantile(1 / 3, "s").drop_vars("quantile")
    hi_p = hsp.quantile(2 / 3, "s").drop_vars("quantile")
    lo_t = hst.quantile(1 / 3, "s").drop_vars("quantile")
    hi_t = hst.quantile(2 / 3, "s").drop_vars("quantile")

    dry = fc_p < lo_p
    wet = fc_p > hi_p
    cool = fc_t < lo_t
    hot = fc_t > hi_t

    # The joint counts. `&` is element-wise over members, so each member votes
    # once and the correlation between the variables is preserved by
    # construction.
    p_hotdry = (dry & hot).mean("number")
    p_coolwet = (wet & cool).mean("number")
    p_hotwet = (wet & hot).mean("number")
    # The fourth corner. Rarer than hot-dry because the dependence works
    # AGAINST it — drier members tend to be hotter, so dry-and-cool is the
    # combination the land-atmosphere feedback suppresses — but not empty:
    # 2088 land cells reach 25% for OND 2026, more than cool-wet's 761.
    # Shipped so the 2x2 matrix has no missing cell for a user to wonder about.
    p_cooldry = (dry & cool).mean("number")

    # Marginals, shipped so a consumer can recompute the ratio independently.
    p_dry = dry.mean("number")
    p_hot = hot.mean("number")

    # How much the joint count differs from what independence would predict.
    # >1 means the ensemble carries the physical correlation. Expect ~1.3-2.5
    # over semi-arid land and ~1.0 over ocean; if this comes back ~1.0
    # EVERYWHERE then member alignment or the logic is broken, which makes this
    # band a free correctness check on the whole pipeline.
    denom = p_dry * p_hot
    ratio = xr.where(denom > 0, p_hotdry / xr.where(denom > 0, denom, 1), np.nan)

    # Argmax, NOT chained xr.where. The four compound classes are not
    # algebraically exclusive — different members can vote different ways, so
    # two classes can both clear the threshold — and a chain would silently let
    # whichever branch is written last win. Ties resolve to 0.
    stack = xr.concat([p_hotdry, p_coolwet, p_hotwet, p_cooldry], dim="cls")
    top = stack.max("cls")
    n_at_top = (stack == top).sum("cls")
    # 1=hotdry, 2=coolwet, 3=hotwet, 4=cooldry
    winner = stack.argmax("cls") + 1
    cat_c = xr.where((top >= PROB_THR_COMPOUND) & (n_at_top == 1), winner, 0)

    # Inherited from precipitation: where a rainfall tercile is noise the
    # compound signal built on it is noise too. Temperature has no analogue.
    clim_p = hsp.mean("s")
    dry_mask = xr.where(clim_p >= dry_mm, 1, 0)

    return xr.Dataset({
        "p_hotdry": p_hotdry, "p_coolwet": p_coolwet, "p_hotwet": p_hotwet,
        "p_cooldry": p_cooldry, "cat_compound": cat_c,
        "p_dry_marginal": p_dry, "p_hot_marginal": p_hot,
        "dependence_ratio": ratio,
        "dry_mask": dry_mask,
    })


# ---------------- 4. EXPORT ----------------
def write_tif(arr3d, band_ids, lat, lon, path):
    """Write a (band, y, x) array as a float32 GeoTIFF with NODATA filled in."""
    da = xr.DataArray(arr3d.astype("float32"), dims=("band", "y", "x"),
                      coords={"band": np.arange(1, len(band_ids) + 1),
                              "y": lat, "x": lon})
    da = da.fillna(NODATA).rio.write_crs("EPSG:4326").rio.write_nodata(NODATA)
    da.rio.to_raster(path, compress="DEFLATE")
    return band_ids


def members_to_array(da):
    """(number, forecastMonth, lat, lon) -> (bands, lat, lon) + ids like L1_m00.

    The transpose fixes band order as lead-major, member-minor. That order IS
    the contract once anything is uploaded — see the module docstring.
    """
    da = da.transpose("forecastMonth", "number", "latitude", "longitude")
    ids = [f"L{int(l)}_m{int(n):02d}"
           for l in da.forecastMonth.values for n in da.number.values]
    return (da.values.reshape(-1, da.sizes["latitude"], da.sizes["longitude"]),
            ids)


def window_reduce(da, var):
    """Collapse the lead months into one period, the way the variable demands.

    Precipitation accumulates, temperature averages. Getting this backwards for
    temperature gives an ~80 C OND "mean" — obvious. Getting it backwards for
    precipitation gives a total 1/3 too small, which is NOT obvious, so the
    dispatch is explicit rather than defaulted.
    """
    agg = VARS[var]["window_agg"]
    if agg == "sum":
        return da.sum("forecastMonth")
    if agg == "mean":
        return da.mean("forecastMonth")
    raise ValueError(f"unknown window_agg {agg!r} for var {var!r}")


def load_pair(out, var):
    """Download if needed, then load (forecast, hindcast) for one variable."""
    fc_path, hc_path = download(out, var)
    print(f"Loading {var} GRIBs ...")
    fc = load(fc_path, var)
    if "time" in fc.dims:
        fc = fc.squeeze("time", drop=True)     # (number, forecastMonth, lat, lon)
    hc = load(hc_path, var)                     # (time, number, fM, lat, lon)
    return fc, hc


def build(out, var, only=None, loaded=None):
    """Download, compute, and write every GeoTIFF for one variable.

    Returns [(collection, stem, band_ids, props)], one entry per image.
    `loaded` reuses an already-open (fc, hc) pair so a combined run does not
    read the 300 MB hindcast GRIB twice.
    """
    cfg = VARS[var]
    fc, hc = loaded if loaded is not None else load_pair(out, var)
    lat, lon = fc.latitude.values, fc.longitude.values

    init_iso = iso(INIT_YEAR, INIT_MONTH)
    # init lands in GEE as the integer 202609 (geeup literal-evals CSV values);
    # init_ym keeps a readable form for anyone reading the asset in the console.
    common = {"system": SYSTEM, "init": INIT_TAG,
              "init_ym": f"{INIT_YEAR}-{INIT_MONTH:02d}",
              "units": cfg["units"], "variable": cfg["cds_variable"]}
    images = []
    # Variable suffix applies to the asset stems too, so the GeoTIFFs for two
    # variables can share one working directory without overwriting each other.
    stem_sfx = "" if var == DEFAULT_VAR else f"_{var}"

    def add(col, stem, arr, ids, props, start):
        if only and col not in only:
            return
        write_tif(arr, ids, lat, lon, out / f"{stem}.tif")
        images.append((collection_for(col, var), stem, ids,
                       {**props, "band_names": ",".join(ids),
                        "system:time_start": start}))
        print(f"  {stem}.tif  ({len(ids)} bands)")

    # raw forecast — one image, every member
    if not only or "raw_forecast" in only:
        arr, ids = members_to_array(fc)
        add("raw_forecast", f"fc{stem_sfx}_{INIT_TAG}", arr, ids,
            {**common, "kind": "forecast", "year": INIT_YEAR}, init_iso)

    # raw hindcast — one image per year, so GEE can filter by date
    if not only or "raw_hindcast" in only:
        for t in hc.time.values:
            yr = int(pd.Timestamp(t).year)
            arr, ids = members_to_array(hc.sel(time=t))
            add("raw_hindcast", f"hc{stem_sfx}_{yr}{INIT_MONTH:02d}", arr, ids,
                {**common, "kind": "hindcast", "year": yr},
                iso(yr, INIT_MONTH))

    # terciles — each lead month, plus the full OND window
    if not only or "terciles" in only:
        periods = {lead_label(l): (fc.sel(forecastMonth=l),
                                   hc.sel(forecastMonth=l),
                                   cfg["mask_thr_month"], l)
                   for l in LEADS}
        # lead 0 = the aggregate window, NOT a string. Earth Engine requires a
        # property to keep one type across a collection, and geeup literal-evals
        # CSV values, so "1" arrives as a number while "window" stays a string:
        # mixing them makes ingestion of the odd one out fail with
        #   property lead (of type STRING) ... does not match ... FLOAT64
        # `period` carries the readable name for filtering.
        periods[f"window_L{LEADS[0]}_L{LEADS[-1]}"] = (
            window_reduce(fc, var), window_reduce(hc, var),
            cfg["mask_thr_window"], 0)

        for name, (f, h, thr, lead) in periods.items():
            ds = terciles(f, h, var, thr)
            ids = list(ds.data_vars)
            arr = np.stack([ds[v].transpose("latitude", "longitude").values
                            for v in ids])
            add("terciles", f"terciles{stem_sfx}_{INIT_TAG}_{name}", arr, ids,
                {**common, "kind": "terciles", "lead": lead, "period": name,
                 "prob_threshold": PROB_THR,
                 # 0 rather than None when the variable has no mask: every image
                 # in a collection must carry the same property TYPE, and an
                 # empty CSV cell arrives as a string where the others are
                 # numbers, which fails ingestion for the odd one out.
                 "dry_mask_mm": thr if thr is not None else 0,
                 # Not "1993_2016": geeup runs ast.literal_eval on every CSV
                 # value, and Python reads 1993_2016 as the integer 19932016.
                 "hindcast": f"{HC_YEARS[0]}-{HC_YEARS[-1]}",
                 "cat_legend": f"1={cfg['low_name']},3={cfg['high_name']},"
                               f"0=no_signal",
                 "lead_legend": "2=Oct,3=Nov,4=Dec, 0=OND window"},
                init_iso)

    return images


def build_compound(out, loaded, only=None):
    """Write the compound GeoTIFFs. Needs both variables loaded.

    `loaded` is {var: (fc, hc)}. Returns the same tuple shape as build().
    """
    if only and COMPOUND_COLLECTION not in only:
        return []
    missing = [v for v in ("prec", "t2m") if v not in loaded]
    if missing:
        sys.exit(f"ERROR: the compound signal needs both variables, missing "
                 f"{missing}.\n"
                 f"       Re-run with:  --var prec --var t2m")

    fc_p, hc_p = loaded["prec"]
    fc_t, hc_t = loaded["t2m"]
    lat, lon = fc_p.latitude.values, fc_p.longitude.values
    init_iso = iso(INIT_YEAR, INIT_MONTH)
    images = []

    print("Building compound signals ...")
    periods = {lead_label(l): (l, DRY_MASK_MM_MONTH,
                               fc_p.sel(forecastMonth=l),
                               hc_p.sel(forecastMonth=l),
                               fc_t.sel(forecastMonth=l),
                               hc_t.sel(forecastMonth=l))
               for l in LEADS}
    # Each variable collapses the window its own way — precipitation sums, mean
    # temperature averages. window_reduce enforces that per variable.
    periods[f"window_L{LEADS[0]}_L{LEADS[-1]}"] = (
        0, DRY_MASK_MM_WINDOW,
        window_reduce(fc_p, "prec"), window_reduce(hc_p, "prec"),
        window_reduce(fc_t, "t2m"), window_reduce(hc_t, "t2m"))

    for name, (lead, thr, f_p, h_p, f_t, h_t) in periods.items():
        ds = compound(f_p, h_p, f_t, h_t, thr)
        ids = list(ds.data_vars)
        arr = np.stack([ds[v].transpose("latitude", "longitude").values
                        for v in ids])
        stem = f"compound_{INIT_TAG}_{name}"
        write_tif(arr, ids, lat, lon, out / f"{stem}.tif")

        # The independence check, logged per period.
        #
        # Conditioned three ways, because a naive global mean of this ratio is
        # actively misleading — it reads ~0.91 for OND 2026 while EVERY
        # populated latitude band is >= 1.0 (Simpson's paradox). The three
        # conditions, in order of how badly each one bites:
        #
        # 1. LATITUDE |lat| <= 60. Poleward of that there is no land-atmosphere
        #    moisture feedback and the relationship inverts: warm advection
        #    brings heat AND moisture, so dry-hot co-occurrence falls below
        #    independence. Pooling those cells with tropical land is what drags
        #    the global mean under 1. This is the condition that matters most.
        # 2. INFORMATIVE hot tercile. Under a warming baseline SEAS5 puts much
        #    of the tropics in its warmest third outright (p_hot >= 0.99 over
        #    ~21% of land here), and where p_hot is pinned at 1 "dry AND hot" is
        #    just "dry", so the ratio is mathematically capped at 1.
        # 3. p_dry >= 0.10, to keep small-denominator noise out of the mean.
        #
        # Verified against the underlying physics: across-member corr(prec, t2m)
        # for OND 2026 is -0.86 Amazon, -0.84 Horn, -0.74 southern Africa, -0.71
        # India. Negative = drier members are hotter = real feedback, so the
        # conditioned ratio SHOULD exceed 1, and does (10-30S reads 1.14).
        lat2d = (ds["p_hotdry"].latitude
                 * xr.ones_like(ds["p_hotdry"].longitude)).transpose(
                     "latitude", "longitude")
        mask = ((ds["dry_mask"] == 1)
                & (abs(lat2d) <= 60)
                & (ds["p_hot_marginal"] <= 0.80)
                & (ds["p_hot_marginal"] >= 0.20)
                & (ds["p_dry_marginal"] >= 0.10))
        r = ds["dependence_ratio"].where(mask)
        r_mean = float(r.mean(skipna=True))
        sat = float(((ds["dry_mask"] == 1)
                     & (ds["p_hot_marginal"] >= 0.99)).mean()) * 100
        images.append((COMPOUND_COLLECTION, stem, ids,
                       {"system": SYSTEM, "init": INIT_TAG,
                        "init_ym": f"{INIT_YEAR}-{INIT_MONTH:02d}",
                        "kind": "compound", "lead": lead, "period": name,
                        "units": "fraction",
                        "variable": "total_precipitation+2m_temperature",
                        "prob_threshold": PROB_THR_COMPOUND,
                        "dry_mask_mm": thr,
                        "hindcast": f"{HC_YEARS[0]}-{HC_YEARS[-1]}",
                        "method": "member-wise joint ensemble count",
                        "joint_chance_level": round(100.0 / 9.0, 1),
                        # Over informative-hot cells only — see the comment at
                        # the computation. Not comparable with a global mean.
                        "dependence_ratio_mean": round(r_mean, 4),
                        "hot_saturated_pct": round(sat, 1),
                        "cat_legend": "1=hot_dry,2=cool_wet,3=hot_wet,"
                                      "4=cool_dry,0=no_signal",
                        "lead_legend": "2=Oct,3=Nov,4=Dec, 0=OND window",
                        "band_names": ",".join(ids),
                        "system:time_start": init_iso}))
        # Flags BELOW 1.0, not below some margin above it: 1.0 is the
        # independence boundary and the only principled line. Observed
        # 1.018-1.081 across the four OND 2026 periods once conditioned, so a
        # sub-1 reading here means the feedback has vanished from the ensemble —
        # which in practice means member alignment or the joint logic, since
        # assert_aligned() cannot catch a logic error.
        flag = "" if r_mean > 1.0 else "   <-- CHECK alignment/logic"
        print(f"  {stem}.tif  ({len(ids)} bands)  "
              f"dependence_ratio {r_mean:.3f} (|lat|<=60, informative hot), "
              f"hot saturated {sat:.0f}% of land{flag}")

    return images


# ---------------- 5. UPLOAD ----------------
def write_bands_json(out, images):
    """Band names per asset stem — the offline copy of the order contract."""
    path = out / "bands.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({stem: ids for _, stem, ids, _ in images}, fh, indent=2)
    return path


def write_metadata_csv(out, images):
    """One metadata CSV per collection, in the shape geeup expects.

    First column is the filename stem; the rest become image properties. geeup
    SKIPS any GeoTIFF with no row here, so every image must be present.
    """
    paths = {}
    for col in sorted({c for c, _, _, _ in images}):
        rows = [{"id_no": stem, **props}
                for c, stem, _, props in images if c == col]
        cols = ["id_no"] + sorted({k for r in rows for k in r} - {"id_no"})
        path = out / f"_meta_{col}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        paths[col] = path
    return paths


def stage(out, images, col):
    """geeup uploads a directory at a time, so give each collection its own."""
    import shutil

    staging = out / "_upload" / col
    if staging.is_dir():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for c, stem, _, _ in images:
        if c == col:
            shutil.copy2(out / f"{stem}.tif", staging / f"{stem}.tif")
    return staging


def ensure_collections(cols):
    """Create the folder and image collections geeup uploads into.

    geeup creates the destination if it is missing, but as a folder-shaped
    guess; making them explicitly keeps them IMAGE_COLLECTIONs, which is what
    filterDate/mosaic in the exposure script need.
    """
    import ee

    key = HERE.parent.parent / "app" / "credentials" / "service_account.json"
    try:
        if key.exists():
            with open(key) as fh:
                info = json.load(fh)
            ee.Initialize(
                credentials=ee.ServiceAccountCredentials(
                    email=info["client_email"], key_file=str(key)),
                project=GEE_PROJECT)
        else:
            ee.Initialize(project=GEE_PROJECT)
    except Exception as e:
        print(f"  (could not initialise Earth Engine: {e})")
        print("   geeup will create the destinations itself; carry on.")
        return

    for path, kind in [(GEE_ROOT, "FOLDER")] + [
            (f"{GEE_ROOT}/{c}", "IMAGE_COLLECTION") for c in cols]:
        try:
            ee.data.getAsset(path)
        except ee.EEException:
            try:
                ee.data.createAsset({"type": kind}, path)
                print(f"  created {kind.lower()} {path}")
            except ee.EEException as e:
                print(f"  (could not create {path}: {e})")


COOKIE_JAR = HERE.parent.parent / "cookie_jar.json"   # repo root, absolute


def cookie_path():
    """Absolute path to cookie_jar.json, or exit with how to create one.

    geeup hardcodes the RELATIVE name "cookie_jar.json"
    (SessionManager.COOKIE_FILE), so which file it picks up depends on the
    working directory. Everything handed to geeup here is absolute instead,
    including this: run_geeup() pins the class attribute to this path before
    uploading, so the jar is found no matter where the script was started.

    Without it geeup falls back to an interactive "Enter your Cookie List:"
    prompt, which dies with EOFError under a non-interactive runner.

    Refresh it from the repo root with:  geeup cookie_setup
    """
    for p in (COOKIE_JAR, Path.cwd() / "cookie_jar.json",
              HERE / "cookie_jar.json"):
        if p.exists():
            return p.resolve()
    sys.exit(f"ERROR: no cookie_jar.json (looked in {COOKIE_JAR}, "
             f"{Path.cwd()}, {HERE}).\n"
             f"       geeup authenticates the upload with browser cookies.\n"
             f"       Create one:  cd {COOKIE_JAR.parent} && geeup cookie_setup")


def check_cookies(jar):
    """Fail fast and actionably on a stale cookie jar.

    Without this, an expired jar makes geeup fall back to an interactive
    "Enter your Cookie List:" prompt, which hits EOF under a non-interactive
    runner and dies with `EOFError: EOF when reading a line` — once per
    collection, so a 3-collection run prints the same opaque error three times
    and ingests nothing. The jar is the single most common thing to go stale
    here (observed lifetime: about a month), so it is worth one check up front.

    Only presence is verified; whether the session is still valid server-side
    cannot be known without trying. A jar missing SACSID, though, has certainly
    not come from a signed-in Earth Engine session.
    """
    try:
        with open(jar, encoding="utf-8") as fh:
            cookies = json.load(fh)
    except (OSError, ValueError) as e:
        sys.exit(f"ERROR: cannot read {jar}: {e}\n"
                 f"       Refresh it:  cd {COOKIE_JAR.parent} && geeup cookie_setup")

    if isinstance(cookies, dict):
        cookies = [cookies]
    names = {c.get("name") for c in cookies if isinstance(c, dict)}
    # SACSID is the Earth Engine session cookie; the SID family is the Google
    # login. Missing either means the jar cannot authenticate an upload.
    missing = [n for n in ("SACSID", "SID", "__Secure-1PSID") if n not in names]
    if missing:
        sys.exit(f"ERROR: {jar} is missing {', '.join(missing)}.\n"
                 f"       It has {len(names)} cookie(s) but not the ones an\n"
                 f"       upload needs, so geeup would prompt and then die with\n"
                 f"       EOFError under a non-interactive runner.\n"
                 f"       Refresh it:  cd {COOKIE_JAR.parent} && geeup cookie_setup")


def run_geeup(staging, meta, dest, user, overwrite, dry_run, workers=1):
    """Upload one collection. Returns a process-style status (0 = success).

    Driven in-process rather than by shelling out to the geeup CLI, so the
    cookie jar can be pinned to an absolute path first. Every path geeup
    receives here is absolute; nothing depends on the working directory.
    """
    jar = cookie_path()
    source = Path(staging).resolve()
    metadata = Path(meta).resolve()

    print(f"\n  geeup upload  {source}"
          f"\n    -> {dest}"
          f"\n    metadata {metadata}"
          f"\n    cookies  {jar}\n")

    try:
        from geeup.batch_uploader import BatchUploader, SessionManager
    except ImportError as e:
        sys.exit(f"ERROR: geeup is not importable ({e}).\n"
                 f"       python -m pip install -r {HERE / 'requirements.txt'}")

    # The one place geeup would otherwise use a relative path.
    SessionManager.COOKIE_FILE = jar

    try:
        # BatchUploader takes no `user`: it uploads as whoever the cookie jar
        # belongs to. --user is still accepted by the CLI for the table path.
        uploader = BatchUploader(
            source_path=source,
            destination_path=dest,
            metadata_path=metadata,
            pyramiding="MEAN",
            nodata_value=int(NODATA),
            overwrite=bool(overwrite),
            dry_run=bool(dry_run),
            workers=max(1, int(workers or 1)),
        )
        return 0 if uploader.upload() else 1
    except Exception as e:
        print(f"  geeup upload failed: {type(e).__name__}: {e}")
        return 1


# ---------------- MAIN ----------------
def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=ALL_COLLECTIONS,
                    default=None,
                    help="build/upload only these collections (repeatable). "
                         "`compound` needs --var prec --var t2m")
    ap.add_argument("--var", action="append", choices=sorted(VARS),
                    default=None,
                    help=f"variable(s) to build, repeatable "
                         f"(default {DEFAULT_VAR}). Temperature lands in "
                         f"parallel _t2m collections, leaving the "
                         f"precipitation assets untouched")
    ap.add_argument("--out", default=str(OUT),
                    help=f"working directory for GRIBs and GeoTIFFs "
                         f"(default {OUT})")
    ap.add_argument("--user", default=os.environ.get("GEEUP_USER"),
                    help="Google account email, recorded for reference only — "
                         "the raster upload authenticates as whoever "
                         "cookie_jar.json belongs to")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace assets that already exist")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel uploads (default 1; raise only after a "
                         "serial run succeeds)")
    ap.add_argument("--no-upload", action="store_true",
                    help="write the GeoTIFFs and stop")
    ap.add_argument("--dry-run", action="store_true",
                    help="build, then validate the upload without ingesting")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only) if args.only else None
    # dict.fromkeys and not set(): --var prec --var t2m should build in the
    # order given, and a repeated --var should not build twice.
    variables = list(dict.fromkeys(args.var)) if args.var else [DEFAULT_VAR]

    # `--only compound` on its own still needs both variables read.
    want_compound = not only or COMPOUND_COLLECTION in only
    if want_compound and only == {COMPOUND_COLLECTION} and len(variables) == 1:
        variables = ["prec", "t2m"]

    print(f"Building SEAS5 {INIT_TAG} ({', '.join(variables)}) into {out}/")
    images = []
    loaded = {}
    for var in variables:
        # Held so the compound build reuses them — the hindcast GRIB is 300 MB
        # and reading it once per variable is enough.
        loaded[var] = load_pair(out, var)
        images += build(out, var, only, loaded=loaded[var])

    if want_compound and len(loaded) > 1:
        images += build_compound(out, loaded, only)
    elif only and COMPOUND_COLLECTION in only:
        images += build_compound(out, loaded, only)   # exits with the reason

    if not images:
        sys.exit("ERROR: nothing built — check --only.")

    bands_path = write_bands_json(out, images)
    metas = write_metadata_csv(out, images)
    print(f"\n{len(images)} GeoTIFF(s) written; band order recorded in "
          f"{bands_path.name}")

    if args.no_upload:
        print("--no-upload: stopping before the upload.")
        return

    # Upload in COLLECTIONS order per variable, so a partial failure is easy to
    # resume: the base names are precipitation's and the suffixed ones follow,
    # with the cross-variable compound collection last.
    cols = [collection_for(c, v) for v in variables for c in COLLECTIONS
            if collection_for(c, v) in metas]
    if COMPOUND_COLLECTION in metas:
        cols.append(COMPOUND_COLLECTION)
    # Before creating anything or staging gigabytes, confirm the upload can
    # actually authenticate — a stale jar otherwise fails once per collection
    # with an opaque EOFError after all the work is done.
    check_cookies(cookie_path())
    ensure_collections(cols)

    failed = []
    for col in cols:
        n = sum(1 for c, _, _, _ in images if c == col)
        print(f"\n=== {col}: {n} image(s) -> {GEE_ROOT}/{col} ===")
        staging = stage(out, images, col)
        rc = run_geeup(staging, metas[col], f"{GEE_ROOT}/{col}",
                       args.user, args.overwrite, args.dry_run, args.workers)
        if rc != 0:
            failed.append((col, rc))

    if failed:
        sys.exit("\nERROR: geeup failed for: "
                 + ", ".join(f"{c} (status {rc})" for c, rc in failed))

    if not args.dry_run:
        print("\nIngestion tasks started. Track them with:  geeup tasks")
        print("Bands arrive as b1, b2, ... — rename by the `band_names` "
              "property, e.g.\n"
              "    img = ee.Image(asset)\n"
              "    img = img.rename(img.get('band_names').getInfo().split(','))")
        print("Remember: assets are created under your account, so the app's "
              "service account needs read access.")


if __name__ == "__main__":
    main()
