#!/usr/bin/env python
"""
export_exposure_table.py — children in dry / wet signal areas, every adm0, to Excel.

One row per country, with the exposed child population at several probability
thresholds for BOTH signals, so the sensitivity of the figure to that threshold
is visible in the sheet rather than buried in a choice someone made once.

Why several thresholds: SEAS5 has 51 ensemble members, so a tercile probability
can only take values in steps of 1/51 (~2 points). A cell sitting near any
cutoff flips category on essentially arbitrary grounds, and a single column
would imply a precision the ensemble does not have.

Method (identical to the dashboard's El Nino tab, so figures reconcile):
  signal  = p_below >= t AND p_below > p_above      (dry; mirrored for wet)
  exposed = child population summed where that holds
  dry_mask is applied: cells whose climatology is under the arid cutoff are
  excluded, because a "driest third" on a fraction of a millimetre of normal
  rainfall is rounding noise at any threshold.

Runs ONE reduceRegions over the 500 km chunk collection and groups the result
by country, rather than looping 289 countries — the chunking is what keeps a
global 1 km reduction inside Earth Engine's limits.

Requirements:  pip install earthengine-api openpyxl
Usage:
    python scripts/el_nino/export_exposure_table.py --init 202609
    python scripts/el_nino/export_exposure_table.py --init 202609 --period L2_Oct
    python scripts/el_nino/export_exposure_table.py --init 202609 --out my_table.xlsx
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import ee

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(APP))          # reuse the dashboard's own config/core

from elnino_config import (            # noqa: E402
    issue_asset, SIGNALS, SIGNAL_MAP,
    HINDCAST, SYSTEM, to_fraction, parse_issue, window_suffix,
)

# Thresholds reported, in percent. 70 is the agreed operational cutoff for
# "potentially impacted children" after visual inspection of all countries;
# the lower three stay so the sensitivity of that choice is visible rather
# than hidden behind one column.
THRESHOLDS = [40, 50, 60, 70]
HEADLINE_THRESHOLD = 70

ADM0_CHUNKS = "projects/unicef-ccri/assets/global_boundary/adm0_chunked_500km_shp"

# UNICEF programme regions. Carries ucode AND the region fields, so it joins to
# adm0 directly rather than through an ISO3 lookup.
REGIONS_ASSET = "projects/unicef-ccri/assets/global_boundary/admin0_regions_merged"

# Same 1 km under-18 grid the dashboard's forecast and El Nino tabs use, so a
# number here matches one produced in the app. See forecast_core._forecast_pop
# for why the values are rescaled by the pixel-area ratio.
POP_1KM = "projects/unicef-ccri/assets/population/worldpop_T_U18_2025_CN_1km"
POP_SCALE = 1000
POP_NATIVE = 92.76624232772798


# Set from --init in main().
INIT_TAG = INIT_LABEL = None
PERIOD_MAP = {}
WINDOW_PERIOD = window_suffix()

def init_ee():
    """Authenticate as the app's service account, falling back to a personal login."""
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


def child_pop():
    """Under-18 count per 1 km cell, rescaled for reduceRegions.

    reduceRegions(sum) SAMPLES one value per requested pixel rather than
    integrating over area, so reducing a 1 km asset at scale=1000 visits ~116x
    fewer pixels than the 92.77 m native grid and returns ~1/116th of the
    population. Multiplying by (1000/native)^2 restores the total.
    """
    ratio = ee.Number(POP_SCALE).divide(POP_NATIVE).pow(2)
    return ee.Image(POP_1KM).multiply(ratio).rename("pop")


def tercile_image(period):
    """The terciles image with its real band names restored.

    geeup's manifest carries no band names, so Earth Engine stores b1..b8 in
    file order; the names ride along in the `band_names` property.
    """
    asset = issue_asset(INIT_TAG, "terciles", f"terciles_{INIT_TAG}_{period}")
    img = ee.Image(asset)
    names = [b.strip() for b in
             str(ee.Image(asset).get("band_names").getInfo()).split(",")
             if b.strip()]
    return img.rename(names)


def signal_mask(img, sig, pct):
    """1 where the signal holds at `pct` percent, arid cells excluded.

    Re-derived from the raw probability bands, never from the stored `cat`
    band, which is frozen at 50%. The second test (> the opposite tercile)
    stops a cell near the baseline being flagged both dry and wet — and is
    also why a union signal cannot double-count: the components are mutually
    exclusive by construction.
    """
    if sig.get("union_of"):
        parts = [signal_mask(img, SIGNAL_MAP[n], pct) for n in sig["union_of"]]
        out = parts[0]
        for extra in parts[1:]:
            out = out.Or(extra)
        return out
    p = img.select(sig["band"])
    other = img.select(sig["other"])
    return (p.gte(to_fraction(pct))
             .And(p.gt(other))
             .And(img.select("dry_mask").eq(1)))


