"""Map legend: per-section specs, data layers and eye toggles."""
import dash_leaflet as dl
from config.hazards import (
    gradient_spec, HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS, HAZARD_VIS_PALETTES,
    is_pop_layer, MHC_EXCLUDED_TOPICS, pop_class_of, POP_LAYER_MAP, POP_VIS,
    swatch_spec, TOPIC_COLORS, vis_gradient_spec,
)
from dash import ALL, ctx, html, Input, Output, State
from config.elnino import EXPLORE_MAP, SIGNAL_MAP
from core.gee import (
    get_hazard_tile_url, get_pixel_score_percentile_tile_url,
    get_pixel_score_tile_url, get_population_tile_url,
    get_topic_count_tile_url, get_topic_tile_url_thr, INFRA_POP_VIS,
)

from dash_app import app
from bootstrap import ANALYSIS_TOPIC_SLOTS, GEE_ATTR
from ui.components import _fmt, _layer_label
from ui.registry import FORECAST_TABS
from sections.elnino import SIGNAL_LAYER
from sections.ew_shared import LIVE_HAZARD_COLOR


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
    Voronoi catchments, hazard areas, selected subregion) — all toggleable.

    Hazard rows start hidden and their tiles are fetched on first reveal, the
    same as the Analysis tab: a district already carries a population raster,
    points and catchments, so showing every hazard on top by default would
    bury them.
    """
    if not viz:
        return []
    pt_color = "#" + (viz.get("color") or "e67e22")
    specs = [
        vis_gradient_spec("Children (per 100 m)", INFRA_POP_VIS,
                          layer_id="infra-pop", unit="+", toggleable=True),
        swatch_spec("Facilities", pt_color,
                    layer_id="infra-points", toggleable=True),
        swatch_spec("Facility catchments", "#1CABE2", shape="line",
                    layer_id="infra-catchments", toggleable=True),
    ]
    for i, topic in enumerate(viz.get("topics") or []):
        if i >= ANALYSIS_TOPIC_SLOTS:
            break
        specs.append(swatch_spec(
            f"{topic} — hazard area", TOPIC_COLORS.get(topic, LIVE_HAZARD_COLOR),
            layer_id=f"infra-topic-{i}", toggleable=True,
            visible=bool(viz.get("live"))))
    specs.append(swatch_spec("Selected subregion", "#f1c40f", shape="line",
                             layer_id="infra-selection", toggleable=True))
    return specs


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


def _elnino_outlook_specs(layer_name, vis, visible=True):
    """Legend row(s) for the outlook layer — a gradient, or one swatch per class.

    A classified layer gets a swatch per class rather than a colour ramp: the
    values are categories, and a gradient would invite reading a cell as
    "between dry and wet" when the classes are exclusive.

    Only the first row carries the layer_id, so the eye toggle appears once and
    controls the whole layer rather than offering three switches for one tile.
    """
    cfg = EXPLORE_MAP.get(layer_name) or {}
    label = cfg.get("label", "Outlook")
    units = cfg.get("units")

    if cfg.get("classified") and cfg.get("classes"):
        rows = []
        for i, c in enumerate(cfg["classes"]):
            rows.append(swatch_spec(
                f"{label} — {c['label']}" if i == 0 else c["label"],
                c["color"],
                layer_id=("elnino-layer" if i == 0 else None),
                toggleable=(i == 0), visible=visible))
        return rows

    if not vis:
        return []
    return [vis_gradient_spec(label + (f" ({units})" if units else ""), vis,
                              layer_id="elnino-layer", toggleable=True,
                              visible=visible)]


def _elnino_legend_specs(viz, preview, extra=None):
    """Legend for the El Niño tab.

    Two states, and the preview one matters: this tab has a default period and
    layer selected the moment it opens, so a global map is on screen before any
    Compute. Without a row for it the legend would be empty while the map was
    clearly showing something — the one case where the overlay and the map
    disagree.

    After a Compute there are three rows, deliberately in draw order: the
    outlook layer, total children (hidden by default), and the exposed subset.
    """
    # Computed result — the full three-layer stack.
    if viz:
        sig = SIGNAL_MAP.get(viz.get("signal")) or {}
        vis = viz.get("layer_vis")
        # The SIGNAL layer, not viz["layer"]: the dialog's choice is an
        # additive extra with its own row below, and labelling the first row
        # after it claimed the signal had been replaced.
        sig_layer = ("cat" if sig.get("union_of") else SIGNAL_LAYER)
        specs = _elnino_outlook_specs(sig_layer, vis)
        exposed, total = viz.get("exposed"), viz.get("total")
        tot_lbl = "All children (per 1 km)"
        exp_lbl = f"Children in {sig.get('area_phrase', 'a signal area')}"
        if total:
            tot_lbl += f" — {_fmt(total)}"
        if exposed is not None and total:
            exp_lbl += (f" — {_fmt(exposed)}"
                        f" ({100.0 * exposed / total:.0f}%)")
        specs += _elnino_extra_specs(extra)
        specs += [
            vis_gradient_spec(tot_lbl, POP_VIS,
                              layer_id="elnino-pop", unit="+",
                              toggleable=True, visible=False),
            vis_gradient_spec(exp_lbl, POP_VIS,
                              layer_id="elnino-exposed", unit="+",
                              toggleable=True),
        ]
        return specs

    # Preview only — describe whatever is already on the map.
    if preview:
        return (_elnino_outlook_specs(preview.get("layer"),
                                      preview.get("vis"))
                + _elnino_extra_specs(extra))
    return []


def _elnino_extra_specs(extra):
    """Legend row for the additive map-dialog layer, when one is drawn.

    Appears BELOW the signal row and above the population rows, matching the
    draw order on the map. Absent entirely when no extra layer is enabled, so
    the legend never describes something that is not there.
    """
    if not extra or not extra.get("layer"):
        return []
    cfg = EXPLORE_MAP.get(extra["layer"]) or {}
    label = f"{cfg.get('label', extra['layer'])} (extra layer)"
    if cfg.get("classified") and cfg.get("classes"):
        return [swatch_spec(f"{label} — {c['label']}" if i == 0
                            else c["label"], c["color"],
                            layer_id=("elnino-extra" if i == 0 else None),
                            toggleable=(i == 0))
                for i, c in enumerate(cfg["classes"])]
    if not extra.get("vis"):
        return []
    units = cfg.get("units")
    return [vis_gradient_spec(label + (f" ({units})" if units else ""),
                              extra["vis"], layer_id="elnino-extra",
                              toggleable=True)]


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
    Input("store-elnino-viz",     "data"),
    Input("store-elnino-preview", "data"),
    Input("store-elnino-extra",   "data"),
    Input("store-view",           "data"),
)
def update_data_layers(sel_layer, exp_topic, mhc, mhi, tab, thresholds,
                       infra_viz, analysis_viz, forecast_viz, forecast_preview,
                       elnino_viz, elnino_preview,
                      elnino_extra, view):
    """Build this tab's map tiles and the legend specs that describe them.

    Single source of truth for the legend: whichever tab is active writes its
    specs here, so the floating overlay always matches what's on the map.
    """
    layers, specs = [], []

    # Infrastructure layers live in dedicated top-level components
    # (infra-pop-tile / infra-points-tile / etc.), not in this group — but
    # their legend is still rendered by the shared overlay.
    if tab == "infrastructure":
        specs = _infra_legend_specs(infra_viz)
        if view == "ew-infra":
            # The live layer stays on the map; after Compute it steps back
            # behind the district's hazard area.
            specs += _preview_specs(forecast_preview, visible=not infra_viz)
        return layers, specs

    if tab == "analysis":
        return layers, _analysis_legend_specs(analysis_viz)

    # Dedicated top-level components (forecast-*-tile), shared by both tabs.
    if tab in FORECAST_TABS:
        # Entering starts clean; the stores still hold the tab being left.
        if ctx.triggered_id == "store-tab":
            return layers, []
        return layers, _forecast_legend_specs(forecast_viz, forecast_preview)

    # Dedicated top-level components (elnino-*-tile).
    if tab == "elnino":
        return layers, _elnino_legend_specs(elnino_viz, elnino_preview,
                                            elnino_extra)

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


# ── Unified map legend overlay (top-left) ─────────────────────────────────────
# Every tab writes a list of legend specs (plain dicts from config/hazards.py) into
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


# Hide the legend the instant the tab changes; the server re-shows it once the
# new tab's layers are known (two round trips otherwise).
app.clientside_callback(
    "function(tab) { return {display: 'none'}; }",
    Output("map-legend", "style", allow_duplicate=True),
    Input("store-tab",   "data"),
    prevent_initial_call=True,
)


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
    function(clicks, ids, topicOpacities, infraTopicOpacities) {
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
        var infraTopics = (infraTopicOpacities || []).map(function(_, i){
            var key = "infra-topic-" + i;
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
            on("infra-selection", 0.75), {style: cellStyle}, infraTopics,
            on("analysis-exposed", 0.85), on("analysis-selection", 0.75),
            topics,
            on("forecast-intensity", 0.75), on("forecast-exposed", 0.85),
            on("forecast-selection", 0.75),
            on("elnino-layer", 0.8), on("elnino-pop", 0.85),
            on("elnino-exposed", 0.85),
            icons
        ];
    }
    """,
    Output("infra-pop-tile",        "opacity"),
    Output("infra-points-tile",     "opacity"),
    Output("infra-selection-tile",  "opacity"),
    Output("infra-voronoi-geojson", "options"),
    Output({"type": "infra-topic-tile", "index": ALL}, "opacity"),
    Output("analysis-exposed-tile", "opacity"),
    Output("analysis-selection-tile", "opacity"),
    Output({"type": "analysis-topic-tile", "index": ALL}, "opacity"),
    Output("forecast-intensity-tile", "opacity"),
    Output("forecast-exposed-tile",   "opacity"),
    Output("forecast-selection-tile", "opacity"),
    Output("elnino-layer-tile",       "opacity"),
    Output("elnino-pop-tile",         "opacity"),
    Output("elnino-exposed-tile",     "opacity"),
    Output({"type": "legend-eye", "index": ALL}, "className"),
    Input({"type": "legend-eye", "index": ALL},  "n_clicks"),
    State({"type": "legend-eye", "index": ALL},  "id"),
    State({"type": "analysis-topic-tile", "index": ALL}, "opacity"),
    State({"type": "infra-topic-tile", "index": ALL}, "opacity"),
    prevent_initial_call=True,
)


