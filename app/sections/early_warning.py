"""Early Warning Hazard card, Observed/Forecast and Pop analysis."""
import dash
import datetime as dt
import json as _json
import re
from config.hazards import ADMIN_DATA, FC_INFO_PREFIX, TOPIC_COLORS
from dash import ALL, ctx, dcc, html, Input, no_update, Output, State
from config.forecast import (
    FORECAST_MAP, KIND_BLURBS, KIND_LABELS, REDUCERS, threshold_for,
)
from core.forecast import (
    active_datasets_live, clamp_window, compute_forecast_exposure,
    default_window, forecast_run_label, ForecastError,
    get_forecast_exposed_tile, get_forecast_intensity_tile,
    get_forecast_preview_tile, probe_custom_asset,
)
from core.gee import (
    get_country_bounds, get_country_ucode, get_feature_bounds,
    get_selected_feature_tile_url,
)

from dash_app import app
from bootstrap import COUNTRY_NAMES
from ui.components import _fmt, POP_BASIS
from ui.registry import FORECAST_TABS
from ui.threshold import _thr_step_marks
from sections.ew_shared import _forecast_cfg, _live_spec


def _forecast_row(d, selected=None):
    """One dataset row: radio + name + topic/units meta + ⓘ.

    Reuses the Layers tab's .layer-item / .layer-item-row markup so selection
    styling, the radio dot and the info button all behave identically — no new
    CSS, and the two lists stay visually consistent.
    """
    active = " active" if d["name"] == selected else ""
    meta   = " · ".join(x for x in (d.get("topic"), d.get("units")) if x)
    return html.Div([
        html.Div(
            [
                html.Div(className="layer-radio"),
                html.Div([
                    html.Div(d["label"], className="layer-name"),
                    html.Div(meta, className="layer-meta"),
                ], className="layer-text"),
            ],
            id={"type": "fc-item", "index": d["name"]},
            className="layer-item" + active,
            n_clicks=0,
        ),
        html.Button(
            html.I(className="bi bi-info-circle"),
            id={"type": "hazard-info-btn", "index": FC_INFO_PREFIX + d["name"]},
            className="hazard-info-btn", n_clicks=0,
            title=d["label"],
        ),
    ], className="layer-item-row")


def _forecast_rows_for(kind, selected=None, expanded=None):
    """Rows for one kind ('forecast' | 'nrt'), grouped under collapsible topics.

    Topics are collapsed by default — Air Pollution alone is four rows, and a
    fully expanded list pushes the window and region controls off screen.
    `expanded` is the list of topic names currently open; the topic containing
    the selected dataset is always shown so the selection stays visible.
    """
    # active_datasets_live() rather than the config-only helper, so a dataset
    # toggled in the GEE config table appears/disappears without a redeploy.
    rows = [d for d in active_datasets_live() if d["kind"] == kind]
    if not rows:
        return [html.Div("No datasets available in this category.",
                         className="ps-caption",
                         style={"padding": "12px 16px"})]

    expanded = set(expanded or [])
    sel_topic = next((d.get("topic") for d in rows if d["name"] == selected), None)

    by_topic = {}
    for d in rows:
        by_topic.setdefault(d.get("topic") or "Other", []).append(d)

    items = []
    for topic, group in by_topic.items():
        # The selected dataset's topic is force-open: collapsing the selection
        # out of sight would make the active row unreachable without hunting.
        is_open = topic in expanded or topic == sel_topic
        items.append(html.Div(
            [
                html.Span(style={"display": "inline-block", "width": "8px",
                                 "height": "8px", "borderRadius": "2px",
                                 "background": TOPIC_COLORS.get(topic, "#9ca3af"),
                                 "marginRight": "7px", "flexShrink": "0"}),
                html.Span(topic, style={"flex": "1"}),
                html.Span(f"{len(group)}",
                          style={"fontSize": "0.6rem", "color": "var(--lo)",
                                 "marginRight": "8px"}),
                html.I(className="bi bi-chevron-down",
                       style={"fontSize": "0.65rem", "color": "var(--lo)",
                              "transition": "transform 0.2s",
                              "transform": "rotate(0deg)" if is_open
                                           else "rotate(-90deg)"}),
            ],
            id={"type": "fc-topic-header", "index": topic},
            className="layer-section-header layer-section-toggle",
            style={"paddingLeft": "24px", "fontWeight": "500",
                   "fontSize": "0.65rem", "color": "var(--mid)",
                   "background": "var(--panel)", "borderTop": "none",
                   "display": "flex", "alignItems": "center",
                   "cursor": "pointer"},
            n_clicks=0,
        ))
        items.append(html.Div(
            [_forecast_row(d, selected) for d in group],
            style={} if is_open else {"display": "none"},
        ))
    return items


def _forecast_kind_badge(cfg):
    """FORECAST / OBSERVATION chip for the selected dataset."""
    if not cfg or cfg.get("is_custom"):
        return None
    kind = cfg.get("kind", "nrt")
    cls  = "fc-badge fc-badge-forecast" if kind == "forecast" else "fc-badge fc-badge-obs"
    return html.Span(KIND_LABELS.get(kind, kind).upper(), className=cls)


_FC_CAVEAT = (
    "Both describe a specific time window, so these numbers are not comparable "
    "with the Pop Analysis tab, whose hazards are fixed 100-year return periods."
)


def tab_observed():
    """Recently observed (near-real-time) hazards: header and dataset list.
    The hazard card above the pane holds the window and aggregation."""
    return html.Div(id="tab-observed", style={"display": "none"}, children=[
        html.Div(className="exposure-method-note", style={"margin": "12px 10px 0"},
                 children=[
            html.P([
                html.Strong("Observation"), " layers report what recently "
                "happened, from near-real-time satellite and reanalysis data. ",
                _FC_CAVEAT,
            ], className="exposure-method-p", style={"marginBottom": "0"}),
        ]),
        html.Div(id="observed-list-wrap", children=[
            html.Div(id="observed-dataset-list", className="layer-list"),
        ]),
    ])


