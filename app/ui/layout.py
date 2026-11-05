"""The full page layout."""
from dash import dcc, html
from config.elnino import DOWNLOAD_DISCLAIMER_ACK, DOWNLOAD_DISCLAIMER_TITLE

from dash_app import app, _BASE_PATHNAME
from ui.components import panel_head, sidebar, view_header_children
from ui.map_view import map_component
from ui.registry import DEFAULT_NAV, nav_to_path, PHASES, VIEWS
from sections.ai import tab_ai
from sections.analysis import tab_analysis
from sections.early_warning import (
    ew_hazard_card, fc_controls, tab_forecast, tab_observed,
)
from sections.elnino import tab_elnino
from sections.infrastructure import tab_infrastructure
from sections.layers import tab_exposure, tab_hazard_layers, tab_mh


# ---------------------------------------------------------------------------
app.layout = html.Div(id="app-root", children=[
    # ── Stores ──
    dcc.Store(id="store-embargo-permanent", storage_type="local",   data=False),
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="store-tab",           data=DEFAULT_NAV["tab"]),
    # Rail button (VIEWS key) and phase; store-tab alone can't tell EW
    # Visualization from EW Pop analysis, which share the Observed/Forecast panes.
    dcc.Store(id="store-view",          data=DEFAULT_NAV["view"]),
    dcc.Store(id="store-phase",         data=DEFAULT_NAV["phase"]),
    # Last pane per view and last view per phase, so returning to a phase or
    # view lands where the user left it: {"views": {..}, "phases": {..}}.
    dcc.Store(id="store-last-nav",      data={"views": {}, "phases": {}}),
    dcc.Store(id="store-url-synced",    data=None),
    dcc.Store(id="store-nav-paths",     data={
        f"{p['key']}|{v['key']}|{sg['tab']}": _BASE_PATHNAME.rstrip("/") + nav_to_path(
            {"phase": p["key"], "view": v["key"], "tab": sg["tab"]})
        for p in PHASES if p["enabled"]
        for v in VIEWS if v["phase"] in (p["key"], None)
        for sg in v["segments"]}),
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
    # Active legend rows (list of specs from config/hazards.py) for the floating overlay.
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

    # ── Leave-section confirmation ─────────────────────────────────────────
    # Each section owns its map layers and result panel, and switching wipes
    # both. Results here cost a GEE reduction that can take tens of seconds, so
    # losing one to a stray click is expensive — hence a confirm step, but ONLY
    # when something is actually on the map (see elnino_leave_guard).
    html.Div(id="leave-gate", style={"display": "none"}, children=[
        html.Div(className="embargo-card leave-card", children=[
            html.Div("Leave this section?", className="embargo-title"),
            html.Div(id="leave-gate-body", className="embargo-body"),
            html.Div(className="leave-btns", children=[
                html.Button("Stay here", id="leave-cancel-btn",
                            className="leave-btn-ghost", n_clicks=0),
                html.Button("Leave and clear", id="leave-confirm-btn",
                            className="embargo-btn leave-btn-go", n_clicks=0),
            ]),
        ]),
    ]),
    # Two stores, deliberately not one. store-leave-dest only REMEMBERS where
    # the open dialog would go; store-pending-tab is what actually triggers the
    # switch. Merging them would mean writing the destination when the dialog
    # opens, which is exactly the moment the switch must NOT happen.
    dcc.Store(id="store-leave-dest",  data=None),
    dcc.Store(id="store-pending-tab", data=None),

    # ── El Niño download disclaimer (see elnino_dl_gate) ───────────────────
    html.Div(id="elnino-dl-gate", style={"display": "none"}, children=[
        html.Div(className="embargo-card leave-card en-dl-card", children=[
            html.Div(DOWNLOAD_DISCLAIMER_TITLE, className="embargo-title"),
            html.Div(id="elnino-dl-body", className="embargo-body"),
            dcc.Checklist(
                id="elnino-dl-ack",
                options=[{"label": " " + DOWNLOAD_DISCLAIMER_ACK,
                          "value": "yes"}],
                value=[], className="embargo-check en-dl-ack",
            ),
            html.Div(className="leave-btns", children=[
                html.Button("Cancel", id="elnino-dl-cancel",
                            className="leave-btn-ghost", n_clicks=0),
                html.Button("Download", id="elnino-dl-confirm",
                            className="embargo-btn leave-btn-go",
                            n_clicks=0, disabled=True),
            ]),
        ]),
    ]),
    # "xlsx" | "json"
    dcc.Store(id="store-elnino-dl-kind", data=None),
    dcc.Download(id="elnino-download"),

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
    html.Div(id="main-app", className=f"phase-{DEFAULT_NAV['phase']}", style={
        "display": "grid",
        "gridTemplateColumns": "77px 320px 1fr",
        "gridTemplateRows": "100vh",
        "width": "100vw",
        "height": "100vh",
        "overflow": "hidden",
    }, children=[
        sidebar(),
        html.Div(id="panel", children=[
            panel_head(),
            html.Div(view_header_children(DEFAULT_NAV["tab"]),
                     id="view-header", className="ph"),
            ew_hazard_card(),
            # Panes are display-toggled, so DOM order is not visually
            # significant.
            tab_hazard_layers(),
            tab_mh(),
            tab_exposure(),
            tab_analysis(),
            tab_infrastructure(),
            tab_observed(),
            tab_forecast(),
            html.Div(id="tab-ewpop", style={"display": "none"}),
            fc_controls(),
            tab_elnino(),
            tab_ai(),
        ]),
        map_component(),
    ]),
])
