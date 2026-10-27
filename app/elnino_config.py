# =============================================================================
# elnino_config.py — El Niño / SEAS5 seasonal outlook catalog
#
# Separate from forecast_config.py for the same reason that one is separate from
# config.HAZARDS: the layers here are neither a frozen return-period asset nor a
# date-windowed live collection. They are monthly forecast issues (ISSUES)
# whose images are selected by period, not by date range — so the date
# picker, reducer and lag machinery of the Forecast tab do not apply at all.
#
# No GEE import — same rule as config.py and forecast_config.py, so this loads
# instantly and can be imported by tooling that has no Earth Engine session.
#
# Produced by scripts/el_nino/download_ecmwf_prec.py. That script is the single
# authority for what is in these assets; everything below must agree with it.
# =============================================================================
import datetime as _dt

# ── The uploaded collections ────────────────────────────────────────────────
# One folder per forecast issue: el_nino/<init>/<collection>/<image>, with
# collections raw_forecast[_t2m], raw_hindcast[_t2m], terciles[_t2m],
# compound, skill[_t2m], brier. Shared across issues at the root:
# chirps_obs, era5_obs (keyed by months) and enso_mask (keyed by season).
ELNINO_ROOT = "projects/unicef-ccri/assets/el_nino"


def issue_collection(tag, collection):
    return f"{ELNINO_ROOT}/{tag}/{collection}"


def issue_asset(tag, collection, image):
    return f"{ELNINO_ROOT}/{tag}/{collection}/{image}"

# ── ENSO attribution mask ───────────────────────────────────────────────────
# User-supplied raster marking where El Nino has historically pushed OND
# rainfall significantly in one direction: -1 drier, +1 wetter, 0 no
# significant historical signal. 0.5 degree, p50/sig90.
#
# Used to narrow a count from "unusual weather is forecast" to "unusual
# weather CONSISTENT WITH El Nino is forecast". Applied DIRECTIONALLY: a dry
# signal counts only on -1 cells, wet only on +1. Reported alongside, never
# applied to the headline figure.
#
# Same asset and the same direction map as
# scripts/el_nino/export_reliability_table.py, so a dashboard figure and a
# workbook figure agree.
# One image per season (scripts/el_nino/build_enso_mask.py). A season period
# uses its own; a single month uses the 3-month season centred on it.
ENSO_MASK_BAND  = "enso_dir"
# Compound signals inherit their RAINFALL component's direction. The mask says
# nothing about temperature, so "attributable" is a weaker claim for them —
# the UI must say so.
ENSO_MASK_DIR = {"dry": -1, "wet": 1,
                 "hotdry": -1, "cooldry": -1,
                 "hotwet": 1, "coolwet": 1}

# Published forecast issues (init YYYYMM), oldest first. Append the new tag each
# month after running the scripts for it; see scripts/el_nino/CLAUDE.md.
ISSUES   = ["202609", "202610"]
LEADS    = (2, 3, 4)          # lead 1 is the init month itself, always excluded
HINDCAST = "1993-2016"
HC_YEARS = list(range(int(HINDCAST[:4]), int(HINDCAST[-4:]) + 1))
SYSTEM   = "51"

# ── Band order contract ─────────────────────────────────────────────────────
# geeup's ingestion manifest carries no `bands` key, so Earth Engine names every
# band b1..b8 in file order. Band ORDER is the contract; the names live in each
# image's `band_names` property and are re-applied with .rename().
#
# This list must stay byte-identical to the terciles() return order in
# scripts/el_nino/download_ecmwf_prec.py. elnino_core verifies it against the
# live asset on first use rather than trusting this copy.
TERCILE_BANDS = [
    "p_below", "p_normal", "p_above", "cat",
    "fc_mean_mm", "clim_mean_mm", "anom_mm", "dry_mask",
]

# SEVEN bands, not eight — temperature has no "too cold to interpret" analogue
# of the arid-cell dry_mask, so consumers needing a mask take it from the
# PRECIPITATION terciles. The count asymmetry is protective: a .rename() against
# the wrong list fails loudly instead of silently mislabeling every band.
TERCILE_T2M_BANDS = [
    "p_below", "p_normal", "p_above", "cat",
    "fc_mean_c", "clim_mean_c", "anom_c",
]

COMPOUND_BANDS = [
    "p_hotdry", "p_coolwet", "p_hotwet", "p_cooldry", "cat_compound",
    "p_dry_marginal", "p_hot_marginal", "dependence_ratio", "dry_mask",
]

# ── Periods ─────────────────────────────────────────────────────────────────
# Derived from ISSUES: one season (leads 2-4 summed/averaged) per issue, plus
# each calendar month taken from the LATEST issue that covers it.
# `id` is "<init>_<suffix>"; `suffix` is the asset-name part the scripts write
# (window_L2_L4, L3_Nov, ...). `lead` 0 = the season (EE needs one type).
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
SEASON_DRY_MASK_MM = 50
MONTH_DRY_MASK_MM  = 10


def issue_label(tag):
    return f"{_MONTHS_LONG[int(tag[4:]) - 1]} {tag[:4]}"


def lead_month(tag, lead):
    """(year, month) of a lead; lead 1 is the init month."""
    m0 = int(tag[4:]) - 1 + (lead - 1)
    return int(tag[:4]) + m0 // 12, m0 % 12 + 1


def season_code(tag):
    return "".join(_MONTHS[lead_month(tag, l)[1] - 1][0] for l in LEADS)


def window_suffix():
    return f"window_L{LEADS[0]}_L{LEADS[-1]}"


def issue_periods(tag):
    """The season plus each lead month of one issue."""
    (y0, m0), (y1, m1) = lead_month(tag, LEADS[0]), lead_month(tag, LEADS[-1])
    span = (f"{_MONTHS[m0 - 1]}–{_MONTHS[m1 - 1]} {y1}" if y0 == y1 else
            f"{_MONTHS[m0 - 1]} {y0}–{_MONTHS[m1 - 1]} {y1}")
    out = [{"suffix": window_suffix(), "lead": 0, "month": None,
            "label": f"{span} ({season_code(tag)} season)",
            "short": season_code(tag), "dry_mask_mm": SEASON_DRY_MASK_MM}]
    for l in LEADS:
        y, m = lead_month(tag, l)
        out.append({"suffix": f"L{l}_{_MONTHS[m - 1]}", "lead": l,
                    "month": (y, m), "label": f"{_MONTHS_LONG[m - 1]} {y}",
                    "short": _MONTHS[m - 1], "dry_mask_mm": MONTH_DRY_MASK_MM})
    for p in out:
        p.update(id=f"{tag}_{p['suffix']}", init=tag,
                 issue_label=issue_label(tag))
    return out


