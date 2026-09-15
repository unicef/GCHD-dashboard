# =============================================================================
# app.py — UNICEF CCRI Global Child Hazard Database (Dash)
# =============================================================================

import os
import re
import time
import json as _json
import datetime as dt

import dash
from dash import dcc, html, Input, Output, State, ctx, no_update, ALL, MATCH
import dash_leaflet as dl
import plotly.graph_objects as go

import json as _json_mod

from config import (
    HAZARD_TOPICS, TOPIC_COLORS, ADMIN_DATA,
    HAZARDS, HAZARD_MAP, SUB_TOPIC_DETAIL, HAZARD_VIS_PALETTES,
    MHC_OPTIONS, MHI_OPTIONS, HAZARD_INFO, EXPOSURE_ONLY_TOPICS, MHC_EXCLUDED_TOPICS,
    FORCE_NULL_RULES, EXCLUDE_ISO3, is_no_data_ucode,
    infra_layer_style,
    is_binary_hazard,
    POPULATION_LAYERS, POP_LAYER_MAP, POP_VIS, POP_PREFIX,
    is_pop_layer, pop_class_of,
    gradient_spec, swatch_spec, vis_gradient_spec,
    FC_INFO_PREFIX,
)
from ai_core import initialize_ai, ask_gemini
from auth import request_otp, verify_otp, SESSION_HOURS
from gee_core import (
    initialize_gee, build_core_images,
    get_country_names,
    get_country_ucode, get_country_bounds,
    get_topic_tile_url, get_topic_tile_url_thr, get_topic_count_tile_url,
    get_pixel_score_tile_url, get_pixel_score_percentile_tile_url,
    get_hazard_tile_url, get_admin_boundary_tile_url, get_selected_feature_tile_url,
    compute_exposure_custom, compute_exposure_asset,
    get_asset_info, get_asset_bounds, get_custom_asset_tile_url,
    get_feature_at_point, compute_exposure, compute_topic_overlap,
    get_infra_tile_url, compute_facility_combined,
    infra_countries, infra_layers_for, infra_asset_id,
    get_clipped_pop_tile_url, INFRA_POP_VIS,
    get_population_tile_url, get_exposed_pop_tile_url,
    get_topic_tile_url_clipped, get_feature_bounds,
)
from forecast_config import (
    FORECAST_DATASETS, FORECAST_MAP, REDUCERS,
    CUSTOM_NAME, CUSTOM_PALETTE,
    KIND_LABELS, KIND_BLURBS, active_datasets, forecast_groups,
)
from forecast_core import (
    ForecastError, default_window, clamp_window,
    compute_forecast_exposure, probe_custom_asset,
    get_forecast_intensity_tile, get_forecast_exposed_tile,
    get_forecast_preview_tile,
    active_datasets_live, fetch_active_overrides, FORECAST_CONFIG_ASSET,
)

# ---------------------------------------------------------------------------
# GEE init (once at server start)
# ---------------------------------------------------------------------------
initialize_gee()
build_core_images()

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
COUNTRY_NAMES  = get_country_names()
initialize_ai(COUNTRY_NAMES)
UN_CLEARMAP    = "https://geoservices.un.org/arcgis/rest/services/ClearMap_WebTopo/MapServer/tile/{z}/{y}/{x}"
with open(os.path.join(os.path.dirname(__file__), "credentials", "carto_api_key.txt")) as _f:
    CARTO_API_KEY = _f.read().strip()
CARTO_FALLBACK = f"https://basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png?key={CARTO_API_KEY}"
ESRI_SAT       = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
GEE_ATTR       = "Google Earth Engine / UNICEF"
UN_ATTR        = "© United Nations Geospatial"
CARTO_ATTR     = "© OpenStreetMap contributors © CARTO"
ESRI_ATTR      = "© Esri, Maxar, Earthstar Geographics"
TOPIC_LIST     = list(HAZARD_TOPICS.keys())

# Ordered layers for the hazard list
MH_LAYERS  = ["Multi Hazard Count", "Multi Hazard Intensity"]
HAZ_LAYERS = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
ALL_LAYERS = MH_LAYERS + HAZ_LAYERS   # order matches pattern-match order in layout

# Analysis tab: one map slot per selectable hazard topic. Slots are declared
# once at layout time and filled lazily (only when a topic's legend eye is
# switched on), so a Compute never fans out into a tile request per topic.
ANALYSIS_TOPIC_SLOTS = len(HAZARD_TOPICS)


def _hazard_info_body(info):
    if not isinstance(info, dict):
        return info
    rows = []
    for label, key in [("Description", "description"), ("Units", "units"), ("Availability", "availability")]:
        val = info.get(key, "")
        if val:
            rows.append(html.Div([
                html.Span(label, className="hi-label"),
                html.Span(val,   className="hi-val"),
            ], className="hi-row"))
    source = info.get("source", "")
    url    = info.get("source_url", "")
    if source:
        rows.append(html.Div([
            html.Span("Source", className="hi-label"),
            html.A(source, href=url, target="_blank", className="hi-val hi-link") if url
            else html.Span(source, className="hi-val"),
        ], className="hi-row"))
    return rows


def _layer_label(name):
    if name in ("Multi Hazard Count", "Multi Hazard Intensity"):
        return name
    if is_pop_layer(name):
        return POP_LAYER_MAP.get(pop_class_of(name), {}).get("label", name)
    # "flood_river_2yr" → "Flood River 2yr"
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


def _layer_meta(name):
    if name == "Multi Hazard Count":
        return f"{len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)} hazard topics combined"
    if name == "Multi Hazard Intensity":
        return "Pixel-based hazard score (MHI)"
    if is_pop_layer(name):
        return "WorldPop 2025 · 100 m"
    h = HAZARD_MAP.get(name)
    return h["id"].split("/")[-1] if h else ""


def _fmt(v):
    if v is None: return "—"
    if abs(v) >= 1_000_000: return f"{v/1e6:.2f}M"
    if abs(v) >= 1_000:     return f"{v/1000:.1f}k"
    if isinstance(v, float): return f"{v:.2f}"
    return str(int(v))


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tab registry — the single source of truth for the nav rail.
#
# Order here IS the visual order of the sidebar. Everything that used to be
# duplicated across four sites (the nav_btn calls, the #panel children, and
# switch_tab's Output list + id->key dict) is now derived from this table, so
# adding or reordering a tab is a one-line edit instead of a four-site edit
# whose mistakes only show up at runtime as a tab that silently won't switch.
#
# `key`   - internal tab id held in store-tab. NOT the label: `hazard` in
#           particular is load-bearing (store-tab's default, and several
#           callbacks compare against it), so it stays put across renames.
# `label` - rail text. CSS uppercases it (.nav-label), so casing here is free.
# `title` / `desc` - the pane's own .ph header, rendered by tab_header().
# ---------------------------------------------------------------------------

TABS = [
    {"key": "hazard",         "btn": "btn-hazard",   "pane": "tab-hazard",
     "icon": "bi bi-layers",         "label": "hazard layers",
     "title": "Hazard Layers",
     "desc": "Select a layer to show the occurrence probability of each type of hazard"},
    {"key": "mh",             "btn": "btn-mh",       "pane": "tab-mh",
     "icon": "bi bi-stack",          "label": "Multi hazard",
     "title": "Multi Hazard Indicators",
     "desc": "Display areas exposed to multiple hazards"},
    {"key": "exposure",       "btn": "btn-exposure", "pane": "tab-exposure",
     "icon": "bi bi-people",         "label": "Pop Exposure",
     "title": "Children's Exposure",
     "desc": "Display child population exposed to each type of hazard at "
             "different thresholds"},
    {"key": "analysis",       "btn": "btn-analysis", "pane": "tab-analysis",
     "icon": "bi bi-bar-chart-line", "label": "Pop Analysis",
     "title": "Exposure Analysis",
     "desc": "Compute number of children exposed by admin unit"},
    {"key": "infrastructure", "btn": "btn-infra",    "pane": "tab-infrastructure",
     "icon": "bi bi-buildings",      "label": "Infra Analysis",
     "title": "Infrastructure Analysis",
     "desc": "Compute number of facilities exposed, and number of children "
             "affected by admin unit"},
    {"key": "observed",       "btn": "btn-observed", "pane": "tab-observed",
     "icon": "bi bi-cloud-sun",      "label": "Observed",
     "title": "Observed Hazards",
     "desc": "Compute number of children recently experiencing each type of "
             "hazard by admin region (under development)"},
    {"key": "forecast",       "btn": "btn-forecast", "pane": "tab-forecast",
     "icon": "bi bi-cloud-drizzle",  "label": "Forecast",
     "title": "Forecast Hazards",
     "desc": "Compute number of children forecast to experience each type of "
             "hazard by admin region (under development)"},
    {"key": "ai",             "btn": "btn-ai",       "pane": "tab-ai",
     "icon": "bi bi-robot",          "label": "AI",
     "title": "AI Assistant",
     "desc": "Ask about hazard layers or child exposure"},
]

TAB_BY_KEY = {t["key"]: t for t in TABS}

# Observed and Forecast are two tabs over one shared control stack and one set
# of map layers, so anything keyed on "the forecast tab" must accept either.
FORECAST_TABS = ("forecast", "observed")


def tab_header(key):
    """The .ph title/subtitle block for a pane, sourced from TABS so the rail
    label and the pane header can never drift apart."""
    t = TAB_BY_KEY[key]
    return html.Div(className="ph", children=[
        html.Div(t["title"], className="ph-title"),
        html.Div(t["desc"],  className="ph-sub"),
    ])


def nav_btn(icon_cls, label, btn_id, active=False):
    return html.Button(
        [html.I(className=icon_cls), html.Span(label, className="nav-label")],
        id=btn_id,
        className="nav-btn active" if active else "nav-btn",
        n_clicks=0,
    )


def sidebar():
    return html.Div(id="sidebar", style={
        "gridColumn": "1",
        "background": "#e8eaed",
        "display": "flex",
        "flexDirection": "column",
        "alignItems": "stretch",
        "width": "77px",
        "overflow": "hidden",
        "zIndex": "100",
        "borderRight": "1px solid #d1d5db",
    }, children=[
        html.Div(
            html.Img(
                src=app.get_asset_url("unicef_logo.webp") + "?v=3",
                style={
                    "width": "77px",
                    "height": "77px",
                    "display": "block",
                    "objectFit": "fill",
                }
            ),
            style={
                "width": "77px",
                "height": "77px",
                "flexShrink": "0",
                "overflow": "hidden",
                "backgroundColor": "#1CABE2",
                "borderBottom": "1px solid #d1d5db",
                "marginBottom": "6px",
                "lineHeight": "0",
                "padding": "0",
                "margin": "0",
            }
        ),
        html.Div(className="sb-nav", children=[
            nav_btn(t["icon"], t["label"], t["btn"], active=(i == 0))
            for i, t in enumerate(TABS)
        ]),
    ])


def _layer_item(name):
    return html.Div([
        html.Div(
            [
                html.Div(className="layer-radio"),
                html.Div([
                    html.Div(_layer_label(name), className="layer-name"),
                    html.Div(_layer_meta(name),  className="layer-meta"),
                ], className="layer-text"),
            ],
            id={"type": "layer-item", "index": name},
            className="layer-item",
            n_clicks=0,
        ),
        html.Button(
            html.I(className="bi bi-info-circle"),
            id={"type": "hazard-info-btn", "index": name},
            className="hazard-info-btn",
            n_clicks=0,
            title=_layer_label(name),
        ),
    ], className="layer-item-row")


def tab_hazard_layers():
    # Group hazard layers by topic for the list
    seen   = set()
    by_topic = []
    for topic, names in HAZARD_TOPICS.items():
        group = [n for n in names if n in HAZARD_MAP and n not in seen]
        if group:
            by_topic.append((topic, group))
            seen.update(group)

    items = []
    # Multi-hazard section
    items.append(html.Div("Multi Hazard", className="layer-section-header"))
    for n in MH_LAYERS:
        items.append(_layer_item(n))

    # Per-topic sections
    items.append(html.Div("Hazard Intensity", className="layer-section-header"))
    for topic, names in by_topic:
        color     = TOPIC_COLORS.get(topic, "#9ca3af")
        foldable  = topic in SUB_TOPIC_DETAIL
        header    = html.Div(
            [
                html.Span(style={"display":"inline-block","width":"8px","height":"8px",
                                 "borderRadius":"2px","background":color,
                                 "marginRight":"7px","verticalAlign":"middle",
                                 "flexShrink":"0"}),
                html.Span(topic, style={"flex":"1"}),
                html.I(className="bi bi-chevron-down",
                       id={"type":"topic-chevron","index":topic},
                       style={"fontSize":"0.65rem","color":"var(--lo)",
                              "transition":"transform 0.2s"}) if foldable else None,
            ],
            id={"type":"topic-group-header","index":topic} if foldable else f"topic-hdr-{topic}",
            className="layer-section-header layer-section-toggle" if foldable else "layer-section-header",
            style={"paddingLeft":"24px","fontWeight":"500","fontSize":"0.65rem",
                   "color":"var(--mid)","background":"var(--panel)","borderTop":"none",
                   "display":"flex","alignItems":"center","cursor":"pointer" if foldable else "default"},
            n_clicks=0 if foldable else None,
        )
        items.append(header)
        layer_divs = [_layer_item(n) for n in names]
        if foldable:
            items.append(html.Div(
                layer_divs,
                id={"type":"topic-group-body","index":topic},
            ))
        else:
            items.extend(layer_divs)

    # ── Population section — WorldPop 2025 grids, grouped by age band. Reuses
    # _layer_item so selection/click behaviour matches the hazard layers. ──
    items.append(html.Div("Population", className="layer-section-header"))
    for group in dict.fromkeys(p["group"] for p in POPULATION_LAYERS):
        items.append(html.Div(
            group,
            className="layer-section-header",
            style={"paddingLeft": "24px", "fontWeight": "500",
                   "fontSize": "0.65rem", "color": "var(--mid)",
                   "background": "var(--panel)", "borderTop": "none"},
        ))
        items.extend(_layer_item(POP_PREFIX + p["id"])
                     for p in POPULATION_LAYERS if p["group"] == group)

    return html.Div(id="tab-hazard", children=[
        tab_header("hazard"),
        html.Div(items, className="layer-list"),
    ])


def _exposure_topic_row(t, selected):
    """One radio-selectable topic row. The whole row is the click target; the
    radio mirrors the single-select state (aria-checked for accessibility)."""
    return html.Div(
        [
            html.Div(className="topic-radio" + (" checked" if selected else ""),
                     **{"aria-checked": "true" if selected else "false",
                        "role": "radio"}),
            html.Div(className="topic-swatch",
                     style={"background": TOPIC_COLORS.get(t, "#888")}),
            html.Span(t, className="topic-name"),
        ],
        id={"type": "topic-item", "index": t},
        className="topic-item active" if selected else "topic-item",
        n_clicks=0,
    )


def _exposure_inline_editor(topic):
    """The 'Adjust thresholds' editor for the selected topic, rendered inline
    directly beneath its row. Ids preserved so the toggle/sync/badge callbacks
    keep working."""
    return html.Div(className="exposure-thr-inline", children=[
        html.Div(id="exposure-thr-toggle", className="athr-toggle", n_clicks=0,
                 children=[
            html.I(className="bi bi-chevron-right", id="exposure-thr-chevron"),
            html.Span("Adjust thresholds", className="athr-toggle-label"),
            html.Span("", id="exposure-thr-badge", className="athr-badge"),
        ]),
        html.Div("Changing a threshold updates the exposure map for this topic.",
                 className="ps-caption", style={"marginTop": "4px",
                                                "color": "var(--mid)"}),
        html.Div(id="exposure-thr-body", style={"display": "none"}, children=[
            html.Div(build_threshold_editor_for("exposure", [topic]),
                     id="exposure-threshold-block",
                     className="analysis-threshold-block"),
        ]),
    ])


def tab_exposure():
    return html.Div(id="tab-exposure", style={"display": "none"}, children=[
        tab_header("exposure"),
        # Radio-selectable topic list; the selected row carries the inline
        # threshold editor. Rebuilt by render_exposure_topics on selection.
        html.Div(id="exposure-topic-list",
                 children=[_exposure_topic_row(t, False) for t in TOPIC_LIST]),
        html.Div(className="exposure-method-note", children=[
            html.Div("Methodology", className="hi-label", style={"marginBottom": "6px"}),
            html.P(
                "All exposure estimates use WorldPop's global gridded children population "
                "estimate (under 18) for 2025 at 100 m spatial resolution.",
                className="exposure-method-p",
            ),
            html.P(
                "For topics with multiple hazard layers (e.g. Drought combines agricultural "
                "and meteorological drought), hazard coverage is first computed independently "
                "for each layer at its own threshold. An OR union is then applied across "
                "layers to derive the combined topic footprint, which is subsequently "
                "overlaid with the WorldPop under-18 grid to estimate the number of "
                "children exposed.",
                className="exposure-method-p",
            ),
        ]),
    ])


