#!/usr/bin/env python
"""
schools_multihazard.py — flag which schools sit inside each hazard footprint and
how many hazards overlap at each school. No population involved.

Standalone task script: it reuses the app's hazard list and default thresholds
(config.HAZARDS / HAZARD_TOPICS) but writes only local CSVs. Schools come from
the GEE infrastructure asset, or from prep_infra.py's local CSV if not uploaded.

Usage
-----
    python scripts/schools_multihazard.py                       # 7 countries, giga + mwater
    python scripts/schools_multihazard.py --iso3 ken --iso3 dji
    python scripts/schools_multihazard.py --source mwater --iso3 mdg

Outputs (in --out, default scripts/out/schools_multihazard/)
    {iso3}_{source}_schools_hazards.csv   one row per school: topic flags,
                                          sub-hazard intensities, n_hazards (all
                                          topics), n_climate_hazards (app's MHC set)
    {iso3}_{source}_topic_overlap.csv     topic x topic school counts
    summary.csv                           per country: totals and per-topic counts
    hazard_count_distribution.csv         per country: schools by n_hazards
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "app"))

import ee                                                    # noqa: E402
import pandas as pd                                          # noqa: E402
from config import HAZARDS, HAZARD_TOPICS, MHC_EXCLUDED_TOPICS  # noqa: E402
from gee_core import (INFRA_FOLDER, initialize_gee,          # noqa: E402
                      _exposed_for_hazard, build_core_images)
from _common import LAYER_SCHOOLS, asset_stem, out_dir       # noqa: E402

DEFAULT_ISO3 = ["ken", "som", "mdg", "tza", "ssd", "sdn", "dji"]
DEFAULT_SOURCES = ["giga", "mwater"]
DEFAULT_OUT =os.path.join(HERE, "out", "schools_multihazard")
EXCLUDED = {"Pixel Based Hazard Score"}
# Points take the value of the native pixel they fall in only if the sampling grid is finer than every hazard.
SAMPLE_SCALE = 30
CHUNK = 2000

SUB_HAZARDS = [h for h in HAZARDS if h["name"] not in EXCLUDED]
TOPICS = list(HAZARD_TOPICS)


def hazard_stack():
    raw = build_core_images()["raw_hazard"]
    one = ee.Image.constant(1)
    bands = []
    for i, h in enumerate(SUB_HAZARDS):
        bands.append(_exposed_for_hazard(h, one).mask().unmask(0).rename(f"f{i}"))
        bands.append(raw[h["name"]].rename(f"v{i}"))
    return ee.Image.cat(bands)


def load_schools(iso3, source, existing):
    """GEE asset if uploaded, else the local prep_infra.py CSV; None if neither."""
    stem = asset_stem(iso3, source, LAYER_SCHOOLS)
    if stem in existing:
        df = ee.data.computeFeatures({"expression": ee.FeatureCollection(f"{INFRA_FOLDER}/{stem}"),
                                      "fileFormat": "GEOPANDAS_GEODATAFRAME"})
        df["longitude"] = df.geometry.x
        df["latitude"] = df.geometry.y
        origin = "GEE"
    else:
        path = os.path.join(out_dir(iso3), f"{stem}.csv")
        if not os.path.exists(path):
            return None, None
        df = pd.read_csv(path)
        origin = "local CSV"
    df = df.dropna(subset=["longitude", "latitude"])
    keep = [c for c in ("facility_id", "name", "education_level") if c in df.columns]
    return pd.DataFrame(df[keep + ["longitude", "latitude"]]).reset_index(drop=True), origin


def sample(schools, stack):
    parts = []
    for start in range(0, len(schools), CHUNK):
        chunk = schools.iloc[start:start + CHUNK]
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([lon, lat]), {"row": int(idx)})
            for idx, lon, lat in zip(chunk.index, chunk.longitude, chunk.latitude)])
        sampled = stack.reduceRegions(collection=fc, reducer=ee.Reducer.first(),
                                      scale=SAMPLE_SCALE, tileScale=4)
        parts.append(ee.data.computeFeatures({"expression": sampled,
                                              "fileFormat": "PANDAS_DATAFRAME"}))
        print(f"    sampled {min(start + CHUNK, len(schools)):,}/{len(schools):,}")
    s = pd.concat(parts).drop(columns=["geo"], errors="ignore").set_index("row").sort_index()

    out = schools.copy()
    for t in TOPICS:
        cols = [f"f{i}" for i, h in enumerate(SUB_HAZARDS) if h["name"] in HAZARD_TOPICS[t]]
        out[t] = s[cols].fillna(0).max(axis=1).astype(int) if cols else 0
    out["n_hazards"] = out[TOPICS].sum(axis=1)
    out["n_climate_hazards"] = out[[t for t in TOPICS if t not in MHC_EXCLUDED_TOPICS]].sum(axis=1)
    out["hazards"] = out[TOPICS].apply(lambda r: "; ".join(t for t in TOPICS if r[t]), axis=1)
    for i, h in enumerate(SUB_HAZARDS):
        out[h["name"]] = s.get(f"v{i}")
    return out


def summarise(iso3, source, df):
    n = len(df)
    row = {"iso3": iso3.upper(), "source": source, "schools": n}
    for col in ("n_hazards", "n_climate_hazards"):
        label = col[2:]
        for k in (1, 2, 3):
            c = int((df[col] >= k).sum())
            row[f"in_{k}plus_{label}"] = c
            row[f"in_{k}plus_{label}_pct"] = round(100 * c / n, 2) if n else None
    for t in TOPICS:
        row[t] = int(df[t].sum())
    dist = df.n_hazards.value_counts().sort_index()
    dist_row = {"iso3": iso3.upper(), "source": source,
                **{f"n_{k}": int(v) for k, v in dist.items()}}
    m = df[TOPICS].astype(int)
    overlap = m.T.dot(m)
    return row, dist_row, overlap


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--iso3", action="append", help=f"repeatable (default: {' '.join(DEFAULT_ISO3)})")
    ap.add_argument("--source", action="append",
                    help="schools source, repeatable (default: giga mwater)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    initialize_gee()
    os.makedirs(args.out, exist_ok=True)
    existing = {a["id"].split("/")[-1] for a in ee.data.listAssets({"parent": INFRA_FOLDER})["assets"]}
    stack = hazard_stack()

    summary, dists, missing = [], [], []
    for iso3 in [c.lower() for c in (args.iso3 or DEFAULT_ISO3)]:
        for source in args.source or DEFAULT_SOURCES:
            stem = asset_stem(iso3, source, LAYER_SCHOOLS)
            schools, origin = load_schools(iso3, source, existing)
            if schools is None or schools.empty:
                print(f"[{iso3.upper()}] no {stem} in GEE or locally — skipped")
                missing.append(stem)
                continue
            print(f"[{iso3.upper()}] {stem} ({origin})")
            df = sample(schools, stack)
            row, dist_row, overlap = summarise(iso3, source, df)
            df.to_csv(os.path.join(args.out, f"{stem}_hazards.csv"), index=False)
            overlap.to_csv(os.path.join(args.out, f"{iso3}_{source}_topic_overlap.csv"))
            summary.append(row)
            dists.append(dist_row)
            print(f"    {row['schools']:,} schools | >=1 hazard {row['in_1plus_hazards']:,} "
                  f"| >=2 {row['in_2plus_hazards']:,} | >=3 {row['in_3plus_hazards']:,}")

    if summary:
        pd.DataFrame(summary).to_csv(os.path.join(args.out, "summary.csv"), index=False)
        d = pd.DataFrame(dists).fillna(0)
        ncols = sorted((c for c in d.columns if c.startswith("n_")), key=lambda c: int(c[2:]))
        d[ncols] = d[ncols].astype(int)
        d[["iso3", "source"] + ncols].to_csv(
            os.path.join(args.out, "hazard_count_distribution.csv"), index=False)
    print(f"\nWrote {args.out}")
    if missing:
        print(f"Skipped (no data): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
