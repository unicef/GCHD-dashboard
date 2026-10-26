#!/usr/bin/env python
"""
build_skill_mask.py — where does SEAS5 actually have forecast skill?

The El Nino tab renders a signal over Europe exactly like one over Indonesia,
but SEAS5 has genuine seasonal skill only where teleconnections are strong.
This computes, per grid cell, the correlation between the model's hindcast
season totals and what CHIRPS observed, over 1993-2016.

The result is a TRUSTWORTHINESS layer, not a correction: it changes no exposure
figure. It answers "should I believe this signal here?", which the probability
bands cannot.

Method, per period:
  1. For each hindcast year, the ensemble-mean season total. Bands are
     L{lead}_m{member} MONTHLY totals, so the season total is the sum over
     leads of the per-lead member means — NOT the mean of every band, which
     would give a monthly figure.
  2. CHIRPS observed total for the same calendar months, regridded to the
     forecast's 1 degree grid.
  3. Pearson correlation across the 24 year-pairs.

Everything runs server-side in Earth Engine — no GRIB, no rasterio, no scipy
(absent from both venvs) — so this runs in the ROOT .venv like
export_exposure_table.py, not the el_nino one.

Outputs one 2-band image per period into
projects/unicef-ccri/assets/el_nino/skill:
    skill_r  Pearson correlation, -1..1
    skill_p  its two-sided p-value

Two phases, and the order matters. Correlating 24 year-pairs where each pair
regrids 5 km CHIRPS on the fly runs the 3-month window out of memory — every
year's regrid is alive at once inside the single reduce(). So the coarse
observations are exported first, and the correlation then reads 1 degree data.
The monthly periods are light enough for --inline-obs, but staging is the
supported path for all of them.

Requirements:  pip install earthengine-api
Usage:
    python scripts/el_nino/build_skill_mask.py --init 202610 --dry-run
    python scripts/el_nino/build_skill_mask.py --init 202610 --stage-obs --wait   # phase 1
    python scripts/el_nino/build_skill_mask.py --init 202610 --wait               # phase 2
    python scripts/el_nino/build_skill_mask.py --init 202610 --period window_L2_L4 --overwrite
"""
import argparse
import json
import sys
import time
from pathlib import Path

import ee

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(APP))          # reuse the dashboard's own config

from elnino_config import (            # noqa: E402
    ELNINO_ROOT, TERCILES_COLLECTION, RAW_HINDCAST_COLLECTION,
    HINDCAST, HC_YEARS, SYSTEM,
    parse_issue, window_suffix, obs_months, staged_obs_name,
)

# Set from --init in main().
INIT_TAG = INIT_MONTH = None
PERIOD_MAP = {}                    # suffix -> period, for that issue
WINDOW_PERIOD = window_suffix()

SKILL_BANDS = ["skill_r", "skill_p"]

# ---------------- OBSERVED REFERENCES ----------------
# One entry per variable, mirroring VARS in download_ecmwf_prec.py. Two
# aggregation axes, and they are INDEPENDENT:
#
#   month_agg  how sub-monthly source data becomes a monthly value.
#              CHIRPS ships pentads, so a month is their SUM. ERA5-Land
#              MONTHLY_AGGR is already monthly, so a month is its MEAN (of the
#              one matching image).
#   season_agg how months combine into the period. Precipitation SUMS
#              (accumulation); temperature MEANS. Same trap as window_agg in
#              the download script — a summed 3-month temperature is ~80 C, and
#              a lead-averaged precipitation total is 1/3 too small, which is
#              the quieter and more dangerous error.
#
# The forecast side has to match: `lead_agg` is how the hindcast's per-lead
# bands combine, and it must equal season_agg or the two sides of the
# correlation describe different quantities.
OBS = {
    "prec": dict(
        collection="UCSB-CHG/CHIRPS/PENTAD", band="precipitation",
        month_agg="sum", season_agg="sum", lead_agg="sum",
        offset=0.0, label="CHIRPS", units="mm",
        # Regridded season totals, staged as their own assets. Not an
        # optimisation — a necessity. Correlating 24 year-pairs where each pair
        # regrids 5 km CHIRPS on the fly runs the export out of memory for the
        # 3-month window: every year's regrid is alive at once inside the single
        # reduce(). Cheap per-year exports materialise the coarse observation,
        # after which the correlation reads 1 degree data and fits comfortably.
        staged=f"{ELNINO_ROOT}/chirps_obs", stem="obs",
        # CHIRPS is land-only and spans 50N-50S, so ocean and high latitudes
        # come back masked — those cells are "not assessed", NOT "low skill",
        # and the UI must keep that distinction.
        coverage="land only, 50N-50S",
    ),
    "t2m": dict(
        collection="ECMWF/ERA5_LAND/MONTHLY_AGGR", band="temperature_2m",
        month_agg="mean", season_agg="mean", lead_agg="mean",
        offset=-273.15,                 # K -> degC, to match terciles_t2m
        label="ERA5-Land", units="degC",
        staged=f"{ELNINO_ROOT}/era5_obs", stem="obs_t2m",
        coverage="land only, global",
        # NOT an independent observation. ERA5 and SEAS5 share IFS physics and
        # assimilation lineage, so temperature skill scored this way is
        # OPTIMISTICALLY BIASED relative to CHIRPS-scored precipitation skill,
        # and ERA5 runs cool over Africa. Partial mitigation, and the reason
        # this is still worth doing: the tercile/Brier framing judges each side
        # against its OWN distribution, so a uniform bias cancels exactly and
        # only direction errors are penalised. A spatially varying bias does
        # not cancel. Both halves of that belong in any caption.
        caveat="reanalysis sharing model lineage with SEAS5; "
               "skill is optimistically biased vs CHIRPS-scored precipitation",
    ),
}
DEFAULT_VAR = "prec"


