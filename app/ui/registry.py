"""Navigation registry: phases, views, panes and URL paths."""
from i18n import N_, tr


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Navigation registry — phases, views and panes.
#
# Three levels, each derived from the table above it:
#   PHASES - the global switch under the panel title (Preparedness, Early
#            Warning, Damage Assessment). Sets the rail's contents and accent.
#   VIEWS  - the rail buttons of a phase. A view with several segments shows a
#            segmented control at the top of the panel (e.g. Observed|Forecast).
#   TABS   - the panes. Each segment opens one pane, and store-tab holds its
#            key. Pane keys are load-bearing (store-tab's default, and ~30
#            callbacks compare against them), so they never change with labels.
#
# Every EW task sits under one Hazard card (ew_hazard_card), so the hazard is
# picked once and stays editable in Visualization, Pop and Infra analysis.
# Prep and EW Infra analysis share the infrastructure pane; store-view sets
# infra-mode-prep / infra-mode-ew, and a view change on a shared pane does not
# fire store-tab callbacks (clear on store-view).
# ---------------------------------------------------------------------------

TABS = [
    {"key": "hazard",         "pane": "tab-hazard",
     "title": N_("Hazard Layers"),
     "desc": N_("Select a layer to show the occurrence probability of each type of hazard")},
    {"key": "mh",             "pane": "tab-mh",
     "title": N_("Multi Hazard Indicators"),
     "desc": N_("Display areas exposed to multiple hazards")},
    {"key": "exposure",       "pane": "tab-exposure",
     "title": N_("Children's Exposure"),
     "desc": N_("Display child population exposed to each type of hazard at "
             "different thresholds")},
    {"key": "analysis",       "pane": "tab-analysis",
     "title": N_("Exposure Analysis"),
     "desc": N_("Compute number of children exposed by admin unit")},
    {"key": "infrastructure", "pane": "tab-infrastructure",
     "title": N_("Infrastructure Analysis"),
     "desc": N_("Compute number of facilities exposed, and number of children "
             "affected by admin unit")},
    {"key": "observed",       "pane": "tab-observed",
     "title": N_("Observed Hazards"),
     "desc": N_("Hazards recently recorded by near-real-time satellite and "
             "reanalysis data (under development)")},
    {"key": "forecast",       "pane": "tab-forecast",
     "title": N_("Forecast Hazards"),
     "desc": N_("Hazards forecast for the days ahead (under development)")},
    {"key": "ewpop",          "pane": "tab-ewpop",
     "title": N_("Population Analysis"),
     "desc": N_("Compute children exposed to the selected hazard by admin unit")},
    {"key": "elnino",         "pane": "tab-elnino",
     "title": N_("El Niño Seasonal Outlook"),
     "desc": N_("Explore the seasonal rainfall outlook and compute the number of "
             "children in areas with a dry or wet signal")},
    {"key": "ai",             "pane": "tab-ai",
     "title": N_("AI Assistant"),
     "desc": N_("Ask about hazard layers or child exposure")},
]

TAB_BY_KEY = {t["key"]: t for t in TABS}

# EW Visualization's catalogue panes (one per dataset kind).
CATALOG_TABS = ("forecast", "observed")
# Panes on the shared forecast map layers; ewpop also shows fc_controls.
FORECAST_TABS = (*CATALOG_TABS, "ewpop")
# Views under the shared EW Hazard card.
EW_HAZARD_VIEWS = ("ew-vis", "ew-pop", "ew-infra")

PHASES = [
    {"key": "prep",   "slug": "preparedness",      "label": N_("Preparedness"),
     "icon": "bi bi-shield-check", "enabled": True,
     "badge": N_("Historical hazard data")},
    {"key": "ew",     "slug": "early-warning",     "label": N_("Early Warning"),
     "icon": "bi bi-broadcast",    "enabled": True,
     "badge": N_("Live hazard data")},
    {"key": "damage", "slug": "damage-assessment", "label": N_("Damage"),
     "icon": "bi bi-clipboard2-pulse", "enabled": False,
     "badge": ""},
]
PHASE_BY_KEY = {p["key"]: p for p in PHASES}