def tab_forecast():
    return html.Div(id="tab-forecast", style={"display": "none"}, children=[
        html.Div(className="exposure-method-note", style={"margin": "12px 10px 0"},
                 children=[
            html.P([
                html.Strong("Forecast"), " layers predict conditions ahead. ",
                _FC_CAVEAT,
            ], className="exposure-method-p", style={"marginBottom": "0"}),
        ]),

        html.Div(className="analysis-sub-tabs", children=[
            html.Button("Catalog", id="fc-seg-forecast",
                        className="analysis-sub-tab active", n_clicks=0,
                        style={"display": "block"}),
            html.Button("Custom", id="fc-seg-custom",
                        className="analysis-sub-tab", n_clicks=0),
        ]),
        html.Div(id="fc-seg-blurb", className="ps-caption",
                 style={"padding": "8px 16px 0"}),

        html.Div(id="forecast-list-wrap", children=[
            html.Div(id="forecast-dataset-list", className="layer-list"),
        ]),

        # Custom asset segment
        html.Div(id="forecast-custom-wrap", style={"display": "none"}, children=[
            html.Div(className="ps", children=[
                html.Div("Use any GEE Image / ImageCollection",
                         className="ps-label"),
                html.Div(style={"display":"flex","gap":"6px"}, children=[
                    dcc.Input(
                        id="forecast-asset-input",
                        placeholder="e.g. COPERNICUS/S5P/NRTI/L3_HCHO",
                        debounce=False,
                        style={"flex":"1","padding":"7px 9px","fontSize":"0.75rem",
                               "border":"1px solid var(--border2)","borderRadius":"6px",
                               "fontFamily":"Source Sans Pro, sans-serif",
                               "background":"var(--panel-h)","color":"var(--hi)"},
                    ),
                    html.Button("Load", id="forecast-asset-load-btn",
                                className="ps-btn", n_clicks=0,
                                style={"width":"auto","padding":"7px 14px",
                                       "flexShrink":"0"}),
                ]),
                html.Div(id="forecast-asset-status",
                         style={"fontSize":"0.72rem","marginTop":"8px",
                                "color":"var(--mid)"}),
                html.Div(id="forecast-asset-band-wrap", style={"display": "none"},
                         children=[
                    html.Div("Band", className="ps-label",
                             style={"marginTop": "8px"}),
                    dcc.Dropdown(id="forecast-asset-band", className="ps-select",
                                 clearable=False, searchable=True),
                ]),
            ]),
        ]),
    ])


def ew_hazard_card():
    """The Early Warning hazard: dataset, window, aggregation and threshold.

    One instance above the panes, shared by EW Visualization, Pop and Infra
    analysis, so the hazard is picked once and stays editable in every task.
    In Visualization the catalogue list below is the picker, so the dropdown
    and threshold are hidden (#ew-hazard-card.ewh-mode-vis).
    """
    return html.Div(id="ew-hazard-card", className="ewh ewh-mode-vis",
                    style={"display": "none"}, children=[
        html.Div("Hazard", className="ewh-title"),
        html.Div(id="ewh-pick", children=[
            dcc.Dropdown(id="forecast-dataset-select", className="ps-select",
                         options=[], value=None, clearable=False,
                         searchable=True,
                         placeholder="Choose an observed or forecast layer…"),
        ]),
        html.Div("Pick a layer below to preview it.", id="ewh-empty",
                 className="ps-caption"),
        html.Div(id="forecast-selected-wrap", style={"display": "none"},
                 children=[
            html.Div(id="forecast-kind-badge-wrap"),
            html.Div(id="forecast-dataset-note", className="ps-caption",
                     style={"marginTop": "6px"}),
            html.Div(id="forecast-catalog-link", style={"marginTop": "4px"}),
        ]),

        # Changing the window or aggregation re-renders the global preview.
        html.Div(id="forecast-params-section", style={"display": "none"}, children=[
            html.Div("Time window", className="ewh-label"),
            dcc.DatePickerRange(
                id="forecast-dates",
                display_format="DD MMM YYYY",
                className="forecast-datepicker",
                minimum_nights=0,
                clearable=False,
                updatemode="bothdates",
            ),
            html.Div(id="forecast-dates-note", className="ps-caption"),
            html.Div("Aggregation over the window", className="ewh-label"),
            dcc.Dropdown(
                id="forecast-reducer", className="ps-select",
                clearable=False, searchable=False,
            ),
            html.Div(id="forecast-preview-status", className="ps-caption",
                     style={"marginTop": "8px"}),

            html.Div(id="ewh-threshold", children=[
                html.Div("Exposure threshold", className="ewh-label"),
                html.Div(className="analysis-threshold-row", children=[
                    html.Div(className="athr-row-head", children=[
                        html.Span("Exposed where value >", className="athr-label"),
                        dcc.Input(id="forecast-threshold", type="number",
                                  debounce=True, className="athr-input"),
                        html.Span(id="forecast-threshold-units",
                                  className="athr-units"),
                    ]),
                    dcc.Slider(id="forecast-threshold-slider", min=0, max=1,
                               value=0, updatemode="mouseup",
                               className="athr-slider",
                               tooltip={"placement": "bottom",
                                        "always_visible": False}),
                ]),
                html.Div(id="forecast-threshold-source", className="ps-caption"),
                html.Div(id="ewh-stale", className="ewh-stale"),
            ]),

            html.Div(id="fc-goto-wrap", className="ewh-goto", children=[
                html.Button([html.I(className="bi bi-people"),
                             "  Analyse children exposed  →"],
                            id="fc-goto-analysis", className="ps-btn",
                            n_clicks=0),
                html.Button([html.I(className="bi bi-buildings"),
                             "  Analyse facilities exposed  →"],
                            id="fc-goto-infra", className="ps-btn",
                            n_clicks=0),
            ]),
        ]),
    ])


