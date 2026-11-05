"""Preparedness Pop analysis, including custom boundaries."""
import dash
import dash_leaflet as dl
import json as _json
import re
from config.hazards import (
    ADMIN_DATA, EXCLUDE_ISO3, EXPOSURE_ONLY_TOPICS, FORCE_NULL_RULES,
    HAZARD_MAP, HAZARD_TOPICS, HAZARDS, is_no_data_ucode, SUB_TOPIC_DETAIL,
    TOPIC_COLORS,
)
from dash import ALL, ctx, dcc, html, Input, no_update, Output, State
from core.gee import (
    compute_exposure, compute_exposure_asset, compute_exposure_custom,
    get_asset_bounds, get_asset_info, get_country_bounds, get_country_ucode,
    get_custom_asset_tile_url, get_exposed_pop_tile_url, get_feature_bounds,
    get_selected_feature_tile_url, get_topic_tile_url_clipped,
)

from dash_app import app
from bootstrap import ANALYSIS_TOPIC_SLOTS, COUNTRY_NAMES, GEE_ATTR
from ui.components import (
    _eff_threshold, _fmt, _hazard_label, _info_icon, _no_data_panel,
    _provenance_block,
)
from ui.threshold import _threshold_overrides
from ui.xlsx import build_xlsx, _thresholds_banner


