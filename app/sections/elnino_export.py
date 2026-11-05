"""El Niño xlsx/json export and download gate."""
import dash
import datetime as dt
import json as _json
import re
from dash import ctx, dcc, html, Input, no_update, Output, State
from elnino_config import (
    CAVEATS, CHANCE_LEVEL, COMPOUND_CHANCE_LEVEL, download_disclaimer,
    HINDCAST, is_compound, METHOD_STEPS, period_issue_label, period_label,
    PERIOD_MAP, SIGNAL_MAP, SKILL_REFERENCE, SKILL_USABLE_MIN,
    SYSTEM as ELNINO_SYSTEM,
)

from dash_app import app
from ui.xlsx import _XLSX_GRP, _XLSX_HDR


# Pastel ramp for the sensitivity table, matching the one the offline
# reliability workbook uses so a dashboard download and a script-produced
# workbook look like the same family of document.
_XLSX_PASTEL = ["#F4C7C3", "#FADBC8", "#FDF3C8", "#DDEFD5", "#CCE5D9"]


def build_elnino_xlsx(meta, result, history, export):
    """Multi-sheet formatted workbook for one computed El Nino region.

    Deliberately NOT an extension of build_xlsx: that helper writes a single
    grid and is used by two other tabs, and widening its contract to carry
    prose sheets would put their formatting at risk for no gain. The styling
    vocabulary (cyan headers, pastel fills) is shared by reading the same
    module constants.

    Sheet order is the reading order: caveats first, because a number lifted
    out of this file without them is the failure mode the whole module is
    written against.
    """
    import io
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    # Excel percent formats: cells hold fractions (via _frac).
    PCT = "0.0%"
    PCT0 = "0%"
    PCT_SIGNED = "+0.0%;-0.0%;0.0%"

    f_title = wb.add_format({"bold": True, "font_size": 13})
    f_h2    = wb.add_format({"bold": True, "font_size": 11,
                             "font_color": "#1CABE2"})
    f_hdr   = wb.add_format({"bold": True, "align": "center",
                             "valign": "vcenter", "bg_color": _XLSX_HDR,
                             "font_color": "white", "border": 1,
                             "text_wrap": True})
    f_lbl   = wb.add_format({"bold": True, "border": 1, "bg_color": _XLSX_GRP,
                             "valign": "top"})
    f_txt   = wb.add_format({"border": 1, "text_wrap": True, "valign": "top"})
    f_int   = wb.add_format({"border": 1, "num_format": "#,##0"})
    f_pct   = wb.add_format({"border": 1, "num_format": PCT})
    f_spct  = wb.add_format({"border": 1, "num_format": PCT_SIGNED})
    f_year  = wb.add_format({"border": 1, "num_format": "0"})
    f_sdec  = wb.add_format({"border": 1, "num_format": "+0.0;-0.0;0.0"})
    f_wrap  = wb.add_format({"text_wrap": True, "valign": "top"})
    f_note  = wb.add_format({"font_size": 9, "font_color": "#595959",
                             "text_wrap": True, "valign": "top"})
    f_quote = wb.add_format({"text_wrap": True, "valign": "top",
                             "bg_color": "#FDF3C8", "border": 1,
                             "border_color": "#E0C96B"})

    sig = SIGNAL_MAP.get(meta["signal"], {})
    anoms = meta.get("anoms") or {}
    attrib = meta.get("attrib") or {}
    skill = meta.get("skill") or {}

    def _frac(v):
        """Percent -> fraction for Excel's % number format, or None."""
        return None if v is None else v / 100.0

    def _pct(part, whole):
        if part is None or not whole:
            return None
        return part / whole * 100.0

    sig_label = sig.get("label", meta["signal"])
    level_txt = re.sub(r"^adm\d\s*\((.*)\)$", r"\1", meta["level"] or "")
    pop_txt = (f"WorldPop 2025, children under 18, "
               f"{meta.get('pop_res', 1000):,} m grid")
    generated = export.get("computed_utc", "")
    issue = period_issue_label(meta["period"])

    def _print_setup(ws, header_rows=None):
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_paper(9)                       # A4
        if header_rows is not None:
            ws.repeat_rows(header_rows)
            ws.freeze_panes(header_rows + 1, 0)

    # -- 1. Read me first --------------------------------------------------
    ws = wb.add_worksheet("Read me first")
    ws.set_column(0, 0, 112)
    disclaimer = export.get("disclaimer") or download_disclaimer(
        period_label(meta["period"]), period_issue_label(meta["period"]))
    ws.write(0, 0, disclaimer[0], f_title)
    r = 1
    for c in disclaimer[1:]:
        ws.write(r, 0, "• " + c, f_wrap)
        ws.set_row(r, max(14, 13 * (len(c) // 95 + 1)))
        r += 1
    if export.get("disclaimer_accepted_utc"):
        ws.write(r, 0, "Accepted by the person downloading this file on "
                       f"{export['disclaimer_accepted_utc']}.", f_note)
        r += 1
    quote = export.get("suggested_citation")
    if quote:
        r += 1
        ws.write(r, 0, "Suggested wording when quoting this figure", f_h2)
        ws.write(r + 1, 0, quote, f_quote)
        ws.set_row(r + 1, 15 * (len(quote) // 100 + 2))
        r += 2
    r += 1
    ws.write(r, 0, "Read this before quoting any number in this file", f_title)
    r += 2
    for c in CAVEATS:
        ws.write(r, 0, "• " + c, f_wrap)
        ws.set_row(r, max(14, 13 * (len(c) // 95 + 1)))
        r += 1
    r += 1
    ws.write(r, 0, f"Forecast: ECMWF SEAS5 system {ELNINO_SYSTEM}, issued "
                   f"{issue}, compared against its own {HINDCAST} "
                   f"hindcast. Population: {pop_txt}. "
                   f"Generated {generated}.", f_note)
    _print_setup(ws)

    # -- 2. Summary --------------------------------------------------------
    ws = wb.add_worksheet("Summary")
    ws.set_column(0, 0, 40)
    ws.set_column(1, 1, 26)
    ws.set_column(2, 2, 70)
    ws.write(0, 0, f"{meta['region']} – {sig_label} – "
                   f"{period_label(meta['period'])}", f_title)
    ws.write(1, 0, "Children living where the seasonal forecast leans "
                   "towards this outcome. A forecast, not a measured impact.",
             f_note)
    chance = export.get("chance_level_pct")
    rows = [
        ("WHAT WAS ASKED", None, None, None),
        ("Region", meta["region"], f_txt, None),
        ("Administrative level", level_txt, f_txt, None),
        ("Period", period_label(meta["period"]), f_txt, None),
        ("Signal", sig_label, f_txt, sig.get("blurb")),
        ("Probability threshold", _frac(meta["threshold"]), f_pct,
         "Share of the 51 forecast ensemble members that must agree "
         "before an area counts."),
        ("No-information baseline", _frac(chance), f_pct,
         "What the probability would be by chance alone. The threshold "
         "should sit well above it."),
        ("Arid areas excluded", "yes" if meta["dry_mask"] else "no", f_txt,
         "Areas too dry in a normal season for 'drier' or 'wetter' to mean "
         "anything are left out of the count."),
        ("Forecast issued", issue, f_txt,
         f"ECMWF SEAS5 system {ELNINO_SYSTEM}; normal = its own {HINDCAST} "
         f"hindcast."),
        ("Population data", pop_txt, f_txt, None),
        ("THE ANSWER", None, None, None),
        ("Children in signal area", export.get("children_exposed"), f_int,
         "Children (under 18) living in areas that meet the threshold."),
        ("Total children in region", export.get("total_children"), f_int,
         None),
        ("Share of children", _frac(export.get("pct_children_exposed")),
         f_pct, "Children in signal area ÷ total children in region."),
        ("   of which boys", export.get("boys_exposed"), f_int,
         "Boys under 18 in the signal area."),
        ("   of which girls", export.get("girls_exposed"), f_int,
         "Girls under 18 in the signal area."),
        ("Share of boys", _frac(_pct(export.get("boys_exposed"),
                                     export.get("total_boys"))), f_pct,
         "Boys in signal area ÷ all boys in region."),
        ("Share of girls", _frac(_pct(export.get("girls_exposed"),
                                      export.get("total_girls"))), f_pct,
         "Girls in signal area ÷ all girls in region."),
        ("Children in excluded arid areas",
         export.get("children_in_excluded_arid_areas"), f_int,
         "Not counted above, and not evidence of 'no signal'."),
        ("HOW UNUSUAL", None, None, None),
        ("Rainfall anomaly (mm vs model normal)", anoms.get("anom_mm"),
         f_sdec, "Forecast minus normal season rainfall, averaged over the "
                 "signal area."),
        ("Rainfall anomaly (% of normal)", _frac(anoms.get("anom_pct")),
         f_spct, None),
        ("Temperature anomaly (°C vs model normal)", anoms.get("anom_c"),
         f_sdec, "Forecast minus normal mean temperature, averaged over the "
                 "signal area."),
        ("ATTRIBUTION", None, None, None),
        ("Children in El Niño-consistent areas", attrib.get("children"),
         f_int, "Children in signal areas where El Niño has historically "
                "pushed rainfall in the same direction."),
        ("Share of signal attributable", _frac(attrib.get("pct")), f_pct,
         "Of the children in the signal area. The rest may be unusual for "
         "other reasons. Consistency with El Niño is not proof of cause."),
        ("RELIABILITY", None, None, None),
        ("Signal area with usable skill", _frac(skill.get("usable_pct")),
         f_pct, f"Share of the signal area where the model's past forecasts "
                f"correlated with {SKILL_REFERENCE} observations at "
                f"r ≥ {SKILL_USABLE_MIN} ({HINDCAST}). A generous measure."),
        ("Signal area that could be scored",
         _frac(skill.get("assessed_pct")), f_pct,
         f"{SKILL_REFERENCE} covers land between 50°N and 50°S only."),
    ]
    ws.write(2, 2, "What it means", f_h2)
    r = 2
    for label, val, fmt, note in rows:
        if val is None and fmt is None:          # section heading
            ws.write(r, 0, label, f_h2)
            r += 1
            continue
        ws.write(r, 0, label, f_lbl)
        if val is None:
            ws.write(r, 1, "not available", f_txt)
        else:
            ws.write(r, 1, val, fmt)
        if note:
            ws.write(r, 2, note, f_txt)
            ws.set_row(r, max(15, 15 * (len(note) // 80 + 1)))
        r += 1
    if attrib.get("rainfall_only"):
        r += 1
        ws.merge_range(r, 0, r, 2,
                       "Attribution note: the El Niño mask covers rainfall "
                       "only. For a combined rainfall + temperature signal it "
                       "says nothing about whether the heat component is "
                       "El Niño-related, so read it as the weaker claim.",
                       f_note)
        ws.set_row(r, 30)
        r += 1
    if export.get("suggested_citation"):
        r += 1
        ws.write(r, 0, "HOW TO QUOTE THIS", f_h2)
        ws.merge_range(r + 1, 0, r + 1, 2, export["suggested_citation"],
                       f_quote)
        ws.set_row(r + 1, 15 * (len(export["suggested_citation"]) // 120 + 2))
    _print_setup(ws)

    # -- 3. Sensitivity ----------------------------------------------------
    ws = wb.add_worksheet("Sensitivity")
    ws.set_column(0, 0, 22)
    ws.set_column(1, 2, 22)
    ws.write(0, 0, "How much does the probability threshold matter?", f_title)
    ws.merge_range(1, 0, 1, 2,
                   "With 51 ensemble members probability can only move in "
                   "steps of about 2 points, so cells near any cutoff qualify "
                   "on essentially arbitrary grounds. Quote the range.",
                   f_note)
    ws.set_row(1, 30)
    for i, h in enumerate(("Threshold", "Children in signal area",
                           "Share of children")):
        ws.write(3, i, h, f_hdr)
    sens = (result or {}).get("_sensitivity") or {}
    total = export.get("total_children") or 0
    ordered = sorted(sens.items(), key=lambda kv: float(kv[0]))
    for i, (k, v) in enumerate(ordered):
        band = _XLSX_PASTEL[min(i, len(_XLSX_PASTEL) - 1)]
        active = abs(float(k) - meta["threshold"]) < 1e-9
        f_k = wb.add_format({"border": 1, "num_format": PCT0,
                             "bg_color": band, "bold": active})
        f_v = wb.add_format({"border": 1, "num_format": "#,##0",
                             "bold": active})
        f_p = wb.add_format({"border": 1, "num_format": PCT, "bold": active})
        ws.write(4 + i, 0, _frac(float(k)), f_k)
        ws.write(4 + i, 1, int(round(v)), f_v)
        ws.write(4 + i, 2, (v / total) if total else 0, f_p)
    ws.write(4 + len(ordered) + 1, 0,
             f"{meta['region']}, {sig_label.lower()}, "
             f"{period_label(meta['period'])}. Bold row = the "
             f"{int(meta['threshold'])}% threshold used on the Summary sheet.",
             f_note)
    _print_setup(ws, header_rows=3)

    # -- 4. Historical context ---------------------------------------------
    ws = wb.add_worksheet("Historical")
    ws.set_column(0, 0, 22)
    ws.set_column(1, 1, 28)
    ws.write(0, 0, "This forecast against the model's own hindcast", f_title)
    ws.merge_range(1, 0, 1, 1,
                   "Shares of interpretable AREA, not population: the child "
                   "population grid is 2025 and applying it to 1993 would "
                   "imply a precision that does not exist. This is why these "
                   "percentages differ from the Summary sheet's share of "
                   "children.", f_note)
    ws.set_row(1, 44)
    ws.write(3, 0, "Hindcast season (year)", f_hdr)
    ws.write(3, 1, "Share of area under signal", f_hdr)
    r = 4
    for h in (history or []):
        ws.write(r, 0, h.get("year"), f_year)
        ws.write(r, 1, _frac(h.get("share") or 0), f_pct)
        r += 1
    area = meta.get("area_share")
    if area is not None:
        f_now = wb.add_format({"border": 1, "bold": True,
                               "bg_color": "#FDF3C8"})
        f_nowp = wb.add_format({"border": 1, "bold": True,
                                "num_format": PCT, "bg_color": "#FDF3C8"})
        ws.write(r, 0, f"This forecast ({issue})", f_now)
        ws.write(r, 1, _frac(area), f_nowp)
    _print_setup(ws, header_rows=3)

    # -- 5. Method ---------------------------------------------------------
    ws = wb.add_worksheet("Method")
    ws.set_column(0, 0, 28)
    ws.set_column(1, 1, 96)
    ws.write(0, 0, "How this figure was produced", f_title)
    r = 2
    for i, (title, body) in enumerate(METHOD_STEPS, start=1):
        ws.write(r, 0, f"{i}. {title}", f_lbl)
        ws.write(r, 1, body, f_txt)
        ws.set_row(r, max(14, 13 * (len(body) // 90 + 1)))
        r += 1
    _print_setup(ws)

    wb.close()
    return buf.getvalue()


def _elnino_export_payload(result, meta):
    """Export record shared by the JSON and the workbook."""
    exposed = int(round(result.get("exposed", 0) or 0))
    total   = int(round(result.get("total_population", 0) or 0))
    male    = int(round(result.get("exposed_male", 0) or 0))
    fema    = int(round(result.get("exposed_female", 0) or 0))
    male_t  = int(round(result.get("total_population_male", 0) or 0))
    fema_t  = int(round(result.get("total_population_female", 0) or 0))
    masked  = int(round(result.get("masked_population", 0) or 0))
    pct     = (exposed / total * 100) if total else 0
    sig = SIGNAL_MAP.get(meta["signal"], {})
    sens = result.get("_sensitivity") or {}

    export = {
        "product": "ECMWF SEAS5 seasonal precipitation outlook",
        "init": period_issue_label(meta["period"]),
        "init_tag": (PERIOD_MAP.get(meta["period"]) or {}).get("init"),
        "system": ELNINO_SYSTEM, "hindcast": HINDCAST,
        "period": meta["period"], "period_label": period_label(meta["period"]),
        "signal": meta["signal"], "signal_label": sig.get("label"),
        "probability_threshold": meta["threshold"],
        "dry_mask_applied": meta["dry_mask"],
        "region": meta["region"], "admin_level": meta["level"],
        "children_exposed": exposed, "total_children": total,
        "boys_exposed": male, "girls_exposed": fema,
        "total_boys": male_t, "total_girls": fema_t,
        # Two different denominators, both reported so neither is mistaken for
        # the other: children under the signal vs interpretable area under it.
        "pct_children_exposed": round(pct, 2),
        "pct_interpretable_area_under_signal": meta.get("area_share"),
        "children_in_excluded_arid_areas": masked,
        "probability_threshold_units": "percent",
        "sensitivity_children_exposed_by_threshold_pct": {
            k: int(round(v))
            for k, v in sorted(sens.items(), key=lambda kv: float(kv[0]))},
        "population_grid_m": meta["pop_res"],
        # Skill travels with the figure so a number cannot be quoted without
        # the evidence for how much it is worth. It does NOT filter the count.
        "skill_metric": f"pearson_r_vs_{SKILL_REFERENCE}_{HINDCAST}",
        "skill_usable_threshold_r": SKILL_USABLE_MIN,
        "skill_usable_pct_of_signal_area": (meta.get("skill") or {}).get("usable_pct"),
        "skill_assessed_pct_of_signal_area": (meta.get("skill") or {}).get("assessed_pct"),
        # No brier_* keys: the Brier score is no longer surfaced (see
        # elnino_config.EXPLORE_LAYERS). Emitting them as nulls would imply the
        # measure had been computed and come back empty.
        "skill_grades": "seasonal mean, not tercile probabilities",
        # Compound figures are not comparable with single-variable ones, so the
        # baseline the threshold should be read against travels with the number.
        # Magnitude and attribution: reported, never applied to the count.
        "rainfall_anomaly_mm": (meta.get("anoms") or {}).get("anom_mm"),
        "rainfall_anomaly_pct_of_normal": (meta.get("anoms") or {}).get("anom_pct"),
        "temperature_anomaly_c": (meta.get("anoms") or {}).get("anom_c"),
        "children_in_enso_consistent_areas": (meta.get("attrib") or {}).get("children"),
        "pct_of_signal_attributable_to_enso": (meta.get("attrib") or {}).get("pct"),
        "enso_mask_covers_rainfall_only": (meta.get("attrib") or {}).get("rainfall_only"),
        "chance_level_pct": round(
            COMPOUND_CHANCE_LEVEL if is_compound(meta["signal"])
            else CHANCE_LEVEL, 1),
        "computed_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "caveats": CAVEATS,
    }
    if is_compound(meta["signal"]):
        export["compound_method"] = "member-wise joint ensemble count"
        export["compound_note"] = (
            "Counted per ensemble member, not as a product of marginal "
            "probabilities. Threshold is not comparable with a "
            "single-variable one.")
    export["disclaimer"] = download_disclaimer(
        period_label(meta["period"]), period_issue_label(meta["period"]))
    export["suggested_citation"] = _elnino_quote(export)
    return export


def _round_people(n):
    """At most 3 significant figures: 538,628 -> "539,000"."""
    n = int(round(n or 0))
    if n == 0:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1e6:.3g} million"
    if n < 1000:
        return f"{n:,}"
    digits = len(str(n)) - 3
    return f"{round(n, -digits):,}"


def _elnino_quote(export):
    """Suggested sentence for quoting the result, caveats included."""
    sig = SIGNAL_MAP.get(export["signal"], {})
    phrase = ("an area forecast to be either drier or wetter than normal"
              if sig.get("union_of") else sig.get("area_phrase", "a signal area"))
    thr = int(round(export["probability_threshold"]))
    n = export["children_exposed"]
    when = (export.get("computed_utc") or "")[:10]
    head = (f"According to the ECMWF SEAS5 seasonal forecast issued "
            f"{export['init']}, ")
    if n:
        head += (f"about {_round_people(n)} children "
                 f"({export['pct_children_exposed']:.1f}% of all children) in "
                 f"{export['region']} live in {phrase} for "
                 f"{export['period_label']}, forecast with at least {thr}% "
                 f"probability.")
    else:
        head += (f"no children in {export['region']} live in {phrase} for "
                 f"{export['period_label']} at a forecast probability of "
                 f"{thr}% or more.")
    sens = export.get("sensitivity_children_exposed_by_threshold_pct") or {}
    rng = ""
    if len(sens) > 1:
        lo_t, hi_t = min(sens, key=float), max(sens, key=float)
        rng = (f" Depending on the probability required, the figure ranges "
               f"from {_round_people(sens[hi_t])} (at {int(float(hi_t))}%) to "
               f"{_round_people(sens[lo_t])} (at {int(float(lo_t))}%).")
    return (head + rng + " This is a forecast, not a measured impact. "
            f"Source: UNICEF Global Child Hazard Database (GCHD), computed "
            f"{when}.")


def _elnino_filename(meta, ext):
    safe = re.sub(r"[^a-zA-Z0-9]", "_",
                  f"{meta['region']}_{meta['period']}_{meta['signal']}")
    return f"gchd_elnino_{safe}.{ext}"


@app.callback(
    Output("elnino-dl-gate",       "style"),
    Output("store-elnino-dl-kind", "data"),
    Output("elnino-dl-ack",        "value"),
    Output("elnino-dl-body",       "children"),
    Input("elnino-dl-xlsx-btn",    "n_clicks"),
    Input("elnino-dl-json-btn",    "n_clicks"),
    Input("elnino-dl-cancel",      "n_clicks"),
    Input("elnino-dl-confirm",     "n_clicks"),
    State("store-elnino-export",   "data"),
    prevent_initial_call=True,
)
def elnino_dl_gate(_x, _j, _c, _ok, payload):
    """Open the disclaimer on every download click (tick reset); close on answer."""
    trig = ctx.triggered_id
    # Ignore the buttons being mounted by a new results panel.
    if not (ctx.triggered and ctx.triggered[0].get("value")):
        raise dash.exceptions.PreventUpdate
    if trig in ("elnino-dl-xlsx-btn", "elnino-dl-json-btn"):
        if not payload:
            raise dash.exceptions.PreventUpdate
        exp = payload["export"]
        lines = exp["disclaimer"]
        body = [
            html.P(html.Strong(lines[0])),
            html.Ul([html.Li(t) for t in lines[1:]]),
            html.Div("Suggested wording when quoting this figure",
                     className="en-dl-quote-head"),
            # Clamped to 2 lines in CSS; copy and title use the full text.
            html.Div(className="en-dl-quote", children=[
                html.Span(exp["suggested_citation"], id="elnino-dl-quote",
                          className="en-dl-quote-text",
                          title=exp["suggested_citation"]),
                # Not dcc.Clipboard: it renders nothing on non-HTTPS origins.
                html.Button(id="elnino-dl-copy", n_clicks=0,
                            className="en-dl-copy",
                            title="Copy the full sentence",
                            children=[html.Span(className="en-dl-copy-ico"),
                                      html.Span("Copy",
                                                className="en-dl-copy-lbl")]),
            ]),
        ]
        kind = "xlsx" if trig == "elnino-dl-xlsx-btn" else "json"
        return {"display": "flex"}, kind, [], body
    return {"display": "none"}, no_update, [], no_update


# Copy the suggested wording; execCommand fallback for non-HTTPS origins.
app.clientside_callback(
    """
    function(n) {
        if (!n) { return window.dash_clientside.no_update; }
        var el  = document.getElementById("elnino-dl-quote");
        var btn = document.getElementById("elnino-dl-copy");
        if (!el || !btn) { return window.dash_clientside.no_update; }
        var text = el.innerText || el.textContent || "";
        var sel = window.getSelection(), range = document.createRange();
        range.selectNodeContents(el);
        sel.removeAllRanges(); sel.addRange(range);
        function done(ok) {
            var lbl = btn.querySelector(".en-dl-copy-lbl");
            btn.classList.toggle("copied", ok);
            btn.classList.toggle("failed", !ok);
            if (lbl) { lbl.textContent = ok ? "Copied" : "Press Ctrl+C"; }
            clearTimeout(btn._t);
            btn._t = setTimeout(function () {
                btn.classList.remove("copied", "failed");
                if (lbl) { lbl.textContent = "Copy"; }
            }, 2000);
        }
        function legacy() {
            var ok = false;
            try { ok = document.execCommand("copy"); } catch (e) {}
            done(ok);
        }
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(
                function () { done(true); }, legacy);
        } else {
            legacy();      // the sentence is already selected above
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("elnino-dl-copy", "title"),
    Input("elnino-dl-copy",  "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("elnino-dl-confirm", "disabled"),
    Input("elnino-dl-ack",      "value"),
)
def elnino_dl_ack(ack):
    return "yes" not in (ack or [])


@app.callback(
    Output("elnino-download",      "data"),
    Input("elnino-dl-confirm",     "n_clicks"),
    State("elnino-dl-ack",         "value"),
    State("store-elnino-dl-kind",  "data"),
    State("store-elnino-export",   "data"),
    State("store-elnino-result",   "data"),
    prevent_initial_call=True,
)
def elnino_download(n, ack, kind, payload, result):
    """Build the chosen file. Re-checks the ack: this is the only route to a file."""
    if not n or "yes" not in (ack or []) or not payload or not result:
        raise dash.exceptions.PreventUpdate
    meta, history = payload["meta"], payload.get("history") or []
    export = dict(payload["export"],
                  disclaimer_accepted_utc=dt.datetime.utcnow().isoformat(
                      timespec="seconds") + "Z")
    if kind == "json":
        return dcc.send_string(_json.dumps(export, indent=2, ensure_ascii=False),
                               _elnino_filename(meta, "json"))
    return dcc.send_bytes(build_elnino_xlsx(meta, result, history, export),
                          _elnino_filename(meta, "xlsx"))
