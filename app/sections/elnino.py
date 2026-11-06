"""El Niño seasonal outlook section."""
import dash
from config.hazards import ADMIN_DATA
from dash import ALL, ctx, dcc, html, Input, no_update, Output, State
from config.elnino import (
    active_periods, CAVEATS, DEFAULT_EXPLORE, default_period, DEFAULT_SIGNAL,
    ENSO_EVENT_MIN, EXPLORE_LAYERS, EXPLORE_MAP, HINDCAST, is_compound,
    is_window, issue_label, ISSUES, METHOD_STEPS, period_issue_label,
    period_label, PERIOD_MAP, PROB_DEFAULT, PROB_MARKS, PROB_MAX, PROB_MIN,
    PROB_STEP, SIGNAL_MAP, SKILL_REFERENCE, SKILL_USABLE_MIN, SOURCE_URL,
    SYSTEM as ELNINO_SYSTEM, threshold_contract,
)
from core.elnino import (
    compound_available, compute_elnino_exposure, elnino_available, ElNinoError,
    enso_attributable, enso_state, forecast_area_share,
    get_elnino_exposed_tile, get_elnino_layer_tile, get_elnino_pop_tile,
    get_elnino_preview_tile, hindcast_area_share, signal_anomalies,
    signal_skill_share, skill_available, skill_t2m_available, t2m_available,
)
from core.gee import get_country_bounds, get_country_ucode, get_feature_bounds

from dash_app import app
from bootstrap import COUNTRY_NAMES
from ui.components import _fmt
from sections.elnino_export import _elnino_export_payload


# The layer that renders the SIGNAL itself. Section 2 chooses the signal and
# this is how it is drawn; the map dialog's layers are additive extras on their
# own tile and never replace it. Compound signals carry their own band, so
# signal_prob resolves per-signal inside core.elnino._d_signal_prob.
SIGNAL_LAYER = "signal_prob"


# Popover headings for the El Nino concept icons. Separate from EN_INFO so the
# config stays pure data and the heading can read as a question.
EN_INFO_TITLES = {
    "threshold":          "Probability threshold",
    "threshold_compound": "Combined threshold",
    "signal":             "What a signal means",
    "heat":               "Adding the heat requirement",
    "sensitivity":        "Why the threshold matters",
    "history":            "Historical context",
    "skill":              "Forecast skill",
    "anomaly":            "How unusual is it",
    "enso_attrib":        "Historically linked to El Nino",
    "enso":               "ENSO conditions",
}


def _elnino_period_options():
    multi = len({p["init"] for p in active_periods()}) > 1
    return [{"label": p["label"] + (f" · issued {p['issue_label']}"
                                    if multi else ""),
             "value": p["id"]} for p in active_periods()]


# ── El Niño tab ──────────────────────────────────────────────────────────────

def _en_collapse(key, summary, body, open_default=False):
    """A <details> block: title visible, body one click away.

    Plain HTML details/summary rather than a Dash callback — it needs no
    server round trip, keeps working if a callback errors, and is accessible
    by default. Used for the long prose that has to EXIST for a figure to be
    quotable but should not occupy the panel before anyone has read a number.
    """
    attrs = {"open": "open"} if open_default else {}
    return html.Details(id=f"en-collapse-{key}", className="en-collapse",
                        children=[
        html.Summary(summary, className="en-collapse-head"),
        html.Div(body, className="en-collapse-body"),
    ], **attrs)


def _en_info(slug):
    """Inline (i) opening an El Nino concept explanation from EN_INFO.

    Same button type as _info_icon so the existing auto-open guard (Dash
    re-fires ALL pattern inputs when results-panel components enter the layout)
    keeps covering these; only the scope differs.
    """
    return html.Button(
        html.I(className="bi bi-info-circle"),
        id={"type": "prov-info-btn", "index": f"en:{slug}"},
        className="info-icon-btn", n_clicks=0,
        title=EN_INFO_TITLES.get(slug, slug),
    )


def _en_layer_info(layer_name):
    """(i) for a map layer — opens its description, changes nothing on the map.

    Deliberately separate from the row button: the eye and the title both
    ENABLE the layer, while this only explains it. Scope "enl" so a layer
    named like a concept slug cannot collide; resolved in toggle_hazard_info
    from EXPLORE_MAP, so the prose is not duplicated.
    """
    return html.Button(
        html.I(className="bi bi-info-circle"),
        id={"type": "prov-info-btn", "index": f"enl:{layer_name}"},
        className="info-icon-btn", n_clicks=0,
        title=(EXPLORE_MAP.get(layer_name) or {}).get("label", layer_name),
    )


def _elnino_radio(id_, options, value, inline=True):
    """Small labelled radio group matching the .ps control styling."""
    return dcc.RadioItems(
        id=id_, options=options, value=value,
        className="en-radio" + (" en-radio-inline" if inline else ""),
        inputClassName="en-radio-input", labelClassName="en-radio-label",
    )


def _elnino_method_block():
    """How the numbers are produced, and what they do not mean.

    Spelled out in the tab rather than buried in a tooltip: every step here is
    a defensible-but-arguable choice, and a user who cannot see them has no way
    to judge whether the figure suits their purpose.
    """
    return html.Div(className="ps", children=[
        html.Div("Methodology", className="ps-label"),
        # The four-sentence version, always visible. The numbered steps are
        # the auditable detail and stay one click away — a reader who needs to
        # judge a defensible-but-arguable choice can still see every one.
        html.Div(className="en-method-tldr", children=[
            html.P([html.Strong("In short. "),
                    "A seasonal forecast is run 51 times with slightly "
                    "different starting conditions. We count how many of "
                    "those 51 runs put this season in the driest (or "
                    "wettest) third of what the model normally predicts."],
                   className="en-method-body"),
            html.P([html.Strong("The threshold "),
                    "is how many runs must agree before a place counts. "
                    "Raising it is more demanding, so fewer children are "
                    "counted."],
                   className="en-method-body"),
            html.P([html.Strong("The figure "),
                    "is how many children live in those places — not how "
                    "many will be harmed. Whether unusual weather becomes a "
                    "crisis depends on water, harvests, drainage and health "
                    "services, none of which are measured here."],
                   className="en-method-body"),
            html.P([html.Strong("Reliability varies. "),
                    "The forecast has a real track record in some regions "
                    "and almost none in others; each result says which."],
                   className="en-method-body", style={"marginBottom": "0"}),
        ]),
        _en_collapse("method", "Step by step", [
            html.Div(className="en-method", children=[
                html.Div(className="en-method-step", children=[
                    html.Div(f"{i}", className="en-method-num"),
                    html.Div([
                        html.Div(title, className="en-method-title"),
                        html.Div(body, className="en-method-body"),
                    ]),
                ]) for i, (title, body) in enumerate(METHOD_STEPS, 1)
            ]),
        ]),
        _en_collapse("caveats", "Read this before quoting a number", [
            html.Ul(className="en-caveats",
                    children=[html.Li(c) for c in CAVEATS]),
        ]),
        html.Div(className="ps-caption", style={"marginTop": "8px"}, children=[
            html.Span("Source: "),
            html.A("Copernicus CDS — seasonal-monthly-single-levels",
                   href=SOURCE_URL, target="_blank", className="hi-link"),
            html.Span(f" · ECMWF SEAS5 system {ELNINO_SYSTEM} · "
                      f"issued {', '.join(issue_label(t) for t in ISSUES)}"
                      f" · hindcast {HINDCAST}"),
        ]),
    ])



# Which explore layers belong in which group on the map panel, and why the
# grouping is this and not alphabetical: the reader's first question is "what
# am I looking at", and these are the answers in descending order of how often
# they are wanted. Layers absent from the config (or gated by `needs`) simply
# do not appear.
EN_LAYER_GROUPS = [
    # No "This signal" group: the signal layer is chosen in section 2 and
    # always drawn on its own tile. Offering signal_prob / cat here too
    # implied this panel could change the signal, which is the confusion
    # the two-tile split exists to remove.
    ("Rainfall",     ["anom_mm", "anom_pct", "signal_strength",
                      "fc_mean_mm", "clim_mean_mm", "dry_mask"]),
    ("Temperature",  ["anom_c", "fc_mean_c", "t2m_signal_prob"]),
    ("Combined",     ["compound_prob", "cat_compound", "dependence_ratio"]),
    ("Reliability",  ["skill_class", "skill_r", "skill_r_t2m"]),
    ("Attribution",  ["enso_composite"]),
]


