"""Phase/view/pane switching, leave guard, URL sync and resets."""
import dash
from config.hazards import EXPOSURE_ONLY_TOPICS, HAZARD_TOPICS
from dash import ALL, ctx, html, Input, no_update, Output, State

from dash_app import app
from i18n import N_, tr, trc
from bootstrap import GLOBAL_VIEWPORT
from ui.components import (
    _fmt_window, phase_btn_class, rail_btn_class, seg_btn_class,
    view_header_children,
)
from ui.registry import (
    default_nav, EW_HAZARD_VIEWS, FORECAST_TABS, nav_from_path, nav_label,
    PHASE_BY_KEY, PHASES, SEGMENTED_VIEWS, TABS, VIEW_BY_KEY, view_tabs, VIEWS,
)
from sections.ew_shared import _ew_source, _forecast_cfg


# ── Navigation: phase → view → pane ──────────────────────────────────────────

@app.callback(
    Output("store-tab",      "data"),
    Output("store-view",     "data"),
    Output("store-phase",    "data"),
    Output("store-last-nav", "data"),
    Output("main-app",       "className"),
    Output("ew-hazard-card", "className"),
    Output("ew-hazard-card", "style"),
    Output("view-header",    "children"),
    Output("tab-infrastructure", "className"),
    *[Output(f"nav-{v['key']}",    "className") for v in VIEWS],
    *[Output(f"phase-{p['key']}",  "className") for p in PHASES],
    *[Output(f"segbar-{v['key']}", "style")     for v in SEGMENTED_VIEWS],
    Output({"type": "seg-btn", "view": ALL, "tab": ALL}, "className"),
    *[Output(t["pane"], "style") for t in TABS],
    Output("hazard-info-panel", "style", allow_duplicate=True),
    Input("store-pending-tab", "data"),
    State("store-tab",         "data"),
    State("store-view",        "data"),
    State("store-phase",       "data"),
    State("store-last-nav",    "data"),
    prevent_initial_call=True,
)
def switch_tab(nav, cur_tab, cur_view, cur_phase, last):
    """Render one navigation state: phase switch, rail, segments and pane.

    Outputs are generated from PHASES / VIEWS / TABS, so their arity can never
    drift from the registry. leave_guard is the only writer of
    store-pending-tab, which is what stops the pane changing behind an open
    dialog. Unchanged stores return no_update: several callbacks clear map
    layers on store-tab / store-view, and Dash fires them on any write.
    """
    if not nav:
        raise dash.exceptions.PreventUpdate
    tab, view, phase = nav["tab"], nav["view"], nav["phase"]

    last = last or {}
    last = {
        "views":  {**last.get("views", {}), view: tab},
        "phases": {**last.get("phases", {}),
                   **({phase: view} if VIEW_BY_KEY[view]["phase"] else {})},
    }
    seg_out = next(o for o in ctx.outputs_list if isinstance(o, list))
    vis = lambda k: {"display": "block"} if tab == k else {"display": "none"}
    same = lambda new, old: no_update if new == old else new
    # The popover serves the Layers list and the Forecast dataset info
    # button, so it must survive a switch to either.
    info_panel = (no_update if tab in ("hazard", *FORECAST_TABS)
                  else {"display": "none"})
    return (
        same(tab, cur_tab), same(view, cur_view), same(phase, cur_phase),
        last,
        f"phase-{phase}",
        "ewh " + ("ewh-mode-vis" if view == "ew-vis" else "ewh-mode-analysis"),
        {"display": "block" if view in EW_HAZARD_VIEWS else "none"},
        view_header_children(tab),
        "infra-mode-ew" if view == "ew-infra" else "infra-mode-prep",
        *[rail_btn_class(v, nav) for v in VIEWS],
        *[phase_btn_class(p, nav) for p in PHASES],
        *[{"display": "flex" if v["key"] == view else "none"}
          for v in SEGMENTED_VIEWS],
        [seg_btn_class(o["id"]["view"], o["id"]["tab"], nav) for o in seg_out],
        *[vis(t["key"]) for t in TABS],
        info_panel,
    )


