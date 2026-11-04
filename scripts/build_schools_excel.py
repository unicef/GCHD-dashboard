#!/usr/bin/env python
"""
build_schools_excel.py — one formatted Excel workbook per country from the
schools_multihazard.py CSVs (Summary, Overlap matrix, Schools per source, About).

    python scripts/build_schools_excel.py
    python scripts/build_schools_excel.py --iso3 ken
"""
import argparse
import glob
import os
import sys
from datetime import date
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "app"))

import pandas as pd                                                   # noqa: E402
from openpyxl import Workbook                                         # noqa: E402
from openpyxl.chart import BarChart, Reference                        # noqa: E402
from openpyxl.chart.marker import DataPoint                           # noqa: E402
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule      # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter as col                   # noqa: E402
from config import HAZARDS, HAZARD_TOPICS, MHC_EXCLUDED_TOPICS, TOPIC_COLORS  # noqa: E402
from _common import adm0_countries                                    # noqa: E402

IN_DIR = os.path.join(HERE, "out", "schools_multihazard")
OUT_DIR = os.path.join(IN_DIR, "excel")

SOURCES = {"giga": ("Giga", "https://maps.giga.global"),
           "mwater": ("mWater", "https://www.mwater.co")}

TOPICS = list(HAZARD_TOPICS)
CLIMATE = [t for t in TOPICS if t not in MHC_EXCLUDED_TOPICS]
HEALTH = ["Air Pollution", "Malaria"]
GEO = [t for t in TOPICS if t not in CLIMATE and t not in HEALTH]
ORDERED = CLIMATE + HEALTH + GEO
CATEGORY = {**{t: "Climate" for t in CLIMATE}, **{t: "Health & environment" for t in HEALTH},
            **{t: "Geophysical & conflict" for t in GEO}}

# (label, unit, dataset) per hazard layer, in config.HAZARDS order.
INDICATORS = {
    "river_flood_100yr_jrc_2024":            ("River flood depth, 100-yr return period", "m", "JRC (2024)"),
    "coastal_flood_100yr_jrc_2024":          ("Coastal flood extent, 100-yr return period", "in extent (1/0)", "JRC (2024)"),
    "tropical_storm_100yr_giri_2024":        ("Tropical storm wind speed, 100-yr return period", "m/s", "GIRI (2024)"),
    "agricultural_drought_fao_1984-2023":    ("Agricultural Stress Index, 100-yr return level", "%", "FAO (1984–2023)"),
    "drought_spei_terraclimate_1958-2025":   ("Drought probability (SPEI-12)", "probability", "TerraClimate (1958–2025)"),
    "drought_spi_terraclimate_1958-2025":    ("Drought probability (SPI-12)", "probability", "TerraClimate (1958–2025)"),
    "heatwave_frequency_ecmwf_2014-2024":    ("Heatwave frequency, 100-yr return level", "events/yr", "ECMWF ERA5 (2014–2024)"),
    "heatwave_duration_ecmwf_2014-2024":     ("Heatwave duration, 100-yr return level", "days", "ECMWF ERA5 (2014–2024)"),
    "heatwave_severity_ecmwf_2014-2024":     ("Heatwave severity, 100-yr return level", "°C", "ECMWF ERA5 (2014–2024)"),
    "extreme_heat_ecmwf_2014-2024":          ("Days above 35 °C, 100-yr return level", "days", "ECMWF ERA5 (2014–2024)"),
    "fire_FRP_nasa_2001-2024":               ("Fire radiative power, 90th percentile", "MW", "NASA FIRMS (2001–2024)"),
    "fire_frequency_nasa_2001-2023":         ("Fire frequency, 90th percentile", "fires/km²/yr", "NASA FIRMS (2001–2023)"),
    "sand_dust_storm_unccd_2024":            ("Sand and dust storm source/impact area", "index", "UNCCD (2024)"),
    "air_pollution_pm25_1998-2023":          ("PM2.5 concentration, 90th percentile", "µg/m³", "Satellite-derived PM2.5 (1998–2023)"),
    "vectorborne_malariapv_2012-2022":       ("Malaria P. vivax parasite rate", "rate", "Malaria Atlas Project (2012–2022)"),
    "vectorborne_malariapf_2012-2022":       ("Malaria P. falciparum parasite rate", "rate", "Malaria Atlas Project (2012–2022)"),
    "landslide_rainfall_worldbank_1980-2018": ("Rainfall-triggered landslide frequency", "events/km²/yr", "World Bank (1980–2018)"),
    "earthquake_pga_gem_2023":               ("Peak ground acceleration, 475-yr return period", "g", "GEM (2023)"),
    "volcanoes_gvp_1800-2025":               ("Within 100 km of a Holocene volcano", "buffer value", "Smithsonian GVP (1800–2025)"),
    "complex_emergencies_acled":             ("Conflict intensity index", "0–10", "ACLED"),
}
LAYERS = [h for h in HAZARDS if h["name"] in INDICATORS]