def _build_periods():
    seasons, months = [], {}
    for tag in sorted(ISSUES):
        ps = issue_periods(tag)
        seasons.append(ps[0])
        for p in ps[1:]:
            months[p["month"]] = p          # later issue overwrites
    return seasons + [months[k] for k in sorted(months)]


PERIODS = _build_periods()          # every period, shown or not
PERIOD_MAP = {p["id"]: p for p in PERIODS}


def _last_month(p):
    return p["month"] if p["lead"] else lead_month(p["init"], LEADS[-1])


def active_periods(today=None):
    """Periods to offer: drops any whose (last) month has ended."""
    today = today or _dt.date.today()
    now = (today.year, today.month)
    live = [p for p in PERIODS if tuple(_last_month(p)) >= now]
    return live or [p for p in PERIODS if p["lead"] == 0][-1:]


def default_period(today=None):
    seasons = [p for p in active_periods(today) if p["lead"] == 0]
    return (seasons or active_periods(today))[-1]["id"]      # newest season


def is_window(period):
    return PERIOD_MAP.get(period, {}).get("lead") == 0


def period_label(period):
    return PERIOD_MAP.get(period, {}).get("label", period)


def period_issue_label(period):
    return PERIOD_MAP.get(period, {}).get("issue_label", "")


def mask_season(period):
    """ENSO mask season: the season of the period's issue, for its months too."""
    return season_code(period["init"])


def enso_mask_asset(period):
    return f"{ELNINO_ROOT}/enso_mask/enso_mask_{mask_season(period)}_sig90_p50"


# ── Used by scripts/el_nino (--init) ─────────────────────────────────────────
def parse_issue(tag):
    """Validate --init; (tag, label, {suffix: period}) for that issue."""
    if len(tag) != 6 or not tag.isdigit() or not 1 <= int(tag[4:]) <= 12:
        raise SystemExit(f"ERROR: --init must be YYYYMM, got {tag!r}")
    return tag, issue_label(tag), {p["suffix"]: p for p in issue_periods(tag)}


def obs_months(tag, suffix):
    """(first, n) observed months; first counts from Jan of the init year,
    so 13 is January of the following year."""
    lead = 0 if suffix == window_suffix() else int(suffix.split("_")[0][1:])
    first = LEADS[0] if lead == 0 else lead
    return int(tag[4:]) + first - 1, (len(LEADS) if lead == 0 else 1)


def staged_obs_name(stem, tag, suffix, year):
    """Staged observations are keyed by the months they cover, so issues can
    share them and never pick up another season's under the same period name."""
    if tag == "202609":            # staged before month-keyed names existed
        return f"{stem}_{suffix}_{year}"
    first, n = obs_months(tag, suffix)
    return f"{stem}_m{first:02d}x{n}_{year}"


# ── Signals ─────────────────────────────────────────────────────────────────
# What "exposed" means. Each maps to a probability band and a direction.
#
# These are risk PROXIES, not predictions of drought or flooding: a wet signal
# means this model puts the season in the wettest third of its own climatology,
# which is a reason to look closer, not a forecast of a flood.
SIGNALS = [
    {"name": "dry", "label": "Drier than normal", "band": "p_below",
     "other": "p_above", "cat": 1, "color": "#d2913c",
     "palette": ["#f7f0e4", "#e8d3a9", "#d2913c", "#a8641f", "#6b3a0a"],
     "blurb": "Drought risk proxy — the season falls in the driest third.",
     "area_phrase": "a drier-than-normal area"},
    {"name": "wet", "label": "Wetter than normal", "band": "p_above",
     "other": "p_below", "cat": 3, "color": "#1d7bff",
     "palette": ["#cfe8ff", "#8fcbff", "#1d7bff", "#0654be", "#00357d"],
     "blurb": "Flood risk proxy — the season falls in the wettest third.",
     "area_phrase": "a wetter-than-normal area"},
    # The UNION of the two, not an intersection. A cell qualifies as dry only
    # when p_below > p_above and as wet only when p_above > p_below, so the two
    # can never both hold — verified zero overlapping cells globally, and
    # dry + wet == either exactly on real population. "Both" therefore means
    # "under a signal of either kind", and computing it here rather than adding
    # two columns removes any doubt about double counting.
    {"name": "either", "label": "Either signal", "band": None,
     "other": None, "cat": None, "color": "#7b5ea8",
     "palette": ["#f2edf7", "#d9cbe8", "#b49ad0", "#8c6bb1", "#5c3b85"],
     "blurb": "Any clear signal, dry or wet — the two never overlap, so this "
              "is exactly their sum.",
     "area_phrase": "an area with either signal",
     "union_of": ("dry", "wet")},

    # ── Compound signals ───────────────────────────────────────────────────
    # Counted by ensemble MEMBER: each of the 51 members either is both dry and
    # hot or is not. NEVER p_dry x p_hot — dry and hot are positively
    # correlated through land-atmosphere feedback (drier soil, less evaporative
    # cooling, hotter), so the product understates the risk exactly where it
    # matters. The `dependence_ratio` layer ships the size of that difference.
    #
    # `compound: True` routes _signal_mask to the compound image and SKIPS the
    # p.gt(other) exclusivity test: unlike dry/wet, these three classes are not
    # algebraically exclusive, because different members can vote different
    # ways. `needs` hides them when the collection is absent.
    {"name": "hotdry", "label": "Hotter and drier than normal",
     "band": "p_hotdry", "other": None, "cat": 1,
     "compound": True, "needs": "compound", "color": "#b2182b",
     "palette": ["#fdf0e6", "#f7c4a5", "#e8744f", "#b2182b", "#67000d"],
     "blurb": "Compound heat and drought — water scarcity, crop failure and "
              "heat stress arriving together. Counted per ensemble member.",
     "area_phrase": "a hotter-and-drier-than-normal area"},
    {"name": "hotwet", "label": "Hotter and wetter than normal",
     "band": "p_hotwet", "other": None, "cat": 3,
     "compound": True, "needs": "compound", "color": "#6a51a3",
     "palette": ["#f2eef7", "#d4c6e6", "#a687cc", "#6a51a3", "#3f2a6b"],
     "blurb": "Warm and wet together — the transmission window for malaria, "
              "dengue and waterborne disease.",
     "area_phrase": "a hotter-and-wetter-than-normal area"},
    {"name": "coolwet", "label": "Cooler and wetter than normal",
     "band": "p_coolwet", "other": None, "cat": 2,
     "compound": True, "needs": "compound", "color": "#2166ac",
     "palette": ["#eef4fa", "#c6dbef", "#6baed6", "#2166ac", "#0b3d70"],
     "blurb": "Cooler and wetter than normal — a flood risk proxy, and the "
              "combination least often forecast this season.",
     "area_phrase": "a cooler-and-wetter-than-normal area"},
    # The fourth corner, so the 2x2 has no missing cell. Rarer than hot-dry
    # because the land-atmosphere feedback works against it (drier members
    # tend to be hotter), but real where it occurs: 2088 land cells reach 25%
    # for OND 2026, against cool-wet's 761.
    {"name": "cooldry", "label": "Cooler and drier than normal",
     "band": "p_cooldry", "other": None, "cat": 4,
     "compound": True, "needs": "compound", "color": "#5a8a3c",
     "palette": ["#f1f6ec", "#d7e8c6", "#a9cc87", "#5a8a3c", "#2f4f1d"],
     "blurb": "Cooler and drier than normal — water stress without the heat "
              "load, so a milder version of the drought pathway.",
     "area_phrase": "a cooler-and-drier-than-normal area"},
]

