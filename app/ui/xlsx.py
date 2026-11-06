"""Styled xlsx builder shared by the analysis exports."""
from config.hazards import HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS

from ui.components import _hazard_label


# ── Compute (dispatches on mode) ──────────────────────────────────────────────

# ── Styled Excel (.xlsx) export ───────────────────────────────────────────────
# Pastel exposed/non-exposed cell fills for 0/1 site-flag columns.
_XLSX_GREEN = "#E2F0D9"   # 0 = not exposed
_XLSX_RED   = "#F8CBAD"   # 1 = exposed
_XLSX_HDR   = "#1CABE2"   # header fill (UNICEF cyan)
_XLSX_GRP   = "#D9EEF7"   # group-title fill (light cyan)
_XLSX_MIME  = ("application/vnd.openxmlformats-officedocument."
               "spreadsheetml.sheet")


def _thresholds_banner(topics, overrides):
    """One text line per selected topic's hazards: 'Hazard > value unit (default|
    custom)'. Reused by every export banner and the panel note."""
    lines = []
    for t in topics or []:
        for h in HAZARD_TOPICS.get(t, []):
            hz      = HAZARD_MAP.get(h, {})
            default = hz.get("threshold")
            if default is None:
                continue
            thr    = (overrides or {}).get(h, default)
            units  = (HAZARD_INFO.get(h) or {}).get("units", "")
            tag    = "custom" if h in (overrides or {}) else "default"
            unit_s = f" {units}" if units else ""
            op     = "<" if thr < 0 else ">"
            lines.append(f"{_hazard_label(h)} {op} {thr:g}{unit_s} ({tag})")
    return lines


def build_xlsx(sheet_name, groups, rows, banner_lines=None):
    """Build a styled .xlsx workbook and return a base64 data-URI for an <a> link.

    `groups`: ordered [{title, columns:[{key,label,kind}], color?}]. `kind` is one
    of 'flag' (0/1 -> pastel red/green), 'int', 'float', 'text'. `rows`: list of
    dicts keyed by column 'key'. `banner_lines`: optional text lines shown merged
    across the top (e.g. title + thresholds used).
    """
    import io, base64
    import xlsxwriter

    flat_cols = [c for g in groups for c in g["columns"]]
    ncols = len(flat_cols)

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws  = wb.add_worksheet(sheet_name[:31] or "Sheet1")

    f_banner   = wb.add_format({"bold": True, "font_size": 11, "align": "left",
                                "valign": "vcenter", "text_wrap": False})
    f_note     = wb.add_format({"font_size": 9, "font_color": "#595959",
                                "align": "left", "valign": "vcenter"})
    f_grp      = wb.add_format({"bold": True, "align": "center", "valign": "vcenter",
                                "bg_color": _XLSX_GRP, "border": 1})
    f_hdr      = wb.add_format({"bold": True, "align": "center", "valign": "vcenter",
                                "bg_color": _XLSX_HDR, "font_color": "white",
                                "border": 1, "text_wrap": True})
    f_text     = wb.add_format({"border": 1})
    f_int      = wb.add_format({"border": 1, "num_format": "#,##0"})
    f_float    = wb.add_format({"border": 1, "num_format": "0.####"})
    f_flag0    = wb.add_format({"border": 1, "align": "center",
                                "bg_color": _XLSX_GREEN})
    f_flag1    = wb.add_format({"border": 1, "align": "center",
                                "bg_color": _XLSX_RED, "bold": True})

    r = 0
    # Banner rows (merged across all columns).
    for i, line in enumerate(banner_lines or []):
        ws.merge_range(r, 0, r, ncols - 1, line,
                       f_banner if i == 0 else f_note)
        r += 1
    if banner_lines:
        r += 1  # spacer

    # Group-title row (merged per group) + column-label row.
    grp_row, hdr_row = r, r + 1
    c = 0
    for g in groups:
        span = len(g["columns"])
        if span == 1:
            ws.write(grp_row, c, g["title"], f_grp)
        else:
            ws.merge_range(grp_row, c, grp_row, c + span - 1, g["title"], f_grp)
        for col in g["columns"]:
            ws.write(hdr_row, c, col["label"], f_hdr)
            c += 1
    data_row0 = hdr_row + 1

    # Data rows.
    fmt_for = {"int": f_int, "float": f_float, "text": f_text}
    for ri, row in enumerate(rows):
        c = 0
        for col in flat_cols:
            v = row.get(col["key"], "")
            kind = col["kind"]
            if kind == "flag":
                iv = int(v) if v not in ("", None) else 0
                ws.write(data_row0 + ri, c, iv, f_flag1 if iv else f_flag0)
            elif kind in ("int", "float"):
                ws.write(data_row0 + ri, c,
                         v if v not in ("", None) else "", fmt_for[kind])
            else:
                ws.write(data_row0 + ri, c, "" if v is None else v, f_text)
            c += 1

    # Column widths + freeze + autofilter.
    c = 0
    for col in flat_cols:
        width = 22 if col["kind"] == "text" else (max(len(col["label"]) + 2, 10))
        ws.set_column(c, c, width)
        c += 1
    ws.freeze_panes(data_row0, 0)
    if rows:
        ws.autofilter(hdr_row, 0, data_row0 + len(rows) - 1, ncols - 1)

    wb.close()
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{_XLSX_MIME};base64,{b64}"