FONT = "Arial"
NAVY, TEAL, GREY, LIGHT = "1F3864", "0E7C86", "595959", "F2F5F9"
THIN = Side(style="thin", color="D9D9D9")
BOX = Border(bottom=THIN)


def f(size=10, bold=False, color="000000", italic=False):
    return Font(name=FONT, size=size, bold=bold, color=color, italic=italic)


def fill(hex_):
    return PatternFill("solid", start_color=hex_.lstrip("#"), end_color=hex_.lstrip("#"))


def text_on(hex_):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "FFFFFF"


def section(ws, row, title, span=7):
    c = ws.cell(row, 1, title.upper())
    c.font = f(11, True, "FFFFFF")
    for i in range(1, span + 1):
        ws.cell(row, i).fill = fill(NAVY)
    ws.row_dimensions[row].height = 20


def header(ws, row, values, start=1):
    for i, v in enumerate(values):
        c = ws.cell(row, start + i, v)
        c.font = f(10, True, NAVY)
        c.fill = fill(LIGHT)
        c.alignment = Alignment(horizontal="center" if i else "left", vertical="center", wrap_text=True)
        c.border = Border(bottom=Side(style="medium", color=NAVY))
    ws.row_dimensions[row].height = 30


# ── Schools sheet ────────────────────────────────────────────────────────────