def tab_mh():
    # Colour ramps for these layers live in the floating map legend
    # (MHC_PALETTE / MHI_PALETTE), rendered once a layer is actually selected.
    return html.Div(id="tab-mh", style={"display": "none"}, children=[
        tab_header("mh"),
        html.Div(className="ps", children=[
            html.Div("Hazard Count (MHC)", className="ps-label"),
            html.Div("Areas exposed to ≥ N simultaneous hazard topics", className="ps-caption"),
            dcc.Dropdown(
                id="mhc-select", className="ps-select",
                options=[{"label": v, "value": v} for v in MHC_OPTIONS],
                value=None, placeholder="None",
                clearable=True, searchable=False,
            ),
        ]),
        html.Div(className="ps", children=[
            html.Div("Hazard Intensity (MHI)", className="ps-label"),
            html.Div("Areas with pixel hazard score above global percentile", className="ps-caption"),
            dcc.Dropdown(
                id="mhi-select", className="ps-select",
                options=[{"label": f"P{v}", "value": v} for v in MHI_OPTIONS],
                value=None, placeholder="None",
                clearable=True, searchable=False,
            ),
        ]),
        html.Div(className="exposure-method-note", children=[
            html.Div("Methodology", className="hi-label", style={"marginBottom": "6px"}),
            html.P([
                html.Strong("MHC — "),
                "Each hazard topic is flagged at the pixel level when its threshold is exceeded. "
                "For topics with multiple layers (e.g. Drought combines agricultural and "
                "meteorological drought), an OR union is applied across layers before flagging. "
                "MHC then counts how many topics flag each pixel; only pixels reaching the "
                "user-selected minimum count N are displayed.",
            ], className="exposure-method-p"),
            html.P([
                html.Strong("MHI — "),
                "A continuous pixel-based hazard score is derived by combining the normalised "
                "intensity values across all hazard layers, integrating both the breadth and "
                "severity of co-occurring hazards. The user selects a global percentile "
                "threshold (e.g. P90); only pixels whose score exceeds that percentile are "
                "highlighted on the map.",
            ], className="exposure-method-p"),
        ]),
    ])