def fc_controls():
    """Region, Compute and results for EW Pop analysis, plus the stores the
    EW panes share. The hazard itself lives in ew_hazard_card()."""
    return html.Div(id="fc-controls", style={"display": "none"}, children=[
        html.Div(className="ps", children=[
            html.Div("1. Select country / territory", className="ps-label"),
            dcc.Dropdown(
                id="forecast-country-select", className="ps-select",
                options=[{"label": c, "value": c} for c in COUNTRY_NAMES],
                value=None, placeholder="Search country / territory…",
                clearable=False, searchable=True,
            ),
        ]),
        html.Div(id="forecast-level-section", style={"display": "none"},
                 children=[
            html.Div(className="ps", children=[
                html.Div("2. Analysis level", className="ps-label"),
                dcc.Dropdown(
                    id="forecast-level-select", className="ps-select",
                    options=[{"label": l, "value": l} for l in ADMIN_DATA.keys()],
                    value=None, placeholder="— select level —",
                    clearable=False, searchable=False,
                ),
                html.Div("For ADM1/ADM2, click the region on the map.",
                         className="ps-caption"),
            ]),
        ]),

        html.Div(id="forecast-badge-wrap"),
        html.Div(id="forecast-compute-wrap", style={"display": "none"}, children=[
            html.Div(className="ps", children=[
                html.Button("▶  Compute exposure", id="forecast-compute-btn",
                            className="ps-btn", n_clicks=0, disabled=True),
                html.Div("Select a region and dataset, then Compute.",
                         id="forecast-compute-hint", className="ps-caption",
                         style={"marginTop": "6px"}),
            ]),
        ]),
        dcc.Loading(
            id="forecast-results-loading", type="circle", color="#1CABE2",
            children=html.Div(id="forecast-results-panel"),
        ),

        # — Stores —
        # Pasted-asset descriptor {asset_id, kind, bands}; None when a catalog
        # dataset is selected. Keeps the two selection routes mutually exclusive.
        dcc.Store(id="store-forecast-asset",   data=None),
        dcc.Store(id="store-forecast-result",  data=None),
        dcc.Store(id="store-forecast-viz",     data=None),
        # Topic groups currently unfolded. Empty = all collapsed, the default.
        dcc.Store(id="store-fc-expanded",      data=[]),
        # Previewed layer's vis + units, so the map legend can describe it
        # before any region has been computed.
        dcc.Store(id="store-forecast-preview", data=None),
    ])


@app.callback(
    Output("ewh-stale",               "children"),
    Input("forecast-dataset-select",  "value"),
    Input("store-forecast-asset",     "data"),
    Input("forecast-dates",           "start_date"),
    Input("forecast-dates",           "end_date"),
    Input("forecast-reducer",         "value"),
    Input("forecast-threshold",       "value"),
    Input("store-forecast-viz",       "data"),
    Input("store-infra-viz",          "data"),
    Input("store-view",               "data"),
)
def ew_hazard_stale(dataset, asset, start, end, reducer, threshold,
                    fc_viz, infra_viz, view):
    """Flag a result computed on a different hazard than the card now shows;
    the result is kept so an accidental edit costs nothing."""
    done = ((fc_viz or {}).get("spec") if view == "ew-pop" else
            (infra_viz or {}).get("live") if view == "ew-infra" else None)
    if not done or done == _live_spec(dataset, asset, start, end, reducer,
                                      threshold):
        return None
    return [html.I(className="bi bi-exclamation-triangle"),
            " Hazard changed since the result below was computed. "
            "Compute again to update it."]


# ── Region selection (own dropdowns writing the shared region stores) ─────────

@app.callback(
    Output("store-country",           "data", allow_duplicate=True),
    Output("store-ucode",             "data", allow_duplicate=True),
    Output("store-bounds",            "data", allow_duplicate=True),
    Output("store-level",             "data", allow_duplicate=True),
    Output("store-clicked-ucode",     "data", allow_duplicate=True),
    Output("store-clicked-name",      "data", allow_duplicate=True),
    Output("forecast-level-section",  "style"),
    Output("forecast-level-select",   "value"),
    Input("forecast-country-select",  "value"),
    prevent_initial_call=True,
)
def forecast_on_country(country):
    if not country:
        return None, None, None, None, None, None, {"display": "none"}, None
    ucode  = get_country_ucode(country)
    bounds = get_country_bounds(ucode)
    return (country, ucode, bounds, "adm0 (Country)", None, None,
            {"display": "block"}, "adm0 (Country)")


@app.callback(
    Output("store-level",              "data", allow_duplicate=True),
    Output("store-clicked-ucode",      "data", allow_duplicate=True),
    Output("store-clicked-name",       "data", allow_duplicate=True),
    Input("forecast-level-select",     "value"),
    prevent_initial_call=True,
)
def forecast_on_level(level):
    # The dataset section is no longer gated on level — dataset is step 1 now,
    # so this only resets the clicked-region state for the new level.
    if not level:
        return no_update, None, None
    return level, None, None


@app.callback(
    Output("forecast-badge-wrap", "children"),
    Input("store-clicked-name",   "data"),
    Input("store-tab",            "data"),
)
def forecast_badge(name, tab):
    if tab not in FORECAST_TABS or not name:
        return None
    return html.Div(className="selected-badge", children=[
        html.Div("Selected region", className="selected-badge-tag"),
        html.Div(name, className="selected-badge-name"),
    ])


