"""Info popover for hazard, provenance and dataset info buttons."""
from config import FC_INFO_PREFIX, HAZARD_INFO
from dash import ALL, ctx, Input, no_update, Output, State
from elnino_config import EN_INFO, EXPLORE_MAP
from forecast_config import FORECAST_MAP

from dash_app import app
from ui.components import _hazard_info_body, _layer_label
from sections.elnino import EN_INFO_TITLES


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
        scope = ""
        if triggered["type"] == "prov-info-btn":
            scope, name = name.split(":", 1)
        # Scoped keys stay scoped in the open-state store: an "en" slug and a
        # hazard could otherwise share a name and toggle each other closed.
        key = f"{scope}:{name}" if scope in ("en", "enl") else name
        if open_for == key:                       # same button -> toggle closed
            return {"display": "none"}, no_update, no_update, None
        # El Nino icons explain CONCEPTS, not layers, so they resolve against
        # EN_INFO rather than the hazard catalogue. Keyed with the scope kept on
        # so an "en" slug cannot collide with a hazard of the same name.
        if scope == "en":
            info = EN_INFO.get(name)
            if info:
                return ({"display": "flex"}, EN_INFO_TITLES.get(name, name),
                        _hazard_info_body(info), f"en:{name}")
            return no_update, no_update, no_update, no_update
        if scope == "enl":
            cfg = EXPLORE_MAP.get(name)
            if cfg:
                body = {"description": cfg.get("desc", "")}
                if cfg.get("units"):
                    body["units"] = cfg["units"]
                return ({"display": "flex"}, cfg.get("label", name),
                        _hazard_info_body(body), f"enl:{name}")
            return no_update, no_update, no_update, no_update
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
        // El Nino concept icons ("en:threshold") keep the scope in openFor, so
        // they hit the `idx === openFor` arm below rather than the split one.
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
        // Open LEFT when there is no room on the right. The map-layer panel
        // sits against the right edge, so a fixed rightward popover fell off
        // the screen entirely. Flip rather than clamp: a clamped panel would
        // cover the button that opened it.
        var left = r.right + 10;
        var flip = (left + W + M) > window.innerWidth;
        if (flip) { left = Math.max(M, r.left - W - 10); }
        return [{
            display: "flex", top: top + "px", left: left + "px",
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