def tab_analysis():
    return html.Div(id="tab-analysis", style={"display": "none"}, children=[
        # ── Sub-tab switcher ──
        html.Div(className="analysis-sub-tabs", children=[
            html.Button("GeoRepo Boundaries", id="btn-georapo-tab",
                        className="analysis-sub-tab active", n_clicks=0),
            html.Button("Custom Boundary", id="btn-custom-tab",
                        className="analysis-sub-tab", n_clicks=0),
        ]),
        # ── GeoRepo content ──
        html.Div(id="georapo-content", children=[
            html.Div(className="ps", children=[
                html.Div("1. Select country / territory", className="ps-label"),
                dcc.Dropdown(
                    id="country-select", className="ps-select",
                    options=[{"label": c, "value": c} for c in COUNTRY_NAMES],
                    value=None, placeholder="Search country / territory…",
                    clearable=False, searchable=True,
                ),
            ]),
            html.Div(id="level-section", style={"display": "none"}, children=[
                html.Div(className="ps", children=[
                    html.Div("2. Analysis level", className="ps-label"),
                    dcc.Dropdown(
                        id="level-select", className="ps-select",
                        options=[{"label": l, "value": l} for l in ADMIN_DATA.keys()],
                        value=None, placeholder="— select level —",
                        clearable=False, searchable=False,
                    ),
                ]),
            ]),
            html.Div(id="hazard-select-section", style={"display": "none"}, children=[
                html.Div(className="ps", children=[
                    html.Div("3. Hazard topics & thresholds", className="ps-label"),
                    dcc.Dropdown(
                        id="analysis-topic-select", className="ps-select",
                        options=[{"label": t, "value": t} for t in HAZARD_TOPICS],
                        value=[t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS],
                        multi=True, placeholder="Select hazard topics…",
                        clearable=True, searchable=False,
                    ),
                    html.Div("Climate hazards are selected by default. Add or remove "
                             "topics, and adjust any hazard's threshold below.",
                             className="ps-caption"),
                    # Collapsible threshold editor (collapsed by default).
                    html.Div(id="analysis-thr-toggle", className="athr-toggle", n_clicks=0,
                             children=[
                        html.I(className="bi bi-chevron-right", id="analysis-thr-chevron"),
                        html.Span("Adjust thresholds", className="athr-toggle-label"),
                        html.Span("", id="analysis-thr-badge", className="athr-badge"),
                    ]),
                    # Shown at ADM0: custom thresholds are country-level disabled.
                    html.Div("Custom thresholds apply at ADM1/ADM2. Country-level "
                             "(ADM0) analysis uses the standard thresholds.",
                             id="analysis-thr-adm0-note", className="ps-caption",
                             style={"display": "none", "marginTop": "4px",
                                    "color": "var(--mid)"}),
                    html.Div(id="analysis-thr-body", style={"display": "none"}, children=[
                        html.Div(id="analysis-threshold-block",
                                 className="analysis-threshold-block"),
                    ]),
                ]),
            ]),
            html.Div(id="compute-section", style={"display": "none"}),
            html.Div(id="selected-badge-wrap"),
            html.Div(id="analysis-compute-wrap", style={"display": "none"}, children=[
                html.Div(className="ps", children=[
                    html.Button("▶  Compute exposure", id="analysis-compute-btn",
                                className="ps-btn", n_clicks=0, disabled=True),
                    html.Div("Select a region, then Compute.", id="analysis-compute-hint",
                             className="ps-caption", style={"marginTop": "6px"}),
                ]),
            ]),
            dcc.Loading(
                id="results-loading", type="circle", color="#1CABE2",
                children=html.Div(id="results-panel"),
            ),
        ]),
        # ── Custom boundary content ──
        html.Div(id="custom-content", style={"display": "none"}, children=[
            # — GeoJSON upload —
            html.Div(className="ps", children=[
                html.Div("1. Upload GeoJSON (max 100 features, 1 MB)", className="ps-label"),
                dcc.Upload(
                    id="custom-upload",
                    children=html.Div([
                        html.I(className="bi bi-cloud-upload",
                               style={"fontSize": "1.4rem", "color": "var(--lo)"}),
                        html.Div("Drop file or click to browse",
                                 style={"fontSize": "0.75rem", "color": "var(--lo)",
                                        "marginTop": "6px"}),
                        html.Div(".geojson / .json",
                                 style={"fontSize": "0.62rem", "color": "var(--lo)",
                                        "marginTop": "2px"}),
                    ], style={"textAlign": "center", "padding": "16px 8px"}),
                    style={
                        "border": "2px dashed var(--border2)", "borderRadius": "8px",
                        "cursor": "pointer", "transition": "border-color 0.15s",
                    },
                    accept=".geojson,.json",
                    multiple=False,
                ),
                html.Div(id="custom-upload-status",
                         style={"fontSize": "0.72rem", "marginTop": "8px",
                                "color": "var(--mid)"}),
            ]),
            html.Div(className="exposure-method-note", style={"margin": "4px 10px 8px"}, children=[
                html.P([
                    "GeoJSON upload supports up to ",
                    html.Strong("100 features and 1 MB"),
                    ". For larger files, upload your boundary to ",
                    html.Strong("Google Earth Engine"),
                    " as an asset, make it publicly sharable, then paste the asset path "
                    "into the text box below. ",
                    html.A("How to upload a table to GEE →",
                           href="https://developers.google.com/earth-engine/guides/table_upload",
                           target="_blank", className="hi-link"),
                ], className="exposure-method-p", style={"marginBottom": "0"}),
            ]),
            html.Div(id="custom-name-field-wrap", style={"display": "none"}, children=[
                html.Div(className="ps", children=[
                    html.Div("2. Feature name / ID field", className="ps-label"),
                    dcc.Dropdown(
                        id="custom-name-field", className="ps-select",
                        clearable=False, searchable=False,
                    ),
                ]),
                html.Div(className="ps", children=[
                    html.Button("Compute Exposure", id="custom-compute-btn",
                                className="ps-btn", n_clicks=0),
                    html.Div("Uses your current hazard thresholds from the "
                             "Analysis tab.", className="ps-caption",
                             style={"marginTop": "6px", "color": "var(--mid)"}),
                ]),
            ]),
            dcc.Loading(
                id="custom-results-loading", type="circle", color="#1CABE2",
                children=html.Div(id="custom-results-panel"),
            ),
            # — Divider —
            html.Div(style={"display":"flex","alignItems":"center","padding":"6px 16px","gap":"8px"},
                     children=[
                html.Hr(style={"flex":"1","border":"none","borderTop":"1px solid var(--border)","margin":"0"}),
                html.Span("or", style={"fontSize":"0.65rem","color":"var(--lo)","flexShrink":"0"}),
                html.Hr(style={"flex":"1","border":"none","borderTop":"1px solid var(--border)","margin":"0"}),
            ]),
            # — GEE Asset —
            html.Div(className="ps", children=[
                html.Div("Use a GEE Asset", className="ps-label"),
                html.Div(style={"display":"flex","gap":"6px"}, children=[
                    dcc.Input(
                        id="gee-asset-input",
                        placeholder="projects/…/assets/…",
                        debounce=False,
                        style={"flex":"1","padding":"7px 9px","fontSize":"0.75rem",
                               "border":"1px solid var(--border2)","borderRadius":"6px",
                               "fontFamily":"Source Sans Pro, sans-serif",
                               "background":"var(--panel-h)","color":"var(--hi)"},
                    ),
                    html.Button("Load", id="gee-asset-load-btn", className="ps-btn",
                                n_clicks=0,
                                style={"width":"auto","padding":"7px 14px","flexShrink":"0"}),
                ]),
                html.Div(id="gee-asset-status",
                         style={"fontSize":"0.72rem","marginTop":"8px","color":"var(--mid)"}),
            ]),
            html.Div(id="gee-asset-name-wrap", style={"display": "none"}, children=[
                html.Div(className="ps", children=[
                    html.Div("Feature name / ID field", className="ps-label"),
                    dcc.Dropdown(
                        id="gee-asset-name-field", className="ps-select",
                        clearable=False, searchable=False,
                    ),
                ]),
                html.Div(className="ps", children=[
                    html.Button("Compute Exposure", id="gee-asset-compute-btn",
                                className="ps-btn", n_clicks=0),
                    html.Div("Uses your current hazard thresholds from the "
                             "Analysis tab.", className="ps-caption",
                             style={"marginTop": "6px", "color": "var(--mid)"}),
                ]),
            ]),
            dcc.Loading(
                id="gee-asset-results-loading", type="circle", color="#1CABE2",
                children=html.Div(id="gee-asset-results-panel"),
            ),
            # — Stores —
            dcc.Store(id="store-custom-geojson",         data=None),
            dcc.Store(id="store-custom-geojson-stripped",data=None),
            dcc.Store(id="store-gee-asset",              data=None),
        ]),
    ])


# ── Country selection ─────────────────────────────────────────────────────────

