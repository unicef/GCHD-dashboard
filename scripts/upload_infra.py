#!/usr/bin/env python
"""
upload_infra.py — upload prepped infrastructure CSVs to GEE as table assets,
using geeup (https://github.com/samapriya/geeup).

Why geeup: the Earth Engine ingestion APIs (`ee.data.startTableIngestion`,
`earthengine upload table`) stage through a Google Cloud Storage bucket, which
this project has no access to. geeup handles staging internally, so it is the
supported upload path here.

geeup names each asset after the CSV's filename stem, so the stems written by
prep_infra.py ({iso3}_{source}_{layer}) become the asset ids directly. Every
stem is validated against that pattern before anything is uploaded — a malformed
name (e.g. a singular 'school') would create an asset the app's discovery regex
silently ignores.

Prerequisites (one-off):
    pip install geeup
    geeup auth --cred app/credentials/service_account.json

Usage:
    python scripts/upload_infra.py --iso3 mwi --dry-run
    python scripts/upload_infra.py --iso3 mwi
    python scripts/upload_infra.py --iso3 mwi --iso3 eth --iso3 mdg
    python scripts/upload_infra.py --iso3 mwi --user you@example.org

The staging step copies the CSVs for one run into a single flat directory
(app/data/infra_upload by default) because geeup uploads a directory at a time.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from _common import HERE, DATA_ROOT, load_env, out_dir
from prep_infra import SOURCES, LAYERS

GEE_FOLDER = "projects/unicef-ccri/assets/infrastructure"

# The contract the app's discovery relies on: {iso3}_{source}_{layer}.
STEM_RE = re.compile(
    r"^(?P<iso3>[a-z]{3})_(?P<source>%s)_(?P<layer>%s)$"
    % ("|".join(SOURCES), "|".join(LAYERS)))


def collect(iso3s, out_root=None, sources=None, layers=None):
    """Prepped CSVs for the given countries, validated against the stem rule.

    `sources`/`layers` narrow the selection further, so a country's folder
    holding several sources can still be uploaded one source at a time.
    """
    found, bad = [], []
    for iso3 in iso3s:
        d = out_dir(iso3, out_root)
        for path in sorted(glob.glob(os.path.join(d, "*.csv"))):
            stem = os.path.splitext(os.path.basename(path))[0]
            m = STEM_RE.match(stem)
            if not m:
                bad.append((stem, path))
                continue
            if sources and m.group("source") not in sources:
                continue
            if layers and m.group("layer") not in layers:
                continue
            found.append((stem, path))
    return found, bad


def stage(files, staging):
    """Copy the run's CSVs into one flat directory for geeup."""
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging, exist_ok=True)
    for stem, path in files:
        shutil.copy2(path, os.path.join(staging, stem + ".csv"))
    return staging


def existing_assets():
    """Asset ids already in the GEE infrastructure folder (for the report)."""
    try:
        import ee
        key = os.path.join(HERE, "..", "app", "credentials", "service_account.json")
        with open(key) as fh:
            info = json.load(fh)
        ee.Initialize(
            credentials=ee.ServiceAccountCredentials(email=info["client_email"],
                                                     key_file=key),
            project="unicef-ccri")
        out, token = set(), None
        while True:
            req = {"parent": GEE_FOLDER}
            if token:
                req["pageToken"] = token
            res = ee.data.listAssets(req)
            for a in res.get("assets", []):
                out.add(a["id"].split("/")[-1])
            token = res.get("nextPageToken")
            if not token:
                return out
    except Exception as e:
        print(f"  (could not list existing assets: {e})")
        return set()


def run_geeup(staging, user, overwrite, dry_run, workers=1):
    cmd = [
        "geeup", "tabup",
        "--source", staging,
        "--dest", GEE_FOLDER,
        "--x", "longitude", "--y", "latitude",
    ]
    if user:
        cmd += ["--user", user]
    if overwrite:
        cmd += ["--overwrite", "yes"]
    if workers and workers > 1:
        cmd += ["--workers", str(workers)]
    if dry_run:
        cmd += ["--dry-run"]

    print("\n$ " + " ".join(cmd) + "\n")
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        sys.exit("ERROR: `geeup` not found on PATH.\n"
                 "       pip install geeup\n"
                 "       geeup auth --cred app/credentials/service_account.json")