def write_schools(wb, label, df):
    ws = wb.create_sheet(f"Schools ({label})")
    has_level = "education_level" in df.columns
    base = ["School ID", "School name"] + (["Education level"] if has_level else []) + \
           ["Longitude", "Latitude", "Climate hazards (count)", "All hazards (count)", "Hazards at school"]
    flag0 = len(base) + 1
    heads = base + ORDERED + [f"{INDICATORS[h['name']][0]} ({INDICATORS[h['name']][1]})" for h in LAYERS]
    n = len(df)
    last = n + 1
    fc = {t: col(flag0 + i) for i, t in enumerate(ORDERED)}
    clim_rng = f"{fc[CLIMATE[0]]}{{r}}:{fc[CLIMATE[-1]]}{{r}}"
    all_rng = f"{fc[ORDERED[0]]}{{r}}:{fc[ORDERED[-1]]}{{r}}"
    n_clim_col, n_all_col = col(len(base) - 2), col(len(base) - 1)

    for i, h in enumerate(heads, 1):
        c = ws.cell(1, i, h)
        c.font = f(9, True, NAVY)
        c.fill = fill(LIGHT)
        c.alignment = Alignment(wrap_text=True, vertical="bottom", horizontal="center" if i > 2 else "left")
        c.border = Border(bottom=Side(style="medium", color=NAVY))
    for t in ORDERED:
        c = ws[f"{fc[t]}1"]
        c.fill = fill(TOPIC_COLORS[t])
        c.font = f(9, True, text_on(TOPIC_COLORS[t]))
        c.alignment = Alignment(text_rotation=90, horizontal="center", vertical="bottom")
    ws.row_dimensions[1].height = 150

    flags = df[ORDERED].astype(int).values.tolist()
    vals = df[[h["name"] for h in LAYERS]].round(4).astype(object).where(
        df[[h["name"] for h in LAYERS]].notna(), None).values.tolist()
    for r, rec in enumerate(df.itertuples(index=False), start=2):
        fid, nm = getattr(rec, "facility_id", None), getattr(rec, "name", None)
        row = [str(fid) if pd.notna(fid) else None, nm if isinstance(nm, str) else None]
        if has_level:
            row.append(rec.education_level if isinstance(rec.education_level, str) else None)
        row += [round(rec.longitude, 6), round(rec.latitude, 6),
                f"=SUM({clim_rng.format(r=r)})", f"=SUM({all_rng.format(r=r)})",
                rec.hazards if isinstance(rec.hazards, str) else None]
        ws.append(row + flags[r - 2] + vals[r - 2])

    dot = '"●";;""'
    for t in ORDERED:
        for (c,) in ws[f"{fc[t]}2:{fc[t]}{last}"]:
            c.number_format = dot
            c.alignment = Alignment(horizontal="center")
            c.font = f(10, color=TOPIC_COLORS[t].lstrip("#"))
    for cl in (n_clim_col, n_all_col):
        for (c,) in ws[f"{cl}2:{cl}{last}"]:
            c.alignment = Alignment(horizontal="center")
        ws.conditional_formatting.add(f"{cl}2:{cl}{last}", ColorScaleRule(
            start_type="num", start_value=0, start_color="FFFFFF",
            end_type="max", end_color="C0504D"))

    widths = [14, 38] + ([16] if has_level else []) + [11, 11, 10, 10, 48] + \
             [6] * len(ORDERED) + [16] * len(LAYERS)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[col(i)].width = w
    ws.freeze_panes = ws.cell(2, 3)
    ws.auto_filter.ref = f"A1:{col(len(heads))}{last}"
    ws.sheet_properties.tabColor = TEAL

    sheet = f"'{ws.title}'"
    return {
        "sheet": sheet, "n": n, "last": last,
        "flag": {t: f"{sheet}!${fc[t]}$2:${fc[t]}${last}" for t in ORDERED},
        "n_clim": f"{sheet}!${n_clim_col}$2:${n_clim_col}${last}",
        "n_all": f"{sheet}!${n_all_col}$2:${n_all_col}${last}",
    }


# ── Summary sheet ────────────────────────────────────────────────────────────

def pct(num, den):
    return f"=IF({den}=0,0,{num}/{den})"


