#!/usr/bin/env python
"""
prep_infra.py — normalise fetched infrastructure point data into one standard
output contract, ready to upload as GEE table assets.

Reads whatever the fetch_*.py scripts left in app/data/infra/{iso3}/raw/ and
writes app/data/infra/{iso3}/out/{iso3}_{source}_{layer}.csv (plus a .geojson
for local inspection).

The output contract — identical for every source
------------------------------------------------
    facility_id   stable id from the source (row index as a last resort)
    name          facility name; "" when genuinely unknown, never "nan"/"None"
    longitude     numeric, EPSG:4326
    latitude      numeric, EPSG:4326
    <extras>      only where the source has them (education_level, subtype, ...)

`facility_type`, `source` and `iso3` are NOT columns: they are implicit in the
asset name ({iso3}_{source}_{layer}), which keeps one source of truth and stops
a column ever disagreeing with the asset it lives in.

Asset ids the outputs are destined for:
    projects/unicef-ccri/assets/infrastructure/{iso3}_{source}_{layer}
    source in {giga, healthsites, wpdx, mwater, hdx, co}
    layer  in {schools, health_facilities, water_points}

Usage:
    python scripts/prep_infra.py --iso3 mwi                    # every source found
    python scripts/prep_infra.py --iso3 mwi --source mwater    # just one
    python scripts/prep_infra.py --iso3 mwi --iso3 eth --iso3 gha
"""
import argparse
import json
import os
import sys

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

from _common import (adm0_countries, country_name, raw_dir, out_dir,
                     asset_stem, script_cmd,
                     LAYER_SCHOOLS, LAYER_HEALTH, LAYER_WATER)

# The standard contract. Extras are appended per job.
SCHEMA = ["facility_id", "name", "longitude", "latitude"]

# Valid components of an asset stem — upload_infra.py validates against these.
# "co" is country-office submitted data (see templates/README.md).
SOURCES = ["giga", "healthsites", "wpdx", "mwater", "hdx", "co"]
LAYERS   = [LAYER_SCHOOLS, LAYER_HEALTH, LAYER_WATER]


# ---------------------------------------------------------------------------
# Source definitions
#
# Each job says: which file to read, which layer it becomes, and how to find the
# id / name / coordinates / extras in that source's own schema. Adding a source
# means adding one entry here — no new code path.
# ---------------------------------------------------------------------------
JOBS = [
    {
        "source": "giga", "layer": LAYER_SCHOOLS, "file": "giga_schools.csv",
        "id":   ["giga_id_school", "school_id_giga", "id"],
        "name": ["school_name", "name"],
        "lon":  ["longitude"], "lat": ["latitude"],
        "extras": {"education_level": ["education_level"]},
    },
    {
        "source": "healthsites", "layer": LAYER_HEALTH, "file": "healthsites.csv",
        "id":   ["osm_id", "uuid", "id"],
        "name": ["name", "name_en"],
        "lon":  ["longitude"], "lat": ["latitude"],
        "extras": {"subtype": ["amenity", "healthcare"]},
    },
    {
        "source": "wpdx", "layer": LAYER_WATER, "file": "wpdx_water.csv",
        "id":   ["wpdx_id", "row_id"],
        "name": ["water_source_clean", "water_source_category", "source"],
        "lon":  ["longitude", "lon_deg"], "lat": ["latitude", "lat_deg"],
        "extras": {"subtype": ["water_source_category", "water_source_clean"],
                   "status":  ["status_clean"]},
    },
    {
        "source": "mwater", "layer": LAYER_SCHOOLS, "file": "mwater_schools.csv",
        "id": ["facility_id"], "name": ["name"],
        "lon": ["longitude"], "lat": ["latitude"],
        "extras": {"subtype": ["subtype"]},
    },
    {
        "source": "mwater", "layer": LAYER_HEALTH, "file": "mwater_health.csv",
        "id": ["facility_id"], "name": ["name"],
        "lon": ["longitude"], "lat": ["latitude"],
        "extras": {"subtype": ["subtype"]},
    },
    {
        "source": "mwater", "layer": LAYER_WATER, "file": "mwater_water.csv",
        "id": ["facility_id"], "name": ["name"],
        "lon": ["longitude"], "lat": ["latitude"],
        "extras": {"subtype": ["subtype"]},
    },
    # ── Country-office submissions ──────────────────────────────────────────
    # Filled in from the template in scripts/templates/. The alias lists are
    # deliberately generous: offices export from QGIS, Excel, KoBo and ODK,
    # which each spell the coordinate columns differently, and every alias
    # handled here is one less file to hand-correct.
    {
        "source": "co", "layer": LAYER_SCHOOLS, "file": "co_schools.csv",
        "id":   ["facility_id", "id", "code", "school_id"],
        "name": ["name", "facility_name", "school_name"],
        "lon":  ["longitude", "lon", "long", "x"],
        "lat":  ["latitude", "lat", "y"],
        "extras": {"subtype": ["subtype", "type", "category"],
                   "education_level": ["education_level", "level"]},
    },
    {
        "source": "co", "layer": LAYER_HEALTH, "file": "co_health.csv",
        "id":   ["facility_id", "id", "code", "facility_code"],
        "name": ["name", "facility_name"],
        "lon":  ["longitude", "lon", "long", "x"],
        "lat":  ["latitude", "lat", "y"],
        "extras": {"subtype": ["subtype", "type", "category", "amenity"]},
    },
    {
        "source": "co", "layer": LAYER_WATER, "file": "co_water.csv",
        "id":   ["facility_id", "id", "code", "wpdx_id"],
        "name": ["name", "facility_name", "water_source"],
        "lon":  ["longitude", "lon", "long", "x"],
        "lat":  ["latitude", "lat", "y"],
        "extras": {"subtype": ["subtype", "type", "water_source", "category"],
                   "status": ["status", "functionality", "status_clean"]},
    },
]