def _en_layer_detail(cfg):
    """Colour key for the ENABLED extra layer, inline in the panel.

    Shown only when that layer is both chosen and switched on, so the panel
    never displays a scale for something that is not drawn. The map's own
    legend keeps describing the SIGNAL plus the population rows; this is the
    key for the additive overlay, which is why both can coexist.

    Always expanded rather than a <details>: it belongs to the one row the
    user just enabled, so hiding it behind a second click serves nothing.
    """
    kids = []
    # No prose here: the description lives in the (i) popover, which the user
    # confirmed worked well. This block is the colour KEY only, expanded
    # because the layer is actually on the map.
    if cfg.get("classified") and cfg.get("classes"):
        kids += [html.Div(className="en-lg-class", children=[
                     html.Span(className="en-lg-swatch",
                               style={"background": c["color"]}),
                     html.Span(c["label"], className="en-lg-lbl"),
                 ]) for c in cfg["classes"]]
    elif cfg.get("palette"):
        grad = ", ".join(cfg["palette"])
        kids += [
            html.Div(className="en-lg-ramp",
                     style={"background":
                            f"linear-gradient(to right, {grad})"}),
            html.Div(className="en-lg-ends", children=[
                html.Span(f"{cfg.get('min', '')}"),
                html.Span(cfg.get("units") or "", className="en-lg-units"),
                html.Span(f"{cfg.get('max', '')}"),
            ]),
        ]
    if not kids:
        return None
    return html.Div(kids, className="en-layer-detail")


def _elnino_layer_panel():
    """Map-layer picker, floating over the map rather than in the sidebar.

    It lived in the control stack as "5. Map layer", which implied it affected
    the computation. It never did — a result depends on period, signal and
    threshold only — so moving it onto the map puts "what am I looking at"
    where the looking happens, and the header says so explicitly.

    The radio keeps the id `elnino-layer` and the `value` property, so
    elnino_update_layers, elnino_preview, elnino_layer_options and the legend
    specs are all untouched. Options are still filtered at runtime by
    elnino_layer_options against the asset probes.
    """
    return html.Div(id="elnino-layer-panel", className="en-layer-panel",
                    style={"display": "none"}, children=[
        html.Div(className="en-layer-head", children=[
            html.Span("Map layer", className="en-layer-title"),
            html.I(className="bi bi-chevron-up", id="en-layer-chevron"),
        ], id="en-layer-header", n_clicks=0),
        html.Div(id="elnino-layer-body", className="en-layer-body", children=[
            html.Div("Changes what the map shows, not the numbers.",
                     className="en-layer-note"),
            # One radio over every layer; the group headings are rendered
            # separately so dcc.RadioItems stays a single component with one
            # value, which is what the existing callbacks expect.
            html.Div(id="elnino-layer-rows"),
            dcc.RadioItems(id="elnino-layer", value=DEFAULT_EXPLORE,
                           options=[], style={"display": "none"}),
        ]),
    ])