# ── Leaving a section with live results ──────────────────────────────────────
#
# Each section owns its map layers and results panel, and a switch wipes both.
# A computed result costs a GEE reduction of tens of seconds, so losing one to a
# stray rail click is expensive — but a confirmation on EVERY switch would be
# nagging. The dialog therefore appears only when the section being left has
# something on the map.
#
# What counts as "something on the map", per section:
_RESULT_STORES = {
    "hazard":         ("store-hazard-layer",   N_("a hazard layer")),
    "exposure":       ("store-exposure-topic", N_("an exposure layer")),
    "analysis":       ("store-analysis-viz",   N_("exposure results")),
    "infrastructure": ("store-infra-viz",      N_("infrastructure results")),
    "observed":       ("store-forecast-viz",   N_("observed-hazard results")),
    "forecast":       ("store-forecast-viz",   N_("forecast results")),
    "ewpop":          ("store-forecast-viz",   N_("exposure results")),
    "elnino":         ("store-elnino-viz",     N_("El Niño results")),
}


def _nav_dest(trig, cur, last, pathname, ew_tab=None):
    """Where a nav trigger leads, as {phase, view, tab}; None = nowhere new.

    A rail click keeps the current pane when the target view also has it,
    otherwise returns to the pane last used in that view. EW Visualization
    opens on the segment of the selected hazard (`ew_tab`) so its row shows.
    """
    views_last  = (last or {}).get("views", {})
    phases_last = (last or {}).get("phases", {})

    def resume(view_key, phase):
        tabs = view_tabs(view_key)
        tab = (ew_tab if view_key == "ew-vis" and ew_tab in tabs
               else cur["tab"] if cur["tab"] in tabs
               else views_last.get(view_key) if views_last.get(view_key) in tabs
               else tabs[0])
        return {"phase": VIEW_BY_KEY[view_key]["phase"] or phase,
                "view": view_key, "tab": tab}

    if trig in (None, "url"):
        return nav_from_path(app.strip_relative_path(pathname or "") or "")
    if trig == "fc-goto-analysis":
        return resume("ew-pop", "ew")
    if trig == "fc-goto-infra":
        return resume("ew-infra", "ew")
    if isinstance(trig, dict):
        view = VIEW_BY_KEY[trig["view"]]
        return {"phase": view["phase"] or cur["phase"],
                "view": view["key"], "tab": trig["tab"]}
    if trig.startswith("phase-"):
        phase = trig[len("phase-"):]
        if phase == cur["phase"] and VIEW_BY_KEY[cur["view"]]["phase"]:
            return None
        view_key = phases_last.get(phase)
        return resume(view_key, phase) if view_key else default_nav(phase)
    if trig.startswith("nav-"):
        return resume(trig[len("nav-"):], cur["phase"])
    return None


