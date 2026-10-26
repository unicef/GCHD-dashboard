#!/usr/bin/env python
"""
build_enso_mask.py — where El Niño has historically shifted a season's rainfall.

Method of github.com/dohyung-kim/ENSO (build_ond_masks.py), generalised to any
3-month season. Source: Lenssen et al. (2020) ENSO teleconnection
probabilities, IRI Data Library. Per 0.5° cell, for El Niño years:
    +1  P(above-normal) >= MIN_PROB and significant  (historically wetter)
    -1  P(below-normal) >= MIN_PROB and significant  (historically drier)
     0  neither, or both
Writes data/enso_mask/enso_mask_<SEASON>_sig<S>_p<P>.tif and uploads it to
el_nino/enso_mask/ (shared by every issue; one image per season).

--classify-years prints each hindcast year's El Niño / La Niña / neutral class
for an issue's season (observed OISST Niño 3.4, ±ENSO_EVENT_MIN), to paste into
ENSO_YEARS in app/elnino_config.py.

Usage (PY = scripts/el_nino/.venv/Scripts/python):
    PY scripts/el_nino/build_enso_mask.py --init 202610           # its season, NDJ
    PY scripts/el_nino/build_enso_mask.py --season DJF
    PY scripts/el_nino/build_enso_mask.py --season OND --no-upload
    PY scripts/el_nino/build_enso_mask.py --init 202610 --classify-years
"""
import argparse
import csv
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(APP))

import download_ecmwf_prec as dl      # noqa: E402  geeup helpers; fixes PROJ env
import numpy as np                    # noqa: E402
import rasterio                       # noqa: E402
from rasterio.transform import from_origin   # noqa: E402

from elnino_config import (            # noqa: E402
    ELNINO_ROOT, HC_YEARS, LEADS, NINO34_BOX, OISST_COLLECTION, OISST_SCALE,
    ENSO_EVENT_MIN, lead_month, parse_issue, season_code,
)

IRI = "http://iridl.ldeo.columbia.edu/home/.lenssen/.ensoTeleconnections/dods.asc"
SIGNIFICANCE = [85, 90, 95, 99]       # IRI index order
EL_NINO = 0                           # ENSO = [ElNino, LaNina]
FILL = 1e30                           # IRI missing value is 1e32
MIN_PROB = 0.5
OUT = HERE / "data" / "enso_mask"
COLLECTION = f"{ELNINO_ROOT}/enso_mask"
_INITIALS = "JFMAMJJASOND"


def season_start(code):
    """First month (1-12) of a 3-letter season code such as NDJ."""
    starts = [m for m in range(12)
              if "".join(_INITIALS[(m + i) % 12] for i in range(3)) == code]
    if len(starts) != 1:
        sys.exit(f"ERROR: unknown season {code!r}")
    return starts[0] + 1


def mask_name(season, significance, min_prob):
    return f"enso_mask_{season}_sig{significance}_p{round(min_prob * 100)}"


def fetch_prob(season, significance):
    """(2, 360, 720) El Niño P(above), P(below); south-up; NaN = not significant."""
    t = season_start(season) % 12      # T is the centre month: 0.5 = DJF
    s = SIGNIFICANCE.index(significance)
    url = f"{IRI}?prob[{s}][0:1][{EL_NINO}][{t}][0:359][0:719]"
    lines = urllib.request.urlopen(url, timeout=300).read().decode().splitlines()
    if not lines[0].startswith("prob, [1][2][1][1][360][720]"):
        sys.exit(f"ERROR: unexpected IRI response: {lines[0][:120]}")
    a = np.array(lines[1].split(","), dtype="float64").reshape(2, 360, 720)
    a[a >= FILL] = np.nan
    return a


def combined(prob, min_prob):
    """int8 -1/0/+1, north-up, and the count of wet-and-dry cells set to 0."""
    wet = np.isfinite(prob[0]) & (prob[0] >= min_prob)
    dry = np.isfinite(prob[1]) & (prob[1] >= min_prob)
    out = wet.astype("int8") - dry.astype("int8")
    out[wet & dry] = 0
    return out[::-1].copy(), int((wet & dry).sum())


def write_tif(arr, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=360, width=720,
                       count=1, dtype="int8", crs="EPSG:4326",
                       transform=from_origin(-180, 90, 0.5, 0.5),
                       compress="deflate") as dst:
        dst.write(arr, 1)