SIGNAL_MAP = {s["name"]: s for s in SIGNALS}
DEFAULT_SIGNAL = "dry"

# Single-variable signals keep the original slider and baseline; compound ones
# get their own. Grouped so the UI can label the two sets rather than showing
# six undifferentiated radios.
SIGNAL_GROUPS = [
    ("Rainfall", ["dry", "wet", "either"]),
    ("Rainfall + temperature", ["hotdry", "hotwet",
                                "cooldry", "coolwet"]),
]


def is_compound(signal):
    return bool(SIGNAL_MAP.get(signal, {}).get("compound"))

# ── Threshold contract ──────────────────────────────────────────────────────
# Everything user-facing is a PERCENTAGE (0-100). The GEE bands are raw
# fractions (0-1), because that is what an ensemble count is, so exactly one
# conversion happens — at the boundary, in elnino_core, via to_fraction().
# Keeping the split at that single point is what stops a 0.5-vs-50 mix-up
# silently producing a threshold 100x too small.
#
# 1/3 is the no-information baseline: with terciles, a forecast carrying no
# signal puts ~33% of members in each third.
#
# 51 ensemble members means probability moves in steps of 1/51 ~ 2 percentage
# points, so cells near any cutoff flip on essentially arbitrary grounds. That
# is why the UI reports a sensitivity spread rather than one number.
# 32, not 34, so the 33.3% no-information baseline sits ON the scale rather
# than just off its left edge. The baseline is the reference that makes the
# number readable; a threshold below it is meaningless and the UI says so, but
# it has to be visible to be understood. (Selecting 32-33 is allowed and the
# caption warns; the old floor of 34 hid the landmark instead.)
PROB_MIN     = 32      # %
PROB_MAX     = 80      # %
# 70%, matching the published methodological note ("at least 70 per cent ...
# Seventy per cent is a demanding threshold") and the exported workbooks, which
# both use 70. The tab previously opened at 50 while every written figure used
# 70, so the same region read differently depending on which artefact you
# looked at. The slider still spans 34-80 — the default is the headline, not a
# limit.
PROB_DEFAULT = 70      # %
PROB_STEP    = 1       # percentage points
PROB_MARKS   = [32, 40, 50, 60, 70, 80]

# Thresholds reported side by side with the headline figure, in %.
# BRACKETS the default (70) rather than sitting below it: the row exists to
# show how much the headline figure moves, which it cannot do if every value
# shown is lower than the one in use. 60 and 80 are the neighbours that matter.
SENSITIVITY_THRESHOLDS = [50, 60, 70, 80]

CHANCE_LEVEL = 100.0 / 3.0      # 33.3 %

# ── Compound threshold contract ─────────────────────────────────────────────
# A compound event needs BOTH variables in their extreme tercile, so for two
# independent terciles the no-information baseline is 1/9 = 11.1%, NOT 1/3.
# This is the single most important thing to get right about the compound
# layers: reading a compound probability against 33% makes every map look
# empty, and placing "25% hot-dry" beside "50% dry" invites the reader to
# conclude the compound risk is half as likely when both sit the SAME distance
# above their own baseline.
#
# 25% is 2.25x the joint baseline; the rainfall default of 70% is 2.10x its
# own. Deliberately not tuned to match exactly — 51 members quantise
# probability in ~2-point steps, so the difference between 23% and 25% is
# below the resolution of the ensemble and matching them would be false
# precision. What matters is that both sit roughly twice their baseline.
COMPOUND_CHANCE_LEVEL = 100.0 / 9.0     # 11.1 %
# The minimum sits BELOW the 11.1% baseline on purpose. Starting at 12 would
# put chance off the left edge of the scale, so the one reference point a
# reader needs in order to judge the number would be invisible — and the whole
# reason this range differs from the rainfall one is that its baseline differs.
# 10 keeps chance on the scale with a little room to its left.
PROB_COMPOUND_MIN     = 10
PROB_COMPOUND_MAX     = 60
PROB_COMPOUND_DEFAULT = 25
PROB_COMPOUND_MARKS   = [10, 20, 25, 33, 40, 50, 60]
SENSITIVITY_COMPOUND  = [20, 25, 33]


def threshold_contract(signal):
    """(min, max, default, marks, chance_level) for a signal's slider.

    One place, so a compound signal can never inherit the single-variable
    bounds by omission.
    """
    if is_compound(signal):
        return (PROB_COMPOUND_MIN, PROB_COMPOUND_MAX, PROB_COMPOUND_DEFAULT,
                PROB_COMPOUND_MARKS, COMPOUND_CHANCE_LEVEL)
    return (PROB_MIN, PROB_MAX, PROB_DEFAULT, PROB_MARKS, CHANCE_LEVEL)


