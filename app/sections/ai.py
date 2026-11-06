"""AI assistant section."""
import dash_leaflet as dl
import json as _json_mod
from core.ai import ask_gemini
from config.hazards import ADMIN_DATA, HAZARD_TOPICS, SUB_TOPIC_DETAIL, TOPIC_COLORS
from dash import dcc, html, Input, no_update, Output, State
from core.gee import (
    compute_exposure, compute_topic_overlap, get_country_bounds,
    get_country_ucode, get_hazard_tile_url, get_topic_tile_url,
)

from dash_app import app
from i18n import fmt_pct, js_strings, N_, tr, trc

js_strings(N_("Thinking ···"))
from bootstrap import GEE_ATTR
from ui.components import _fmt, _layer_label


def tab_ai():
    return html.Div(id="tab-ai", style={"display": "none"}, children=[
        html.Div(className="exposure-method-note", style={"margin": "12px 10px 0"}, children=[
            html.Div(tr("Under Development"), className="hi-label", style={"marginBottom": "6px"}),
            html.P(trc(
                "The AI application on GCHD is currently under development. "
                "To test the initial version, please contact {email}.",
                email=html.A("dokim@unicef.org", href="mailto:dokim@unicef.org",
                             className="hi-link"),
            ), className="exposure-method-p"),
        ]),
        dcc.Store(id="store-ai-auth",    data=False),
        dcc.Store(id="store-ai-pending", data=None),
        # ── Auth gate ──
        html.Div(id="ai-auth-gate", children=[
            html.Div(className="ai-auth-card", children=[
                html.Div(tr("AI Assistant Access"),
                         style={"fontSize":"0.72rem","fontWeight":"700","color":"var(--hi)",
                                "textTransform":"uppercase","letterSpacing":"0.08em",
                                "marginBottom":"16px"}),
                dcc.Input(id="ai-auth-id",  placeholder=tr("User ID"),
                          type="text",     className="ai-auth-input", n_submit=0),
                dcc.Input(id="ai-auth-pwd", placeholder=tr("Password"),
                          type="password", className="ai-auth-input", n_submit=0),
                html.Div(id="ai-auth-error",
                         style={"fontSize":"0.7rem","color":"var(--red)",
                                "marginBottom":"8px","minHeight":"16px"}),
                html.Button(tr("Sign In"), id="ai-auth-btn", className="ps-btn", n_clicks=0),
            ]),
        ]),
        # ── Main AI interface (hidden until authenticated) ──
        html.Div(id="ai-main", style={"display": "none"}, children=[
            html.Div(className="ps", children=[
                dcc.Textarea(
                    id="ai-query-input",
                    placeholder=tr("e.g. Show river flood layer in Bangladesh, or "
                                "How many children are exposed to heatwaves in India?"),
                    maxLength=400,
                    style={"width":"100%","height":"90px","resize":"none",
                           "fontSize":"0.78rem","padding":"8px 10px",
                           "border":"1px solid var(--border2)","borderRadius":"6px",
                           "fontFamily":"Source Sans Pro, sans-serif",
                           "background":"var(--panel-h)","color":"var(--hi)"},
                ),
                html.Div(id="ai-char-count",
                         style={"fontSize":"0.6rem","color":"var(--lo)",
                                "textAlign":"right","marginTop":"3px"}),
                html.Button(tr("Ask"), id="ai-submit-btn", className="ps-btn",
                            n_clicks=0, style={"marginTop":"8px"}),
            ]),
            html.Div(id="ai-confirm-line",
                     style={"minHeight": "22px", "padding": "6px 12px 0"}),
            html.Div(id="ai-response-panel"),
        ]),
    ])


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

def _ai_msg(text, icon="bi-check-circle", color="var(--cyan)"):
    return html.Div(className="ps", children=[
        html.I(className=f"bi {icon}", style={"color": color, "marginRight": "8px"}),
        html.Span(text, style={"fontSize": "0.8rem", "color": "var(--mid)"}),
    ])

def _ai_error(text):
    return _ai_msg(text, icon="bi-exclamation-circle", color="var(--red)")