def build_stack(period):
    """One multi-band image: total child population + every threshold/signal."""
    img = tercile_image(period)
    pop = child_pop()
    bands = [pop.rename("total_children")]
    for sig in SIGNALS:
        for t in THRESHOLDS:
            bands.append(pop.updateMask(signal_mask(img, sig, t))
                            .rename(f"{sig['name']}_{t}"))
    return ee.Image.cat(bands)


def run_export(period):
    stack = build_stack(period)
    chunks = ee.FeatureCollection(ADM0_CHUNKS)

    # Sum per chunk, then add the country keys back so the result can be
    # grouped client-side. Chunks exist because a single global reduction at
    # 1 km over 289 countries exceeds Earth Engine's request limits.
    reduced = stack.reduceRegions(
        collection=chunks, reducer=ee.Reducer.sum(),
        scale=POP_SCALE, tileScale=4,
    ).select(
        ["ISO3", "name", "ucode"] + list(band_names()),
        retainGeometry=False,
    )
    return reduced


def band_names():
    yield "total_children"
    for sig in SIGNALS:
        for t in THRESHOLDS:
            yield f"{sig['name']}_{t}"


def region_lookup():
    """Region and current-ucode lookups from the UNICEF regions asset.

    Returns (by_ucode, by_iso, ucode_fix):
      by_ucode / by_iso  {key: region name}
      ucode_fix          {adm0 ucode: the regions asset's ucode}

    The two assets drift on version suffix — Mali is MLI_V1 in adm0 (and in the
    chunk collection actually reduced over) but MLI_V2 in the regions table.
    ISO3 is the fallback that keeps such a unit from losing its region, and
    ucode_fix relabels the row to the current ucode.

    Relabelling is only safe because the V1/V2 difference here is internal
    (admin subdivisions), leaving the adm0 outline unchanged — verified for
    Mali at 1 km2 of symmetric difference on 1,258,836 km2. A version bump that
    MOVED the outline would invalidate the figure, not just the label, and
    would need recomputing against the new geometry; check_ucode_drift() below
    reports any such case rather than silently relabelling it.

    Territories with no UNICEF programme region come back blank, which is a
    real answer — they sit outside the programme structure — not a gap to fill.
    """
    fc = ee.FeatureCollection(REGIONS_ASSET)
    ucodes, isos, regions = fc.reduceColumns(
        ee.Reducer.toList().repeat(3),
        ["ucode", "ISO3", "Region"]).getInfo()["list"]

    by_ucode, by_iso = {}, {}
    iso_units = {}          # ISO3 -> every ucode carrying it
    for uc, iso, reg in zip(ucodes, isos, regions):
        reg = (reg or "").strip()
        if uc and iso:
            iso_units.setdefault(iso, []).append(uc)
        if not reg:
            continue
        if uc:
            by_ucode[uc] = reg
        # Several units share one ISO3 (UMI covers nine islands), but siblings
        # sit in the same programme region, so the first is a safe answer for
        # the REGION. It is not a safe answer for the ucode — see below.
        if iso and iso not in by_iso:
            by_iso[iso] = reg

    # Only offer a ucode correction where the ISO3 identifies exactly ONE unit.
    # With shared codes the "newer" ucode belongs to a specific sibling, and
    # adopting it would relabel nine separate islands as UMI8_V1 or stamp
    # Ashmore and Cartier as AUS_V1.
    by_iso_ucode = {iso: ucs[0] for iso, ucs in iso_units.items()
                    if len(ucs) == 1}
    return by_ucode, by_iso, by_iso_ucode


def check_ucode_drift(pairs):
    """Warn where adm0's ucode differs from the regions asset's for one ISO3.

    A differing suffix is only a labelling matter while the OUTLINE is the
    same. If a boundary genuinely moved, the exposure figure was computed over
    the old geometry and must be recomputed — so this prints the area gap and
    leaves the decision visible rather than burying it.
    """
    flagged = []
    for iso, adm0_uc, reg_uc in pairs:
        try:
            a = ee.FeatureCollection(ADM0_CHUNKS).filter(
                ee.Filter.eq("ISO3", iso)).geometry(maxError=1000)
            b = ee.FeatureCollection(REGIONS_ASSET).filter(
                ee.Filter.eq("ISO3", iso)).geometry(maxError=1000)
            area = ee.Number(a.area(maxError=1000)).divide(1e6)
            diff = ee.Number(a.symmetricDifference(b, maxError=1000)
                             .area(maxError=1000)).divide(1e6)
            av, dv = ee.List([area, diff]).getInfo()
            pct = (dv / av * 100) if av else 0
            flagged.append((iso, adm0_uc, reg_uc, dv, pct))
        except Exception as e:
            flagged.append((iso, adm0_uc, reg_uc, None, None))
    return flagged