@app.callback(
    Output("store-country",       "data"),
    Output("store-ucode",         "data"),
    Output("store-bounds",        "data"),
    Output("store-level",         "data"),
    Output("store-clicked-ucode", "data"),
    Output("store-clicked-name",  "data"),
    Output("store-exposure",      "data"),
    Output("level-section",       "style"),
    Output("level-select",        "value"),
    Input("country-select",       "value"),
    prevent_initial_call=True,
)
def on_country_select(country):
    if not country:
        return None, None, None, None, None, None, None, {"display": "none"}, None
    ucode  = get_country_ucode(country)
    bounds = get_country_bounds(ucode)
    return country, ucode, bounds, "adm0 (Country)", None, None, None, {"display": "block"}, "adm0 (Country)"


# ── Admin level selection ─────────────────────────────────────────────────────

@app.callback(
    Output("store-level",          "data", allow_duplicate=True),
    Output("store-clicked-ucode",  "data", allow_duplicate=True),
    Output("store-clicked-name",   "data", allow_duplicate=True),
    Output("store-exposure",       "data", allow_duplicate=True),
    Output("compute-section",      "children"),
    Output("compute-section",      "style"),
    Output("hazard-select-section", "style"),
    Output("analysis-compute-wrap", "style"),
    Input("level-select",          "value"),
    prevent_initial_call=True,
)
def on_level_select(level):
    if not level:
        return (no_update, no_update, no_update, no_update, None,
                {"display": "none"}, {"display": "none"}, {"display": "none"})
    if level == "adm0 (Country)":
        content = None                      # country is already selected; just Compute
    else:
        short = "province" if "adm1" in level else "district"
        content = html.Div(className="ps", children=[
            html.Div(f"Click a {short} on the map, then Compute.",
                     className="info-box"),
        ])
    return (level, None, None, None, content,
            {"display": "block"}, {"display": "block"}, {"display": "block"})


@app.callback(
    Output("analysis-thr-body",    "style"),
    Output("analysis-thr-chevron", "className"),
    Output("analysis-thr-toggle",  "className"),
    Output("analysis-thr-adm0-note", "style"),
    Input("analysis-thr-toggle",   "n_clicks"),
    Input("store-level",           "data"),
)
def toggle_analysis_thr(n, level):
    # Custom thresholds only apply at ADM1/ADM2. At ADM0 the section is shown but
    # disabled (greyed, non-expandable) with an explanatory note.
    note_base = {"marginTop": "4px", "color": "var(--mid)"}
    if level == "adm0 (Country)":
        return ({"display": "none"}, "bi bi-chevron-right",
                "athr-toggle athr-toggle-disabled",
                {**note_base, "display": "block"})
    open_ = bool(n) and (n % 2 == 1)         # starts collapsed
    body  = {"display": "block"} if open_ else {"display": "none"}
    chev  = "bi bi-chevron-down" if open_ else "bi bi-chevron-right"
    return (body, chev, "athr-toggle", {**note_base, "display": "none"})


@app.callback(
    Output("analysis-thr-badge", "children"),
    Input({"type": "analysis-threshold", "index": ALL}, "value"),
    State({"type": "analysis-threshold", "index": ALL}, "id"),
)
def analysis_thr_badge(values, ids):
    n = 0
    for id_obj, val in zip(ids or [], values or []):
        h_name = id_obj.get("index")
        default = HAZARD_MAP.get(h_name, {}).get("threshold")
        if val in (None, "") or default is None:
            continue
        try:
            if abs(float(val) - float(default)) > 1e-12:
                n += 1
        except (TypeError, ValueError):
            continue
    return f"{n} changed" if n else ""


@app.callback(
    Output("store-thresholds", "data", allow_duplicate=True),
    Input({"type": "analysis-threshold", "index": ALL}, "value"),
    State({"type": "analysis-threshold", "index": ALL}, "id"),
    State("analysis-topic-select", "value"),
    prevent_initial_call=True,
)
def sync_analysis_thresholds(values, ids, topics):
    """Publish the Analysis-tab thresholds to the shared store so custom-boundary
    analysis (and any consumer) applies the same current thresholds."""
    overrides, _ = _threshold_overrides(topics, ids, values)
    return overrides


# ── Compute-button enable/disable ─────────────────────────────────────────────
# adm0: enabled once a country is selected (store-ucode). Sub-national: enabled
# once a feature is clicked (store-clicked-ucode).

@app.callback(
    Output("analysis-compute-btn",  "disabled"),
    Output("analysis-compute-hint", "style"),
    Input("store-level",            "data"),
    Input("store-ucode",            "data"),
    Input("store-clicked-ucode",    "data"),
)
def analysis_toggle_compute(level, ucode, clicked_ucode):
    if level == "adm0 (Country)":
        ready = bool(ucode)
    else:
        ready = bool(clicked_ucode)
    hint_style = {"marginTop": "6px", "display": "none" if ready else "block"}
    return (not ready), hint_style


# ── Selected badge ────────────────────────────────────────────────────────────

@app.callback(
    Output("selected-badge-wrap", "children"),
    Input("store-clicked-name",   "data"),
)
def update_badge(name):
    if not name:
        return None
    return html.Div(className="selected-badge", children=[
        html.Div("Selected region", className="selected-badge-tag"),
        html.Div(name, className="selected-badge-name"),
    ])