def write_summary(ws, country, srcs, data):
    """srcs: [(key, label)] primary first; data[key] from write_schools."""
    ncols = 3 + 2 * len(srcs)
    for c, w in zip("ABCDEFG", [7, 44, 22, 13, 11, 13, 11]):
        ws.column_dimensions[c].width = w
    ws.sheet_view.showGridLines = False

    ws["A1"] = f"{country}: schools exposed to climate and environmental hazards"
    ws["A1"].font = f(18, True, NAVY)
    src_txt = " · ".join(f"{lab}: {data[k]['n']:,} schools" for k, lab in srcs)
    ws["A2"] = f"School location datasets: {src_txt}  |  Analysis: {date.today():%B %Y}"
    ws["A2"].font = f(10, color=GREY, italic=True)

    totals = {}
    # Key figures first in layout terms (rows 10+), messages (rows 5-7) reference them.
    r = 9
    section(ws, r, "Key figures", ncols)
    r += 1
    heads = ["", "Indicator", ""]
    for _, lab in srcs:
        heads += [f"{lab}\nschools", f"{lab}\n%"]
    header(ws, r, heads)
    key_rows = [("Schools analysed", None, None),
                ("Exposed to at least 1 climate hazard", "n_clim", 1),
                ("Exposed to 2 or more climate hazards", "n_clim", 2),
                ("Exposed to 3 or more climate hazards", "n_clim", 3),
                ("Exposed to at least 1 hazard of any type", "n_all", 1),
                ("Exposed to 3 or more hazards of any type", "n_all", 3)]
    kref = {}
    for label, field, k in key_rows:
        r += 1
        ws.cell(r, 2, label).font = f(10, bold=field is None)
        for j, (key, _) in enumerate(srcs):
            nc, pc = 4 + 2 * j, 5 + 2 * j
            d = data[key]
            if field is None:
                ws.cell(r, nc, f"=COUNTA({d['n_clim']})")
                totals[key] = f"${col(nc)}${r}"
            else:
                ws.cell(r, nc, f'=COUNTIF({d[field]},">={k}")')
                ws.cell(r, pc, pct(f"{col(nc)}{r}", totals[key])).number_format = "0.0%"
            ws.cell(r, nc).number_format = "#,##0"
            ws.cell(r, nc).font = f(10, bold=field is None)
            ws.cell(r, pc).font = f(10, True, TEAL)
        kref[(field, k)] = r
        for i in range(1, ncols + 1):
            ws.cell(r, i).border = BOX

    # Hazard ranking
    r += 2
    section(ws, r, "Schools exposed, by hazard", ncols)
    r += 1
    heads = ["Rank", "Hazard", "Category"]
    for _, lab in srcs:
        heads += [f"{lab}\nschools", f"{lab}\n%"]
    header(ws, r, heads)
    prim = data[srcs[0][0]]
    ranked = [t for grp in (CLIMATE, HEALTH, GEO)
              for t in sorted(grp, key=lambda t: (-prim["counts"][t], ORDERED.index(t)))]
    rank_start = r + 1
    hazard_row = {}
    rank = 0
    for t in ranked:
        r += 1
        hazard_row[t] = r
        rank = 1 if rank == 0 or CATEGORY[t] != CATEGORY[ranked[ranked.index(t) - 1]] else rank + 1
        a = ws.cell(r, 1, rank)
        a.fill = fill(TOPIC_COLORS[t])
        a.font = f(10, True, text_on(TOPIC_COLORS[t]))
        a.alignment = Alignment(horizontal="center")
        ws.cell(r, 2, t).font = f(10, True)
        ws.cell(r, 3, CATEGORY[t]).font = f(9, color=GREY)
        for j, (key, _) in enumerate(srcs):
            nc, pc = 4 + 2 * j, 5 + 2 * j
            ws.cell(r, nc, f"=SUM({data[key]['flag'][t]})").number_format = "#,##0"
            ws.cell(r, pc, pct(f"{col(nc)}{r}", totals[key])).number_format = "0.0%"
            ws.cell(r, nc).font = f(10)
            ws.cell(r, pc).font = f(10)
        for c in range(1, ncols + 1):
            ws.cell(r, c).border = BOX
    rank_end = r
    for j in range(len(srcs)):
        pc = col(5 + 2 * j)
        ws.conditional_formatting.add(f"{pc}{rank_start}:{pc}{rank_end}", DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color="5B9BD5"))

    # Distribution
    r += 2
    section(ws, r, "Schools by number of overlapping climate hazards", ncols)
    r += 1
    heads = ["", "Climate hazards at the school", ""]
    for _, lab in srcs:
        heads += [f"{lab}\nschools", f"{lab}\n%"]
    header(ws, r, heads)
    dist_start = r + 1
    for k in range(len(CLIMATE) + 1):
        r += 1
        ws.cell(r, 1, k).alignment = Alignment(horizontal="center")
        ws.cell(r, 1).font = f(10, True)
        ws.cell(r, 2, "None" if k == 0 else f"{k} climate hazard{'s' if k > 1 else ''}").font = f(10)
        for j, (key, _) in enumerate(srcs):
            nc, pc = 4 + 2 * j, 5 + 2 * j
            ws.cell(r, nc, f"=COUNTIF({data[key]['n_clim']},{k})").number_format = "#,##0"
            ws.cell(r, pc, pct(f"{col(nc)}{r}", totals[key])).number_format = "0.0%"
            ws.cell(r, nc).font = f(10)
            ws.cell(r, pc).font = f(10)
        for c in range(1, ncols + 1):
            ws.cell(r, c).border = BOX
    dist_end = r
    for j in range(len(srcs)):
        pc = col(5 + 2 * j)
        ws.conditional_formatting.add(f"{pc}{dist_start}:{pc}{dist_end}", DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color="C0504D"))

    # Pairs
    r += 2
    section(ws, r, "Most common climate hazard combinations", ncols)
    r += 1
    heads = ["Rank", "Hazard pair", ""]
    for _, lab in srcs:
        heads += [f"{lab}\nschools", f"{lab}\n%"]
    header(ws, r, heads)
    pairs = sorted(((a, b) for a, b in combinations(CLIMATE, 2) if prim["pairs"][(a, b)] > 0),
                   key=lambda p: -prim["pairs"][p])[:10]
    for i, (a, b) in enumerate(pairs, 1):
        r += 1
        ws.cell(r, 1, i).alignment = Alignment(horizontal="center")
        ws.cell(r, 2, f"{a} + {b}").font = f(10)
        for j, (key, _) in enumerate(srcs):
            nc, pc = 4 + 2 * j, 5 + 2 * j
            d = data[key]
            ws.cell(r, nc, f"=COUNTIFS({d['flag'][a]},1,{d['flag'][b]},1)").number_format = "#,##0"
            ws.cell(r, pc, pct(f"{col(nc)}{r}", totals[key])).number_format = "0.0%"
        for c in range(1, ncols + 1):
            ws.cell(r, c).border = BOX
    if not pairs:
        r += 1
        ws.cell(r, 2, "No school is exposed to two climate hazards at once.").font = f(10, italic=True)

    r += 2
    notes = ["Exposure = the school's location falls inside the hazard footprint (value above the threshold listed in 'About').",
             "Giga and mWater are independent school datasets that may list the same schools: compare them, do not add them.",
             "Climate hazards: " + ", ".join(CLIMATE) + ". See 'About' for methods, thresholds and sources."]
    if any(data[k]["n"] < 100 for k, _ in srcs):
        small = ", ".join(f"{lab} ({data[k]['n']:,})" for k, lab in srcs if data[k]["n"] < 100)
        notes.insert(0, f"Small sample: {small} schools. Figures are indicative, not nationally representative.")
    for n in notes:
        ws.cell(r, 1, n).font = f(9, color=GREY, italic=True)
        r += 1

    # Key messages (rows 4-7), referencing the primary source.
    section(ws, 4, f"Key messages ({srcs[0][1]} schools)", ncols)
    tot = totals[srcs[0][0]]
    k2, k3 = kref[("n_clim", 2)], kref[("n_clim", 3)]
    top3 = [t for t in ranked if t in CLIMATE][:3]
    top_txt = '&", "&'.join(f'B{hazard_row[t]}&" ("&TEXT(E{hazard_row[t]},"0%")&")"' for t in top3)
    msgs = [f'=TEXT(D{k2},"#,##0")&" of "&TEXT({tot},"#,##0")&" schools ("&TEXT(E{k2},"0%")&") are exposed to two or more climate hazards."',
            f'=TEXT(D{k3},"#,##0")&" schools ("&TEXT(E{k3},"0%")&") face three or more climate hazards at the same location."',
            f'="Most widespread climate hazards: "&{top_txt}&"."']
    for i, m in enumerate(msgs):
        c = ws.cell(5 + i, 1, m)
        c.font = f(12, True, NAVY if i else "C0504D")
        ws.merge_cells(start_row=5 + i, start_column=1, end_row=5 + i, end_column=ncols)
        ws.row_dimensions[5 + i].height = 20

    # Charts (primary source); titles live in cells, chart titles overlap the axes.
    cc = ncols + 2
    ws.cell(9, cc, f"% of schools exposed, by hazard ({srcs[0][1]})").font = f(11, True, NAVY)
    ch = BarChart()
    ch.type = "bar"
    ch.add_data(Reference(ws, min_col=5, min_row=rank_start, max_row=rank_end), titles_from_data=False)
    ch.set_categories(Reference(ws, min_col=2, min_row=rank_start, max_row=rank_end))
    ch.y_axis.number_format = "0%"
    ch.y_axis.scaling.min, ch.y_axis.scaling.max = 0, 1
    ch.x_axis.scaling.orientation = "maxMin"
    ch.y_axis.crosses = "max"
    ch.y_axis.majorGridlines = None
    ch.legend = None
    ch.gapWidth = 40
    for i, t in enumerate(ranked):
        pt = DataPoint(idx=i)
        pt.graphicalProperties.solidFill = TOPIC_COLORS[t].lstrip("#")
        ch.series[0].dPt.append(pt)
    ch.height, ch.width = 11, 17
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ws.add_chart(ch, f"{col(cc)}10")

    ws.cell(33, cc, f"Schools by number of overlapping climate hazards ({srcs[0][1]})").font = f(11, True, NAVY)
    ch2 = BarChart()
    ch2.add_data(Reference(ws, min_col=4, min_row=dist_start, max_row=dist_end), titles_from_data=False)
    ch2.set_categories(Reference(ws, min_col=1, min_row=dist_start, max_row=dist_end))
    ch2.legend = None
    ch2.gapWidth = 40
    ch2.series[0].graphicalProperties.solidFill = "C0504D"
    ch2.y_axis.number_format = "#,##0"
    ch2.y_axis.majorGridlines = None
    ch2.height, ch2.width = 8, 17
    ch2.x_axis.delete = False
    ch2.y_axis.delete = False
    ws.add_chart(ch2, f"{col(cc)}34")

    ws.freeze_panes = "A4"
    ws.sheet_properties.tabColor = NAVY