def to_fraction(pct):
    """Percentage (0-100) -> the 0-1 fraction the GEE bands hold.

    The single conversion point between what the UI shows and what the imagery
    stores. Anything crossing into elnino_core goes through here.
    """
    return float(pct) / 100.0


# ── Forecast skill ──────────────────────────────────────────────────────────
# Correlation between the model's hindcast season totals and CHIRPS observed,
# 1993-2016, per grid cell. This says whether a signal is worth believing HERE;
# it corrects nothing and changes no exposure figure.
#
# Measured while building: maritime continent +0.58, Horn of Africa +0.47,
# southern Africa +0.28, Europe -0.00 — the ENSO-teleconnected regions score
# high and Europe sits at zero, which is what a working skill metric should do.
SKILL_BANDS = ["skill_r", "skill_p"]

# r thresholds. 0.4 is the "believe it" line used in the results caption; 0.2
# separates "weak but present" from "none". Both are conventional rather than
# derived, which is why the layer shows the continuous r as well.
SKILL_USABLE_MIN   = 0.4
SKILL_MODERATE_MIN = 0.2

# Contiguous values, as every classified layer must be: a GEE palette spreads
# evenly over min..max, so a gap would blend two class colours through it.
# 0 is "not assessed", NOT "no skill" — CHIRPS is land-only and spans 50N-50S,
# so ocean and high latitudes have no observation to score against.
SKILL_CLASSES = [
    {"value": 0, "label": "Not assessed (no land observations)",
     "color": "#d8d8d8"},
    {"value": 1, "label": f"Low (r < {SKILL_MODERATE_MIN})", "color": "#c94040"},
    {"value": 2, "label": f"Moderate (r {SKILL_MODERATE_MIN}–{SKILL_USABLE_MIN})",
     "color": "#e8b84b"},
    {"value": 3, "label": f"Usable (r ≥ {SKILL_USABLE_MIN})", "color": "#3d9e56"},
]

SKILL_REFERENCE = "CHIRPS"


# ── Probabilistic skill (Brier) ─────────────────────────────────────────────
# Correlation above grades the ensemble MEAN. The exposure count reads the
# tercile PROBABILITIES, and a model can rank years well while being
# systematically overconfident — correlation cannot see that, because it never
# looks at a probability. The Brier skill score does.
#
# Measured over the OND window, and the divergence is the whole justification:
#     region                  r      BSS
#     Maritime continent   +0.58   +0.35   genuinely skillful
#     Horn of Africa       +0.47   +0.07   barely beats guessing
#     southern Africa      +0.28   +0.04
#     Europe               -0.00   -0.05   worse than guessing
BRIER_BANDS = ["bss_dry", "bss_wet", "brier_dry", "brier_wet"]

# BSS is 1 - Brier/Brier_climatology, where climatology is always forecasting
# 1/3. So ZERO is the meaningful boundary — not 0.5, and not the midpoint of
# any colour ramp. Anything at or below 0 adds nothing over guessing.
BSS_USEFUL_MIN = 0.0
BSS_GOOD_MIN   = 0.10      # a conventional "worth acting on" line

# Contiguous values, as every classified layer must be.
BRIER_CLASSES = [
    {"value": 0, "label": "Not assessed (no land observations)",
     "color": "#d8d8d8"},
    {"value": 1, "label": "No better than guessing (BSS ≤ 0)",
     "color": "#c94040"},
    {"value": 2, "label": f"Marginal (BSS 0–{BSS_GOOD_MIN})",
     "color": "#e8b84b"},
    {"value": 3, "label": f"Useful (BSS ≥ {BSS_GOOD_MIN})",
     "color": "#3d9e56"},
]


# ── ENSO context ────────────────────────────────────────────────────────────
# The tab is named El Nino, so it should show whether one is actually happening
# rather than leaving the reader to assume it.
#
# Nino 3.4 is the standard ENSO index region. NOAA OISST stores SST and its
# anomaly as int16 with a 0.01 scale factor — forgetting that multiply yields
# values near +266 instead of +2.66, which is why the scale lives here as a
# named constant rather than a literal in the query.
NINO34_BOX      = [-170, -5, -120, 5]      # lon/lat, 5S-5N, 170W-120W
OISST_COLLECTION = "NOAA/CDR/OISST/V2_1"
OISST_SCALE      = 0.01
NINO34_MONTHS    = 36                       # how much history the chart shows

# Standard NOAA thresholds on the 3-month running mean.
ENSO_EVENT_MIN  = 0.5
ENSO_STRONG_MIN = 1.5

# Hindcast years by observed Niño 3.4 over each issue's season (OISST), for the
# El Niño minus neutral composite. One entry per issue; generate with
# scripts/el_nino/build_enso_mask.py --init <tag> --classify-years.
ENSO_YEARS = {
    "202609": {  # OND
        "elnino": [1994, 1997, 2002, 2004, 2006, 2009, 2014, 2015],
        "lanina": [1995, 1998, 1999, 2000, 2007, 2010, 2011],
        "neutral": [1993, 1996, 2001, 2003, 2005, 2008, 2012, 2013, 2016],
    },
    "202610": {  # NDJ
        "elnino": [1994, 1997, 2002, 2004, 2006, 2009, 2014, 2015],
        "lanina": [1995, 1998, 1999, 2000, 2005, 2007, 2008, 2010, 2011],
        "neutral": [1993, 1996, 2001, 2003, 2012, 2013, 2016],
    },
}

