"""Infra analysis (Preparedness and Early Warning)."""
import dash
from config.hazards import (
    _infra_type_of, EXPOSURE_ONLY_TOPICS, HAZARD_INFO, HAZARD_TOPICS,
    infra_layer_style, is_no_data_ucode, SUB_TOPIC_DETAIL, TOPIC_COLORS,
)
from dash import ALL, ctx, dcc, html, Input, no_update, Output, State
from core.forecast import ForecastError, get_live_hazard_area_tile
from core.gee import (
    compute_facility_combined, get_asset_bounds, get_asset_info,
    get_clipped_pop_tile_url, get_country_bounds, get_country_ucode,
    get_feature_bounds, get_infra_tile_url, get_topic_tile_url_clipped,
    infra_asset_id, infra_countries, infra_layers_for,
)

from dash_app import app
from i18n import fmt_int, fmt_pct, N_, tr
from bootstrap import ANALYSIS_TOPIC_SLOTS
from ui.components import (
    _eff_threshold, _fmt, _hazard_label, _info_icon, _no_data_panel,
    _provenance_block,
)
from ui.threshold import build_threshold_editor_for, _threshold_overrides
from ui.xlsx import build_xlsx, _thresholds_banner
from sections.ew_shared import (
    _build_live, LIVE_HAZARD_COLOR, _live_provenance, _live_spec,
    _live_summary,
)


def _infra_color_for_asset(asset_id):
    """Map an infrastructure asset id to its facility-type colour (no '#').

    The asset id ends in {iso3}_{source}_{layer}, so the facility type is
    readable straight from the name — no need to scan the discovered assets.
    Falls back to the neutral colour for a custom/unrecognised asset."""
    stem = (asset_id or "").split("/")[-1]
    for layer, label in (("health_facilities", "Health Facilities"),
                         ("water_points",      "Water Points"),
                         ("schools",           "Schools")):
        if stem.endswith("_" + layer):
            return infra_layer_style(label)["color"].lstrip("#")
    return "e67e22"


def _infra_layer_options(country=None):
    """Layer dropdown options for the selected country — only its uploaded,
    source-qualified layers (e.g. 'Schools (Giga)'). Empty until a country with
    assets is picked. Discovered from GEE, so a newly uploaded layer appears
    without a code change."""
    return [{"label": _infra_layer_label(name), "value": name}
            for name in infra_layers_for(country)]


def _infra_layer_label(name):
    """'Schools (Giga)' -> translated facility type + untranslated source."""
    base = _infra_type_of(name)
    return tr(base) + name[len(base):] if name.startswith(base) else name


