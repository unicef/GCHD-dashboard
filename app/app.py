# =============================================================================
# app.py — UNICEF CCRI Global Child Hazard Database (Dash)
# =============================================================================

import os
import re
import json as _json

import dash
from dash import dcc, html, Input, Output, State, ctx, no_update, ALL, MATCH
import dash_leaflet as dl
import plotly.graph_objects as go

import json as _json_mod

from config import (
    HAZARD_TOPICS, TOPIC_COLORS, ADMIN_DATA,
    HAZARDS, HAZARD_MAP, SUB_TOPIC_DETAIL,
    MHC_OPTIONS, MHI_OPTIONS, HAZARD_INFO, EXPOSURE_ONLY_TOPICS, MHC_EXCLUDED_TOPICS,
    FORCE_NULL_RULES, EXCLUDE_ISO3,
    INFRA_COUNTRIES, INFRA_ASSETS, infra_layer_style,
    is_binary_hazard,
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
    get_clipped_pop_tile_url, INFRA_POP_VIS,
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
            nav_btn("bi bi-buildings",     "Infra",    "btn-infra"),
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
        html.Div(className="ph", children=[
            html.Div("Children's Exposure", className="ph-title"),
            html.Div("Population exposed to each hazard topic", className="ph-sub"),
        ]),
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


def _infra_layer_options(country=None):
    """Layer dropdown options for the selected country — only its uploaded,
    source-qualified layers (e.g. 'Schools (Giga)'). Empty until a country with
    assets is picked."""
    return [{"label": name, "value": name}
            for name in INFRA_ASSETS.get(country, {})]


def tab_infrastructure():
    return html.Div(id="tab-infrastructure", style={"display": "none"}, children=[
        html.Div(className="ph", children=[
            html.Div("Infrastructure Analysis", className="ph-title"),
            html.Div("Correlate facilities with hazards & child population",
                     className="ph-sub"),
        ]),

        # 1. Region — own selector that writes to the shared region stores.
        # Analyses are adm2-only, so selecting a country hard-sets adm2 and
        # enables map district selection immediately (no level dropdown).
        html.Div(className="ps", children=[
            html.Div("1. Select country / territory", className="ps-label"),
            dcc.Dropdown(
                id="infra-country-select", className="ps-select",
                options=[{"label": c, "value": c} for c in INFRA_COUNTRIES],
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
        html.Div(id="infra-map-legend", className="map-legend",
                 style={"display": "none"}),
        # Basemap toggle (map ↔ satellite) — bottom-left floating control.
        html.Button(
            [html.I(className="bi bi-globe-americas"), html.Span("Satellite")],
            id="basemap-toggle", className="basemap-toggle", n_clicks=0,
            title="Toggle satellite basemap",
        ),
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
                # Satellite basemap (Esri World Imagery) — hidden until toggled.
                dl.TileLayer(id="sat-basemap", url=ESRI_SAT,
                             attribution=ESRI_ATTR, maxZoom=19, opacity=0),
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
    # Shared custom hazard thresholds {hazard_name: value} (non-default only).
    # Written by the Exposure-tab editor; read by the exposure visualization.
    dcc.Store(id="store-thresholds",    data={}),

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
            tab_infrastructure(),
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
    Output("btn-infra",         "className"),
    Output("btn-ai",            "className"),
    Output("tab-hazard",        "style"),
    Output("tab-exposure",      "style"),
    Output("tab-mh",            "style"),
    Output("tab-analysis",      "style"),
    Output("tab-infrastructure","style"),
    Output("tab-ai",            "style"),
    Output("hazard-info-panel", "style", allow_duplicate=True),
    Input("btn-hazard",    "n_clicks"),
    Input("btn-exposure",  "n_clicks"),
    Input("btn-mh",        "n_clicks"),
    Input("btn-analysis",  "n_clicks"),
    Input("btn-infra",     "n_clicks"),
    Input("btn-ai",        "n_clicks"),
    State("store-tab",     "data"),
    prevent_initial_call=True,
)
def switch_tab(n1, n2, n3, n4, n5, n6, current):
    tab = {"btn-hazard":"hazard","btn-exposure":"exposure",
           "btn-mh":"mh","btn-analysis":"analysis",
           "btn-infra":"infrastructure","btn-ai":"ai"}.get(ctx.triggered_id, current)
    cls = lambda t: "nav-btn active" if tab == t else "nav-btn"
    vis = lambda t: {"display": "block"} if tab == t else {"display": "none"}
    info_panel = no_update if tab == "hazard" else {"display": "none"}
    return (
        tab,
        cls("hazard"), cls("exposure"), cls("mh"), cls("analysis"),
        cls("infrastructure"), cls("ai"),
        vis("hazard"), vis("exposure"), vis("mh"), vis("analysis"),
        vis("infrastructure"), vis("ai"),
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
    Input("store-thresholds",     "data"),
)
def update_data_layers(sel_layer, exp_topic, mhc, mhi, tab, thresholds):
    layers = []

    # Infrastructure layers live in dedicated top-level components
    # (infra-pop-tile / infra-points-tile / etc.), not in this group.
    if tab == "infrastructure":
        return layers

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
        # Honor the exposure editor's custom thresholds (falls back to the cached
        # default tile when this topic has no override).
        url, _ = get_topic_tile_url_thr(exp_topic, color, thresholds or None)
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
    # On the infra tab the yellow highlight is drawn by the dedicated,
    # toggleable infra-selection-tile instead of this group.
    if tab == "infrastructure":
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
    if level == "adm0 (Country)" or tab not in ("analysis", "infrastructure"):
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
        return no_update, None
    # Resolve region by level: adm0 uses the country; sub-national uses the click.
    if level == "adm0 (Country)":
        ucode, name = country_ucode, country_name
    else:
        ucode, name = clicked_ucode, clicked_name
    if not ucode:
        return no_update, None

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
            return no_update, no_update
        data = _apply_force_null(existing, ucode)
        return no_update, render_results(data, name, mhc, mhi,
                                         data.get("_topics"), data.get("_overrides"))

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
    return result, render_results(result, name, mhc, mhi, sel_topics, overrides)


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


def _eff_threshold(h_name, overrides):
    """Effective threshold + unit label for a hazard, given the user overrides."""
    default = HAZARD_MAP.get(h_name, {}).get("threshold")
    thr     = (overrides or {}).get(h_name, default)
    units   = (HAZARD_INFO.get(h_name) or {}).get("units", "")
    if thr is None:
        return ""
    thr_str = f"{thr:g}"
    return f"thr {thr_str}" + (f" {units}" if units else "")


def render_results(result, region_name, mhc_val, mhi_val, sel_topics=None, overrides=None):
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
                    f"└ {_hazard_label(h_name)}",
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
    The asset is resolved for the selected country from INFRA_ASSETS."""
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
        asset_id = INFRA_ASSETS.get(country, {}).get(layer_name)
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
        asset_id = INFRA_ASSETS.get(country, {}).get(layer_name)
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
                    html.Span([f"└ {_hazard_label(h)}",
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
    asset_id = asset["asset_id"]
    topics_sel = topics or None
    overrides, _ = _threshold_overrides(_infra_editor_topics(topics), thr_ids, thr_values)

    color = "e67e22"
    for layers in INFRA_ASSETS.values():
        for layer_name, layer_asset in layers.items():
            if layer_asset == asset_id:
                color = infra_layer_style(layer_name)["color"].lstrip("#")
                break
        else:
            continue
        break

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

    panel = _infra_panel("Facility exposure", cards, items, None, caption=caption)

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


# ── Map legend overlay (top-left) ─────────────────────────────────────────────

def _legend_toggle(layer, swatch, label):
    """A legend row with a leading eye toggle (clientside-controlled)."""
    return html.Div(className="map-legend-item", children=[
        html.I(className="bi bi-eye-fill map-legend-eye",
               id={"type": "infra-legend-eye", "index": layer}, n_clicks=0),
        swatch,
        html.Span(label, className="map-legend-item-label"),
    ])


@app.callback(
    Output("infra-map-legend", "children"),
    Output("infra-map-legend", "style"),
    Input("store-infra-viz",   "data"),
    Input("store-tab",         "data"),
)
def infra_update_legend(viz, tab):
    if tab != "infrastructure" or not viz:
        return None, {"display": "none"}

    pal   = INFRA_POP_VIS["palette"]
    pt_color = "#" + (viz.get("color") or "e67e22")
    grad  = ", ".join(pal)
    rows = [
        html.Div("Legend", className="map-legend-title"),
        # Population: stacked block — title line, full-width bar, min/max line.
        html.Div(className="map-legend-pop", children=[
            html.Div(className="map-legend-pop-head", children=[
                html.I(className="bi bi-eye-fill map-legend-eye",
                       id={"type": "infra-legend-eye", "index": "pop"}, n_clicks=0),
                html.Span("Children (per 100 m)", className="map-legend-item-label"),
            ]),
            html.Div(className="legend-bar",
                     style={"background": f"linear-gradient(to right,{grad})",
                            "width": "100%"}),
            html.Div(className="legend-range",
                     children=[html.Span(str(INFRA_POP_VIS["min"])),
                               html.Span(f"{INFRA_POP_VIS['max']}+")]),
        ]),
        # Facility points
        _legend_toggle(
            "points",
            html.Span(className="map-legend-dot",
                      style={"background": pt_color, "border": "1.5px solid #fff"}),
            "Facilities",
        ),
        # Facility catchments (Voronoi cells)
        _legend_toggle(
            "catchments",
            html.Span(className="map-legend-line",
                      style={"borderTop": "1.5px solid #1CABE2"}),
            "Facility catchments",
        ),
        # Selected subregion (yellow adm2 highlight)
        _legend_toggle(
            "selection",
            html.Span(className="map-legend-line",
                      style={"borderTop": "2px solid #f1c40f"}),
            "Selected subregion",
        ),
    ]
    return rows, {"display": "block"}


# ── Clientside layer toggles (eye icons → layer opacity + icon state) ──────────
app.clientside_callback(
    """
    function(nPop, nPoints, nCatch, nSelection) {
        // Even clicks = visible, odd = hidden.
        var vis = function(n){ return (n % 2 === 0); };
        var op  = function(n, base){ return vis(n) ? base : 0; };
        var icon = function(n){
            return vis(n) ? "bi bi-eye-fill map-legend-eye"
                          : "bi bi-eye-slash map-legend-eye map-legend-eye-off";
        };
        // Voronoi cells GeoJSON: toggle via style opacity.
        var cellStyle = {color:"#1CABE2", weight:1, fillOpacity:0.0,
                         opacity: vis(nCatch) ? 0.7 : 0.0};
        return [
            op(nPop, 0.8), op(nPoints, 0.9), op(nSelection, 0.75),
            {style: cellStyle},
            icon(nPop), icon(nPoints), icon(nCatch), icon(nSelection)
        ];
    }
    """,
    Output("infra-pop-tile",        "opacity"),
    Output("infra-points-tile",     "opacity"),
    Output("infra-selection-tile",  "opacity"),
    Output("infra-voronoi-geojson", "options"),
    Output({"type": "infra-legend-eye", "index": "pop"},        "className"),
    Output({"type": "infra-legend-eye", "index": "points"},     "className"),
    Output({"type": "infra-legend-eye", "index": "catchments"}, "className"),
    Output({"type": "infra-legend-eye", "index": "selection"},  "className"),
    Input({"type": "infra-legend-eye", "index": "pop"},        "n_clicks"),
    Input({"type": "infra-legend-eye", "index": "points"},     "n_clicks"),
    Input({"type": "infra-legend-eye", "index": "catchments"}, "n_clicks"),
    Input({"type": "infra-legend-eye", "index": "selection"},  "n_clicks"),
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


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8502)
