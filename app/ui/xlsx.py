"""Styled xlsx builder shared by the analysis exports."""
from config import HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS

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
    """One text line per selected topic's hazards: 'Hazard >= value unit (default|
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
            lines.append(f"{_hazard_label(h)} >= {thr:g}{unit_s} ({tag})")
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