_seg = lambda tab, slug=None, label=None: {"tab": tab, "slug": slug, "label": label}
_EW_SEGMENTS = [_seg("observed", "observed", N_("Observed")),
                _seg("forecast", "forecast", N_("Forecast"))]

# `phase` None = shown in every phase (the AI utility at the foot of the rail).
VIEWS = [
    {"key": "prep-vis",   "phase": "prep", "slug": "visualization",
     "icon": "bi bi-layers",         "label": N_("Visualization"),
     "segments": [_seg("hazard",   "hazard",       N_("Hazard")),
                  _seg("mh",       "multi-hazard", N_("Multi-hazard")),
                  _seg("exposure", "exposure",     N_("Exposure"))]},
    {"key": "prep-pop",   "phase": "prep", "slug": "population",
     "icon": "bi bi-people",         "label": N_("Pop analysis"),
     "segments": [_seg("analysis")]},
    {"key": "prep-infra", "phase": "prep", "slug": "infrastructure",
     "icon": "bi bi-buildings",      "label": N_("Infra analysis"),
     "segments": [_seg("infrastructure")]},
    {"key": "ew-vis",     "phase": "ew",   "slug": "visualization",
     "icon": "bi bi-layers",         "label": N_("Visualization"),
     "segments": _EW_SEGMENTS},
    {"key": "ew-pop",     "phase": "ew",   "slug": "population",
     "icon": "bi bi-people",         "label": N_("Pop analysis"),
     "segments": [_seg("ewpop")]},
    {"key": "ew-infra",   "phase": "ew",   "slug": "infrastructure",
     "icon": "bi bi-buildings",      "label": N_("Infra analysis"),
     "segments": [_seg("infrastructure")]},
    {"key": "ew-seasonal", "phase": "ew",  "slug": "seasonal",
     "icon": "bi bi-thermometer-sun", "label": N_("Seasonal"),
     "segments": [_seg("elnino")]},
    {"key": "ai",         "phase": None,   "slug": "assistant",
     "icon": "bi bi-robot",          "label": N_("AI"),
     "segments": [_seg("ai")]},
]
VIEW_BY_KEY = {v["key"]: v for v in VIEWS}
SEGMENTED_VIEWS = [v for v in VIEWS if len(v["segments"]) > 1]


def view_tabs(view_key):
    return [s["tab"] for s in VIEW_BY_KEY[view_key]["segments"]]


def default_nav(phase):
    view = next(v for v in VIEWS if v["phase"] == phase)
    return {"phase": phase, "view": view["key"],
            "tab": view["segments"][0]["tab"]}


DEFAULT_NAV = default_nav("prep")


def nav_to_path(nav):
    """'/<phase>/<view>[/<segment>]' relative to the app's base pathname."""
    view = VIEW_BY_KEY[nav["view"]]
    parts = [PHASE_BY_KEY[nav["phase"]]["slug"], view["slug"]]
    seg = next(s for s in view["segments"] if s["tab"] == nav["tab"])
    if seg["slug"]:
        parts.append(seg["slug"])
    return "/" + "/".join(parts)


def nav_from_path(rel):
    """Inverse of nav_to_path; None for an unknown or empty path. A missing
    segment falls back to the view's first one."""
    parts = [p for p in (rel or "").strip("/").split("/") if p]
    if not parts:
        return None
    phase = next((p for p in PHASES if p["slug"] == parts[0] and p["enabled"]),
                 None)
    if not phase:
        return None
    if len(parts) == 1:
        return default_nav(phase["key"])
    view = next((v for v in VIEWS if v["slug"] == parts[1]
                 and v["phase"] in (phase["key"], None)), None)
    if not view:
        return None
    seg = next((s for s in view["segments"]
                if len(parts) > 2 and s["slug"] == parts[2]),
               view["segments"][0])
    return {"phase": phase["key"], "view": view["key"], "tab": seg["tab"]}


def nav_label(nav):
    view = VIEW_BY_KEY[nav["view"]]
    seg = next(s for s in view["segments"] if s["tab"] == nav["tab"])
    parts = [tr(PHASE_BY_KEY[nav["phase"]]["label"]), tr(view["label"])]
    if seg["label"]:
        parts.append(tr(seg["label"]))
    return " › ".join(parts)