# ── Overlap matrix ───────────────────────────────────────────────────────────

def write_matrix(wb, srcs, data):
    ws = wb.create_sheet("Overlap matrix")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Hazard overlap: number of schools exposed to both hazards"
    ws["A1"].font = f(14, True, NAVY)
    ws["A2"] = "Each cell counts schools exposed to both the row and the column hazard; the diagonal is the total for that hazard."
    ws["A2"].font = f(9, color=GREY, italic=True)
    ws.column_dimensions["A"].width = 24
    r = 4
    for key, lab in srcs:
        d = data[key]
        ws.cell(r, 1, f"{lab} ({d['n']:,} schools)").font = f(11, True, NAVY)
        r += 1
        for j, t in enumerate(ORDERED, 2):
            c = ws.cell(r, j, t)
            c.fill = fill(TOPIC_COLORS[t])
            c.font = f(9, True, text_on(TOPIC_COLORS[t]))
            c.alignment = Alignment(text_rotation=90, horizontal="center", vertical="bottom")
            ws.column_dimensions[col(j)].width = 8
        ws.row_dimensions[r].height = 110
        top = r + 1
        for t in ORDERED:
            r += 1
            c = ws.cell(r, 1, t)
            c.font = f(9, True, text_on(TOPIC_COLORS[t]))
            c.fill = fill(TOPIC_COLORS[t])
            for j, u in enumerate(ORDERED, 2):
                v = (f"=SUM({d['flag'][t]})" if t == u
                     else f"=COUNTIFS({d['flag'][t]},1,{d['flag'][u]},1)")
                cell = ws.cell(r, j, v)
                cell.number_format = "#,##0;;\"–\""
                cell.alignment = Alignment(horizontal="center")
                cell.font = f(9, bold=t == u)
                cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        rng = f"B{top}:{col(len(ORDERED) + 1)}{r}"
        ws.conditional_formatting.add(rng, ColorScaleRule(
            start_type="num", start_value=0, start_color="FFFFFF",
            mid_type="percentile", mid_value=50, mid_color="FCD5B4",
            end_type="max", end_color="C0504D"))
        r += 3
    ws.freeze_panes = "B4"