def fetch_rows(reduced):
    """Pull the chunk sums down and aggregate them to one row per adm0 unit.

    Grouped by UCODE, not ISO3. ISO3 is not unique across adm0 — 14 codes are
    shared by 2-9 units each — so grouping on it merges disputed territories
    into the states they are disputed with: Egypt would absorb Bi'r Tawil,
    Sudan the Hala'ib Triangle, South Sudan the Ilemi Triangle, and Portugal
    both Madeira and the Azores. ucode is one per unit.

    getInfo() on ~2238 small features is well inside the response limit, and
    avoids a Drive export plus a manual download for a table this size.
    """
    by_ucode, by_iso, by_iso_ucode = region_lookup()
    feats = reduced.getInfo().get("features", [])

    # How many adm0 units carry each ISO3, so the correction below can be
    # refused where the code is shared on THIS side too.
    adm0_iso_units = {}
    for f in feats:
        p = f.get("properties") or {}
        if p.get("ucode") and p.get("ISO3"):
            adm0_iso_units.setdefault(p["ISO3"], set()).add(p["ucode"])

    out, drift = {}, {}
    for f in feats:
        p = f.get("properties") or {}
        uc = p.get("ucode") or p.get("ISO3") or "—"
        iso = p.get("ISO3") or ""
        # Adopt the regions asset's ucode only when this ISO3 identifies ONE
        # unit on BOTH sides — that is a genuine version bump (Mali V1 -> V2).
        # Where the code is shared, the two assets are describing different
        # islands and any swap would mislabel them.
        shown = uc
        if (uc not in by_ucode and iso in by_iso_ucode
                and len(adm0_iso_units.get(iso, ())) == 1):
            shown = by_iso_ucode[iso]
            if shown != uc:
                drift[iso] = (uc, shown)
        row = out.setdefault(uc, {
            "ucode": shown, "ISO3": iso,
            "country": p.get("name") or uc,
            "region": by_ucode.get(uc) or by_iso.get(iso, ""),
        })
        for b in band_names():
            row[b] = row.get(b, 0.0) + float(p.get(b) or 0.0)
    rows = sorted(out.values(), key=lambda r: (r["country"] or "").lower())
    return rows, drift