# ── Explore layers ──────────────────────────────────────────────────────────
# Every band is visualisable, because the probabilities alone say how LIKELY a
# signal is and nothing about how LARGE it is. `anom_mm` is what separates
# "confidently slightly wetter" from "confidently much wetter".
#
#   band      band to display (or None when `derived` builds it)
#   derived   key into elnino_core._DERIVED
#   diverging palette runs low->high through a neutral midpoint
EXPLORE_LAYERS = [
    {"name": "signal_prob", "label": "Signal probability",
     "band": None, "derived": "signal_prob",
     # Rendered 0-100 by _d_signal_prob, so the legend reads in the same units
     # as the threshold slider rather than 0-1 beside a "50%" control.
     "min": 0, "max": 100, "units": "%",
     "palette": ["#f7f7f7", "#d9e8f5", "#92c5de", "#2166ac", "#0b2c56"],
     "desc": "Probability that the selected signal occurs — the fraction of "
             "the 51 ensemble members falling in that tercile. Follows the "
             "signal selector above.",
     "default": True},
    # A CLASSIFIED layer: three flat colours, not a ramp. `classes` drives both
    # the palette and a swatch-per-class legend, so the map and the key cannot
    # drift. Values are contiguous (0,1,2) because a palette spreads evenly over
    # min..max — a gap would blend two class colours through the missing value.
    {"name": "cat", "label": "Signal category",
     "band": None, "derived": "category",
     "min": 0, "max": 2, "units": "",
     "classified": True,
     "classes": [
         {"value": 0, "label": "No clear signal", "color": "#e9e9e9"},
         {"value": 1, "label": "Drier than normal", "color": "#d2913c"},
         {"value": 2, "label": "Wetter than normal", "color": "#1d7bff"},
     ],
     "palette": ["#e9e9e9", "#d2913c", "#1d7bff"],
     "desc": "Each cell classified at the threshold on the slider — dry, wet, "
             "or neither. The two signals cannot overlap, so the classes are "
             "exclusive. Recomputed live, unlike the stored `cat` band which "
             "is frozen at 50%.",
     "default": False},
    {"name": "anom_mm", "label": "Rainfall anomaly",
     "band": "anom_mm", "derived": None,
     "min": -200, "max": 200, "units": "mm", "diverging": True,
     "palette": ["#8c510a", "#dfc27d", "#f5f5f5", "#80cdc1", "#01665e"],
     "desc": "Forecast mean minus the model's climatological mean, in mm. "
             "This is the magnitude the probabilities do not carry: a cell can "
             "be 90% likely to be wetter while only 3 mm above normal.",
     "default": False},
    {"name": "anom_pct", "label": "Rainfall anomaly vs normal",
     "band": None, "derived": "anom_pct",
     "min": -60, "max": 60, "units": "%", "diverging": True,
     "palette": ["#8c510a", "#dfc27d", "#f5f5f5", "#80cdc1", "#01665e"],
     "desc": "The anomaly as a share of the climatological mean. +50 mm is "
             "trivial in Kerala and enormous in the Sahel — this is what makes "
             "regions comparable.",
     "default": False},
    {"name": "signal_strength", "label": "Signal strength (wet − dry)",
     "band": None, "derived": "signal_strength",
     # Percentage points, on the same 0-100 scale as the probabilities above.
     "min": -100, "max": 100, "units": "pp", "diverging": True,
     "palette": ["#d2913c", "#e8d3a9", "#f5f5f5", "#8fcbff", "#1d7bff"],
     "desc": "p_above − p_below as one signed layer: brown where a dry signal "
             "dominates, blue where a wet one does, pale where the ensemble is "
             "split.",
     "default": False},
    {"name": "fc_mean_mm", "label": "Forecast rainfall",
     "band": "fc_mean_mm", "derived": None,
     "min": 0, "max": 1500, "units": "mm",
     "palette": ["#f7f7d7", "#c7e9b4", "#41b6c4", "#2c7fb8", "#253494"],
     "desc": "Ensemble-mean total rainfall forecast for the period.",
     "default": False},
    {"name": "clim_mean_mm", "label": "Climatological rainfall",
     "band": "clim_mean_mm", "derived": None,
     "min": 0, "max": 1500, "units": "mm",
     "palette": ["#f7f7d7", "#c7e9b4", "#41b6c4", "#2c7fb8", "#253494"],
     "desc": f"What the model normally predicts for this period, pooled over "
             f"the {HINDCAST} hindcast. The baseline the terciles are cut from.",
     "default": False},
    {"name": "dry_mask", "label": "Too dry to interpret",
     "band": "dry_mask", "derived": None,
     "min": 0, "max": 1, "units": "",
     "palette": ["#c94040", "#f0f0f0"],
     "desc": "Red where the climatology is below the cutoff and a tercile is "
             "rounding noise rather than signal — a 'wet third' on 0.3 mm of "
             "normal rainfall means nothing. Excluded from exposure by default.",
     "default": False},

    # ── Skill and ENSO context ──────────────────────────────────────────────
    # These describe the FORECAST rather than this season's rainfall, so they
    # are exempt from the dry mask (see elnino_core.build_explore_image) and
    # need the skill collection to exist — the tab hides them otherwise.
    {"name": "skill_class", "label": "Rainfall skill (classified)",
     "band": None, "derived": "skill_class",
     "min": 0, "max": 3, "units": "",
     "classified": True,
     "classes": SKILL_CLASSES,
     "palette": [c["color"] for c in SKILL_CLASSES],
     "needs": "skill",
     "desc": "Where this forecast has demonstrated skill, from the correlation "
             "between its 1993–2016 hindcast and CHIRPS observations. Green "
             "cells are worth believing; red ones carry a signal the model has "
             "never predicted well. Grey is unscored, not unskilled — CHIRPS "
             "is land-only.",
     "default": False},
    {"name": "skill_r", "label": "Rainfall skill (correlation)",
     "band": "skill_r", "derived": None,
     "min": -1, "max": 1, "units": "r", "diverging": True,
     "palette": ["#7b3294", "#c2a5cf", "#f7f7f7", "#a6dba0", "#008837"],
     "needs": "skill",
     "desc": "The raw correlation behind the classified layer, so the cutoffs "
             "can be judged rather than taken on trust. Negative means the "
             "model's hindcast ran opposite to observations.",
     "default": False},
    # Brier skill. Range is deliberately SYMMETRIC about zero (-0.4..0.4) even
    # though BSS is unbounded below: zero is the meaningful boundary — beats
    # guessing or does not — so it must land on the palette's neutral midpoint,
    # not somewhere arbitrary along a 0..1 ramp. Values below -0.4 clamp, which
    # is fine: "much worse than guessing" and "worse than guessing" call for
    # the same decision.
    # The two Brier / BSS layers were REMOVED from the UI deliberately, not
    # lost. Reviewers of the rainfall report compared both and chose the
    # correlation: BSS answers "do the probabilities verify", which is the
    # stricter question, but it is not directly comparable with the classified
    # historical values people use to read the DIRECTION of a signal, and it
    # did not survive contact with a non-specialist audience.
    #
    # `el_nino/brier`, build_brier_skill.py and the BSS_* / BRIER_* constants
    # all still exist and still work — nothing is referenced from here, so
    # restoring the layers is re-adding these two entries. The technical point
    # stands and is recorded in CLAUDE.md: correlation grades the ensemble
    # MEAN, not the probabilities the threshold reads, so it is the more
    # generous of the two measures.
    {"name": "enso_composite", "label": "El Niño rainfall fingerprint",
     "band": None, "derived": "enso_composite",
     "min": -300, "max": 300, "units": "mm", "diverging": True,
     "palette": ["#8c510a", "#dfc27d", "#f5f5f5", "#80cdc1", "#01665e"],
     "desc": "How this model's rainfall differs between its 8 El Niño hindcast "
             "seasons and its 9 neutral ones. Where the current forecast "
             "resembles this pattern, attributing it to El Niño has empirical "
             "support from the model's own history; where it does not, it does "
             "not.",
     "default": False},

    # ── Temperature ────────────────────────────────────────────────────────
    {"name": "anom_c", "label": "Temperature anomaly",
     "band": "anom_c", "derived": None,
     "min": -3, "max": 3, "units": "°C", "diverging": True,
     "palette": ["#2166ac", "#92c5de", "#f7f7f7", "#f4a582", "#b2182b"],
     "needs": "t2m", "source": "t2m",
     "desc": "Forecast seasonal mean temperature minus the model's own "
             "1993–2016 climatology. Global land mean is about +1 °C, so most "
             "of the map is warm — the baseline is three decades old, not a "
             "pre-industrial one.",
     "default": False},
    {"name": "fc_mean_c", "label": "Forecast mean temperature",
     "band": "fc_mean_c", "derived": None,
     "min": -10, "max": 40, "units": "°C",
     "palette": ["#313695", "#74add1", "#fee090", "#f46d43", "#a50026"],
     "needs": "t2m", "source": "t2m",
     "desc": "The forecast seasonal mean temperature itself, averaged across "
             "the 51 members — the OND mean, not a total.",
     "default": False},
    {"name": "t2m_signal_prob", "label": "Hot/cool tercile probability",
     "band": None, "derived": "t2m_signal_prob",
     "min": 0, "max": 100, "units": "%",
     "palette": ["#f7f7f7", "#fee0b6", "#fdb863", "#e08214", "#b35806"],
     "needs": "t2m", "source": "t2m",
     "desc": "Probability the season lands in the warmest third of the model's "
             "own climatology. Note how much of the tropics sits near 100%: "
             "against a 1993–2016 baseline, warming has made the hot tercile "
             "close to certain in many places, which is why it adds little "
             "there.",
     "default": False},
    {"name": "skill_r_t2m", "label": "Temperature skill (correlation)",
     "band": None, "derived": "skill_r_t2m",
     "min": -1, "max": 1, "units": "r", "diverging": True,
     "palette": ["#7b3294", "#c2a5cf", "#f7f7f7", "#a6dba0", "#008837"],
     "needs": "skill_t2m",
     "desc": "Correlation between the model's hindcast seasonal mean "
             "temperature and ERA5-Land, 1993–2016. Higher than the rainfall "
             "equivalent almost everywhere — but ERA5 is a reanalysis sharing "
             "model lineage with SEAS5, so this flatters temperature relative "
             "to the CHIRPS-scored rainfall skill.",
     "default": False},

    # ── Compound ───────────────────────────────────────────────────────────
    {"name": "compound_prob", "label": "Compound signal probability",
     "band": None, "derived": "compound_prob",
     "min": 0, "max": 60, "units": "%",
     "palette": ["#f7f7f7", "#fddbc7", "#f4a582", "#d6604d", "#67001f"],
     "needs": "compound", "source": "compound",
     "desc": "Fraction of the 51 members where BOTH variables land in their "
             "extreme tercile at once. Read against 11.1%, not 33%: requiring "
             "two conditions together makes the no-information baseline 1/9.",
     "default": False},
    {"name": "cat_compound", "label": "Combined category",
     "band": None, "derived": "category_compound",
     "min": 0, "max": 4, "units": "",
     "classified": True,
     "classes": [
         {"value": 0, "label": "No clear combined signal", "color": "#e9e9e9"},
         {"value": 1, "label": "Hotter and drier", "color": "#b2182b"},
         {"value": 2, "label": "Cooler and wetter", "color": "#2166ac"},
         {"value": 3, "label": "Hotter and wetter", "color": "#6a51a3"},
         {"value": 4, "label": "Cooler and drier", "color": "#5a8a3c"},
     ],
     "palette": ["#e9e9e9", "#b2182b", "#2166ac", "#6a51a3", "#5a8a3c"],
     "needs": "compound", "source": "compound",
     "desc": "The strongest compound signal in each cell at the slider "
             "threshold. Assigned by largest probability, not by precedence — "
             "unlike dry/wet these classes are not mutually exclusive, so "
             "different members can support different combinations.",
     "default": False},
    # Neutral point is 1.0, NOT 0, so this is rendered as log2(ratio) over
    # -1..+1: a diverging palette spread over a raw 0.5..3 would put 1.0 at a
    # fifth of the ramp instead of the middle and mislabel independence as
    # "low". Same mistake the BSS layer's comment warns about.
    {"name": "dependence_ratio", "label": "Dependence vs independence",
     "band": None, "derived": "dependence_ratio",
     "min": -1, "max": 1, "units": "×", "diverging": True,
     "palette": ["#2166ac", "#92c5de", "#f7f7f7", "#f4a582", "#b2182b"],
     "needs": "compound",
     "desc": "How many times more likely hot-and-dry is than if the two were "
             "independent, on a log₂ scale — 0 means independent, +1 means "
             "twice as likely. Positive values are the land–atmosphere "
             "feedback showing up in the ensemble, and are why the compound "
             "probability is counted per member rather than multiplied.",
     "default": False},
]

