"""Small layout builders and formatters shared by sections."""
import datetime as dt
from config.hazards import (
    HAZARD_INFO, HAZARD_MAP, HAZARD_TOPICS, hazard_name_words, is_pop_layer,
    MHC_EXCLUDED_TOPICS, pop_class_of, POP_LAYER_MAP,
)
from dash import html

from dash_app import app
from i18n import (
    current_lang, fmt_day_range, fmt_num, LANG_BY_CODE, LANG_COOKIE, LANGUAGES, N_,
    SKIP_NOTICE_COOKIE, tr,
)
from ui.registry import DEFAULT_NAV, PHASES, SEGMENTED_VIEWS, TAB_BY_KEY, VIEWS


def _hazard_info_body(info):
    if not isinstance(info, dict):
        return info
    rows = []
    for label, key in [(N_("Description"), "description"), (N_("Units"), "units"),
                       (N_("Availability"), "availability"),
                       (N_("Resolution"), "resolution")]:
        val = info.get(key, "")
        if val:
            rows.append(html.Div([
                html.Span(tr(label), className="hi-label"),
                html.Span(tr(val),   className="hi-val"),
            ], className="hi-row"))
    source = info.get("source", "")
    url    = info.get("source_url", "")
    if source:
        rows.append(html.Div([
            html.Span(tr("Source"), className="hi-label"),
            html.A(source, href=url, target="_blank", className="hi-val hi-link") if url
            else html.Span(source, className="hi-val"),
        ], className="hi-row"))
    return rows


def _layer_label(name):
    if name in ("Multi Hazard Count", "Multi Hazard Intensity"):
        return tr(name)
    if is_pop_layer(name):
        return tr(POP_LAYER_MAP.get(pop_class_of(name), {}).get("label", name))
    # "heatwave_frequency_ecmwf_2014-2024" → "Heatwave Frequency Ecmwf 2014 2024"
    head, rest = hazard_name_words(name)
    return " ".join(([tr(head)] if head else []) + rest)


def _layer_meta(name):
    if name == "Multi Hazard Count":
        return tr("{n} hazard topics combined",
                  n=len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS))
    if name == "Multi Hazard Intensity":
        return tr("Pixel-based hazard score (MHI)")
    if is_pop_layer(name):
        return "WorldPop 2025 · 100 m"
    h = HAZARD_MAP.get(name)
    return h["id"].split("/")[-1] if h else ""


def _fmt(v):
    if v is None: return "—"
    if abs(v) >= 1_000_000: return fmt_num(v / 1e6, 2) + "M"
    if abs(v) >= 1_000:     return fmt_num(v / 1000, 1) + "k"
    if isinstance(v, float): return fmt_num(v, 2)
    return str(int(v))


def view_header_children(tab):
    t = TAB_BY_KEY[tab]
    return [html.Div(tr(t["title"]), className="ph-title"),
            html.Div(tr(t["desc"]),  className="ph-sub")]


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
                html.Span(tr(view["label"]), className="nav-label")]
    if view.get("soon"):
        children.append(html.Span(tr("soon"), className="nav-soon"))
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


def language_switcher():
    """Translate icon + current language code beside the app title. Menu and
    switching are handled by assets/i18n.js (sets the language cookie and
    reloads, so the whole layout re-renders in that language)."""
    lang = current_lang()
    opts = []
    for L in LANGUAGES:
        draft = L["status"] != "reviewed"
        opts.append(html.Button(
            [html.Span(L["name"]),
             *([html.Span(tr("draft"), className="lang-draft")] if draft else [])],
            lang=L["code"], role="menuitem",
            className="lang-opt active" if L["code"] == lang else "lang-opt",
            **{"data-lang": L["code"], "data-cookie": LANG_COOKIE,
               "data-skip-cookie": SKIP_NOTICE_COOKIE}))
    note = ([html.Div(tr("Draft translations are machine-assisted and awaiting "
                         "review; the English version is authoritative."),
                      className="lang-menu-note")]
            if LANG_BY_CODE[lang]["status"] != "reviewed" else [])
    return html.Div(id="lang-switch", className="lang-switch", children=[
        html.Button(
            [html.I(className="bi bi-translate"),
             html.Span(lang.upper(), className="lang-code")],
            id="lang-btn", className="lang-btn", title=tr("Language"),
            **{"aria-haspopup": "true"},
        ),
        html.Div(opts + note, className="lang-menu", role="menu"),
    ])


def panel_head():
    """Title, phase switch and the active view's segments — pinned at the top
    of the panel so the phase and view stay visible while the pane scrolls."""
    return html.Div(id="panel-head", children=[
        # The product name is a proper noun, kept in English in every language.
        html.Div([html.Div("Global Child Hazard Database", className="panel-title"),
                  language_switcher()], className="panel-title-row"),
        html.Div(className="phase-switch", role="tablist", children=[
            html.Button(
                [html.I(className=p["icon"]),
                 html.Span(tr(p["label"]), className="phase-label"),
                 *([] if p["enabled"] else
                   [html.Span(tr("soon"), className="phase-soon")])],
                id=f"phase-{p['key']}", n_clicks=0,
                className=phase_btn_class(p, DEFAULT_NAV),
                disabled=not p["enabled"],
                title=("" if p["enabled"] else
                       tr("Damage Assessment — planned, not yet available")),
            )
            for p in PHASES
        ]),
        *[html.Div(
            id=f"segbar-{v['key']}", className="view-segbar",
            style={"display": "flex" if v["key"] == DEFAULT_NAV["view"]
                   else "none"},
            children=[
                html.Button(tr(s["label"]), n_clicks=0,
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
    return fmt_day_range(a, b)


def _hazard_label(name):
    """Short hazard name: the translated descriptive head, plus the first
    identifier word when the head alone is ambiguous ("Drought Spei")."""
    head, rest = hazard_name_words(name)
    if not head:
        return " ".join(rest[:2])
    if len(head.split()) >= 2 or not rest:
        return tr(head)
    return f"{tr(head)} {rest[0]}"


POP_BASIS = N_("WorldPop under-18 gridded population, 2025, 100 m")


_ESTIMATE_CAVEAT = N_(
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
    lines = [tr("Population: {basis}", basis=tr(POP_BASIS)), *(extra_lines or [])]
    return html.Div(className="ps", children=[
        html.Div(tr("Methodology"), className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(tr(_ESTIMATE_CAVEAT), className="ps-caption"),
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
    thr_str = tr("thr {value}", value=f"{thr:g}")
    return thr_str + (f" {tr(units)}" if units else "")


def _no_data_panel(region_name):
    """Shown instead of figures where no hazard has usable data.

    Deliberately reports nothing numeric — not even a population total. The
    previous behaviour zeroed every field, which a reader cannot distinguish
    from a measured "no children are exposed here".
    """
    return html.Div(className="ps", children=[
        html.Div(tr("Data not available"), className="hi-label",
                 style={"marginBottom": "6px"}),
        html.Div(
            tr("Hazard and exposure data are not available for {region}, so no "
               "figures are reported. This reflects a gap in the underlying "
               "global datasets, not an absence of hazard or of children.",
               region=region_name),
            className="ps-caption", style={"marginBottom": "0"},
        ),
    ])
