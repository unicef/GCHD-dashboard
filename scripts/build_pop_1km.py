#!/usr/bin/env python
"""
build_pop_1km.py — aggregate the WorldPop 100 m child-population grids to 1 km.

Why
---
The Observed and Forecast layers are coarse: GFS is ~28 km, CAMS ~44 km, IMERG
~11 km, ERA5-Land ~11 km. Reducing those against a 100 m population grid costs
~100x the pixels for no extra precision — the hazard value is constant across
every one of those 100 m cells. Aggregating the population once, offline, makes
every forecast exposure run cheaper without changing what is being measured.

The static hazard layers are NOT affected: they stay on the 100 m grid, which is
what keeps the Analysis tab's numbers comparable with the published CCRR
figures. Only forecast_core reads the 1 km assets.

Aggregation
-----------
SUM, not mean. These pixels are counts of children, so a 10x10 block of 100 m
cells must add up to the 1 km cell that contains them. A mean would silently
divide every total by ~100 and still look plausible.

`reduceResolution` caps out at 256 input pixels per output pixel; 10x10 = 100 is
within that. The source mosaic is reprojected to an explicit 1 km grid so the
output is deterministic rather than dependent on the request's zoom level.

Usage
-----
    python scripts/build_pop_1km.py --dry-run        # print what would run
    python scripts/build_pop_1km.py                  # export all three classes
    python scripts/build_pop_1km.py --class T_U18    # just one
    python scripts/build_pop_1km.py --status         # poll running exports

Exports are GEE batch tasks writing to
    projects/unicef-ccri/assets/population/worldpop_{class}_2025_CN_1km
They take a while (tens of minutes); --status polls without resubmitting.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "app"))

import ee                                              # noqa: E402
from gee_core import initialize_gee                    # noqa: E402
from config import POP_ASSET_TMPL                      # noqa: E402

# Only the under-18 classes: those are the ones forecast exposure reduces over.
# The all-ages grids stay 100 m — nothing reads them at forecast scale.
CLASSES = ["T_U18", "T_M_U18", "T_F_U18"]

DEST_TMPL = "projects/unicef-ccri/assets/population/worldpop_{}_2025_CN_1km"
SOURCE_SCALE = 100
TARGET_SCALE = 1000


def build_100m_image(pop_class):
    """The source mosaic on an explicit 100 m EPSG:4326 grid.

    setDefaultProjection is required twice over: mosaic() drops the projection,
    and reduceResolution refuses to run without one. The scale is stated as a
    round 100 rather than the asset's native ~92.77 m — the aggregation ratio is
    taken from this declared grid, and a fractional ratio skews the totals.
    """
    return (ee.ImageCollection(POP_ASSET_TMPL.format(pop_class))
            .mosaic().select(0).rename("population")
            # Population is zero where nobody lives, not unknown; a masked
            # input pixel would otherwise void the whole 1 km cell containing it.
            .unmask(0)
            .setDefaultProjection(crs="EPSG:4326", scale=SOURCE_SCALE))


def build_1km_image(pop_class):
    """Sum-aggregate one 100 m population class to a 1 km image.

    SUM, not mean: these pixels are counts of children, so the 1 km cell must be
    the total of the ~100 source cells inside it.
    """
    return (build_100m_image(pop_class)
            .reduceResolution(reducer=ee.Reducer.sum(), maxPixels=200)
            .reproject(crs="EPSG:4326", scale=TARGET_SCALE))


def export_one(pop_class, dry_run=False):
    dest = DEST_TMPL.format(pop_class)
    desc = f"pop1km_{pop_class}"
    if dry_run:
        print(f"  would export {POP_ASSET_TMPL.format(pop_class)}")
        print(f"            -> {dest}")
        return None

    task = ee.batch.Export.image.toAsset(
        image=build_1km_image(pop_class),
        description=desc,
        assetId=dest,
        scale=TARGET_SCALE,
        crs="EPSG:4326",
        maxPixels=1e13,
        # Overview pyramids only — GEE accepts MEAN/MODE/MIN/MAX/SAMPLE here,
        # not SUM. Analyses read the full-resolution band, so this affects
        # zoomed-out display alone.
        pyramidingPolicy={".default": "MEAN"},
    )
    task.start()
    print(f"  started {desc} -> {dest}")
    return task


def check_totals(pop_class, ucode="MWI_V1", exported=True):
    """Compare the 100 m and 1 km totals over one region.

    BOTH images are measured at the SOURCE scale. reduceRegion(sum) samples one
    value per requested pixel rather than integrating over area, so measuring
    the 1 km image at scale=1000 samples one cell per square kilometre and
    undercounts by roughly the aggregation ratio — the totals look ~99% wrong
    while the asset is perfectly fine. This is the single easiest way to
    convince yourself a correct aggregation is broken.
    """
    adm1 = ee.FeatureCollection("projects/unicef-ccri/assets/global_boundary/adm1")
    geom = adm1.filter(ee.Filter.eq("adm0_ucode", ucode)).first().geometry()

    src = build_100m_image(pop_class)
    dst = (ee.Image(DEST_TMPL.format(pop_class)) if exported
           else build_1km_image(pop_class))

    kw = dict(geometry=geom, scale=SOURCE_SCALE, maxPixels=1e13, bestEffort=True)
    va = list(src.reduceRegion(ee.Reducer.sum(), **kw).getInfo().values())[0] or 0
    vb = list(dst.reduceRegion(ee.Reducer.sum(), **kw).getInfo().values())[0] or 0
    diff = (vb - va) / va * 100 if va else float("nan")
    flag = "ok" if abs(diff) < 2 else "CHECK"
    print(f"  {pop_class:9} {ucode:8} 100 m = {va:>12,.0f} | "
          f"1 km = {vb:>12,.0f} | {diff:+.2f}%  {flag}")


def status():
    for t in ee.batch.Task.list()[:25]:
        st = t.status()
        if st.get("description", "").startswith("pop1km_"):
            line = f"  {st['description']:16} {st['state']}"
            if st.get("error_message"):
                line += f"  — {st['error_message']}"
            print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class", dest="pop_class", choices=CLASSES,
                    help="only this population class (default: all three)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the asset ids without starting exports")
    ap.add_argument("--status", action="store_true",
                    help="poll running pop1km_* export tasks")
    ap.add_argument("--check", action="store_true",
                    help="compare 100 m vs 1 km totals (run after exports finish)")
    ap.add_argument("--check-live", action="store_true",
                    help="same comparison against the on-the-fly aggregation, "
                         "before anything is exported")
    args = ap.parse_args()

    initialize_gee()
    classes = [args.pop_class] if args.pop_class else CLASSES

    if args.status:
        print("Export tasks:")
        status()
        return 0
    if args.check or args.check_live:
        # Three latitudes: EPSG:4326 pixel area varies with latitude, so a
        # single mid-latitude test would not catch a projection error.
        print("1 km vs 100 m totals — expect within ~1% (1 km cells cannot "
              "follow a boundary as tightly):")
        for c in classes:
            for uc in ("MWI_V1", "NOR_V1", "ECU_V1"):
                check_totals(c, uc, exported=args.check)
        return 0

    print(f"Aggregating {len(classes)} class(es) to {TARGET_SCALE} m (sum):")
    for c in classes:
        export_one(c, args.dry_run)
    if not args.dry_run:
        print("\nExports queued. Poll with:  python scripts/build_pop_1km.py --status")
        print("When they finish:           python scripts/build_pop_1km.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