EXPLORE_MAP = {l["name"]: l for l in EXPLORE_LAYERS}
DEFAULT_EXPLORE = next(l["name"] for l in EXPLORE_LAYERS if l.get("default"))

# ── Methodology, shown in the tab ───────────────────────────────────────────
# Written for a user who has not read the script. Each step states what was
# decided AND why, because every one of them is a defensible-but-arguable call.
METHOD_STEPS = [
    ("The forecast",
     f"ECMWF SEAS5 (system {SYSTEM}), issued monthly, 51 ensemble members, "
     f"the 2nd to 4th months after the issue month (e.g. issued September → "
     f"Oct, Nov, Dec). Each member is one plausible version of the season; "
     f"their spread is the uncertainty. The issue month itself is excluded: "
     f"it is partly fixed by conditions already present at launch."),
    ("The baseline",
     f"The same model re-run over {HINDCAST} — 24 years × 25 members = 600 "
     f"values per grid cell. Terciles are cut from THIS, the model's own "
     f"climatology, not from observations, so the model's biases cancel out."),
    ("The probabilities",
     "For each cell, the fraction of the 51 members below the 33rd percentile "
     "(drier third) and above the 67th (wetter third). With terciles, 33% is "
     "the no-information baseline."),
    ("The threshold",
     "A cell carries a signal when its probability reaches the slider value "
     "and exceeds the opposite tercile. 50% is the default; ECMWF's own charts "
     "shade from about 40%. Because 51 members quantise probability in steps "
     "of about 2 points, the figure is reported at several thresholds."),
    ("The dry mask",
     "Cells whose climatology is under the cutoff are excluded: where normal "
     "rainfall is a fraction of a millimetre, a 'wettest third' is rounding "
     "noise at any threshold. This is not a tuning choice — leave it off and "
     "desert children are counted as flood-exposed."),
    ("The exposure",
     "Child population is summed wherever the signal holds, using the same "
     "under-18 grid as every other tab, so a figure here reconciles with an "
     "Analysis-tab figure for the same region."),
    ("The skill check",
     f"Separately, the model's {HINDCAST} hindcast is compared against "
     f"observations per grid cell: rainfall against {SKILL_REFERENCE}, "
     f"temperature against ERA5-Land. The correlation asks whether the "
     f"forecast's seasonal average tracked reality year to year, which is what "
     f"tells you where the model has a historical track record. It adjusts no "
     f"figure — it is reported beside each result so a signal from a "
     f"well-predicted region can be told from one that is not."),
    ("What the skill check does not cover",
     "The correlation grades the seasonal AVERAGE, not the tercile "
     "probabilities the threshold reads. A model can rank wet and dry years "
     "sensibly while still being overconfident about how likely each third is, "
     "and the correlation cannot see that. Read it as the generous measure of "
     "the two, and treat a respectable correlation as 'worth looking at' "
     "rather than 'verified'."),
]

