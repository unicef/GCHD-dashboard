"""Small layout builders and formatters shared by sections."""
import datetime as dt
from config.hazards import (
    HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS, is_pop_layer, MHC_EXCLUDED_TOPICS,
    pop_class_of, POP_LAYER_MAP,
)
from dash import html

from dash_app import app
from ui.registry import DEFAULT_NAV, PHASES, SEGMENTED_VIEWS, TAB_BY_KEY, VIEWS


def _hazard_info_body(info):
    if not isinstance(info, dict):
        return info
    rows = []
    for label, key in [("Description", "description"), ("Units", "units"),
                       ("Availability", "availability"), ("Resolution", "resolution")]:
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
    if is_pop_layer(name):
        return POP_LAYER_MAP.get(pop_class_of(name), {}).get("label", name)
    # "flood_river_2yr" → "Flood River 2yr"
    return " ".join(w.capitalize() for w in name.replace("-", " ").split("_"))


def _layer_meta(name):
    if name == "Multi Hazard Count":
        return f"{len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)} hazard topics combined"
    if name == "Multi Hazard Intensity":
        return "Pixel-based hazard score (MHI)"
    if is_pop_layer(name):
        return "WorldPop 2025 · 100 m"
    h = HAZARD_MAP.get(name)
    return h["id"].split("/")[-1] if h else ""


def _fmt(v):
    if v is None: return "—"
    if abs(v) >= 1_000_000: return f"{v/1e6:.2f}M"
    if abs(v) >= 1_000:     return f"{v/1000:.1f}k"
    if isinstance(v, float): return f"{v:.2f}"
    return str(int(v))


def view_header_children(tab):
    t = TAB_BY_KEY[tab]
    return [html.Div(t["title"], className="ph-title"),
            html.Div(t["desc"],  className="ph-sub")]


def rail_btn_class(view, nav):
    cls = ["nav-btn"]
    if view["key"] == nav["view"]:
        cls.append("active")
    if view["phase"] not in (None, nav["phase"]):
        cls.append("off-phase")
    if view.get("soon"):
        cls.append("soon")
    return " ".join(cls)


def nav_btn(view, nav):
    children = [html.I(className=view["icon"]),
                html.Span(view["label"], className="nav-label")]
    if view.get("soon"):
        children.append(html.Span("soon", className="nav-soon"))
    return html.Button(children, id=f"nav-{view['key']}",
                       className=rail_btn_class(view, nav), n_clicks=0)


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
                src=app.get_asset_url("unicef_logo.webp") + "?v=3",
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
            nav_btn(v, DEFAULT_NAV) for v in VIEWS if v["phase"]
        ]),
        html.Div(className="sb-nav sb-foot", children=[
            nav_btn(v, DEFAULT_NAV) for v in VIEWS if not v["phase"]
        ]),
    ])


def phase_btn_class(phase, nav):
    return "phase-btn active" if phase["key"] == nav["phase"] else "phase-btn"


def seg_btn_class(view_key, tab, nav):
    on = view_key == nav["view"] and tab == nav["tab"]
    return "view-seg active" if on else "view-seg"


def panel_head():
    """Title, phase switch and the active view's segments — pinned at the top
    of the panel so the phase and view stay visible while the pane scrolls."""
    return html.Div(id="panel-head", children=[
        html.Div("Global Child Hazard Database", className="panel-title"),
        html.Div(className="phase-switch", role="tablist", children=[
            html.Button(
                [html.I(className=p["icon"]),
                 html.Span(p["label"], className="phase-label"),
                 *([] if p["enabled"] else
                   [html.Span("soon", className="phase-soon")])],
                id=f"phase-{p['key']}", n_clicks=0,
                className=phase_btn_class(p, DEFAULT_NAV),
                disabled=not p["enabled"],
                title=("" if p["enabled"] else
                       "Damage Assessment — planned, not yet available"),
            )
            for p in PHASES
        ]),
        *[html.Div(
            id=f"segbar-{v['key']}", className="view-segbar",
            style={"display": "flex" if v["key"] == DEFAULT_NAV["view"]
                   else "none"},
            children=[
                html.Button(s["label"], n_clicks=0,
                            id={"type": "seg-btn", "view": v["key"],
                                "tab": s["tab"]},
                            className=seg_btn_class(v["key"], s["tab"],
                                                    DEFAULT_NAV))
                for s in v["segments"]
            ])
          for v in SEGMENTED_VIEWS],
    ])


def _as_float(v, default=0.0):
    try:
        return float(v if v not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _fmt_window(start, end):
    try:
        a = dt.date.fromisoformat(str(start)[:10])
        b = dt.date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return ""
    if a == b:
        return a.strftime("%d %b %Y")
    left = a.strftime("%d %b" if a.year == b.year else "%d %b %Y")
    return f"{left} – {b.strftime('%d %b %Y')}"


def _hazard_label(name):
    words = [w.capitalize() for w in name.replace("-", " ").split("_")]
    return " ".join(words[:2])


POP_BASIS = "WorldPop under-18 gridded population, 2025, 100 m"


_ESTIMATE_CAVEAT = (
    "Figures are the modelled child population in areas where the hazard "
    "exceeds the stated threshold — indicative of relative exposure, not "
    "observed impacts."
)


def _info_icon(hazard_name, scope="an"):
    """Inline ⓘ opening the shared layer-info popover.

    A distinct `type` from the Layers tab's buttons, and a `scope`-prefixed
    index: that tab renders one button per hazard already, and the Analysis and
    Infrastructure panels both persist in the layout at once, so an unprefixed
    id would put duplicate component ids on the page for the same hazard.
    """
    return html.Button(
        html.I(className="bi bi-info-circle"),
        id={"type": "prov-info-btn", "index": f"{scope}:{hazard_name}"},
        className="info-icon-btn", n_clicks=0,
        title=_layer_label(hazard_name),
    )



def _provenance_block(extra_lines=None):
    """Methodology section for a results panel.

    Numbers get copied into slides and emails, so the basis travels with them.
    Shared by Analysis and Infrastructure so the two panels can never state it
    differently.
    """
    lines = [f"Population: {POP_BASIS}", *(extra_lines or [])]
    return html.Div(className="ps", children=[
        html.Div("Methodology", className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(_ESTIMATE_CAVEAT, className="ps-caption"),
        html.Div([html.Div(t) for t in lines],
                 className="ps-caption", style={"lineHeight": "1.7",
                                                "marginBottom": "0"}),
    ])


def _eff_threshold(h_name, overrides):
    """Effective threshold + unit label for a hazard, given the user overrides."""
    default = HAZARD_MAP.get(h_name, {}).get("threshold")
    thr     = (overrides or {}).get(h_name, default)
    units   = (HAZARD_INFO.get(h_name) or {}).get("units", "")
    if thr is None:
        return ""
    thr_str = f"{thr:g}"
    return f"thr {thr_str}" + (f" {units}" if units else "")


def _no_data_panel(region_name):
    """Shown instead of figures where no hazard has usable data.

    Deliberately reports nothing numeric — not even a population total. The
    previous behaviour zeroed every field, which a reader cannot distinguish
    from a measured "no children are exposed here".
    """
    return html.Div(className="ps", children=[
        html.Div("Data not available", className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(
            f"Hazard and exposure data are not available for {region_name}, "
            "so no figures are reported. This reflects a gap in the underlying "
            "global datasets, not an absence of hazard or of children.",
            className="ps-caption", style={"marginBottom": "0"},
        ),
    ])
