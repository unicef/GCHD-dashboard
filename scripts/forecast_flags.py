"""Manage the runtime enable/disable table for Forecast-tab datasets.

The app reads a small GEE FeatureCollection at startup and every 10 minutes, so
toggling a dataset here takes effect on the running server with no code change,
no redeploy and no restart — the same principle as the infrastructure asset
discovery described in this folder's README.

The table is OPTIONAL. With no asset present the app falls back to the `active`
flags in app/forecast_config.py, which is a fully supported configuration.

Only dataset names that exist in forecast_config are honoured; anything else is
ignored with a warning, so this table can never surface a dataset that has no
verified code path behind it.

Usage
-----
    python forecast_flags.py list                  # effective state, config vs table
    python forecast_flags.py disable s5p_no2 s5p_co
    python forecast_flags.py enable  s5p_no2
    python forecast_flags.py sync                  # write the config defaults
    python forecast_flags.py delete                # remove the table entirely
    python forecast_flags.py csv                   # write forecast_datasets.csv

After any write the app picks up the change within its 10-minute cache window.

`csv` regenerates the manual-upload file next to this script — useful for
seeding the table through the Code Editor's CSV import instead of an Export
task. It has no geometry columns, so import it as a geometry-less table.
"""
import csv as _csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "app"))

import ee

from forecast_config import FORECAST_DATASETS, FORECAST_MAP

KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "app", "credentials", "service_account.json")

ASSET = os.environ.get("GCHD_FORECAST_CONFIG_ASSET",
                       "projects/unicef-ccri/assets/config/forecast_datasets")


def init():
    with open(KEY_PATH) as f:
        info = json.load(f)
    ee.Initialize(
        credentials=ee.ServiceAccountCredentials(email=info["client_email"],
                                                 key_file=KEY_PATH),
        project="unicef-ccri")


def read_table():
    """{name: bool} currently in the asset, or None if there is no asset."""
    try:
        rows = ee.FeatureCollection(ASSET).getInfo().get("features", [])
    except Exception:
        return None
    out = {}
    for f in rows:
        p = f.get("properties") or {}
        if p.get("name") is not None and "active" in p:
            out[str(p["name"])] = bool(p["active"])
    return out


def write_table(flags):
    """Replace the asset with one row per dataset.

    Every dataset is written, not just the overridden ones, so the table is a
    readable picture of the whole catalog rather than a sparse diff.
    """
    feats = []
    for i, d in enumerate(FORECAST_DATASETS):
        name = d["name"]
        # Export.table.toAsset REFUSES null-geometry features ("Unable to export
        # features with null geometry"), even though a manual CSV upload accepts
        # them happily. Give each row a throwaway point; the app reads only
        # properties and never looks at geometry.
        feats.append(ee.Feature(ee.Geometry.Point([0, 0]), {
            "name": name,
            "active": 1 if flags.get(name, d.get("active", True)) else 0,
            # Context columns so the table is self-explanatory in the Code
            # Editor — the app reads only `name` and `active`.
            "label": d.get("label", ""),
            "kind": d.get("kind", ""),
            "topic": d.get("topic", ""),
            "row": i,
        }))
    fc = ee.FeatureCollection(feats)

    parent = ASSET.rsplit("/", 1)[0]
    try:
        ee.data.createAsset({"type": "FOLDER"}, parent)
        print(f"created folder {parent}")
    except Exception:
        pass                                    # already exists — fine

    # Write to a staging asset FIRST, then swap. An earlier version deleted the
    # live asset up front and started the export afterwards — when the export
    # failed, the table was simply gone. Never destroy the current state until
    # the replacement exists.
    staging = ASSET + "_staging"
    try:
        ee.data.deleteAsset(staging)
    except Exception:
        pass                                    # no leftover staging — fine

    task = ee.batch.Export.table.toAsset(
        collection=fc, description="forecast_flags", assetId=staging)
    task.start()
    print(f"export task {task.id} -> {staging}")

    print("waiting for the export to finish…")
    for _ in range(60):                          # up to ~5 min
        time.sleep(5)
        st = task.status()
        state = st.get("state")
        if state == "COMPLETED":
            break
        if state in ("FAILED", "CANCELLED"):
            print(f"EXPORT {state}: {st.get('error_message', '')}")
            print(f"The live table at {ASSET} was NOT touched.")
            sys.exit(1)
        print(f"   … {state}")
    else:
        print("Timed out waiting. Check the Tasks tab; the live table is intact.")
        sys.exit(1)

    # Export succeeded — now it is safe to replace the live asset.
    try:
        ee.data.deleteAsset(ASSET)
    except Exception:
        pass                                    # first write — fine
    ee.data.renameAsset(staging, ASSET)
    print(f"wrote {len(feats)} rows -> {ASSET}")
    print("The app reflects it within 10 minutes (TTL cache).")


CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "forecast_datasets.csv")


def cmd_csv():
    """Write the manual-upload CSV from the current config defaults.

    Kept alongside this script rather than in app/: it is an upload artifact for
    seeding the GEE table, not something the running app ever reads.
    """
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["name", "active", "label", "kind", "topic"])
        for d in FORECAST_DATASETS:
            w.writerow([d["name"], 1 if d.get("active", True) else 0,
                        d.get("label", ""), d.get("kind", ""),
                        d.get("topic", "")])
    print(f"wrote {CSV_PATH}")
    print(f"upload to: {ASSET}")
    print("No lat/lon columns — import as a geometry-less table.")


def cmd_list():
    table = read_table()
    print(f"asset: {ASSET}")
    print("table: " + ("(absent — using config defaults)" if table is None
                       else f"{len(table)} row(s)"))
    print()
    print(f"{'dataset':18s} {'config':>7s} {'table':>7s} {'effective':>10s}  label")
    print("-" * 78)
    for d in FORECAST_DATASETS:
        n = d["name"]
        cfg = d.get("active", True)
        tbl = (table or {}).get(n)
        eff = cfg if tbl is None else tbl
        mark = "" if (tbl is None or tbl == cfg) else "  <- override"
        print(f"{n:18s} {str(cfg):>7s} "
              f"{('-' if tbl is None else str(tbl)):>7s} "
              f"{str(eff):>10s}  {d['label']}{mark}")


def cmd_set(names, value):
    unknown = [n for n in names if n not in FORECAST_MAP]
    if unknown:
        print(f"ERROR: unknown dataset(s): {unknown}")
        print(f"known: {sorted(FORECAST_MAP)}")
        sys.exit(1)
    flags = read_table()
    if flags is None:
        flags = {d["name"]: d.get("active", True) for d in FORECAST_DATASETS}
    for n in names:
        flags[n] = value
    write_table(flags)
    print(f"{'enabled' if value else 'disabled'}: {', '.join(names)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]

    # `csv` reads config only — no GEE round-trip needed.
    if cmd == "csv":
        cmd_csv()
        return

    init()

    if cmd == "list":
        cmd_list()
    elif cmd in ("enable", "disable"):
        if not args:
            print(f"usage: forecast_flags.py {cmd} <name> [name...]")
            sys.exit(1)
        cmd_set(args, cmd == "enable")
    elif cmd == "sync":
        write_table({d["name"]: d.get("active", True) for d in FORECAST_DATASETS})
        print("table written from config defaults")
    elif cmd == "delete":
        try:
            ee.data.deleteAsset(ASSET)
            print(f"deleted {ASSET} — app falls back to config defaults")
        except Exception as e:
            print(f"could not delete: {str(e)[:150]}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