def _preview_specs(preview, visible=True):
    if not (preview and preview.get("vis")):
        return []
    units = preview.get("units", "")
    label = preview.get("label", "Preview")
    return [vis_gradient_spec(
        f"{label} ({units})".strip() if units else label,
        preview["vis"], layer_id="forecast-intensity", unit=units,
        toggleable=True, visible=visible)]


def _forecast_legend_specs(viz, preview=None):
    """Legend rows for the Forecast tab.

    Before Compute there is only the previewed intensity layer, so the legend
    describes that alone — without it the preview is an unlabelled colour wash
    with no way to read a value. After Compute, `viz` takes over and adds the
    exposed-population and selected-region rows.
    """
    if not viz:
        return _preview_specs(preview) or None
    specs = []
    vis = viz.get("intensity_vis")
    if vis:
        specs.append(vis_gradient_spec(
            f"{viz.get('label','Forecast')} ({viz.get('units','')})".strip(),
            vis, layer_id="forecast-intensity", unit=viz.get("units", ""),
            toggleable=True, visible=False))
    # Forecast exposure runs on the 1 km population grid, so the legend must
    # not claim "per 100 m" — that is the Analysis tab's unit.
    pop_unit = f"per {viz.get('pop_res', 100):,} m"
    specs.append(gradient_spec(
        "Children exposed", POP_VIS["palette"], POP_VIS["min"], POP_VIS["max"],
        layer_id="forecast-exposed", unit=pop_unit, toggleable=True))
    specs.append(swatch_spec("Selected region", "#FFD700", shape="line",
                             layer_id="forecast-selection", toggleable=True))
    return specs