def tab_elnino():
    return html.Div(id="tab-elnino", style={"display": "none"}, children=[

        html.Div(className="exposure-method-note",
                 style={"margin": "12px 10px 0"}, children=[
            html.P([
                html.Strong("A signal is a reason to look closer, not a "
                            "forecast of drought or flooding."),
            ], className="exposure-method-p", style={"marginBottom": "0"}),
            _en_collapse("intro", "What this tab shows", [
                html.P(
                    f"Monthly ECMWF SEAS5 forecasts (latest issued "
                    f"{issue_label(ISSUES[-1])}), not a live feed. Each "
                    f"period is labelled with its issue. It shows where the season is likely to fall "
                    f"in the driest or wettest third of what this model "
                    f"normally predicts, during a period when El Niño "
                    f"conditions are present.",
                    className="exposure-method-p"),
                html.P(
                    "Co-occurrence with El Niño is not proof of causation: "
                    "the forecast is the model's response to every initial "
                    "condition it was given, and El Niño is never isolated "
                    "within it.",
                    className="exposure-method-p",
                    style={"marginBottom": "0"}),
            ]),
        ]),

        # Availability banner — one clear sentence when the assets are missing.
        html.Div(id="elnino-status", style={"margin": "10px"}),

        html.Div(id="elnino-controls", children=[
            # 1. Period
            html.Div(className="ps", children=[
                html.Div("1. Period", className="ps-label"),
                dcc.Dropdown(
                    id="elnino-period", className="ps-select",
                    options=_elnino_period_options(),
                    value=default_period(), clearable=False, searchable=False,
                ),
                html.Div(id="elnino-period-note", className="ps-caption"),
            ]),

            # 2. Signal — rainfall direction, then heat as an optional
            # qualifier. Six flat radios asked the reader to hold the whole
            # variable matrix in their head; this matches how people actually
            # think about it ("drier — and also hot?"). The resolved signal
            # name goes into a hidden store so every downstream callback keeps
            # reading ONE signal value and none of the computation moves.
            html.Div(className="ps", children=[
                html.Div([
                    html.Span("2. Signal"), _en_info("signal"),
                ], className="ps-label"),
                html.Div("Rainfall", className="en-sub-label"),
                _elnino_radio(
                    "elnino-rain",
                    [{"label": SIGNAL_MAP[n]["label"], "value": n}
                     for n in ("dry", "wet", "either")],
                    DEFAULT_SIGNAL, inline=False),
                html.Div(id="elnino-heat-wrap", children=[
                    html.Div([
                        dcc.Checklist(
                            id="elnino-heat-on", className="en-check",
                            options=[{"label": " Also require unusual "
                                               "temperature", "value": "on"}],
                            value=[],
                        ),
                        _en_info("heat"),
                    ], style={"display": "flex", "alignItems": "center",
                              "gap": "1px"}),
                    # Revealed only when the box is ticked: the direction is a
                    # second question that does not exist until the first is
                    # answered, and showing three radios up front made
                    # "temperature" look mandatory.
                    html.Div(id="elnino-heat-dir-wrap",
                             style={"display": "none"}, children=[
                        _elnino_radio(
                            "elnino-heat",
                            [{"label": "Unusually hot", "value": "hot"},
                             {"label": "Unusually cool", "value": "cool"}],
                            "hot", inline=True),
                    ]),
                ], style={"marginTop": "8px"}),
                html.Div(id="elnino-signal-resolved",
                         className="en-resolved"),
                html.Div(id="elnino-signal-note", className="ps-caption"),
            ]),

            # 3. Threshold
            html.Div(className="ps", children=[
                html.Div("3. Probability threshold", className="ps-label"),
                html.Div(className="analysis-threshold-row", children=[
                    html.Div(className="athr-row-head", children=[
                        html.Span("Signal where probability ≥",
                                  className="athr-label"),
                        dcc.Input(id="elnino-threshold", type="number",
                                  min=PROB_MIN, max=PROB_MAX, step=PROB_STEP,
                                  value=PROB_DEFAULT, debounce=True,
                                  className="athr-input"),
                        html.Span("%", className="athr-units"),
                    ]),
                    dcc.Slider(
                        id="elnino-threshold-slider",
                        min=PROB_MIN, max=PROB_MAX, step=PROB_STEP,
                        value=PROB_DEFAULT, updatemode="mouseup",
                        className="athr-slider",
                        marks={m: {"label": f"{m}%"} for m in PROB_MARKS},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                # Where the value sits relative to chance. Without this the
                # percentage invites comparison between two scales whose
                # baselines differ (33.3% vs 11.1%).
                # The chance-baseline bar was removed: an unlabelled red line
                # read as an artefact. The same point is made in words by the
                # readout below and the (i) beside the section title.
                html.Div(id="elnino-thr-readout",
                         className="en-thr-readout"),
                html.Div(id="elnino-threshold-note", className="ps-caption"),
            ]),

            # 4. Dry mask
            html.Div(className="ps", children=[
                html.Div("4. Arid-area handling", className="ps-label"),
                dcc.Checklist(
                    id="elnino-dry-mask",
                    options=[{"label": " Exclude areas too dry to interpret",
                              "value": "on"}],
                    value=["on"], className="en-check",
                ),
                html.Div("Recommended. Where normal rainfall is a fraction of "
                         "a millimetre, a “wettest third” is rounding noise at "
                         "any threshold — without this, desert children are "
                         "counted as flood-exposed.",
                         className="ps-caption"),
            ]),

            # Section 5 (map layer) MOVED to an overlay on the map itself —
            # see _elnino_layer_panel(). It sat here implying it affected the
            # computation, which it never did: results depend on period,
            # signal and threshold only. The control keeps the id
            # `elnino-layer` so every consumer (elnino_update_layers,
            # elnino_preview, the legend specs, elnino_layer_options) is
            # unchanged.

            # 6. Country
            html.Div(className="ps", children=[
                html.Div("6. Select country / territory", className="ps-label"),
                dcc.Dropdown(
                    id="elnino-country", className="ps-select",
                    options=[{"label": c, "value": c} for c in COUNTRY_NAMES],
                    value=None, placeholder="Search country / territory…",
                    clearable=False, searchable=True,
                ),
            ]),

            # 7. Level
            html.Div(id="elnino-level-section", style={"display": "none"},
                     children=[
                html.Div(className="ps", children=[
                    html.Div("7. Analysis level", className="ps-label"),
                    dcc.Dropdown(
                        id="elnino-level", className="ps-select",
                        options=[{"label": l, "value": l}
                                 for l in ADMIN_DATA.keys()],
                        value=None, placeholder="— select level —",
                        clearable=False, searchable=False,
                    ),
                    html.Div("For ADM1/ADM2, click the region on the map.",
                             className="ps-caption"),
                ]),
            ]),

            html.Div(id="elnino-compute-wrap", style={"display": "none"},
                     children=[
                html.Div(className="ps", children=[
                    html.Button("▶  Compute exposure", id="elnino-compute-btn",
                                className="ps-btn", n_clicks=0, disabled=True),
                    html.Div("Select a region, then Compute.",
                             id="elnino-compute-hint", className="ps-caption",
                             style={"marginTop": "6px"}),
                ]),
            ]),

            dcc.Loading(
                id="elnino-results-loading", type="circle", color="#1CABE2",
                children=html.Div(id="elnino-results-panel"),
            ),

            # Global, so it needs no country selection. Populated on tab open.
            html.Div(id="elnino-enso-section"),

            _elnino_method_block(),
        ]),

        # — Stores —
        # The resolved signal name, written by elnino_resolve_signal from the
        # rainfall radio + heat toggle. A STORE rather than a visible control
        # so every existing reader (nine _signal_mask call sites, the compute
        # callback, the threshold contract) keeps reading one signal value and
        # the computation path is untouched.
        dcc.Store(id="elnino-signal", data=DEFAULT_SIGNAL),
        # Whether the map dialog's extra layer is currently drawn, and what it
        # is. Separate from `elnino-layer` (which layer is chosen) so a hidden
        # layer keeps its selection.
        dcc.Store(id="elnino-extra-on", data=False),
        dcc.Store(id="store-elnino-extra", data=None),
        dcc.Store(id="store-elnino-result",  data=None),
        # {meta, history, export}; files are built from it on download.
        dcc.Store(id="store-elnino-export",  data=None),
        dcc.Store(id="store-elnino-viz",     data=None),
        # Previewed layer + its vis, so the legend can describe the global map
        # that is on screen before any region has been computed.
        dcc.Store(id="store-elnino-preview", data=None),
    ])


# =============================================================================
# El Niño seasonal outlook
# =============================================================================

def _elnino_error(msg):
    return html.Div(className="ps", children=[
        html.Div(msg, style={"fontSize": "0.78rem", "color": "var(--red)",
                             "lineHeight": "1.5"}),
    ])


@app.callback(
    Output("elnino-layer", "options"),
    Input("store-tab",     "data"),
)
def elnino_layer_options(tab):
    """Offer the skill layers only when the skill collection exists.

    It is built by a separate script and is legitimately absent on a fresh
    deployment, so the dropdown is filtered at runtime rather than baked at
    import — otherwise selecting one would produce an asset-not-found error
    the user cannot act on.
    """
    if tab != "elnino":
        return no_update
    have = {}
    # "brier" is deliberately absent: no layer carries needs="brier" any more.
    for key, probe in (("skill", skill_available),
                       ("t2m", t2m_available),
                       ("compound", compound_available),
                       ("skill_t2m", skill_t2m_available)):
        try:
            have[key] = probe()
        except Exception:
            have[key] = False
    return [{"label": l["label"], "value": l["name"]}
            for l in EXPLORE_LAYERS
            if not l.get("needs") or have.get(l["needs"])]


@app.callback(
    Output("elnino-status",   "children"),
    Output("elnino-controls", "style"),
    Input("store-tab",        "data"),
)
def elnino_status(tab):
    """Check the assets once the tab is opened, not at import.

    A missing or unshared collection is the expected first-run state, so it
    gets one plain sentence instead of an exception inside every callback.
    """
    if tab != "elnino":
        return no_update, no_update
    try:
        ok, msg = elnino_available()
    except Exception as e:
        ok, msg = False, str(e)[:200]
    if ok:
        return None, {"display": "block"}
    return html.Div(className="ps", children=[
        html.Div("Outlook unavailable", className="ps-label"),
        html.Div(msg, className="ps-caption", style={"color": "var(--red)"}),
    ]), {"display": "none"}


@app.callback(
    Output("elnino-layer",  "value"),
    Input("elnino-signal",  "data"),
    State("elnino-layer",   "value"),
    prevent_initial_call=True,
)
def elnino_layer_for_signal(signal, current):
    """Switch the map to the categorised view when the signal is the union.

    A single probability ramp cannot represent two opposite signals — there is
    no one band to colour — so "Either signal" on the probability layer shows
    max(p_below, p_above), which looks like one continuous field and hides the
    very distinction the user asked for. The classified layer is the only view
    that answers it, so select it automatically.

    Moving off the union restores the probability layer, but only if the user
    is still on the classified one: a deliberate choice of some other layer is
    left alone.
    """
    cfg = SIGNAL_MAP.get(signal) or {}
    if cfg.get("union_of"):
        return "cat"
    # A compound signal's probability lives in a different asset, so the
    # default rainfall probability layer would show something unrelated to the
    # count. Point at the joint layer instead.
    if cfg.get("compound"):
        if current in ("signal_prob", "cat"):
            return "compound_prob"
        return no_update
    # Coming BACK from a compound signal, the joint layers no longer match.
    if current in ("cat", "compound_prob", "cat_compound"):
        return DEFAULT_EXPLORE
    return no_update


@app.callback(
    Output("elnino-period-note",    "children"),
    Output("elnino-signal-note",    "children"),
    Output("elnino-layer-note",     "children"),
    Input("elnino-period",          "value"),
    Input("elnino-signal",          "data"),
    Input("elnino-layer",           "value"),
)
def elnino_notes(period, signal, layer):
    p = PERIOD_MAP.get(period) or {}
    s = SIGNAL_MAP.get(signal) or {}
    l = EXPLORE_MAP.get(layer) or {}
    p_note = (f"Forecast issued {p.get('issue_label', '—')}. "
              + ("Three-month total. " if p.get("lead") == 0 else "")
              + f"Dry-area cutoff: {p.get('dry_mask_mm', '—')} mm.")
    return p_note, s.get("blurb", ""), l.get("desc", "")


# Ended months drop out without a restart: rebuilt whenever the tab opens.
@app.callback(
    Output("elnino-period", "options"),
    Output("elnino-period", "value"),
    Input("store-tab",      "data"),
    State("elnino-period",  "value"),
)
def elnino_period_refresh(tab, current):
    if tab != "elnino":
        raise dash.exceptions.PreventUpdate
    opts = _elnino_period_options()
    keep = current in {o["value"] for o in opts}
    return opts, (no_update if keep else default_period())


@app.callback(
    Output("elnino-layer-panel", "style"),
    Output("elnino-layer-rows",  "children"),
    Output("elnino-layer",       "options"),
    Input("store-tab",           "data"),
    Input("elnino-layer",        "value"),
    Input("elnino-extra-on",     "data"),
)
def elnino_layer_panel(tab, current, extra_on):
    """Show the map-layer panel on this tab only, and build its grouped rows.

    The hidden dcc.RadioItems holds the value (so every existing consumer is
    unchanged); these rows are the visible control and write to it by click.
    Each row carries the layer's own `desc` prose in an (i) rather than
    stacking six captions under a dropdown.

    Layers are filtered by `needs` against the live asset probes, so a
    half-finished deployment shows only what exists.
    """
    if tab != "elnino":
        return {"display": "none"}, no_update, no_update
    extra_on = bool(extra_on)

    have = {}
    for key, probe in (("skill", skill_available),
                       ("t2m", t2m_available),
                       ("compound", compound_available),
                       ("skill_t2m", skill_t2m_available)):
        try:
            have[key] = probe()
        except Exception:
            have[key] = False

    def ok(name):
        cfg = EXPLORE_MAP.get(name)
        if not cfg:
            return False
        need = cfg.get("needs")
        return (not need) or have.get(need, False)

    rows, opts = [], []
    for group, names in EN_LAYER_GROUPS:
        live = [n for n in names if ok(n)]
        if not live:
            continue
        rows.append(html.Div(group, className="en-layer-group"))
        for n in live:
            cfg = EXPLORE_MAP[n]
            opts.append({"label": cfg["label"], "value": n})
            # "Enabled" means chosen AND switched on. The legend expands only
            # then, because a key for a layer that is not drawn is noise.
            is_sel = (n == current)
            live = is_sel and extra_on
            rows.append(html.Div(className="en-layer-item", children=[
                html.Div(className=(
                    "en-layer-row active" if live else "en-layer-row"
                ), children=[
                    # Eye and title do the SAME thing: enable this layer. The
                    # eye additionally turns it back off.
                    html.Button(html.I(className="bi bi-eye-fill"),
                                id={"type": "en-layer-eye", "index": n},
                                className=("en-layer-eye" if live
                                           else "en-layer-eye "
                                                "en-layer-eye-off"),
                                n_clicks=0,
                                title="Show / hide on map"),
                    # Title enables the layer (same as the eye).
                    html.Button(cfg["label"],
                                id={"type": "en-layer-pick", "index": n},
                                className="en-layer-btn", n_clicks=0,
                                title="Show this layer on the map"),
                    # The (i) explains the layer and does NOT change the map —
                    # a separate concern, so a separate control.
                    _en_layer_info(n),
                ]),
                # Key expands when the layer is ENABLED, not merely chosen.
                _en_layer_detail(cfg) if live else None,
            ]))
    return {"display": "flex"}, rows, opts


@app.callback(
    Output("elnino-layer", "value", allow_duplicate=True),
    Input({"type": "en-layer-pick", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def elnino_pick_layer(clicks):
    """Clicking a row sets the hidden radio that everything else reads.

    Guarded the same way the info popover is: Dash re-fires ALL pattern inputs
    when matching components enter the layout, and the rows are rebuilt
    whenever the panel re-renders, so without this an unrelated re-render would
    silently change the selected layer.
    """
    if not any(c for c in (clicks or []) if c):
        return no_update
    t = ctx.triggered_id
    if not t:
        return no_update
    return t["index"]


# The EXTRA map layer: an additive overlay chosen in the map dialog, drawn
# above the signal at lower opacity. It never replaces the signal tile — that
# always shows what section 2 selected, because the signal is what the numbers
# in the panel describe.
@app.callback(
    Output("elnino-extra-tile", "url"),
    Output("elnino-extra-tile", "opacity"),
    Output("store-elnino-extra", "data"),
    Input("elnino-layer",       "value"),
    Input("elnino-extra-on",    "data"),
    Input("elnino-period",      "value"),
    Input("elnino-signal",      "data"),
    Input("elnino-threshold",   "value"),
    Input("elnino-dry-mask",    "value"),
    Input("store-tab",          "data"),
    prevent_initial_call=True,
)
def elnino_extra_layer(layer, on, period, signal, threshold, dry_mask, tab):
    """Render the chosen extra layer, or nothing when it is switched off.

    Opacity 0 rather than an empty url when off: keeping the tile loaded means
    re-enabling it is instant, and a blank url would make the eye feel like a
    reload rather than a switch.
    """
    if tab != "elnino":
        return no_update, no_update, no_update
    if not layer or not on:
        return no_update, 0, None
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = PROB_DEFAULT
    try:
        url, vis = get_elnino_preview_tile(period, layer, signal, thr,
                                           "on" in (dry_mask or []))
    except Exception:
        return no_update, 0, None
    return url, 0.6, {"layer": layer, "vis": vis}


# Clicking a layer row turns the extra layer ON; the eye toggles it. Two
# inputs, one state, so selecting a different layer while hidden also reveals
# it — otherwise a click would appear to do nothing.
@app.callback(
    Output("elnino-extra-on", "data"),
    Output({"type": "en-layer-eye", "index": ALL}, "className"),
    Input({"type": "en-layer-pick", "index": ALL}, "n_clicks"),
    Input({"type": "en-layer-eye", "index": ALL}, "n_clicks"),
    State("elnino-extra-on", "data"),
    State({"type": "en-layer-eye", "index": ALL}, "id"),
    State("elnino-layer", "value"),
    prevent_initial_call=True,
)
def elnino_extra_toggle(picks, eyes, on, eye_ids, current):
    """Keep the extra layer's on/off state and the eye icons in step.

    Guarded against Dash's re-fire of ALL pattern inputs when the panel
    re-renders: without it, rebuilding the rows would silently toggle the
    layer.
    """
    # PreventUpdate, not no_update: a bare no_update is invalid for the
    # wildcard (ALL) output in Dash 4.
    if not any(c for c in ((picks or []) + (eyes or [])) if c):
        raise dash.exceptions.PreventUpdate
    t = ctx.triggered_id
    if not t:
        raise dash.exceptions.PreventUpdate
    if t.get("type") == "en-layer-eye":
        on = not bool(on) if t["index"] == current else True
    else:
        on = True                      # choosing a layer shows it
    cls = ["en-layer-eye" if (on and i["index"] == current)
           else "en-layer-eye en-layer-eye-off" for i in (eye_ids or [])]
    return bool(on), cls


@app.callback(
    Output("elnino-layer-body", "style"),
    Output("en-layer-chevron",  "className"),
    Input("en-layer-header",    "n_clicks"),
    prevent_initial_call=True,
)
def elnino_layer_collapse(n):
    """Collapse the panel — it overlaps the map, and on a small screen the
    reader needs the map back."""
    closed = bool(n) and n % 2 == 1
    return ({"display": "none"} if closed else {"display": "block"},
            "bi bi-chevron-down" if closed else "bi bi-chevron-up")


# elnino_signal_options was REMOVED. It populated six radios with disabled
# rows faking group headings; section 2 now has a static rainfall radio plus a
# heat toggle, and elnino_resolve_signal below turns that pair into the single
# signal name everything downstream already expects.
@app.callback(
    Output("elnino-signal",          "data"),
    Output("elnino-signal-resolved", "children"),
    Output("elnino-heat",            "options"),
    Output("elnino-heat",            "value"),
    Output("elnino-heat-wrap",       "style"),
    Output("elnino-heat-dir-wrap",   "style"),
    Output("elnino-heat-on",         "options"),
    Output("elnino-heat-on",         "value"),
    Input("elnino-rain",             "value"),
    Input("elnino-heat-on",          "value"),
    Input("elnino-heat",             "value"),
    Input("store-tab",               "data"),
    State("elnino-heat-wrap",        "style"),
)
def elnino_resolve_signal(rain, heat_on, heat, tab, wrap_style):
    """(rainfall direction, heat on/off) -> one signal name.

    The whole point of the indirection: downstream code keeps reading a single
    signal, so nothing about the computation changes while the control becomes
    readable.

    "Either" + heat is not a valid combination. A union has no single joint
    probability band to threshold — dry-and-hot and wet-and-hot are different
    forecasts that verify separately — so the toggle is disabled and says why
    rather than silently producing a number that means neither.
    """
    # Leaving for another section must not rewrite the signal: that would
    # re-run the whole El Niño layer chain behind a pane nobody is looking at.
    if ctx.triggered_id == "store-tab" and tab != "elnino":
        raise dash.exceptions.PreventUpdate
    rain = rain or DEFAULT_SIGNAL
    style = dict(wrap_style or {})

    # Hide the toggle entirely when the compound assets are absent: same
    # degradation contract as the skill layers.
    have_compound = False
    if tab == "elnino":
        try:
            have_compound = compound_available()
        except Exception:
            have_compound = False
        style["display"] = "flex" if have_compound else "none"

    is_union = bool((SIGNAL_MAP.get(rain) or {}).get("union_of"))
    # All four corners of the 2x2 exist in the compound asset, so every
    # rainfall x temperature pairing resolves — except with the union, which
    # has no single joint band to threshold.
    pair = {("dry", "hot"):  "hotdry", ("dry", "cool"):  "cooldry",
            ("wet", "hot"):  "hotwet", ("wet", "cool"):  "coolwet"}
    direction = heat if heat in ("hot", "cool") else "hot"
    on = bool(heat_on) and not is_union and have_compound

    opts = [{"label": "Unusually hot", "value": "hot"},
            {"label": "Unusually cool", "value": "cool"}]
    # Disabled rather than hidden for a union, so the reason is visible.
    check_opts = [{"label": " Also require unusual temperature",
                   "value": "on", "disabled": is_union}]
    dir_style = {"display": "block" if on else "none",
                 "marginTop": "5px", "marginLeft": "18px"}

    signal = pair.get((rain, direction), rain) if on else rain
    cfg = SIGNAL_MAP.get(signal) or {}
    if is_union:
        resolved = html.Span([
            "Temperature cannot be combined with ", html.Em("Either signal"),
            " — the two rainfall directions verify separately, so there is no "
            "single combined probability to threshold.",
        ])
    else:
        resolved = html.Span([
            "Showing: ", html.Strong(cfg.get("label", signal)),
        ])
    return (signal, resolved, opts, direction, style, dir_style,
            check_opts, (["on"] if on else []))


@app.callback(
    Output("elnino-threshold",        "value"),
    Output("elnino-threshold-slider", "value"),
    Output("elnino-threshold-note",   "children"),
    Output("elnino-threshold",        "min"),
    Output("elnino-threshold",        "max"),
    Output("elnino-threshold-slider", "min"),
    Output("elnino-threshold-slider", "max"),
    Output("elnino-threshold-slider", "marks"),
    Output("elnino-thr-readout",      "children"),
    Output("elnino-thr-info",         "children"),
    Input("elnino-threshold",         "value"),
    Input("elnino-threshold-slider",  "value"),
    Input("elnino-signal",            "data"),
)
def elnino_sync_threshold(box, slider, signal):
    """Keep the number box and slider in step, and say what the value means.

    The bounds and the baseline BOTH depend on the signal. A compound event
    needs two conditions at once, so its no-information baseline is 1/9 =
    11.1%, not 1/3 — and a slider left on the single-variable range would make
    every compound map look empty while implying the two numbers are
    comparable. Switching signal between the groups therefore resets the value
    to that group's default rather than carrying a meaningless number across.

    The note is not decoration: a user dragging towards the baseline needs to
    know the signal is dissolving into chance rather than getting more
    inclusive, and the baseline differs between the two groups.
    """
    lo, hi, default, marks, chance = threshold_contract(signal)
    mark_dict = {m: {"label": f"{m}%"} for m in marks}

    src = ctx.triggered_id
    if src == "elnino-signal":
        # Crossing between the two groups invalidates the current number.
        val = default
    else:
        val = box if src == "elnino-threshold" else slider
    try:
        val = float(val)
    except (TypeError, ValueError):
        val = default
    # Already a percentage — no scaling here. The single 0-100 -> 0-1 step
    # happens in core.elnino._signal_mask via to_fraction().
    val = int(round(max(lo, min(hi, val))))

    margin = val - chance
    if is_compound(signal):
        if val <= chance + 3:
            note = (f"{val}% is barely above the {chance:.1f}% joint baseline "
                    f"— for two conditions at once, chance is 1/9, not 1/3.")
        else:
            note = (f"{val}% of the 51 members have BOTH conditions at once "
                    f"— {margin:+.0f} points above the {chance:.1f}% joint "
                    f"baseline. Not comparable with a single-variable "
                    f"threshold: 25% here is as far above chance as 50% there.")
    elif val <= 36:
        note = (f"{val}% is barely above the {chance:.0f}% "
                f"no-information baseline — almost every cell will qualify.")
    elif val >= 65:
        note = (f"{val}% demands strong ensemble agreement; expect few "
                f"cells and a conservative count.")
    else:
        note = (f"{val}% of the 51 members — {margin:+.0f} points above the "
                f"{chance:.0f}% baseline. ECMWF's own charts shade from "
                f"about 40%.")
    ratio = (val / chance) if chance else 0
    readout = html.Span([
        html.Strong(f"{ratio:.1f}x"),
        f" more likely than chance ({chance:.1f}%).",
    ] + ([" Not comparable with the rainfall threshold — a combined event "
          "is rarer, so its whole scale sits lower."]
         if is_compound(signal) else []))
    slug = "threshold_compound" if is_compound(signal) else "threshold"
    return (val, val, note, lo, hi, lo, hi, mark_dict,
            readout, _en_info(slug))


@app.callback(
    Output("store-country",         "data", allow_duplicate=True),
    Output("store-ucode",           "data", allow_duplicate=True),
    Output("store-bounds",          "data", allow_duplicate=True),
    Output("store-level",           "data", allow_duplicate=True),
    Output("store-clicked-ucode",   "data", allow_duplicate=True),
    Output("store-clicked-name",    "data", allow_duplicate=True),
    Output("elnino-level-section",  "style"),
    Output("elnino-level",          "value"),
    Input("elnino-country",         "value"),
    prevent_initial_call=True,
)
def elnino_on_country(country):
    """Write the SHARED region stores, exactly as forecast_on_country does.

    The compute gate reads store-ucode / store-clicked-ucode, which every other
    tab populates through these stores. A tab-local dropdown that only toggled
    its own section would leave those empty and the Compute button would never
    appear — which is precisely what happened.

    Defaulting the level to adm0 also means a country selection alone is enough
    to run, rather than requiring a second pick that adds nothing at adm0.
    """
    if not country:
        return None, None, None, None, None, None, {"display": "none"}, None
    ucode  = get_country_ucode(country)
    bounds = get_country_bounds(ucode)
    return (country, ucode, bounds, "adm0 (Country)", None, None,
            {"display": "block"}, "adm0 (Country)")


@app.callback(
    Output("store-level",         "data", allow_duplicate=True),
    Output("store-clicked-ucode", "data", allow_duplicate=True),
    Output("store-clicked-name",  "data", allow_duplicate=True),
    Input("elnino-level",         "value"),
    prevent_initial_call=True,
)
def elnino_on_level(level):
    """Adopt the level and clear any region clicked at the previous one."""
    if not level:
        return no_update, None, None
    return level, None, None


@app.callback(
    Output("elnino-compute-wrap", "style"),
    Output("elnino-compute-btn",  "disabled"),
    Output("elnino-compute-hint", "children"),
    Input("elnino-level",         "value"),
    Input("store-ucode",          "data"),
    Input("store-clicked-ucode",  "data"),
    State("store-tab",            "data"),
)
def elnino_toggle_compute(level, ucode, clicked, tab):
    if not level:
        return {"display": "none"}, True, no_update
    if level == "adm0 (Country)":
        ready = bool(ucode)
        hint = "Ready." if ready else "Select a country first."
    else:
        ready = bool(clicked)
        hint = "Ready." if ready else "Click the region on the map."
    return {"display": "block"}, (not ready), hint


@app.callback(
    Output("store-elnino-result", "data"),
    Output("elnino-results-panel", "children"),
    Output("store-elnino-viz",    "data"),
    Output("store-elnino-export", "data"),
    Input("elnino-compute-btn",   "n_clicks"),
    State("elnino-period",        "value"),
    State("elnino-signal",        "data"),
    State("elnino-threshold",     "value"),
    State("elnino-layer",         "value"),
    State("elnino-dry-mask",      "value"),
    State("store-level",          "data"),
    State("store-ucode",          "data"),
    State("store-clicked-ucode",  "data"),
    State("store-clicked-name",   "data"),
    State("elnino-country",       "value"),
    State("elnino-level",         "value"),
    prevent_initial_call=True,
)
def elnino_compute(_n, period, signal, threshold, layer, dry_mask,
                   level_store, ucode, clicked, clicked_name, country,
                   level_sel):
    level = level_sel or level_store
    if not level:
        return no_update, no_update, no_update, no_update
    target = ucode if level == "adm0 (Country)" else clicked
    if not target:
        return no_update, no_update, no_update, no_update
    region_name = country if level == "adm0 (Country)" else (clicked_name
                                                             or target)
    apply_mask = "on" in (dry_mask or [])
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = PROB_DEFAULT

    try:
        result = compute_elnino_exposure(period, signal, thr, target, level,
                                         apply_mask)
    except ElNinoError as ex:
        return None, _elnino_error(str(ex)), None, None
    except Exception as ex:
        return (None, _elnino_error(f"Computation failed: {str(ex)[:200]}"),
                None, None)

    # Historical context is best-effort — the numbers stand without it.
    # Both sides must be AREA shares: the panel's headline percentage is a
    # share of children, which is not comparable with the hindcast rows.
    try:
        history = hindcast_area_share(period, signal, thr, target, level)
    except Exception:
        history = []
    try:
        this_area = forecast_area_share(period, signal, thr, target, level)
    except Exception:
        this_area = None
    # Skill is a qualifier on the figure, never a filter of it — a failure here
    # must not cost the user their numbers.
    try:
        skill = signal_skill_share(period, signal, thr, target, level,
                                   apply_mask)
    except Exception:
        skill = None
    # Magnitude and attribution are qualifiers too: a failure in either must
    # not cost the user their count, so each fails soft independently.
    try:
        anoms = signal_anomalies(period, signal, thr, target, level,
                                 apply_mask)
    except Exception:
        anoms = None
    try:
        attrib = enso_attributable(period, signal, thr, target, level,
                                   apply_mask)
    except Exception:
        attrib = None
    meta = {"period": period, "signal": signal, "threshold": thr,
            "region": region_name, "level": level, "ucode": target,
            "layer": layer, "dry_mask": apply_mask,
            "skill": skill, "anoms": anoms, "attrib": attrib,
            "pop_res": result.get("_pop_resolution_m", 1000)}

    # The outlook layer's vis travels with the result so the legend's gradient
    # matches what was actually rendered rather than a re-derived guess.
    try:
        _, meta["layer_vis"] = get_elnino_layer_tile(
            period=period, layer=layer, signal=signal, threshold=thr,
            feature_ucode=target, admin_level=level,
            apply_dry_mask=apply_mask)
    except Exception:
        meta["layer_vis"] = None

    meta["area_share"] = this_area
    # The legend states the actual counts, not just layer names: a reader
    # looking at the map should not have to scroll back to the panel to learn
    # what the highlighted area amounts to.
    meta["exposed"] = int(round(result.get("exposed", 0) or 0))
    meta["total"] = int(round(result.get("total_population", 0) or 0))
    payload = {"meta": meta, "history": history,
               "export": _elnino_export_payload(result, meta)}
    return (result, render_elnino_results(result, meta, history), meta,
            payload)


@app.callback(
    Output("elnino-layer-tile",   "url"),
    Output("elnino-pop-tile",     "url"),
    Output("elnino-exposed-tile", "url"),
    Output("main-map",            "viewport", allow_duplicate=True),
    Input("store-elnino-viz",     "data"),
    prevent_initial_call=True,
)
def elnino_update_layers(viz):
    """The three result layers: the outlook, all children, and the subset.

    Each is best-effort and independent — a failure in one must not blank the
    other two, because the numbers in the panel are already computed and the
    map is the only way to see where they came from.
    """
    if not viz:
        return "", "", "", no_update
    kw = dict(period=viz["period"], signal=viz["signal"],
              threshold=viz["threshold"], apply_dry_mask=viz["dry_mask"])
    try:
        # The signal, always — never viz["layer"], which is the map dialog's
        # additive extra and has its own tile.
        sig_layer = ("cat" if (SIGNAL_MAP.get(viz["signal"]) or {}).get(
                         "union_of") else SIGNAL_LAYER)
        l_url, _ = get_elnino_layer_tile(
            layer=sig_layer, feature_ucode=viz["ucode"],
            admin_level=viz["level"], **kw)
    except Exception:
        l_url = ""
    try:
        p_url, _ = get_elnino_pop_tile(viz["ucode"], viz["level"])
    except Exception:
        p_url = ""
    try:
        e_url, _ = get_elnino_exposed_tile(
            feature_ucode=viz["ucode"], admin_level=viz["level"], **kw)
    except Exception:
        e_url = ""
    try:
        viewport = {"bounds": get_feature_bounds(viz["level"], viz["ucode"]),
                    "transition": "flyTo"}
    except Exception:
        viewport = no_update
    return l_url, p_url, e_url, viewport


@app.callback(
    Output("elnino-layer-tile",   "url", allow_duplicate=True),
    Output("store-elnino-preview", "data"),
    Input("store-tab",            "data"),
    Input("elnino-period",        "value"),
    Input("elnino-signal",        "data"),
    Input("elnino-threshold",     "value"),
    Input("elnino-dry-mask",      "value"),
    prevent_initial_call=True,
)
def elnino_preview(tab, period, signal, threshold, dry_mask):
    """Global preview, so a layer can be inspected before choosing a region.

    Driven by store-tab as well as the controls, so opening the tab renders the
    default period immediately: the controls already carry a default selection,
    and a blank map beside a filled-in form reads as broken rather than as
    "nothing chosen yet".

    This is also what makes the tab explorable rather than a form — the map
    responds to the threshold slider at once, which is the clearest possible
    demonstration of how much the signal depends on where that line is drawn.
    """
    if tab != "elnino":
        return no_update, no_update
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = PROB_DEFAULT
    # SIGNAL_LAYER, not the map dialog's selection: this tile shows what the
    # chosen signal looks like, and the dialog's layers are additive extras
    # drawn on top (see elnino_extra_layer). A classified view is the only
    # readable option for the union, which has no single probability band.
    layer = "cat" if (SIGNAL_MAP.get(signal) or {}).get("union_of")         else SIGNAL_LAYER
    try:
        url, vis = get_elnino_preview_tile(period, layer, signal, thr,
                                           "on" in (dry_mask or []))
    except Exception:
        return no_update, no_update
    return url, {"layer": layer, "signal": signal, "period": period,
                 "vis": vis}


def _enso_chart(series):
    """Niño 3.4 as CSS divs diverging from a zero baseline.

    Hand-rolled rather than dcc.Graph to match _elnino_history_block — the only
    other chart in this app — and to avoid pulling a plotting bundle in for one
    sparkline. Warm months read red, cool blue, and the ±0.5 event thresholds
    are drawn so "is this an event" is answerable at a glance.
    """
    if not series:
        return None
    vals = [r["oni"] for r in series]
    span = max(2.0, max(abs(min(vals)), abs(max(vals))))   # symmetric, min ±2
    half = lambda v: 50.0 * v / span                        # % of box height

    bars = []
    for r in series:
        v = r["oni"]
        h = abs(half(v))
        warm = v >= 0
        style = {"height": f"{h:.1f}%",
                 "background": "#d7443e" if warm else "#2b7bba"}
        style["bottom" if warm else "top"] = "50%"
        bars.append(html.Div(className="en-enso-bar", title=f"{r['date']}: {v:+.2f} °C",
                             children=html.Div(className="en-enso-fill",
                                               style=style)))
    return html.Div(className="en-enso-chart", children=[
        html.Div(className="en-enso-zero"),
        html.Div(className="en-enso-thr",
                 style={"bottom": f"{50 + half(ENSO_EVENT_MIN):.1f}%"}),
        html.Div(className="en-enso-thr",
                 style={"bottom": f"{50 - half(ENSO_EVENT_MIN):.1f}%"}),
        html.Div(className="en-enso-bars", children=bars),
    ])


def _elnino_enso_block(state):
    """Observed ENSO state, its recent trend, and what it does not prove."""
    if not state:
        return None
    oni, label = state["oni"], state["label"]
    series = state.get("series") or []
    warm = oni >= ENSO_EVENT_MIN
    cool = oni <= -ENSO_EVENT_MIN
    tone = "#d7443e" if warm else ("#2b7bba" if cool else "var(--mid)")

    first = series[0]["date"] if series else ""
    last = series[-1]["date"] if series else ""

    return html.Div(className="ps", children=[
        html.Div([
            html.Span("ENSO conditions"), _en_info("enso"),
        ], className="ps-label"),
        html.Div(className="en-enso-head", children=[
            html.Span(label, className="en-enso-state",
                      style={"color": tone}),
            html.Span(f"Niño 3.4  {oni:+.2f} °C", className="en-enso-val"),
            html.Span(f"3-month mean to {state['asof']}",
                      className="en-enso-asof"),
        ]),
        _enso_chart(series),
        html.Div(f"Monthly sea-surface temperature anomaly in the Niño 3.4 "
                 f"region, {first} to {last}. Dashed lines mark the ±"
                 f"{ENSO_EVENT_MIN} °C event thresholds. Observed from NOAA "
                 f"OISST — independent of the rainfall forecast.",
                 className="ps-caption", style={"marginTop": "8px"}),
        html.Div([
            html.Strong("This does not prove the forecast signal is caused "
                        "by ENSO. "),
            "For the empirical check, compare the map against the ",
            html.Em("El Niño rainfall fingerprint"),
            " layer — the model's own difference between its El Niño "
            "and neutral hindcast seasons.",
        ], className="ps-caption", style={"marginTop": "8px"}),
    ])


@app.callback(
    Output("elnino-enso-section", "children"),
    Input("store-tab",            "data"),
)
def elnino_enso(tab):
    """Populate the ENSO block when the tab opens.

    Best-effort: a failure here leaves the section empty rather than taking the
    tab down, since it is context rather than a result.
    """
    if tab != "elnino":
        return no_update
    try:
        return _elnino_enso_block(enso_state())
    except Exception as e:
        print(f"[elnino] ENSO context unavailable: {str(e)[:160]}", flush=True)
        return None


# _elnino_bss_row was REMOVED here. It rendered the Brier verdict beside the
# correlation, and the two genuinely disagree (Horn of Africa: r +0.47 vs BSS
# +0.07). Reviewers of the rainfall report preferred the correlation: BSS asks
# the stricter question but does not line up with the classified historical
# values they read a signal's direction from. The scores are still computed and
# stored in el_nino/brier by build_brier_skill.py, and signal_bss() still works
# — nothing renders them. See config.elnino.EXPLORE_LAYERS for the full note.


def _elnino_skill_note(skill, bss=None):
    """How much of this signal area the model has a track record for.

    The CORRELATION only. A Brier skill score is also computed and stored
    (el_nino/brier), and it is the stricter measure — it grades the tercile
    probabilities the threshold actually reads, rather than the seasonal mean —
    but reviewers of the rainfall report found it did not sit alongside the
    classified historical values they use to read a signal's direction, and
    chose the correlation. `bss` is accepted and ignored so the caller does not
    have to change; see elnino_config's EXPLORE_LAYERS comment.

    Caption on the figure, never a filter of it: the count stays comparable
    with every other published number. Returns None when the skill collection
    does not exist, so the panel degrades quietly.
    """
    usable = (skill or {}).get("usable_pct")
    if usable is None:
        return None
    assessed = skill.get("assessed_pct")

    if usable >= 60:
        tone, verdict = "#3d9e56", "most of this signal sits where the model has a track record"
    elif usable >= 30:
        tone, verdict = "#e8b84b", "only part of this signal sits where the model has a track record"
    else:
        tone, verdict = "#c94040", "little of this signal sits where the model has a track record"

    bits = [
        html.Span(f"{usable:.0f}%", style={"fontWeight": "700",
                                           "color": tone}),
        html.Span(f" of the signal area has usable forecast skill "
                  f"(r ≥ {SKILL_USABLE_MIN} against {SKILL_REFERENCE}, "
                  f"{HINDCAST}) — {verdict}."),
    ]
    if assessed is not None and assessed < 95:
        bits.append(html.Span(
            f" {100 - assessed:.0f}% of it is over ocean or outside the "
            f"observation record and could not be scored.",
            style={"color": "var(--lo)"}))
    return html.Div(className="ps", children=[
        html.Div([
            html.Span("Forecast skill here"), _en_info("skill"),
        ], className="ps-label"),
        html.Div(bits, className="ps-caption"),
        html.Div("Skill is reported, never applied — the count above is "
                 "unfiltered. The correlation grades how well the forecast "
                 "ranked wet and dry years, which is a more forgiving test "
                 "than whether its probabilities verify.",
                 className="ps-caption",
                 style={"color": "var(--lo)", "fontStyle": "italic",
                        "marginTop": "6px"}),
    ])


def _elnino_history_block(history, area_share):
    """Where this year's signal sits against the 24 hindcast years.

    Answers the question the forecast alone cannot: is this unusual, or an
    ordinary year?

    BOTH sides are shares of interpretable AREA. The panel's headline figure is
    a share of CHILDREN and must never be substituted here — for Colombia they
    are 94% and 67%, because children cluster in the Andean highlands while the
    signal is measured across the whole territory. Mixing them compares two
    different denominators and overstates how unusual the year looks.

    Area rather than population on the hindcast side is deliberate: the
    population grid is 2025, and applying it to 1993 would imply a precision
    that does not exist.
    """
    if not history or area_share is None:
        return None
    shares = [h["share"] for h in history]
    mean_share = sum(shares) / len(shares)
    # Rank among all 25 seasons (24 hindcast + this forecast), 1 = least area.
    rank = sum(1 for s in shares if s < area_share) + 1
    hi = max(max(shares), area_share, 1)
    bars = [html.Div(className="en-hist-bar",
                     title=f"{h['year']}: {h['share']}% of area",
                     style={"height": f"{100 * h['share'] / hi:.1f}%"})
            for h in history]
    return html.Div(className="ps", children=[
        html.Div([
            html.Span("Historical context"), _en_info("history"),
        ], className="ps-label"),
        html.Div(className="en-hist", children=[
            html.Div(className="en-hist-bars", children=bars),
            html.Div(className="en-hist-now",
                     style={"bottom": f"{100 * area_share / hi:.1f}%"},
                     title=f"This forecast: {area_share:.1f}% of area"),
        ]),
        html.Div([
            html.Strong(f"{area_share:.0f}% of this region's interpretable "
                        f"area"),
            f" falls under the signal in this forecast. Across the "
            f"{len(history)} hindcast seasons the average was "
            f"{mean_share:.0f}% (range {min(shares):.0f}–{max(shares):.0f}%), "
            f"ranking this season {rank} of {len(history) + 1} — ",
            html.Strong(f"{'more' if rank > len(history) / 2 else 'less'} "
                        f"widespread than usual."),
        ], className="ps-caption", style={"marginTop": "8px"}),
        html.Div("Each bar is one hindcast season (1993–2016); the dashed "
                 "line is this forecast. Shares of AREA, not population — "
                 "see the note above for why the two differ.",
                 className="ps-caption", style={"marginTop": "6px"}),
    ])


def _elnino_empty_state(exposed, sens, threshold):
    """Explain a zero instead of showing a bare 0.

    At a demanding default (70%) a region can hold a strong signal at 50% and
    exactly zero at 70% — Zimbabwe is 7.2M children at 50% and 0 at 65%. A
    lone "0" reads as "no risk here" when it means "nothing clears this bar",
    so when a lower threshold would return children, say which one.
    """
    if exposed:
        return None
    hits = sorted(((float(k), v) for k, v in (sens or {}).items() if v),
                  key=lambda kv: kv[0], reverse=True)
    if not hits:
        return html.Div(className="en-empty", children=[
            "No children fall under this signal at any threshold tested — ",
            "this region has no clear signal for the selected period, not a ",
            "missing result.",
        ])
    best_thr, best_val = hits[0]
    return html.Div(className="en-empty", children=[
        html.Strong("Nothing clears this threshold."),
        f" No children are in a {int(threshold)}% signal area here. Lowering "
        f"it to ", html.Strong(f"{int(best_thr)}%"), " would return ",
        html.Strong(_fmt(int(round(best_val)))),
        " children — so this is a demanding cutoff, not an absence of signal. "
        "The row below shows the full range.",
    ])


def _elnino_magnitude_block(meta):
    """How far from normal, and how much of it looks like El Nino.

    The probability answers "how likely"; these answer "how much" and "how
    attributable". Both are reported, neither filters the count.
    """
    anoms = meta.get("anoms") or {}
    attrib = meta.get("attrib")
    rows = []

    mm, pc = anoms.get("anom_mm"), anoms.get("anom_pct")
    if mm is not None:
        txt = f"{mm:+,.0f} mm"
        if pc is not None:
            txt += f"  ({pc:+.0f}% of normal)"
        rows.append(html.Div(className="info-row", children=[
            html.Span("Rainfall vs normal", className="info-lbl"),
            html.Span(txt, className="info-val"),
        ]))
    c = anoms.get("anom_c")
    if c is not None:
        rows.append(html.Div(className="info-row", children=[
            html.Span("Temperature vs normal", className="info-lbl"),
            html.Span(f"{c:+.1f} °C", className="info-val"),
        ]))
    if not rows and not attrib:
        return None

    kids = []
    if rows:
        kids.append(html.Div([
            html.Span("How unusual is it?"), _en_info("anomaly"),
        ], className="ps-label"))
        kids += rows
        kids.append(html.Div(
            "Averaged over the signal area, against the model's own "
            "1993–2016 normal. This is the size of the departure; the "
            "probability above is only how consistently the ensemble "
            "predicts it.",
            className="ps-caption", style={"marginTop": "6px"}))

    if attrib:
        kids.append(html.Div([
            html.Span("Historically linked to El Niño"),
            _en_info("enso_attrib"),
        ], className="ps-label", style={"marginTop": "12px"}))
        kids.append(html.Div(className="info-row", children=[
            html.Span("Children in El Niño-consistent areas",
                      className="info-lbl"),
            html.Span(f"{_fmt(attrib['children'])}  ({attrib['pct']:.0f}%)",
                      className="en-attrib-val"),
        ]))
        note = ("Where El Niño has historically pushed rainfall the same "
                "direction this forecast points. The rest may be unusual "
                "for other reasons.")
        if attrib.get("rainfall_only"):
            note += (" This mask covers rainfall only, so for a combined "
                     "signal it says nothing about whether the heat is "
                     "El Niño-related — treat it as the weaker claim.")
        if not is_window(meta.get("period")):
            note += (" A single month is read against its forecast issue's "
                     "seasonal mask, so this is approximate.")
        kids.append(html.Div(note, className="ps-caption",
                             style={"marginTop": "6px"}))

    return html.Div(className="ps", children=kids)


def render_elnino_results(result, meta, history=None):
    """Exposure, the threshold sensitivity, and the caveats that must travel.

    The sensitivity row is the important part. With 51 members, probability
    moves in steps of about 2 points, so a single figure implies a precision the
    ensemble does not have — showing 40/50/60% alongside it is what keeps the
    headline number honest.
    """
    if not result:
        return _elnino_error("No result.")

    exposed = int(round(result.get("exposed", 0) or 0))
    total   = int(round(result.get("total_population", 0) or 0))
    # Boys/girls in the signal area; *_t are region totals.
    male    = int(round(result.get("exposed_male", 0) or 0))
    fema    = int(round(result.get("exposed_female", 0) or 0))
    male_t  = int(round(result.get("total_population_male", 0) or 0))
    fema_t  = int(round(result.get("total_population_female", 0) or 0))
    masked  = int(round(result.get("masked_population", 0) or 0))
    pct     = (exposed / total * 100) if total else 0

    def _share(part, whole):
        return (part / whole * 100) if whole else 0

    sig = SIGNAL_MAP.get(meta["signal"], {})
    colour = sig.get("color", "#e31a1c")
    sens = result.get("_sensitivity") or {}


    # The history chart is AREA-based on both sides. `pct` above is a share of
    # CHILDREN and is NOT interchangeable with it — see forecast_area_share.
    area_share = meta.get("area_share")

    return html.Div([
        html.Div(className="metrics", children=[
            html.Div(className="metric", children=[
                html.Div(_fmt(exposed), className="metric-val",
                         style={"color": colour}),
                html.Div("Children in signal area", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(_fmt(total), className="metric-val"),
                html.Div("Total children", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(f"{pct:.1f}%", className="metric-val",
                         style={"color": colour}),
                html.Div("Share", className="metric-lbl"),
            ]),
            html.Div(className="metric", children=[
                html.Div(f"{int(meta['threshold'])}%", className="metric-val"),
                html.Div("Threshold", className="metric-lbl"),
            ]),
        ]),

        _elnino_empty_state(exposed, sens, meta["threshold"]),

        _elnino_skill_note(meta.get("skill")),

        _elnino_magnitude_block(meta),

        html.Div(className="ps", children=[
            html.Div(f"{sig.get('label', '')} — {period_label(meta['period'])}",
                     className="ps-label"),
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch",
                              style={"background": colour}),
                    (meta["region"] or "").upper(),
                ]),
                html.Span([
                    html.Span(f"{exposed:,}", className="info-val"),
                    html.Span(f" ({pct:.1f}%)", className="info-pct"),
                ]),
            ]),
            html.Div(className="bar-track", children=[
                html.Div(className="bar-fill",
                         style={"width": f"{min(100, pct)}%",
                                "background": colour}),
            ]),
            html.Div(className="info-row", children=[
                html.Span("└ Boys", className="info-lbl",
                          style={"paddingLeft": "22px", "color": "var(--lo)",
                                 "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span([
                    html.Span(f"{male:,}", className="info-val"),
                    html.Span(f" ({_share(male, male_t):.1f}% of boys)",
                              className="info-pct"),
                ], style={"fontSize": "0.85em"}),
            ]),
            html.Div(className="info-row", children=[
                html.Span("└ Girls", className="info-lbl",
                          style={"paddingLeft": "22px", "color": "var(--lo)",
                                 "fontWeight": "400", "fontSize": "0.85em"}),
                html.Span([
                    html.Span(f"{fema:,}", className="info-val"),
                    html.Span(f" ({_share(fema, fema_t):.1f}% of girls)",
                              className="info-pct"),
                ], style={"fontSize": "0.85em"}),
            ]),
        ]),

        # Sensitivity — the honest framing of a single number.
        html.Div(className="ps", children=[
            html.Div([
                html.Span("How much does the probability threshold matter?"),
                _en_info("sensitivity"),
            ], className="ps-label"),
            html.Div(className="en-sens", children=[
                html.Div(className=("en-sens-cell active"
                                    if abs(float(k) - meta["threshold"]) < 1e-9
                                    else "en-sens-cell"),
                         children=[
                    html.Div(f"{int(float(k))}%", className="en-sens-thr"),
                    html.Div(_fmt(int(round(v))), className="en-sens-val"),
                    html.Div(f"{(v / total * 100) if total else 0:.0f}%",
                             className="en-sens-pct"),
                ]) for k, v in sorted(sens.items(), key=lambda kv: float(kv[0]))
            ]),
            html.Div("Quote the range, not a single figure.",
                     className="ps-caption", style={"marginTop": "8px"}),
        ]),

        _elnino_history_block(history, area_share),

        # What was excluded, so a small total is never mistaken for no signal.
        (html.Div(className="ps", children=[
            html.Div("Excluded from the count", className="ps-label"),
            html.Div([
                html.Span(f"{masked:,}", className="info-val"),
                html.Span(" children live in areas too dry for a tercile to "
                          "mean anything, and are not counted above.",
                          className="ps-caption"),
            ]),
        ]) if masked else None),

        html.Div(className="ps", children=[
            html.Div([
                f"ECMWF SEAS5 system {ELNINO_SYSTEM}, initialised "
                f"{period_issue_label(meta['period'])}, hindcast {HINDCAST}. ",
                f"Population grid: {meta['pop_res']} m. ",
                html.Br(),
                html.Strong("A tercile signal is relative to this model's own "
                            "climatology — it is not a forecast of drought or "
                            "flooding."),
            ], className="ps-caption", style={"lineHeight": "1.7"}),
        ]),

        # These open the disclaimer; elnino_download builds the file.
        html.Div(className="ps", children=[
            html.Button(
                "⬇  Download Excel workbook",
                id="elnino-dl-xlsx-btn", n_clicks=0,
                className="ps-btn",
                style={"display": "block", "width": "100%",
                       "textAlign": "center", "padding": "9px 16px"},
            ),
            # JSON kept as the machine-readable form. Secondary styling: the
            # workbook is what a person wants, this is what a script wants.
            html.Button(
                "⬇  JSON (for scripts)",
                id="elnino-dl-json-btn", n_clicks=0,
                className="ps-btn-ghost",
                style={"display": "block", "width": "100%",
                       "textAlign": "center", "padding": "7px 16px",
                       "marginTop": "6px", "fontSize": "0.7rem"},
            ),
        ]),
    ])