# ── About ────────────────────────────────────────────────────────────────────

def write_about(wb, srcs, data):
    ws = wb.create_sheet("About")
    ws.sheet_view.showGridLines = False
    for c, w in zip("ABCDEF", [24, 50, 14, 16, 34, 60]):
        ws.column_dimensions[c].width = w
    ws["A1"] = "About this analysis"
    ws["A1"].font = f(14, True, NAVY)
    lines = [
        "Each school is located on the map and checked against 14 hazard groups built from 20 global hazard layers.",
        "A school is counted as exposed to a hazard group when the value at its location exceeds the threshold for any layer in that group.",
        "No population data is used: the result describes school sites, not the number of pupils.",
        "Thresholds are the defaults of the UNICEF Global Climate Hazard Dashboard (GCHD), so results are consistent with it.",
        "Schools in cells with no hazard data are counted as not exposed (e.g. PM2.5 has no coverage over some coastal cities).",
        "Giga and mWater are independent datasets with different coverage; they may overlap and should not be summed.",
    ]
    for i, t in enumerate(lines, 3):
        ws.cell(i, 1, f"• {t}").font = f(10)
    r = len(lines) + 4
    ws.cell(r, 1, "School datasets").font = f(11, True, NAVY)
    for key, lab in srcs:
        r += 1
        ws.cell(r, 1, lab).font = f(10, True)
        ws.cell(r, 2, f"{data[key]['n']:,} schools — {SOURCES[key][1]}").font = f(10)
    r += 2
    header(ws, r, ["Hazard group", "Indicator", "Threshold (exposed if above)", "Unit", "Dataset", "Category"])
    for t in ORDERED:
        for h in LAYERS:
            if h["name"] not in HAZARD_TOPICS[t]:
                continue
            r += 1
            label, unit, ds = INDICATORS[h["name"]]
            ws.cell(r, 1, t).font = f(10, True)
            ws.cell(r, 1).fill = fill(TOPIC_COLORS[t])
            ws.cell(r, 1).font = f(10, True, text_on(TOPIC_COLORS[t]))
            ws.cell(r, 2, label).font = f(10)
            thr = ws.cell(r, 3, h["threshold"])
            thr.font = f(10)
            thr.alignment = Alignment(horizontal="center")
            ws.cell(r, 4, unit).font = f(10)
            ws.cell(r, 5, ds).font = f(10)
            ws.cell(r, 6, CATEGORY[t]).font = f(10, color=GREY)
            for c in range(1, 7):
                ws.cell(r, c).border = BOX
    r += 2
    ws.cell(r, 1, f"Generated {date.today():%d %B %Y} by scripts/schools_multihazard.py and build_schools_excel.py.").font = f(9, color=GREY, italic=True)
    ws.sheet_properties.tabColor = "A6A6A6"