@app.callback(
    Output("store-pending-tab",  "data"),
    Output("store-leave-dest",   "data"),
    Output("leave-gate",         "style"),
    Output("leave-gate-body",    "children"),
    *[Input(f"nav-{v['key']}",   "n_clicks") for v in VIEWS],
    *[Input(f"phase-{p['key']}", "n_clicks") for p in PHASES if p["enabled"]],
    Input({"type": "seg-btn", "view": ALL, "tab": ALL}, "n_clicks"),
    Input("fc-goto-analysis",    "n_clicks"),
    Input("fc-goto-infra",       "n_clicks"),
    Input("url",                 "pathname"),
    Input("leave-confirm-btn",   "n_clicks"),
    Input("leave-cancel-btn",    "n_clicks"),
    State("store-tab",           "data"),
    State("store-view",          "data"),
    State("store-phase",         "data"),
    State("store-last-nav",      "data"),
    State("store-hazard-layer",  "data"),
    State("store-exposure-topic", "data"),
    State("store-analysis-viz",  "data"),
    State("store-infra-viz",     "data"),
    State("store-forecast-viz",  "data"),
    State("store-elnino-viz",    "data"),
    State("store-leave-dest",    "data"),
    State("forecast-dataset-select", "value"),
    State("store-forecast-asset",    "data"),
    # Not prevented: the initial call is what applies a deep-linked URL.
    prevent_initial_call=False,
)
def leave_guard(*args):
    """Decide every navigation: straight through, or via a confirmation.

    The single writer of store-pending-tab, which switch_tab listens to. That
    one-writer rule is what stops the pane changing behind an open dialog.
    """
    # Read States by name rather than position: the nav Inputs are generated
    # from the registry, so positional indices would shift with every view.
    st      = ctx.states
    cur     = {"tab":   st.get("store-tab.data"),
               "view":  st.get("store-view.data"),
               "phase": st.get("store-phase.data")}
    held    = st.get("store-leave-dest.data")
    live    = {
        "hazard":         st.get("store-hazard-layer.data"),
        "exposure":       st.get("store-exposure-topic.data"),
        "analysis":       st.get("store-analysis-viz.data"),
        "infrastructure": st.get("store-infra-viz.data"),
        "forecast":       st.get("store-forecast-viz.data"),
        "elnino":         st.get("store-elnino-viz.data"),
    }
    trig    = ctx.triggered_id
    hidden  = {"display": "none"}

    if trig == "leave-cancel-btn":
        return no_update, None, hidden, no_update
    if trig == "leave-confirm-btn":
        # Release the held destination into store-pending-tab, which is what
        # switch_tab listens to; clear_on_leave then wipes the old layers.
        if not held:
            raise dash.exceptions.PreventUpdate
        return held, None, hidden, no_update

    cfg = _forecast_cfg(st.get("forecast-dataset-select.value"),
                        st.get("store-forecast-asset.data"))
    ew_tab = (None if not cfg else "forecast"
              if cfg["is_custom"] or cfg.get("kind") == "forecast" else "observed")
    dest = _nav_dest(trig, cur, st.get("store-last-nav.data"),
                     ctx.inputs.get("url.pathname"), ew_tab)
    if not dest or dest == cur:
        raise dash.exceptions.PreventUpdate

    entry = _RESULT_STORES.get(cur["tab"])
    key = "forecast" if cur["tab"] in FORECAST_TABS else cur["tab"]
    moving = dest["tab"] != cur["tab"] or dest["view"] != cur["view"]
    if not moving or not entry or not live.get(key):
        return dest, None, hidden, no_update   # nothing to lose

    # Hold the destination WITHOUT writing store-pending-tab: writing it here
    # would switch the pane behind the dialog, which is the whole thing the
    # confirmation exists to prevent.
    _, what = entry
    body = trc("This section is showing {what}. Moving to {dest} clears the "
               "map layers and the results panel, and the calculation would "
               "have to be run again.",
               what=html.Strong(tr(what)), dest=nav_label(dest))
    return no_update, dest, {"display": "flex"}, body


