#!/usr/bin/env python
"""
download_ecmwf_prec.py — SEAS5 precipitation tercile forecast -> GeoTIFFs -> GEE.

Init Sep 2026, lead months 2-4 (Oct-Nov-Dec). Hindcast 1993-2016, same init
month and the same leads, so forecast and baseline are compared like for like.
Terciles come from the model's own hindcast climatology, so model bias cancels.

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

Outputs (all uploaded):
  raw_forecast  1 image,   153 bands (3 leads x 51 members), mm/month
  raw_hindcast  24 images, 75 bands each (3 leads x 25 members), mm/month
  terciles      4 images (one per lead month + the full OND window), bands
                p_below, p_normal, p_above, cat, fc_mean_mm, clim_mean_mm,
                anom_mm, dry_mask

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
    PY scripts/el_nino/download_ecmwf_prec.py
    PY scripts/el_nino/download_ecmwf_prec.py --only terciles --overwrite
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
DRY_MASK_MM_MONTH = 10                   # dry_mask=0 below this clim (one month)
# Kept at 50 mm for the 3-month OND total rather than scaled down from the old
# 4-month window: it is a round "too arid to interpret" floor, not a figure
# derived from the window length, and holding it steady keeps this comparable
# with what has already been shown.
DRY_MASK_MM_WINDOW = 50
NODATA = -9999.0

GEE_PROJECT = "unicef-ccri"
GEE_ROOT = f"projects/{GEE_PROJECT}/assets/el_nino"

INIT_TAG = f"{INIT_YEAR}{INIT_MONTH:02d}"
OUT = HERE / "data" / f"seas5_{INIT_TAG}"

# Collection -> the images that belong in it. Also the --only choices.
COLLECTIONS = ["raw_forecast", "raw_hindcast", "terciles"]


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
def download(out):
    """Fetch forecast and hindcast GRIBs, skipping any already on disk."""
    import cdsapi

    base = {
        "originating_centre": "ecmwf",
        "system": SYSTEM,
        "variable": ["total_precipitation"],
        "product_type": ["monthly_mean"],   # all ensemble members, not the mean
        "month": [f"{INIT_MONTH:02d}"],
        "leadtime_month": [str(l) for l in LEADS],
        "data_format": "grib",
    }
    fc_path = out / f"fc_{INIT_TAG}.grib"
    hc_path = out / f"hc_{INIT_MONTH:02d}_{HC_YEARS[0]}_{HC_YEARS[-1]}.grib"

    client = None
    for path, years in [(fc_path, [str(INIT_YEAR)]),
                        (hc_path, [str(y) for y in HC_YEARS])]:
        if path.exists():
            print(f"  {path.name} already downloaded")
            continue
        client = client or cdsapi.Client()
        print(f"  requesting {path.name} (CDS queues can take a while) ...")
        client.retrieve("seasonal-monthly-single-levels",
                        {**base, "year": years}, str(path))
    return fc_path, hc_path


# ---------------- 2. LOAD ----------------
def load(path):
    """Precipitation in mm/month, dims (time?, number, forecastMonth, lat, lon)."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"time_dims": ("forecastMonth", "time"), "indexpath": ""},
    )
    da = ds["tprate"]  # mean precipitation rate, m/s
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
    # m/s -> mm/month, using the length of each valid month
    days = xr.DataArray([days_in_lead(int(l)) for l in da.forecastMonth.values],
                        dims="forecastMonth",
                        coords={"forecastMonth": da.forecastMonth})
    return (da * 86400 * 1000 * days).rename("precip_mm")


