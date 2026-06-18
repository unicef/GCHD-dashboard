# =============================================================================
# app.py — UNICEF CCRI Global Child Hazard Database (Dash)
# =============================================================================

import os
import re
import json as _json

import dash
from dash import dcc, html, Input, Output, State, ctx, no_update, ALL
import dash_leaflet as dl
import plotly.graph_objects as go

import json as _json_mod

from config import (
    HAZARD_TOPICS, TOPIC_COLORS, ADMIN_DATA,
    HAZARDS, HAZARD_MAP, SUB_TOPIC_DETAIL,
    MHC_OPTIONS, MHI_OPTIONS, HAZARD_INFO, EXPOSURE_ONLY_TOPICS, MHC_EXCLUDED_TOPICS,
    FORCE_NULL_RULES, EXCLUDE_ISO3,
)
from ai_core import initialize_ai, ask_gemini
from auth import request_otp, verify_otp, SESSION_HOURS
from gee_core import (
    initialize_gee, build_core_images,
    get_country_names,
    get_country_ucode, get_country_bounds,
    get_topic_tile_url, get_topic_count_tile_url,
    get_pixel_score_tile_url, get_pixel_score_percentile_tile_url,
    get_hazard_tile_url, get_admin_boundary_tile_url, get_selected_feature_tile_url,
    compute_exposure_custom, compute_exposure_asset,
    get_asset_info, get_asset_bounds, get_custom_asset_tile_url,
    get_feature_at_point, compute_exposure, compute_topic_overlap,
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
CARTO_FALLBACK = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
GEE_ATTR       = "Google Earth Engine / UNICEF"
UN_ATTR        = "© United Nations Geospatial"
CARTO_ATTR     = "© OpenStreetMap contributors © CARTO"
TOPIC_LIST     = list(HAZARD_TOPICS.keys())

# Ordered layers for the hazard list
MH_LAYERS  = ["Multi Hazard Count", "Multi Hazard Intensity"]
HAZ_LAYERS = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
ALL_LAYERS = MH_LAYERS + HAZ_LAYERS   # order matches pattern-match order in layout


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
    # "flood_river_2yr" → "Flood River 2yr"
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


def _layer_meta(name):
    if name == "Multi Hazard Count":
        return f"{len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)} hazard topics combined"
    if name == "Multi Hazard Intensity":
        return "Pixel-based hazard score (MHI)"
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
                src="/assets/unicef_logo.webp?v=3",
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
            nav_btn("bi bi-layers",        "Layers",   "btn-hazard",   active=True),
            nav_btn("bi bi-people",        "Exposure", "btn-exposure"),
            nav_btn("bi bi-stack",         "Multi HZ", "btn-mh"),
            nav_btn("bi bi-bar-chart-line","Analysis", "btn-analysis"),
            nav_btn("bi bi-robot",         "AI",       "btn-ai"),
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

    return html.Div(id="tab-hazard", children=[
        html.Div(className="ph", children=[
            html.Div("Hazard Layers", className="ph-title"),
            html.Div("Select a layer to display on the map", className="ph-sub"),
        ]),
        html.Div(items, className="layer-list"),
        html.Div(id="hazard-legend"),
    ])


def tab_exposure():
    topics = [
        html.Div(
            [
                html.Div(className="topic-swatch",
                         style={"background": TOPIC_COLORS.get(t, "#888")}),
                html.Span(t, className="topic-name"),
            ],
            id={"type": "topic-item", "index": t},
            className="topic-item",
            n_clicks=0,
        )
        for t in TOPIC_LIST
    ]
    return html.Div(id="tab-exposure", style={"display": "none"}, children=[
        html.Div(className="ph", children=[
            html.Div("Children's Exposure", className="ph-title"),
            html.Div("Population exposed to each hazard topic", className="ph-sub"),
        ]),
        html.Div(topics),
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
    mhc_pal = ["#ffffd4","#fed98e","#fe9929","#d95f0e","#993404"]
    mhi_pal = ["#000004","#3b0f70","#8c2981","#de4968","#fe9f6d"]
    n = len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)
    return html.Div(id="tab-mh", style={"display": "none"}, children=[
        html.Div(className="ph", children=[
            html.Div("Multi Hazard Indicators", className="ph-title"),
            html.Div("Combined hazard count & intensity", className="ph-sub"),
        ]),
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
            html.Div("Count layer legend", className="ps-label"),
            html.Div(className="legend-bar",
                     style={"background": f"linear-gradient(to right,{','.join(mhc_pal)})"}),
            html.Div(className="legend-range",
                     children=[html.Span("1 topic"), html.Span(f"{n} topics")]),
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
        html.Div(className="ps", children=[
            html.Div("Intensity layer legend", className="ps-label"),
            html.Div(className="legend-bar",
                     style={"background": f"linear-gradient(to right,{','.join(mhi_pal)})"}),
            html.Div(className="legend-range",
                     children=[html.Span("Low (0)"), html.Span("High (10)")]),
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
        html.Div(className="ph", children=[
            html.Div("Exposure Analysis", className="ph-title"),
            html.Div("Compute children exposed by admin region", className="ph-sub"),
        ]),
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
            html.Div(id="compute-section", style={"display": "none"}),
            html.Div(id="selected-badge-wrap"),
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


def tab_ai():
    return html.Div(id="tab-ai", style={"display": "none"}, children=[
        html.Div(className="ph", children=[
            html.Div("AI Assistant", className="ph-title"),
            html.Div("Ask about hazard layers or child exposure", className="ph-sub"),
        ]),
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
                    subdomains="abcd",
                ),
                dl.TileLayer(
                    url=UN_CLEARMAP,
                    attribution=UN_ATTR,
                    maxZoom=6,
                ),
                dl.ZoomControl(position="topright"),
                dl.LayerGroup(id="data-layers"),
                dl.LayerGroup(id="boundary-layers"),
                dl.LayerGroup(id="selection-layer"),
                dl.GeoJSON(
                    id="custom-boundary-geojson",
                    data=None,
                    options={"style": {"color": "#e67e22", "weight": 2,
                                       "fillOpacity": 0.05, "opacity": 0.9}},
                ),
            ],
            style={"height": "100vh", "width": "100%"},
        ),
    ])


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "UNICEF GCHD — Global Child Hazard Database"
server = app.server

from werkzeug.middleware.proxy_fix import ProxyFix
app.server.wsgi_app = ProxyFix(app.server.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ---------------------------------------------------------------------------
# Auth — Flask secret key (auto-generated on first run)
# ---------------------------------------------------------------------------
import secrets as _secrets, datetime as _dt
from flask import session as _fsess, request as _freq, redirect as _fredirect

_key_path = os.path.join(os.path.dirname(__file__), "credentials", "flask_secret.txt")
if os.path.exists(_key_path):
    app.server.secret_key = open(_key_path).read().strip()
else:
    _key = _secrets.token_hex(32)
    open(_key_path, "w").write(_key)
    app.server.secret_key = _key

_PUBLIC_PATHS = ("/login", "/assets/", "/_dash-component-suites/", "/favicon.ico")


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
        hint = '<a href="/login">Use a different email</a>'
    err_html = '<div class="lc-err">' + error + "</div>" if error else '<div class="lc-err"></div>'
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>UNICEF Hazard DB — Sign In</title>"
        '<link rel="stylesheet" href="/assets/style.css">'
        "<style>" + _CSS + "</style>"
        "</head><body>"
        '<div class="lc">'
        '<div class="lc-icon"><i class="bi bi-globe2"></i></div>'
        '<div class="lc-title">UNICEF Global Child Hazard Database</div>'
        '<div class="lc-sub">Staff access only · @unicef.org required</div>'
        '<form method="POST" action="/login">'
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
#         return _fredirect("/login")


@app.server.route("/login", methods=["GET", "POST"])
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
                return _fredirect("/")
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
    dcc.Store(id="store-embargo",       storage_type="session", data=False),
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

    # ── Embargo gate ──
    html.Div(id="embargo-gate", children=[
        html.Div(className="embargo-card", children=[
            html.Div("UNICEF CCRR — Data Access", className="embargo-tag"),
            html.Div("Official Data Release Policy", className="embargo-title"),
            html.Div(className="embargo-body", children=[
                "Results are ", html.Strong("not final"),
                " and currently under review. External sharing is under ",
                html.Strong("embargo"),
                " until the Children Climate Risk Report (CCRR) global release.",
            ]),
            html.Button("I Understand and Accept",
                        id="embargo-btn", className="embargo-btn", n_clicks=0),
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
            tab_hazard_layers(),
            tab_exposure(),
            tab_mh(),
            tab_analysis(),
            tab_ai(),
        ]),
        map_component(),
    ]),
])


# ===========================================================================
# Callbacks
# ===========================================================================

# ── Embargo ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("store-embargo", "data"),
    Output("embargo-gate",  "style"),
    Input("embargo-btn",    "n_clicks"),
    State("store-embargo",  "data"),
    prevent_initial_call=True,
)
def accept_embargo(n, _accepted):
    if n:
        return True, {"display": "none"}
    return no_update, no_update


@app.callback(
    Output("embargo-gate", "style", allow_duplicate=True),
    Input("store-embargo", "data"),
    prevent_initial_call=True,
)
def restore_embargo_state(accepted):
    return {"display": "none"} if accepted else no_update


# ── Tab switching ─────────────────────────────────────────────────────────────

@app.callback(
    Output("store-tab",         "data"),
    Output("btn-hazard",        "className"),
    Output("btn-exposure",      "className"),
    Output("btn-mh",            "className"),
    Output("btn-analysis",      "className"),
    Output("btn-ai",            "className"),
    Output("tab-hazard",        "style"),
    Output("tab-exposure",      "style"),
    Output("tab-mh",            "style"),
    Output("tab-analysis",      "style"),
    Output("tab-ai",            "style"),
    Output("hazard-info-panel", "style", allow_duplicate=True),
    Input("btn-hazard",    "n_clicks"),
    Input("btn-exposure",  "n_clicks"),
    Input("btn-mh",        "n_clicks"),
    Input("btn-analysis",  "n_clicks"),
    Input("btn-ai",        "n_clicks"),
    State("store-tab",     "data"),
    prevent_initial_call=True,
)
def switch_tab(n1, n2, n3, n4, n5, current):
    tab = {"btn-hazard":"hazard","btn-exposure":"exposure",
           "btn-mh":"mh","btn-analysis":"analysis",
           "btn-ai":"ai"}.get(ctx.triggered_id, current)
    cls = lambda t: "nav-btn active" if tab == t else "nav-btn"
    vis = lambda t: {"display": "block"} if tab == t else {"display": "none"}
    info_panel = no_update if tab == "hazard" else {"display": "none"}
    return (
        tab,
        cls("hazard"), cls("exposure"), cls("mh"), cls("analysis"), cls("ai"),
        vis("hazard"), vis("exposure"), vis("mh"), vis("analysis"), vis("ai"),
        info_panel,
    )


# ── Hazard info popup ────────────────────────────────────────────────────────

@app.callback(
    Output("hazard-info-panel",       "style"),
    Output("hazard-info-panel-title", "children"),
    Output("hazard-info-panel-body",  "children"),
    Input({"type": "hazard-info-btn", "index": ALL}, "n_clicks"),
    Input("hazard-info-close",    "n_clicks"),
    Input("store-hazard-layer",   "data"),
    State("hazard-info-panel",    "style"),
    prevent_initial_call=True,
)
def toggle_hazard_info(info_clicks, _close, layer_name, panel_style):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update, no_update
    if triggered == "hazard-info-close":
        return {"display": "none"}, no_update, no_update
    if triggered == "store-hazard-layer":
        # Only follow the active layer when the panel is already open
        if not layer_name or not panel_style or panel_style.get("display") == "none":
            return no_update, no_update, no_update
        return no_update, _layer_label(layer_name), _hazard_info_body(HAZARD_INFO.get(layer_name, layer_name))
    if isinstance(triggered, dict) and triggered.get("type") == "hazard-info-btn":
        name  = triggered["index"]
        return {"display": "flex"}, _layer_label(name), _hazard_info_body(HAZARD_INFO.get(name, name))
    return no_update, no_update, no_update


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
    Output({"type": "topic-item", "index": ALL}, "className"),
    Output("store-exposure-topic", "data"),
    Input({"type": "topic-item", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_topic(all_clicks):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update
    selected = triggered["index"]
    classes = [
        "topic-item active" if inp["id"]["index"] == selected else "topic-item"
        for inp in ctx.inputs_list[0]
    ]
    return classes, selected


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


# ── Hazard legend ─────────────────────────────────────────────────────────────

@app.callback(
    Output("hazard-legend", "children"),
    Input("store-hazard-layer", "data"),
)
def update_hazard_legend(sel):
    if not sel:
        return None

    from config import HAZARD_VIS_PALETTES
    if sel == "Multi Hazard Count":
        pal = ["#ffffd4","#fed98e","#fe9929","#d95f0e","#993404"]
        n   = len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)
        sub = [html.Span("1 topic"), html.Span(f"{n} topics")]
    elif sel == "Multi Hazard Intensity":
        pal = ["#000004","#3b0f70","#8c2981","#de4968","#fe9f6d"]
        sub = [html.Span("Low (0)"), html.Span("High (10)")]
    else:
        hazard = HAZARD_MAP.get(sel)
        if not hazard:
            return None
        pal = HAZARD_VIS_PALETTES.get(sel, ["#ffffb2","#fecc5c","#fd8d3c","#f03b20","#bd0026"])
        sub = [html.Span("Low"), html.Span("High")]

    return html.Div(className="legend-wrap", children=[
        html.Div("Legend", className="legend-label"),
        html.Div(className="legend-bar",
                 style={"background": f"linear-gradient(to right,{','.join(pal)})"}),
        html.Div(className="legend-range", children=sub),
    ])


# ── Map data layers ───────────────────────────────────────────────────────────

@app.callback(
    Output("data-layers", "children"),
    Input("store-hazard-layer",   "data"),
    Input("store-exposure-topic", "data"),
    Input("mhc-select",           "value"),
    Input("mhi-select",           "value"),
    Input("store-tab",            "data"),
)
def update_data_layers(sel_layer, exp_topic, mhc, mhi, tab):
    layers = []

    if tab == "hazard" and sel_layer:
        if sel_layer == "Multi Hazard Count":
            url, _ = get_topic_count_tile_url()
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        elif sel_layer == "Multi Hazard Intensity":
            url, _ = get_pixel_score_tile_url()
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        else:
            url, _ = get_hazard_tile_url(sel_layer)
            if url:
                layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))

    elif tab == "exposure" and exp_topic:
        color = TOPIC_COLORS.get(exp_topic, "#ff0000")
        url, _ = get_topic_tile_url(exp_topic, color)
        layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))

    elif tab == "mh":
        if mhc:
            url, _ = get_topic_count_tile_url(int(mhc))
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75))
        if mhi:
            url, _ = get_pixel_score_percentile_tile_url(mhi)
            layers.append(dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.65))

    return layers


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
    Input("level-select",          "value"),
    prevent_initial_call=True,
)
def on_level_select(level):
    if not level:
        return no_update, no_update, no_update, no_update, None, {"display": "none"}
    if level == "adm0 (Country)":
        content = html.Div(className="ps", children=[
            html.Button("▶  Compute country exposure",
                        id="compute-btn", className="ps-btn", n_clicks=0),
        ])
    else:
        short = "province" if "adm1" in level else "district"
        content = html.Div(className="ps", children=[
            html.Div(f"Click a {short} on the map to compute exposure",
                     className="info-box"),
        ])
    return level, None, None, None, content, {"display": "block"}