def skill_collection(var):
    """Precipitation keeps the unsuffixed name the dashboard already reads."""
    return (f"{ELNINO_ROOT}/skill" if var == DEFAULT_VAR
            else f"{ELNINO_ROOT}/skill_{var}")


# Back-compat for anything importing these names.
SKILL_COLLECTION = skill_collection(DEFAULT_VAR)
OBS_COLLECTION = OBS[DEFAULT_VAR]["staged"]
CHIRPS = OBS[DEFAULT_VAR]["collection"]



def init_ee():
    """Service account if available, else a personal login."""
    key = APP / "credentials" / "service_account.json"
    if key.exists():
        with open(key) as fh:
            info = json.load(fh)
        ee.Initialize(
            credentials=ee.ServiceAccountCredentials(
                email=info["client_email"], key_file=str(key)),
            project="unicef-ccri")
    else:
        ee.Initialize(project="unicef-ccri")


def band_names_of(asset_id):
    """Real band names from the image's `band_names` property.

    Uploaded assets arrive as b1..bN because geeup's manifest carries no band
    names; the names ride along in this property. Every consumer renames before
    selecting — see the module docstring of download_ecmwf_prec.py.
    """
    prop = ee.Image(asset_id).get("band_names").getInfo()
    if not prop:
        raise SystemExit(f"ERROR: {asset_id} has no band_names property.")
    return [b.strip() for b in str(prop).split(",") if b.strip()]


def hindcast_collection(var):
    """Raw hindcast collection for a variable (prec keeps the bare name)."""
    return (RAW_HINDCAST_COLLECTION if var == DEFAULT_VAR
            else f"{RAW_HINDCAST_COLLECTION}_{var}")


def hindcast_image_id(var, year):
    stem = "hc" if var == DEFAULT_VAR else f"hc_{var}"
    return f"{hindcast_collection(var)}/{stem}_{year}{INIT_MONTH:02d}"


def hindcast_structure(var=DEFAULT_VAR):
    """({lead_key: [band names]}, [lead keys in order]) for the hindcast images.

    Derived from the bands rather than hardcoded: this collection was 4x25 when
    the season was Sep-Dec and is 3x25 now, so a stale constant would select a
    lead that no longer exists.
    """
    names = band_names_of(hindcast_image_id(var, HC_YEARS[0]))
    by_lead = {}
    for nm in names:
        by_lead.setdefault(nm.split("_")[0], []).append(nm)
    keys = sorted(by_lead, key=lambda k: int(k[1:]))
    if not keys:
        raise SystemExit(f"ERROR: unrecognised hindcast bands: {names[:4]}")
    return names, by_lead, keys


def leads_for_period(period, lead_keys):
    """Which lead band-groups a period covers.

    The window spans every lead; a monthly period is the single lead whose
    number matches. Returning a list either way keeps one code path.
    """
    if period == WINDOW_PERIOD:
        return list(lead_keys)
    lead = PERIOD_MAP[period]["lead"]
    key = f"L{lead}"
    if key not in lead_keys:
        raise SystemExit(f"ERROR: period '{period}' wants {key}, but the "
                         f"hindcast only has {lead_keys}.")
    return [key]


def months_for_period(period):
    """(start_month, n_months); start > 12 rolls into the next year."""
    return obs_months(INIT_TAG, period)