@app.callback(
    Output("forecast-selection-tile", "url"),
    Input("store-clicked-ucode",      "data"),
    Input("store-level",              "data"),
    Input("store-tab",                "data"),
)
def forecast_highlight_selection(ucode, level, tab):
    if (tab not in FORECAST_TABS or not ucode or not level
            or level == "adm0 (Country)"):
        return ""
    try:
        return get_selected_feature_tile_url(level, ucode)
    except Exception:
        return ""


# ── Pasted GEE asset: probe → band list ──────────────────────────────────────

@app.callback(
    Output("store-forecast-asset",     "data"),
    Output("forecast-asset-status",    "children"),
    Output("forecast-asset-status",    "style"),
    Output("forecast-asset-band-wrap", "style"),
    Output("forecast-asset-band",      "options"),
    Output("forecast-asset-band",      "value"),
    Output("forecast-dataset-select",  "value", allow_duplicate=True),
    Input("forecast-asset-load-btn",   "n_clicks"),
    State("forecast-asset-input",      "value"),
    prevent_initial_call=True,
)
def forecast_load_asset(_n, asset_id):
    err_style = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--red)"}
    ok_style  = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--hi)"}
    hidden    = {"display": "none"}
    try:
        info = probe_custom_asset(asset_id)
    except ForecastError as e:
        return None, str(e), err_style, hidden, [], None, no_update
    except Exception as e:                       # unexpected — still no traceback
        return None, f"Error: {str(e)[:160]}", err_style, hidden, [], None, no_update

    bands = info.get("bands") or []
    if not bands:
        return (None, "Asset has no readable bands.", err_style, hidden, [], None,
                no_update)
    span = (f" · {info['start']} → {info['end']}"
            if info.get("start") and info.get("end") else "")
    status = f"✓  {info['kind']} · {info['n_images']:,} image(s){span}"
    store  = {"asset_id": (asset_id or "").strip(), "kind": info["kind"],
              "bands": bands, "band": bands[0],
              "start": info.get("start"), "end": info.get("end")}
    # Selecting a pasted asset clears the catalog dropdown — the two routes are
    # mutually exclusive, and leaving both set would make the panel ambiguous.
    return (store, status, ok_style, {"display": "block"},
            [{"label": b, "value": b} for b in bands], bands[0], None)


@app.callback(
    Output("store-forecast-asset", "data", allow_duplicate=True),
    Input("forecast-asset-band",   "value"),
    State("store-forecast-asset",  "data"),
    prevent_initial_call=True,
)
def forecast_set_band(band, store):
    if not store or not band:
        return no_update
    return {**store, "band": band}


# Selecting a catalog dataset clears any pasted asset (mirror of the above).
@app.callback(
    Output("store-forecast-asset",     "data", allow_duplicate=True),
    Output("forecast-asset-status",    "children", allow_duplicate=True),
    Output("forecast-asset-band-wrap", "style", allow_duplicate=True),
    Input("forecast-dataset-select",   "value"),
    prevent_initial_call=True,
)
def forecast_clear_asset(dataset):
    if not dataset:
        return no_update, no_update, no_update
    return None, "", {"display": "none"}


# ── Dataset → window, reducer, threshold defaults ────────────────────────────

@app.callback(
    Output("forecast-params-section", "style"),
    Output("forecast-dataset-note",   "children"),
    Output("forecast-dates",          "start_date"),
    Output("forecast-dates",          "end_date"),
    Output("forecast-dates",          "min_date_allowed"),
    Output("forecast-dates",          "max_date_allowed"),
    Output("forecast-reducer",        "options"),
    Output("forecast-reducer",        "value"),
    Output("forecast-threshold",      "value"),
    Output("forecast-threshold",      "min"),
    Output("forecast-threshold",      "max"),
    Output("forecast-threshold",      "step"),
    Output("forecast-threshold-slider", "min"),
    Output("forecast-threshold-slider", "max"),
    Output("forecast-threshold-slider", "value"),
    Output("forecast-threshold-slider", "step"),
    Output("forecast-threshold-slider", "marks"),
    Output("forecast-threshold-units",  "children"),
    Input("forecast-dataset-select",  "value"),
    Input("store-forecast-asset",     "data"),
)
def forecast_dataset_params(dataset, asset):
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return ({"display": "none"}, "", None, None, None, None, [], None,
                None, None, None, None, 0, 1, 0, 0.01, {}, "")

    name = cfg["name"]
    if cfg["is_custom"]:
        # A pasted collection: window from its own time extent; a plain Image
        # has no time dimension, so fall back to a nominal recent window.
        start = asset.get("start") or (dt.date.today() - dt.timedelta(days=7)).isoformat()
        end   = asset.get("end")   or dt.date.today().isoformat()
        # Cap the initial view to the last 7 days of the asset's range so the
        # first Compute is cheap on a decade-long archive.
        e_d = dt.date.fromisoformat(end)
        s_d = max(dt.date.fromisoformat(start), e_d - dt.timedelta(days=7))
        start = s_d.isoformat()
        min_d, max_d = asset.get("start"), asset.get("end")
    else:
        start, end = default_window(name)
        today = dt.date.today()
        if cfg["kind"] == "forecast":
            min_d = (today - dt.timedelta(days=2)).isoformat()
            max_d = (today + dt.timedelta(days=16)).isoformat()
        else:
            min_d, max_d = "2015-01-01", today.isoformat()

    lo, hi = cfg.get("min", 0), cfg.get("max", 100)
    first = (cfg.get("reducers") or ["mean"])[0]
    default_thr = threshold_for(cfg, first)[0]
    step, marks = _thr_step_marks(lo, hi)
    reducers = [{"label": REDUCERS.get(r, r), "value": r}
                for r in cfg.get("reducers") or ["mean"]]
    note = cfg.get("note", "")

    return ({"display": "block"}, note, start, end, min_d, max_d,
            reducers, first,
            default_thr, lo, hi, step, lo, hi, default_thr, step, marks,
            cfg.get("units", ""))