# Caveats that must travel with any number this tab produces.
CAVEATS = [
    "A tercile signal is relative to THIS MODEL's climatology. “Wetter than "
    "normal” means wetter than SEAS5 usually predicts — not wet in absolute "
    "terms, and not a flood forecast.",
    "Seasonal skill is regional. SEAS5 predicts well where ENSO "
    "teleconnections are strong and barely at all elsewhere, so two "
    "equal-looking signals are not equally trustworthy. Check the skill layer "
    "before relying on a figure; it is reported alongside each result but is "
    "NOT applied to it. Read it as a generous estimate: the correlation grades "
    "how well the model ranks wet and dry years, which is an easier test than "
    "whether the probabilities themselves verify. A region can correlate "
    "respectably and still add little over guessing.",
    "El Niño conditions co-occurring with a signal is not proof the signal is "
    "caused by them. The forecast is the model's net response to every "
    "initial condition it was given — ENSO is never isolated within it. The "
    "El Niño fingerprint layer is the empirical cross-check: where the "
    "forecast resembles the model's own El Niño composite, the attribution "
    "has support; where it does not, it does not.",
    "The forecast grid is 1° (~111 km). A national total is a sum over coarse "
    "cells and should not be read at sub-district precision.",
    "Each period comes from one monthly forecast issue, named with it. A "
    "newer issue replaces an older one for any month both cover, so figures "
    "for the same month can change from one issue to the next. Periods are "
    "withdrawn once their months have passed: past months are observed, not "
    "forecast.",
    "The El Niño attribution mask is seasonal. A single month is read "
    "against its forecast issue's season (e.g. November from the October "
    "issue against NDJ), so monthly attribution is approximate.",
    "A compound probability is counted per ensemble MEMBER — the fraction of "
    "members where both conditions hold at once — not the product of the two "
    "separate probabilities. Dry and hot go together physically (drier soil "
    "means less evaporative cooling), so multiplying would understate the risk "
    "where it matters most. The dependence layer shows how much difference "
    "that makes.",
    "Compound thresholds are NOT comparable with single-variable ones. "
    "Requiring two conditions together puts the no-information baseline at "
    "11.1% rather than 33%, so a 25% compound probability sits as far above "
    "chance as a 50% rainfall probability. The slider changes range when a "
    "compound signal is selected for exactly this reason.",
    "Against a 1993–2016 baseline the model puts about a fifth of land in its "
    "warmest third outright, and much of the tropics near certainty. Where "
    "nearly every member is hot, “hot and dry” carries almost the same "
    "information as “dry” — the compound signals add most in the subtropics, "
    "southern Africa and Australia among them, and least near the equator.",
    "Temperature skill is scored against ERA5-Land, a reanalysis that shares "
    "model physics and assimilation lineage with SEAS5, so it is "
    "optimistically biased relative to the CHIRPS-scored rainfall skill beside "
    "it. ERA5 also runs cool over Africa. The tercile framing judges each side "
    "against its own distribution, so a uniform bias cancels — a spatially "
    "varying one does not.",
]

# ── Download disclaimer (dialog, workbook and JSON) ─────────────────────────
DOWNLOAD_DISCLAIMER_TITLE = "Before you download"


def download_disclaimer(period_label, issue_label):
    """Disclaimer lines for one result, naming its period and issue."""
    return [
        "These figures are a seasonal FORECAST, not a measured impact.",
        f"They come from the ECMWF SEAS5 forecast issued {issue_label} for "
        f"{period_label}. They count children (under 18) living where that "
        "forecast leans towards drier, wetter, hotter or cooler conditions "
        "than normal. “Normal” is the model's own re-forecasts of "
        f"{HINDCAST.replace('-', '–')}, used only as a baseline.",
        "They do not predict drought, flooding or harm, and do not reflect "
        "vulnerability or capacity to cope.",
        "Forecast skill varies by region, and the forecast grid is 1° (about "
        "110 km at the equator), so figures are not precise below district "
        "level. Quote the range across thresholds and the forecast issue "
        "date — suggested wording below.",
    ]