def tab_analysis():
    return html.Div(id="tab-analysis", style={"display": "none"}, children=[
        tab_header("analysis"),
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


def _infra_color_for_asset(asset_id):
    """Map an infrastructure asset id to its facility-type colour (no '#').

    The asset id ends in {iso3}_{source}_{layer}, so the facility type is
    readable straight from the name — no need to scan the discovered assets.
    Falls back to the neutral colour for a custom/unrecognised asset."""
    stem = (asset_id or "").split("/")[-1]
    for layer, label in (("health_facilities", "Health Facilities"),
                         ("water_points",      "Water Points"),
                         ("schools",           "Schools")):
        if stem.endswith("_" + layer):
            return infra_layer_style(label)["color"].lstrip("#")
    return "e67e22"


def _infra_layer_options(country=None):
    """Layer dropdown options for the selected country — only its uploaded,
    source-qualified layers (e.g. 'Schools (Giga)'). Empty until a country with
    assets is picked. Discovered from GEE, so a newly uploaded layer appears
    without a code change."""
    return [{"label": name, "value": name}
            for name in infra_layers_for(country)]


def tab_infrastructure():
    return html.Div(id="tab-infrastructure", style={"display": "none"}, children=[
        tab_header("infrastructure"),

        # 1. Region — own selector that writes to the shared region stores.
        # Analyses are adm2-only, so selecting a country hard-sets adm2 and
        # enables map district selection immediately (no level dropdown).
        html.Div(className="ps", children=[
            html.Div("1. Select country / territory", className="ps-label"),
            dcc.Dropdown(
                id="infra-country-select", className="ps-select",
                # Populated by infra_populate_countries on tab entry, not here:
                # tab_infrastructure() runs once at import, so baking the list
                # in would freeze it at server start and defeat discovery.
                options=[],
                value=None, placeholder="Search country / territory…",
                clearable=False, searchable=True,
            ),
        ]),
        html.Div(id="infra-level-section", style={"display": "none"}, children=[
            html.Div(id="infra-selected-badge-wrap"),
            html.Div("Click a district on the map to analyze.",
                     className="info-box", id="infra-region-hint"),
        ]),

        # 2. Facility layer — options depend on the selected country (only its
        # uploaded, source-qualified layers). Populated on country select.
        html.Div(className="ps", children=[
            html.Div("2. Facility layer", className="ps-label"),
            dcc.Dropdown(
                id="infra-layer-select", className="ps-select",
                options=[], value=None,
                placeholder="— select a country first —",
                clearable=True, searchable=False,
            ),
            html.Button(
                [html.I(className="bi bi-plus-circle"),
                 html.Span("Add your own facility data")],
                id="infra-add-layer-btn", className="link-btn", n_clicks=0,
            ),
            html.Div(style={"display": "flex", "gap": "6px", "marginTop": "8px"}, children=[
                dcc.Input(
                    id="infra-asset-input",
                    placeholder="…or paste a GEE asset path",
                    debounce=False,
                    style={"flex": "1", "padding": "7px 9px", "fontSize": "0.75rem",
                           "border": "1px solid var(--border2)", "borderRadius": "6px",
                           "fontFamily": "Source Sans Pro, sans-serif",
                           "background": "var(--panel-h)", "color": "var(--hi)"},
                ),
                html.Button("Load", id="infra-asset-load-btn", className="ps-btn",
                            n_clicks=0,
                            style={"width": "auto", "padding": "7px 14px", "flexShrink": "0"}),
            ]),
            html.Div(id="infra-asset-status",
                     style={"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--mid)"}),
        ]),

        # 3. Hazard topics
        html.Div(className="ps", children=[
            html.Div("3. Hazard topics", className="ps-label"),
            dcc.Dropdown(
                id="infra-topic-select", className="ps-select",
                options=[{"label": t, "value": t} for t in HAZARD_TOPICS
                         if t not in EXPOSURE_ONLY_TOPICS],
                value=[], multi=True, placeholder="All hazards (default)",
                clearable=True, searchable=False,
            ),
            html.Div("Exposed population is attributed to each facility's "
                     "nearest-neighbour (Voronoi) catchment across the district.",
                     className="ps-caption"),
            # Collapsible threshold editor (mirrors the Analysis tab).
            html.Div(id="infra-thr-toggle", className="athr-toggle", n_clicks=0,
                     children=[
                html.I(className="bi bi-chevron-right", id="infra-thr-chevron"),
                html.Span("Adjust thresholds", className="athr-toggle-label"),
                html.Span("", id="infra-thr-badge", className="athr-badge"),
            ]),
            html.Div(id="infra-thr-body", style={"display": "none"}, children=[
                html.Div(id="infra-threshold-block",
                         className="analysis-threshold-block"),
            ]),
        ]),

        html.Div(className="ps", children=[
            html.Div(className="infra-help", style={"display": "flex", "gap": "6px",
                     "alignItems": "flex-start", "marginBottom": "8px"}, children=[
                html.I(className="bi bi-info-circle",
                       style={"color": "var(--cyan)", "marginTop": "2px",
                              "flexShrink": "0"}),
                html.Div(className="ps-caption", style={"color": "var(--mid)"}, children=[
                    html.Div([html.Strong("How to run: "),
                              "click a district (ADM2) on the map, then press Compute. "
                              "Analysis is per-district — there is no country-wide "
                              "summary, because per-facility Voronoi computation over "
                              "many points is slow even for small areas."]),
                    html.Div([html.Strong("Thresholds: "),
                              "the summary applies your current hazard thresholds set "
                              "above."], style={"marginTop": "4px"}),
                ]),
            ]),
            html.Button("Compute", id="infra-compute-btn", className="ps-btn",
                        n_clicks=0, disabled=True),
        ]),
        dcc.Loading(
            id="infra-results-loading", type="circle", color="#1CABE2",
            children=html.Div(id="infra-results-panel"),
        ),
        # Per-facility CSV download (phase 2) — spinner until ready.
        html.Div(className="ps", children=[
            dcc.Loading(
                id="infra-download-loading", type="circle", color="#1CABE2",
                children=html.Div(id="infra-download-wrap"),
            ),
        ]),

        # — Stores — the facility asset is empty until the user picks a country
        # and a layer (layers are country-specific now, so there's no sensible
        # global default to seed). infra_load_asset sets it on selection.
        dcc.Store(id="store-infra-asset", data=None),
        # Last computed viz (asset/ucode/buffer/color) — lets update_data_layers
        # rebuild the infra tiles so they aren't wiped on store/tab changes.
        dcc.Store(id="store-infra-viz", data=None),
        # Phase-2 trigger: per-facility download computed after the summary.
        dcc.Store(id="store-infra-pending", data=None),
    ])


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
    """Recently observed (near-real-time) hazards.

    Carries only its own dataset list. The window / region / threshold controls
    and every store live once in tab_forecast() — Dash ids must be unique, so
    they cannot be duplicated per pane — and are shared by both tabs, with the
    active tab supplying the dataset `kind`.
    """
    return html.Div(id="tab-observed", style={"display": "none"}, children=[
        tab_header("observed"),
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
        html.Div("Set the window, region and threshold on the Forecast tab — "
                 "the controls are shared between both tabs.",
                 className="ps-caption", style={"padding": "10px 16px"}),
    ])


def tab_forecast():
    return html.Div(id="tab-forecast", style={"display": "none"}, children=[
        tab_header("forecast"),
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

        # Selected-dataset summary — badge, caveat, catalog link.
        html.Div(id="forecast-selected-wrap", style={"display": "none"},
                 children=[
            html.Div(className="ps", children=[
                html.Div(id="forecast-kind-badge-wrap"),
                html.Div(id="forecast-dataset-note", className="ps-caption",
                         style={"marginTop": "6px"}),
                html.Div(id="forecast-catalog-link", style={"marginTop": "4px"}),
            ]),
        ]),

        # Hidden holder for the selected dataset name. Deliberately a
        # RadioItems, not a Store: nine existing callbacks read
        # `forecast-dataset-select.value`, and only an input component exposes
        # `.value` — so the row list can replace the dropdown without touching
        # a single downstream callback.
        dcc.RadioItems(id="forecast-dataset-select", options=[], value=None,
                       style={"display": "none"}),

        # 2. Window + aggregation. Changing either re-renders the global preview.
        html.Div(id="forecast-params-section", style={"display": "none"}, children=[
            html.Div(className="ps", children=[
                html.Div("2. Time window", className="ps-label"),
                dcc.DatePickerRange(
                    id="forecast-dates",
                    display_format="DD MMM YYYY",
                    className="forecast-datepicker",
                    minimum_nights=0,
                    clearable=False,
                    updatemode="bothdates",
                ),
                html.Div(id="forecast-dates-note", className="ps-caption"),
            ]),
            html.Div(className="ps", children=[
                html.Div("3. Aggregation over the window", className="ps-label"),
                dcc.Dropdown(
                    id="forecast-reducer", className="ps-select",
                    clearable=False, searchable=False,
                ),
                html.Div(id="forecast-preview-status", className="ps-caption",
                         style={"marginTop": "8px"}),
            ]),

            # 4. Country — only now, once the layer is visible on the map.
            html.Div(className="ps", children=[
                html.Div("4. Select country / territory", className="ps-label"),
                dcc.Dropdown(
                    id="forecast-country-select", className="ps-select",
                    options=[{"label": c, "value": c} for c in COUNTRY_NAMES],
                    value=None, placeholder="Search country / territory…",
                    clearable=False, searchable=True,
                ),
            ]),

            # 5. Level
            html.Div(id="forecast-level-section", style={"display": "none"},
                     children=[
                html.Div(className="ps", children=[
                    html.Div("5. Analysis level", className="ps-label"),
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

            html.Div(className="ps", children=[
                html.Div("6. Exposure threshold", className="ps-label"),
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
                html.Div("Children are counted where the aggregated value is "
                         "strictly greater than this threshold.",
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


def tab_ai():
    return html.Div(id="tab-ai", style={"display": "none"}, children=[
        tab_header("ai"),
        html.Div(className="exposure-method-note", style={"margin": "12px 10px 0"}, children=[
            html.Div("Under Development", className="hi-label", style={"marginBottom": "6px"}),
            html.P([
                "The AI application on GCHD is currently under development. "
                "To test the initial version, please contact ",
                html.A("dokim@unicef.org", href="mailto:dokim@unicef.org", className="hi-link"),
                ".",
            ], className="exposure-method-p"),
        ]),
        dcc.Store(id="store-ai-auth",    data=False),
        dcc.Store(id="store-ai-pending", data=None),
        # ── Auth gate ──
        html.Div(id="ai-auth-gate", children=[
            html.Div(className="ai-auth-card", children=[
                html.Div("AI Assistant Access",
                         style={"fontSize":"0.72rem","fontWeight":"700","color":"var(--hi)",
                                "textTransform":"uppercase","letterSpacing":"0.08em",
                                "marginBottom":"16px"}),
                dcc.Input(id="ai-auth-id",  placeholder="User ID",
                          type="text",     className="ai-auth-input", n_submit=0),
                dcc.Input(id="ai-auth-pwd", placeholder="Password",
                          type="password", className="ai-auth-input", n_submit=0),
                html.Div(id="ai-auth-error",
                         style={"fontSize":"0.7rem","color":"var(--red)",
                                "marginBottom":"8px","minHeight":"16px"}),
                html.Button("Sign In", id="ai-auth-btn", className="ps-btn", n_clicks=0),
            ]),
        ]),
        # ── Main AI interface (hidden until authenticated) ──
        html.Div(id="ai-main", style={"display": "none"}, children=[
            html.Div(className="ps", children=[
                dcc.Textarea(
                    id="ai-query-input",
                    placeholder="e.g. Show river flood layer in Bangladesh, or "
                                "How many children are exposed to heatwaves in India?",
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
                html.Button("Ask", id="ai-submit-btn", className="ps-btn",
                            n_clicks=0, style={"marginTop":"8px"}),
            ]),
            html.Div(id="ai-confirm-line",
                     style={"minHeight": "22px", "padding": "6px 12px 0"}),
            html.Div(id="ai-response-panel"),
        ]),
    ])


def map_component():
    return html.Div(id="map-container", children=[
        # Map loading indicator — a plain badge, not dcc.Loading.
        #
        # dcc.Loading only tracks the server callback, which returns as soon as
        # the tile URL is built; the tiles themselves are still being fetched
        # and painted by Leaflet for a good while after that. dash-leaflet's
        # TileLayer exposes n_loads (incremented on layer 'load'), so the
        # clientside callback below turns it on when a URL changes and off when
        # the corresponding layer reports it has finished.
        html.Div(id="map-loading", className="map-loading",
                 style={"display": "none"}, children=[
            html.Div(className="map-loading-spinner"),
            html.Span("Loading layer…", className="map-loading-text"),
        ]),
        # Unified floating legend — every tab renders into this one overlay
        # (see render_map_legend). Collapsible via the header chevron.
        html.Div(id="map-legend", className="map-legend",
                 style={"display": "none"}, children=[
            html.Div(id="map-legend-header", className="map-legend-header",
                     n_clicks=0, children=[
                html.Span("Legend", className="map-legend-title"),
                html.I(className="bi bi-chevron-up", id="map-legend-chevron"),
            ]),
            html.Div(id="map-legend-body"),
        ]),
        # Basemap toggle (map ↔ satellite) — bottom-left floating control.
        html.Button(
            [html.I(className="bi bi-globe-americas"), html.Span("Satellite")],
            id="basemap-toggle", className="basemap-toggle", n_clicks=0,
            title="Toggle satellite basemap",
        ),
        # PLACEHOLDER COPY — pending comms/legal sign-off.
        html.Div(id="map-disclaimer", className="map-disclaimer", children=[
            html.Span(
                "Modelled estimates, not observed impacts. Boundaries and names "
                "shown do not imply endorsement or acceptance by the United "
                "Nations.",
                className="map-disclaimer-text",
            ),
        ]),
        dl.Map(
            id="main-map",
            center=[10, 20], zoom=3, zoomControl=False,
            minZoom=3,
            maxBounds=[[-60, -190], [80, 190]],
            maxBoundsViscosity=1.0,
            children=[
                dl.TileLayer(
                    url=CARTO_FALLBACK,
                    attribution=CARTO_ATTR,
                    maxZoom=19,
                ),
                dl.TileLayer(
                    url=UN_CLEARMAP,
                    attribution=UN_ATTR,
                    maxZoom=6,
                ),
                # Satellite basemap (Esri World Imagery) — hidden until toggled.
                dl.TileLayer(id="sat-basemap", url=ESRI_SAT,
                             attribution=ESRI_ATTR, maxZoom=19, opacity=0),
                dl.ZoomControl(position="topright"),
                # Metric scalebar — bottom-right keeps it clear of the
                # basemap toggle (bottom-left) and the attribution strip.
                dl.ScaleControl(position="bottomright", metric=True,
                                imperial=False, maxWidth=140),
                dl.LayerGroup(id="data-layers"),
                dl.LayerGroup(id="boundary-layers"),
                dl.LayerGroup(id="selection-layer"),
                dl.GeoJSON(
                    id="custom-boundary-geojson",
                    data=None,
                    options={"style": {"color": "#e67e22", "weight": 2,
                                       "fillOpacity": 0.05, "opacity": 0.9}},
                ),
                # ── Infrastructure layers: dedicated, stably-identified top-level
                # components so the legend's clientside eye toggles can flip each
                # one's opacity/visibility reliably. ──
                dl.TileLayer(id="infra-selection-tile", url="", opacity=0.75),
                dl.TileLayer(id="infra-pop-tile", url="", opacity=0.8),
                dl.GeoJSON(
                    id="infra-voronoi-geojson",
                    data=None,
                    options={"style": {"color": "#1CABE2", "weight": 1,
                                       "fillOpacity": 0.0, "opacity": 0.7}},
                ),
                dl.TileLayer(id="infra-points-tile", url="", opacity=0.9),
                # ── Analysis result layers: exposed-population raster plus a
                # fixed pool of per-topic hazard slots. Declared at layout time
                # (not inside a LayerGroup) so the clientside eye toggles bind
                # to stable ids and hiding a layer costs no server round-trip.
                dl.TileLayer(id="analysis-selection-tile", url="", opacity=0.75),
                dl.TileLayer(id="analysis-exposed-tile", url="", opacity=0.85),
                *[dl.TileLayer(id={"type": "analysis-topic-tile", "index": i},
                               url="", opacity=0)
                  for i in range(ANALYSIS_TOPIC_SLOTS)],
                # ── Forecast tab layers: same stable-id pattern as Analysis, so
                # the legend eye toggles work without any new clientside code. ──
                dl.TileLayer(id="forecast-selection-tile", url="", opacity=0.75),
                dl.TileLayer(id="forecast-intensity-tile", url="", opacity=0),
                dl.TileLayer(id="forecast-exposed-tile", url="", opacity=0.85),
            ],
            style={"height": "100vh", "width": "100%"},
        ),
    ])


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
# This instance is served under the /ew subpath on the shared gchd.unicef.org
# domain (alongside the main instance at the root path) — see TECHNICAL_DOCUMENTATION.md.
_BASE_PATHNAME = "/ew/"

app = dash.Dash(__name__, suppress_callback_exceptions=True, url_base_pathname=_BASE_PATHNAME)
app.title = "UNICEF GCHD — Global Child Hazard Database (EW)"
server = app.server

from werkzeug.middleware.proxy_fix import ProxyFix
app.server.wsgi_app = ProxyFix(app.server.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ---------------------------------------------------------------------------
# Auth — Flask secret key (auto-generated on first run)
# ---------------------------------------------------------------------------
import secrets as _secrets, datetime as _dt
from flask import session as _fsess, request as _freq, redirect as _fredirect

# Scope the session cookie to /ew and give it its own name so it can never be
# confused with the main instance's session cookie on the same domain.
app.server.config["SESSION_COOKIE_PATH"] = _BASE_PATHNAME.rstrip("/") + "/"
app.server.config["SESSION_COOKIE_NAME"] = "gchd_ew_session"

_key_path = os.path.join(os.path.dirname(__file__), "credentials", "flask_secret.txt")
if os.path.exists(_key_path):
    app.server.secret_key = open(_key_path).read().strip()
else:
    _key = _secrets.token_hex(32)
    open(_key_path, "w").write(_key)
    app.server.secret_key = _key

_PUBLIC_PATHS = tuple(_BASE_PATHNAME.rstrip("/") + p
                      for p in ("/login", "/assets/", "/_dash-component-suites/", "/favicon.ico"))


def _render_login(step, email, error):
    _CSS = (
        "body{display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;margin:0;background:var(--bg)}"
        ".lc{background:var(--panel);border:1px solid var(--border2);"
        "border-radius:10px;padding:36px 32px;width:340px;"
        "box-shadow:0 4px 24px rgba(0,0,0,.3)}"
        ".lc-icon{color:var(--cyan);font-size:2rem;text-align:center;margin-bottom:6px}"
        ".lc-title{font-size:.88rem;font-weight:700;color:var(--hi);text-align:center;"
        "text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}"
        ".lc-sub{font-size:.74rem;color:var(--lo);text-align:center;margin-bottom:28px}"
        "label{font-size:.7rem;color:var(--lo);text-transform:uppercase;"
        "letter-spacing:.06em;display:block;margin-bottom:5px}"
        "input{width:100%;box-sizing:border-box;padding:9px 12px;font-size:.82rem;"
        "border:1px solid var(--border2);border-radius:6px;"
        "background:var(--panel-h);color:var(--hi);"
        "font-family:'Source Sans Pro',sans-serif;outline:none;margin-bottom:18px}"
        "input:focus{border-color:var(--cyan)}"
        ".lc-btn{width:100%;padding:10px;font-size:.84rem;font-weight:600;"
        "background:var(--cyan);color:#fff;border:none;"
        "border-radius:6px;cursor:pointer;font-family:inherit}"
        ".lc-btn:hover{opacity:.85}"
        ".lc-err{font-size:.74rem;color:var(--red);margin-bottom:12px;min-height:16px}"
        ".lc-hint{font-size:.7rem;color:var(--lo);text-align:center;margin-top:18px}"
        ".lc-hint a{color:var(--cyan);text-decoration:none}"
    )
    safe_email = (email or "").replace('"', "").replace("<", "").replace(">", "")
    if step == "email":
        form_inner = (
            '<label>UNICEF Email Address</label>'
            '<input type="email" name="email" placeholder="yourname@unicef.org"'
            ' value="' + safe_email + '" autofocus required>'
        )
        btn_label = "Send Verification Code"
        hint = "A 6-digit code will be sent to your inbox."
    else:
        form_inner = (
            '<div style="font-size:.74rem;color:var(--mid);margin-bottom:16px">'
            "Code sent to <strong>" + safe_email + "</strong></div>"
            '<input type="hidden" name="email" value="' + safe_email + '">'
            "<label>6-Digit Verification Code</label>"
            '<input type="text" name="code" placeholder="000000" maxlength="6"'
            ' autofocus required inputmode="numeric"'
            ' style="letter-spacing:.3em;font-size:1.1rem;text-align:center">'
        )
        btn_label = "Sign In"
        hint = f'<a href="{_BASE_PATHNAME}login">Use a different email</a>'
    err_html = '<div class="lc-err">' + error + "</div>" if error else '<div class="lc-err"></div>'
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>UNICEF Hazard DB — Sign In</title>"
        f'<link rel="stylesheet" href="{_BASE_PATHNAME}assets/style.css">'
        "<style>" + _CSS + "</style>"
        "</head><body>"
        '<div class="lc">'
        '<div class="lc-icon"><i class="bi bi-globe2"></i></div>'
        '<div class="lc-title">UNICEF Global Child Hazard Database</div>'
        '<div class="lc-sub">Staff access only · @unicef.org required</div>'
        f'<form method="POST" action="{_BASE_PATHNAME}login">'
        + form_inner + err_html
        + '<button type="submit" class="lc-btn">' + btn_label + "</button>"
        "</form>"
        '<div class="lc-hint">' + hint + "</div>"
        "</div></body></html>"
    )


# @app.server.before_request
# def _require_login():
#     if any(_freq.path.startswith(p) for p in _PUBLIC_PATHS):
#         return
#     if not _fsess.get("authed"):
#         return _fredirect(_BASE_PATHNAME + "login")


@app.server.route(_BASE_PATHNAME + "login", methods=["GET", "POST"])
def _login_page():
    error = ""
    step  = "email"
    email = ""
    if _freq.method == "POST":
        email = _freq.form.get("email", "").strip().lower()
        code  = _freq.form.get("code", "").strip()
        if code:
            if verify_otp(email, code):
                _fsess["authed"] = True
                _fsess.permanent = True
                app.server.permanent_session_lifetime = _dt.timedelta(hours=SESSION_HOURS)
                return _fredirect(_BASE_PATHNAME)
            error = "Incorrect or expired code. Please try again."
            step  = "code"
        else:
            ok, msg = request_otp(email)
            if ok:
                step = "code"
            else:
                error = msg or "Failed to send code."
    return _render_login(step, email, error)


# ---------------------------------------------------------------------------
app.layout = html.Div(id="app-root", children=[
    # ── Stores ──
    dcc.Store(id="store-embargo-permanent", storage_type="local",   data=False),
    dcc.Store(id="store-tab",           data="hazard"),
    dcc.Store(id="store-hazard-layer",  data=None),
    dcc.Store(id="store-exposure-topic",data=None),
    dcc.Store(id="store-country",       data=None),
    dcc.Store(id="store-ucode",         data=None),
    dcc.Store(id="store-bounds",        data=None),
    dcc.Store(id="store-level",         data=None),
    dcc.Store(id="store-clicked-ucode", data=None),
    dcc.Store(id="store-clicked-name",  data=None),
    dcc.Store(id="store-exposure",      data=None),
    dcc.Store(id="store-last-click",    data=None),
    # Shared custom hazard thresholds {hazard_name: value} (non-default only).
    # Written by the Exposure-tab editor; read by the exposure visualization.
    dcc.Store(id="store-thresholds",    data={}),
    # Active legend rows (list of specs from config.py) for the floating overlay.
    dcc.Store(id="store-legend",        data=None),
    # Layer whose info popover is open (None = closed). Drives the open/close
    # toggle and tells the clientside positioner which button to anchor to.
    dcc.Store(id="store-info-open",     data=None),
    # Last computed analysis result as map layers: {ucode, level, topics, thr}.
    # Survives tab switches so returning to Analysis restores the view for free.
    dcc.Store(id="store-analysis-viz",  data=None),
    # Lazily fetched per-topic clipped tiles {slot_index: url} — populated only
    # when a topic's legend eye is first switched on.
    dcc.Store(id="store-analysis-topic-urls", data={}),
    # Bumped whenever a map-changing request starts; the clientside loading
    # badge watches this alongside the tile layers' n_loads.
    dcc.Store(id="store-map-busy",      data=None),

    # ── Data-use gate ──────────────────────────────────────────────────────
    # PLACEHOLDER COPY — pending comms/legal sign-off before public launch.
    html.Div(id="embargo-gate", children=[
        html.Div(className="embargo-card", children=[
            html.Div("UNICEF — Data Use Notice", className="embargo-tag"),
            html.Div("About this data", className="embargo-title"),
            html.Div(className="embargo-body", children=[
                "Figures shown are ", html.Strong("modelled estimates"),
                ", derived from global hazard datasets combined with WorldPop "
                "gridded population. They indicate relative exposure — not "
                "observed impacts — and carry uncertainty that increases at "
                "finer administrative levels.",
                html.Br(), html.Br(),
                "The designations employed and the presentation of material on "
                "this site do not imply the expression of any opinion on the "
                "part of UNICEF concerning the legal status of any country or "
                "territory, or the delimitation of its frontiers or boundaries.",
            ]),
            dcc.Checklist(
                id="embargo-dismiss-forever",
                options=[{"label": "Don't show this again", "value": "yes"}],
                value=[], className="embargo-check",
            ),
            html.Button("I Understand",
                        id="embargo-btn", className="embargo-btn", n_clicks=0),
        ]),
    ]),

    # ── Add-your-own-data dialog (Infra tab) ───────────────────────────────
    html.Div(id="co-template-gate", style={"display": "none"}, children=[
        html.Div(className="embargo-card co-template-card", children=[
            html.Div("UNICEF — Infrastructure Data", className="embargo-tag"),
            html.Div("Add your own facility data", className="embargo-title"),
            html.Div(className="embargo-body", children=[
                "Country offices can have their own schools, health facilities "
                "or water points added as a layer in this tab. Send us the data "
                "in the format below and we prepare and load it for you.",
                html.Br(), html.Br(),
                html.Strong("1. Download the template"), " for the facility "
                "type you hold:",
                html.Div(className="co-template-links", children=[
                    html.A([html.I(className="bi bi-download"), "Schools"],
                           href="/assets/templates/co_schools_template.csv",
                           download="co_schools_template.csv",
                           className="co-template-link"),
                    html.A([html.I(className="bi bi-download"),
                            "Health facilities"],
                           href="/assets/templates/co_health_template.csv",
                           download="co_health_template.csv",
                           className="co-template-link"),
                    html.A([html.I(className="bi bi-download"), "Water points"],
                           href="/assets/templates/co_water_template.csv",
                           download="co_water_template.csv",
                           className="co-template-link"),
                ]),
                html.Strong("2. Fill it in."), " Coordinates must be decimal "
                "degrees in WGS84 (EPSG:4326) — longitude between −180 and 180, "
                "latitude between −90 and 90, negative south and west. Leave "
                "out any column you do not hold; do not add columns for "
                "country, facility type or source.",
                html.Br(), html.Br(),
                html.Strong("3. Check it here"), " before sending — drop your "
                "filled file below and we will tell you straight away if "
                "anything needs fixing. Nothing is uploaded; the check runs in "
                "your browser.",
                dcc.Upload(
                    id="co-validate-upload",
                    accept=".csv",
                    multiple=False,
                    className="co-validate-drop",
                    children=html.Div([
                        html.I(className="bi bi-file-earmark-check"),
                        html.Span("Drop your CSV here, or click to choose"),
                    ]),
                ),
                # Markdown with raw HTML: the clientside check builds its report
                # as an HTML string, and a plain Div would render the tags as
                # visible text. Content is generated in-browser from the user's
                # own file and escaped before insertion.
                dcc.Markdown(id="co-validate-report", children="",
                             dangerously_allow_html=True,
                             className="co-validate-report"),
                html.Strong("4. Send it to the GCHD team"), " through your usual "
                "channel, telling us the country, the source and date of the "
                "data, and whether it may be shown publicly. We confirm once "
                "the layer is live.",
            ]),
            html.Button("Close", id="co-template-close",
                        className="embargo-btn", n_clicks=0),
        ]),
    ]),

    # ── Hazard info side panel ──
    html.Div(id="hazard-info-panel", className="hazard-info-panel", style={"display": "none"}, children=[
        html.Div(className="hazard-info-panel-header", children=[
            html.Div(id="hazard-info-panel-title", className="hazard-info-panel-title"),
            html.Button("×", id="hazard-info-close", className="hazard-info-close"),
        ]),
        html.Div(id="hazard-info-panel-body", className="hazard-info-panel-body"),
    ]),

    # ── Main app ──
    html.Div(id="main-app", style={
        "display": "grid",
        "gridTemplateColumns": "77px 320px 1fr",
        "gridTemplateRows": "100vh",
        "width": "100vw",
        "height": "100vh",
        "overflow": "hidden",
    }, children=[
        sidebar(),
        html.Div(id="panel", children=[
            html.Div(
                "Global Child Hazard Database",
                style={
                    "padding": "14px 16px",
                    "fontSize": "0.95rem",
                    "fontWeight": "700",
                    "color": "#1CABE2",
                    "borderBottom": "1px solid var(--border)",
                    "letterSpacing": "0.01em",
                }
            ),
            # Order mirrors TABS so the file reads in rail order; panes are
            # display-toggled, so DOM order itself is not visually significant.
            tab_hazard_layers(),
            tab_mh(),
            tab_exposure(),
            tab_analysis(),
            tab_infrastructure(),
            tab_observed(),
            tab_forecast(),
            tab_ai(),
        ]),
        map_component(),
    ]),
])


# ===========================================================================
# Callbacks
# ===========================================================================

# ── Embargo ──────────────────────────────────────────────────────────────────

# Mirrors scripts/validate_co_submission.py. Clientside so a country office
# needs nothing installed and no file ever leaves their machine — the Python
# version stays for the GCHD team's own use on arrival. Keep the two in step:
# the rules are the coordinate ranges and the column aliases, both of which
# change rarely.
app.clientside_callback(
    """
    function(contents, filename) {
        if (!contents) { return ""; }
        var LON = ["longitude","lon","long","x"];
        var LAT = ["latitude","lat","y"];
        var NAME = ["name","facility_name","school_name","water_source"];
        var ID  = ["facility_id","id","code","school_id","facility_code","wpdx_id"];

        function esc(s){ return String(s).replace(/[<>&]/g, ""); }
        function report(errs, warns, nOk, total) {
            var h = "";
            errs.forEach(function(e){
                h += '<div class="co-v-err"><b>Error</b> ' + esc(e) + '</div>'; });
            warns.forEach(function(w){
                h += '<div class="co-v-warn"><b>Check</b> ' + esc(w) + '</div>'; });
            if (errs.length) {
                h += '<div class="co-v-sum co-v-bad">Not ready to send - '
                   + 'fix the errors above.</div>';
            } else if (warns.length) {
                h += '<div class="co-v-sum co-v-ok">' + nOk + ' of ' + total
                   + ' rows usable. Review the points above, then send.</div>';
            } else {
                h += '<div class="co-v-sum co-v-ok">All checks passed - '
                   + nOk + ' rows ready to send.</div>';
            }
            return h;
        }

        try {
            var raw = atob((contents.split(",")[1]) || "");
            // Strip a UTF-8 BOM: Excel writes one and it corrupts the first header.
            if (raw.charCodeAt(0) === 0xEF) { raw = raw.slice(3); }
            var lines = raw.split(/\\r\\n|\\n|\\r/).filter(function(l){
                return l.trim().length; });
            if (lines.length < 2) {
                return report(["The file has no data rows."], [], 0, 0);
            }
            // Split on commas outside quotes, so a quoted name with a comma
            // does not shift every column after it.
            function cells(line) {
                var out = [], cur = "", q = false;
                for (var i = 0; i < line.length; i++) {
                    var c = line[i];
                    if (c === '"') { q = !q; }
                    else if (c === "," && !q) { out.push(cur); cur = ""; }
                    else { cur += c; }
                }
                out.push(cur);
                return out.map(function(s){ return s.trim().replace(/^"|"$/g, ""); });
            }

            var hdr = cells(lines[0]).map(function(h){ return h.toLowerCase(); });
            function find(cands) {
                for (var i = 0; i < cands.length; i++) {
                    var j = hdr.indexOf(cands[i]);
                    if (j >= 0) { return j; }
                }
                return -1;
            }
            var iLon = find(LON), iLat = find(LAT),
                iName = find(NAME), iId = find(ID);

            var errs = [], warns = [];
            if (iLon < 0) { errs.push("No longitude column. Name it 'longitude' "
                                      + "(also accepted: lon, long, x)."); }
            if (iLat < 0) { errs.push("No latitude column. Name it 'latitude' "
                                      + "(also accepted: lat, y)."); }
            if (iName < 0) { warns.push("No name column - facilities will be "
                                        + "unlabelled."); }
            if (iId < 0) { warns.push("No facility_id column - ids will be "
                                      + "generated, so a re-submission cannot "
                                      + "update existing records."); }
            ["iso3","country","source","facility_type"].forEach(function(c){
                if (hdr.indexOf(c) >= 0) {
                    warns.push("Column '" + c + "' is ignored - country, type "
                             + "and source are set when the layer is registered.");
                }
            });
            if (errs.length) { return report(errs, warns, 0, lines.length - 1); }

            var nBad = 0, nLonR = 0, nLatR = 0, nNull = 0, nMulti = 0,
                nMetres = 0, nPos = 0, nNeg = 0, nOk = 0, seen = {}, nDup = 0;

            for (var r = 1; r < lines.length; r++) {
                var row = cells(lines[r]);
                var lo = parseFloat(row[iLon]), la = parseFloat(row[iLat]);
                if (isNaN(lo) || isNaN(la)) { nBad++; continue; }
                nOk++;
                if (Math.abs(lo) > 180) { nLonR++; }
                if (Math.abs(la) > 90)  { nLatR++; }
                if (Math.abs(lo) > 1000 || Math.abs(la) > 1000) { nMetres++; }
                if (lo === 0 && la === 0) { nNull++; }
                if (la > 0) { nPos++; } else if (la < 0) { nNeg++; }
                var key = lo + "," + la;
                if (seen[key]) { nDup++; } else { seen[key] = 1; }
                if (iName >= 0 && /[\\r\\n]/.test(row[iName] || "")) { nMulti++; }
            }

            var total = lines.length - 1;
            if (nBad) { warns.push(nBad + " of " + total + " rows have a missing "
                      + "or non-numeric coordinate and will be dropped."); }
            if (!nOk) { return report(["No row has a usable coordinate pair."],
                                      warns, 0, total); }
            if (nLonR) { errs.push(nLonR + " rows have longitude outside "
                       + "-180..180."); }
            if (nLatR) { errs.push(nLatR + " rows have latitude outside -90..90. "
                       + "If these look like longitudes, the two columns are "
                       + "swapped."); }
            if (nMetres) { errs.push("Coordinates look like metres, not degrees "
                         + "- reproject to WGS84 (EPSG:4326) before exporting."); }
            if (nNull) { warns.push(nNull + " rows sit at exactly 0,0 - usually "
                       + "a blank coordinate rather than a real location."); }
            if (nPos && nNeg) { warns.push("Latitudes span both hemispheres - "
                              + "check for a missing minus sign if the country "
                              + "is entirely north or south of the equator."); }
            if (nDup) { warns.push(nDup + " rows share a coordinate with another "
                      + "row - possible duplicates."); }
            if (nMulti) { warns.push(nMulti + " names contain a line break. "
                        + "These are collapsed automatically, but a line break "
                        + "can shift columns in some exports."); }

            return report(errs, warns, nOk, total);
        } catch (e) {
            return report(["Could not read that file as CSV: " + e.message],
                          [], 0, 0);
        }
    }
    """,
    Output("co-validate-report", "children"),
    Input("co-validate-upload",  "contents"),
    State("co-validate-upload",  "filename"),
    prevent_initial_call=True,
)


@app.callback(
    Output("co-template-gate",   "style"),
    Input("infra-add-layer-btn", "n_clicks"),
    Input("co-template-close",   "n_clicks"),
    prevent_initial_call=True,
)
def toggle_co_template_dialog(_open, _close):
    if ctx.triggered_id == "infra-add-layer-btn":
        return {"display": "flex"}
    return {"display": "none"}


@app.callback(
    Output("store-embargo-permanent", "data"),
    Output("embargo-gate",            "style"),
    Input("embargo-btn",              "n_clicks"),
    State("embargo-dismiss-forever",  "value"),
    prevent_initial_call=True,
)
def accept_embargo(n, dismiss_forever):
    """Dismiss the notice, persisting only if the box was ticked."""
    if not n:
        return no_update, no_update
    return bool(dismiss_forever), {"display": "none"}


@app.callback(
    Output("embargo-gate",           "style", allow_duplicate=True),
    Input("store-embargo-permanent", "data"),
    prevent_initial_call=True,
)
def restore_embargo_state(accepted_forever):
    """Hide on load only for users who ticked 'don't show this again'."""
    return {"display": "none"} if accepted_forever else no_update


# ── Tab switching ─────────────────────────────────────────────────────────────

@app.callback(
    Output("store-tab", "data"),
    *[Output(t["btn"],  "className") for t in TABS],
    *[Output(t["pane"], "style")     for t in TABS],
    Output("hazard-info-panel", "style", allow_duplicate=True),
    *[Input(t["btn"], "n_clicks") for t in TABS],
    State("store-tab", "data"),
    prevent_initial_call=True,
)
def switch_tab(*args):
    """Show one pane and highlight its rail button.

    Outputs are generated from TABS, so the arity can never drift out of sync
    with the rail the way a hand-maintained Output list could — a mismatch
    there used to fail silently at click time rather than at import.
    """
    current = args[-1]
    btn_to_key = {t["btn"]: t["key"] for t in TABS}
    tab = btn_to_key.get(ctx.triggered_id, current)
    cls = lambda k: "nav-btn active" if tab == k else "nav-btn"
    vis = lambda k: {"display": "block"} if tab == k else {"display": "none"}
    # The popover serves the Layers list and the Forecast dataset info
    # button, so it must survive a switch to either.
    info_panel = (no_update if tab in ("hazard", *FORECAST_TABS)
                  else {"display": "none"})
    return (
        tab,
        *[cls(t["key"]) for t in TABS],
        *[vis(t["key"]) for t in TABS],
        info_panel,
    )


# ── Hazard info popup ────────────────────────────────────────────────────────

@app.callback(
    Output("hazard-info-panel",       "style"),
    Output("hazard-info-panel-title", "children"),
    Output("hazard-info-panel-body",  "children"),
    Output("store-info-open",         "data"),
    Input({"type": "hazard-info-btn", "index": ALL}, "n_clicks"),
    Input({"type": "prov-info-btn",   "index": ALL}, "n_clicks"),
    Input("hazard-info-close",       "n_clicks"),
    Input("store-hazard-layer",      "data"),
    Input("forecast-dataset-select", "value"),
    State("store-info-open",         "data"),
    prevent_initial_call=True,
)
def toggle_hazard_info(info_clicks, _prov_clicks, _close, layer_name,
                       fc_dataset, open_for):
    """Open the info popover for a layer. It closes on: a second click of the
    same button, the × button, selecting any layer on the map, or changing the
    forecast dataset.

    `store-info-open` holds the layer the popover is showing (None = closed);
    deriving this from the panel's own style is what made re-clicking a no-op.

    Serves two families keyed into the same HAZARD_INFO dict: hazard and
    population layers by layer name, and forecast datasets by
    FC_INFO_PREFIX + name. Each forecast row carries its own prefixed index,
    so both families flow through one branch.
    """
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update, no_update, no_update
    if triggered == "hazard-info-close":
        return {"display": "none"}, no_update, no_update, None
    if triggered in ("store-hazard-layer", "forecast-dataset-select"):
        # Selecting a layer dismisses an open popover rather than retargeting
        # it: the popover answers "what is this hazard?", so once the user
        # moves on to loading a layer it has served its purpose and would
        # otherwise sit over the map describing something they didn't ask about.
        if not open_for:
            return no_update, no_update, no_update, no_update
        return {"display": "none"}, no_update, no_update, None
    if (isinstance(triggered, dict)
            and triggered.get("type") in ("hazard-info-btn", "prov-info-btn")):
        # The results-panel ⓘ buttons are created when a result renders, and
        # Dash re-fires an ALL pattern input when new matching components enter
        # the layout — with triggered_id set to one of them. prevent_initial_call
        # does not cover that, so without this guard the popover opens by itself
        # as soon as results appear. A real click always leaves a non-zero
        # n_clicks somewhere in the group.
        if not any(c for c in ((info_clicks or []) + (_prov_clicks or [])) if c):
            return no_update, no_update, no_update, no_update
        name = triggered["index"]
        # Results-panel icons carry a "<scope>:" prefix to keep their ids unique
        # across the Analysis and Infrastructure panels; the popover is keyed on
        # the hazard itself.
        if triggered["type"] == "prov-info-btn":
            name = name.split(":", 1)[1]
        if open_for == name:                      # same button → toggle closed
            return {"display": "none"}, no_update, no_update, None
        if name.startswith(FC_INFO_PREFIX):
            fc_name = name[len(FC_INFO_PREFIX):]
            title   = FORECAST_MAP.get(fc_name, {}).get("label", fc_name)
        else:
            title = _layer_label(name)
        return ({"display": "flex"}, title,
                _hazard_info_body(HAZARD_INFO.get(name, name)), name)
    return no_update, no_update, no_update, no_update


# Anchor the popover to the info button that opened it, and mark that button
# active. The layer list scrolls inside #panel, so the button's position is only
# knowable in the browser — hence clientside. Runs after the server callback
# has set the panel's display, and vertically clamps to stay on screen.
app.clientside_callback(
    """
    function(openFor, ids) {
        var OFF = {display: "none"};
        var classes = (ids || []).map(function(id){
            return (openFor && id.index === openFor)
                ? "hazard-info-btn active" : "hazard-info-btn";
        });
        if (!openFor) { return [OFF, classes]; }

        // Dash serialises pattern-matching ids as JSON with sorted keys, so
        // build the exact id rather than substring-matching (layer names would
        // otherwise collide, e.g. "fire_FRP..." inside another id).
        var exact = JSON.stringify({index: openFor, type: "hazard-info-btn"});
        var btn = document.getElementById(exact);
        // Results-panel icons prefix their index with a scope ("an:", "infra:")
        // to stay unique across panels, so match on the part after the colon.
        if (!btn) {
            btn = (Array.prototype.find.call(
                document.querySelectorAll(".hazard-info-btn, .info-icon-btn"),
                function(el){
                    try {
                        var idx = JSON.parse(el.id).index;
                        if (idx === openFor) { return true; }
                        var c = idx.indexOf(":");
                        return c >= 0 && idx.slice(c + 1) === openFor;
                    } catch (e) { return false; }
                }) || null);
        }
        if (!btn) { return [window.dash_clientside.no_update, classes]; }

        // The panel div persists across opens — only its children are swapped —
        // so the browser keeps the previous scrollTop. Without this, opening a
        // short description after a long one starts it scrolled past the text.
        // rAF: run after Dash has painted the new children, or the reset lands
        // on the old content and is undone by the re-render.
        window.requestAnimationFrame(function() {
            var body = document.getElementById("hazard-info-panel-body");
            if (body) { body.scrollTop = 0; }
        });

        var r = btn.getBoundingClientRect();
        var H = 260, W = 260, M = 8;             // popover height/width, margin
        // Centre on the button, then clamp inside the viewport.
        var top = r.top + r.height / 2 - H / 2;
        top = Math.max(M, Math.min(top, window.innerHeight - H - M));
        return [{
            display: "flex", top: top + "px", left: (r.right + 10) + "px",
            height: H + "px", width: W + "px", bottom: "auto"
        }, classes];
    }
    """,
    Output("hazard-info-panel", "style", allow_duplicate=True),
    Output({"type": "hazard-info-btn", "index": ALL}, "className"),
    Input("store-info-open", "data"),
    State({"type": "hazard-info-btn", "index": ALL}, "id"),
    prevent_initial_call=True,
)


# ── Hazard layer selection ────────────────────────────────────────────────────

@app.callback(
    Output({"type": "layer-item", "index": ALL}, "className"),
    Output("store-hazard-layer", "data"),
    Input({"type": "layer-item", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_hazard_layer(all_clicks):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update
    selected = triggered["index"]
    classes = [
        "layer-item active" if inp["id"]["index"] == selected else "layer-item"
        for inp in ctx.inputs_list[0]
    ]
    return classes, selected


# ── Exposure topic selection ──────────────────────────────────────────────────

@app.callback(
    Output("store-exposure-topic", "data"),
    Input({"type": "topic-item", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_topic(all_clicks):
    # Row click → set the selected topic. render_exposure_topics owns the row
    # styling/radio state and the inline editor placement.
    triggered = ctx.triggered
    if not triggered:
        return no_update
    # Ignore spurious fires from re-rendered rows (n_clicks resets to 0/None on
    # re-render). Only act on a real click, where the triggered value is truthy.
    trig = triggered[0]
    if not trig.get("value"):
        return no_update
    return ctx.triggered_id["index"]


@app.callback(
    Output("exposure-topic-list", "children"),
    Input("store-exposure-topic", "data"),
)
def render_exposure_topics(topic):
    """Rebuild the topic list with the radio state, and inject the inline
    threshold editor directly beneath the selected topic row."""
    rows = []
    for t in TOPIC_LIST:
        rows.append(_exposure_topic_row(t, t == topic))
        if t == topic:
            rows.append(_exposure_inline_editor(topic))
    return rows


# Clientside toggle — reliable for the dynamically-rendered inline editor
# (server callbacks with prevent_initial_call can miss the first click on
# freshly-injected components).
app.clientside_callback(
    """
    function(n) {
        var open = !!n && (n % 2 === 1);
        return [
            {"display": open ? "block" : "none"},
            open ? "bi bi-chevron-down" : "bi bi-chevron-right"
        ];
    }
    """,
    Output("exposure-thr-body",    "style"),
    Output("exposure-thr-chevron", "className"),
    Input("exposure-thr-toggle",   "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("store-thresholds",   "data", allow_duplicate=True),
    Output("exposure-thr-badge", "children"),
    Input({"type": "exposure-threshold", "index": ALL}, "value"),
    State({"type": "exposure-threshold", "index": ALL}, "id"),
    State("store-exposure-topic", "data"),
    prevent_initial_call=True,
)
def sync_exposure_thresholds(values, ids, topic):
    """Publish the exposure editor's thresholds to the shared store (drives the
    exposure map) and update the 'N changed' badge."""
    overrides, _ = _threshold_overrides([topic] if topic else [], ids, values)
    badge = f"{len(overrides)} changed" if overrides else ""
    return overrides, badge


# ── Infrastructure threshold editor ───────────────────────────────────────────

def _infra_editor_topics(topics):
    """Infra topic-select is empty for 'all hazards' — expand to all infra topics."""
    if topics:
        return topics
    return [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]


@app.callback(
    Output("infra-threshold-block", "children"),
    Input("infra-topic-select",     "value"),
)
def build_infra_threshold_editor(topics):
    return build_threshold_editor_for("infra", _infra_editor_topics(topics))


@app.callback(
    Output("infra-thr-body",    "style"),
    Output("infra-thr-chevron", "className"),
    Input("infra-thr-toggle",   "n_clicks"),
    prevent_initial_call=True,
)
def toggle_infra_thr(n):
    open_ = bool(n) and (n % 2 == 1)
    body  = {"display": "block"} if open_ else {"display": "none"}
    chev  = "bi bi-chevron-down" if open_ else "bi bi-chevron-right"
    return body, chev


@app.callback(
    Output("infra-thr-badge", "children"),
    Input({"type": "infra-threshold", "index": ALL}, "value"),
    State({"type": "infra-threshold", "index": ALL}, "id"),
    State("infra-topic-select", "value"),
)
def infra_thr_badge(values, ids, topics):
    overrides, _ = _threshold_overrides(_infra_editor_topics(topics), ids, values)
    return f"{len(overrides)} changed" if overrides else ""


# ── Topic group fold/unfold ───────────────────────────────────────────────────

@app.callback(
    Output({"type": "topic-group-body",    "index": ALL}, "style"),
    Output({"type": "topic-chevron",       "index": ALL}, "style"),
    Input({"type":  "topic-group-header",  "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def toggle_topic_group(all_clicks):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update
    toggled = triggered["index"]
    body_styles    = []
    chevron_styles = []
    for inp in ctx.inputs_list[0]:
        topic   = inp["id"]["index"]
        clicks  = inp.get("value") or 0
        is_toggled = topic == toggled
        # odd clicks = collapsed, even = expanded (starts expanded)
        collapsed = (clicks % 2 == 1)
        body_styles.append({"display": "none"} if collapsed else {})
        rot = "rotate(180deg)" if collapsed else "rotate(0deg)"
        chevron_styles.append({
            "fontSize": "0.65rem", "color": "var(--lo)",
            "transition": "transform 0.2s", "transform": rot,
        })
    return body_styles, chevron_styles


# ── Legend specs per layer ────────────────────────────────────────────────────
# Palettes are read back from the vis dict GEE actually rendered with, so the
# legend stops can never drift from the tiles on screen.

MHC_PALETTE = ["#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404"]
MHI_PALETTE = ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d"]


def _layer_legend_specs(sel):
    """Legend specs for a Layers-tab selection (hazard, multi-hazard or
    population). Returns [] when nothing meaningful is selected."""
    if not sel:
        return []

    if is_pop_layer(sel):
        meta = POP_LAYER_MAP.get(pop_class_of(sel), {})
        return [vis_gradient_spec(f"{meta.get('label', 'Population')} (per 100 m)",
                                  POP_VIS, unit="+")]

    if sel == "Multi Hazard Count":
        n = len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)
        return [gradient_spec("Hazard topics (count)", MHC_PALETTE, 1, n)]

    if sel == "Multi Hazard Intensity":
        return [gradient_spec("Hazard score", MHI_PALETTE, 0, 10)]

    if sel not in HAZARD_MAP:
        return []
    pal   = HAZARD_VIS_PALETTES.get(
        sel, ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"])
    units = (HAZARD_INFO.get(sel) or {}).get("units", "")
    return [gradient_spec(_layer_label(sel), pal, "Low", "High", unit=units)]


def _infra_legend_specs(viz):
    """Legend for the Infrastructure tab (population raster, facility points,
    Voronoi catchments, selected subregion) — all toggleable."""
    if not viz:
        return []
    pt_color = "#" + (viz.get("color") or "e67e22")
    return [
        vis_gradient_spec("Children (per 100 m)", INFRA_POP_VIS,
                          layer_id="infra-pop", unit="+", toggleable=True),
        swatch_spec("Facilities", pt_color,
                    layer_id="infra-points", toggleable=True),
        swatch_spec("Facility catchments", "#1CABE2", shape="line",
                    layer_id="infra-catchments", toggleable=True),
        swatch_spec("Selected subregion", "#f1c40f", shape="line",
                    layer_id="infra-selection", toggleable=True),
    ]


def _analysis_legend_specs(viz):
    """Legend for a computed Analysis result: the exposed-population raster is
    shown by default; each selected hazard topic gets a row that starts hidden
    so the map stays readable (its tile is fetched only on first reveal)."""
    if not viz:
        return []
    if viz.get("no_data"):
        return [swatch_spec("Selected region", "#FFD700", shape="line",
                            layer_id="analysis-selection", toggleable=True)]
    specs = [
        vis_gradient_spec("Children exposed (per 100 m)", POP_VIS,
                          layer_id="analysis-exposed", unit="+",
                          toggleable=True),
    ]
    for i, topic in enumerate(viz.get("topics") or []):
        if i >= ANALYSIS_TOPIC_SLOTS:
            break
        specs.append(swatch_spec(
            f"{topic} — hazard area", TOPIC_COLORS.get(topic, "#888"),
            layer_id=f"analysis-topic-{i}", toggleable=True, visible=False))
    specs.append(swatch_spec("Selected region", "#FFD700", shape="line",
                             layer_id="analysis-selection", toggleable=True))
    return specs


# ── Map data layers ───────────────────────────────────────────────────────────

@app.callback(
    Output("data-layers", "children"),
    Output("store-legend", "data"),
    Input("store-hazard-layer",   "data"),
    Input("store-exposure-topic", "data"),
    Input("mhc-select",           "value"),
    Input("mhi-select",           "value"),
    Input("store-tab",            "data"),
    Input("store-thresholds",     "data"),
    Input("store-infra-viz",      "data"),
    Input("store-analysis-viz",   "data"),
    Input("store-forecast-viz",   "data"),
    Input("store-forecast-preview", "data"),
)
def update_data_layers(sel_layer, exp_topic, mhc, mhi, tab, thresholds,
                       infra_viz, analysis_viz, forecast_viz, forecast_preview):
    """Build this tab's map tiles and the legend specs that describe them.

    Single source of truth for the legend: whichever tab is active writes its
    specs here, so the floating overlay always matches what's on the map.
    """
    layers, specs = [], []

    # Infrastructure layers live in dedicated top-level components
    # (infra-pop-tile / infra-points-tile / etc.), not in this group — but
    # their legend is still rendered by the shared overlay.
    if tab == "infrastructure":
        return layers, _infra_legend_specs(infra_viz)

    if tab == "analysis":
        return layers, _analysis_legend_specs(analysis_viz)

    # Dedicated top-level components (forecast-*-tile), shared by both tabs.
    if tab in FORECAST_TABS:
        return layers, _forecast_legend_specs(forecast_viz, forecast_preview)

    if tab == "hazard" and sel_layer:
        if sel_layer == "Multi Hazard Count":
            url, _ = get_topic_count_tile_url()
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        elif sel_layer == "Multi Hazard Intensity":
            url, _ = get_pixel_score_tile_url()
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        elif is_pop_layer(sel_layer):
            url, _ = get_population_tile_url(pop_class_of(sel_layer))
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.85))
        else:
            url, _ = get_hazard_tile_url(sel_layer)
            if url:
                layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        specs = _layer_legend_specs(sel_layer)

    elif tab == "exposure" and exp_topic:
        color = TOPIC_COLORS.get(exp_topic, "#ff0000")
        # Honor the exposure editor's custom thresholds (falls back to the cached
        # default tile when this topic has no override).
        url, _ = get_topic_tile_url_thr(exp_topic, color, thresholds or None)
        layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        specs = [swatch_spec(f"{exp_topic} — children exposed", color)]

    elif tab == "mh":
        n = len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)
        if mhc:
            url, _ = get_topic_count_tile_url(int(mhc))
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
            specs.append(gradient_spec(f"Hazard count (≥ {mhc})",
                                       MHC_PALETTE, int(mhc), n))
        if mhi:
            url, _ = get_pixel_score_percentile_tile_url(mhi)
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.65))
            specs.append(gradient_spec(f"Hazard score (> P{mhi})",
                                       MHI_PALETTE, 0, 10))

    return layers, specs


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


# ── Hazard-threshold editor ───────────────────────────────────────────────────

def _fmt_mark(v):
    """Compact number for slider marks (13994 -> '14k', 0.01 -> '0.01')."""
    av = abs(v)
    if av >= 1000:
        return f"{v/1000:g}k"
    if av == 0 or av >= 1:
        return f"{v:g}"
    return f"{v:g}"


def _thr_step_marks(lo, hi):
    """Adaptive slider step + sparse min/mid/max marks for a hazard range."""
    lo = 0 if lo is None else lo
    hi = 100 if hi is None else hi
    span = (hi - lo) or 1
    step = 0.001 if span <= 2 else (0.01 if span <= 50 else 1)
    mid  = lo + span / 2
    marks = {
        lo:  {"label": _fmt_mark(lo),  "style": {"fontSize": "0.55rem"}},
        mid: {"label": _fmt_mark(mid), "style": {"fontSize": "0.55rem"}},
        hi:  {"label": _fmt_mark(hi),  "style": {"fontSize": "0.55rem"}},
    }
    return step, marks


def _threshold_row(h_name, prefix="analysis"):
    """One editable-threshold row: slider (bounds) + linked number input (precision).
    `prefix` scopes the pattern-matching ids so the same editor can live in
    multiple tabs (analysis | exposure | infra). Binary hazards render disabled."""
    hz      = HAZARD_MAP.get(h_name, {})
    units   = (HAZARD_INFO.get(h_name) or {}).get("units", "")
    default = hz.get("threshold", 0)
    lo, hi  = hz.get("min", 0), hz.get("max", 100)
    step, marks = _thr_step_marks(lo, hi)
    binary  = is_binary_hazard(h_name)
    return html.Div(className="analysis-threshold-row", children=[
        html.Div(className="athr-row-head", children=[
            html.Span(_hazard_label(h_name), className="athr-label"),
            dcc.Input(
                id={"type": f"{prefix}-threshold", "index": h_name},
                type="number", value=default, min=lo, max=hi, step=step,
                debounce=True, className="athr-input", disabled=binary,
            ),
            html.Span("presence/absence" if binary else units,
                      className="athr-units", title=f"range {lo}–{hi}"),
        ]),
        dcc.Slider(
            id={"type": f"{prefix}-threshold-slider", "index": h_name},
            min=lo, max=hi, value=default, step=step, marks=marks,
            updatemode="mouseup",
            className="athr-slider" + (" athr-slider-disabled" if binary else ""),
            disabled=binary,
            tooltip={"placement": "bottom", "always_visible": False},
        ),
    ])


def build_threshold_editor_for(prefix, topics):
    """Reusable threshold-editor body for any tab. `prefix` scopes the row ids
    (analysis | exposure | infra); `topics` is the selected topic list."""
    if not topics:
        return html.Div("Select at least one hazard topic to adjust thresholds.",
                        className="ps-caption", style={"padding": "4px 0"})
    blocks = []
    for topic in HAZARD_TOPICS:            # stable, config order
        if topic not in topics:
            continue
        rows = [_threshold_row(h, prefix) for h in HAZARD_TOPICS[topic]]
        blocks.append(html.Div(className="athr-topic", children=[
            html.Div([
                html.Span(className="topic-swatch",
                          style={"background": TOPIC_COLORS.get(topic, "#888")}),
                html.Span(topic, className="athr-topic-name"),
            ], className="athr-topic-head"),
            *rows,
        ]))
    # Reset-to-defaults control (restores every visible hazard to its config
    # threshold). id is prefix-scoped so each tab's editor resets independently.
    blocks.append(html.Button(
        [html.I(className="bi bi-arrow-counterclockwise",
                style={"marginRight": "5px"}), "Reset to defaults"],
        id=f"{prefix}-thr-reset", n_clicks=0, className="athr-reset-btn",
    ))
    return blocks


@app.callback(
    Output("analysis-threshold-block", "children"),
    Input("analysis-topic-select",     "value"),
)
def build_threshold_editor(topics):
    return build_threshold_editor_for("analysis", topics)


# Clientside slider <-> number-input sync (keeps drags off the server).
# Registered once per editor prefix (analysis | exposure | infra).
def _register_threshold_sync(prefix):
    app.clientside_callback(
        """
        function(sliderVal, inputVal) {
            var ctx = dash_clientside.callback_context;
            if (!ctx || !ctx.triggered || ctx.triggered.length === 0) {
                return [dash_clientside.no_update, dash_clientside.no_update];
            }
            var prop = ctx.triggered[0].prop_id;
            // Slider moved -> update the number input; input changed -> move slider.
            if (prop.indexOf("-threshold-slider") !== -1) {
                if (sliderVal === null || sliderVal === undefined) {
                    return [dash_clientside.no_update, dash_clientside.no_update];
                }
                return [sliderVal, dash_clientside.no_update];
            } else {
                if (inputVal === null || inputVal === undefined || inputVal === "") {
                    return [dash_clientside.no_update, dash_clientside.no_update];
                }
                return [dash_clientside.no_update, inputVal];
            }
        }
        """,
        Output({"type": f"{prefix}-threshold",        "index": MATCH}, "value", allow_duplicate=True),
        Output({"type": f"{prefix}-threshold-slider", "index": MATCH}, "value"),
        Input({"type": f"{prefix}-threshold-slider",  "index": MATCH}, "value"),
        Input({"type": f"{prefix}-threshold",         "index": MATCH}, "value"),
        prevent_initial_call=True,
    )


def _register_threshold_reset(prefix):
    """Reset every hazard input + slider in this editor to its config default."""
    @app.callback(
        Output({"type": f"{prefix}-threshold",        "index": ALL}, "value",
               allow_duplicate=True),
        Output({"type": f"{prefix}-threshold-slider", "index": ALL}, "value",
               allow_duplicate=True),
        Input(f"{prefix}-thr-reset", "n_clicks"),
        State({"type": f"{prefix}-threshold", "index": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _reset(_n, ids):
        defaults = [HAZARD_MAP.get(i["index"], {}).get("threshold", 0) for i in ids]
        return defaults, defaults


for _prefix in ("analysis", "exposure", "infra"):
    _register_threshold_sync(_prefix)
    _register_threshold_reset(_prefix)


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


# ── Map cursor ───────────────────────────────────────────────────────────────

@app.callback(
    Output("map-container", "className"),
    Input("store-level",    "data"),
)
def update_map_cursor(level):
    return "map-clickable" if level and level != "adm0 (Country)" else ""


# ── Map loading indicator ────────────────────────────────────────────────────
# Two-phase, because "the callback returned" and "the map looks right" are
# different moments: building a GEE tile URL is a server round-trip (getMapId,
# sometimes a reduceRegion), and only after that does Leaflet start fetching and
# painting the actual tiles.
#
# Phase 1 (server): a no-op callback mirrors the inputs of every tile-producing
# callback, so `store-map-busy` flips as soon as the user does something that
# will change the map.
# Phase 2 (clientside): the badge hides only once the tile layers report
# n_loads, i.e. the imagery is actually on screen.

@app.callback(
    Output("store-map-busy",       "data"),
    # Layers / Exposure / MH tabs
    Input("store-hazard-layer",    "data"),
    Input("store-exposure-topic",  "data"),
    Input("mhc-select",            "value"),
    Input("mhi-select",            "value"),
    Input("store-thresholds",      "data"),
    # Region changes redraw boundary + selection tiles
    Input("store-ucode",           "data"),
    Input("store-clicked-ucode",   "data"),
    Input("store-level",           "data"),
    # Analysis / Infra / Forecast result layers
    Input("store-analysis-viz",    "data"),
    Input("store-infra-viz",       "data"),
    Input("store-forecast-viz",    "data"),
    # Forecast preview: the slowest common path, and the one with no other
    # visible progress cue.
    Input("forecast-dataset-select", "value"),
    Input("forecast-dates",          "start_date"),
    Input("forecast-dates",          "end_date"),
    Input("forecast-reducer",        "value"),
    prevent_initial_call=True,
)
def track_map_loading(*_):
    """Bump a counter whenever something that changes the map is requested.
    Does no work — the value only needs to differ from the last one."""
    return {"t": time.time()}


# Show while a request is outstanding OR a tile URL has changed but its layer
# has not finished loading; hide once every watched layer reports n_loads.
# A hard 15 s ceiling guarantees the badge can never stick if a layer errors or
# a URL resolves to empty (in which case Leaflet may never fire 'load').
app.clientside_callback(
    """
    function(busy, uPrev, uInt, uExp, uSel, uData, nPrev, nInt, nExp, nSel) {
        var st = (window._gchdMapLoad = window._gchdMapLoad || {
            urls: {}, loads: {}, since: 0, shown: false, primed: false
        });
        var HIDE = {display: "none"}, SHOW = {display: "flex"};

        var urls  = {prev: uPrev, inten: uInt, exp: uExp, sel: uSel,
                     data: JSON.stringify(uData || [])};
        var loads = {prev: nPrev, inten: nInt, exp: nExp, sel: nSel};

        // First run only records the starting values. The default hazard layer
        // populates data-layers during page load, which would otherwise read as
        // "new imagery pending" and show the badge before the user has asked
        // for anything — and a LayerGroup has no n_loads to clear it, so it
        // would hang until the 15 s timeout.
        if (!st.primed) {
            st.primed = true;
            for (var k0 in urls)  { st.urls[k0]  = urls[k0]; }
            for (var j0 in loads) { st.loads[j0] = loads[j0]; }
            return HIDE;
        }

        // A URL that changed to a non-empty value means new imagery is coming.
        var pending = false;
        for (var k in urls) {
            if (urls[k] !== st.urls[k]) {
                st.urls[k] = urls[k];
                if (urls[k] && urls[k] !== "[]" && urls[k] !== "") pending = true;
            }
        }
        // A layer reporting a new n_loads has finished painting.
        var finished = false;
        for (var j in loads) {
            if (loads[j] !== st.loads[j]) { st.loads[j] = loads[j]; finished = true; }
        }

        var ctx = dash_clientside.callback_context;
        var trg = (ctx && ctx.triggered && ctx.triggered.length)
                  ? ctx.triggered[0].prop_id : "";
        if (trg.indexOf("store-map-busy") !== -1 || pending) {
            st.since = Date.now();
            st.shown = true;
            return SHOW;
        }
        if (finished && st.shown) { st.shown = false; return HIDE; }
        // Safety valve: never let the badge outlive the work it describes.
        if (st.shown && st.since && (Date.now() - st.since) > 15000) {
            st.shown = false;
            return HIDE;
        }
        return st.shown ? SHOW : HIDE;
    }
    """,
    Output("map-loading", "style"),
    Input("store-map-busy",           "data"),
    Input("forecast-intensity-tile",  "url"),
    Input("analysis-exposed-tile",    "url"),
    Input("forecast-exposed-tile",    "url"),
    Input("infra-pop-tile",           "url"),
    Input("data-layers",              "children"),
    Input("forecast-intensity-tile",  "n_loads"),
    Input("analysis-exposed-tile",    "n_loads"),
    Input("forecast-exposed-tile",    "n_loads"),
    Input("infra-pop-tile",           "n_loads"),
    prevent_initial_call=True,
)


# ── Map viewport & boundary layers ───────────────────────────────────────────

@app.callback(
    Output("main-map",        "viewport"),
    Output("boundary-layers", "children"),
    Input("store-bounds",     "data"),
    Input("store-ucode",      "data"),
    Input("store-level",      "data"),
)
def update_map_view(bounds, ucode, level):
    boundary_layers = []
    if ucode and level:
        try:
            url = get_admin_boundary_tile_url(level, ucode)
            boundary_layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=1.0))
        except Exception:
            pass
    viewport = {"bounds": bounds, "transition": "fitBounds"} if bounds else no_update
    return viewport, boundary_layers


# ── Selection highlight ───────────────────────────────────────────────────────

@app.callback(
    Output("selection-layer",     "children"),
    Input("store-clicked-ucode",  "data"),
    Input("store-tab",            "data"),
    State("store-level",          "data"),
)
def update_selection_layer(ucode, tab, level):
    # On the infra, analysis and forecast tabs the yellow highlight is drawn by
    # their dedicated, toggleable *-selection-tile components instead of this
    # group (a LayerGroup's children can't be flipped clientside). Drawing it
    # here too would double the highlight and leave a copy that the legend's
    # eye cannot switch off.
    if tab in ("infrastructure", "analysis") or tab in FORECAST_TABS:
        return []
    if not ucode or not level:
        return []
    try:
        url = get_selected_feature_tile_url(level, ucode)
        return [dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75)]
    except Exception:
        return []


# Infra tab: draw the yellow district highlight immediately on click (before
# Compute) into the dedicated, toggleable infra-selection-tile.
@app.callback(
    Output("infra-selection-tile", "url", allow_duplicate=True),
    Input("store-clicked-ucode",   "data"),
    State("store-level",           "data"),
    State("store-tab",             "data"),
    prevent_initial_call=True,
)
def infra_highlight_selection(ucode, level, tab):
    if tab != "infrastructure" or not ucode or not level \
            or level == "adm0 (Country)":
        return ""
    try:
        return get_selected_feature_tile_url(level, ucode)
    except Exception:
        return ""


# ── Map click → feature lookup ────────────────────────────────────────────────

@app.callback(
    Output("store-clicked-ucode", "data", allow_duplicate=True),
    Output("store-clicked-name",  "data", allow_duplicate=True),
    Output("store-exposure",      "data", allow_duplicate=True),
    Output("store-last-click",    "data"),
    Input("main-map",             "clickData"),
    State("store-level",          "data"),
    State("store-ucode",          "data"),
    State("store-last-click",     "data"),
    State("store-tab",            "data"),
    prevent_initial_call=True,
)
def on_map_click(click_data, level, country_ucode, last_click, tab):
    if not click_data or not level or not country_ucode:
        return no_update, no_update, no_update, no_update
    if level == "adm0 (Country)" or tab not in ("analysis", "infrastructure",
                                                *FORECAST_TABS):
        return no_update, no_update, no_update, no_update
    latlng = click_data.get("latlng")
    if not latlng:
        return no_update, no_update, no_update, no_update
    if isinstance(latlng, dict):
        lat, lng = latlng.get("lat") or latlng.get("0"), latlng.get("lng") or latlng.get("1")
    else:
        lat, lng = latlng[0], latlng[1]
    if lat is None or lng is None:
        return no_update, no_update, no_update, no_update
    click_key = f"{round(lat,5)},{round(lng,5)}"
    if click_key == last_click:
        return no_update, no_update, no_update, no_update
    ucode, fname = get_feature_at_point(lng, lat, level, country_ucode)
    if not ucode:
        return no_update, no_update, no_update, click_key
    return ucode, fname, None, click_key


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


# ── Exposure computation ──────────────────────────────────────────────────────

def _threshold_overrides(topic_values, threshold_ids, threshold_values):
    """Map pattern-matching threshold inputs to {hazard_name: value}, keeping
    only hazards whose value differs from the config default and whose topic is
    selected. Returns (overrides_dict, sig_string)."""
    overrides = {}
    for id_obj, val in zip(threshold_ids or [], threshold_values or []):
        h_name = id_obj.get("index")
        if h_name not in HAZARD_MAP or val is None or val == "":
            continue
        default = HAZARD_MAP[h_name].get("threshold")
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if default is None or abs(v - float(default)) > 1e-12:
            overrides[h_name] = v
    sig = "|".join(sorted(topic_values or [])) + "#" + \
          ",".join(f"{k}={overrides[k]}" for k in sorted(overrides))
    return overrides, sig


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


def _hazard_label(name):
    words = [w.capitalize() for w in name.replace("-", " ").split("_")]
    return " ".join(words[:2])


POP_BASIS = "WorldPop under-18 gridded population, 2025, 100 m"


_ESTIMATE_CAVEAT = (
    "Figures are the modelled child population in areas where the hazard "
    "exceeds the stated threshold — indicative of relative exposure, not "
    "observed impacts."
)


def _info_icon(hazard_name, scope="an"):
    """Inline ⓘ opening the shared layer-info popover.

    A distinct `type` from the Layers tab's buttons, and a `scope`-prefixed
    index: that tab renders one button per hazard already, and the Analysis and
    Infrastructure panels both persist in the layout at once, so an unprefixed
    id would put duplicate component ids on the page for the same hazard.
    """
    return html.Button(
        html.I(className="bi bi-info-circle"),
        id={"type": "prov-info-btn", "index": f"{scope}:{hazard_name}"},
        className="info-icon-btn", n_clicks=0,
        title=_layer_label(hazard_name),
    )


def _provenance_block(extra_lines=None):
    """Methodology section for a results panel.

    Numbers get copied into slides and emails, so the basis travels with them.
    Shared by Analysis and Infrastructure so the two panels can never state it
    differently.
    """
    lines = [f"Population: {POP_BASIS}", *(extra_lines or [])]
    return html.Div(className="ps", children=[
        html.Div("Methodology", className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(_ESTIMATE_CAVEAT, className="ps-caption"),
        html.Div([html.Div(t) for t in lines],
                 className="ps-caption", style={"lineHeight": "1.7",
                                                "marginBottom": "0"}),
    ])


def _eff_threshold(h_name, overrides):
    """Effective threshold + unit label for a hazard, given the user overrides."""
    default = HAZARD_MAP.get(h_name, {}).get("threshold")
    thr     = (overrides or {}).get(h_name, default)
    units   = (HAZARD_INFO.get(h_name) or {}).get("units", "")
    if thr is None:
        return ""
    thr_str = f"{thr:g}"
    return f"thr {thr_str}" + (f" {units}" if units else "")


def _no_data_panel(region_name):
    """Shown instead of figures where no hazard has usable data.

    Deliberately reports nothing numeric — not even a population total. The
    previous behaviour zeroed every field, which a reader cannot distinguish
    from a measured "no children are exposed here".
    """
    return html.Div(className="ps", children=[
        html.Div("Data not available", className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(
            f"Hazard and exposure data are not available for {region_name}, "
            "so no figures are reported. This reflects a gap in the underlying "
            "global datasets, not an absence of hazard or of children.",
            className="ps-caption", style={"marginBottom": "0"},
        ),
    ])


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
        html.Strong(f"Child exposure — {country_name}",
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(f"Total children under 18: {_fmt(total)}",
                 style={"fontSize": "0.78rem", "color": "var(--mid)",
                        "marginTop": "6px", "marginBottom": "6px"}),
    ]
    for topic in HAZARD_TOPICS:
        exposed = stats.get(topic, 0)
        if exposed and total:
            pct = exposed / total * 100
            lines.append(html.Div(
                f"• {topic}: {_fmt(exposed)} ({pct:.1f}%)",
                style={"fontSize": "0.78rem", "color": "var(--mid)"},
            ))
    return html.Div(className="ps", children=lines)


def _format_single_hazard_exposure(stats, topic, country_name):
    total   = stats.get("total_population", 0)
    exposed = stats.get(topic, 0)
    pct     = (exposed / total * 100) if total else 0
    lines   = [
        html.Strong(f"{topic} exposure — {country_name}",
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(f"Total children under 18: {_fmt(total)}",
                 style={"fontSize": "0.78rem", "color": "var(--mid)", "marginTop": "6px"}),
        html.Div(f"Exposed to {topic}: {_fmt(exposed)} ({pct:.1f}%)",
                 style={"fontSize": "0.78rem", "color": "var(--cyan)",
                        "marginTop": "4px", "fontWeight": "600"}),
    ]
    if topic in SUB_TOPIC_DETAIL:
        for h_name in HAZARD_TOPICS[topic]:
            sub = stats.get(h_name, 0)
            if sub and total:
                lines.append(html.Div(
                    f"  ↳ {h_name}: {_fmt(sub)} ({sub/total*100:.1f}%)",
                    style={"fontSize": "0.74rem", "color": "var(--lo)", "paddingLeft": "12px"},
                ))
    return html.Div(className="ps", children=lines)


def _format_multi_hazard_exposure(stats, topics, overlap, country_name):
    total = stats.get("total_population", 0)
    lines = [
        html.Strong(f"Multi-hazard exposure — {country_name}",
                    style={"fontSize": "0.82rem", "color": "var(--hi)"}),
        html.Div(f"Total children under 18: {_fmt(total)}",
                 style={"fontSize": "0.78rem", "color": "var(--mid)",
                        "marginTop": "6px", "marginBottom": "4px"}),
    ]
    for topic in topics:
        exposed = stats.get(topic, 0)
        pct = (exposed / total * 100) if total else 0
        lines.append(html.Div(
            f"• {topic}: {_fmt(exposed)} ({pct:.1f}%)",
            style={"fontSize": "0.78rem", "color": "var(--mid)"},
        ))
    if overlap is not None and total:
        ov_pct = overlap / total * 100
        lines.append(html.Div(
            f"Exposed to ALL ({' & '.join(topics)}): {_fmt(overlap)} ({ov_pct:.1f}%)",
            style={"fontSize": "0.78rem", "color": "var(--cyan)",
                   "marginTop": "6px", "fontWeight": "600"},
        ))
    return html.Div(className="ps", children=lines)


def _ai_processing():
    return html.Div("Computing ···", className="ai-processing")


def _confirm_sentence(action, params):
    B = lambda t: html.Strong(str(t), style={"color": "var(--hi)"})
    if action == "show_hazard_layer":
        parts = ["Showing hazard layer ", B(params.get("hazard_name", ""))]
        if params.get("country_name"):
            parts += [" in ", B(params["country_name"])]
    elif action == "show_topic_layer":
        parts = ["Showing ", B(params.get("topic_name", "")), " exposure layer"]
        if params.get("country_name"):
            parts += [" in ", B(params["country_name"])]
    elif action == "exposure_summary":
        adm = params.get("admin_level") or "adm0 (Country)"
        parts = ["Full hazard exposure summary for ", B(params.get("country_name", "")),
                 " at ", B(adm), " level"]
    elif action == "hazard_exposure":
        adm = params.get("admin_level") or "adm0 (Country)"
        parts = ["Child exposure to ", B(params.get("topic_name", "")),
                 " in ", B(params.get("country_name", "")),
                 " at ", B(adm), " level"]
    elif action == "multi_hazard_exposure":
        adm    = params.get("admin_level") or "adm0 (Country)"
        topics = params.get("topic_names", [])
        topic_parts = []
        for i, t in enumerate(topics):
            if i > 0:
                topic_parts.append(" & ")
            topic_parts.append(B(t))
        parts = ["Children exposed to "] + topic_parts + [
            " simultaneously in ", B(params.get("country_name", "")),
            " at ", B(adm), " level",
        ]
    elif action == "zoom_to_country":
        parts = ["Zooming map to ", B(params.get("country_name", ""))]
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
    "function(n){ return n ? ['Thinking ···', null] : [window.dash_clientside.no_update, window.dash_clientside.no_update]; }",
    Output("ai-confirm-line",   "children", allow_duplicate=True),
    Output("ai-response-panel", "children", allow_duplicate=True),
    Input("ai-submit-btn",      "n_clicks"),
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
    return False, no_update, no_update, "Incorrect credentials."


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
        return None, "", _ai_error("AI returned an unexpected response. Please try rephrasing.")
    except Exception as e:
        return None, "", _ai_error(f"AI error: {e}")

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
            return _ai_error(f"Country not found: {country}"), no_update, no_update
        return _ai_msg(message), no_update, viewport

    if action == "show_hazard_layer":
        hazard   = params.get("hazard_name", "")
        url, _   = get_hazard_tile_url(hazard)
        if not url:
            return _ai_error(f"Unknown hazard: {hazard}"), no_update, no_update
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
            return _ai_error(f"Could not compute exposure: {e}"), no_update, no_update
        return summary, no_update, viewport

    if action == "hazard_exposure":
        topic        = params.get("topic_name", "")
        country_name = params.get("country_name", "")
        admin_level  = params.get("admin_level") or "adm0 (Country)"
        if admin_level not in ADMIN_DATA:
            admin_level = "adm0 (Country)"
        if topic not in HAZARD_TOPICS:
            return _ai_error(f"Unknown hazard topic: {topic}"), no_update, no_update
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
            return _ai_error(f"Could not compute exposure: {e}"), no_update, no_update
        return summary, layer, viewport

    if action == "multi_hazard_exposure":
        topics       = params.get("topic_names", [])
        country_name = params.get("country_name", "")
        admin_level  = params.get("admin_level") or "adm0 (Country)"
        if admin_level not in ADMIN_DATA:
            admin_level = "adm0 (Country)"
        invalid = [t for t in topics if t not in HAZARD_TOPICS]
        if invalid:
            return _ai_error(f"Unknown topics: {', '.join(invalid)}"), no_update, no_update
        if not topics:
            return _ai_error("No topics specified."), no_update, no_update
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
            return _ai_error(f"Could not compute exposure: {e}"), no_update, no_update
        return summary, no_update, viewport

    return _ai_error("Unrecognised action from AI."), no_update, no_update


# ---------------------------------------------------------------------------
# Infrastructure tab
# ---------------------------------------------------------------------------

# ── Country dropdown (discovered from GEE, not hardcoded) ────────────────────

@app.callback(
    Output("infra-country-select", "options"),
    Output("infra-country-select", "placeholder"),
    Input("store-tab", "data"),
)
def infra_populate_countries(tab):
    """Fill the country dropdown from the assets actually in the GEE
    infrastructure folder.

    Runs on tab entry rather than at import so a country uploaded while the
    server is running appears without a restart (discovery is cached with a
    short TTL). A discovery failure yields an empty list and an explanatory
    placeholder instead of an error page.
    """
    if tab != "infrastructure":
        return no_update, no_update
    try:
        countries = infra_countries()
    except Exception as e:
        print(f"[infra] country discovery failed: {e}")
        countries = []
    if not countries:
        return [], "— no infrastructure data available —"
    return ([{"label": c, "value": c} for c in countries],
            "Search country / territory…")


# ── Region selection (writes the shared region stores) ────────────────────────

@app.callback(
    Output("store-country",       "data", allow_duplicate=True),
    Output("store-ucode",         "data", allow_duplicate=True),
    Output("store-bounds",        "data", allow_duplicate=True),
    Output("store-level",         "data", allow_duplicate=True),
    Output("store-clicked-ucode", "data", allow_duplicate=True),
    Output("store-clicked-name",  "data", allow_duplicate=True),
    Output("infra-level-section", "style"),
    Output("infra-layer-select",  "options"),
    Output("infra-layer-select",  "value"),
    Output("infra-layer-select",  "placeholder"),
    Input("infra-country-select", "value"),
    prevent_initial_call=True,
)
def infra_on_country_select(country):
    # Analyses are adm2-only: selecting a country hard-sets the adm2 level and
    # zooms to the country, so the map is immediately clickable for districts.
    # Also repopulate the facility-layer dropdown with THIS country's uploaded,
    # source-qualified layers (empty if none are ingested yet).
    if not country:
        return (None, None, None, None, None, None, {"display": "none"},
                [], None, "— select a country first —")
    ucode  = get_country_ucode(country)
    bounds = get_country_bounds(ucode)
    options = _infra_layer_options(country)
    value   = options[0]["value"] if options else None
    placeholder = ("— select a facility layer —" if options
                   else "— no infrastructure data for this country yet —")
    return (country, ucode, bounds, "adm2 (Districts/Counties)", None, None,
            {"display": "block"}, options, value, placeholder)


@app.callback(
    Output("infra-selected-badge-wrap", "children"),
    Output("infra-region-hint",         "style"),
    Input("store-clicked-name",         "data"),
    State("store-tab",                  "data"),
)
def infra_update_badge(name, tab):
    if not name or tab != "infrastructure":
        return None, no_update
    badge = html.Div(className="selected-badge", children=[
        html.Div("Selected region", className="selected-badge-tag"),
        html.Div(name, className="selected-badge-name"),
    ])
    return badge, {"display": "none"}  # hide the "click a district" hint


# ── Compute button enable/disable ─────────────────────────────────────────────

@app.callback(
    Output("infra-compute-btn",  "disabled"),
    Input("store-clicked-ucode", "data"),
    Input("store-level",         "data"),
)
def infra_toggle_compute(clicked_ucode, level):
    return not (clicked_ucode and level and level != "adm0 (Country)")


# ── Facility-layer / asset selection → resolve asset id, draw points ──────────

@app.callback(
    Output("store-infra-asset",       "data"),
    Output("infra-asset-status",      "children"),
    Output("infra-asset-status",      "style"),
    Output("infra-points-tile",       "url",      allow_duplicate=True),
    Output("main-map",                "viewport", allow_duplicate=True),
    Input("infra-layer-select",       "value"),
    Input("infra-asset-load-btn",     "n_clicks"),
    Input("store-tab",                "data"),
    Input("store-country",            "data"),
    State("infra-asset-input",        "value"),
    State("store-infra-viz",          "data"),
    prevent_initial_call=True,
)
def infra_load_asset(layer_name, _n, tab, country, asset_input, infra_viz):
    """Picking/loading a facility layer previews ALL points for the country
    (no AOI) on the dedicated infra-points-tile. infra_compute later overwrites
    it with the AOI-clipped points. Same path for every layer → uniform.
    Also fires on entering the Infra tab so the default (Schools) draws.
    The asset is resolved for the selected country from the discovered
    infrastructure assets."""
    err_style = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--red)"}
    ok_style  = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--mid)"}

    trig = ctx.triggered_id
    fit  = True  # fit the map to the asset bounds (suppressed on tab-entry)

    if trig == "store-tab":
        # Entering the Infra tab: draw the currently-selected layer's points
        # without yanking the viewport. Skip if results are already computed
        # (store-infra-viz set) so we don't overwrite the AOI-clipped points.
        if tab != "infrastructure" or not layer_name or infra_viz:
            return no_update, no_update, no_update, no_update, no_update
        asset_id = infra_asset_id(country, layer_name)
        if not asset_id:
            return no_update, no_update, no_update, no_update, no_update
        color    = infra_layer_style(layer_name)["color"].lstrip("#")
        label    = layer_name
        fit      = False
    elif trig in ("infra-layer-select", "store-country"):
        # Layer picked, or country switched: reload the layer for that country.
        # On a country switch, infra_on_country_select already zooms to the
        # country via store-bounds, so don't also fit to the asset bounds.
        # store-country is shared with the Analysis tab, so ignore its changes
        # unless we're actually on the Infrastructure tab.
        if trig == "store-country" and tab != "infrastructure":
            return no_update, no_update, no_update, no_update, no_update
        if not layer_name:
            return None, "", ok_style, "", no_update
        asset_id = infra_asset_id(country, layer_name)
        if not asset_id:
            return None, "Select a country first.", ok_style, "", no_update
        color    = infra_layer_style(layer_name)["color"].lstrip("#")
        label    = layer_name
        if trig == "store-country":
            fit = False
    else:
        if not asset_input or not asset_input.strip():
            return no_update, no_update, no_update, no_update, no_update
        asset_id = asset_input.strip()
        color    = "e67e22"
        label    = asset_id.split("/")[-1]

    try:
        n, props = get_asset_info(asset_id)
        tile_url = get_infra_tile_url(asset_id, color)   # whole country (no AOI)
        bounds   = get_asset_bounds(asset_id)
    except Exception as e:
        return None, f"Error loading asset: {e}", err_style, no_update, no_update

    status   = f"✓  {n} features — {label}"
    viewport = {"bounds": bounds, "transition": "fitBounds"} if fit else no_update
    return ({"asset_id": asset_id, "name_field": "name", "props": props},
            status, ok_style, tile_url, viewport)


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


def _infra_download(groups, rows, filename, banner_lines=None):
    """Secondary (ghost) styled-Excel download link, below the visual summary."""
    return html.A(
        "⬇  Download full data (Excel)",
        href=build_xlsx("Facility exposure", groups, rows, banner_lines),
        download=filename, className="ps-btn-ghost",
        style={"display": "block", "textAlign": "center", "textDecoration": "none",
               "padding": "9px 16px", "marginTop": "10px"},
    )


# Disclaimer shown in the panel and embedded in the CSV: per-facility figures
# come from each facility's own (overlapping) buffer and must not be summed.
INFRA_PERFAC_DISCLAIMER = (
    "Per-facility figures count children within each facility's individual "
    "buffer; buffers overlap, so these values may double-count and must not be "
    "summed. Use the dissolved-buffer summary above for district totals."
)


def _infra_error(msg):
    return html.Div(msg, style={"color": "var(--red)", "fontSize": "0.75rem",
                                "padding": "12px 16px"})


def _metric(value, label, color=None):
    style = {"color": color} if color else {}
    return html.Div(className="metric", children=[
        html.Div(value, className="metric-val", style=style),
        html.Div(label, className="metric-lbl"),
    ])


def _bar_row(label, count, pct, color):
    """Ranked bar row mirroring the Exposure tab (info-row + bar-track)."""
    return html.Div([
        html.Div(className="info-row", children=[
            html.Span(className="info-lbl", children=[
                html.Span(className="topic-swatch", style={"background": color}),
                label,
            ]),
            html.Span([
                html.Span(f"{count:,}", className="info-val"),
                html.Span(f" ({pct:.1f}%)", className="info-pct"),
            ]),
        ]),
        html.Div(className="bar-track", children=[
            html.Div(className="bar-fill",
                     style={"width": f"{min(100, pct)}%", "background": color}),
        ]),
    ])


def _infra_panel(title, metric_cards, bar_rows, download, caption=None):
    children = [html.Div(title, className="ps-label")]
    if caption:
        children.append(html.Div(caption, className="ps-caption"))
    if metric_cards:
        children.append(html.Div(className="metrics", children=metric_cards))
    children.extend(bar_rows)
    if download is not None:
        children.append(download)
    return html.Div(className="ps", children=children)


def _infra_combined_items(per_topic, per_sub, site_tally, sel_topics, total,
                          overrides=None):
    """Merged per-hazard rows: exposed children (bar) + '· N facilities' from
    the facility-site tally, with subhazard child-population detail rows. Each
    row shows the effective threshold used (custom overrides honored)."""
    tdata = sorted(
        ({"topic": t,
          "pop":  int(round((per_topic or {}).get(t) or 0)),
          "fac":  int(round((site_tally or {}).get(t) or 0))}
         for t in sel_topics),
        key=lambda d: d["pop"], reverse=True,
    )
    items = []
    for td in tdata:
        topic = td["topic"]
        pct   = (td["pop"] / total * 100) if total else 0
        color = TOPIC_COLORS.get(topic, "#888")
        # Single-hazard topics show the threshold inline; multi-hazard topics
        # show it per-subhazard on the detail rows below.
        hazards = HAZARD_TOPICS.get(topic, [])
        thr_txt = _eff_threshold(hazards[0], overrides) if len(hazards) == 1 else ""
        # Bar row with an extra facility-count suffix.
        header = html.Div([
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch", style={"background": color}),
                    _info_icon(hazards[0], "infra") if hazards else None,
                    topic.upper(),
                ]),
                html.Span([
                    html.Span(f"{td['pop']:,}", className="info-val"),
                    html.Span(f" ({pct:.1f}%)", className="info-pct"),
                    html.Span(f"  · {td['fac']} fac.", className="info-pct",
                              style={"color": "var(--lo)"}),
                ]),
            ]),
            (html.Div(thr_txt, className="info-pct",
                      style={"color": "var(--lo)", "fontSize": "0.7rem",
                             "marginTop": "1px"}) if thr_txt else None),
            html.Div(className="bar-track", children=[
                html.Div(className="bar-fill",
                         style={"width": f"{min(100, pct)}%", "background": color}),
            ]),
        ])
        if topic in SUB_TOPIC_DETAIL:
            sub_rows = []
            for h in HAZARD_TOPICS[topic]:
                hc = int(round((per_sub or {}).get(h) or 0))
                thr = _eff_threshold(h, overrides)
                sub_rows.append(html.Div(className="info-row", children=[
                    html.Span(["└ ",
                               _info_icon(h, "infra"),
                               _hazard_label(h),
                               html.Span(f"  {thr}" if thr else "",
                                         style={"color": "var(--lo)",
                                                "fontSize": "0.9em"})],
                              className="info-lbl",
                              style={"paddingLeft": "22px", "color": "var(--lo)",
                                     "fontWeight": "400", "fontSize": "0.85em"}),
                    html.Span(f"{hc:,}" if hc else "—", className="info-val",
                              style={"color": "var(--mid)", "fontWeight": "500"}),
                ]))
            items.append(html.Details(open=True, className="topic-detail", children=[
                html.Summary(header, className="topic-detail-summary"),
                *sub_rows,
            ]))
        else:
            items.append(html.Div(header))
    return items


@app.callback(
    Output("infra-results-panel", "children"),
    Output("infra-pop-tile",      "url"),
    Output("infra-points-tile",   "url"),
    Output("infra-voronoi-geojson","data",     allow_duplicate=True),
    Output("store-infra-viz",     "data"),
    Output("store-infra-pending", "data"),
    Output("infra-download-wrap", "children"),
    Input("infra-compute-btn",    "n_clicks"),
    State("store-infra-asset",    "data"),
    State("store-clicked-ucode",  "data"),
    State("store-clicked-name",   "data"),
    State("store-level",          "data"),
    State("infra-topic-select",   "value"),
    State({"type": "infra-threshold", "index": ALL}, "value"),
    State({"type": "infra-threshold", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def infra_compute(_n, asset, adm2_ucode, region_name, level, topics,
                  thr_values, thr_ids):
    """Single combined analysis: exposed population (Voronoi catchments) +
    exposed facilities (site hazard). Renders panel + map; per-facility CSV in
    phase 2."""
    nu = (no_update,) * 6
    if not asset:
        return (_infra_error("Select or load a facility layer first."), *nu)
    if not adm2_ucode or not level or level == "adm0 (Country)":
        return (_infra_error("Select an adm2 region and click a district on the map "
                             "to bound the analysis."), *nu)
    # Districts inherit their country's data availability: the adm2 ucode
    # carries the same stem, so this catches them without a separate list.
    if is_no_data_ucode(adm2_ucode):
        return (_no_data_panel(region_name or "this region"), *nu)
    asset_id = asset["asset_id"]
    topics_sel = topics or None
    overrides, _ = _threshold_overrides(_infra_editor_topics(topics), thr_ids, thr_values)

    color = _infra_color_for_asset(asset_id)

    try:
        r = compute_facility_combined(asset_id, adm2_ucode, topics_sel,
                                      threshold_overrides=overrides or None)
    except Exception as e:
        return (_infra_error(f"GEE error: {e}"), *nu)

    sel_topics = r.get("topics") or [t for t in HAZARD_TOPICS
                                     if t not in EXPOSURE_ONLY_TOPICS]
    nfac    = int(r.get("n_facilities") or 0)
    total   = int(round(r.get("served_children_total") or 0))
    exposed = int(round(r.get("served_children_exposed") or 0))
    site_tally = r.get("facility_site_tally") or {}
    n_site_exp = sum(1 for f in r.get("facilities_site", [])
                     if any(int(f["properties"].get(t) or 0) for t in sel_topics))
    pct     = f"{exposed/total*100:.1f}%" if total else "—"

    cards = [
        _metric(f"{nfac:,}", "Facilities"),
        _metric(_fmt(total), "Children served"),
        _metric(_fmt(exposed), "Children exposed", color="#e31a1c"),
        _metric(f"{n_site_exp:,}", "Facilities in hazard", color="#e31a1c"),
    ]

    items = _infra_combined_items(r.get("per_topic_exposed"),
                                  r.get("per_subhazard_exposed"),
                                  site_tally, sel_topics, total,
                                  overrides=overrides or None)

    n_custom = len(overrides or {})
    thr_note = (f"{n_custom} custom threshold(s) applied"
                if n_custom else "Standard (default) thresholds")
    where = region_name or "the selected district"
    caption = (f"{nfac:,} facilities in {where}. Population is attributed to "
               "each facility's nearest-neighbour (Voronoi) catchment; per-hazard "
               "rows show exposed children · facilities whose site is in that "
               f"hazard. {thr_note}.")

    panel = html.Div([
        _infra_panel("Facility exposure", cards, items, None, caption=caption),
        _provenance_block([
            f"Region: {region_name or adm2_ucode} · {level}",
            "Facilities: as supplied by the selected infrastructure layer "
            "(see the layer name for its source)",
        ]),
    ])

    # ── Map layers ──
    try:
        pop_url, _vis = get_clipped_pop_tile_url(asset_id, adm2_ucode)
    except Exception:
        pop_url = no_update
    try:
        pt_url = get_infra_tile_url(asset_id, color, adm2_ucode)
    except Exception:
        pt_url = no_update
    cells = r.get("voronoi_geojson") or no_update

    viz = {"asset_id": asset_id, "ucode": adm2_ucode, "color": color}
    pending = {"asset_id": asset_id, "ucode": adm2_ucode, "topics": topics_sel,
               "overrides": overrides or None}
    placeholder = html.Div("Preparing per-facility download…",
                           className="ps-caption", style={"padding": "4px 0"})

    return (panel, pop_url, pt_url, cells, viz, pending, placeholder)


@app.callback(
    Output("infra-download-wrap", "children", allow_duplicate=True),
    Input("store-infra-pending",  "data"),
    prevent_initial_call=True,
)
def infra_compute_perfacility(pending):
    """Phase 2: per-facility CSV — Voronoi population + exposed pop + site flags
    + raw intensity. Voronoi cells don't overlap → figures are summable."""
    if not pending:
        return no_update
    try:
        r = compute_facility_combined(pending["asset_id"], pending["ucode"],
                                      pending.get("topics"),
                                      threshold_overrides=pending.get("overrides"))
    except Exception as e:
        return html.Div(f"Per-facility data failed: {e}", className="ps-caption",
                        style={"color": "var(--red)"})

    topic_cols = r.get("topic_cols") or []
    int_cols   = r.get("intensity_cols") or []
    # Voronoi pop keyed by facility location (names aren't unique — many blank).
    def _key(p):
        lon, lat = p.get("lon"), p.get("lat")
        return (round(lon, 6), round(lat, 6)) if lon is not None and lat is not None else None
    vor = {}
    for f in r.get("facilities_voronoi", []):
        p = f.get("properties") or {}
        vor[_key(p)] = (p.get("vpop"), p.get("vexp"))

    rows = []
    for f in r.get("facilities_site", []):
        p = f.get("properties") or {}
        nm = p.get("fname")
        vpop, vexp = vor.get(_key(p), (None, None))
        row = {
            "name": nm or "",
            "lon":  round(p.get("lon"), 6) if p.get("lon") is not None else "",
            "lat":  round(p.get("lat"), 6) if p.get("lat") is not None else "",
            "voronoi_pop":  int(round(vpop or 0)),
            "exposed_pop":  int(round(vexp or 0)),
        }
        for t in topic_cols:
            row[t] = int(round(p.get(t) or 0))
        for h in int_cols:
            v = p.get(h)
            row[h] = round(v, 4) if v is not None else ""
        rows.append(row)

    # Column groups drive the styled workbook's merged header + cell formats.
    groups = [
        {"title": "Facility", "columns": [
            {"key": "name", "label": "Name",      "kind": "text"},
            {"key": "lon",  "label": "Longitude", "kind": "float"},
            {"key": "lat",  "label": "Latitude",  "kind": "float"},
        ]},
        {"title": "Catchment population (children)", "columns": [
            {"key": "voronoi_pop", "label": "In catchment", "kind": "int"},
            {"key": "exposed_pop", "label": "Exposed",      "kind": "int"},
        ]},
        {"title": "Site in hazard (1 = exposed)", "columns": [
            {"key": t, "label": t, "kind": "flag"} for t in topic_cols
        ]},
        {"title": "Hazard intensity at site (native units)", "columns": [
            {"key": h,
             "label": f"{_hazard_label(h)}"
                      + (f" ({(HAZARD_INFO.get(h) or {}).get('units','')})"
                         if (HAZARD_INFO.get(h) or {}).get("units") else ""),
             "kind": "float"}
            for h in int_cols
        ]},
    ]
    banner = ["Facility exposure — per-facility detail"]
    banner += _thresholds_banner(pending.get("topics")
                                 or [t for t in HAZARD_TOPICS
                                     if t not in EXPOSURE_ONLY_TOPICS],
                                 pending.get("overrides"))
    banner.append("Catchment population uses each facility's nearest-neighbour "
                  "(Voronoi) catchment (non-overlapping, summable). Site flags and "
                  "Exposed reflect the thresholds above; intensity columns are raw "
                  "native values.")
    return _infra_download(groups, rows, "infra_facility_exposure.xlsx",
                           banner_lines=banner)


# ── Clear infra map layers when leaving the Infrastructure tab ─────────────────

@app.callback(
    Output("infra-selection-tile", "url",  allow_duplicate=True),
    Output("infra-pop-tile",       "url",  allow_duplicate=True),
    Output("infra-points-tile",    "url",  allow_duplicate=True),
    Output("infra-voronoi-geojson","data", allow_duplicate=True),
    Output("store-infra-viz",      "data", allow_duplicate=True),
    Input("store-tab",             "data"),
    prevent_initial_call=True,
)
def infra_clear_layers_on_leave(tab):
    """The infra tiles live in dedicated top-level components (not the shared
    data-layers group), so nothing clears them on tab change. Blank them when
    navigating away so they don't linger over other tabs' maps. Re-entering the
    tab redraws points via infra_load_asset."""
    if tab == "infrastructure":
        return (no_update,) * 5
    return "", "", "", None, None


# ── Unified map legend overlay (top-left) ─────────────────────────────────────
# Every tab writes a list of legend specs (plain dicts from config.py) into
# store-legend; this single renderer turns them into DOM. Adding a legend for a
# new layer is therefore data, not markup.

def _legend_eye(layer_id, visible=True):
    """Leading eye toggle for a legend row (clientside-controlled)."""
    cls = "bi bi-eye-fill map-legend-eye" if visible else \
          "bi bi-eye-slash map-legend-eye map-legend-eye-off"
    return html.I(className=cls, n_clicks=0 if visible else 1,
                  id={"type": "legend-eye", "index": layer_id})


def _legend_row(spec):
    """Render one legend spec (see config.gradient_spec / swatch_spec)."""
    layer_id   = spec.get("layer_id")
    toggleable = spec.get("toggleable") and layer_id
    visible    = spec.get("visible", True)
    eye        = _legend_eye(layer_id, visible) if toggleable else None

    if spec["kind"] == "gradient":
        pal  = spec.get("palette") or ["#ccc"]
        grad = ", ".join(pal)
        unit = spec.get("unit") or ""
        lo   = spec.get("min", 0)
        hi   = spec.get("max", 1)
        return html.Div(className="map-legend-pop", children=[
            html.Div(className="map-legend-pop-head", children=[
                eye,
                html.Span(spec["label"], className="map-legend-item-label"),
            ]),
            html.Div(className="legend-bar",
                     style={"background": f"linear-gradient(to right,{grad})",
                            "width": "100%"}),
            html.Div(className="legend-range", children=[
                html.Span(f"{lo:g}" if isinstance(lo, (int, float)) else str(lo)),
                html.Span((f"{hi:g}" if isinstance(hi, (int, float)) else str(hi))
                          + (f" {unit}" if unit else "")),
            ]),
        ])

    # swatch: a dot (points) or a line (outlines)
    color = spec.get("color") or "#888"
    if spec.get("shape") == "line":
        mark = html.Span(className="map-legend-line",
                         style={"borderTop": f"2px solid {color}"})
    else:
        mark = html.Span(className="map-legend-dot",
                         style={"background": color, "border": "1.5px solid #fff"})
    return html.Div(className="map-legend-item", children=[
        eye, mark,
        html.Span(spec["label"], className="map-legend-item-label"),
    ])


@app.callback(
    Output("map-legend-body", "children"),
    Output("map-legend",      "style"),
    Input("store-legend",     "data"),
)
def render_map_legend(specs):
    if not specs:
        return None, {"display": "none"}
    # "flex" (not "block") so the stylesheet's column layout applies — the
    # header stays pinned while a long row list scrolls inside the body.
    return [_legend_row(s) for s in specs], {"display": "flex"}


# Collapse / expand the legend panel (clientside — no server round-trip).
app.clientside_callback(
    """
    function(n) {
        var open = (n % 2 === 0);                     // even clicks = expanded
        return [
            open ? {} : {display: "none"},
            open ? "bi bi-chevron-up" : "bi bi-chevron-down"
        ];
    }
    """,
    Output("map-legend-body",    "style"),
    Output("map-legend-chevron", "className"),
    Input("map-legend-header",   "n_clicks"),
    prevent_initial_call=True,
)


# ── Clientside layer toggles (eye icons → layer opacity + icon state) ──────────
# One generalized handler for every toggleable layer, keyed by the legend row's
# layer_id. Purely clientside: showing/hiding a layer never hits the server.
app.clientside_callback(
    """
    function(clicks, ids, topicOpacities) {
        // Even clicks = visible, odd = hidden. Rows render with n_clicks=1
        // when they start hidden, so the same parity rule covers both.
        var vis = function(n){ return ((n || 0) % 2 === 0); };
        var state = {};
        (ids || []).forEach(function(id, i){
            state[id.index] = vis((clicks || [])[i]);
        });
        var on = function(key, base){
            return (key in state) ? (state[key] ? base : 0) : base;
        };

        // Per-topic analysis slots keep their existing opacity when the legend
        // has no row for them (e.g. a stale slot from a previous Compute).
        var topics = (topicOpacities || []).map(function(_, i){
            var key = "analysis-topic-" + i;
            return (key in state) ? (state[key] ? 0.7 : 0) : 0;
        });

        var cellStyle = {color:"#1CABE2", weight:1, fillOpacity:0.0,
                         opacity: (("infra-catchments" in state)
                                   && !state["infra-catchments"]) ? 0.0 : 0.7};

        var icons = (ids || []).map(function(id, i){
            return vis((clicks || [])[i])
                ? "bi bi-eye-fill map-legend-eye"
                : "bi bi-eye-slash map-legend-eye map-legend-eye-off";
        });

        // The forecast intensity row renders hidden (n_clicks=1), so its first
        // click turns it on under the same parity rule as every other row.
        return [
            on("infra-pop", 0.8), on("infra-points", 0.9),
            on("infra-selection", 0.75), {style: cellStyle},
            on("analysis-exposed", 0.85), on("analysis-selection", 0.75),
            topics,
            on("forecast-intensity", 0.75), on("forecast-exposed", 0.85),
            on("forecast-selection", 0.75),
            icons
        ];
    }
    """,
    Output("infra-pop-tile",        "opacity"),
    Output("infra-points-tile",     "opacity"),
    Output("infra-selection-tile",  "opacity"),
    Output("infra-voronoi-geojson", "options"),
    Output("analysis-exposed-tile", "opacity"),
    Output("analysis-selection-tile", "opacity"),
    Output({"type": "analysis-topic-tile", "index": ALL}, "opacity"),
    Output("forecast-intensity-tile", "opacity"),
    Output("forecast-exposed-tile",   "opacity"),
    Output("forecast-selection-tile", "opacity"),
    Output({"type": "legend-eye", "index": ALL}, "className"),
    Input({"type": "legend-eye", "index": ALL},  "n_clicks"),
    State({"type": "legend-eye", "index": ALL},  "id"),
    State({"type": "analysis-topic-tile", "index": ALL}, "opacity"),
    prevent_initial_call=True,
)


# ── Basemap toggle: map ↔ satellite (clientside) ──────────────────────────────
app.clientside_callback(
    """
    function(n) {
        var on = (n % 2 === 1);                       // odd clicks = satellite on
        var cls = on ? "basemap-toggle active" : "basemap-toggle";
        return [on ? 1 : 0, cls];
    }
    """,
    Output("sat-basemap",    "opacity"),
    Output("basemap-toggle", "className"),
    Input("basemap-toggle",  "n_clicks"),
    prevent_initial_call=True,
)


# ===========================================================================
# Forecast & Live tab
#
# Structurally parallel to the Analysis tab, with two deliberate differences:
#   * every layer is parameterised by a date window, so nothing is precomputed;
#   * there is no ADM0 fast path, so the threshold applies at every level.
# ===========================================================================

def _forecast_cfg(dataset, asset):
    """Resolve the active selection to a config-shaped dict, whether it came
    from the catalog dropdown or the pasted-asset box. Returns None if neither
    is ready."""
    if asset and asset.get("asset_id"):
        return {
            "name": CUSTOM_NAME, "label": asset["asset_id"].split("/")[-1],
            "units": "", "threshold": 0, "min": 0, "max": 100,
            "reducers": list(REDUCERS), "palette": CUSTOM_PALETTE,
            "topic": None, "kind": "nrt", "note": "",
            "custom_id": asset["asset_id"], "custom_band": asset.get("band"),
            "is_custom": True,
        }
    if dataset and dataset in FORECAST_MAP:
        cfg = dict(FORECAST_MAP[dataset])
        cfg["custom_id"] = None
        cfg["custom_band"] = None
        cfg["is_custom"] = False
        return cfg
    return None


def _forecast_legend_specs(viz, preview=None):
    """Legend rows for the Forecast tab.

    Before Compute there is only the previewed intensity layer, so the legend
    describes that alone — without it the preview is an unlabelled colour wash
    with no way to read a value. After Compute, `viz` takes over and adds the
    exposed-population and selected-region rows.
    """
    if not viz:
        if preview and preview.get("vis"):
            units = preview.get("units", "")
            label = preview.get("label", "Preview")
            return [vis_gradient_spec(
                f"{label} ({units})".strip() if units else label,
                preview["vis"], layer_id="forecast-intensity", unit=units,
                toggleable=True)]
        return None
    specs = []
    vis = viz.get("intensity_vis")
    if vis:
        specs.append(vis_gradient_spec(
            f"{viz.get('label','Forecast')} ({viz.get('units','')})".strip(),
            vis, layer_id="forecast-intensity", unit=viz.get("units", ""),
            toggleable=True, visible=False))
    specs.append(gradient_spec(
        "Children exposed", POP_VIS["palette"], POP_VIS["min"], POP_VIS["max"],
        layer_id="forecast-exposed", unit="per 100 m", toggleable=True))
    specs.append(swatch_spec("Selected region", "#FFD700", shape="line",
                             layer_id="forecast-selection", toggleable=True))
    return specs


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
    default_thr = cfg.get("threshold", 0)
    step, marks = _thr_step_marks(lo, hi)
    reducers = [{"label": REDUCERS.get(r, r), "value": r}
                for r in cfg.get("reducers") or ["mean"]]
    note = cfg.get("note", "")

    return ({"display": "block"}, note, start, end, min_d, max_d,
            reducers, (cfg.get("reducers") or ["mean"])[0],
            default_thr, lo, hi, step, lo, hi, default_thr, step, marks,
            cfg.get("units", ""))


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
    Input("forecast-dataset-select", "value"),
    Input("store-fc-expanded",       "data"),
    # Tab entry re-reads the runtime override table (TTL-cached), so a dataset
    # enabled in GEE shows up without restarting the server.
    Input("store-tab",               "data"),
)
def forecast_render_list(selected, expanded, tab):
    """Render both tabs' lists.

    One callback rather than two so a single read of active_datasets_live()
    serves both, and the selection highlight can never disagree between them.
    """
    return (_forecast_rows_for("forecast", selected, expanded),
            _forecast_rows_for("nrt", selected, expanded))


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
    Input("forecast-dataset-select",   "value"),
    Input("store-forecast-asset",      "data"),
)
def forecast_kind_badge(dataset, asset):
    cfg = _forecast_cfg(dataset, asset)
    if not cfg:
        return None, None, {"display": "none"}
    badge = _forecast_kind_badge(cfg)
    url   = FORECAST_MAP.get(cfg["name"], {}).get("source_url")
    link  = html.A("View in Earth Engine catalog ↗", href=url, target="_blank",
                   className="hi-link",
                   style={"fontSize": "0.68rem"}) if url else None
    return badge, link, {"display": "block"}


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
    State("store-forecast-asset",     "data"),
    State("store-forecast-viz",       "data"),
    prevent_initial_call=True,
)
def forecast_preview(dataset, start, end, reducer, asset, viz):
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
    msg = (f"Previewing {n:,} image(s) · {vis['min']:g}–{vis['max']:g} "
           f"{units}".strip() + " — select a region below to compute exposure.")
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
            "dataset": cfg["name"],
            "asset_id": cfg.get("custom_id"), "band": cfg.get("custom_band")}

    # Intensity tile is best-effort: a failed stretch must not lose the numbers.
    intensity_vis = None
    try:
        _, intensity_vis = get_forecast_intensity_tile(
            cfg["name"], s, e, reducer, target, level,
            custom_id=cfg.get("custom_id"), custom_band=cfg.get("custom_band"))
    except Exception:
        pass

    viz = {**meta, "ucode": target, "intensity_vis": intensity_vis}
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
    male    = int(round(result.get("total_population_male", 0) or 0))
    fema    = int(round(result.get("total_population_female", 0) or 0))
    pct     = (exposed / total * 100) if total else 0
    units   = meta.get("units", "")
    thr_txt = f"{meta['threshold']:g}" + (f" {units}" if units else "")

    export = {
        "dataset": meta["label"], "dataset_id": meta.get("dataset"),
        "gee_asset": meta.get("asset_id"), "band": meta.get("band"),
        "region": meta["region"], "admin_level": meta["level"],
        "window_start": meta["start"], "window_end": meta["end"],
        "aggregation": meta["reducer"], "threshold": meta["threshold"],
        "threshold_units": units, "images_in_window": meta["n_images"],
        "children_exposed": exposed, "total_children": total,
        "male": male, "female": fema,
        "pct_exposed": round(pct, 2),
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
                html.Span(f"{male:,}", className="info-val",
                          style={"color": "var(--mid)", "fontWeight": "500"}),
            ]),
            html.Div(className="info-row", children=[
                html.Span("└ Girls", className="info-lbl",
                          style={"paddingLeft": "22px", "color": "var(--lo)",
                                 "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span(f"{fema:,}", className="info-val",
                          style={"color": "var(--mid)", "fontWeight": "500"}),
            ]),
        ]),
        # Provenance — the window and image count travel with the number.
        html.Div(className="ps", children=[
            html.Div("How this was computed", className="ps-label"),
            html.Div([
                html.Div(f"Dataset: {meta['label']}"),
                html.Div(f"Window: {meta['start']} → {meta['end']} "
                         f"({meta['n_images']:,} images)"),
                html.Div(f"Aggregation: {REDUCERS.get(meta['reducer'], meta['reducer'])}"),
                html.Div(f"Exposed where value > {thr_txt}"),
                html.Div(f"Region: {meta['region']} · {meta['level']}"),
            ], className="ps-caption", style={"lineHeight": "1.7"}),
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


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8502)