def _combine(images, how):
    """Sum or mean a list of single-band images. One place, so the sum-vs-mean
    choice is always explicit and never defaulted."""
    out = images[0]
    for extra in images[1:]:
        out = out.add(extra)
    if how == "mean":
        return out.divide(len(images))
    if how != "sum":
        raise SystemExit(f"ERROR: unknown aggregation {how!r}")
    return out


def forecast_season_mean(year, hc_names, by_lead, leads, var=DEFAULT_VAR):
    """Ensemble-mean season value for one hindcast year, in the var's units.

    Per-lead member means, then combined across leads the way the variable
    demands: precipitation SUMS to a season total, temperature MEANS to a
    season mean. Averaging all bands at once would produce a MONTHLY value and
    silently compare one month of forecast against several of observation.
    """
    img = ee.Image(hindcast_image_id(var, year)).rename(hc_names)
    per_lead = [img.select(by_lead[k]).reduce(ee.Reducer.mean()) for k in leads]
    return _combine(per_lead, OBS[var]["lead_agg"]).rename("fc")


def observed_season_total(year, start_month, n_months, proj, var=DEFAULT_VAR):
    """Observed season value over the same months, on the forecast grid.

    setDefaultProjection is required: reducing a collection drops the
    projection, and reduceResolution then fails with "does not have a valid
    default projection". reduceResolution before reproject is what averages the
    fine pixels into a 1 degree cell rather than point-sampling one of them.

    Each MONTH is aggregated to the coarse grid before the months are combined.
    Combining first and regridding once ran the 3-month window out of memory:
    that holds three months of 5 km pentads for all 24 years at full
    resolution. Aggregating first is arithmetically identical — combining
    coarse-cell means equals the coarse-cell mean of the combination — but
    keeps only 1 degree data in flight.

    Both aggregations come from OBS[var]: CHIRPS pentads SUM into a month and
    months SUM into a season total; ERA5-Land is already monthly (so the
    month-level reducer sees one image) and months MEAN into a season mean.
    """
    cfg = OBS[var]
    coll = ee.ImageCollection(cfg["collection"]).select(cfg["band"])
    native = ee.Image(coll.first()).projection()

    monthly = []
    for i in range(n_months):
        y, m = year, start_month + i
        while m > 12:
            y, m = y + 1, m - 12
        start = ee.Date.fromYMD(y, m, 1)
        window = coll.filterDate(start, start.advance(1, "month"))
        one = window.sum() if cfg["month_agg"] == "sum" else window.mean()
        # Offset AFTER the monthly reducer and BEFORE the season reducer: for a
        # mean either order works, but for a sum adding -273.15 once per month
        # would subtract it n_months times.
        if cfg["offset"]:
            one = one.add(cfg["offset"])
        monthly.append(one.setDefaultProjection(native)
                          .reduceResolution(ee.Reducer.mean(), maxPixels=65536)
                          .reproject(proj))

    return _combine(monthly, cfg["season_agg"]).rename("obs")


def obs_asset(period, year, var=DEFAULT_VAR):
    cfg = OBS[var]
    return f"{cfg['staged']}/{staged_obs_name(cfg['stem'], INIT_TAG, period, year)}"


def stage_observations(period, proj, overwrite=False, var=DEFAULT_VAR):
    """Export the regridded observed value for every hindcast year.

    Returns (tasks, already_there). Each image is a single 1 degree band, so
    these are small and quick; the point is that the correlation afterwards
    never touches native-resolution data.
    """
    cfg = OBS[var]
    start_month, n_months = months_for_period(period)
    try:
        have = {a["id"].split("/")[-1]
                for a in ee.data.listAssets(
                    {"parent": cfg["staged"]}).get("assets", [])}
    except ee.EEException:
        have = set()

    tasks, skipped = [], 0
    for y in HC_YEARS:
        name = staged_obs_name(cfg["stem"], INIT_TAG, period, y)
        if name in have and not overwrite:
            skipped += 1
            continue
        img = observed_season_total(y, start_month, n_months, proj, var)
        if overwrite and name in have:
            try:
                ee.data.deleteAsset(obs_asset(period, y, var))
            except ee.EEException:
                pass
        t = ee.batch.Export.image.toAsset(
            image=img.set({"period": period, "year": y,
                           "reference": cfg["label"], "units": cfg["units"]}),
            description=name[:100],
            assetId=obs_asset(period, y, var),
            region=ee.Geometry.Rectangle([-180, -60, 180, 60], None, False),
            crs="EPSG:4326", crsTransform=[1, 0, -180, 0, -1, 90],
            maxPixels=1e9,
        )
        t.start()
        tasks.append((t, name))
    return tasks, skipped