def tab_infrastructure():
    return html.Div(id="tab-infrastructure", className="infra-mode-prep",
                    style={"display": "none"}, children=[

        # 1. Region — own selector that writes to the shared region stores.
        # Analyses are adm2-only, so selecting a country hard-sets adm2 and
        # enables map district selection immediately (no level dropdown).
        html.Div(className="ps", children=[
            html.Div(tr("1. Select country / territory"), className="ps-label"),
            dcc.Dropdown(
                id="infra-country-select", className="ps-select",
                # Populated by infra_populate_countries on tab entry, not here:
                # tab_infrastructure() runs once at import, so baking the list
                # in would freeze it at server start and defeat discovery.
                options=[],
                value=None, placeholder=tr("Search country / territory…"),
                clearable=False, searchable=True,
            ),
        ]),
        html.Div(id="infra-level-section", style={"display": "none"}, children=[
            html.Div(id="infra-selected-badge-wrap"),
            html.Div(tr("Click a district on the map to analyze."),
                     className="info-box", id="infra-region-hint"),
        ]),

        # 2. Facility layer — options depend on the selected country (only its
        # uploaded, source-qualified layers). Populated on country select.
        html.Div(className="ps", children=[
            html.Div(tr("2. Facility layer"), className="ps-label"),
            dcc.Dropdown(
                id="infra-layer-select", className="ps-select",
                options=[], value=None,
                placeholder=tr("— select a country first —"),
                clearable=True, searchable=False,
            ),
            html.Button(
                [html.I(className="bi bi-plus-circle"),
                 html.Span(tr("Add your own facility data"))],
                id="infra-add-layer-btn", className="link-btn", n_clicks=0,
            ),
            html.Div(style={"display": "flex", "gap": "6px", "marginTop": "8px"}, children=[
                dcc.Input(
                    id="infra-asset-input",
                    placeholder=tr("…or paste a GEE asset path"),
                    debounce=False,
                    style={"flex": "1", "padding": "7px 9px", "fontSize": "0.75rem",
                           "border": "1px solid var(--border2)", "borderRadius": "6px",
                           "fontFamily": "Source Sans Pro, sans-serif",
                           "background": "var(--panel-h)", "color": "var(--hi)"},
                ),
                html.Button(tr("Load"), id="infra-asset-load-btn", className="ps-btn",
                            n_clicks=0,
                            style={"width": "auto", "padding": "7px 14px", "flexShrink": "0"}),
            ]),
            html.Div(id="infra-asset-status",
                     style={"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--mid)"}),
        ]),

        # 3. Hazard topics — Preparedness only; in EW the Hazard card above
        # the pane holds the hazard (#tab-infrastructure.infra-mode-ew).
        html.Div(id="infra-hazard-prep", className="ps", children=[
            html.Div(tr("3. Hazard topics"), className="ps-label"),
            dcc.Dropdown(
                id="infra-topic-select", className="ps-select",
                options=[{"label": tr(t), "value": t} for t in HAZARD_TOPICS
                         if t not in EXPOSURE_ONLY_TOPICS],
                value=[], multi=True, placeholder=tr("All hazards (default)"),
                clearable=True, searchable=False,
            ),
            html.Div(tr("Exposed population is attributed to each facility's "
                     "nearest-neighbour (Voronoi) catchment across the district."),
                     className="ps-caption"),
            # Collapsible threshold editor (mirrors the Analysis tab).
            html.Div(id="infra-thr-toggle", className="athr-toggle", n_clicks=0,
                     children=[
                html.I(className="bi bi-chevron-right", id="infra-thr-chevron"),
                html.Span(tr("Adjust thresholds"), className="athr-toggle-label"),
                html.Span("", id="infra-thr-badge", className="athr-badge"),
            ]),
            html.Div(id="infra-thr-body", style={"display": "none"}, children=[
                html.Div(id="infra-threshold-block",
                         className="analysis-threshold-block"),
            ]),
        ]),

        html.Div(className="ps", children=[
            html.Div(className="infra-help", style={"display": "flex", "gap": "6px",
                     "alignItems": "flex-start", "marginBottom": "8px"}, children=[
                html.I(className="bi bi-info-circle",
                       style={"color": "var(--cyan)", "marginTop": "2px",
                              "flexShrink": "0"}),
                html.Div(className="ps-caption", style={"color": "var(--mid)"}, children=[
                    html.Div([html.Strong(tr("How to run:") + " "),
                              tr("click a district (ADM2) on the map, then press Compute. "
                              "Analysis is per-district — there is no country-wide "
                              "summary, because per-facility Voronoi computation over "
                              "many points is slow even for small areas.")]),
                    html.Div([html.Strong(tr("Thresholds:") + " "),
                              tr("the summary applies your current hazard thresholds set "
                              "above.")], style={"marginTop": "4px"}),
                ]),
            ]),
            html.Button(tr("Compute"), id="infra-compute-btn", className="ps-btn",
                        n_clicks=0, disabled=True),
        ]),
        dcc.Loading(
            id="infra-results-loading", type="circle", color="#1CABE2",
            children=html.Div(id="infra-results-panel"),
        ),
        # Per-facility CSV download (phase 2) — spinner until ready.
        html.Div(className="ps", children=[
            dcc.Loading(
                id="infra-download-loading", type="circle", color="#1CABE2",
                children=html.Div(id="infra-download-wrap"),
            ),
        ]),

        # — Stores — the facility asset is empty until the user picks a country
        # and a layer (layers are country-specific now, so there's no sensible
        # global default to seed). infra_load_asset sets it on selection.
        dcc.Store(id="store-infra-asset", data=None),
        # Last computed viz (asset/ucode/buffer/color) — lets update_data_layers
        # rebuild the infra tiles so they aren't wiped on store/tab changes.
        dcc.Store(id="store-infra-viz", data=None),
        # Phase-2 trigger: per-facility download computed after the summary.
        dcc.Store(id="store-infra-pending", data=None),
    ])


@app.callback(
    Output({"type": "infra-topic-tile", "index": ALL}, "url", allow_duplicate=True),
    Input("store-infra-viz", "data"),
    State({"type": "infra-topic-tile", "index": ALL}, "url"),
    prevent_initial_call=True,
)
def infra_reset_topic_tiles(viz, urls):
    """Hazard-area slots are fetched once, on first reveal; any new result (or
    none) empties them so a reveal never shows the previous district's tile.
    An EW result's single live hazard area starts visible, so it is fetched
    now."""
    out = [""] * len(urls)
    live = (viz or {}).get("live")
    if live and viz.get("topics") and out:
        try:
            out[0] = get_live_hazard_area_tile(
                live["name"], live["start"], live["end"], live["reducer"],
                live["threshold"], viz["ucode"], LIVE_HAZARD_COLOR,
                custom_id=live.get("custom_id"),
                custom_band=live.get("custom_band"))
        except Exception:
            pass
    return out