@app.callback(
    Output("store-exposure",      "data", allow_duplicate=True),
    Output("results-panel",       "children"),
    Output("store-analysis-viz",  "data"),
    Input("analysis-compute-btn", "n_clicks"),
    Input("mhc-select",           "value"),
    Input("mhi-select",           "value"),
    State("store-clicked-ucode",  "data"),
    State("store-clicked-name",   "data"),
    State("store-ucode",          "data"),
    State("store-country",        "data"),
    State("analysis-topic-select", "value"),
    State({"type": "analysis-threshold", "index": ALL}, "value"),
    State({"type": "analysis-threshold", "index": ALL}, "id"),
    State("store-level",          "data"),
    State("store-exposure",       "data"),
    prevent_initial_call=True,
)
def run_exposure(_n, mhc, mhi, clicked_ucode, clicked_name, country_ucode, country_name,
                 sel_topics, thr_values, thr_ids, level, existing):
    if not level:
        return no_update, None, no_update
    # Resolve region by level: adm0 uses the country; sub-national uses the click.
    if level == "adm0 (Country)":
        ucode, name = country_ucode, country_name
    else:
        ucode, name = clicked_ucode, clicked_name
    if not ucode:
        return no_update, None, no_update

    sel_topics = sel_topics or []
    overrides, sig = _threshold_overrides(sel_topics, thr_ids, thr_values)
    # Custom thresholds are only applied at ADM1/ADM2. At ADM0 they'd bypass the
    # fast precomputed-asset path and trigger a slow full-country chunked
    # reduction, so drop them and let the country level use standard thresholds.
    if level == "adm0 (Country)":
        overrides, sig = {}, ""
    # Non-default request cannot use the precomputed adm0 asset path.
    topics_arg = None if set(sel_topics) == {t for t in HAZARD_TOPICS
                                             if t not in EXPOSURE_ONLY_TOPICS} else sel_topics

    triggered_ids = {t["prop_id"].split(".")[0] for t in ctx.triggered}
    filter_only = triggered_ids and triggered_ids <= {"mhc-select", "mhi-select"}

    # MHC/MHI change: never recompute here — re-render the last Compute result.
    # (Topic/threshold edits only take effect when Compute is pressed.)
    if filter_only:
        if not existing:
            return no_update, no_update, no_update
        data = _apply_force_null(existing, ucode)
        return no_update, render_results(data, name, mhc, mhi,
                                         data.get("_topics"), data.get("_overrides"),
                                         ucode=ucode), no_update

    # Compute button pressed → run the analysis for the current selection.
    result = compute_exposure(
        feature_ucode=ucode, admin_level=level,
        mhc_value=mhc if mhc else None,
        mhi_percentile=mhi if mhi else None,
        topics=topics_arg,
        threshold_overrides=overrides or None,
    )
    result = _apply_force_null(result, ucode)
    result["_sig"]       = sig
    result["_topics"]    = sel_topics
    result["_overrides"] = overrides

    # Map layers for this result. Only topics that actually have data here get a
    # legend row — a toggle for a hazard with no local coverage is just noise.
    map_topics = [t for t in HAZARD_TOPICS if t in sel_topics
                  and (result.get(t) or 0) > 0][:ANALYSIS_TOPIC_SLOTS]
    no_data = is_no_data_ucode(ucode)
    viz = {
        "ucode": ucode, "level": level, "name": name,
        # Where no hazard data exists, the region outline is the only honest
        # thing to draw: an exposed-population raster would render a figure the
        # results panel is explicitly declining to report.
        "topics": [] if no_data else map_topics,
        "no_data": no_data,
        # Sorted tuple-of-pairs: stable, hashable cache key for the tile helpers.
        "thr": sorted((h, v) for h, v in (overrides or {}).items()),
    }
    return (result,
            render_results(result, name, mhc, mhi, sel_topics, overrides,
                           ucode=ucode),
            viz)


# ── Analysis result → map layers ──────────────────────────────────────────────

@app.callback(
    Output("analysis-exposed-tile",   "url"),
    Output("analysis-selection-tile", "url"),
    Output("main-map", "viewport", allow_duplicate=True),
    Output("store-analysis-topic-urls", "data"),
    Input("store-analysis-viz", "data"),
    Input("store-tab",          "data"),
    prevent_initial_call=True,
)
def update_analysis_layers(viz, tab):
    """Draw the computed result: child population masked to the selected
    hazards, clipped to the AOI, plus the AOI outline, and fit the map to it.

    Only these two tiles are built eagerly — per-topic hazard tiles are fetched
    lazily on first reveal (see reveal_analysis_topic), so a Compute never fans
    out into one GEE call per selected topic. The outline gets its own
    component (not the shared selection-layer group) so the legend's eye can
    flip it clientside.
    """
    if tab != "analysis" or not viz:
        # Clear the rasters when leaving the tab; store-analysis-viz is kept so
        # returning to Analysis restores the view without recomputing.
        return "", "", no_update, {}

    thr_key = tuple((h, v) for h, v in (viz.get("thr") or []))
    if viz.get("no_data"):
        url = ""
    else:
        try:
            url, _ = get_exposed_pop_tile_url(
                viz["ucode"], viz["level"], tuple(viz.get("topics") or ()), thr_key)
        except Exception:
            return "", "", no_update, {}

    # AOI outline — cached, so this is free on repeat Computes of the region.
    try:
        sel_url = get_selected_feature_tile_url(viz["level"], viz["ucode"])
    except Exception:
        sel_url = ""

    viewport = no_update
    try:
        viewport = {"bounds": get_feature_bounds(viz["level"], viz["ucode"]),
                    "transition": "flyToBounds"}
    except Exception:
        pass
    return url, sel_url, viewport, {}