@app.callback(
    Output("forecast-threshold",        "value", allow_duplicate=True),
    Output("forecast-threshold-slider", "value", allow_duplicate=True),
    Input("forecast-reducer",           "value"),
    State("forecast-dataset-select",    "value"),
    State("store-forecast-asset",       "data"),
    prevent_initial_call=True,
)
def forecast_reducer_threshold(reducer, dataset, asset):
    """Each aggregation has its own default threshold (e.g. wettest day vs
    average daily rainfall)."""
    cfg = _forecast_cfg(dataset, asset)
    if not cfg or reducer not in (cfg.get("reducers") or []):
        raise dash.exceptions.PreventUpdate
    thr = threshold_for(cfg, reducer)[0]
    return thr, thr


@app.callback(
    Output("forecast-threshold-source", "children"),
    Input("forecast-reducer",           "value"),
    Input("forecast-dataset-select",    "value"),
    Input("store-forecast-asset",       "data"),
)
def forecast_threshold_source(reducer, dataset, asset):
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return ""
    if reducer not in (cfg.get("reducers") or []):
        reducer = (cfg.get("reducers") or ["mean"])[0]
    value, source = threshold_for(cfg, reducer)
    if not source:
        return ""
    units = cfg.get("units", "")
    return f"Default {value:g}{' ' + units if units else ''}: {source}"


# ── Observed / Forecast: one control block, reset per tab ────────────────────

@app.callback(
    Output("fc-controls", "style"),
    Input("store-tab",    "data"),
)
def forecast_show_controls(tab):
    return {"display": "block" if tab == "ewpop" else "none"}


@app.callback(
    Output("store-forecast-result",   "data", allow_duplicate=True),
    Output("forecast-results-panel",  "children", allow_duplicate=True),
    Input("store-tab",                "data"),
    prevent_initial_call=True,
)
def forecast_reset_on_tab(tab):
    """The hazard (Hazard card) persists across EW panes; results do not."""
    if tab not in FORECAST_TABS:
        raise dash.exceptions.PreventUpdate
    return None, None


# ── Dataset segment switch + row list ────────────────────────────────────────

def _fc_kind_for_tab(tab):
    """Which dataset kind the active tab browses."""
    return "nrt" if tab == "observed" else "forecast"


@app.callback(
    Output("fc-seg-forecast",      "className"),
    Output("fc-seg-custom",        "className"),
    Output("forecast-list-wrap",   "style"),
    Output("forecast-custom-wrap", "style"),
    Output("fc-seg-blurb",         "children"),
    Input("fc-seg-forecast",       "n_clicks"),
    Input("fc-seg-custom",         "n_clicks"),
    Input("store-tab",             "data"),
)
def forecast_switch_segment(_catalog, _custom, tab):
    show, hide = {"display": "block"}, {"display": "none"}
    custom = ctx.triggered_id == "fc-seg-custom"
    cls = lambda is_custom: ("analysis-sub-tab active" if custom == is_custom
                             else "analysis-sub-tab")
    blurb = ("Paste any GEE Image or ImageCollection id to run the same "
             "exposure analysis." if custom
             else KIND_BLURBS.get(_fc_kind_for_tab(tab), "")
                  + " — pick a layer to preview it.")
    return (cls(False), cls(True),
            hide if custom else show,
            show if custom else hide,
            blurb)


@app.callback(
    Output("forecast-dataset-list",  "children"),
    Output("observed-dataset-list",  "children"),
    Output("forecast-dataset-select", "options"),
    Output("forecast-dataset-select", "placeholder"),
    Input("forecast-dataset-select", "value"),
    Input("store-fc-expanded",       "data"),
    # Tab entry re-reads the runtime override table (TTL-cached), so a dataset
    # enabled in GEE shows up without restarting the server.
    Input("store-tab",               "data"),
    Input("store-forecast-asset",    "data"),
)
def forecast_render_list(selected, expanded, tab, asset):
    """Render both kinds' lists and the Hazard card dropdown.

    One callback so a single read of active_datasets_live() serves all three,
    and the selection can never disagree between them.
    """
    active = active_datasets_live()
    options = []
    for kind, head in (("nrt", "OBSERVED"), ("forecast", "FORECAST")):
        options.append({"label": head, "value": f"__{kind}", "disabled": True})
        options += [{"label": d["label"], "value": d["name"]}
                    for d in active if d["kind"] == kind]
    placeholder = (f"Custom layer: {asset['asset_id']}"
                   if asset and asset.get("asset_id")
                   else "Choose an observed or forecast layer…")
    return (_forecast_rows_for("forecast", selected, expanded),
            _forecast_rows_for("nrt", selected, expanded),
            options, placeholder)


@app.callback(
    Output("store-fc-expanded",   "data"),
    Input({"type": "fc-topic-header", "index": ALL}, "n_clicks"),
    State("store-fc-expanded",    "data"),
    prevent_initial_call=True,
)
def forecast_toggle_topic(all_clicks, expanded):
    """Fold/unfold one topic group.

    Tracks open topics in a Store rather than the Layers tab's click-parity
    trick: this list re-renders whenever the selection changes, which resets
    n_clicks and would silently drop the fold state.
    """
    trig = ctx.triggered_id
    if not isinstance(trig, dict) or trig.get("type") != "fc-topic-header":
        return no_update
    # Rows are rebuilt on every render, so guard the spurious fire Dash emits
    # when the new headers enter the layout (same guard as forecast_select_row).
    if not any(c for c in (all_clicks or []) if c):
        return no_update
    topic = trig["index"]
    cur = list(expanded or [])
    if topic in cur:
        cur.remove(topic)
    else:
        cur.append(topic)
    return cur