# ── Infrastructure threshold editor ───────────────────────────────────────────

def _infra_editor_topics(topics):
    """Infra topic-select is empty for 'all hazards' — expand to all infra topics."""
    if topics:
        return topics
    return [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]


@app.callback(
    Output("infra-threshold-block", "children"),
    Input("infra-topic-select",     "value"),
)
def build_infra_threshold_editor(topics):
    return build_threshold_editor_for("infra", _infra_editor_topics(topics))


@app.callback(
    Output("infra-thr-body",    "style"),
    Output("infra-thr-chevron", "className"),
    Input("infra-thr-toggle",   "n_clicks"),
    prevent_initial_call=True,
)
def toggle_infra_thr(n):
    open_ = bool(n) and (n % 2 == 1)
    body  = {"display": "block"} if open_ else {"display": "none"}
    chev  = "bi bi-chevron-down" if open_ else "bi bi-chevron-right"
    return body, chev


@app.callback(
    Output("infra-thr-badge", "children"),
    Input({"type": "infra-threshold", "index": ALL}, "value"),
    State({"type": "infra-threshold", "index": ALL}, "id"),
    State("infra-topic-select", "value"),
)
def infra_thr_badge(values, ids, topics):
    overrides, _ = _threshold_overrides(_infra_editor_topics(topics), ids, values)
    return tr("{n} changed", n=len(overrides)) if overrides else ""


@app.callback(
    Output({"type": "infra-topic-tile", "index": ALL}, "url"),
    Input({"type": "legend-eye", "index": ALL}, "n_clicks"),
    State({"type": "infra-topic-tile", "index": ALL}, "url"),
    State("store-infra-viz", "data"),
    prevent_initial_call=True,
)
def reveal_infra_topic(_eye_clicks, existing_urls, viz):
    """Infrastructure-tab twin of reveal_analysis_topic: fetch a topic's clipped
    hazard tile the first time its legend eye is switched on, then never again.
    """
    triggered = ctx.triggered_id
    if not viz or not isinstance(triggered, dict):
        raise dash.exceptions.PreventUpdate

    # The legend eyes are shared across tabs; only infra rows matter here.
    layer_id = str(triggered.get("index", ""))
    if not layer_id.startswith("infra-topic-"):
        raise dash.exceptions.PreventUpdate
    try:
        slot = int(layer_id.rsplit("-", 1)[1])
    except ValueError:
        raise dash.exceptions.PreventUpdate

    topics = viz.get("topics") or []
    if slot >= len(topics) or slot >= len(existing_urls):
        raise dash.exceptions.PreventUpdate
    if existing_urls[slot]:
        raise dash.exceptions.PreventUpdate      # already fetched

    topic   = topics[slot]
    thr_key = tuple((h, v) for h, v in (viz.get("thr") or []))
    live    = viz.get("live")
    try:
        if live:
            url = get_live_hazard_area_tile(
                live["name"], live["start"], live["end"], live["reducer"],
                live["threshold"], viz["ucode"], LIVE_HAZARD_COLOR,
                custom_id=live.get("custom_id"),
                custom_band=live.get("custom_band"))
        else:
            url, _ = get_topic_tile_url_clipped(
                topic, TOPIC_COLORS.get(topic, "#888888"),
                viz["ucode"], viz.get("level") or "adm2 (Districts/Counties)",
                thr_key)
    except Exception:
        raise dash.exceptions.PreventUpdate

    out = list(existing_urls)
    out[slot] = url
    return out


# ---------------------------------------------------------------------------
# Infrastructure tab
# ---------------------------------------------------------------------------

# ── Country dropdown (discovered from GEE, not hardcoded) ────────────────────

@app.callback(
    Output("infra-country-select", "options"),
    Output("infra-country-select", "placeholder"),
    Input("store-tab", "data"),
)
def infra_populate_countries(tab):
    """Fill the country dropdown from the assets actually in the GEE
    infrastructure folder.

    Runs on tab entry rather than at import so a country uploaded while the
    server is running appears without a restart (discovery is cached with a
    short TTL). A discovery failure yields an empty list and an explanatory
    placeholder instead of an error page.
    """
    if tab != "infrastructure":
        return no_update, no_update
    try:
        countries = infra_countries()
    except Exception as e:
        print(f"[infra] country discovery failed: {e}")
        countries = []
    if not countries:
        return [], tr("— no infrastructure data available —")
    return ([{"label": c, "value": c} for c in countries],
            tr("Search country / territory…"))