# ── Map cursor ───────────────────────────────────────────────────────────────

@app.callback(
    Output("map-container", "className"),
    Input("store-level",    "data"),
)
def update_map_cursor(level):
    return "map-clickable" if level and level != "adm0 (Country)" else ""


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
    State("store-level",          "data"),
)
def update_selection_layer(ucode, level):
    if not ucode or not level:
        return []
    try:
        url = get_selected_feature_tile_url(level, ucode)
        return [dl.TileLayer(url=url, attribution=GEE_ATTR, opacity=0.75)]
    except Exception:
        return []


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
    if level == "adm0 (Country)" or tab != "analysis":
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


# ── adm0 compute button ───────────────────────────────────────────────────────

@app.callback(
    Output("store-clicked-ucode", "data", allow_duplicate=True),
    Output("store-clicked-name",  "data", allow_duplicate=True),
    Output("store-exposure",      "data", allow_duplicate=True),
    Input("compute-btn",          "n_clicks"),
    State("store-ucode",          "data"),
    State("store-country",        "data"),
    prevent_initial_call=True,
)
def on_compute_click(n, ucode, country):
    if not n or not ucode:
        return no_update, no_update, no_update
    return ucode, country, None


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

@app.callback(
    Output("store-exposure",      "data", allow_duplicate=True),
    Output("results-panel",       "children"),
    Input("store-clicked-ucode",  "data"),
    Input("store-clicked-name",   "data"),
    Input("mhc-select",           "value"),
    Input("mhi-select",           "value"),
    State("store-level",          "data"),
    State("store-exposure",       "data"),
    prevent_initial_call=True,
)
def run_exposure(ucode, name, mhc, mhi, level, existing):
    if not ucode or not level:
        return no_update, None

    triggered_ids = {t["prop_id"].split(".")[0] for t in ctx.triggered}
    filter_changed = "mhc-select" in triggered_ids or "mhi-select" in triggered_ids

    if existing and not filter_changed:
        return no_update, render_results(_apply_force_null(existing, ucode), name, mhc, mhi)

    if existing and filter_changed:
        data = _apply_force_null(existing, ucode)
        # Can only re-render if the needed filter values are already stored
        mhc_ready = (not mhc) or (f"count_filter_{mhc}" in data)
        mhi_ready = (not mhi) or (f"intensity_filter_{mhi}" in data)
        if mhc_ready and mhi_ready:
            return no_update, render_results(data, name, mhc, mhi)
        # Values not precomputed (old-format data or sub-national MHI) — recompute

    result = compute_exposure(
        feature_ucode=ucode, admin_level=level,
        mhc_value=mhc if mhc else None,
        mhi_percentile=mhi if mhi else None,
    )
    result = _apply_force_null(result, ucode)
    return result, render_results(result, name, mhc, mhi)


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


