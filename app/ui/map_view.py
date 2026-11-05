"""Map component, viewport, selection, click, loading and basemap."""
import dash_leaflet as dl
import time
from dash import html, Input, no_update, Output, State
from gee_core import (
    get_admin_boundary_tile_url, get_feature_at_point,
    get_selected_feature_tile_url,
)

from dash_app import app
from bootstrap import (
    ANALYSIS_TOPIC_SLOTS, CARTO_ATTR, CARTO_FALLBACK, ESRI_ATTR, ESRI_SAT,
    GEE_ATTR, GLOBAL_CENTER, GLOBAL_ZOOM, UN_ATTR, UN_CLEARMAP,
)
from ui.registry import FORECAST_TABS
from sections.elnino import _elnino_layer_panel


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
        # Which phase and hazard source the map is showing, so a forecast is
        # never mistaken for the historical catalogue (see update_mode_badge).
        html.Div(id="map-mode-badge", className="map-mode-badge"),
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
        _elnino_layer_panel(),
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
                "shown do not imply endorsement or acceptance by the UNICEF",
                className="map-disclaimer-text",
            ),
        ]),
        dl.Map(
            id="main-map",
            center=GLOBAL_CENTER, zoom=GLOBAL_ZOOM, zoomControl=False,
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
                # Per-topic hazard slots for the Infrastructure tab, same
                # pattern and slot count as Analysis below.
                *[dl.TileLayer(id={"type": "infra-topic-tile", "index": i},
                               url="", opacity=0)
                  for i in range(ANALYSIS_TOPIC_SLOTS)],
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
                # ── El Niño: its own pair rather than reusing the forecast
                # tiles, so switching tabs does not blank the other tab's map
                # state (both stacks stay addressable independently). ──
                # The SIGNAL layer, always driven by section 2's signal and
                # never replaced by a map-dialog choice: the thing the numbers
                # describe must stay on screen.
                dl.TileLayer(id="elnino-layer-tile", url="", opacity=0.8),
                # An optional EXTRA layer chosen in the map dialog, drawn over
                # the signal at lower opacity so both read at once. Its own
                # tile rather than a shared one, because overwriting the
                # signal's url is exactly the behaviour being removed.
                dl.TileLayer(id="elnino-extra-tile", url="", opacity=0),
                # Total child population sits UNDER the exposed layer so the
                # exposed subset reads as a highlight on the whole, not as a
                # separate map. It starts hidden to keep the default readable.
                dl.TileLayer(id="elnino-pop-tile", url="", opacity=0),
                dl.TileLayer(id="elnino-exposed-tile", url="", opacity=0.85),
            ],
            style={"height": "100vh", "width": "100%"},
        ),
    ])


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
                                                "elnino", *FORECAST_TABS):
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