# ── Region selection (writes the shared region stores) ────────────────────────

@app.callback(
    Output("store-country",       "data", allow_duplicate=True),
    Output("store-ucode",         "data", allow_duplicate=True),
    Output("store-bounds",        "data", allow_duplicate=True),
    Output("store-level",         "data", allow_duplicate=True),
    Output("store-clicked-ucode", "data", allow_duplicate=True),
    Output("store-clicked-name",  "data", allow_duplicate=True),
    Output("infra-level-section", "style"),
    Output("infra-layer-select",  "options"),
    Output("infra-layer-select",  "value"),
    Output("infra-layer-select",  "placeholder"),
    Input("infra-country-select", "value"),
    prevent_initial_call=True,
)
def infra_on_country_select(country):
    # Analyses are adm2-only: selecting a country hard-sets the adm2 level and
    # zooms to the country, so the map is immediately clickable for districts.
    # Also repopulate the facility-layer dropdown with THIS country's uploaded,
    # source-qualified layers (empty if none are ingested yet).
    if not country:
        return (None, None, None, None, None, None, {"display": "none"},
                [], None, tr("— select a country first —"))
    ucode  = get_country_ucode(country)
    bounds = get_country_bounds(ucode)
    options = _infra_layer_options(country)
    value   = options[0]["value"] if options else None
    placeholder = (tr("— select a facility layer —") if options
                   else tr("— no infrastructure data for this country yet —"))
    return (country, ucode, bounds, "adm2 (Districts/Counties)", None, None,
            {"display": "block"}, options, value, placeholder)


@app.callback(
    Output("infra-selected-badge-wrap", "children"),
    Output("infra-region-hint",         "style"),
    Input("store-clicked-name",         "data"),
    State("store-tab",                  "data"),
)
def infra_update_badge(name, tab):
    if not name or tab != "infrastructure":
        return None, ({"display": "block"} if tab == "infrastructure"
                      else no_update)
    badge = html.Div(className="selected-badge", children=[
        html.Div(tr("Selected region"), className="selected-badge-tag"),
        html.Div(name, className="selected-badge-name"),
    ])
    return badge, {"display": "none"}  # hide the "click a district" hint


# ── Compute button enable/disable ─────────────────────────────────────────────

@app.callback(
    Output("infra-compute-btn",  "disabled"),
    Input("store-clicked-ucode", "data"),
    Input("store-level",         "data"),
)
def infra_toggle_compute(clicked_ucode, level):
    return not (clicked_ucode and level and level != "adm0 (Country)")


# ── Facility-layer / asset selection → resolve asset id, draw points ──────────