# The URL follows navigation via history.pushState, which dcc.Location does
# not observe: writing url.pathname from switch_tab would close a callback
# cycle with leave_guard. Back/Forward still fire popstate into leave_guard.
# A cancelled dialog re-pushes the section the user stayed in.
app.clientside_callback(
    """
    function(tab, view, phase, _cancel, paths) {
        const path = paths[phase + "|" + view + "|" + tab];
        if (path && window.location.pathname !== path) {
            window.history.pushState({}, "", path);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("store-url-synced", "data"),
    Input("store-tab",        "data"),
    Input("store-view",       "data"),
    Input("store-phase",      "data"),
    Input("leave-cancel-btn", "n_clicks"),
    State("store-nav-paths",  "data"),
    prevent_initial_call=True,
)


@app.callback(
    Output("map-mode-badge",          "children"),
    Output("map-mode-badge",          "style"),
    Input("store-phase",              "data"),
    Input("store-view",               "data"),
    Input("store-tab",                "data"),
    Input("forecast-dataset-select",  "value"),
    Input("store-forecast-asset",     "data"),
    Input("forecast-dates",           "start_date"),
    Input("forecast-dates",           "end_date"),
)
def update_mode_badge(phase, view, tab, dataset, asset, start, end):
    """Phase + hazard source on the map. Text, not only the accent colour, so
    Observed vs Forecast vs the historical catalogue is never ambiguous."""
    if tab == "ai" or phase not in PHASE_BY_KEY:
        return None, {"display": "none"}
    p = PHASE_BY_KEY[phase]
    cfg = _forecast_cfg(dataset, asset) if view in EW_HAZARD_VIEWS else None
    source = (tr("Seasonal outlook") if tab == "elnino" else
              tr(_ew_source(cfg)) if cfg else tr(p["badge"]))
    parts = [html.Span(tr(p["label"]), className="mmb-phase"),
             html.Span(source, className="mmb-source")]
    if cfg:
        parts.append(html.Span(tr(cfg["label"]), className="mmb-detail"))
        window = _fmt_window(start, end)
        if window:
            parts.append(html.Span(window, className="mmb-detail"))
    return parts, {"display": "flex"}


_DEFAULT_ANALYSIS_TOPICS = [t for t in HAZARD_TOPICS
                            if t not in EXPOSURE_ONLY_TOPICS]


# Dash re-runs every listener on each write, even of an unchanged value, so a
# reset that rewrites already-empty stores cascades through the whole app.
# Both reset callbacks below write only what actually changes.
def _only_changed(desired, current):
    return [no_update if d is no_update or d == c else d
            for d, c in zip(desired, current)]


_RESET_FORMS = [
    # Prep Pop analysis
    ("country-select",          "value"),
    ("level-select",            "value"),
    ("analysis-topic-select",   "value"),
    # Infra analysis (Prep and EW share the pane)
    ("infra-country-select",    "value"),
    ("infra-layer-select",      "value"),
    ("infra-asset-input",       "value"),
    ("infra-asset-status",      "children"),
    ("infra-topic-select",      "value"),
    ("store-infra-asset",       "data"),
    ("infra-results-panel",     "children"),
    ("infra-download-wrap",     "children"),
    ("store-infra-viz",         "data"),
    ("store-infra-pending",     "data"),
    ("infra-pop-tile",          "url"),
    ("infra-points-tile",       "url"),
    ("infra-selection-tile",    "url"),
    ("infra-voronoi-geojson",   "data"),
    # EW Pop analysis
    ("forecast-country-select", "value"),
    ("forecast-level-select",   "value"),
    ("store-forecast-viz",      "data"),
    ("store-forecast-result",   "data"),
    ("forecast-results-panel",  "children"),
    ("forecast-exposed-tile",   "url"),
    ("forecast-selection-tile", "url"),
    # El Niño
    ("elnino-country",          "value"),
    # Live preview: EW views only (forecast_preview redraws it there)
    ("forecast-intensity-tile", "url"),
    ("store-forecast-preview",  "data"),
    # Shared region
    ("store-country",           "data"),
    ("store-ucode",             "data"),
    ("store-bounds",            "data"),
    ("store-clicked-ucode",     "data"),
    ("store-clicked-name",      "data"),
    ("main-map",                "viewport"),
]


@app.callback(
    *[Output(i, p, allow_duplicate=True) for i, p in _RESET_FORMS],
    Input("store-view", "data"),
    *[State(i, p) for i, p in _RESET_FORMS],
    prevent_initial_call=True,
)
def reset_forms_on_view(view, *current):
    """Every view change starts each analysis form from scratch: country,
    level, facility layer, topics, results and their map layers. Rebuilding a
    half-kept form on re-entry is what left dropdowns out of step with the
    map. Only the EW Hazard card survives, within Early Warning
    (reset_hazard_card_on_phase)."""
    preview = ((no_update, no_update) if view in EW_HAZARD_VIEWS
               else ("", None))
    desired = (
        None, None, list(_DEFAULT_ANALYSIS_TOPICS),
        None, None, "", "", [], None, None, None, None, None,
        "", "", "", None,
        None, None, None, None, None, "", "",
        None,
        *preview,
        None, None, None, None, None, GLOBAL_VIEWPORT,
    )
    return _only_changed(desired, current)


@app.callback(
    Output("forecast-dataset-select",  "value",    allow_duplicate=True),
    Output("store-forecast-asset",     "data",     allow_duplicate=True),
    Output("forecast-asset-input",     "value",    allow_duplicate=True),
    Output("forecast-asset-status",    "children", allow_duplicate=True),
    Output("forecast-asset-band-wrap", "style",    allow_duplicate=True),
    Input("store-phase",               "data"),
    prevent_initial_call=True,
)
def reset_hazard_card_on_phase(phase):
    """The EW hazard carries across EW tasks only; leaving the phase drops it."""
    if phase == "ew":
        raise dash.exceptions.PreventUpdate
    return None, None, "", "", {"display": "none"}


_CLEAR_ON_LEAVE = [
    ("elnino-layer-tile",       "url"),
    ("elnino-pop-tile",         "url"),
    ("elnino-exposed-tile",     "url"),
    ("forecast-intensity-tile", "url"),
    ("forecast-exposed-tile",   "url"),
    ("infra-pop-tile",          "url"),
    ("infra-selection-tile",    "url"),
    ("analysis-exposed-tile",   "url"),
    ("store-elnino-viz",        "data"),
    ("store-elnino-preview",    "data"),
    ("store-forecast-viz",      "data"),
    ("store-forecast-preview",  "data"),
    ("store-infra-viz",         "data"),
    ("store-analysis-viz",      "data"),
    # Region state. Clearing these removes the admin boundary and the yellow
    # selection highlight, which are both derived from them rather than being
    # set directly — see update_map_view / update_selection_layer.
    ("store-ucode",             "data"),
    ("store-bounds",            "data"),
    ("store-country",           "data"),
    ("store-clicked-ucode",     "data"),
    ("store-clicked-name",      "data"),
    ("infra-selection-tile",    "url"),
    ("analysis-selection-tile", "url"),
    ("forecast-selection-tile", "url"),
    ("main-map",                "viewport"),
    # The per-section country pickers, so a cleared region does not leave a
    # country name sitting in a dropdown with no boundary drawn for it.
    ("elnino-country",          "value"),
    ("forecast-country-select", "value"),
]


@app.callback(
    *[Output(i, p, allow_duplicate=True) for i, p in _CLEAR_ON_LEAVE],
    Input("store-tab",  "data"),
    State("store-view", "data"),
    *[State(i, p) for i, p in _CLEAR_ON_LEAVE],
    prevent_initial_call=True,
)
def clear_on_leave(tab, view, *current):
    """Blank the OTHER sections' map layers and result stores on a tab change.

    Never touches the section being entered. Both this and that section's own
    preview callback fire on store-tab, so clearing the incoming tab's layers
    is a race: this callback would write "" over a URL the preview had just
    set, leaving a blank map beside a legend that says a layer is showing.

    Everything else is cleared unconditionally — that is what stops a previous
    section's raster lingering under the new one, and emptying the viz stores
    clears the legend, which is driven from them.

    Observed and Forecast are separate sections over shared layers, so those
    are always cleared; their preview follows the dataset selection, not
    store-tab, so there is no race (forecast_reset_on_tab).
    """
    keep_elnino   = tab == "elnino"
    keep_forecast = False
    # The preview follows the dataset selection, which survives a return to
    # Observed/Forecast (forecast_reset_on_tab); forecast_preview redraws it.
    keep_preview  = tab in FORECAST_TABS or view == "ew-infra"
    keep_infra    = tab == "infrastructure"
    keep_analysis = tab == "analysis"
    blank = lambda keep: no_update if keep else ""
    none_ = lambda keep: no_update if keep else None
    desired = (
        blank(keep_elnino), blank(keep_elnino), blank(keep_elnino),
        blank(keep_preview), blank(keep_forecast),
        blank(keep_infra), blank(keep_infra),
        blank(keep_analysis),
        none_(keep_elnino), none_(keep_elnino),
        none_(keep_forecast), none_(keep_preview),
        none_(keep_infra), none_(keep_analysis),
        # Region state and the selection highlights are shared, so they reset
        # on every switch regardless of destination.
        None, None, None, None, None,
        "", "", "",
        GLOBAL_VIEWPORT,
        None, None,
    )
    return _only_changed(desired, current)