def write_xlsx(rows, path, period):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Exposure"

    pcfg = PERIOD_MAP.get(period, {})
    sig_label = {s["name"]: s["label"] for s in SIGNALS}

    # Two header rows: the signal spans its thresholds, so the sheet reads as
    # two blocks rather than eight similarly-named columns.
    lead_cols = ["Ucode", "ISO3", "Country", "UNICEF region", "Total children"]
    top = list(lead_cols)
    sub = [""] * len(lead_cols)
    for sig in SIGNALS:
        for i, t in enumerate(THRESHOLDS):
            top.append(sig_label[sig["name"]] if i == 0 else "")
            sub.append(f"{t}%")
    ws.append(top)
    ws.append(sub)
    first_num = len(lead_cols)          # 1-based column of "Total children"
    first_sig = len(lead_cols) + 1      # 1-based column of the first threshold

    bold = Font(bold=True)
    # Header tints, keyed off each signal's own colour so a signal added to the
    # config cannot crash the writer for want of a hardcoded entry.
    tints = {"dry": "FBEEDC", "wet": "E3F0FB", "either": "F0EAF7"}
    fills = {s["name"]: PatternFill("solid",
                                    fgColor=tints.get(s["name"], "EFEFEF"))
             for s in SIGNALS}
    for c in range(1, len(top) + 1):
        ws.cell(1, c).font = bold
        ws.cell(2, c).font = bold
        ws.cell(2, c).alignment = Alignment(horizontal="center")
    col = first_sig
    for sig in SIGNALS:
        ws.merge_cells(start_row=1, start_column=col,
                       end_row=1, end_column=col + len(THRESHOLDS) - 1)
        ws.cell(1, col).alignment = Alignment(horizontal="center")
        for i in range(len(THRESHOLDS)):
            ws.cell(1, col + i).fill = fills[sig["name"]]
            ws.cell(2, col + i).fill = fills[sig["name"]]
        col += len(THRESHOLDS)

    for r in rows:
        ws.append([r["ucode"], r["ISO3"], r["country"], r.get("region", ""),
                   int(round(r.get("total_children", 0)))]
                  + [int(round(r.get(b, 0))) for b in list(band_names())[1:]])

    for c in range(first_num, len(top) + 1):
        for cell in ws[get_column_letter(c)][2:]:
            cell.number_format = "#,##0"
    for letter, width in [("A", 10), ("B", 7), ("C", 34), ("D", 30)]:
        ws.column_dimensions[letter].width = width
    for c in range(first_num, len(top) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.freeze_panes = ws.cell(3, first_num).coordinate
    ws.auto_filter.ref = (f"A2:{get_column_letter(len(top))}"
                          f"{2 + len(rows)}")

    # A second sheet so the numbers can never travel without their method.
    notes = wb.create_sheet("Method")
    for line in [
        ["ECMWF SEAS5 seasonal precipitation outlook — child exposure"],
        [],
        ["Period", pcfg.get("label", period)],
        ["Forecast", f"SEAS5 system {SYSTEM}, initialised {INIT_LABEL}, "
                     f"51 ensemble members"],
        ["Baseline", f"Model's own hindcast {HINDCAST} "
                     f"(24 years x 25 members = 600 values per cell)"],
        ["Population", "UNICEF under-18 grid, 1 km, 2025"],
        ["Boundaries", "UNICEF adm0, ALL units, no exclusions — one row per "
                       "ucode, not per ISO3"],
        ["", "ISO3 is not unique across adm0 (14 codes are shared by 2-9 "
             "units), so grouping on it would"],
        ["", "merge disputed territories into the states they are disputed "
             "with — Bi'r Tawil into Egypt,"],
        ["", "the Hala'ib Triangle into Sudan, the Ilemi Triangle into South "
             "Sudan. ucode is one per unit."],
        ["Regions", "UNICEF programme regions from admin0_regions_merged. "
                    "Blank = outside the programme"],
        ["", "structure (uninhabited or non-programme territories), not "
             "missing data."],
        ["Generated", dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"],
        [],
        ["How a cell counts as exposed"],
        ["", "Its probability reaches the column's threshold AND exceeds the "
             "opposite tercile."],
        ["", "Probabilities are the fraction of the 51 members falling in the "
             "driest / wettest third"],
        ["", "of what this model normally predicts for this cell and season."],
        [],
        ["Which threshold to use"],
        ["", f"{HEADLINE_THRESHOLD}% is the agreed operational cutoff for "
             f"'potentially impacted children', chosen after visual"],
        ["", "inspection of all countries. The lower columns are kept so the "
             "sensitivity of that choice stays visible:"],
        ["", "51 members means probability moves in steps of ~2 points, so "
             "cells near any cutoff flip on"],
        ["", "arbitrary grounds. ECMWF's own charts shade from ~40%."],
        [],
        ["'Either signal' is a union, not a sum of overlapping sets"],
        ["", "A cell counts as dry only when p_below > p_above, and as wet "
             "only when p_above > p_below,"],
        ["", "so the two can never both hold — verified as zero overlapping "
             "cells globally. 'Either signal'"],
        ["", "therefore equals dry + wet exactly, with NO double counting. "
             "The script re-checks this on every"],
        ["", "run against the real figures and reports a mismatch if the two "
             "ever start to overlap."],
        [],
        ["Excluded"],
        ["", "Cells whose climatology is below the arid cutoff "
             f"({pcfg.get('dry_mask_mm', '—')} mm for this period) are not "
             "counted:"],
        ["", "a 'wettest third' on a fraction of a millimetre is rounding "
             "noise, not a signal."],
        [],
        ["Read this before quoting a number"],
        ["", "A tercile signal is relative to THIS MODEL's climatology — "
             "'drier than normal' means drier than"],
        ["", "SEAS5 usually predicts, not drought. It is a risk proxy, not a "
             "forecast of drought or flooding."],
        ["", "Seasonal skill is regional: SEAS5 predicts well where ENSO "
             "teleconnections are strong and barely"],
        ["", "at all elsewhere. This table carries no skill column — use the "
             "dashboard's skill layers to check"],
        ["", "whether a country's signal sits where the model has a track "
             "record. Prefer the BRIER skill score"],
        ["", "over the correlation: it grades the tercile probabilities these "
             "counts are built on, and it is the"],
        ["", "harsher of the two (over the Horn of Africa the correlation is "
             "+0.47, the Brier skill score +0.07)."],
        ["", "El Nino conditions co-occurring with a signal is not proof the "
             "signal is caused by them: the"],
        ["", "forecast is the model's net response to all its initial "
             "conditions, and ENSO is never isolated"],
        ["", "within it. Prefer 'during a period when El Nino conditions are "
             "present' over a causal claim."],
        ["", "The forecast grid is 1 degree (~111 km); national totals are "
             "sums over coarse cells."],
    ]:
        notes.append(line)
    notes.column_dimensions["A"].width = 18
    notes.column_dimensions["B"].width = 100
    notes["A1"].font = Font(bold=True, size=13)

    wb.save(path)


def main():
    global INIT_TAG, INIT_LABEL, PERIOD_MAP
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True, metavar="YYYYMM",
                    help="forecast issue (init month), e.g. 202610")
    ap.add_argument("--period", default=WINDOW_PERIOD,
                    help=f"which period to export (default {WINDOW_PERIOD}); "
                         f"window_L2_L4 or L<lead>_<Mon>, e.g. L3_Dec")
    ap.add_argument("--out", default=None,
                    help="output .xlsx path (default alongside this script)")
    args = ap.parse_args()
    INIT_TAG, INIT_LABEL, PERIOD_MAP = parse_issue(args.init)
    if args.period not in PERIOD_MAP:
        sys.exit(f"ERROR: {args.period} not in issue {INIT_TAG}: "
                 f"{list(PERIOD_MAP)}")

    out = Path(args.out) if args.out else (
        HERE / f"elnino_exposure_{INIT_TAG}_{args.period}.xlsx")

    print(f"Period      : {PERIOD_MAP[args.period]['label']}")
    print(f"Thresholds  : {THRESHOLDS} % , both signals")
    print("Initialising Earth Engine ...")
    init_ee()

    print("Reducing over adm0 chunks (this is the slow part) ...")
    reduced = run_export(args.period)
    rows, drift = fetch_rows(reduced)
    print(f"  {len(rows)} adm0 units returned (grouped by ucode)")

    # A ucode version bump is only cosmetic while the outline is unchanged. If
    # one moved, the figure was computed over the old geometry and needs
    # recomputing — so check rather than assume, and say so either way.
    if drift:
        print(f"\n  {len(drift)} unit(s) relabelled to the regions asset's "
              f"ucode; verifying the outline is unchanged:")
        for iso, adm0_uc, reg_uc, dv, pct in check_ucode_drift(
                [(i, a, b) for i, (a, b) in drift.items()]):
            if dv is None:
                print(f"    {iso}: {adm0_uc} -> {reg_uc}  (could not compare)")
            elif pct < 0.01:
                print(f"    {iso}: {adm0_uc} -> {reg_uc}  outline identical "
                      f"({dv:,.0f} km2 diff) — figure valid")
            else:
                print(f"    {iso}: {adm0_uc} -> {reg_uc}  OUTLINE DIFFERS by "
                      f"{dv:,.0f} km2 ({pct:.2f}%) — RECOMPUTE THIS UNIT "
                      f"SEPARATELY")

    # The union must equal the sum of its parts, because a cell can only be
    # dry when p_below > p_above and wet when the reverse holds. Checked on the
    # real numbers rather than asserted: if this ever fails, the two signals
    # have started overlapping and "either" would be double counting.
    union = next((s for s in SIGNALS if s.get("union_of")), None)
    if union:
        worst = 0.0
        for r in rows:
            for t in THRESHOLDS:
                parts = sum(r.get(f"{n}_{t}", 0.0) for n in union["union_of"])
                worst = max(worst, abs(parts - r.get(f"{union['name']}_{t}", 0.0)))
        status = "OK — no double counting" if worst < 1 else (
            f"MISMATCH of {worst:,.0f} children — signals overlap!")
        print(f"\n  union check (dry + wet == "
              f"{union['name']}, all thresholds): {status}")

    missing = [r["country"] for r in rows if not r.get("region")]
    if missing:
        print(f"\n  {len(missing)} unit(s) with no UNICEF programme region "
              f"(blank in the sheet): {', '.join(missing[:6])}"
              + (" ..." if len(missing) > 6 else ""))

    write_xlsx(rows, out, args.period)
    print(f"\nWrote {out}")
    total = sum(r.get("total_children", 0) for r in rows)
    print(f"  total children across all units: {total:,.0f}")


if __name__ == "__main__":
    main()