@app.callback(
    Output("store-infra-asset",       "data"),
    Output("infra-asset-status",      "children"),
    Output("infra-asset-status",      "style"),
    Output("infra-points-tile",       "url",      allow_duplicate=True),
    Output("main-map",                "viewport", allow_duplicate=True),
    Input("infra-layer-select",       "value"),
    Input("infra-asset-load-btn",     "n_clicks"),
    Input("store-tab",                "data"),
    Input("store-country",            "data"),
    State("infra-asset-input",        "value"),
    State("store-infra-viz",          "data"),
    prevent_initial_call=True,
)
def infra_load_asset(layer_name, _n, tab, country, asset_input, infra_viz):
    """Picking/loading a facility layer previews ALL points for the country
    (no AOI) on the dedicated infra-points-tile. infra_compute later overwrites
    it with the AOI-clipped points. Same path for every layer → uniform.
    Also fires on entering the Infra tab so the default (Schools) draws.
    The asset is resolved for the selected country from the discovered
    infrastructure assets."""
    err_style = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--red)"}
    ok_style  = {"fontSize": "0.72rem", "marginTop": "8px", "color": "var(--mid)"}

    trig = ctx.triggered_id
    fit  = True  # fit the map to the asset bounds (suppressed on tab-entry)

    if trig == "store-tab":
        # Entering the Infra tab: draw the currently-selected layer's points
        # without yanking the viewport. Skip if results are already computed
        # (store-infra-viz set) so we don't overwrite the AOI-clipped points.
        if tab != "infrastructure" or not layer_name or infra_viz:
            return no_update, no_update, no_update, no_update, no_update
        asset_id = infra_asset_id(country, layer_name)
        if not asset_id:
            return no_update, no_update, no_update, no_update, no_update
        color    = infra_layer_style(layer_name)["color"].lstrip("#")
        label    = layer_name
        fit      = False
    elif trig in ("infra-layer-select", "store-country"):
        # Layer picked, or country switched: reload the layer for that country.
        # On a country switch, infra_on_country_select already zooms to the
        # country via store-bounds, so don't also fit to the asset bounds.
        # store-country is shared with the Analysis tab, so ignore its changes
        # unless we're actually on the Infrastructure tab.
        if trig == "store-country" and tab != "infrastructure":
            return no_update, no_update, no_update, no_update, no_update
        if not layer_name:
            return None, "", ok_style, "", no_update
        asset_id = infra_asset_id(country, layer_name)
        if not asset_id:
            return None, tr("Select a country first."), ok_style, "", no_update
        color    = infra_layer_style(layer_name)["color"].lstrip("#")
        label    = layer_name
        if trig == "store-country":
            fit = False
    else:
        if not asset_input or not asset_input.strip():
            return no_update, no_update, no_update, no_update, no_update
        asset_id = asset_input.strip()
        color    = "e67e22"
        label    = asset_id.split("/")[-1]

    try:
        n, props = get_asset_info(asset_id)
        tile_url = get_infra_tile_url(asset_id, color)   # whole country (no AOI)
        bounds   = get_asset_bounds(asset_id)
    except Exception as e:
        return None, tr("Error loading asset: {e}", e=e), err_style, no_update, no_update

    status   = "✓  " + tr("{n} features — {name}", n=fmt_int(n),
                          name=_infra_layer_label(label))
    viewport = {"bounds": bounds, "transition": "fitBounds"} if fit else no_update
    return ({"asset_id": asset_id, "name_field": "name", "props": props},
            status, ok_style, tile_url, viewport)


def _infra_download(groups, rows, filename, banner_lines=None):
    """Secondary (ghost) styled-Excel download link, below the visual summary."""
    return html.A(
        "⬇  " + tr("Download full data (Excel)"),
        href=build_xlsx(tr("Facility exposure"), groups, rows, banner_lines),
        download=filename, className="ps-btn-ghost",
        style={"display": "block", "textAlign": "center", "textDecoration": "none",
               "padding": "9px 16px", "marginTop": "10px"},
    )


# Disclaimer shown in the panel and embedded in the CSV: per-facility figures
# come from each facility's own (overlapping) buffer and must not be summed.
INFRA_PERFAC_DISCLAIMER = N_(
    "Per-facility figures count children within each facility's individual "
    "buffer; buffers overlap, so these values may double-count and must not be "
    "summed. Use the dissolved-buffer summary above for district totals."
)


def _infra_error(msg):
    return html.Div(msg, style={"color": "var(--red)", "fontSize": "0.75rem",
                                "padding": "12px 16px"})


def _metric(value, label, color=None):
    style = {"color": color} if color else {}
    return html.Div(className="metric", children=[
        html.Div(value, className="metric-val", style=style),
        html.Div(label, className="metric-lbl"),
    ])


def _bar_row(label, count, pct, color):
    """Ranked bar row mirroring the Exposure tab (info-row + bar-track)."""
    return html.Div([
        html.Div(className="info-row", children=[
            html.Span(className="info-lbl", children=[
                html.Span(className="topic-swatch", style={"background": color}),
                label,
            ]),
            html.Span([
                html.Span(fmt_int(count), className="info-val"),
                html.Span(f" ({fmt_pct(pct)})", className="info-pct"),
            ]),
        ]),
        html.Div(className="bar-track", children=[
            html.Div(className="bar-fill",
                     style={"width": f"{min(100, pct)}%", "background": color}),
        ]),
    ])


def _infra_panel(title, metric_cards, bar_rows, download, caption=None):
    children = [html.Div(title, className="ps-label")]
    if caption:
        children.append(html.Div(caption, className="ps-caption"))
    if metric_cards:
        children.append(html.Div(className="metrics", children=metric_cards))
    children.extend(bar_rows)
    if download is not None:
        children.append(download)
    return html.Div(className="ps", children=children)