def skill_image(period, hc_names, by_lead, lead_keys, proj, staged=True,
                var=DEFAULT_VAR):
    """2-band image: Pearson r between hindcast and observed, and its p-value.

    `staged` reads the pre-exported coarse observations; False recomputes them
    from source inline, which is fine interactively but exhausts memory in a
    global export of the 3-month window.
    """
    leads = leads_for_period(period, lead_keys)
    start_month, n_months = months_for_period(period)

    def obs(y):
        if staged:
            return ee.Image(obs_asset(period, y, var)).rename("obs")
        return observed_season_total(y, start_month, n_months, proj, var)

    pairs = ee.ImageCollection([
        forecast_season_mean(y, hc_names, by_lead, leads, var).addBands(obs(y))
        for y in HC_YEARS
    ])
    # pearsonsCorrelation returns bands "correlation" and "p-value"; the hyphen
    # makes the second awkward to select downstream, hence the rename here.
    out = pairs.reduce(ee.Reducer.pearsonsCorrelation())
    return out.select(["correlation", "p-value"], SKILL_BANDS).toFloat()


def ensure_collection(asset_id):
    try:
        ee.data.getAsset(asset_id)
    except ee.EEException:
        ee.data.createAsset({"type": "IMAGE_COLLECTION"}, asset_id)
        print(f"  created image collection {asset_id}")


def export_one(period, img, overwrite, var=DEFAULT_VAR):
    """Start a toAsset export. Band names survive this path, unlike geeup."""
    stem = "skill" if var == DEFAULT_VAR else f"skill_{var}"
    asset_id = f"{skill_collection(var)}/{stem}_{INIT_TAG}_{period}"
    if overwrite:
        try:
            ee.data.deleteAsset(asset_id)
            print(f"  deleted existing {asset_id}")
        except ee.EEException:
            pass

    # `lead` stays numeric across the collection (0 = the aggregate window):
    # Earth Engine requires one type per property, and a string alongside
    # integers makes ingestion of the odd one out fail.
    cfg = OBS[var]
    props = {
        "kind": "skill",
        "period": period,
        "lead": PERIOD_MAP[period]["lead"],
        "reference": cfg["label"],
        "variable": var,
        "coverage": cfg["coverage"],
        "hindcast": HINDCAST,
        "metric": "pearson_r",
        "system": SYSTEM,
        "init": INIT_TAG,
        "band_names": ",".join(SKILL_BANDS),
    }
    # Travels with the asset so a temperature skill figure cannot be quoted
    # without the reanalysis caveat attached.
    if cfg.get("caveat"):
        props["caveat"] = cfg["caveat"]
    task = ee.batch.Export.image.toAsset(
        image=img.set(props),
        description=f"{stem}_{INIT_TAG}_{period}"[:100],
        assetId=asset_id,
        region=ee.Geometry.Rectangle([-180, -60, 180, 60], None, False),
        # crsTransform, NOT scale. With `scale` Earth Engine derives its own
        # grid and lands on origin -180.9992, which extends past the
        # antimeridian; any later reduction pinned to lon/lat then fails with
        # "Unable to transform edge (...) from EPSG:4326 PLANAR".
        crs="EPSG:4326", crsTransform=[1, 0, -180, 0, -1, 90],
        maxPixels=1e9,
    )
    task.start()
    print(f"  started {asset_id}  (task {task.id})")
    return task, asset_id


def wait_for(tasks, poll=20, timeout=5400):
    """Block until every task leaves a running state.

    Reports the failure reason rather than only the state: the window export
    once failed with "out of memory" and a state-only report made that look
    like an asset that simply never appeared.
    """
    print("\nWaiting for exports ...")
    pending = {t.id: name for t, name in tasks}
    waited, failures = 0, []
    while pending and waited < timeout:
        time.sleep(poll)
        waited += poll
        for tid in list(pending):
            st = ee.data.getTaskStatus([tid])[0]
            state = st.get("state")
            if state in ("COMPLETED", "FAILED", "CANCELLED"):
                msg = st.get("error_message", "")
                ok = state == "COMPLETED"
                print(f"  {'OK  ' if ok else 'FAIL'} {pending[tid]}  {state}"
                      + (f"  {msg[:160]}" if msg else ""))
                if not ok:
                    failures.append((pending[tid], state, msg))
                pending.pop(tid)
        if pending:
            print(f"  ... {len(pending)} still running ({waited}s)")
    if pending:
        print(f"\n  TIMED OUT with {len(pending)} still running: "
              f"{list(pending.values())}")
    if failures:
        print(f"\n  {len(failures)} export(s) FAILED:")
        for name, state, msg in failures:
            print(f"    {name}: {state} {msg[:200]}")
        sys.exit(1)