@app.callback(
    Output({"type": "analysis-topic-tile", "index": ALL}, "url"),
    Input({"type": "legend-eye", "index": ALL}, "n_clicks"),
    State({"type": "analysis-topic-tile", "index": ALL}, "url"),
    State("store-analysis-viz", "data"),
    prevent_initial_call=True,
)
def reveal_analysis_topic(_eye_clicks, existing_urls, viz):
    """Fetch a topic's clipped hazard tile the first time its legend eye is
    switched on — lazily, one GEE call per topic the user actually looks at.

    Once a slot holds a URL it is never refetched; subsequent show/hide is a
    pure clientside opacity flip with no server round-trip.
    """
    triggered = ctx.triggered_id
    if not viz or not isinstance(triggered, dict):
        raise dash.exceptions.PreventUpdate

    # Legend eyes are shared with the infra tab; only analysis rows matter here.
    layer_id = str(triggered.get("index", ""))
    if not layer_id.startswith("analysis-topic-"):
        raise dash.exceptions.PreventUpdate
    try:
        slot = int(layer_id.rsplit("-", 1)[1])
    except ValueError:
        raise dash.exceptions.PreventUpdate

    topics = viz.get("topics") or []
    if slot >= len(topics) or slot >= len(existing_urls):
        raise dash.exceptions.PreventUpdate
    if existing_urls[slot]:
        raise dash.exceptions.PreventUpdate      # already fetched

    topic   = topics[slot]
    thr_key = tuple((h, v) for h, v in (viz.get("thr") or []))
    try:
        url, _ = get_topic_tile_url_clipped(
            topic, TOPIC_COLORS.get(topic, "#888888"),
            viz["ucode"], viz["level"], thr_key)
    except Exception:
        raise dash.exceptions.PreventUpdate

    out = list(existing_urls)
    out[slot] = url
    return out


def _apply_force_null(result, ucode):
    if not result or not ucode:
        return result
    iso3 = ucode.split("_")[0].upper()
    result = dict(result)
    if iso3 in EXCLUDE_ISO3:
        for topic in HAZARD_TOPICS:
            cov_key = "cov_" + re.sub(r"[^a-zA-Z0-9]", "_", topic)
            result[topic] = 0
            result[cov_key] = 0
        for h in HAZARDS:
            result[h["name"]] = 0
        result["total_population"] = 0
        result["total_population_male"] = 0
        result["total_population_female"] = 0
        return result
    for topic in FORCE_NULL_RULES.get(iso3, []):
        cov_key = "cov_" + re.sub(r"[^a-zA-Z0-9]", "_", topic)
        result[topic] = 0
        result[cov_key] = 0
        for h_name in HAZARD_TOPICS.get(topic, []):
            result[h_name] = 0
    return result