def _infra_combined_items(per_topic, per_sub, site_tally, sel_topics, total,
                          overrides=None):
    """Merged per-hazard rows: exposed children (bar) + '· N facilities' from
    the facility-site tally, with subhazard child-population detail rows. Each
    row shows the effective threshold used (custom overrides honored)."""
    tdata = sorted(
        ({"topic": t,
          "pop":  int(round((per_topic or {}).get(t) or 0)),
          "fac":  int(round((site_tally or {}).get(t) or 0))}
         for t in sel_topics),
        key=lambda d: d["pop"], reverse=True,
    )
    items = []
    for td in tdata:
        topic = td["topic"]
        pct   = (td["pop"] / total * 100) if total else 0
        color = TOPIC_COLORS.get(topic, LIVE_HAZARD_COLOR)
        # Single-hazard topics show the threshold inline; multi-hazard topics
        # show it per-subhazard on the detail rows below.
        hazards = HAZARD_TOPICS.get(topic, [])
        thr_txt = _eff_threshold(hazards[0], overrides) if len(hazards) == 1 else ""
        # Bar row with an extra facility-count suffix.
        header = html.Div([
            html.Div(className="info-row", children=[
                html.Span(className="info-lbl", children=[
                    html.Span(className="topic-swatch", style={"background": color}),
                    _info_icon(hazards[0], "infra") if hazards else None,
                    tr(topic).upper(),
                ]),
                html.Span([
                    html.Span(fmt_int(td["pop"]), className="info-val"),
                    html.Span(f" ({fmt_pct(pct)})", className="info-pct"),
                    html.Span("  · " + tr("{n} fac.", n=fmt_int(td["fac"])),
                              className="info-pct",
                              style={"color": "var(--lo)"}),
                ]),
            ]),
            (html.Div(thr_txt, className="info-pct",
                      style={"color": "var(--lo)", "fontSize": "0.7rem",
                             "marginTop": "1px"}) if thr_txt else None),
            html.Div(className="bar-track", children=[
                html.Div(className="bar-fill",
                         style={"width": f"{min(100, pct)}%", "background": color}),
            ]),
        ])
        if topic in SUB_TOPIC_DETAIL:
            sub_rows = []
            for h in HAZARD_TOPICS[topic]:
                hc = int(round((per_sub or {}).get(h) or 0))
                thr = _eff_threshold(h, overrides)
                sub_rows.append(html.Div(className="info-row", children=[
                    html.Span(["└ ",
                               _info_icon(h, "infra"),
                               _hazard_label(h),
                               html.Span(f"  {thr}" if thr else "",
                                         style={"color": "var(--lo)",
                                                "fontSize": "0.9em"})],
                              className="info-lbl",
                              style={"paddingLeft": "22px", "color": "var(--lo)",
                                     "fontWeight": "400", "fontSize": "0.85em"}),
                    html.Span(fmt_int(hc) if hc else "—", className="info-val",
                              style={"color": "var(--mid)", "fontWeight": "500"}),
                ]))
            items.append(html.Details(open=True, className="topic-detail", children=[
                html.Summary(header, className="topic-detail-summary"),
                *sub_rows,
            ]))
        else:
            items.append(html.Div(header))
    return items


def _intensity_label(h, live_spec):
    if live_spec:
        units = tr(live_spec.get("units"))
        label = tr(live_spec["label"])
        return f"{label} ({units})" if units else label
    units = tr((HAZARD_INFO.get(h) or {}).get("units"))
    return f"{_hazard_label(h)} ({units})" if units else _hazard_label(h)


