"""Preparedness Visualization: hazard, multi-hazard and exposure."""
import dash
from config import (
    HAZARD_MAP, HAZARD_TOPICS, MHC_OPTIONS, MHI_OPTIONS, POP_PREFIX,
    POPULATION_LAYERS, SUB_TOPIC_DETAIL, TOPIC_COLORS,
)
from dash import ALL, ctx, dcc, html, Input, no_update, Output, State

from dash_app import app
from bootstrap import MH_LAYERS, TOPIC_LIST
from ui.components import _layer_label, _layer_meta
from ui.threshold import build_threshold_editor_for, _threshold_overrides


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
        raise dash.exceptions.PreventUpdate
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
        raise dash.exceptions.PreventUpdate
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