@app.callback(
    Output("forecast-dataset-select", "value", allow_duplicate=True),
    Input({"type": "fc-item", "index": ALL}, "n_clicks"),
    State("forecast-dataset-select", "value"),
    prevent_initial_call=True,
)
def forecast_select_row(all_clicks, current):
    """Row click -> selected dataset. Mirrors select_hazard_layer; the list
    re-renders from the store, so the active class needs no separate output."""
    trig = ctx.triggered_id
    if not isinstance(trig, dict) or trig.get("type") != "fc-item":
        return no_update
    # Ignore the spurious initial fire when Dash instantiates the rows.
    if not any(c for c in (all_clicks or [])):
        return no_update
    name = trig["index"]
    return no_update if name == current else name


# ── Kind badge + catalog link ────────────────────────────────────────────────

@app.callback(
    Output("forecast-kind-badge-wrap", "children"),
    Output("forecast-catalog-link",    "children"),
    Output("forecast-selected-wrap",   "style"),
    Output("ewh-empty",                "style"),
    Input("forecast-dataset-select",   "value"),
    Input("store-forecast-asset",      "data"),
)
def forecast_kind_badge(dataset, asset):
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return None, None, {"display": "none"}, {"display": "block"}
    badge = _forecast_kind_badge(cfg)
    url   = FORECAST_MAP.get(cfg["name"], {}).get("source_url")
    link  = html.A("View in Earth Engine catalog ↗", href=url, target="_blank",
                   className="hi-link",
                   style={"fontSize": "0.68rem"}) if url else None
    return badge, link, {"display": "block"}, {"display": "none"}


# ── Global preview: render the layer before a region is chosen ───────────────
# Modelled on infra_load_asset (app.py:3276), which previews a whole country's
# facilities before an AOI exists. Deliberately does NOT write
# store-forecast-viz — that store means "a computed result exists" and drives
# the legend and results panel.

@app.callback(
    Output("forecast-intensity-tile", "url", allow_duplicate=True),
    Output("forecast-intensity-tile", "opacity", allow_duplicate=True),
    Output("forecast-preview-status", "children"),
    Output("forecast-preview-status", "style"),
    Output("store-forecast-preview",  "data"),
    Input("forecast-dataset-select",  "value"),
    Input("forecast-dates",           "start_date"),
    Input("forecast-dates",           "end_date"),
    Input("forecast-reducer",         "value"),
    Input("store-tab",                "data"),
    Input("store-view",               "data"),
    State("store-forecast-asset",     "data"),
    State("store-forecast-viz",       "data"),
    prevent_initial_call=True,
)
def forecast_preview(dataset, start, end, reducer, tab, view, asset, viz):
    # Every EW hazard view redraws the kept selection on entry; other panes
    # leave the tile to clear_on_leave / reset_forms_on_view.
    if tab not in FORECAST_TABS and view != "ew-infra":
        raise dash.exceptions.PreventUpdate
    if ctx.triggered_id in ("store-tab", "store-view"):
        viz = None
    base = {"fontSize": "0.72rem", "marginTop": "8px"}
    ok   = {**base, "color": "var(--mid)"}
    err  = {**base, "color": "var(--red)"}

    cfg = _forecast_cfg(dataset, asset)
    if not cfg or not start or not end or not reducer:
        return "", 0, "", {"display": "none"}, None

    # Once a result has been computed, the post-compute layers own the map;
    # re-previewing would overwrite the AOI-clipped intensity tile.
    if viz:
        return no_update, no_update, no_update, no_update, no_update

    s, e = start[:10], end[:10]
    try:
        url, vis, n = get_forecast_preview_tile(
            cfg["name"], s, e, reducer,
            custom_id=cfg.get("custom_id"), custom_band=cfg.get("custom_band"))
    except ForecastError as ex:
        return "", 0, str(ex), err, None
    except Exception as ex:
        return "", 0, f"Preview failed: {str(ex)[:160]}", err, None

    units = cfg.get("units", "")
    run = forecast_run_label(cfg["name"], s)
    msg = (f"Previewing {n:,} image(s)"
           + (f" from the {run} run" if run else "")
           + f" · {vis['min']:g}–{vis['max']:g} {units}".rstrip()
           + " — select a region below to compute exposure.")
    # Drives the map legend so the preview is readable rather than an
    # unlabelled colour wash.
    preview = {"vis": vis, "units": units, "label": cfg["label"],
               "n_images": n}
    return url, 0.75, msg, ok, preview


# Slider ↔ number-input sync. Not the pattern-matched _register_threshold_sync
# used by the hazard editors — this tab has a single, plainly-identified pair.
app.clientside_callback(
    """
    function(sliderVal, inputVal) {
        var ctx = dash_clientside.callback_context;
        if (!ctx || !ctx.triggered || ctx.triggered.length === 0) {
            return [dash_clientside.no_update, dash_clientside.no_update];
        }
        var prop = ctx.triggered[0].prop_id;
        if (prop.indexOf("forecast-threshold-slider") !== -1) {
            if (sliderVal === null || sliderVal === undefined) {
                return [dash_clientside.no_update, dash_clientside.no_update];
            }
            return [sliderVal, dash_clientside.no_update];
        }
        if (inputVal === null || inputVal === undefined || inputVal === "") {
            return [dash_clientside.no_update, dash_clientside.no_update];
        }
        return [dash_clientside.no_update, inputVal];
    }
    """,
    Output("forecast-threshold",        "value", allow_duplicate=True),
    Output("forecast-threshold-slider", "value", allow_duplicate=True),
    Input("forecast-threshold-slider",  "value"),
    Input("forecast-threshold",         "value"),
    prevent_initial_call=True,
)


# ── Date window clamping ─────────────────────────────────────────────────────

