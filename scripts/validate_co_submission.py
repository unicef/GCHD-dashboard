#!/usr/bin/env python
"""
validate_co_submission.py - check a country-office CSV before it is ingested.

Reports problems in plain language, so an office can fix its own file instead
of waiting on a round trip. Every check here mirrors something prep_infra.py
or GEE would do silently: a row dropped for a bad coordinate, a name whose
line break shifts the columns, a point that lands in the sea.

Needs only pandas - no GEE credentials - so a country office can run it on its
own machine before sending anything.

Usage:
    python scripts/validate_co_submission.py path/to/co_schools.csv
    python scripts/validate_co_submission.py path/to/*.csv
    python scripts/validate_co_submission.py mydata.csv --layer schools

Exit code: 0 clean, 1 warnings only, 2 errors.
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prep_infra import JOBS, SCHEMA, _first, _clean_text   # noqa: E402

_LAYER_BY_FILE = {j["file"]: j for j in JOBS if j["source"] == "co"}


def _job_for(path, layer=None):
    """Pick the co job matching the filename, or an explicit --layer."""
    base = os.path.basename(path).lower()
    if layer:
        for j in JOBS:
            if j["source"] == "co" and j["layer"] == layer:
                return j
    for fname, job in _LAYER_BY_FILE.items():
        if base == fname or fname.replace(".csv", "") in base:
            return job
    return None


def validate(path, layer=None):
    """Return (errors, warnings, n_usable_rows)."""
    errors, warnings = [], []

    job = _job_for(path, layer)
    if job is None:
        return ([f"Cannot tell which facility type this is. Name the file "
                 f"co_schools.csv / co_health.csv / co_water.csv, or pass "
                 f"--layer."], [], 0)

    try:
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except Exception as e:
        return ([f"Could not read the file as CSV: {e}"], [], 0)

    if df.empty:
        return (["The file has no rows."], [], 0)

    # ── Columns ──────────────────────────────────────────────────────────────
    lon_col = _first(df, job["lon"])
    lat_col = _first(df, job["lat"])
    if lon_col is None:
        errors.append(f"No longitude column. Name it 'longitude' (also accepted: "
                      f"{', '.join(job['lon'][1:])}).")
    if lat_col is None:
        errors.append(f"No latitude column. Name it 'latitude' (also accepted: "
                      f"{', '.join(job['lat'][1:])}).")
    if _first(df, job["name"]) is None:
        warnings.append("No name column - facilities will be unlabelled.")
    if _first(df, job["id"]) is None:
        warnings.append("No facility_id column - ids will be generated, so a "
                        "re-submission cannot update existing records.")

    for implicit in ("iso3", "country", "source", "facility_type"):
        if implicit in {c.lower() for c in df.columns}:
            warnings.append(f"Column '{implicit}' is ignored - country, type and "
                            f"source are recorded when the layer is registered.")

    if errors:
        return (errors, warnings, 0)

    # ── Coordinates ──────────────────────────────────────────────────────────
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    lat = pd.to_numeric(df[lat_col], errors="coerce")

    n_bad = int((lon.isna() | lat.isna()).sum())
    if n_bad:
        warnings.append(f"{n_bad} of {len(df)} rows have a missing or "
                        f"non-numeric coordinate and will be dropped.")

    ok = lon.notna() & lat.notna()
    if not ok.any():
        errors.append("No row has a usable coordinate pair.")
        return (errors, warnings, 0)

    # Out of range is unambiguous: these are not valid degrees at all.
    n_lon_range = int((ok & (lon.abs() > 180)).sum())
    n_lat_range = int((ok & (lat.abs() > 90)).sum())
    if n_lon_range:
        errors.append(f"{n_lon_range} rows have longitude outside -180..180.")
    if n_lat_range:
        errors.append(f"{n_lat_range} rows have latitude outside -90..90. "
                      f"If these look like longitudes, the two columns are "
                      f"swapped.")

    # Values far outside degree range are almost always a projected CRS.
    if (ok & (lon.abs() > 1000)).any() or (ok & (lat.abs() > 1000)).any():
        errors.append("Coordinates look like metres, not degrees - reproject "
                      "to WGS84 (EPSG:4326) before exporting.")

    # Exactly 0,0 is the classic 'missing coordinate' sentinel.
    n_null_island = int((ok & (lon == 0) & (lat == 0)).sum())
    if n_null_island:
        warnings.append(f"{n_null_island} rows sit at exactly 0,0 - usually a "
                        f"blank coordinate rather than a real location.")

    # A single hemisphere sign error is the hardest fault to spot by eye.
    if ok.any() and (lat[ok] > 0).any() and (lat[ok] < 0).any():
        warnings.append("Latitudes span both hemispheres - check for a missing "
                        "minus sign if the country is entirely north or south "
                        "of the equator.")

    # ── Text safety ──────────────────────────────────────────────────────────
    name_col = _first(df, job["name"])
    if name_col is not None:
        raw = df[name_col].fillna("").astype(str)
        n_multiline = int(raw.str.contains(r"[\r\n]", regex=True).sum())
        if n_multiline:
            warnings.append(f"{n_multiline} names contain a line break. These "
                            f"are collapsed automatically, but a line break can "
                            f"shift columns in some exports - check those rows.")

    # ── Duplicates ───────────────────────────────────────────────────────────
    dup = int(df.duplicated(subset=[lon_col, lat_col]).sum())
    if dup:
        warnings.append(f"{dup} rows share a coordinate with another row - "
                        f"possible duplicates.")

    return (errors, warnings, int(ok.sum()))


def main():
    ap = argparse.ArgumentParser(
        description="Validate a country-office facility CSV before submission.")
    ap.add_argument("files", nargs="+", help="CSV file(s) to check")
    ap.add_argument("--layer", choices=["schools", "health_facilities",
                                        "water_points"],
                    help="facility type, if the filename does not say")
    args = ap.parse_args()

    worst = 0
    for path in args.files:
        print(f"\n=== {os.path.basename(path)} ===")
        if not os.path.exists(path):
            print("  ERROR  file not found")
            worst = max(worst, 2)
            continue

        errors, warnings, n_ok = validate(path, args.layer)

        for e in errors:
            print(f"  ERROR    {e}")
        for w in warnings:
            print(f"  WARNING  {w}")

        if errors:
            print(f"\n  Not ready to send - fix the errors above.")
            worst = max(worst, 2)
        elif warnings:
            print(f"\n  OK with warnings - {n_ok} usable rows. Review the "
                  f"warnings, then send.")
            worst = max(worst, 1)
        else:
            print(f"  All checks passed - {n_ok} usable rows. Ready to send.")

    return 0 if worst == 0 else (1 if worst == 1 else 2)


if __name__ == "__main__":
    sys.exit(main())