@app.callback(
    Output("infra-results-panel", "children"),
    Output("infra-pop-tile",      "url"),
    Output("infra-points-tile",   "url"),
    Output("infra-voronoi-geojson","data",     allow_duplicate=True),
    Output("store-infra-viz",     "data"),
    Output("store-infra-pending", "data"),
    Output("infra-download-wrap", "children"),
    Output("main-map",            "viewport", allow_duplicate=True),
    Input("infra-compute-btn",    "n_clicks"),
    State("store-infra-asset",    "data"),
    State("store-clicked-ucode",  "data"),
    State("store-clicked-name",   "data"),
    State("store-level",          "data"),
    State("infra-topic-select",   "value"),
    State({"type": "infra-threshold", "index": ALL}, "value"),
    State({"type": "infra-threshold", "index": ALL}, "id"),
    State("store-view",              "data"),
    State("forecast-dataset-select", "value"),
    State("store-forecast-asset",    "data"),
    State("forecast-dates",          "start_date"),
    State("forecast-dates",          "end_date"),
    State("forecast-reducer",        "value"),
    State("forecast-threshold",      "value"),
    prevent_initial_call=True,
)
def infra_compute(_n, asset, adm2_ucode, region_name, level, topics,
                  thr_values, thr_ids, view, fc_dataset, fc_asset, fc_start,
                  fc_end, fc_reducer, fc_threshold):
    """Single combined analysis: exposed population (Voronoi catchments) +
    exposed facilities (site hazard). Renders panel + map; per-facility CSV in
    phase 2."""
    nu = (no_update,) * 7
    if not asset:
        return (_infra_error(tr("Select or load a facility layer first.")), *nu)
    if not adm2_ucode or not level or level == "adm0 (Country)":
        return (_infra_error(tr("Select an adm2 region and click a district on "
                                "the map to bound the analysis.")), *nu)
    # Districts inherit their country's data availability: the adm2 ucode
    # carries the same stem, so this catches them without a separate list.
    if is_no_data_ucode(adm2_ucode):
        return (_no_data_panel(region_name or tr("this region")), *nu)
    asset_id = asset["asset_id"]
    color = _infra_color_for_asset(asset_id)

    live_spec = None
    if view == "ew-infra":
        live_spec = _live_spec(fc_dataset, fc_asset, fc_start, fc_end,
                               fc_reducer, fc_threshold)
        if not live_spec:
            return (_infra_error(tr("Choose an Observed or Forecast layer in "
                                    "Visualization first.")), *nu)
        topics_sel, overrides = None, {}
    else:
        topics_sel = topics or None
        overrides, _ = _threshold_overrides(_infra_editor_topics(topics),
                                            thr_ids, thr_values)

    try:
        live = _build_live(live_spec) if live_spec else None
        r = compute_facility_combined(asset_id, adm2_ucode, topics_sel,
                                      threshold_overrides=overrides or None,
                                      live=live)
    except ForecastError as e:
        return (_infra_error(str(e)), *nu)
    except Exception as e:
        return (_infra_error(tr("GEE error: {e}", e=e)), *nu)

    sel_topics = r.get("topics") or [t for t in HAZARD_TOPICS
                                     if t not in EXPOSURE_ONLY_TOPICS]
    nfac    = int(r.get("n_facilities") or 0)
    total   = int(round(r.get("served_children_total") or 0))
    exposed = int(round(r.get("served_children_exposed") or 0))
    site_tally = r.get("facility_site_tally") or {}
    n_site_exp = sum(1 for f in r.get("facilities_site", [])
                     if any(int(f["properties"].get(t) or 0) for t in sel_topics))
    cards = [
        _metric(fmt_int(nfac), tr("Facilities")),
        _metric(_fmt(total), tr("Children served")),
        _metric(_fmt(exposed), tr("Children exposed"), color="#e31a1c"),
        _metric(fmt_int(n_site_exp), tr("Facilities in hazard"), color="#e31a1c"),
    ]

    items = _infra_combined_items(r.get("per_topic_exposed"),
                                  r.get("per_subhazard_exposed"),
                                  site_tally, sel_topics, total,
                                  overrides=overrides or None)

    n_custom = len(overrides or {})
    thr_note = (_live_summary(live_spec) if live_spec else
                tr("{n} custom threshold(s) applied", n=n_custom)
                if n_custom else tr("Standard (default) thresholds"))
    where = region_name or tr("the selected district")
    caption = tr("{n} facilities in {where}. Population is attributed to "
                 "each facility's nearest-neighbour (Voronoi) catchment; per-hazard "
                 "rows show exposed children · facilities whose site is in that "
                 "hazard. {thr_note}.",
                 n=fmt_int(nfac), where=where, thr_note=thr_note)

    prov = [tr("Region: {region}", region=region_name or adm2_ucode)
            + f" · {tr(level)}",
            tr("Facilities: as supplied by the selected infrastructure layer "
               "(see the layer name for its source)")]
    if live_spec:
        prov += _live_provenance(live_spec)
    panel = html.Div([
        _infra_panel(tr("Facility exposure"), cards, items, None, caption=caption),
        _provenance_block(prov),
    ])

    # ── Map layers ──
    try:
        pop_url, _vis = get_clipped_pop_tile_url(asset_id, adm2_ucode)
    except Exception:
        pop_url = no_update
    try:
        pt_url = get_infra_tile_url(asset_id, color, adm2_ucode)
    except Exception:
        pt_url = no_update
    cells = r.get("voronoi_geojson") or no_update

    # Only topics with exposure here get a legend row — a toggle for a hazard
    # with no local coverage is noise (same rule as the Analysis tab).
    map_topics = [t for t in (sel_topics if live_spec else HAZARD_TOPICS)
                  if t in sel_topics
                  and (r.get("per_topic_exposed") or {}).get(t)][:ANALYSIS_TOPIC_SLOTS]
    viz = {"asset_id": asset_id, "ucode": adm2_ucode, "color": color,
           "level": level, "topics": map_topics,
           "thr": sorted((h, v) for h, v in (overrides or {}).items()),
           "live": live_spec}
    pending = {"asset_id": asset_id, "ucode": adm2_ucode, "topics": topics_sel,
               "overrides": overrides or None, "live": live_spec}
    placeholder = html.Div(tr("Preparing per-facility download…"),
                           className="ps-caption", style={"padding": "4px 0"})

    # Fit the map to the district once its layers are built — same treatment as
    # the Analysis tab. Best-effort: a failed bounds lookup must not cost the
    # user the result they just computed.
    try:
        viewport = {"bounds": get_feature_bounds(level, adm2_ucode),
                    "transition": "flyToBounds"}
    except Exception:
        viewport = no_update

    return (panel, pop_url, pt_url, cells, viz, pending, placeholder, viewport)


