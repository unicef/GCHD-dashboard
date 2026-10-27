#!/usr/bin/env python
"""
export_reliability_table.py — exposed children per country, WITH how much the
forecast behind each figure can be relied on.

export_exposure_table.py answers "how many children". This answers the question
that should follow: "and should I believe it?". Same exposure maths, plus three
diagnostics per country and signal:

  correlation (r)   does the model's ensemble MEAN season rainfall track what
                    CHIRPS observed, 1993-2016? A track record for amounts.
  Brier skill (BSS) do the tercile PROBABILITIES verify? This grades what the
                    exposure threshold actually reads, and it is the harsher
                    test — a model can rank years well while being
                    consistently overconfident. Zero is the boundary: above it
                    the probabilities beat always forecasting 33%, at or below
                    they do not.
  anomaly (mm)      forecast minus climatological rainfall over the signal
                    area. Signed: negative under a dry signal and positive
                    under a wet one is the coherent case. It carries the
                    MAGNITUDE the probabilities do not — a cell can be 90%
                    likely to be drier while only 3 mm below normal.

Every diagnostic is area-weighted over the country's SIGNAL area, not the whole
country: what matters is the skill where the children are being counted.

Two classification schemes, both reported side by side because they disagree
and the disagreement is informative:

  *_class_eq     five EQUAL intervals, as specified
  *_class_jenks  five Fisher-Jenks classes fitted to the observed global
                 distribution

Equal intervals are easy to explain but poorly matched to these data: only
0.1% of land cells reach BSS >= 0.8, so "Very high" is all but empty and most
countries pile into two classes. Jenks puts the breaks where the data actually
separates (15/26/28/19/11% of cells for r). Compare the two columns before
quoting either.

Requires el_nino/skill and el_nino/brier (build_skill_mask.py,
build_brier_skill.py).

Requirements:  pip install earthengine-api openpyxl
Usage:
    python scripts/el_nino/export_reliability_table.py --init 202609
    python scripts/el_nino/export_reliability_table.py --init 202609 --period L3_Nov
    python scripts/el_nino/export_reliability_table.py --init 202609 --threshold 50
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import ee

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(APP))

from elnino_config import (            # noqa: E402
    issue_asset,
    SIGNALS, SIGNAL_MAP,
    HINDCAST, SYSTEM, SKILL_REFERENCE, to_fraction, parse_issue, window_suffix,
    ENSO_MASK_DIR as CFG_ENSO_DIR, enso_mask_asset,
)

# Brier is deliberately absent. It is still computed and stored in
# el_nino/brier, but reviewers of the rainfall report chose the correlation
# for interpretability and the dashboard dropped it, so a workbook that still
# carried BSS columns would disagree with the tab it is meant to match. There
# is also no joint (rainfall+temperature) Brier, so a compound row could only
# ever have shown the rainfall half — see the Method sheet.

ADM0_CHUNKS = "projects/unicef-ccri/assets/global_boundary/adm0_chunked_500km_shp"
REGIONS_ASSET = "projects/unicef-ccri/assets/global_boundary/admin0_regions_merged"
POP_1KM_TMPL = "projects/unicef-ccri/assets/population/worldpop_{}_2025_CN_1km"
POP_SCALE = 1000
POP_NATIVE = 92.76624232772798

# Sex-disaggregated under-18 grids. Verified to sum exactly to the total
# (Ethiopia: 31,055,207 + 29,925,424 = 60,980,631), so the breakdown is a true
# partition rather than two independent estimates — the sheet checks this on
# every run.
POP_VARIANTS = [("", "T_U18"), ("_m", "T_M_U18"), ("_f", "T_F_U18")]

DEFAULT_THRESHOLD = 70          # the agreed operational cutoff (rainfall)
# Joint signals are read against a 1/9 baseline rather than 1/3, so they need
# their own cutoff: 70% of members agreeing on BOTH conditions is a far
# stronger demand than 70% agreeing on one, and would empty the columns.
# 25% sits the same distance above its baseline that 70% does above 1/3.
DEFAULT_THRESHOLD_COMPOUND = 25

# ── ENSO attribution mask ───────────────────────────────────────────────────
# A 0.5 degree global raster of where El Nino HISTORICALLY shifts rainfall:
#   -1  historically drier under El Nino
#   +1  historically wetter
#    0  no statistically significant signal
#
# Applied DIRECTIONALLY (--enso-mask): a forecast dry signal counts only where
# the mask is -1, a wet signal only where it is +1. A dry signal sitting on a
# +1 cell is excluded, because El Nino makes that place wetter — the forecast
# may well be right, but it cannot be attributed to ENSO.
#
# This is the difference between "children under a forecast signal" and
# "children under a forecast signal that El Nino plausibly caused".
# From elnino_config so a workbook figure and a dashboard figure cannot drift.
# Compound signals inherit their RAINFALL component's direction; the mask says
# nothing about temperature, which the Method sheet states.
ENSO_MASK_DIR = dict(CFG_ENSO_DIR)

# ── Classification ──────────────────────────────────────────────────────────
# Five classes, two schemes. Labels are shared so the columns are comparable.
CLASS_LABELS = ["Very low", "Low", "Medium", "High", "Very high"]

# Equal intervals over 0..1 in 20% steps, as requested. Values at or below 0
# fall in "Very low" — for BSS that is meaningful (no better than guessing),
# for r it means the hindcast ran counter to observations.
EQ_BREAKS = [0.0, 0.2, 0.4, 0.6, 0.8]

# Fisher-Jenks, fitted to a 1146-cell global sample of the OND window. These
# are DATA-DERIVED: re-fit them if the underlying assets change.
# RAINFALL r only. Temperature r runs systematically higher (Horn of Africa
# +0.60 against rainfall's +0.47), so applying these breaks to it would make
# temperature score "High" as an artefact of the breaks rather than of skill.
# Temperature therefore gets equal-interval classes only, and the Classes
# sheet says why there is no Jenks column for it.
JENKS_BREAKS = {
    "r":   [-0.057, 0.162, 0.381, 0.609],
}

# Pastel fills, low -> high. Deliberately muted: these tint whole columns, and
# saturated colour on every row would compete with the numbers.
CLASS_FILLS = ["F4C7C3", "FADBC8", "FDF3C8", "DDEFD5", "CCE5D9"]


# Set from --init in main().
INIT_TAG = INIT_LABEL = None
PERIOD_MAP = {}
WINDOW_PERIOD = window_suffix()

def classify(value, breaks):
    """Index 0..4 for a value against four ascending break points."""
    if value is None:
        return None
    for i, b in enumerate(breaks):
        if value < b:
            return i
    return len(breaks)


def eq_class(value):
    return classify(value, EQ_BREAKS[1:])


def jenks_class(value, metric="r"):
    """Jenks class for RAINFALL correlation.

    Only defined for `r`: the breaks were fitted to the rainfall correlation
    distribution, and temperature r sits systematically higher, so the same
    breaks applied to it would report better classes without better skill.
    Temperature therefore carries equal-interval classes only.
    """
    if value is None or metric not in JENKS_BREAKS:
        return None
    return classify(value, JENKS_BREAKS[metric])


def init_ee():
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


def band_names_of(asset_id, fallback):
    try:
        prop = ee.Image(asset_id).get("band_names").getInfo()
    except Exception:
        return list(fallback)
    if not prop:
        return list(fallback)
    return [b.strip() for b in str(prop).split(",") if b.strip()]


def child_pop(variant="T_U18"):
    """Under-18 count per 1 km cell, rescaled for reduceRegions.

    reduceRegions(sum) samples one value per requested pixel rather than
    integrating, so a 1 km asset reduced at scale=1000 returns ~1/116th of the
    population; the ratio restores it. Same correction the dashboard uses.

    `variant` picks the total, male or female grid — all three share the naming
    pattern and the same native resolution, so one rescale serves all.
    """
    ratio = ee.Number(POP_SCALE).divide(POP_NATIVE).pow(2)
    return ee.Image(POP_1KM_TMPL.format(variant)).multiply(ratio).rename("pop")


def tercile_image(period):
    asset = issue_asset(INIT_TAG, "terciles", f"terciles_{INIT_TAG}_{period}")
    names = band_names_of(asset, [
        "p_below", "p_normal", "p_above", "cat",
        "fc_mean_mm", "clim_mean_mm", "anom_mm", "dry_mask"])
    return ee.Image(asset).rename(names)


def _on_tercile_grid(img, period):
    """Re-declare the grid WITHOUT resampling.

    The skill and Brier assets were exported with `region` + `scale` and no
    crsTransform, so they sit on an offset 1 degree grid (origin -180.9992
    rather than -180) that runs past the antimeridian. reduceRegions then fails
    with "Unable to transform edge ... from EPSG:4326 PLANAR".

    setDefaultProjection, not reproject: reproject forces a resampling pass
    whose own tiling reintroduced the same edge error. Re-declaring the grid is
    enough here because the values ARE on 1 degree cells already — only the
    stated origin was off — and they align with the terciles they qualify.
    """
    return img.setDefaultProjection(
        crs="EPSG:4326", crsTransform=[1, 0, -180, 0, -1, 90])


def skill_image(period):
    asset = issue_asset(INIT_TAG, "skill", f"skill_{INIT_TAG}_{period}")
    img = ee.Image(asset).rename(
        band_names_of(asset, ["skill_r", "skill_p"]))
    return _on_tercile_grid(img, period)


def skill_t2m_image(period):
    """Temperature correlation against ERA5-Land.

    NOT an independent observation: ERA5 shares IFS physics and assimilation
    lineage with SEAS5, so this is optimistically biased relative to the
    CHIRPS-scored rainfall skill beside it. The Method sheet says so, because
    the two columns sit next to each other and invite direct comparison.
    """
    asset = issue_asset(INIT_TAG, "skill_t2m", f"skill_t2m_{INIT_TAG}_{period}")
    img = ee.Image(asset).rename(
        band_names_of(asset, ["skill_r", "skill_p"]))
    return _on_tercile_grid(img, period)


def tercile_t2m_image(period):
    """Temperature terciles — SEVEN bands, no dry_mask."""
    asset = issue_asset(INIT_TAG, "terciles_t2m", f"terciles_t2m_{INIT_TAG}_{period}")
    return ee.Image(asset).rename(band_names_of(
        asset, ["p_below", "p_normal", "p_above", "cat",
                "fc_mean_c", "clim_mean_c", "anom_c"]))


def compound_image(period):
    """Joint rainfall+temperature probabilities — nine bands."""
    asset = issue_asset(INIT_TAG, "compound", f"compound_{INIT_TAG}_{period}")
    return ee.Image(asset).rename(band_names_of(
        asset, ["p_hotdry", "p_coolwet", "p_hotwet", "p_cooldry",
                "cat_compound", "p_dry_marginal", "p_hot_marginal",
                "dependence_ratio", "dry_mask"]))


def signal_mask(img, sig, pct, period=None):
    """1 where the signal holds at `pct` percent, arid cells excluded.

    Compound signals read the compound asset and drop the `p.gt(other)` test:
    for dry/wet that test is what guarantees exclusivity, but the four joint
    classes are not exclusive (different ensemble members can support
    different combinations in one cell), so there is no opposite band to
    out-rank. `period` is required for them.

    Thresholds are NOT comparable across the two families: a joint event needs
    two conditions at once, so its no-information baseline is 1/9 rather than
    1/3. The sheet records the threshold used for each.
    """
    if sig.get("union_of"):
        parts = [signal_mask(img, SIGNAL_MAP[n], pct, period)
                 for n in sig["union_of"]]
        out = parts[0]
        for extra in parts[1:]:
            out = out.Or(extra)
        return out
    if sig.get("compound"):
        c = compound_image(period)
        return (c.select(sig["band"]).gte(to_fraction(pct))
                 # Arid mask from the PRECIPITATION terciles via the compound
                 # image's inherited copy — one rule for every signal.
                 .And(c.select("dry_mask").eq(1)))
    p = img.select(sig["band"])
    other = img.select(sig["other"])
    return (p.gte(to_fraction(pct))
             .And(p.gt(other))
             .And(img.select("dry_mask").eq(1)))


def enso_mask_for(sig, period):
    """Cells where El Nino historically shifts rainfall in THIS direction.

    Direction-matched: dry against -1, wet against +1. The union has no single
    direction, so it takes either — a cell qualifies if El Nino historically
    moves it at all.
    """
    m = ee.Image(enso_mask_asset(PERIOD_MAP[period])).rename("enso_dir")
    if sig.get("union_of"):
        return m.neq(0)
    return m.eq(ENSO_MASK_DIR[sig["name"]])


def threshold_for(sig, thr_rain, thr_compound):
    """Which threshold a signal is read against.

    Two families, two baselines: a single-variable tercile has chance 1/3, a
    joint one has 1/9. Using one number for both would make every compound
    count look near-empty at a rainfall-appropriate cutoff. The sheet records
    the threshold per signal so the figures cannot be compared naively.
    """
    return thr_compound if sig.get("compound") else thr_rain


def build_stack(period, thr_rain, thr_compound, with_enso=True):
    """One multi-band image carrying every number the sheet needs.

    Population bands are SUMMED per country; diagnostic bands are AVERAGED
    over the signal area.

    When `with_enso`, a second set of population bands is added, restricted to
    cells where El Nino historically shifts rainfall in the SAME direction the
    forecast predicts. Both sets travel together so the sheet can show the
    unmasked and attributable counts side by side — the masked figure is a
    subset of the unmasked one by construction.

    Diagnostics per signal: the rainfall correlation always, the TEMPERATURE
    correlation as well for compound signals (they rest on two track records,
    not one), the rainfall anomaly always, and the temperature anomaly for
    compound signals. No Brier — see the note at the imports.
    """
    img = tercile_image(period)
    pops = {sfx: child_pop(v) for sfx, v in POP_VARIANTS}
    sk = skill_image(period).select("skill_r")

    # Temperature diagnostics are optional: the collections are built by
    # separate scripts and a run against a part-built project should still
    # produce the rainfall columns rather than failing outright.
    try:
        sk_t = skill_t2m_image(period).select("skill_r")
        anom_c = tercile_t2m_image(period).select("anom_c")
        have_t2m = True
    except Exception as e:
        print(f"  (temperature diagnostics unavailable: {str(e)[:90]})")
        sk_t = anom_c = None
        have_t2m = False

    sum_bands = [pops[sfx].rename(f"total_children{sfx}")
                 for sfx, _ in POP_VARIANTS]
    mean_bands = []
    for sig in SIGNALS:
        thr = threshold_for(sig, thr_rain, thr_compound)
        mask = signal_mask(img, sig, thr, period)
        n = sig["name"]
        attributable = (mask.And(enso_mask_for(sig, period))
                        if with_enso else None)
        for sfx, _ in POP_VARIANTS:
            sum_bands.append(
                pops[sfx].updateMask(mask).rename(f"{n}_children{sfx}"))
            if with_enso:
                sum_bands.append(pops[sfx].updateMask(attributable)
                                 .rename(f"{n}_children_enso{sfx}"))
        # Diagnostics restricted to the signal area: the skill that matters is
        # the skill where children are actually being counted.
        mean_bands += [
            sk.updateMask(mask).rename(f"{n}_r"),
            img.select("anom_mm").updateMask(mask).rename(f"{n}_anom"),
        ]
        if sig.get("compound") and have_t2m:
            mean_bands += [
                sk_t.updateMask(mask).rename(f"{n}_r_t"),
                anom_c.updateMask(mask).rename(f"{n}_anom_c"),
            ]
    return ee.Image.cat(sum_bands), ee.Image.cat(mean_bands), have_t2m


def sum_band_names(with_enso=True):
    """Band order for the summed population stack.

    3 totals + per signal (3 unmasked + 3 ENSO-masked) = 21 bands for three
    signals. One reduceRegions pass computes them all.
    """
    for sfx, _ in POP_VARIANTS:
        yield f"total_children{sfx}"
    for sig in SIGNALS:
        for sfx, _ in POP_VARIANTS:
            yield f"{sig['name']}_children{sfx}"
            if with_enso:
                yield f"{sig['name']}_children_enso{sfx}"


def mean_band_names(have_t2m=True):
    """Band order for the averaged diagnostic stack.

    MUST match build_stack's mean_bands exactly, including the conditional
    temperature pair — a mismatch would silently shift every value one column.
    """
    for sig in SIGNALS:
        yield f"{sig['name']}_r"
        yield f"{sig['name']}_anom"
        if sig.get("compound") and have_t2m:
            yield f"{sig['name']}_r_t"
            yield f"{sig['name']}_anom_c"


def region_lookup():
    """{ucode: region}, {ISO3: region}, {ISO3: current ucode}."""
    fc = ee.FeatureCollection(REGIONS_ASSET)
    ucodes, isos, regions = fc.reduceColumns(
        ee.Reducer.toList().repeat(3),
        ["ucode", "ISO3", "Region"]).getInfo()["list"]
    by_ucode, by_iso, iso_units = {}, {}, {}
    for uc, iso, reg in zip(ucodes, isos, regions):
        reg = (reg or "").strip()
        if uc and iso:
            iso_units.setdefault(iso, []).append(uc)
        if not reg:
            continue
        if uc:
            by_ucode[uc] = reg
        if iso and iso not in by_iso:
            by_iso[iso] = reg
    # Only offer a ucode correction where the ISO3 identifies ONE unit; with
    # shared codes (UMI covers nine islands) the "newer" ucode belongs to a
    # specific sibling and adopting it would mislabel the others.
    return by_ucode, by_iso, {i: u[0] for i, u in iso_units.items()
                              if len(u) == 1}


def fetch_rows(period, thr_rain, thr_compound):
    """One row per adm0 UCODE.

    Grouped by ucode, never ISO3: 14 ISO3 codes are shared by 2-9 adm0 units,
    so grouping on ISO3 merges disputed territories into the states they are
    disputed with (Bi'r Tawil into Egypt, the Ilemi Triangle into South Sudan).
    """
    sums, means, have_t2m = build_stack(period, thr_rain, thr_compound)

    # 10 chunks (Russia, Fiji) extend past +180 — to 185 degrees — because the
    # 500 km tiling wraps the antimeridian without splitting there. A reduction
    # pinned to a 1 degree grid then asks Earth Engine to transform a pixel edge
    # outside valid lon/lat and fails with
    #     Unable to transform edge (-1.0, 127.0 ...) from EPSG:4326 PLANAR
    #
    # Intersecting EVERY chunk with the valid domain also works but exceeds the
    # user memory limit — 2238 geometry intersections is far too much for a
    # 10-feature problem. Dropping just the offenders from the DIAGNOSTIC
    # reduction is cheap and costs nothing real: they are slivers east of 180 in
    # Chukotka and Fiji that a 1 degree forecast barely resolves. The population
    # sums keep every chunk, because they reduce at 1 km with no pinned grid,
    # so no child is lost from any count.
    all_chunks = ee.FeatureCollection(ADM0_CHUNKS)

    got_sum = sums.reduceRegions(
        collection=all_chunks, reducer=ee.Reducer.sum(),
        scale=POP_SCALE, tileScale=4,
    ).select(["ISO3", "name", "ucode"] + list(sum_band_names()),
             retainGeometry=False).getInfo().get("features", [])

    # Diagnostics are reduced ONE COUNTRY AT A TIME, with reduceRegion over the
    # dissolved country geometry — not a global reduceRegions over the chunks.
    #
    # A global chunk-wise reduction of these surfaces fails with
    #     Unable to transform edge (...) from EPSG:4326 PLANAR
    # regardless of scale or crsTransform, and clipping all 2238 chunk
    # geometries to dodge it exceeds the user memory limit. The per-country path
    # is the one the dashboard already uses (signal_skill_share / signal_bss)
    # and it handles even the antimeridian cases — Fiji returns cleanly.
    #
    # Slower, but these are ~290 small reductions of a 1 degree surface, and it
    # is the difference between a table and no table.
    got_mean = {}
    ucodes = sorted({(f.get("properties") or {}).get("ucode")
                     for f in got_sum} - {None})
    print(f"  diagnostics: {len(ucodes)} countries, one reduction each ...")
    for i, uc in enumerate(ucodes, 1):
        geom = all_chunks.filter(ee.Filter.eq("ucode", uc)).geometry()
        try:
            got_mean[uc] = means.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=geom,
                scale=100000, bestEffort=True, maxPixels=1e9).getInfo()
        except Exception as e:
            print(f"    {uc}: diagnostics unavailable ({str(e)[:70]})")
            got_mean[uc] = {}
        if i % 50 == 0:
            print(f"    {i}/{len(ucodes)}")

    by_ucode, by_iso, iso_ucode = region_lookup()

    adm0_iso = {}
    for f in got_sum:
        p = f.get("properties") or {}
        if p.get("ucode") and p.get("ISO3"):
            adm0_iso.setdefault(p["ISO3"], set()).add(p["ucode"])

    rows = {}
    for f in got_sum:
        p = f.get("properties") or {}
        uc = p.get("ucode") or p.get("ISO3") or "—"
        iso = p.get("ISO3") or ""
        shown = uc
        if (uc not in by_ucode and iso in iso_ucode
                and len(adm0_iso.get(iso, ())) == 1):
            shown = iso_ucode[iso]
        row = rows.setdefault(uc, {
            "ucode": shown, "ISO3": iso, "country": p.get("name") or uc,
            "region": by_ucode.get(uc) or by_iso.get(iso, ""),
            "_chunks": 0,
        })
        for b in sum_band_names():
            row[b] = row.get(b, 0.0) + float(p.get(b) or 0.0)

    # One dict per ucode, already area-averaged over the whole country by
    # reduceRegion. A band is absent when no signal cell there was scorable —
    # that is a real "not assessed", so it stays None rather than becoming 0.
    for uc, row in rows.items():
        stats = got_mean.get(uc) or {}
        for b in mean_band_names(have_t2m):
            v = stats.get(b)
            row[b] = float(v) if v is not None else None
    return (sorted(rows.values(), key=lambda r: (r["country"] or "").lower()),
            have_t2m)


def write_xlsx(rows, path, period, thr_rain, thr_compound):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Reliability"
    pcfg = PERIOD_MAP.get(period, {})

    lead = ["Ucode", "ISO3", "Country", "UNICEF region",
            "Total children", "Total boys", "Total girls"]
    # Boys/girls sit immediately after each headline count rather than in a
    # separate block, so a reader comparing one country's numbers never has to
    # scroll sideways past the diagnostics to find the breakdown.
    # One column set per signal. The temperature columns are present for
    # every signal so the block width is uniform (Excel merges and the tint
    # offsets both depend on that) and are simply blank for rainfall-only
    # signals, where no temperature component exists to score.
    #
    # Two correlations rather than one for a compound signal: it rests on two
    # separate track records, and collapsing them would hide which half is
    # weak. No Jenks column for temperature — the breaks were fitted to the
    # rainfall distribution, see JENKS_BREAKS.
    per_sig = ["Children", "Boys", "Girls",
               "Children (ENSO area)", "Boys (ENSO)", "Girls (ENSO)",
               "% attributable",
               "Threshold %",
               "Anomaly (mm)", "Anomaly (degC)",
               "Correlation r (rain)", "r rain class (equal)",
               "r rain class (Jenks)",
               "Correlation r (temp)", "r temp class (equal)"]

    top = list(lead) + [""] * (len(SIGNALS) * len(per_sig))
    sub = [""] * len(lead)
    col = len(lead) + 1
    for sig in SIGNALS:
        top[col - 1] = sig["label"]
        sub += list(per_sig)
        col += len(per_sig)
    # Banner BEFORE the headers. The single most likely misreading of this
    # sheet is to sum or compare the rainfall and combined blocks, which use
    # different thresholds for a real reason (different baselines) — India
    # reads 0 children dry at 70% and 332M hot-and-dry at 25%, which looks
    # like a contradiction and is not. Said here because a reader who sums a
    # column will not have read the Method sheet first.
    ws.append([
        f"WARNING: rainfall signals are read at {thr_rain}% and combined "
        f"signals at {thr_compound}%. These blocks are NOT comparable and "
        f"must not be summed together — see the 'Threshold %' column in each "
        f"block and the Method sheet. A single-variable and a joint signal "
        f"have different no-information baselines (1/3 vs 1/9)."])
    ws.append([])
    ws.append(top)
    ws.append(sub)

    # Header rows are now 3 (signal names) and 4 (column labels).
    HDR_TOP, HDR_SUB = 3, 4
    warn = ws.cell(1, 1)
    warn.font = Font(bold=True, color="9C0006")
    warn.alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells(start_row=1, start_column=1,
                   end_row=1, end_column=min(len(top), 16))
    ws.row_dimensions[1].height = 46

    bold = Font(bold=True)
    for c in range(1, len(top) + 1):
        ws.cell(HDR_TOP, c).font = bold
        ws.cell(HDR_SUB, c).font = bold
        ws.cell(HDR_SUB, c).alignment = Alignment(
            horizontal="center", wrap_text=True, vertical="bottom")
    col = len(lead) + 1
    for sig in SIGNALS:
        ws.merge_cells(start_row=HDR_TOP, start_column=col,
                       end_row=HDR_TOP, end_column=col + len(per_sig) - 1)
        ws.cell(HDR_TOP, col).alignment = Alignment(horizontal="center")
        col += len(per_sig)

    fills = [PatternFill("solid", fgColor=f) for f in CLASS_FILLS]
    for r in rows:
        # Bound to THIS row, inside the loop: a lambda defined outside would
        # close over whatever `r` happened to be last.
        iv = lambda key, _r=r: int(round(_r.get(key, 0) or 0))
        line = [r["ucode"], r["ISO3"], r["country"], r.get("region", ""),
                iv("total_children"), iv("total_children_m"),
                iv("total_children_f")]
        for sig in SIGNALS:
            n = sig["name"]
            rv = r.get(f"{n}_r")                    # rainfall correlation
            rt = r.get(f"{n}_r_t")                  # temperature correlation
            am = r.get(f"{n}_anom")
            ac = r.get(f"{n}_anom_c")
            kids = iv(f"{n}_children")
            kids_e = iv(f"{n}_children_enso")
            jr = jenks_class(rv, "r") if rv is not None else None
            line += [
                kids, iv(f"{n}_children_m"), iv(f"{n}_children_f"),
                kids_e, iv(f"{n}_children_enso_m"), iv(f"{n}_children_enso_f"),
                round(100 * kids_e / kids, 1) if kids else None,
                threshold_for(sig, thr_rain, thr_compound),
                round(am, 1) if am is not None else None,
                round(ac, 1) if ac is not None else None,
                round(rv, 3) if rv is not None else None,
                CLASS_LABELS[eq_class(rv)] if rv is not None else None,
                CLASS_LABELS[jr] if jr is not None else None,
                round(rt, 3) if rt is not None else None,
                CLASS_LABELS[eq_class(rt)] if rt is not None else None,
            ]
        ws.append(line)

        # Pastel-tint the class cells, so a reader scanning the sheet sees
        # reliability before reading any number.
        #
        # Offsets are derived from per_sig rather than hardcoded: adding a
        # column (as the ENSO ones did) would otherwise silently shift every
        # tint onto the wrong cell.
        row_i = ws.max_row
        c = len(lead) + 1
        tint_at = [(per_sig.index("r rain class (equal)"), "r", eq_class),
                   (per_sig.index("r rain class (Jenks)"), "r",
                    lambda v: jenks_class(v, "r")),
                   (per_sig.index("r temp class (equal)"), "r_t", eq_class)]
        for sig in SIGNALS:
            n = sig["name"]
            vals = {"r": r.get(f"{n}_r"), "r_t": r.get(f"{n}_r_t")}
            for off, metric, fn in tint_at:
                v = vals[metric]
                if v is None:
                    continue
                ws.cell(row_i, c + off).fill = fills[fn(v)]
            c += len(per_sig)

    # Number formats and widths.
    for c in range(len(lead), len(top) + 1):
        letter = get_column_letter(c)
        head = ws.cell(HDR_SUB, c).value or ""
        for cell in ws[letter][HDR_SUB:]:
            if (head.startswith("Children") or head.startswith("Boys")
                    or head.startswith("Girls") or head.startswith("Total")
                    or head == ""):
                cell.number_format = "#,##0"
            elif head.startswith("%"):
                cell.number_format = '0.0"%"'
            elif "Anomaly" in head:
                cell.number_format = "#,##0.0"
            elif "Correlation" in head:
                cell.number_format = "0.000"
            elif head.startswith("Threshold"):
                cell.number_format = '0"%"'

    for letter, w in (("A", 10), ("B", 7), ("C", 32), ("D", 28), ("E", 14)):
        ws.column_dimensions[letter].width = w
    for c in range(len(lead) + 1, len(top) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 13
    ws.freeze_panes = ws.cell(HDR_SUB + 1, len(lead) + 1).coordinate
    ws.auto_filter.ref = (f"A{HDR_SUB}:{get_column_letter(len(top))}"
                          f"{HDR_SUB + len(rows)}")
    ws.row_dimensions[HDR_SUB].height = 30

    _write_method_sheet(wb, period, thr_rain, thr_compound, len(rows))
    _write_classes_sheet(wb)
    wb.save(path)


def _write_method_sheet(wb, period, thr_rain, thr_compound, n_rows):
    from openpyxl.styles import Font
    pcfg = PERIOD_MAP.get(period, {})
    ws = wb.create_sheet("Method")
    L = [
        ["ECMWF SEAS5 seasonal outlook — child exposure and forecast reliability"],
        [],
        ["WHAT THIS TABLE IS FOR"],
        ["", "The companion sheet answers 'how many children'. This one answers "
             "'and can the forecast behind"],
        ["", "that number be relied on?'. A large count from a model with no "
             "track record in that region is not"],
        ["", "the same evidence as the same count where the model has been "
             "verified."],
        [],
        ["THE FORECAST"],
        ["Period", pcfg.get("label", period)],
        ["Model", f"ECMWF SEAS5 system {SYSTEM}, initialised {INIT_LABEL}, "
                  f"51 ensemble members"],
        ["Baseline", f"The model's OWN hindcast {HINDCAST} — 24 years x 25 "
                     f"members = 600 values per grid cell."],
        ["", "Terciles are cut from this, not from observations, so the model's "
             "systematic biases cancel."],
        ["Threshold", f"{thr_rain}% rainfall / {thr_compound}% combined — a "
                      f"cell carries a signal when that share "
                      f"of the 51 members falls in the"],
        ["", "relevant third AND that share exceeds the opposite third."],
        ["Population", "WorldPop under-18 grids, 1 km, 2025 — total, male and "
                       "female counted separately"],
        ["", "Boys and girls are summed from their own grids, not split from "
             "the total by a ratio, so they"],
        ["", "reconcile with it to within 0.0001% globally. ONE exception: "
             "Cook Islands, where the source"],
        ["", "grids disagree by 42% (total 2,947 vs 2,765 boys + 1,425 "
             "girls). That is a defect in the"],
        ["", "population data itself — read that country's breakdown with "
             "caution. Elsewhere any 1-child"],
        ["", "difference is just independent rounding of three figures."],
        ["Boundaries", f"UNICEF adm0, all {n_rows} units, no exclusions. One "
                       f"row per UCODE, not per ISO3:"],
        ["", "14 ISO3 codes are shared by 2-9 adm0 units, so grouping on ISO3 "
             "would merge disputed"],
        ["", "territories into the states they are disputed with."],
        [],
        ["KEY CONCEPTS"],
        [],
        ["Tercile", "The model's own history for a cell, split into thirds: "
                    "driest, near-normal, wettest."],
        ["", "'Drier than normal' means drier than SEAS5 usually predicts "
             "there — NOT drought, and not"],
        ["", "dry in absolute terms."],
        [],
        ["Probability", "The fraction of the 51 ensemble members landing in a "
                        "given third. 33% is the"],
        ["", "no-information baseline, because terciles split the past into "
             "three equal parts. With 51"],
        ["", "members the probability moves in steps of about 2 points, so "
             "cells near any cutoff flip on"],
        ["", "essentially arbitrary grounds."],
        [],
        ["Exposed children", "Child population summed wherever the signal "
                             "holds. Cells whose climatology is below"],
        ["", f"the arid cutoff ({pcfg.get('dry_mask_mm', '—')} mm for this "
             f"period) are excluded: a 'wettest third' on a fraction of a"],
        ["", "millimetre is rounding noise at any threshold."],
        [],
        ["Children (ENSO area)", "The subset of the count that falls where El "
                                 "Nino HISTORICALLY shifts rainfall in the"],
        ["", "same direction the forecast predicts. Applied from a 0.5 degree "
             "global mask of statistically"],
        ["", "significant historical ENSO response (-1 drier, +1 wetter, 0 no "
             "significant signal):"],
        ["", "a DRY forecast signal counts only on -1 cells, a WET signal only "
             "on +1 cells."],
        ["", "A dry signal sitting on a +1 cell is excluded — El Nino makes "
             "that place wetter, so whatever the"],
        ["", "forecast is responding to there, it is not the ENSO "
             "teleconnection. The forecast may still be"],
        ["", "right; the point is that the signal cannot be ATTRIBUTED to El "
             "Nino."],
        ["", "This is the difference between 'children under a forecast "
             "signal' and 'children under a forecast"],
        ["", "signal El Nino plausibly caused'. The masked figure is always a "
             "subset of the unmasked one."],
        ["% attributable", "The ENSO-area count as a share of the unmasked "
                           "count. A low percentage means most of the"],
        ["", "signal in that country sits outside the historical ENSO "
             "footprint."],
        [],
        ["Anomaly (mm)", "Forecast mean rainfall minus the climatological "
                         "mean, averaged over the signal area."],
        ["", "NEGATIVE under a dry signal and POSITIVE under a wet one is the "
             "coherent case. This carries the"],
        ["", "magnitude the probabilities do not: a cell can be 90% likely to "
             "be drier while only 3 mm below"],
        ["", "normal. A large count with a near-zero anomaly is a confident "
             "forecast of very little."],
        [],
        ["Correlation (r)", f"Pearson correlation between the hindcast "
                            f"ensemble-MEAN season total and"],
        ["", f"{SKILL_REFERENCE} observations, over {HINDCAST}, per grid cell. "
             f"A track record for AMOUNTS."],
        ["", "Averaged over the signal area only — the skill that matters is "
             "the skill where children are"],
        ["", "being counted."],
        [],
        ["Correlation r (temp)", "The same measure for TEMPERATURE, scored "
                                  "against ERA5-Land, and present only"],
        ["", "for the combined signals — they are the only ones with a "
             "temperature component."],
        ["", "IMPORTANT: ERA5-Land is a reanalysis that shares model physics "
             "and assimilation lineage with"],
        ["", "SEAS5, so it is NOT an independent observation the way CHIRPS "
             "is. Temperature skill scored"],
        ["", "this way is optimistically biased relative to the rainfall "
             "column beside it, and ERA5 also"],
        ["", "runs cool over Africa. Temperature skill IS genuinely higher "
             "than rainfall skill (Horn of"],
        ["", "Africa +0.60 against +0.47) — both things are true at once, and "
             "the gap is smaller than it"],
        ["", "looks. The tercile framing cancels a uniform bias, because each "
             "side is judged against its own"],
        ["", "distribution; a spatially varying bias does not cancel."],
        [],
        ["WHY TWO CORRELATIONS FOR A COMBINED SIGNAL"],
        ["", "A hot-and-dry signal rests on two separate track records. "
             "Collapsing them into one number"],
        ["", "would hide which half is weak, and they routinely differ. Read "
             "the WEAKER of the two as the"],
        ["", "ceiling on how much the combined figure is worth."],
        [],
        ["NO BRIER SCORE IN THIS WORKBOOK"],
        ["", "Earlier versions carried a Brier skill score (BSS), which asks "
             "the stricter question: do the"],
        ["", "PROBABILITIES verify, not just the ranking of years. It is still "
             "computed and stored"],
        ["", "(el_nino/brier) and the technical argument for it stands — a "
             "model can rank years well while"],
        ["", "being systematically overconfident, and correlation cannot see "
             "that. Over the OND window the"],
        ["", "two disagree sharply: Horn of Africa r +0.47 against BSS +0.07."],
        ["", "It was dropped because reviewers of the rainfall report compared "
             "both and chose the"],
        ["", "correlation: BSS did not sit alongside the classified historical "
             "values they use to read a"],
        ["", "signal's DIRECTION. The dashboard dropped it too, so a workbook "
             "that kept it would disagree"],
        ["", "with the tab it is meant to match."],
        ["", "There is also no JOINT Brier: nothing verifies the "
             "rainfall-and-temperature event together, so"],
        ["", "a combined row could only ever have shown the rainfall half, "
             "which would read as though the"],
        ["", "joint forecast had been validated when it has not."],
        ["", "CONSEQUENCE: read the correlation as the GENEROUS measure. A "
             "respectable r means 'worth"],
        ["", "looking at', not 'verified'."],
        [],
        ["HOW TO READ A ROW"],
        ["", "High children + High r + coherent anomaly  -> a well-supported "
             "figure."],
        ["", "High children + Very low r                 -> a real signal the "
             "model has no track record for."],
        ["", "                                              Treat as a flag "
             "for attention, not a planning number."],
        ["", "High children + anomaly near zero          -> confident "
             "direction, negligible magnitude."],
        ["", "Combined signal, r rain High + r temp Low  -> the rainfall half "
             "is trustworthy, the heat half is"],
        ["", "                                              not. The weaker "
             "number governs."],
        [],
        ["WHY A COUNTRY CAN SHOW 0 IN ONE BLOCK AND MILLIONS IN ANOTHER"],
        ["", "This is the most confusing thing in the sheet, and it is not an "
             "error. India reads ZERO children"],
        ["", "under 'Drier than normal' and 332 million under 'Hotter and "
             "drier than normal'. A combined"],
        ["", "signal cannot be commoner than the rainfall signal inside it, "
             "so what is going on?"],
        ["", "Two different thresholds. Over India's interpretable land the "
             "dry probability averages 41% of"],
        ["", "the 51 ensemble members — well short of the 70% the rainfall "
             "column demands, so that column"],
        ["", "is empty. The hot probability averages 92%, because warming "
             "has made the warmest third"],
        ["", "near-certain there, so 'dry AND hot' averages 40% — almost "
             "exactly the dry figure, since the"],
        ["", "heat condition excludes nearly nothing. 40% clears the 25% bar "
             "the combined column uses."],
        ["", "So India is not a hot-and-dry story. It is a moderate dry "
             "signal that fails a demanding"],
        ["", "threshold and passes a lenient one, with heat adding almost no "
             "information."],
        ["", "AT A MATCHED THRESHOLD the two behave as expected: at 25% "
             "India reads 310M dry and 332M"],
        ["", "hot-and-dry; at 70% both read zero. Globally at a common 70%: "
             "192M dry, 190M hot-and-dry."],
        ["", "DO NOT sum or compare across blocks without checking the "
             "'Threshold %' column."],
        [],
        ["THRESHOLDS ARE NOT COMPARABLE ACROSS FAMILIES"],
        ["", "A rainfall tercile has a 1-in-3 no-information baseline; a "
             "combined one needs two conditions"],
        ["", "at once, so its baseline is 1 in 9 (11.1%). A 25% combined "
             "probability therefore sits the"],
        ["", "same distance above chance as a 70% rainfall one. The "
             "'Threshold %' column records which"],
        ["", "cutoff each signal was read against — do not compare the counts "
             "without it."],
        [],
        ["TEMPERATURE SATURATION — READ THIS BEFORE USING THE COMBINED "
         "COLUMNS"],
        ["", "Measured against a 1993-2016 baseline, warming has made the "
             "warmest third close to certain"],
        ["", "across much of the tropics: the model puts about a fifth of land "
             "in its hottest tercile"],
        ["", "outright. Where nearly every ensemble member is hot, 'hot AND "
             "dry' carries almost the same"],
        ["", "information as 'dry' alone, and the two counts will be close. "
             "The combined signals"],
        ["", "discriminate most in the SUBTROPICS — southern Africa and "
             "Australia among them — and least"],
        ["", "near the equator. The cool combinations are correspondingly "
             "rarer; small counts there are"],
        ["", "expected, not an error."],
        [],
        ["LIMITS"],
        ["", "No diagnostic is applied to the counts — the children figures "
             "are unfiltered, so they stay"],
        ["", "comparable with the dashboard."],
        ["", f"{SKILL_REFERENCE} is land-only and spans 50N-50S, so cells "
             f"outside it are unscored; a blank r"],
        ["", "means 'not assessed', not 'no skill'."],
        ["", "The ENSO mask is a RAINFALL mask. For a combined signal it "
             "constrains the rainfall half only"],
        ["", "and says nothing about whether the heat is El Nino-related, so "
             "'% attributable' is a weaker"],
        ["", "claim for those rows than for the rainfall-only ones."],
        ["", "The forecast grid is 1 degree (~111 km); national figures are "
             "sums over coarse cells."],
        ["", "El Nino conditions co-occurring with a signal is not proof the "
             "signal is caused by them."],
        [],
        ["Generated", dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"],
    ]
    for line in L:
        ws.append(line)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 104
    ws["A1"].font = Font(bold=True, size=13)
    for i, line in enumerate(L, 1):
        if line and line[0] and line[0].isupper() and len(line) == 1:
            ws.cell(i, 1).font = Font(bold=True)


def _write_classes_sheet(wb):
    """The two class schemes, so the breaks are auditable."""
    from openpyxl.styles import Font, PatternFill
    ws = wb.create_sheet("Classes")
    ws.append(["Two five-class schemes, both reported — they disagree, and "
               "that is informative"])
    ws.append([])
    ws.append(["EQUAL INTERVALS — 20% steps, simple to explain"])
    ws.append(["Class", "Range", "Applies to"])
    for i, lab in enumerate(CLASS_LABELS):
        lo = EQ_BREAKS[i]
        hi = EQ_BREAKS[i + 1] if i + 1 < len(EQ_BREAKS) else None
        rng = (f"< {EQ_BREAKS[1]}" if i == 0 else
               f"≥ {lo}" if hi is None else f"{lo} – {hi}")
        ws.append([lab, rng, "rainfall r and temperature r"])
        ws.cell(ws.max_row, 1).fill = PatternFill("solid",
                                                  fgColor=CLASS_FILLS[i])
    ws.append([])
    ws.append(["", "Caveat: only ~1% of land cells reach r >= 0.8, so "
                   "'Very high' is nearly empty"])
    ws.append(["", "and most countries fall into the bottom "
                   "two classes. Easy to read, but it"])
    ws.append(["", "compresses the range where the real variation is."])
    ws.append([])
    ws.append(["FISHER-JENKS — breaks fitted to the observed distribution"])
    ws.append(["Class", "Rainfall correlation r", "Applies to"])
    for i, lab in enumerate(CLASS_LABELS):
        b = JENKS_BREAKS["r"]
        if i == 0:
            rng = f"< {b[0]}"
        elif i == len(CLASS_LABELS) - 1:
            rng = f">= {b[-1]}"
        else:
            rng = f"{b[i - 1]} - {b[i]}"
        ws.append([lab, rng, "rainfall r only"])
        ws.cell(ws.max_row, 1).fill = PatternFill("solid",
                                                  fgColor=CLASS_FILLS[i])
    ws.append([])
    ws.append(["", "Fitted to a 1146-cell global sample of the OND window. "
                   "Class shares for r: 15/26/28/19/11%."])
    ws.append(["", "Breaks fall where the data separates, so differences "
                   "between countries are visible — but the"])
    ws.append(["", "boundaries are data-derived, not absolute, and would move "
                   "if the underlying assets changed."])
    ws.append([])
    ws.append(["WHY THERE IS NO JENKS COLUMN FOR TEMPERATURE"])
    ws.append(["", "These breaks were fitted to the RAINFALL correlation "
                   "distribution. Temperature correlation runs"])
    ws.append(["", "systematically higher — Horn of Africa +0.60 against "
                   "rainfall's +0.47 — so applying the same"])
    ws.append(["", "breaks to it would report better classes without better "
                   "skill, and a reader comparing the two"])
    ws.append(["", "columns would be misled by the scheme rather than informed "
                   "by the data."])
    ws.append(["", "Temperature therefore carries equal-interval classes only. "
                   "Re-fitting Jenks on the temperature"])
    ws.append(["", "surface would be the correct way to add one."])
    ws.append([])
    ws.append(["NO BRIER CLASSES"])
    ws.append(["", "The Brier skill score is no longer reported in this "
                   "workbook — see the Method sheet for why,"])
    ws.append(["", "and note that it is still computed and stored in "
                   "el_nino/brier if it is wanted again."])
    ws.append([])
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 96
    ws.column_dimensions["C"].width = 22
    ws["A1"].font = Font(bold=True, size=12)


def main():
    global INIT_TAG, INIT_LABEL, PERIOD_MAP
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True, metavar="YYYYMM",
                    help="forecast issue (init month), e.g. 202610")
    ap.add_argument("--period", default=WINDOW_PERIOD,
                    help="window_L2_L4 or L<lead>_<Mon>, e.g. L3_Dec")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help=f"signal probability cutoff in %% "
                         f"(default {DEFAULT_THRESHOLD})")
    ap.add_argument("--threshold-compound", type=int,
                    default=DEFAULT_THRESHOLD_COMPOUND,
                    help=f"joint signal cutoff in %% (default "
                         f"{DEFAULT_THRESHOLD_COMPOUND}). Separate from "
                         f"--threshold because a joint event's "
                         f"no-information baseline is 1/9, not 1/3")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    INIT_TAG, INIT_LABEL, PERIOD_MAP = parse_issue(args.init)
    if args.period not in PERIOD_MAP:
        sys.exit(f"ERROR: {args.period} not in issue {INIT_TAG}: "
                 f"{list(PERIOD_MAP)}")

    out = Path(args.out) if args.out else (
        HERE / f"elnino_exposure_reliability_{INIT_TAG}_{args.period}"
               f"_{args.threshold}pct.xlsx")

    print(f"Period    : {PERIOD_MAP[args.period]['label']}")
    print(f"Threshold : {args.threshold}% rainfall, "
          f"{args.threshold_compound}% combined")
    print(f"Signals   : {[s['name'] for s in SIGNALS]}")
    init_ee()

    # Rainfall skill is required; temperature and compound are optional, so a
    # run against a part-built project still produces the rainfall columns
    # rather than failing outright.
    aid = issue_asset(INIT_TAG, "skill", f"skill_{INIT_TAG}_{args.period}")
    try:
        ee.data.getAsset(aid)
    except ee.EEException:
        sys.exit(f"ERROR: {aid} is missing. Build it with "
                 f"scripts/el_nino/build_skill_mask.py")
    for label, aid2 in (
            ("temperature skill",
             issue_asset(INIT_TAG, "skill_t2m",
                         f"skill_t2m_{INIT_TAG}_{args.period}")),
            ("combined signals",
             issue_asset(INIT_TAG, "compound",
                         f"compound_{INIT_TAG}_{args.period}"))):
        try:
            ee.data.getAsset(aid2)
        except ee.EEException:
            print(f"  NOTE: {label} absent - those columns will be blank.")

    print("\nReducing over adm0 chunks ...")
    rows, have_t2m = fetch_rows(args.period, args.threshold,
                                args.threshold_compound)
    print(f"  {len(rows)} adm0 units")

    # Boys + girls should equal the total: the three WorldPop grids are meant
    # to be a partition of one population. Checked RELATIVELY, because the
    # absolute test flags harmless rounding — three independently rounded
    # numbers routinely leave a 1-unit remainder.
    #
    # Known genuine exception: Cook Islands, where the source grids disagree by
    # 42% (total 2,947 vs male 2,765 + female 1,425). That is a defect in the
    # population assets, not in this script, and it is the only one worldwide —
    # globally the breakdown reconciles to within 0.0001%.
    keys = (["total_children"]
            + [f"{s['name']}_children" for s in SIGNALS]
            + [f"{s['name']}_children_enso" for s in SIGNALS])
    offenders = {}
    for r in rows:
        for k in keys:
            t = r.get(k) or 0
            if t < 100:          # tiny populations: rounding dominates
                continue
            d = (r.get(f"{k}_m") or 0) + (r.get(f"{k}_f") or 0) - t
            if abs(d) / t > 0.001:
                prev = offenders.get(r["country"], 0)
                if abs(d) > abs(prev):
                    offenders[r["country"]] = d
    gt = sum(r.get("total_children") or 0 for r in rows)
    gd = (sum(r.get("total_children_m") or 0 for r in rows)
          + sum(r.get("total_children_f") or 0 for r in rows) - gt)
    print(f"  boys + girls == total: global agreement within "
          f"{abs(gd) / gt * 100:.4f}%"
          + (f"; {len(offenders)} country/ies off by >0.1%: "
             f"{', '.join(sorted(offenders))}" if offenders else ""))
    if offenders:
        print("    (a defect in the source WorldPop grids, not this script — "
              "read those countries' breakdown with caution)")

    write_xlsx(rows, out, args.period, args.threshold,
               args.threshold_compound)
    print(f"\nWrote {out}")

    scored = [r for r in rows if r.get("dry_bss") is not None
              and r.get("dry_children", 0) > 0]
    scored.sort(key=lambda r: -r["dry_children"])
    print("\n  Largest dry-signal exposures, with reliability:")
    print(f'    {"country":22s} {"children":>12s} {"r":>7s} {"BSS":>7s} '
          f'{"anom mm":>8s}')
    for r in scored[:10]:
        print(f'    {r["country"][:21]:22s} {r["dry_children"]:>12,.0f} '
              f'{r["dry_r"]:>+7.2f} {r["dry_bss"]:>+7.2f} '
              f'{r["dry_anom"]:>+8.1f}')


if __name__ == "__main__":
    main()