# ---------------- 3. TERCILES ----------------
def terciles(fc, hc, dry_mm):
    """fc: (number, lat, lon); hc: (time, number, lat, lon) for one period.

    Thresholds pool all hindcast years and members (24 x 25 = 600 values per
    cell), so they describe the model's own climate rather than the observed one.
    """
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

    # Where a tercile is meaningless rather than merely uncertain: in a desert
    # cell the 33rd/67th percentiles may be 0.1 and 0.4 mm, so "above normal" is
    # rounding noise at ANY threshold. Shipped as a band, not applied as a mask,
    # so consumers can choose their own cutoff (clim_mean_mm is here too).
    #   1 = usable, 0 = too dry to interpret
    dry_mask = xr.where(clim >= dry_mm, 1, 0)

    return xr.Dataset({
        "p_below": p_below, "p_normal": p_normal, "p_above": p_above, "cat": cat,
        "fc_mean_mm": fc_mean, "clim_mean_mm": clim, "anom_mm": fc_mean - clim,
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


def build(out, only=None):
    """Download, compute, and write every GeoTIFF.

    Returns [(collection, stem, band_ids, props)], one entry per image.
    """
    fc_path, hc_path = download(out)

    print("Loading GRIBs ...")
    fc = load(fc_path)
    if "time" in fc.dims:
        fc = fc.squeeze("time", drop=True)     # (number, forecastMonth, lat, lon)
    hc = load(hc_path)                          # (time, number, fM, lat, lon)
    lat, lon = fc.latitude.values, fc.longitude.values

    init_iso = iso(INIT_YEAR, INIT_MONTH)
    # init lands in GEE as the integer 202609 (geeup literal-evals CSV values);
    # init_ym keeps a readable form for anyone reading the asset in the console.
    common = {"system": SYSTEM, "init": INIT_TAG,
              "init_ym": f"{INIT_YEAR}-{INIT_MONTH:02d}",
              "units": "mm_per_month", "variable": "total_precipitation"}
    images = []

    def add(col, stem, arr, ids, props, start):
        if only and col not in only:
            return
        write_tif(arr, ids, lat, lon, out / f"{stem}.tif")
        images.append((col, stem, ids,
                       {**props, "band_names": ",".join(ids),
                        "system:time_start": start}))
        print(f"  {stem}.tif  ({len(ids)} bands)")

    # raw forecast — one image, every member
    if not only or "raw_forecast" in only:
        arr, ids = members_to_array(fc)
        add("raw_forecast", f"fc_{INIT_TAG}", arr, ids,
            {**common, "kind": "forecast", "year": INIT_YEAR}, init_iso)

    # raw hindcast — one image per year, so GEE can filter by date
    if not only or "raw_hindcast" in only:
        for t in hc.time.values:
            yr = int(pd.Timestamp(t).year)
            arr, ids = members_to_array(hc.sel(time=t))
            add("raw_hindcast", f"hc_{yr}{INIT_MONTH:02d}", arr, ids,
                {**common, "kind": "hindcast", "year": yr},
                iso(yr, INIT_MONTH))

    # terciles — each lead month, plus the full OND window
    if not only or "terciles" in only:
        periods = {lead_label(l): (fc.sel(forecastMonth=l),
                                   hc.sel(forecastMonth=l),
                                   DRY_MASK_MM_MONTH, l)
                   for l in LEADS}
        # lead 0 = the aggregate window, NOT a string. Earth Engine requires a
        # property to keep one type across a collection, and geeup literal-evals
        # CSV values, so "1" arrives as a number while "window" stays a string:
        # mixing them makes ingestion of the odd one out fail with
        #   property lead (of type STRING) ... does not match ... FLOAT64
        # `period` carries the readable name for filtering.
        periods[f"window_L{LEADS[0]}_L{LEADS[-1]}"] = (
            fc.sum("forecastMonth"), hc.sum("forecastMonth"),
            DRY_MASK_MM_WINDOW, 0)

        for name, (f, h, thr, lead) in periods.items():
            ds = terciles(f, h, thr)
            ids = list(ds.data_vars)
            arr = np.stack([ds[v].transpose("latitude", "longitude").values
                            for v in ids])
            add("terciles", f"terciles_{INIT_TAG}_{name}", arr, ids,
                {**common, "kind": "terciles", "lead": lead, "period": name,
                 "prob_threshold": PROB_THR, "dry_mask_mm": thr,
                 # Not "1993_2016": geeup runs ast.literal_eval on every CSV
                 # value, and Python reads 1993_2016 as the integer 19932016.
                 "hindcast": f"{HC_YEARS[0]}-{HC_YEARS[-1]}",
                 "cat_legend": "1=dry,3=wet,0=no_signal",
                 "lead_legend": "2=Oct,3=Nov,4=Dec, 0=OND window"},
                init_iso)

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
    ap.add_argument("--only", action="append", choices=COLLECTIONS, default=None,
                    help="build/upload only these collections (repeatable)")
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

    print(f"Building SEAS5 {INIT_TAG} into {out}/")
    images = build(out, only)
    if not images:
        sys.exit("ERROR: nothing built — check --only.")

    bands_path = write_bands_json(out, images)
    metas = write_metadata_csv(out, images)
    print(f"\n{len(images)} GeoTIFF(s) written; band order recorded in "
          f"{bands_path.name}")

    if args.no_upload:
        print("--no-upload: stopping before the upload.")
        return

    cols = [c for c in COLLECTIONS if c in metas]
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