@app.callback(
    Output("infra-download-wrap", "children", allow_duplicate=True),
    Input("store-infra-pending",  "data"),
    prevent_initial_call=True,
)
def infra_compute_perfacility(pending):
    """Phase 2: per-facility CSV — Voronoi population + exposed pop + site flags
    + raw intensity. Voronoi cells don't overlap → figures are summable."""
    if not pending:
        return no_update
    live_spec = pending.get("live")
    try:
        r = compute_facility_combined(pending["asset_id"], pending["ucode"],
                                      pending.get("topics"),
                                      threshold_overrides=pending.get("overrides"),
                                      live=_build_live(live_spec) if live_spec else None)
    except Exception as e:
        return html.Div(tr("Per-facility data failed: {e}", e=e), className="ps-caption",
                        style={"color": "var(--red)"})

    topic_cols = r.get("topic_cols") or []
    int_cols   = r.get("intensity_cols") or []
    # Voronoi pop keyed by facility location (names aren't unique — many blank).
    def _key(p):
        lon, lat = p.get("lon"), p.get("lat")
        return (round(lon, 6), round(lat, 6)) if lon is not None and lat is not None else None
    vor = {}
    for f in r.get("facilities_voronoi", []):
        p = f.get("properties") or {}
        vor[_key(p)] = (p.get("vpop"), p.get("vexp"))

    rows = []
    for f in r.get("facilities_site", []):
        p = f.get("properties") or {}
        nm = p.get("fname")
        vpop, vexp = vor.get(_key(p), (None, None))
        row = {
            "name": nm or "",
            "lon":  round(p.get("lon"), 6) if p.get("lon") is not None else "",
            "lat":  round(p.get("lat"), 6) if p.get("lat") is not None else "",
            "voronoi_pop":  int(round(vpop or 0)),
            "exposed_pop":  int(round(vexp or 0)),
        }
        for t in topic_cols:
            row[t] = int(round(p.get(t) or 0))
        for h in int_cols:
            v = p.get(h)
            row[h] = round(v, 4) if v is not None else ""
        rows.append(row)

    # Column groups drive the styled workbook's merged header + cell formats.
    groups = [
        {"title": tr("Facility"), "columns": [
            {"key": "name", "label": tr("Name"),      "kind": "text"},
            {"key": "lon",  "label": tr("Longitude"), "kind": "float"},
            {"key": "lat",  "label": tr("Latitude"),  "kind": "float"},
        ]},
        {"title": tr("Catchment population (children)"), "columns": [
            {"key": "voronoi_pop", "label": tr("In catchment"), "kind": "int"},
            {"key": "exposed_pop", "label": tr("Exposed"),      "kind": "int"},
        ]},
        {"title": tr("Site in hazard (1 = exposed)"), "columns": [
            {"key": t, "label": tr(t), "kind": "flag"} for t in topic_cols
        ]},
        {"title": tr("Hazard intensity at site (native units)"), "columns": [
            {"key": h, "label": _intensity_label(h, live_spec), "kind": "float"}
            for h in int_cols
        ]},
    ]
    banner = [tr("Facility exposure — per-facility detail")]
    if live_spec:
        banner += _live_provenance(live_spec)
    else:
        banner += _thresholds_banner(pending.get("topics")
                                     or [t for t in HAZARD_TOPICS
                                         if t not in EXPOSURE_ONLY_TOPICS],
                                     pending.get("overrides"))
    banner.append(tr("Catchment population uses each facility's nearest-neighbour "
                     "(Voronoi) catchment (non-overlapping, summable). Site flags and "
                     "Exposed reflect the thresholds above; intensity columns are raw "
                     "native values."))
    return _infra_download(groups, rows, "infra_facility_exposure.xlsx",
                           banner_lines=banner)
