#!/usr/bin/env python
"""
clip_anomalies.py — clip the SEAS5 anomaly rasters to one or more countries.

Country offices ask for "the anomaly for my country", and a global 1 degree
raster is an awkward thing to hand over: they have to know which cells are
theirs, and a careless crop to a bounding box pulls in neighbours. This cuts
each anomaly to a country's own boundary, writes it with the units and the
aggregation embedded, and drops a README beside it.

Boundaries come from the project's GEE adm0 asset — the same source the
dashboard and the exposure workbook use — so a clipped raster and a published
figure describe the same territory. Selection is by UCODE or by name, never by
ISO3 alone: 14 ISO3 codes are shared by 2-9 adm0 units, so an ISO3 match can
silently merge a disputed territory into the state it is disputed with.

THE GRID IS NOT REPROJECTED OR RESAMPLED. The forecast is 1 degree (~111 km);
a country smaller than a few cells will come out as a handful of pixels, which
is the honest representation. Resampling to look smoother would imply detail
the forecast does not have.

Run with the el_nino interpreter (it has rasterio):
    scripts/el_nino/.venv/Scripts/python scripts/el_nino/clip_anomalies.py \
        --country Cambodia

Usage:
    --country NAME        repeatable; matched against the adm0 `name` field
    --ucode CODE          repeatable; exact UCODE, the unambiguous form
    --buffer DEG          pad the clip box (default 0.5, i.e. half a cell)
    --all-touched         keep any cell the boundary touches (default on;
                          --no-all-touched keeps only cell centres inside)
    --src DIR             where the global anomaly tifs are
    --out DIR             output root; one subdirectory per country
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

# A machine-wide PROJ_LIB/GDAL_DATA (PostgreSQL/PostGIS sets both on Windows)
# points PROJ at a PROJ-6 era proj.db, which rasterio 1.4 / PROJ 9 rejects:
#     proj_create_from_database: ... DATABASE.LAYOUT.VERSION.MINOR = 2
#                                    whereas a number >= 5 is expected
# EPSG lookups then fail and the CRS degrades to a bare WGS 84 string with no
# AUTHORITY code. Drop the inherited values so the wheels' own bundled data
# wins. Must happen before rasterio imports. Same guard as
# download_ecmwf_prec.py — see its header for the full story.
for _stale in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA"):
    if os.environ.get(_stale) and ".venv" not in os.environ[_stale]:
        os.environ.pop(_stale)

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(APP))

ADM0 = "projects/unicef-ccri/assets/global_boundary/adm0"
GEE_PROJECT = "unicef-ccri"

SRC_DEFAULT = HERE / "data" / "output" / "anomalies"
OUT_DEFAULT = HERE / "data" / "output" / "country"

# Filename stem -> (pretty label, units, how the period aggregates).
# Read from the source files' own tags where present; this is the fallback and
# the single place the sum-vs-mean distinction is spelled out for the README.
AGG = {
    "rainfall": "SUM over the months in the period (a seasonal TOTAL)",
    "temperature": "MEAN over the months in the period (an AVERAGE, not a total)",
}


def init_ee():
    """Service account if available, else a personal login."""
    import ee
    key = APP / "credentials" / "service_account.json"
    try:
        if key.exists():
            info = json.loads(key.read_text())
            ee.Initialize(
                credentials=ee.ServiceAccountCredentials(
                    email=info["client_email"], key_file=str(key)),
                project=GEE_PROJECT)
        else:
            ee.Initialize(project=GEE_PROJECT)
    except Exception as e:
        sys.exit(f"ERROR: could not initialise Earth Engine: {str(e)[:160]}")
    return ee


def resolve_units(ee, names, ucodes):
    """-> [(ucode, name, geojson geometry)] for every requested unit.

    Name matching is case-insensitive and exact. A name that matches more than
    one adm0 unit is an error rather than a guess: that is precisely the
    disputed-territory case, and picking one silently would misattribute a
    figure to the wrong authority.
    """
    fc = ee.FeatureCollection(ADM0)
    out = []

    for uc in ucodes:
        f = fc.filter(ee.Filter.eq("ucode", uc))
        n = f.size().getInfo()
        if n == 0:
            sys.exit(f"ERROR: no adm0 unit with ucode '{uc}'.")
        feat = f.first().getInfo()
        out.append((uc, feat["properties"].get("name") or uc,
                    feat["geometry"]))

    for nm in names:
        # Case-insensitive exact match on the name field.
        f = fc.filter(ee.Filter.eq("name", nm))
        n = f.size().getInfo()
        if n == 0:
            # Try a case-insensitive pass before giving up, and show near
            # misses so a typo is easy to fix.
            allnames = fc.aggregate_array("name").getInfo()
            hit = [a for a in allnames if (a or "").lower() == nm.lower()]
            if len(hit) == 1:
                f = fc.filter(ee.Filter.eq("name", hit[0]))
                n = 1
            else:
                near = sorted(a for a in allnames
                              if a and nm.lower() in a.lower())[:8]
                sys.exit(f"ERROR: no adm0 unit named '{nm}'."
                         + (f"\n       Did you mean: {', '.join(near)}?"
                            if near else ""))
        if n > 1:
            ucs = f.aggregate_array("ucode").getInfo()
            sys.exit(f"ERROR: '{nm}' matches {n} adm0 units ({', '.join(ucs)}).\n"
                     f"       Pass --ucode to choose one; an ISO3 or name can "
                     f"cover several units.")
        feat = f.first().getInfo()
        out.append((feat["properties"].get("ucode"),
                    feat["properties"].get("name") or nm, feat["geometry"]))
    return out


def clip_one(src_path, polys, bounds, buffer_deg, all_touched, dst_path):
    """Write the part of `src_path` inside `polys`, cropped to its bounds.

    Two steps, and both matter. The WINDOW crops to the bounding box so the
    file is small; the MASK then blanks cells outside the actual boundary, so a
    coastal or oddly-shaped country does not ship its neighbours' values.
    """
    with rasterio.open(src_path) as src:
        minx, miny, maxx, maxy = bounds
        # round_offsets()/round_lengths() alone SHRINK the window and can drop
        # the outermost cell a country only partly occupies: Cambodia reaches
        # 107.63E and 9.91N, so it genuinely occupies the 107-108 and 9-10
        # cells, and both were being cut before the mask ever ran — a visible
        # missing column on the eastern edge. floor/ceil the offsets and
        # lengths outward instead, so every cell the bounds touch is read.
        win = from_bounds(minx - buffer_deg, miny - buffer_deg,
                          maxx + buffer_deg, maxy + buffer_deg,
                          src.transform)
        win = rasterio.windows.Window(
            col_off=math.floor(win.col_off), row_off=math.floor(win.row_off),
            width=math.ceil(win.col_off + win.width) - math.floor(win.col_off),
            height=math.ceil(win.row_off + win.height)
                   - math.floor(win.row_off))
        # Clamp to the raster: a country touching the antimeridian or a pole
        # would otherwise ask for a window outside the array.
        win = win.intersection(
            rasterio.windows.Window(0, 0, src.width, src.height))
        if win.width < 1 or win.height < 1:
            return None

        data = src.read(1, window=win)
        transform = src.window_transform(win)
        nodata = src.nodata if src.nodata is not None else -9999.0

        mask = geometry_mask(polys, out_shape=data.shape,
                             transform=transform, invert=True,
                             all_touched=all_touched)
        data = np.where(mask, data, nodata).astype("float32")

        if not np.any(data != nodata):
            return None       # nothing of this country on the forecast grid

        # Shrink to the cells that actually carry data. A country straddling
        # the antimeridian has a bounding box spanning the whole globe — Fiji
        # came out 360x10 for 26 valid cells — because its longitudes run
        # 176E to 178W. The file was correct but absurd, and a nearly empty
        # global-width raster looks broken in a GIS. Cropping to the data
        # keeps the geotransform honest for each piece.
        rows = np.where(np.any(data != nodata, axis=1))[0]
        cols = np.where(np.any(data != nodata, axis=0))[0]
        # Only tighten longitude when the data really is split across the
        # seam: for an ordinary country this is a no-op.
        split = len(cols) > 1 and np.any(np.diff(cols) > 1)
        if split:
            # Keep the WIDER cluster and note the loss, rather than silently
            # writing two halves under one filename.
            groups = np.split(cols, np.where(np.diff(cols) > 1)[0] + 1)
            groups.sort(key=len, reverse=True)
            kept, dropped = groups[0], sum(len(g) for g in groups[1:])
            cols = kept
        else:
            dropped = 0
        data = data[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
        transform = src.window_transform(
            rasterio.windows.Window(
                win.col_off + int(cols.min()), win.row_off + int(rows.min()),
                int(cols.max() - cols.min() + 1),
                int(rows.max() - rows.min() + 1)))

        prof = src.profile.copy()
        prof.update(height=data.shape[0], width=data.shape[1],
                    transform=transform, count=1, dtype="float32",
                    nodata=nodata, compress="DEFLATE", driver="GTiff",
                    # Write an explicit EPSG:4326. The source files carry a
                    # bare WGS 84 definition with no AUTHORITY code — a relic
                    # of the PROJ/GDAL version that wrote them — so
                    # crs.to_epsg() returns None and some GIS tools then
                    # refuse to reproject or overlay the layer.
                    crs=rasterio.crs.CRS.from_epsg(4326))
        tags = src.tags()
        desc = src.descriptions[0]

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **prof) as dst:
        dst.write(data, 1)
        if desc:
            dst.set_band_description(1, desc)
        if tags:
            dst.update_tags(**tags)

    valid = data[data != nodata]
    return {"path": dst_path, "cells": int(valid.size),
            "min": float(valid.min()), "max": float(valid.max()),
            "mean": float(valid.mean()), "dropped": dropped,
            "shape": f"{data.shape[1]}x{data.shape[0]}"}


def write_readme(out_dir, name, ucode, results, buffer_deg, all_touched):
    """A README per country, carrying the one thing that gets misread."""
    lines = [
        f"SEAS5 seasonal forecast anomalies — {name}",
        "=" * (44 + len(name)),
        "",
        f"Clipped from the global 1 degree anomaly rasters to the {name}",
        f"boundary (UCODE {ucode}) from the UNICEF adm0 boundary set — the same",
        "boundaries used for the published exposure figures, so these rasters and",
        "those numbers describe the same territory.",
        "",
        "FILES",
        "-----",
    ]
    for r in results:
        lines.append(f"{r['path'].name:46} {r['shape']:>9} cells")
    lines += [
        "",
        "THE ONE THING TO KNOW",
        "---------------------",
        "The two variables aggregate DIFFERENTLY over the three-month period:",
        "",
        "  Rainfall     the OND file is the SUM of the three months — a seasonal",
        "               TOTAL departure, in mm.",
        "  Temperature  the OND file is the MEAN of the three months — an AVERAGE",
        "               departure, in degrees C. Summing monthly temperature",
        "               anomalies is meaningless.",
        "",
        "Monthly files are included for both. If the timing within the season",
        "matters, use them: a seasonal mean can hide a trend across the months.",
        "",
        "VALUES IN THESE FILES",
        "---------------------",
    ]
    for r in results:
        lines.append(f"  {r['path'].name}")
        lines.append(f"     {r['cells']} valid cells, "
                     f"min {r['min']:+.2f}, mean {r['mean']:+.2f}, "
                     f"max {r['max']:+.2f}")
    lines += [
        "",
        "WHAT AN ANOMALY MEANS HERE",
        "--------------------------",
        "Anomaly = the forecast ensemble mean minus the model's own 1993-2016",
        "climatology for the same place and period. So it is relative to THIS",
        "MODEL's normal, not an observed normal — which is what cancels the",
        "model's biases. It answers 'how far from normal', not 'what will the",
        "temperature be'.",
        "",
        "The baseline is three decades old. Warming since then means positive",
        "temperature anomalies are widespread and should not by themselves be",
        "read as an extreme signal.",
        "",
        "An anomaly says HOW FAR from normal, not HOW LIKELY. These files carry",
        "no ensemble-agreement information: a large anomaly only a few of the 51",
        "forecast runs support looks identical here to one they all agree on.",
        "",
        "LIMITATIONS",
        "-----------",
    ]
    if any(r.get("dropped") for r in results):
        lines += [
            "- THIS COUNTRY SPANS THE ANTIMERIDIAN (180 degrees longitude).",
            "  Its territory falls on both sides of the date line, and a",
            "  single raster cannot cover both without stretching across the",
            "  whole globe. These files hold the LARGER side only; cells on",
            "  the far side are not included. For a complete picture use the",
            "  global rasters, or request the two sides separately.",
        ]
    lines += [
        "- The forecast grid is 1 degree (~111 km). These files are NOT",
        "  resampled, so a small country is a handful of cells. That is the",
        "  honest resolution; do not interpolate it to imply more detail.",
        f"- Cells were kept where the boundary {'touches them' if all_touched else 'contains their centre'}",
        f"  (buffer {buffer_deg} degrees applied to the crop box).",
        "- A single forecast issue, not a monitoring product. Initialised",
        "  September 2026 and superseded by later initialisations.",
        "- Forecast reliability varies by region and is not included here.",
        "- An anomaly is not an impact.",
        "",
        "Source: ECMWF SEAS5 system 51 via Copernicus CDS.",
        "Full method: scripts/el_nino/docs/methodological_note_v2.md",
        "Prepared by UNICEF, Global Child Hazard Database team.",
        "",
    ]
    (out_dir / "README.txt").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", action="append", default=[],
                    help="adm0 name, repeatable")
    ap.add_argument("--ucode", action="append", default=[],
                    help="adm0 UCODE, repeatable — unambiguous")
    ap.add_argument("--buffer", type=float, default=0.5,
                    help="degrees of padding on the crop box (default 0.5, "
                         "half a forecast cell)")
    ap.add_argument("--no-all-touched", action="store_true",
                    help="keep only cells whose CENTRE is inside the boundary "
                         "(default keeps any cell the boundary touches, which "
                         "suits a 1 degree grid and small countries)")
    ap.add_argument("--src", default=str(SRC_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    if not args.country and not args.ucode:
        sys.exit("ERROR: pass at least one --country or --ucode.")

    src_dir = Path(args.src)
    tifs = sorted(src_dir.glob("*anomaly*.tif"))
    if not tifs:
        sys.exit(f"ERROR: no anomaly rasters in {src_dir}.\n"
                 f"       Expected the global files written alongside "
                 f"README.txt there.")

    print(f"Source : {src_dir}  ({len(tifs)} rasters)")
    ee = init_ee()
    units = resolve_units(ee, args.country, args.ucode)
    print(f"Clipping to {len(units)} unit(s): "
          f"{', '.join(n for _, n, _ in units)}\n")

    all_touched = not args.no_all_touched
    for ucode, name, geom in units:
        polys = polygons_of(geom)
        if not polys:
            print(f"  {name}: boundary has no polygon parts — skipped.")
            continue
        # Bounds from the geometry itself, so no extra GEE round trip.
        coords = np.array(
            [c for poly in _rings(geom) for c in poly], dtype="float64")
        bounds = (coords[:, 0].min(), coords[:, 1].min(),
                  coords[:, 0].max(), coords[:, 1].max())

        safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
        out_dir = Path(args.out) / safe
        results = []
        for t in tifs:
            r = clip_one(t, polys, bounds, args.buffer, all_touched,
                         out_dir / f"{safe}_{t.name}")
            if r:
                results.append(r)
            else:
                print(f"  (no data for {name} in {t.name})")

        if not results:
            print(f"  {name}: nothing written — the country may be smaller "
                  f"than one forecast cell at this buffer.")
            continue

        write_readme(out_dir, name, ucode, results, args.buffer, all_touched)
        print(f"  {name} ({ucode}) -> {out_dir}")
        for r in results:
            print(f"     {r['path'].name:46} {r['shape']:>8}  "
                  f"{r['cells']:>4} cells  mean {r['mean']:+.2f}"
                  + (f"   [{r['dropped']} cell-columns across the "
                     f"antimeridian NOT included]" if r.get("dropped")
                     else ""))
        if any(r.get("dropped") for r in results):
            print(f"     WARNING: {name} spans the antimeridian. Only the "
                  f"larger side is written — a single raster cannot hold "
                  f"both without a 360-degree extent. Use the global files "
                  f"for a complete picture.")
        print(f"     README.txt")


def polygons_of(geom):
    """Every Polygon in a geometry, as a list of GeoJSON Polygons.

    The adm0 features are GeometryCollections — Cambodia has 48 parts — and
    they mix Polygons with LineStrings (boundary lines). Only the Polygons
    describe area, so the LineStrings are dropped: passing them to
    geometry_mask would contribute nothing to the fill and, worse, a
    collection containing them is not a valid mask input at all.
    """
    t = geom.get("type")
    if t == "Polygon":
        return [geom]
    if t == "MultiPolygon":
        return [{"type": "Polygon", "coordinates": c}
                for c in geom["coordinates"]]
    if t == "GeometryCollection":
        out = []
        for g in geom.get("geometries", []):
            out += polygons_of(g)
        return out
    if t in ("LineString", "MultiLineString", "Point", "MultiPoint"):
        return []                      # no area to contribute
    raise SystemExit(f"ERROR: unsupported geometry type {t}.")


def _rings(geom):
    """Flat list of coordinate rings, for computing bounds."""
    return [ring for poly in polygons_of(geom)
            for ring in poly["coordinates"]]


if __name__ == "__main__":
    main()
