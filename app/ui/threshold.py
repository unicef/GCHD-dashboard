"""Hazard-threshold editors shared by Exposure, Analysis and Infra."""
from config.hazards import (
    HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS, is_binary_hazard, TOPIC_COLORS,
)
from dash import ALL, dcc, html, Input, MATCH, Output, State

from dash_app import app
from ui.components import _hazard_label


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