def render_results(result, region_name, mhc_val, mhi_val, sel_topics=None,
                   overrides=None, ucode=None):
    if ucode and is_no_data_ucode(ucode):
        return _no_data_panel(region_name)
    if not result:
        return html.Div("No data available.", className="ps-caption",
                        style={"padding": "14px 16px"})

    # Which topics to display: the user's selection (default = climate topics).
    if sel_topics:
        show_topics = [t for t in HAZARD_TOPICS if t in sel_topics]
    else:
        show_topics = [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]

    total = int(round(result.get("total_population",       0) or 0))
    male  = int(round(result.get("total_population_male",  0) or 0))
    fema  = int(round(result.get("total_population_female",0) or 0))
    pct_f = f"{fema/total*100:.0f}%" if total else "—"

    # Collect + sort topic data
    no_data_topics = []
    topic_data     = []
    for topic in show_topics:
        cov_key = "cov_" + re.sub(r"[^a-zA-Z0-9]", "_", topic)
        has_cov = (result.get(cov_key) or 0) > 0
        count   = int(round(result.get(topic) or 0))
        if not has_cov:
            no_data_topics.append(topic)
            continue
        topic_data.append({
            "topic": topic, "count": count,
            "pct": count / total * 100 if total else 0,
        })
    topic_data.sort(key=lambda r: r["count"], reverse=True)

    def _topic_row(td):
        topic = td["topic"]
        color = TOPIC_COLORS.get(topic, "#888")
        pct   = td["pct"]
        count = td["count"]
        # Single-hazard topics show their effective threshold inline.
        hazards = HAZARD_TOPICS.get(topic, [])
        thr_txt = _eff_threshold(hazards[0], overrides) if len(hazards) == 1 else ""
        return [
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch", style={"background": color}),
                    _info_icon(hazards[0]) if hazards else None,
                    topic.upper(),
                    html.Span(f" · {thr_txt}", className="info-thr") if thr_txt else None,
                ]),
                html.Span([
                    html.Span(f"{count:,}", className="info-val"),
                    html.Span(f" ({pct:.1f}%)", className="info-pct"),
                ]),
            ]),
            html.Div(className="bar-track", children=[
                html.Div(className="bar-fill",
                         style={"width": f"{min(100,pct)}%", "background": color}),
            ]),
        ]

    def _sub_rows(topic):
        rows = []
        for h_name in HAZARD_TOPICS[topic]:
            h_count = int(round(result.get(h_name) or 0))
            thr_txt = _eff_threshold(h_name, overrides)
            rows.append(html.Div(className="info-row", children=[
                html.Span([
                    "└ ",
                    _info_icon(h_name),
                    _hazard_label(h_name),
                    html.Span(f" · {thr_txt}", className="info-thr") if thr_txt else None,
                ], className="info-lbl",
                    style={"paddingLeft": "22px", "color": "var(--lo)",
                           "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span(f"{h_count:,}" if h_count else "—", className="info-val",
                          style={"color": "var(--mid)", "fontWeight": "500"}),
            ]))
        return rows

    exposure_items = []
    for td in topic_data:
        topic = td["topic"]
        header = _topic_row(td)
        if topic in SUB_TOPIC_DETAIL:
            exposure_items.append(html.Details(open=True, className="topic-detail", children=[
                html.Summary(header, className="topic-detail-summary"),
                *_sub_rows(topic),
            ]))
        else:
            exposure_items.append(html.Div(header))

    thresholds_used = {}
    for topic in show_topics:
        for h_name in HAZARD_TOPICS.get(topic, []):
            thresholds_used[h_name] = (overrides or {}).get(
                h_name, HAZARD_MAP.get(h_name, {}).get("threshold"))

    export_data = {
        "region": region_name, "total_children": total,
        "male": male, "female": fema,
        "selected_topics": show_topics,
        "thresholds_used": thresholds_used,
        "exposure_by_topic": {
            td["topic"]: {"count": td["count"], "pct": round(td["pct"], 2)}
            for td in topic_data
        },
        "no_data_topics": no_data_topics,
    }
    if mhc_val:
        mhc_count_val = result.get(f"count_filter_{mhc_val}") or 0
        if mhc_count_val:
            export_data[f"mhc_gte_{mhc_val}"] = int(round(mhc_count_val))
    if mhi_val:
        mhi_count_val = result.get(f"intensity_filter_{mhi_val}") or result.get("active_intensity_filter") or 0
        if mhi_count_val:
            export_data[f"mhi_gte_p{mhi_val}"] = int(round(mhi_count_val))

    safe = re.sub(r"[^a-zA-Z0-9]", "_", region_name or "result")

    mh_rows = []
    for val, key, fallback_key, color, label in [
        (mhc_val, f"count_filter_{mhc_val}",     None,                      "#800026", f"MHC (≥{mhc_val} topics)"),
        (mhi_val, f"intensity_filter_{mhi_val}", "active_intensity_filter", "#de4968", f"MHI (≥P{mhi_val})"),
    ]:
        raw = 0
        if val:
            raw = result.get(key) or 0
            if raw == 0 and fallback_key:
                raw = result.get(fallback_key) or 0
        if val and raw > 0:
            c   = int(round(raw))
            pct = c / total * 100 if total else 0
            mh_rows.append(html.Div([
                html.Div(className="info-row", children=[
                    html.Span(className="info-lbl", children=[
                        html.Span(className="topic-swatch",
                                  style={"background": color, "borderRadius": "50%"}),
                        label,
                    ]),
                    html.Span([
                        html.Span(f"{c:,}", className="info-val"),
                        html.Span(f" ({pct:.1f}%)", className="info-pct"),
                    ]),
                ]),
                html.Div(className="bar-track", children=[
                    html.Div(className="bar-fill",
                             style={"width": f"{min(100,pct)}%", "background": color}),
                ]),
            ]))

    return html.Div([
        html.Div(className="metrics", children=[
            html.Div(className="metric", children=[
                html.Div(_fmt(total), className="metric-val"),
                html.Div("Total children", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(_fmt(male), className="metric-val", style={"color": "#3b82f6"}),
                html.Div("Male", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(_fmt(fema), className="metric-val", style={"color": "#ec4899"}),
                html.Div("Female", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(pct_f, className="metric-val", style={"color": "#ec4899"}),
                html.Div("Female %", className="metric-lbl"),
            ]),
        ]),
        html.Div(className="ps", children=[
            html.Div("Exposure by hazard topic", className="ps-label"),
            *exposure_items,
        ]) if topic_data else None,
        html.Div(className="ps", children=[
            html.Div("Multi Hazard Filters", className="ps-label"),
            *mh_rows,
        ]) if mh_rows else None,
        html.Div(className="ps", children=[
            html.Div("No hazard data available:", className="ps-label"),
            html.Div(
                [html.Div([
                    html.Span(className="topic-swatch",
                              style={"background": TOPIC_COLORS.get(t, "#ccc"),
                                     "display": "inline-block", "marginRight": "6px"}),
                    html.Span(t, style={"color": "var(--lo)"}),
                ], style={"display": "flex", "alignItems": "center", "gap": "4px",
                          "padding": "3px 16px", "fontSize": "0.8em"})
                 for t in no_data_topics],
            ),
        ]) if no_data_topics else None,
        _provenance_block([f"Region: {region_name}"]),
        html.Div(className="ps", children=[
            html.A(
                "⬇  Download results (JSON)",
                href=f"data:application/json;charset=utf-8,{_json.dumps(export_data, indent=2)}",
                download=f"ccri_{safe}.json",
                className="ps-btn-ghost",
                style={"display": "block", "textAlign": "center",
                       "textDecoration": "none", "padding": "9px 16px"},
            ),
        ]),
    ])


# ---------------------------------------------------------------------------
# Custom boundary callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("georapo-content",     "style"),
    Output("custom-content",      "style"),
    Output("btn-georapo-tab",     "className"),
    Output("btn-custom-tab",      "className"),
    Input("btn-georapo-tab",      "n_clicks"),
    Input("btn-custom-tab",       "n_clicks"),
    prevent_initial_call=True,
)
def switch_analysis_subtab(n_geo, n_custom):
    active = ctx.triggered_id
    if active == "btn-custom-tab":
        return ({"display": "none"}, {},
                "analysis-sub-tab", "analysis-sub-tab active")
    return ({}, {"display": "none"},
            "analysis-sub-tab active", "analysis-sub-tab")


# ── Custom GeoJSON upload — fully client-side ─────────────────────────────────

app.clientside_callback(
    """
    function(contents, filename) {
        var nu = window.dash_clientside.no_update;
        if (!contents) return [null,"",{display:"none"},{display:"none"},[],null,nu];
        var err = function(msg) {
            return [null, msg, {fontSize:'0.72rem',marginTop:'8px',color:'var(--red)'},
                    {display:'none'}, [], null, nu];
        };
        try {
            var b64 = contents.split(',')[1];
            var raw = atob(b64);
            if (raw.length > 1048576) return err('File exceeds 1 MB limit.');
            var gj = JSON.parse(raw);
            if (gj.type !== 'FeatureCollection') return err('Must be a GeoJSON FeatureCollection.');
            var n = (gj.features || []).length;
            if (n === 0) return err('File contains no features.');
            if (n > 100) return err('Too many features (' + n + '). Limit is 100.');
            var minLat=90,maxLat=-90,minLon=180,maxLon=-180;
            function visit(p){
                if(p[1]<minLat)minLat=p[1]; if(p[1]>maxLat)maxLat=p[1];
                if(p[0]<minLon)minLon=p[0]; if(p[0]>maxLon)maxLon=p[0];
            }
            function cx(g){
                if(!g)return; var t=g.type,c=g.coordinates;
                if(t==='Point')visit(c);
                else if(t==='MultiPoint'||t==='LineString')c.forEach(visit);
                else if(t==='MultiLineString'||t==='Polygon')c.forEach(function(r){r.forEach(visit);});
                else if(t==='MultiPolygon')c.forEach(function(p){p.forEach(function(r){r.forEach(visit);});});
                else if(t==='GeometryCollection')(g.geometries||[]).forEach(cx);
            }
            gj.features.forEach(function(f){cx(f.geometry);});
            var viewport = {bounds:[[minLat,minLon],[maxLat,maxLon]],transition:'fitBounds'};
            var props = (gj.features[0].properties || {});
            var keys  = Object.keys(props);
            var opts  = keys.map(function(k){return {label:k,value:k};});
            var def   = keys.indexOf('ucode')>=0 ? 'ucode' : (keys[0]||null);
            var kb    = (raw.length/1024).toFixed(1);
            var msg   = '✓  ' + n + ' feature' + (n!==1?'s':'') + ' loaded — ' + kb + ' KB';
            return [gj, msg, {fontSize:'0.72rem',marginTop:'8px',color:'var(--mid)'},
                    {display:'block'}, opts, def, viewport];
        } catch(e) {
            return err('Parse error: ' + e.message);
        }
    }
    """,
    Output("store-custom-geojson",   "data"),
    Output("custom-upload-status",   "children"),
    Output("custom-upload-status",   "style"),
    Output("custom-name-field-wrap", "style"),
    Output("custom-name-field",      "options"),
    Output("custom-name-field",      "value"),
    Output("main-map",               "viewport", allow_duplicate=True),
    Input("custom-upload",           "contents"),
    State("custom-upload",           "filename"),
    prevent_initial_call=True,
)

app.clientside_callback(
    "function(data) { return data || null; }",
    Output("custom-boundary-geojson", "data"),
    Input("store-custom-geojson",     "data"),
)

app.clientside_callback(
    """
    function(geojson, nameField) {
        if (!geojson || !nameField) return null;
        var features = (geojson.features || []).map(function(f) {
            var p = {}; p[nameField] = ((f.properties||{})[nameField]);
            return {type:'Feature', geometry:f.geometry, properties:p};
        });
        return {type:'FeatureCollection', features:features};
    }
    """,
    Output("store-custom-geojson-stripped", "data"),
    Input("store-custom-geojson",           "data"),
    Input("custom-name-field",              "value"),
)


@app.callback(
    Output("custom-results-panel", "children"),
    Input("custom-compute-btn",              "n_clicks"),
    State("store-custom-geojson-stripped",   "data"),
    State("custom-name-field",               "value"),
    State("store-thresholds",                "data"),
    prevent_initial_call=True,
)
def compute_custom_exposure(n_clicks, geojson, name_field, thresholds):
    if not geojson or not name_field:
        return no_update

    try:
        features = compute_exposure_custom(geojson, threshold_overrides=thresholds or None)
    except Exception as e:
        return html.Div(f"GEE error: {e}",
                        style={"color": "var(--red)", "fontSize": "0.75rem",
                               "padding": "12px 16px"})

    return _exposure_xlsx_div(features, name_field, "custom_hazard_exposure.xlsx",
                              overrides=thresholds or None)


# ── GEE Asset load + compute ──────────────────────────────────────────────────

def _exposure_xlsx_div(features, name_field, filename, overrides=None):
    """Results card + styled-Excel download for custom-boundary / GEE-asset
    exposure (per-feature exposed-children counts, by hazard)."""
    hazard_cols = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
    rows = []
    for feat in features:
        props = feat.get("properties") or {}
        row   = {name_field: props.get(name_field) or "-"}
        for col in ["total_population", "total_population_male",
                    "total_population_female"] + hazard_cols:
            row[col] = int(round(props.get(col) or 0))
        rows.append(row)
    if not rows:
        return html.Div("No results returned.",
                        style={"padding": "12px 16px", "fontSize": "0.75rem"})

    groups = [
        {"title": "Feature", "columns": [
            {"key": name_field, "label": name_field, "kind": "text"},
        ]},
        {"title": "Population (children)", "columns": [
            {"key": "total_population",        "label": "Total",  "kind": "int"},
            {"key": "total_population_male",   "label": "Male",   "kind": "int"},
            {"key": "total_population_female", "label": "Female", "kind": "int"},
        ]},
        {"title": "Exposed children by hazard", "columns": [
            {"key": h, "label": _hazard_label(h), "kind": "int"} for h in hazard_cols
        ]},
    ]
    climate_topics = [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]
    banner = ["Hazard exposure by feature"] + _thresholds_banner(climate_topics, overrides)

    n_feat = len(rows)
    return html.Div(className="ps", children=[
        html.Div([
            html.I(className="bi bi-check-circle-fill",
                   style={"color": "var(--cyan)", "marginRight": "8px"}),
            html.Strong(f"Hazard exposure for {n_feat} feature{'s' if n_feat != 1 else ''} ready."),
        ], style={"fontSize": "0.8rem", "color": "var(--hi)", "marginBottom": "10px"}),
        html.A(
            f"⬇  Download Excel ({n_feat} features × {len(hazard_cols)} hazards)",
            href=build_xlsx("Hazard exposure", groups, rows, banner),
            download=filename, className="ps-btn",
            style={"display": "block", "textAlign": "center", "textDecoration": "none",
                   "padding": "10px 16px"},
        ),
    ])


@app.callback(
    Output("store-gee-asset",         "data"),
    Output("gee-asset-status",        "children"),
    Output("gee-asset-status",        "style"),
    Output("gee-asset-name-wrap",     "style"),
    Output("gee-asset-name-field",    "options"),
    Output("gee-asset-name-field",    "value"),
    Output("boundary-layers",         "children", allow_duplicate=True),
    Output("custom-boundary-geojson", "data",     allow_duplicate=True),
    Output("main-map",                "viewport",  allow_duplicate=True),
    Input("gee-asset-load-btn",       "n_clicks"),
    State("gee-asset-input",          "value"),
    prevent_initial_call=True,
)
def load_gee_asset(n_clicks, asset_id):
    hidden    = {"display": "none"}
    visible   = {"display": "block"}
    err_style = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--red)"}
    ok_style  = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--mid)"}
    nu        = no_update

    if not asset_id or not asset_id.strip():
        return nu, nu, nu, nu, nu, nu, nu, nu, nu

    asset_id = asset_id.strip()
    try:
        n, props = get_asset_info(asset_id)
        tile_url = get_custom_asset_tile_url(asset_id)
        bounds   = get_asset_bounds(asset_id)
    except Exception as e:
        return None, f"Error: {e}", err_style, hidden, [], None, nu, nu, nu

    options    = [{"label": p, "value": p} for p in props]
    default    = "ucode" if "ucode" in props else (props[0] if props else None)
    status     = f"✓  {n} features — {asset_id.split('/')[-1]}"
    tile_layer = [dl.TileLayer(url=tile_url, attribution=GEE_ATTR, opacity=0.9)]
    viewport   = {"bounds": bounds, "transition": "fitBounds"}
    return ({"asset_id": asset_id, "n": n}, status, ok_style,
            visible, options, default, tile_layer, None, viewport)


@app.callback(
    Output("gee-asset-results-panel", "children"),
    Input("gee-asset-compute-btn",    "n_clicks"),
    State("store-gee-asset",          "data"),
    State("gee-asset-name-field",     "value"),
    State("store-thresholds",         "data"),
    prevent_initial_call=True,
)
def compute_gee_asset_exposure(n_clicks, asset_info, name_field, thresholds):
    if not asset_info or not name_field:
        return no_update
    try:
        features = compute_exposure_asset(asset_info["asset_id"],
                                          threshold_overrides=thresholds or None)
    except Exception as e:
        return html.Div(f"GEE error: {e}",
                        style={"color": "var(--red)", "fontSize": "0.75rem",
                               "padding": "12px 16px"})
    fname = asset_info["asset_id"].split("/")[-1] + "_hazard_exposure.xlsx"
    return _exposure_xlsx_div(features, name_field, fname, overrides=thresholds or None)