def _country_viewport(country_name):
    if not country_name:
        return no_update
    try:
        ucode  = get_country_ucode(country_name)
        bounds = get_country_bounds(ucode)
        return {"bounds": bounds, "transition": "fitBounds"}
    except Exception:
        return no_update

def _format_exposure_summary(stats, country_name):
    total = stats.get("total_population", 0)
    lines = [
        html.Strong(tr("Child exposure — {country}", country=country_name),
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(tr("Total children under 18: {n}", n=_fmt(total)),
                 style={"fontSize": "0.78rem", "color": "var(--mid)",
                        "marginTop": "6px", "marginBottom": "6px"}),
    ]
    for topic in HAZARD_TOPICS:
        exposed = stats.get(topic, 0)
        if exposed and total:
            lines.append(html.Div(
                f"• {tr(topic)}: {_fmt(exposed)} ({fmt_pct(exposed / total * 100)})",
                style={"fontSize": "0.78rem", "color": "var(--mid)"},
            ))
    return html.Div(className="ps", children=lines)


def _format_single_hazard_exposure(stats, topic, country_name):
    total   = stats.get("total_population", 0)
    exposed = stats.get(topic, 0)
    pct     = (exposed / total * 100) if total else 0
    lines   = [
        html.Strong(tr("{topic} exposure — {country}", topic=tr(topic),
                       country=country_name),
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(tr("Total children under 18: {n}", n=_fmt(total)),
                 style={"fontSize": "0.78rem", "color": "var(--mid)", "marginTop": "6px"}),
        html.Div(tr("Exposed to {topic}: {n}", topic=tr(topic), n=_fmt(exposed))
                 + f" ({fmt_pct(pct)})",
                 style={"fontSize": "0.78rem", "color": "var(--cyan)",
                        "marginTop": "4px", "fontWeight": "600"}),
    ]
    if topic in SUB_TOPIC_DETAIL:
        for h_name in HAZARD_TOPICS[topic]:
            sub = stats.get(h_name, 0)
            if sub and total:
                lines.append(html.Div(
                    f"  ↳ {_layer_label(h_name)}: {_fmt(sub)} "
                    f"({fmt_pct(sub / total * 100)})",
                    style={"fontSize": "0.74rem", "color": "var(--lo)",
                           "paddingInlineStart": "12px"},
                ))
    return html.Div(className="ps", children=lines)


def _format_multi_hazard_exposure(stats, topics, overlap, country_name):
    total = stats.get("total_population", 0)
    lines = [
        html.Strong(tr("Multi-hazard exposure — {country}", country=country_name),
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(tr("Total children under 18: {n}", n=_fmt(total)),
                 style={"fontSize": "0.78rem", "color": "var(--mid)",
                        "marginTop": "6px", "marginBottom": "4px"}),
    ]
    for topic in topics:
        exposed = stats.get(topic, 0)
        pct = (exposed / total * 100) if total else 0
        lines.append(html.Div(
            f"• {tr(topic)}: {_fmt(exposed)} ({fmt_pct(pct)})",
            style={"fontSize": "0.78rem", "color": "var(--mid)"},
        ))
    if overlap is not None and total:
        lines.append(html.Div(
            tr("Exposed to ALL ({topics}): {n}",
               topics=" & ".join(tr(t) for t in topics), n=_fmt(overlap))
            + f" ({fmt_pct(overlap / total * 100)})",
            style={"fontSize": "0.78rem", "color": "var(--cyan)",
                   "marginTop": "6px", "fontWeight": "600"},
        ))
    return html.Div(className="ps", children=lines)


def _ai_processing():
    return html.Div(tr("Computing ···"), className="ai-processing")


def _confirm_sentence(action, params):
    # Whole-sentence templates (not concatenated fragments) so each language
    # can order the bold parts its own way.
    B = lambda t: html.Strong(str(t), style={"color": "var(--hi)"})
    country = B(params.get("country_name", ""))
    adm     = B(tr(params.get("admin_level") or "adm0 (Country)"))
    if action == "show_hazard_layer":
        layer = B(_layer_label(params.get("hazard_name", "")))
        parts = (trc("Showing hazard layer {layer} in {country}",
                     layer=layer, country=country)
                 if params.get("country_name") else
                 trc("Showing hazard layer {layer}", layer=layer))
    elif action == "show_topic_layer":
        topic = B(tr(params.get("topic_name", "")))
        parts = (trc("Showing {topic} exposure layer in {country}",
                     topic=topic, country=country)
                 if params.get("country_name") else
                 trc("Showing {topic} exposure layer", topic=topic))
    elif action == "exposure_summary":
        parts = trc("Full hazard exposure summary for {country} at {level} level",
                    country=country, level=adm)
    elif action == "hazard_exposure":
        parts = trc("Child exposure to {topic} in {country} at {level} level",
                    topic=B(tr(params.get("topic_name", ""))),
                    country=country, level=adm)
    elif action == "multi_hazard_exposure":
        topic_parts = []
        for i, t in enumerate(params.get("topic_names", [])):
            if i > 0:
                topic_parts.append(" & ")
            topic_parts.append(B(tr(t)))
        parts = trc("Children exposed to {topics} simultaneously in {country} "
                    "at {level} level",
                    topics=html.Span(topic_parts), country=country, level=adm)
    elif action == "zoom_to_country":
        parts = trc("Zooming map to {country}", country=country)
    else:
        return None
    return html.Div(parts, style={"fontSize": "0.76rem", "color": "var(--lo)",
                                   "fontStyle": "italic", "marginBottom": "6px"})


app.clientside_callback(
    "function(v){ return (v||'').length + ' / 400'; }",
    Output("ai-char-count",  "children"),
    Input("ai-query-input",  "value"),
)

app.clientside_callback(
    """function(n, i18n){
        return n ? [window.gchdT(i18n, 'Thinking ···'), null]
                 : [window.dash_clientside.no_update, window.dash_clientside.no_update];
    }""",
    Output("ai-confirm-line",   "children", allow_duplicate=True),
    Output("ai-response-panel", "children", allow_duplicate=True),
    Input("ai-submit-btn",      "n_clicks"),
    State("store-i18n",         "data"),
    prevent_initial_call=True,
)


@app.callback(
    Output("store-ai-auth", "data"),
    Output("ai-auth-gate",  "style"),
    Output("ai-main",       "style"),
    Output("ai-auth-error", "children"),
    Input("ai-auth-btn",    "n_clicks"),
    State("ai-auth-id",     "value"),
    State("ai-auth-pwd",    "value"),
    prevent_initial_call=True,
)
def ai_authenticate(_n, user_id, password):
    _AI_ID  = "unicef2026"
    _AI_PWD = "unicef2026"
    if (user_id or "").strip() == _AI_ID and (password or "") == _AI_PWD:
        return True, {"display": "none"}, {"display": "block"}, ""
    return False, no_update, no_update, tr("Incorrect credentials.")


@app.callback(
    Output("store-ai-pending",  "data"),
    Output("ai-confirm-line",   "children",  allow_duplicate=True),
    Output("ai-response-panel", "children",  allow_duplicate=True),
    Input("ai-submit-btn",      "n_clicks"),
    State("ai-query-input",     "value"),
    prevent_initial_call=True,
)
def ai_classify(_n, query):
    """Phase 1: call Gemini (~1s), show confirm sentence + Computing indicator."""
    if not query or not query.strip():
        return no_update, no_update, no_update

    try:
        result = ask_gemini(query.strip())
    except _json_mod.JSONDecodeError:
        return None, "", _ai_error(tr("AI returned an unexpected response. "
                                      "Please try rephrasing."))
    except Exception as e:
        return None, "", _ai_error(tr("AI error: {e}", e=e))

    action  = result.get("action")
    params  = result.get("params", {})
    message = result.get("message", "")

    if action == "reject":
        return None, "", _ai_msg(message, icon="bi-x-circle", color="var(--amber)")

    confirm = _confirm_sentence(action, params)
    return result, confirm, _ai_processing()


@app.callback(
    Output("ai-response-panel", "children",  allow_duplicate=True),
    Output("data-layers",       "children",  allow_duplicate=True),
    Output("main-map",          "viewport",  allow_duplicate=True),
    Input("store-ai-pending",   "data"),
    prevent_initial_call=True,
)
def ai_execute(pending):
    """Phase 2: run the GEE function and return results."""
    if not pending:
        return no_update, no_update, no_update

    action  = pending.get("action")
    params  = pending.get("params", {})
    message = pending.get("message", "")

    if action == "zoom_to_country":
        country = params.get("country_name", "")
        try:
            ucode    = get_country_ucode(country)
            bounds   = get_country_bounds(ucode)
            viewport = {"bounds": bounds, "transition": "fitBounds"}
        except Exception:
            return _ai_error(tr("Country not found: {country}", country=country)), no_update, no_update
        return _ai_msg(message), no_update, viewport

    if action == "show_hazard_layer":
        hazard   = params.get("hazard_name", "")
        url, _   = get_hazard_tile_url(hazard)
        if not url:
            return _ai_error(tr("Unknown hazard: {hazard}", hazard=hazard)), no_update, no_update
        layer    = [dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75)]
        viewport = _country_viewport(params.get("country_name"))
        return _ai_msg(message), layer, viewport

    if action == "show_topic_layer":
        topic    = params.get("topic_name", "")
        color    = TOPIC_COLORS.get(topic, "#ff0000")
        url, _   = get_topic_tile_url(topic, color)
        layer    = [dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75)]
        viewport = _country_viewport(params.get("country_name"))
        return _ai_msg(message), layer, viewport

    if action == "exposure_summary":
        country_name = params.get("country_name", "")
        admin_level  = params.get("admin_level") or "adm0 (Country)"
        if admin_level not in ADMIN_DATA:
            admin_level = "adm0 (Country)"
        try:
            ucode    = get_country_ucode(country_name)
            stats    = compute_exposure(ucode, admin_level)
            summary  = _format_exposure_summary(stats, country_name)
            bounds   = get_country_bounds(ucode)
            viewport = {"bounds": bounds, "transition": "fitBounds"}
        except Exception as e:
            return _ai_error(tr("Could not compute exposure: {e}", e=e)), no_update, no_update
        return summary, no_update, viewport

    if action == "hazard_exposure":
        topic        = params.get("topic_name", "")
        country_name = params.get("country_name", "")
        admin_level  = params.get("admin_level") or "adm0 (Country)"
        if admin_level not in ADMIN_DATA:
            admin_level = "adm0 (Country)"
        if topic not in HAZARD_TOPICS:
            return _ai_error(tr("Unknown hazard topic: {topic}", topic=topic)), no_update, no_update
        try:
            ucode    = get_country_ucode(country_name)
            stats    = compute_exposure(ucode, admin_level)
            summary  = _format_single_hazard_exposure(stats, topic, country_name)
            color    = TOPIC_COLORS.get(topic, "#ff0000")
            url, _   = get_topic_tile_url(topic, color)
            layer    = [dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75)]
            bounds   = get_country_bounds(ucode)
            viewport = {"bounds": bounds, "transition": "fitBounds"}
        except Exception as e:
            return _ai_error(tr("Could not compute exposure: {e}", e=e)), no_update, no_update
        return summary, layer, viewport

    if action == "multi_hazard_exposure":
        topics       = params.get("topic_names", [])
        country_name = params.get("country_name", "")
        admin_level  = params.get("admin_level") or "adm0 (Country)"
        if admin_level not in ADMIN_DATA:
            admin_level = "adm0 (Country)"
        invalid = [t for t in topics if t not in HAZARD_TOPICS]
        if invalid:
            return _ai_error(tr("Unknown topics: {topics}", topics=", ".join(invalid))), no_update, no_update
        if not topics:
            return _ai_error(tr("No topics specified.")), no_update, no_update
        try:
            ucode    = get_country_ucode(country_name)
            stats    = compute_exposure(ucode, admin_level)
            overlap  = None
            if len(topics) >= 2:
                ov_stats = compute_topic_overlap(ucode, admin_level, tuple(topics))
                overlap  = ov_stats.get("overlap", 0)
            summary  = _format_multi_hazard_exposure(stats, topics, overlap, country_name)
            bounds   = get_country_bounds(ucode)
            viewport = {"bounds": bounds, "transition": "fitBounds"}
        except Exception as e:
            return _ai_error(tr("Could not compute exposure: {e}", e=e)), no_update, no_update
        return summary, no_update, viewport

    return _ai_error(tr("Unrecognised action from AI.")), no_update, no_update