def render_results(result, region_name, mhc_val, mhi_val):
    if not result:
        return html.Div("No data available.", className="ps-caption",
                        style={"padding": "14px 16px"})

    total = int(round(result.get("total_population",       0) or 0))
    male  = int(round(result.get("total_population_male",  0) or 0))
    fema  = int(round(result.get("total_population_female",0) or 0))
    pct_f = f"{fema/total*100:.0f}%" if total else "—"

    # Collect + sort topic data
    no_data_topics = []
    topic_data     = []
    for topic in HAZARD_TOPICS:
        if topic in EXPOSURE_ONLY_TOPICS:
            continue
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
        color = TOPIC_COLORS.get(td["topic"], "#888")
        pct   = td["pct"]
        count = td["count"]
        return [
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch", style={"background": color}),
                    td["topic"].upper(),
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
            rows.append(html.Div(className="info-row", children=[
                html.Span(f"└ {_hazard_label(h_name)}", className="info-lbl",
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

    export_data = {
        "region": region_name, "total_children": total,
        "male": male, "female": fema,
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
    prevent_initial_call=True,
)
def compute_custom_exposure(n_clicks, geojson, name_field):
    if not geojson or not name_field:
        return no_update

    try:
        features = compute_exposure_custom(geojson)
    except Exception as e:
        return html.Div(f"GEE error: {e}",
                        style={"color": "var(--red)", "fontSize": "0.75rem",
                               "padding": "12px 16px"})

    hazard_cols = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
    extra_cols  = ["total_population", "total_population_male", "total_population_female"]
    all_cols    = extra_cols + hazard_cols

    rows = []
    for feat in features:
        props = feat.get("properties") or {}
        row   = {name_field: props.get(name_field, "—")}
        for col in all_cols:
            row[col] = props.get(col) or 0
        rows.append(row)

    if not rows:
        return html.Div("No results returned.",
                        style={"padding": "12px 16px", "fontSize": "0.75rem"})

    import io, csv as _csv, urllib.parse
    buf = io.StringIO()
    fieldnames = [name_field] + all_cols
    writer = _csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    csv_href = "data:text/csv;charset=utf-8," + urllib.parse.quote(buf.getvalue())

    n_feat = len(rows)
    return html.Div(className="ps", children=[
        html.Div([
            html.I(className="bi bi-check-circle-fill",
                   style={"color": "var(--cyan)", "marginRight": "8px"}),
            html.Strong(f"Hazard exposure for {n_feat} feature{'s' if n_feat != 1 else ''} ready."),
        ], style={"fontSize": "0.8rem", "color": "var(--hi)", "marginBottom": "10px"}),
        html.A(
            f"⬇  Download CSV ({n_feat} features × {len(hazard_cols)} hazards)",
            href=csv_href,
            download="custom_hazard_exposure.csv",
            className="ps-btn",
            style={"display": "block", "textAlign": "center", "textDecoration": "none",
                   "padding": "10px 16px"},
        ),
    ])


# ── GEE Asset load + compute ──────────────────────────────────────────────────

def _exposure_results_div(features, name_field, filename):
    hazard_cols = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
    extra_cols  = ["total_population", "total_population_male", "total_population_female"]
    all_cols    = extra_cols + hazard_cols
    rows = []
    for feat in features:
        props = feat.get("properties") or {}
        row   = {name_field: props.get(name_field, "—")}
        for col in all_cols:
            row[col] = props.get(col) or 0
        rows.append(row)
    if not rows:
        return html.Div("No results returned.",
                        style={"padding": "12px 16px", "fontSize": "0.75rem"})
    import io, csv as _csv, urllib.parse
    buf = io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=[name_field] + all_cols)
    writer.writeheader()
    writer.writerows(rows)
    csv_href = "data:text/csv;charset=utf-8," + urllib.parse.quote(buf.getvalue())
    n_feat = len(rows)
    return html.Div(className="ps", children=[
        html.Div([
            html.I(className="bi bi-check-circle-fill",
                   style={"color": "var(--cyan)", "marginRight": "8px"}),
            html.Strong(f"Hazard exposure for {n_feat} feature{'s' if n_feat != 1 else ''} ready."),
        ], style={"fontSize": "0.8rem", "color": "var(--hi)", "marginBottom": "10px"}),
        html.A(
            f"⬇  Download CSV ({n_feat} features × {len(hazard_cols)} hazards)",
            href=csv_href, download=filename, className="ps-btn",
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
    prevent_initial_call=True,
)
def compute_gee_asset_exposure(n_clicks, asset_info, name_field):
    if not asset_info or not name_field:
        return no_update
    try:
        features = compute_exposure_asset(asset_info["asset_id"])
    except Exception as e:
        return html.Div(f"GEE error: {e}",
                        style={"color": "var(--red)", "fontSize": "0.75rem",
                               "padding": "12px 16px"})
    fname = asset_info["asset_id"].split("/")[-1] + "_hazard_exposure.csv"
    return _exposure_results_div(features, name_field, fname)


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
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8502)