@app.callback(
    Output("forecast-dates",      "end_date", allow_duplicate=True),
    Output("forecast-dates-note", "children"),
    Input("forecast-dates",       "start_date"),
    Input("forecast-dates",       "end_date"),
    State("forecast-dataset-select", "value"),
    State("store-forecast-asset",    "data"),
    prevent_initial_call=True,
)
def forecast_clamp_dates(start, end, dataset, asset):
    if not start or not end:
        return no_update, ""
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return no_update, ""
    if cfg["is_custom"]:
        s, e = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
        days = (e - s).days + 1
        return no_update, f"{days} day window."
    s2, e2, msg = clamp_window(cfg["name"], start[:10], end[:10])
    days = (dt.date.fromisoformat(e2) - dt.date.fromisoformat(s2)).days + 1
    note = msg or f"{days} day window."
    return (e2 if e2 != end[:10] else no_update), note


# ── Compute-button enable/disable ────────────────────────────────────────────

@app.callback(
    Output("forecast-compute-wrap", "style"),
    Output("forecast-compute-btn",  "disabled"),
    Output("forecast-compute-hint", "children"),
    Output("forecast-compute-hint", "style"),
    Input("store-level",            "data"),
    Input("store-ucode",            "data"),
    Input("store-clicked-ucode",    "data"),
    Input("forecast-dataset-select", "value"),
    Input("store-forecast-asset",    "data"),
    Input("store-tab",               "data"),
)
def forecast_toggle_compute(level, ucode, clicked, dataset, asset, tab):
    if tab not in FORECAST_TABS:
        return no_update, no_update, no_update, no_update
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return {"display": "none"}, True, "", {"display": "none"}
    region_ready = bool(ucode) if level == "adm0 (Country)" else bool(clicked)
    hint = ("" if region_ready else
            ("Select a country." if not ucode else
             "Click a region on the map to select it."))
    return ({"display": "block"}, (not region_ready), hint,
            {"marginTop": "6px", "display": "none" if region_ready else "block"})


# ── Compute ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("store-forecast-result",  "data"),
    Output("forecast-results-panel", "children"),
    Output("store-forecast-viz",     "data"),
    Input("forecast-compute-btn",    "n_clicks"),
    State("forecast-dataset-select", "value"),
    State("store-forecast-asset",    "data"),
    State("forecast-dates",          "start_date"),
    State("forecast-dates",          "end_date"),
    State("forecast-reducer",        "value"),
    State("forecast-threshold",      "value"),
    State("store-level",             "data"),
    State("store-ucode",             "data"),
    State("store-clicked-ucode",     "data"),
    State("store-clicked-name",      "data"),
    State("forecast-country-select", "value"),
    prevent_initial_call=True,
)
def forecast_compute(_n, dataset, asset, start, end, reducer, threshold,
                     level, ucode, clicked, clicked_name, country):
    cfg = _forecast_cfg(dataset, asset)
    if not cfg or not level:
        return no_update, no_update, no_update

    target = ucode if level == "adm0 (Country)" else clicked
    if not target:
        return no_update, no_update, no_update
    region_name = country if level == "adm0 (Country)" else (clicked_name or target)

    try:
        thr = float(threshold if threshold not in (None, "") else 0)
    except (TypeError, ValueError):
        thr = 0.0

    s, e = (start or "")[:10], (end or "")[:10]
    try:
        result = compute_forecast_exposure(
            cfg["name"], s, e, reducer, thr, target, level,
            custom_id=cfg.get("custom_id"), custom_band=cfg.get("custom_band"))
    except ForecastError as ex:
        return None, _forecast_error(str(ex)), None
    except Exception as ex:
        return None, _forecast_error(f"Computation failed: {str(ex)[:200]}"), None

    meta = {"label": cfg["label"], "units": cfg.get("units", ""),
            "start": s, "end": e, "reducer": reducer, "threshold": thr,
            "region": region_name, "level": level,
            "n_images": result.get("_n_images", 0),
            "run": result.get("_run"),
            "dataset": cfg["name"],
            "pop_res": result.get("_pop_resolution_m", 100),
            "asset_id": cfg.get("custom_id"), "band": cfg.get("custom_band")}

    # Intensity tile is best-effort: a failed stretch must not lose the numbers.
    intensity_vis = None
    try:
        _, intensity_vis = get_forecast_intensity_tile(
            cfg["name"], s, e, reducer, target, level,
            custom_id=cfg.get("custom_id"), custom_band=cfg.get("custom_band"))
    except Exception:
        pass

    viz = {**meta, "ucode": target, "intensity_vis": intensity_vis,
           "spec": _live_spec(dataset, asset, start, end, reducer, threshold)}
    return result, render_forecast_results(result, meta), viz


@app.callback(
    Output("forecast-intensity-tile", "url"),
    Output("forecast-exposed-tile",   "url"),
    Output("main-map",                "viewport", allow_duplicate=True),
    Input("store-forecast-viz",       "data"),
    prevent_initial_call=True,
)
def forecast_update_layers(viz):
    if not viz:
        return "", "", no_update
    name  = viz["dataset"]
    kw    = {"custom_id": viz.get("asset_id"), "custom_band": viz.get("band")}
    try:
        i_url, _ = get_forecast_intensity_tile(
            name, viz["start"], viz["end"], viz["reducer"],
            viz["ucode"], viz["level"], **kw)
    except Exception:
        i_url = ""
    try:
        e_url, _ = get_forecast_exposed_tile(
            name, viz["start"], viz["end"], viz["reducer"], viz["threshold"],
            viz["ucode"], viz["level"], **kw)
    except Exception:
        e_url = ""
    try:
        viewport = {"bounds": get_feature_bounds(viz["level"], viz["ucode"]),
                    "transition": "flyTo"}
    except Exception:
        viewport = no_update
    return i_url, e_url, viewport


def _forecast_error(msg):
    return html.Div(className="ps", children=[
        html.Div(msg, style={"fontSize": "0.78rem", "color": "var(--red)",
                             "lineHeight": "1.5"}),
    ])