# ── Driver ───────────────────────────────────────────────────────────────────

def build(iso3, country):
    srcs, frames = [], {}
    for key, (lab, _) in SOURCES.items():
        path = os.path.join(IN_DIR, f"{iso3}_{key}_schools_hazards.csv")
        if os.path.exists(path):
            frames[key] = pd.read_csv(path, low_memory=False)
            srcs.append((key, lab))
    if not srcs:
        return None
    srcs.sort(key=lambda s: -len(frames[s[0]]))

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    data = {}
    for key, lab in srcs:
        df = frames[key]
        d = write_schools(wb, lab, df)
        d["counts"] = {t: int(df[t].sum()) for t in ORDERED}
        d["pairs"] = {(a, b): int((df[a].astype(bool) & df[b].astype(bool)).sum())
                      for a, b in combinations(CLIMATE, 2)}
        data[key] = d
    write_summary(summary, country, srcs, data)
    write_matrix(wb, srcs, data)
    write_about(wb, srcs, data)
    wb.move_sheet("Overlap matrix", offset=-(len(srcs)))
    wb.calculation.fullCalcOnLoad = True

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{iso3.upper()}_schools_hazard_exposure.xlsx")
    wb.save(out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--iso3", action="append")
    args = ap.parse_args()
    names = adm0_countries()
    found = sorted({os.path.basename(p)[:3] for p in glob.glob(os.path.join(IN_DIR, "*_schools_hazards.csv"))})
    for iso3 in [c.lower() for c in (args.iso3 or found)]:
        out = build(iso3, names.get(iso3.upper(), iso3.upper()))
        print(f"{iso3.upper()}: {out or 'no CSVs — skipped'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