def _first(df, candidates):
    """First candidate column actually present, else None.
    """
    if not candidates:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    lowered = {str(col).lower(): col for col in df.columns}
    for c in candidates:
        hit = lowered.get(str(c).lower())
        if hit is not None:
            return hit
    return None


def _clean_text(series):
    """Text for a GEE-bound CSV: no NaN sentinels, no embedded newlines.

    GEE's CSV parser mis-splits quoted multi-line fields, which shifts the
    longitude/latitude columns and corrupts the geometry. A real example that
    caused this: a school named "Mpatawamilonde c\\nCDSS". Collapsing all
    whitespace keeps every record on one physical line.
    """
    return (series.fillna("").astype(str)
            .replace({"nan": "", "None": "", "NaN": ""})
            .str.replace(r"\s+", " ", regex=True).str.strip())


def prep_one(job, data_dir):
    """Normalise one source file to the standard contract; None if absent."""
    path = os.path.join(data_dir, job["file"])
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    if df.empty:
        print(f"    {job['file']}: empty — skipping")
        return None

    lon_col, lat_col = _first(df, job["lon"]), _first(df, job["lat"])
    if not lon_col or not lat_col:
        print(f"    {job['file']}: no coordinate columns "
              f"(looked for {job['lon']} / {job['lat']}) — skipping")
        return None

    out = pd.DataFrame()
    id_col = _first(df, job["id"])
    out["facility_id"] = (df[id_col].astype(str).values if id_col
                          else [str(i) for i in range(len(df))])
    name_col = _first(df, job["name"])
    out["name"] = _clean_text(df[name_col]) if name_col else ""

    out["longitude"] = pd.to_numeric(df[lon_col], errors="coerce")
    out["latitude"]  = pd.to_numeric(df[lat_col], errors="coerce")

    extras = []
    for col, cands in (job.get("extras") or {}).items():
        src_col = _first(df, cands)
        if src_col:
            out[col] = _clean_text(df[src_col])
            extras.append(col)

    bad = out["longitude"].isna() | out["latitude"].isna()
    if bad.any():
        print(f"    dropping {int(bad.sum())} rows without valid coordinates")
        out = out[~bad]
    if out.empty:
        return None

    return out[SCHEMA + extras].reset_index(drop=True)


def write_outputs(df, dest, stem):
    """Write the GEE upload CSV and a GeoJSON for local inspection."""
    os.makedirs(dest, exist_ok=True)
    csv_path = os.path.join(dest, stem + ".csv")
    df.to_csv(csv_path, index=False, encoding="utf-8")

    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=[Point(xy) for xy in zip(df["longitude"], df["latitude"])],
        crs=4326)
    gdf.to_file(os.path.join(dest, stem + ".geojson"), driver="GeoJSON")
    return csv_path


def prep_country(iso3, sources=None, data_dir=None, out_root=None):
    """Run every matching job for one country. Returns [(stem, rows, path)]."""
    data = raw_dir(iso3, data_dir)
    dest = out_dir(iso3, out_root)
    if not os.path.isdir(data):
        print(f"    no raw folder {data} — run the fetch scripts first")
        return []

    results = []
    for job in JOBS:
        if sources and job["source"] not in sources:
            continue
        df = prep_one(job, data)
        if df is None:
            continue
        stem = asset_stem(iso3, job["source"], job["layer"])
        path = write_outputs(df, dest, stem)
        named = int((df["name"].str.strip() != "").sum())
        pct = named / len(df) * 100 if len(df) else 0
        print(f"    {stem:44s} {len(df):>7d} rows, {pct:5.1f}% named")
        results.append((stem, len(df), path))
    return results


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", required=True,
                    help="country ISO3 code; repeat for several")
    ap.add_argument("--source", action="append", choices=SOURCES, default=None,
                    help="only prep these sources (default: every one found)")
    ap.add_argument("--data", default=None,
                    help="raw dir (default app/data/infra/{iso3}/raw); "
                         "only valid with a single --iso3")
    ap.add_argument("--out", default=None,
                    help="output dir (default app/data/infra/{iso3}/out); "
                         "only valid with a single --iso3")
    args = ap.parse_args()

    iso3s = [c.lower() for c in args.iso3]
    if len(iso3s) > 1 and (args.data or args.out):
        sys.exit("ERROR: --data/--out only work with a single --iso3.")

    countries = adm0_countries()
    total = 0
    for iso3 in iso3s:
        print(f"\n{iso3.upper()} — {country_name(iso3, countries)}")
        res = prep_country(iso3, sources=args.source, data_dir=args.data,
                           out_root=args.out)
        total += len(res)
        if not res:
            print("    nothing prepped")

    print(f"\nDone. {total} asset file(s) ready.")
    print(f"Next: {script_cmd('upload_infra.py')} --iso3 "
          + " --iso3 ".join(iso3s))


if __name__ == "__main__":
    main()