def render_forecast_results(result, meta):
    """Single-dataset exposure result.

    Always states the window, aggregation, threshold and image count: a live
    number is meaningless without the window it was computed over, and the
    image count is what distinguishes "no children exposed" from "no data".
    """
    if not result:
        return _forecast_error("No result.")

    exposed = int(round(result.get("exposed", 0) or 0))
    total   = int(round(result.get("total_population", 0) or 0))
    # Boys/girls exposed; *_t are region totals.
    male    = int(round(result.get("exposed_male", 0) or 0))
    fema    = int(round(result.get("exposed_female", 0) or 0))
    male_t  = int(round(result.get("total_population_male", 0) or 0))
    fema_t  = int(round(result.get("total_population_female", 0) or 0))
    pct     = (exposed / total * 100) if total else 0
    units   = meta.get("units", "")

    def _share(part, whole):
        return (part / whole * 100) if whole else 0
    thr_txt = f"{meta['threshold']:g}" + (f" {units}" if units else "")

    export = {
        "dataset": meta["label"], "dataset_id": meta.get("dataset"),
        "gee_asset": meta.get("asset_id"), "band": meta.get("band"),
        "region": meta["region"], "admin_level": meta["level"],
        "window_start": meta["start"], "window_end": meta["end"],
        "aggregation": meta["reducer"], "threshold": meta["threshold"],
        "threshold_units": units, "images_in_window": meta["n_images"],
        "model_run": meta.get("run"),
        "children_exposed": exposed, "total_children": total,
        "boys_exposed": male, "girls_exposed": fema,
        "total_boys": male_t, "total_girls": fema_t,
        "pct_exposed": round(pct, 2),
        "population_grid_m": meta["pop_res"],
        "computed_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    safe = re.sub(r"[^a-zA-Z0-9]", "_", f"{meta['region']}_{meta.get('dataset')}")

    return html.Div([
        html.Div(className="metrics", children=[
            html.Div(className="metric", children=[
                html.Div(_fmt(exposed), className="metric-val",
                         style={"color": "#e31a1c"}),
                html.Div("Children exposed", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(_fmt(total), className="metric-val"),
                html.Div("Total children", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(f"{pct:.1f}%", className="metric-val",
                         style={"color": "#e31a1c"}),
                html.Div("Share exposed", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(f"{meta['n_images']:,}", className="metric-val"),
                html.Div("Images used", className="metric-lbl"),
            ]),
        ]),
        html.Div(className="ps", children=[
            html.Div("Exposed children", className="ps-label"),
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch",
                              style={"background": "#e31a1c"}),
                    (meta["label"] or "").upper(),
                    html.Span(f" · > {thr_txt}", className="info-thr"),
                ]),
                html.Span([
                    html.Span(f"{exposed:,}", className="info-val"),
                    html.Span(f" ({pct:.1f}%)", className="info-pct"),
                ]),
            ]),
            html.Div(className="bar-track", children=[
                html.Div(className="bar-fill",
                         style={"width": f"{min(100, pct)}%",
                                "background": "#e31a1c"}),
            ]),
            html.Div(className="info-row", children=[
                html.Span("└ Boys", className="info-lbl",
                          style={"paddingLeft": "22px", "color": "var(--lo)",
                                 "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span([
                    html.Span(f"{male:,}", className="info-val",
                              style={"color": "var(--mid)", "fontWeight": "500"}),
                    html.Span(f" ({_share(male, male_t):.1f}% of boys)",
                              className="info-pct"),
                ]),
            ]),
            html.Div(className="info-row", children=[
                html.Span("└ Girls", className="info-lbl",
                          style={"paddingLeft": "22px", "color": "var(--lo)",
                                 "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span([
                    html.Span(f"{fema:,}", className="info-val",
                              style={"color": "var(--mid)", "fontWeight": "500"}),
                    html.Span(f" ({_share(fema, fema_t):.1f}% of girls)",
                              className="info-pct"),
                ]),
            ]),
        ]),
        # Provenance — the window and image count travel with the number.
        html.Div(className="ps", children=[
            html.Div("Methodology", className="hi-label",
                     style={"marginBottom": "6px"}),
            html.Div([
                html.Div(f"Dataset: {meta['label']}"),
                html.Div(f"Window: {meta['start']} → {meta['end']} "
                         f"({meta['n_images']:,} images)"),
                (html.Div(f"Model run: {meta['run']} (one run, by valid time)")
                 if meta.get("run") else None),
                html.Div(f"Aggregation: {REDUCERS.get(meta['reducer'], meta['reducer'])}"),
                html.Div(f"Exposed where value > {thr_txt}"),
                html.Div(f"Region: {meta['region']} · {meta['level']}"),
                html.Div(f"Population: {POP_BASIS}, aggregated to "
                         f"{meta['pop_res']:,} m"
                         if meta["pop_res"] != 100
                         else f"Population: {POP_BASIS}"),
            ], className="ps-caption", style={"lineHeight": "1.7"}),
            (html.Div(
                "Forecast layers are 11–44 km, so exposure is computed on a "
                "1 km population grid rather than 100 m. Totals differ from the "
                "Pop Analysis tab by well under 1%.",
                className="ps-caption",
                style={"marginTop": "6px", "marginBottom": "0",
                       "color": "var(--lo)", "fontStyle": "italic"})
             if meta["pop_res"] != 100 else None),
        ]),
        html.Div(className="ps", children=[
            html.A(
                "⬇  Download result (JSON)",
                href="data:application/json;charset=utf-8,"
                     + _json.dumps(export, indent=2),
                download=f"gchd_forecast_{safe}.json",
                className="ps-btn-ghost",
                style={"display": "block", "textAlign": "center",
                       "textDecoration": "none", "padding": "9px 16px"},
            ),
        ]),
    ])