def main():
    global INIT_TAG, INIT_MONTH, PERIOD_MAP
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True, metavar="YYYYMM",
                    help="forecast issue (init month), e.g. 202610")
    ap.add_argument("--period", action="append", default=None,
                    help="only these periods (default: all four); "
                         "window_L2_L4 or L<lead>_<Mon>, e.g. L3_Dec")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace assets that already exist")
    ap.add_argument("--wait", action="store_true",
                    help="poll until the exports finish")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the computation and report, without exporting")
    ap.add_argument("--stage-obs", action="store_true",
                    help="export the regridded observations and stop. "
                         "Run this first; the skill export reads them.")
    ap.add_argument("--inline-obs", action="store_true",
                    help="skip the staged observations and regrid the source "
                         "inside the skill computation. Fine for a single "
                         "month; exhausts memory on the 3-month window.")
    ap.add_argument("--var", choices=sorted(OBS), default=DEFAULT_VAR,
                    help=f"variable to score (default {DEFAULT_VAR}). "
                         f"prec scores against CHIRPS, t2m against ERA5-Land")
    args = ap.parse_args()
    INIT_TAG, _, PERIOD_MAP = parse_issue(args.init)
    INIT_MONTH = int(INIT_TAG[4:])

    periods = args.period or list(PERIOD_MAP)
    unknown = set(periods) - set(PERIOD_MAP)
    if unknown:
        sys.exit(f"ERROR: {sorted(unknown)} not in issue {INIT_TAG}: "
                 f"{list(PERIOD_MAP)}")
    var = args.var
    cfg = OBS[var]

    print(f"Skill mask — SEAS5 {INIT_TAG} {var} vs {cfg['label']}, "
          f"hindcast {HINDCAST}")
    print(f"  periods: {periods}")
    print(f"  aggregation: month={cfg['month_agg']} season={cfg['season_agg']} "
          f"lead={cfg['lead_agg']}")
    if cfg.get("caveat"):
        print(f"  CAVEAT: {cfg['caveat']}")
    init_ee()

    hc_names, by_lead, lead_keys = hindcast_structure(var)
    print(f"  hindcast leads: {lead_keys}  ({len(hc_names)} bands)")

    # The terciles grid is the target: skill must line up cell-for-cell with the
    # signal it qualifies.
    proj = ee.Image(
        f"{TERCILES_COLLECTION}/terciles_{INIT_TAG}_{WINDOW_PERIOD}").projection()

    if not args.dry_run:
        ensure_collection(skill_collection(var))

    # Phase 1 — stage the coarse observations.
    if args.stage_obs:
        ensure_collection(cfg["staged"])
        tasks = []
        for period in periods:
            t, skipped = stage_observations(period, proj, args.overwrite, var)
            print(f"\n{period}: {len(t)} observation export(s) started"
                  + (f", {skipped} already present" if skipped else ""))
            tasks += t
        if not tasks:
            print("\nAll observations already staged.")
            return
        if args.wait:
            wait_for(tasks)
        else:
            print("\nObservation exports started. Re-run without --stage-obs "
                  "once they finish.")
        return

    # Phase 2 — the skill correlation itself.
    staged = not args.inline_obs
    tasks = []
    for period in periods:
        img = skill_image(period, hc_names, by_lead, lead_keys, proj, staged,
                          var)
        leads = leads_for_period(period, lead_keys)
        sm, nm = months_for_period(period)
        print(f"\n{period}: leads {leads}, {cfg['label']} months "
              f"{sm}..{sm + nm - 1}"
              f", observations {'staged' if staged else 'inline'}")
        if args.dry_run:
            # Force evaluation over a small box so a broken graph fails HERE
            # rather than inside a batch task an hour later.
            box = ee.Geometry.Rectangle([30, -5, 45, 15], None, False)
            s = img.select("skill_r").reduceRegion(
                ee.Reducer.mean(), box, 111319, bestEffort=True).getInfo()
            print(f"  dry-run OK — mean r over the Horn of Africa: "
                  f"{s.get('skill_r')}")
            continue
        tasks.append(export_one(period, img, args.overwrite, var))

    if args.dry_run:
        print("\n--dry-run: nothing exported.")
        return
    if args.wait:
        wait_for(tasks)
    else:
        print("\nExports started. Track them with: earthengine task list")


if __name__ == "__main__":
    main()