def upload(tif, name, season, significance, min_prob, args):
    staging = OUT / "_upload" / name
    if staging.is_dir():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy2(tif, staging / tif.name)
    meta = OUT / f"_meta_{name}.csv"
    with open(meta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id_no", "band_names", "kind", "legend", "period",
                    "significance", "source"])
        w.writerow([name, "enso_dir", "enso_mask",
                    "-1=historically drier,1=historically wetter,"
                    "0=no significant signal", season,
                    f"p{round(min_prob * 100)}_sig{significance}",
                    "Lenssen et al. 2020 (IRI); method of dohyung-kim/ENSO"])
    dl.check_cookies(dl.cookie_path())
    # Categorical int8: MODE pyramids, and no nodata (-9999 does not fit int8).
    return dl.run_geeup(staging, meta, COLLECTION, args.user, args.overwrite,
                        args.dry_run, pyramiding="MODE", nodata=None)


def classify_years(tag):
    """{elnino, lanina, neutral: [hindcast years]} by observed season Niño 3.4."""
    import ee
    key = APP / "credentials" / "service_account.json"
    if key.exists():
        info = json.loads(key.read_text())
        ee.Initialize(credentials=ee.ServiceAccountCredentials(
            email=info["client_email"], key_file=str(key)), project="unicef-ccri")
    else:
        ee.Initialize(project="unicef-ccri")
    box = ee.Geometry.Rectangle(NINO34_BOX, None, False)

    def month(y, m):
        start = ee.Date.fromYMD(y, m, 1)
        return (ee.ImageCollection(OISST_COLLECTION).select("anom")
                .filterDate(start, start.advance(1, "month"))
                .mean().multiply(OISST_SCALE)
                .reduceRegion(ee.Reducer.mean(), box, 25000, bestEffort=True)
                .get("anom"))

    wanted = {str(y): ee.List([month(*lead_month(f"{y}{tag[4:]}", l))
                               for l in LEADS]) for y in HC_YEARS}
    got = ee.Dictionary(wanted).getInfo()
    out = {"elnino": [], "lanina": [], "neutral": []}
    for y in HC_YEARS:
        v = sum(got[str(y)]) / len(LEADS)
        cls = ("elnino" if v >= ENSO_EVENT_MIN else
               "lanina" if v <= -ENSO_EVENT_MIN else "neutral")
        out[cls].append(y)
        print(f"  {y}  {v:+.2f}  {cls}")
    return out


def main():
    dl.load_env()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--season", help="3-month season code, e.g. NDJ")
    g.add_argument("--init", metavar="YYYYMM",
                   help="forecast issue; uses its season (leads 2-4)")
    ap.add_argument("--significance", type=int, default=90,
                    choices=SIGNIFICANCE)
    ap.add_argument("--min-prob", type=float, default=MIN_PROB)
    ap.add_argument("--classify-years", action="store_true",
                    help="print ENSO_YEARS for --init's season and stop")
    ap.add_argument("--user", default=os.environ.get("GEEUP_USER"))
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tag = parse_issue(args.init)[0] if args.init else None
    season = season_code(tag) if tag else args.season.upper()
    season_start(season)

    if args.classify_years:
        if not tag:
            sys.exit("ERROR: --classify-years needs --init (years follow the "
                     "issue's hindcast).")
        print(f"Niño 3.4 for {season} of each hindcast year (issue {tag}):")
        years = classify_years(tag)
        print(f'\nENSO_YEARS["{tag}"] = {json.dumps(years)}')
        return

    name = mask_name(season, args.significance, args.min_prob)
    print(f"{name}: fetching El Niño probabilities from IRI ...")
    arr, overlap = combined(fetch_prob(season, args.significance),
                            args.min_prob)
    cells = {int(k): int(v) for k, v in zip(*np.unique(arr, return_counts=True))}
    print(f"  cells: {cells}  (wet-and-dry set to 0: {overlap})")
    tif = OUT / f"{name}.tif"
    write_tif(arr, tif)
    print(f"  wrote {tif}")
    if args.no_upload:
        return
    if upload(tif, name, season, args.significance, args.min_prob, args):
        sys.exit("ERROR: upload failed")


if __name__ == "__main__":
    main()