# ── Report-style workbook (one region's result, read top to bottom) ───────────
_XLSX_TILE = "#F3F9FC"
_XLSX_BAR  = "#F4A6A6"
# A = colour swatch, B = row label, C-D = figures, E = last figure or text.
_REPORT_WIDTHS = [2.5, 36, 17, 17, 46]


def build_report_xlsx(sheet_name, title, subtitle, blocks):
    """Single-sheet summary workbook as a base64 data-URI. Tables take up to
    three value columns (C-E); a text column belongs in E, the wide one.

    `blocks`, in reading order:
      {"kind": "metrics", "items": [{label, value, fmt ('int'|'pct'), color?}]}
          up to 4 headline tiles across columns B-E.
      {"kind": "table", "title", "columns": [{label, fmt ('int'|'pct'|'text')}],
       "rows": [{label, values, swatch?, indent?, muted?}], "note"?}
          'pct' columns get an in-cell bar; values are fractions.
      {"kind": "list", "title", "items": [(label, text)]}
      {"kind": "note", "text"}
    """
    import io, base64
    import xlsxwriter

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws  = wb.add_worksheet(sheet_name[:31] or "Sheet1")
    for i, w in enumerate(_REPORT_WIDTHS):
        ws.set_column(i, i, w)
    ws.hide_gridlines(2)
    last = len(_REPORT_WIDTHS) - 1

    fmt = lambda **k: wb.add_format(k)
    f_title = fmt(bold=True, font_size=15, font_color=_XLSX_HDR)
    f_sub   = fmt(font_size=9, font_color="#595959")
    f_h2    = fmt(bold=True, font_size=11, font_color="#374151", bottom=2,
                  bottom_color=_XLSX_HDR)
    f_hdr   = fmt(bold=True, align="center", valign="vcenter", bg_color=_XLSX_HDR,
                  font_color="white", border=1, border_color="#D1D5DB",
                  text_wrap=True)
    f_hdr_l = fmt(bold=True, valign="vcenter", bg_color=_XLSX_HDR,
                  font_color="white", border=1, border_color="#D1D5DB")
    f_note  = fmt(font_size=9, font_color="#595959", text_wrap=True, valign="top")
    f_klbl  = fmt(bold=True, bg_color=_XLSX_GRP, border=1, border_color="#D1D5DB",
                  valign="top")
    f_kval  = fmt(border=1, border_color="#D1D5DB", text_wrap=True, valign="top")

    def cell(kind, bold=False, muted=False):
        k = {"border": 1, "border_color": "#D1D5DB", "valign": "vcenter"}
        if kind == "int":
            k["num_format"] = "#,##0"
        elif kind == "pct":
            k["num_format"] = "0.0%"
        if bold:
            k["bold"] = True
        if muted:
            k["font_color"] = "#6B7280"
        return wb.add_format(k)

    ws.write(0, 1, title, f_title)
    ws.set_row(0, 22)
    ws.write(1, 1, subtitle, f_sub)
    r = 3

    for b in blocks:
        kind = b["kind"]
        if kind == "metrics":
            for i, it in enumerate(b["items"][:4]):
                c = 1 + i
                ws.write(r, c, it["label"],
                         fmt(font_size=9, font_color="#595959", bg_color=_XLSX_TILE,
                             top=1, left=1, right=1, border_color="#D1D5DB"))
                num = "0.0%" if it.get("fmt") == "pct" else "#,##0"
                v = it.get("value")
                ws.write(r + 1, c, "—" if v is None else v,
                         fmt(bold=True, font_size=16, num_format=num, align="left",
                             font_color=it.get("color") or "#111827",
                             bg_color=_XLSX_TILE, bottom=1, left=1, right=1,
                             border_color="#D1D5DB"))
            ws.set_row(r + 1, 26)
            r += 3

        elif kind == "table":
            cols = b["columns"]
            ws.merge_range(r, 1, r, last, b["title"], f_h2)
            r += 1
            ws.write(r, 0, "", f_hdr)
            ws.write(r, 1, b.get("label_header", ""), f_hdr_l)
            for i, col in enumerate(cols):
                ws.write(r, 2 + i, col["label"], f_hdr)
            ws.set_row(r, 30)
            r += 1
            first = r
            for row in b["rows"]:
                bold, muted = not row.get("indent"), bool(row.get("muted"))
                sw = row.get("swatch")
                ws.write(r, 0, "", fmt(bg_color=sw, border=1, border_color="#D1D5DB")
                         if sw else cell("text"))
                label = ("    └ " if row.get("indent") else "") + row["label"]
                ws.write(r, 1, label, cell("text", bold=bold, muted=muted))
                for i, col in enumerate(cols):
                    v = row["values"][i] if i < len(row["values"]) else None
                    ws.write(r, 2 + i, "—" if v is None else v,
                             cell(col["fmt"], bold=bold and col["fmt"] != "text",
                                  muted=muted))
                r += 1
            for i, col in enumerate(cols):
                if col["fmt"] == "pct" and r > first:
                    ws.conditional_format(first, 2 + i, r - 1, 2 + i, {
                        "type": "data_bar", "bar_color": _XLSX_BAR,
                        "bar_solid": True, "bar_no_border": True,
                        "min_type": "num", "min_value": 0,
                        "max_type": "num", "max_value": 1})
            if b.get("note"):
                ws.merge_range(r, 1, r, last, b["note"], f_note)
                ws.set_row(r, 15 * (len(b["note"]) // 140 + 1))
                r += 1
            r += 1

        elif kind == "list":
            ws.merge_range(r, 1, r, last, b["title"], f_h2)
            r += 1
            for label, text in b["items"]:
                ws.write(r, 1, label, f_klbl)
                ws.merge_range(r, 2, r, last, text, f_kval)
                ws.set_row(r, 15 * (len(str(text)) // 95 + 1))
                r += 1
            r += 1

        elif kind == "note":
            ws.merge_range(r, 1, r, last, b["text"], f_note)
            ws.set_row(r, 15 * (len(b["text"]) // 140 + 1))
            r += 2

    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 0)
    wb.close()
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{_XLSX_MIME};base64,{b64}"


def report_download_links(xlsx_uri, xlsx_name, json_text, json_name):
    """Excel (primary) + JSON side by side, under a result panel."""
    from urllib.parse import quote
    from dash import html
    style = {"textAlign": "center", "textDecoration": "none",
             "padding": "9px 12px", "flex": "1"}
    return html.Div(className="ps", style={"display": "flex", "gap": "8px"},
                    children=[
        html.A("⬇  Excel", href=xlsx_uri, download=xlsx_name,
               className="ps-btn", style=style),
        html.A("⬇  JSON", href="data:application/json;charset=utf-8," + quote(json_text),
               download=json_name, className="ps-btn-ghost", style=style),
    ])