def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", default=None,
                    help="country ISO3 code; repeat for several")
    ap.add_argument("--all-countries", action="store_true",
                    help="every country with prepped files on disk")
    ap.add_argument("--source", action="append", choices=SOURCES, default=None,
                    help="only upload these sources (default: every prepped "
                         "source found for the countries)")
    ap.add_argument("--layer", action="append", choices=LAYERS, default=None,
                    help="only upload these layers (default: all)")
    ap.add_argument("--user", default=os.environ.get("GEEUP_USER"),
                    help="Google account email for geeup (default: $GEEUP_USER "
                         "from scripts/.env)")
    ap.add_argument("--staging", default=os.path.join(DATA_ROOT, "_upload"),
                    help="flat directory geeup uploads from (rebuilt each run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace assets that already exist")
    ap.add_argument("--skip-existing", action="store_true",
                    help="upload only assets not already in GEE, leaving "
                         "existing ones untouched (the resumable option for a "
                         "large batch)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel uploads (default 1; raise only after a "
                         "serial run succeeds)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate without uploading")
    args = ap.parse_args()

    if args.all_countries:
        iso3s = sorted(
            os.path.basename(d) for d in glob.glob(os.path.join(DATA_ROOT, "*"))
            if os.path.isdir(os.path.join(d, "out")))
    elif args.iso3:
        iso3s = [c.lower() for c in args.iso3]
    else:
        sys.exit("ERROR: pass --iso3 <code> (repeatable) or --all-countries.")

    files, bad = collect(iso3s, sources=args.source, layers=args.layer)

    if bad:
        print("ERROR: these files don't match {iso3}_{source}_{layer} and would "
              "create assets the app cannot discover:")
        for stem, path in bad:
            print(f"    {stem}  ({path})")
        print(f"  valid sources: {SOURCES}")
        print(f"  valid layers:  {LAYERS}")
        sys.exit(1)

    if not files:
        filt = ""
        if args.source:
            filt += f" --source {' --source '.join(args.source)}"
        if args.layer:
            filt += f" --layer {' --layer '.join(args.layer)}"
        sys.exit(f"ERROR: no prepped CSVs for "
                 f"{', '.join(i.upper() for i in iso3s)}{filt}.\n"
                 f"       Run: python scripts/prep_infra.py --iso3 "
                 + " --iso3 ".join(iso3s))

    if args.overwrite and args.skip_existing:
        sys.exit("ERROR: --overwrite and --skip-existing are contradictory.")

    present = existing_assets()

    if args.skip_existing:
        already = [s for s, _ in files if s in present]
        files = [(s, p) for s, p in files if s not in present]
        if already:
            print(f"Skipping {len(already)} asset(s) already in GEE "
                  "(--skip-existing).")
        if not files:
            print("Nothing new to upload — every selected asset is already "
                  "in GEE.")
            return

    print(f"{len(files)} asset(s) to upload into {GEE_FOLDER}:")
    for stem, path in files:
        mb = os.path.getsize(path) / 1e6
        mark = "OVERWRITE" if stem in present else "new"
        print(f"    {stem:46s} {mb:7.1f} MB   [{mark}]")

    clashes = [s for s, _ in files if s in present]
    if clashes and not args.overwrite and not args.dry_run:
        print(f"\nERROR: {len(clashes)} asset(s) already exist. Re-run with "
              "--overwrite to replace them, or --skip-existing to upload only "
              "the new ones.")
        sys.exit(1)

    staging = stage(files, args.staging)
    print(f"\nStaged {len(files)} file(s) in {staging}")

    rc = run_geeup(staging, args.user, args.overwrite, args.dry_run, args.workers)
    if rc != 0:
        sys.exit(f"geeup exited with status {rc}")

    if not args.dry_run:
        print("\nVerifying against the GEE folder ...")
        now = existing_assets()
        missing = [s for s, _ in files if s not in now]
        print(f"  {len(files) - len(missing)}/{len(files)} asset(s) present")
        if missing:
            print("  NOT found (ingestion may still be running — re-check in a "
                  "minute):")
            for s in missing:
                print(f"    {s}")
        print("\nRemember: new assets must be readable by the app's service "
              "account.")


if __name__ == "__main__":
    main()