DOWNLOAD_DISCLAIMER_ACK = ("I understand these are forecast estimates, "
                           "not measured impacts.")

SOURCE_URL = ("https://cds.climate.copernicus.eu/datasets/"
              "seasonal-monthly-single-levels")


# ── Info popover content ────────────────────────────────────────────────────
# Keyed by topic slug, rendered by app._hazard_info_body via _info_icon(slug,
# scope="en"). Shape matches HAZARD_INFO: description / units / source.
#
# These explain CONCEPTS beside a control — what a threshold means, why two
# baselines differ. Methodology deliberately stays visible in the Methodology
# block rather than moving in here: every step there is a defensible-but-
# arguable choice, and a user who cannot see them cannot judge whether a figure
# suits their purpose.
EN_INFO = {
    "threshold": {
        "description":
            "The share of the 51 ensemble members that must agree before a "
            "cell counts. Higher is more demanding, so counts fall as you "
            "raise it. 'Chance' is where a forecast carrying no information "
            "would sit — 33% for a single variable, because there are three "
            "equally likely thirds. A threshold near chance includes almost "
            "everything; the useful question is how far above chance it sits.",
        "units": "% of 51 ensemble members",
    },
    "threshold_compound": {
        "description":
            "One threshold on one number — NOT two filters applied in "
            "sequence. Each of the 51 members is checked against both its own "
            "rainfall and its own temperature, and counts only if both land "
            "in their extreme third. The percentage is the fraction of "
            "members that did. Counting per member is what keeps the physical "
            "link between heat and drought: multiplying two separate "
            "probabilities would assume they are independent, and they are "
            "not. Because two conditions must hold at once, chance agreement "
            "is 1 in 9 (11.1%), not 1 in 3 — so the whole scale sits lower, "
            "and 60% of members agreeing on both is already an extreme "
            "demand. A 25% compound value is as far above chance as 70% is "
            "for rainfall alone; the two numbers are not comparable.",
        "units": "% of 51 ensemble members meeting BOTH conditions",
    },
    "signal": {
        "description":
            "A signal means this forecast puts the season in the driest or "
            "wettest third of what the model itself usually predicts — not "
            "that rainfall will be low or high in absolute terms, and not "
            "that a drought or flood will occur. It is a reason to look "
            "closer. Adding the heat requirement narrows it to places where "
            "unusual rainfall and unusual warmth are forecast together.",
    },
    "heat": {
        "description":
            "Requires the same ensemble members to be in the warmest (or "
            "coolest) third as well, giving four combinations. Hot-and-dry "
            "drives water scarcity, crop failure and heat stress together; "
            "hot-and-wet is the window for malaria, dengue and waterborne "
            "disease; cool-and-wet is a flood proxy; cool-and-dry is water "
            "stress without the heat load. Two cautions. Against a 1993-2016 "
            "baseline, warming has made the warmest third close to certain "
            "across much of the tropics, so there the hot requirement adds "
            "little and the count stays near the rainfall-only figure — it "
            "discriminates most in the subtropics. And the cool combinations "
            "are genuinely rarer for the same reason, so expect smaller "
            "counts rather than reading them as an error.",
    },
    "sensitivity": {
        "description":
            "With 51 members, probability can only move in steps of about 2 "
            "points, so cells sitting near any cutoff qualify or not on "
            "essentially arbitrary grounds. Showing several thresholds side "
            "by side is what keeps a single headline figure honest — if the "
            "count swings wildly across this row, the exact number matters "
            "less than the range.",
    },
    "history": {
        "description":
            "Where this forecast sits against the model's own 24 hindcast "
            "seasons, as a share of interpretable AREA. Area, not population: "
            "the child population grid is 2025, and applying it to 1993 would "
            "imply a precision that does not exist. This is also why the "
            "percentage here usually differs from the headline figure above, "
            "which is a share of CHILDREN — for Colombia the two are 94% and "
            "67%, because children cluster in the Andes while the signal is "
            "measured across the whole territory. These are the model's "
            "hindcast seasons, not a record of observed past droughts.",
    },
    "skill": {
        "description":
            "How well the model's hindcast tracked observations in each cell, "
            "1993-2016, as a correlation. It says whether a signal here has a "
            "historical track record — it corrects nothing and changes no "
            "count. Read it as the generous measure: a correlation grades how "
            "well the model ranked wet and dry years, which is an easier test "
            "than whether its stated probabilities verify. A region can "
            "correlate respectably and still add little over guessing.",
        "units": "Pearson r",
    },
    "anomaly": {
        "description":
            "How far from normal, as opposed to how likely. The probability "
            "says the season lands in the driest third; the anomaly says "
            "whether that means 10 mm below average or 200. Both are relative "
            "to the model's own 1993-2016 climatology, so a temperature "
            "anomaly here is not an absolute forecast temperature and carries "
            "the model's cool bias.",
        "units": "mm for rainfall, °C for temperature",
    },
    "enso_attrib": {
        "description":
            "Children in the signal area who are also in places where El "
            "Niño has historically pushed rainfall the same direction this "
            "forecast now points. The remainder may be unusual for other "
            "reasons entirely. This narrows the claim from 'unusual weather "
            "is forecast' to 'unusual weather consistent with El Niño is "
            "forecast', which is the stronger statement — but it is a "
            "rainfall mask only, so for a compound signal it says nothing "
            "about whether the heat component is El Niño-related.",
        "source": "User-supplied significance mask, OND, p50/sig90",
    },
    "enso": {
        "description":
            "Observed sea-surface temperature anomaly in the Niño 3.4 region, "
            "from NOAA OISST — independent of the rainfall forecast. It shows "
            "whether an El Niño is actually underway. It does NOT prove the "
            "forecast signal is caused by one: the forecast is the model's net "
            "response to every initial condition it was given, and ENSO is "
            "never isolated within it. For the empirical check, compare the "
            "map against the El Niño rainfall fingerprint layer.",
        "units": "°C anomaly, 3-month running mean",
        "source": "NOAA OISST v2.1",
    },
}
