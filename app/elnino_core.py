# =============================================================================
# elnino_core.py — GEE logic for the El Niño seasonal outlook tab
#
# Parallel to forecast_core.py, with one structural difference that drives the
# whole module: these assets were uploaded with geeup, whose ingestion manifest
# carries no `bands` key. Earth Engine therefore names every band b1..b8 in file
# order. Band ORDER is the contract; the real names ride along in each image's
# `band_names` property. _tercile_image() is the ONLY place that renames, so no
# caller ever has to think about b-numbers.
#
# The child-population grid is imported FROM forecast_core so this tab's numbers
# reconcile with the Forecast tab's, which in turn reconcile with Analysis.
# =============================================================================

import datetime as dt
from threading import RLock

import ee
from cachetools import TTLCache, cached as _ttl_cached

from config import ADMIN_DATA, POP_VIS
from elnino_config import (
    TERCILES_COLLECTION, RAW_HINDCAST_COLLECTION, SKILL_COLLECTION,
    TERCILE_BANDS, PERIOD_MAP, DEFAULT_PERIOD, WINDOW_PERIOD,
    SIGNAL_MAP, EXPLORE_MAP,
    PROB_DEFAULT, SENSITIVITY_THRESHOLDS, INIT_TAG, to_fraction,
    SKILL_BANDS, SKILL_USABLE_MIN, SKILL_MODERATE_MIN,
    BRIER_COLLECTION, BRIER_BANDS, BSS_USEFUL_MIN, BSS_GOOD_MIN,
    NINO34_BOX, OISST_COLLECTION, OISST_SCALE, NINO34_MONTHS,
    ENSO_EVENT_MIN, ENSO_STRONG_MIN, ENSO_YEARS,
    TERCILES_T2M_COLLECTION, COMPOUND_COLLECTION, SKILL_T2M_COLLECTION,
    TERCILE_T2M_BANDS, COMPOUND_BANDS, is_compound,
    PROB_COMPOUND_DEFAULT, SENSITIVITY_COMPOUND,
    ENSO_MASK_ASSET, ENSO_MASK_BAND, ENSO_MASK_DIR,
)
from gee_core import _admin_region
from forecast_core import _forecast_pop

_TILE_TTL   = 3000   # 50 min — GEE map tokens expire
_RESULT_TTL = 3600   # 1 h — unlike the Forecast tab these assets never change
_lock = RLock()


class ElNinoError(Exception):
    """User-facing problem (missing asset, unknown period). Callbacks render
    the message inline rather than showing a traceback."""


# ---------------------------------------------------------------------------
# Band naming
# ---------------------------------------------------------------------------

# maxsize 32, not 4: there are now seven collections (terciles, terciles_t2m,
# compound, skill, skill_t2m, brier, raw_hindcast) x four periods, and a cache
# too small to hold the working set silently costs a getInfo() round trip per
# map tile instead of erroring.
@_ttl_cached(cache=TTLCache(maxsize=32, ttl=_RESULT_TTL), lock=_lock)
def _band_names(asset_id):
    """Real band names for an uploaded image, newest-first preference:

      1. the image's own `band_names` property (written by the upload script);
      2. the TERCILE_BANDS copy in elnino_config.

    Reading the property rather than trusting the config copy means a re-upload
    that adds a band cannot silently mislabel every layer in this tab — the one
    failure mode that would be invisible in the UI and wrong in every number.
    """
    try:
        prop = ee.Image(asset_id).get("band_names").getInfo()
    except Exception as e:
        raise ElNinoError(
            f"Cannot read {asset_id}. The El Niño assets may not be shared "
            f"with this app's service account. ({str(e)[:120]})")
    if prop:
        names = [b.strip() for b in str(prop).split(",") if b.strip()]
        if names:
            return names
    return list(TERCILE_BANDS)


def tercile_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{TERCILES_COLLECTION}/terciles_{INIT_TAG}_{period}"


def _tercile_image(period):
    """The terciles image for a period, with its real band names restored.

    Every other function in this module goes through here, so b1..b8 never
    escapes into calling code.
    """
    asset = tercile_asset(period)
    img = ee.Image(asset)
    names = _band_names(asset)
    # bandNames() would cost a round trip per call; the manifest guarantees the
    # count, and _band_names() already validated the source.
    return img.rename(names)


def tercile_t2m_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{TERCILES_T2M_COLLECTION}/terciles_t2m_{INIT_TAG}_{period}"


def _tercile_t2m_image(period):
    """Temperature terciles, band names restored. SEVEN bands — no dry_mask."""
    asset = tercile_t2m_asset(period)
    try:
        names = _band_names(asset)
    except ElNinoError:
        names = list(TERCILE_T2M_BANDS)
    return ee.Image(asset).rename(names)


def compound_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{COMPOUND_COLLECTION}/compound_{INIT_TAG}_{period}"


def _compound_image(period):
    """The compound image for a period, band names restored."""
    asset = compound_asset(period)
    try:
        names = _band_names(asset)
    except ElNinoError:
        names = list(COMPOUND_BANDS)
    return ee.Image(asset).rename(names)


def _dry_mask_for(period):
    """THE dry mask, always from the precipitation terciles.

    One source for every layer. The temperature terciles have no dry_mask band
    at all, and the compound image carries an inherited copy — routing every
    consumer here means no layer can end up masked by a different rule than its
    neighbour, and nothing ever asks the 7-band image for a band it lacks.
    """
    return _tercile_image(period).select("dry_mask")


def skill_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{SKILL_COLLECTION}/skill_{INIT_TAG}_{period}"


def skill_t2m_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{SKILL_T2M_COLLECTION}/skill_t2m_{INIT_TAG}_{period}"


def _skill_t2m_image(period):
    """Temperature skill (vs ERA5-Land), band names restored."""
    asset = skill_t2m_asset(period)
    try:
        names = _band_names(asset)
    except ElNinoError:
        names = list(SKILL_BANDS)
    return ee.Image(asset).rename(names)


def _skill_image(period):
    """The skill image for a period, band names restored.

    Exported with Export.image.toAsset rather than geeup, so its band names DO
    survive — but it is read through _band_names anyway so every collection in
    this feature uses one code path.
    """
    asset = skill_asset(period)
    try:
        names = _band_names(asset)
    except ElNinoError:
        names = list(SKILL_BANDS)
    return ee.Image(asset).rename(names)


def brier_asset(period):
    if period not in PERIOD_MAP:
        raise ElNinoError(f"Unknown period '{period}'.")
    return f"{BRIER_COLLECTION}/brier_{INIT_TAG}_{period}"


def _brier_image(period):
    """The Brier skill image for a period, band names restored."""
    asset = brier_asset(period)
    try:
        names = _band_names(asset)
    except ElNinoError:
        names = list(BRIER_BANDS)
    return ee.Image(asset).rename(names)


def _hindcast_structure():
    """({lead_key: [band names]}, ordered lead keys) for the hindcast images.

    Derived from the band names, never hardcoded: the collection was 4x25 when
    the season was Sep-Dec and is 3x25 now, so a stale constant would select a
    lead that no longer exists. Shared by the historical context and the ENSO
    composite so they cannot disagree about the structure.
    """
    names = _band_names(f"{RAW_HINDCAST_COLLECTION}/hc_1993{INIT_TAG[4:]}")
    by_lead = {}
    for nm in names:
        by_lead.setdefault(nm.split("_")[0], []).append(nm)
    keys = sorted(by_lead, key=lambda k: int(k[1:]))
    return names, by_lead, keys


def _hindcast_season_mean(year, names, by_lead, lead_keys):
    """Ensemble-mean season total for one hindcast year, mm.

    Each band is a MONTHLY total, so the season total is the sum over leads of
    the per-lead member means. Averaging every band at once would give a
    monthly mean and compare one month against a multi-month quantity.
    """
    img = ee.Image(
        f"{RAW_HINDCAST_COLLECTION}/hc_{year}{INIT_TAG[4:]}").rename(names)
    per_lead = [img.select(by_lead[k]).reduce(ee.Reducer.mean())
                for k in lead_keys]
    total = per_lead[0]
    for extra in per_lead[1:]:
        total = total.add(extra)
    return total.rename("m")


# ---------------------------------------------------------------------------
# Masks and derived layers
# ---------------------------------------------------------------------------

def _signal_mask(img, signal, threshold, apply_dry_mask=True, period=None):
    """Boolean image: 1 where `signal` holds at `threshold` PERCENT.

    `threshold` is a percentage (0-100), as everything user-facing is; the
    probability bands are 0-1 fractions. This is the one place the two meet, so
    the conversion lives here and nowhere else.

    Deliberately re-derived from the raw probability bands rather than read from
    the stored `cat` band, which is frozen at 50%. That is the whole reason the
    upload ships p_below/p_above unmasked: the threshold stays tunable here.

    A cell qualifies when its probability reaches the threshold AND exceeds the
    opposite tercile — without the second test, a cell could be flagged both dry
    and wet near the baseline.

    COMPOUND signals read a different asset and drop that second test; `period`
    is required for them, because `img` is the terciles image the caller already
    had. Callers pass it unconditionally and it is ignored for the rest.
    """
    cfg = SIGNAL_MAP.get(signal)
    if not cfg:
        raise ElNinoError(f"Unknown signal '{signal}'.")

    # A union signal ("either") is the OR of its components. They are mutually
    # exclusive by construction — dry needs p_below > p_above and wet the
    # reverse — so the union is also exactly their sum, with no double count.
    if cfg.get("union_of"):
        parts = [_signal_mask(img, n, threshold, apply_dry_mask, period)
                 for n in cfg["union_of"]]
        mask = parts[0]
        for extra in parts[1:]:
            mask = mask.Or(extra)
        return mask.rename("signal")

    if cfg.get("compound"):
        if period is None:
            raise ElNinoError(
                f"Signal '{signal}' is a compound signal and needs a period.")
        cimg = _compound_image(period)
        # NO `.gt(other)` test. For dry/wet that test is what guarantees
        # exclusivity, but the compound classes are not exclusive: different
        # members can support hot-dry and hot-wet in the same cell, so there is
        # no "opposite" band to out-rank. cat_compound resolves overlap by
        # argmax when a single class is needed.
        mask = cimg.select(cfg["band"]).gte(to_fraction(threshold))
        if apply_dry_mask:
            # From the precipitation terciles via the one shared source, not
            # the compound image's inherited copy, so every layer in the tab is
            # masked by the same rule.
            mask = mask.And(_dry_mask_for(period).eq(1))
        return mask.rename("signal")

    p    = img.select(cfg["band"])
    other = img.select(cfg["other"])
    mask = p.gte(to_fraction(threshold)).And(p.gt(other))
    if apply_dry_mask:
        mask = mask.And(img.select("dry_mask").eq(1))
    return mask.rename("signal")


def _d_signal_prob(img, ctx):
    """Selected signal's probability, scaled to 0-100 to match the UI.

    For the union signal there is no single probability band, so show whichever
    tercile the cell leans towards — max(p_below, p_above). That is the
    probability of the signal the cell would actually be flagged for, which is
    what a reader of this layer is asking.
    """
    cfg = SIGNAL_MAP[ctx["signal"]]
    if cfg.get("union_of"):
        bands = [SIGNAL_MAP[n]["band"] for n in cfg["union_of"]]
        out = img.select(bands[0])
        for b in bands[1:]:
            out = out.max(img.select(b))
        return out.multiply(100).rename("value")
    # A compound signal's band lives in the compound asset, not the terciles
    # image this function is handed. Needed because signal_prob is now THE
    # signal layer for every signal, compound included.
    if cfg.get("compound"):
        return (_compound_image(ctx["period"]).select(cfg["band"])
                .multiply(100).rename("value"))
    return img.select(cfg["band"]).multiply(100).rename("value")


def _d_signal_strength(img, ctx):
    """p_above - p_below as percentage points: one signed layer instead of two.

    Scaled like every other probability shown, so -100..+100 reads on the same
    scale as the individual probabilities rather than a separate -1..1 one.
    """
    return img.select("p_above").subtract(img.select("p_below")) \
              .multiply(100).rename("value")


def _d_anom_pct(img, ctx):
    """Anomaly as a share of climatology.

    Guarded against the near-zero climatology cells that the dry mask exists to
    flag: without the guard those cells divide to enormous percentages and
    dominate the colour stretch everywhere else.
    """
    clim = img.select("clim_mean_mm")
    pct  = img.select("anom_mm").divide(clim.max(1)).multiply(100)
    return pct.updateMask(clim.gte(1)).rename("value")


def _d_category(img, ctx):
    """Discrete classes at the live threshold: 0 no signal, 1 dry, 2 wet.

    Contiguous values, NOT the stored band's 0/1/3. A palette maps evenly
    across min..max, so a gap at 2 would make Earth Engine blend the dry and
    wet colours through it and paint no-signal cells a midpoint shade that
    belongs to no class.

    The stored `cat` band is also frozen at 50%, so recomputing here is what
    keeps the map honest when the slider moves.

    Dry and wet are mutually exclusive by construction — dry needs
    p_below > p_above and wet the reverse — so the `where` order cannot
    silently overwrite one with the other.
    """
    thr = float(ctx.get("threshold", PROB_DEFAULT))
    keep = ctx.get("apply_dry_mask", True)
    dry = _signal_mask(img, "dry", thr, keep, ctx["period"])
    wet = _signal_mask(img, "wet", thr, keep, ctx["period"])
    return ee.Image(0).where(dry, 1).where(wet, 2).rename("value").toFloat()


def _d_skill_class(img, ctx):
    """Skill banded into the four classes, 0..3.

    0 is "not assessed", not "no skill": CHIRPS is land-only and spans
    50N-50S, so ocean and high-latitude cells have nothing to score against.
    Unmasking to 0 is what keeps that distinction visible — left masked they
    would simply vanish, which reads as "nothing here" rather than "unknown".
    """
    r = _skill_image(ctx["period"]).select("skill_r")
    classed = (ee.Image(1)
               .where(r.gte(SKILL_MODERATE_MIN), 2)
               .where(r.gte(SKILL_USABLE_MIN), 3)
               .updateMask(r.mask())
               .unmask(0))
    return classed.rename("value").toFloat()


def _d_skill_r(img, ctx):
    """The raw correlation, so the class cutoffs can be judged not trusted."""
    return _skill_image(ctx["period"]).select("skill_r").rename("value")


def _brier_band_for(signal):
    """Which BSS band a signal maps to, or None when there is no honest answer.

    The union has no score of its own — it is two different forecasts — so it
    falls back to the dry band with the UI naming the limitation rather than
    inventing a combined number.

    COMPOUND signals return None. The Brier collection scores single-variable
    terciles only (bss_dry, bss_wet); there is no verification of the JOINT
    event, and showing the rainfall score beside a compound figure would read
    as though the compound forecast had been validated when it has not.
    Building that properly needs a leave-one-out Brier on the joint outcome
    against a joint base rate — a separate script, not a band rename.
    """
    cfg = SIGNAL_MAP.get(signal) or {}
    if cfg.get("compound"):
        return None
    name = cfg["union_of"][0] if cfg.get("union_of") else signal
    return f"bss_{name}"


def _d_bss(img, ctx):
    """Brier skill score for the selected signal's tercile probabilities."""
    band = _brier_band_for(ctx.get("signal", "dry"))
    if band is None:
        # Nothing to show for a compound signal. Fully masked rather than a
        # misleading stand-in; the layer description says why.
        return ee.Image(0).updateMask(ee.Image(0)).rename("value").toFloat()
    return _brier_image(ctx["period"]).select(band).rename("value")


def _d_bss_class(img, ctx):
    """BSS banded into the four classes, 0..3.

    Class 1 is "no better than guessing" (BSS <= 0), not "low" — the zero
    boundary is meaningful for a skill SCORE in a way that no correlation
    cutoff is. 0 stays "not assessed" for cells CHIRPS cannot score.
    """
    band = _brier_band_for(ctx.get("signal", "dry"))
    b = _brier_image(ctx["period"]).select(band)
    classed = (ee.Image(1)
               .where(b.gt(BSS_USEFUL_MIN), 2)
               .where(b.gte(BSS_GOOD_MIN), 3)
               .updateMask(b.mask())
               .unmask(0))
    return classed.rename("value").toFloat()


def _d_enso_composite(img, ctx):
    """Mean model season rainfall in El Nino hindcast years minus neutral ones.

    The empirical answer to "how do we know this is El Nino?". It is the
    model's own fingerprint: where the live forecast resembles this pattern,
    attributing it to ENSO has support from the model's history; where it does
    not, it does not.

    Only meaningful for the aggregate season — a hindcast image holds every
    lead as bands, and the monthly periods would need a different selection.
    Returns a fully masked image for those rather than a misleading one.
    """
    if ctx.get("period") != WINDOW_PERIOD:
        return ee.Image(0).selfMask().rename("value")
    names, by_lead, lead_keys = _hindcast_structure()
    warm = [_hindcast_season_mean(y, names, by_lead, lead_keys)
            for y in ENSO_YEARS["elnino"]]
    cold = [_hindcast_season_mean(y, names, by_lead, lead_keys)
            for y in ENSO_YEARS["neutral"]]
    warm_mean = ee.ImageCollection(warm).mean()
    cold_mean = ee.ImageCollection(cold).mean()
    return warm_mean.subtract(cold_mean).rename("value")


def _d_t2m_signal_prob(img, ctx):
    """Probability of the WARMEST tercile, 0-100.

    Always p_above, not the selected signal: this layer answers "how likely is
    a hot season here", which is what makes the compound layers readable. Much
    of the tropics sits near 100 against a 1993-2016 baseline.
    """
    p = _tercile_t2m_image(ctx["period"]).select("p_above")
    return p.multiply(100).rename("value").toFloat()


def _d_compound_prob(img, ctx):
    """Selected compound signal's joint probability, 0-100.

    Falls back to hot-dry when a single-variable signal is selected, so the
    layer still renders something meaningful rather than erroring — the
    description names it as the headline compound signal.
    """
    cfg = SIGNAL_MAP.get(ctx.get("signal") or "")
    band = cfg["band"] if cfg and cfg.get("compound") else "p_hotdry"
    p = _compound_image(ctx["period"]).select(band)
    return p.multiply(100).rename("value").toFloat()


def _d_category_compound(img, ctx):
    """The four compound classes, 0..4, by ARGMAX.

    Not chained `where`: unlike dry/wet these classes are NOT mutually
    exclusive, because different ensemble members can support different
    combinations in one cell. The `n_at_top` test is what makes the chain
    below safe — a tie falls to 0 rather than to whichever branch is last. A chain would silently let whichever branch is
    written last win, so the strongest class is selected explicitly and ties
    fall to 0.

    Contiguous values for the same palette reason as _d_category.
    """
    thr = to_fraction(float(ctx.get("threshold", PROB_COMPOUND_DEFAULT)))
    c = _compound_image(ctx["period"])
    hd = c.select("p_hotdry")
    cw = c.select("p_coolwet")
    hw = c.select("p_hotwet")
    cd = c.select("p_cooldry")

    top = hd.max(cw).max(hw).max(cd)
    # A class wins only if it IS the max and nothing else equals it.
    n_at_top = (hd.eq(top).add(cw.eq(top)).add(hw.eq(top)).add(cd.eq(top)))
    winner = (ee.Image(0)
              .where(hd.eq(top), 1)
              .where(cw.eq(top), 2)
              .where(hw.eq(top), 3)
              .where(cd.eq(top), 4))
    out = winner.where(top.lt(thr).Or(n_at_top.gt(1)), 0)
    if ctx.get("apply_dry_mask", True):
        out = out.where(_dry_mask_for(ctx["period"]).neq(1), 0)
    return out.rename("value").toFloat()


def _d_dependence_ratio(img, ctx):
    """log2 of the hot-dry dependence ratio.

    log2, not the raw ratio: the neutral point is 1.0 (independent), and a
    diverging palette stretched over a raw 0.5..3 would land 1.0 at a fifth of
    the ramp and colour independence as if it were "low". On a log2 scale 0 is
    independent, +1 is twice as likely as independence, -1 is half.
    """
    r = _compound_image(ctx["period"]).select("dependence_ratio")
    # Guard the log: a cell where either marginal is 0 has an undefined ratio.
    return (r.max(0.001).log().divide(ee.Number(2).log())
             .updateMask(r.gt(0))
             .rename("value").toFloat())


def _d_skill_r_t2m(img, ctx):
    """Temperature correlation against ERA5-Land."""
    return _skill_t2m_image(ctx["period"]).select("skill_r").rename("value")


_DERIVED = {
    "signal_prob":       _d_signal_prob,
    "signal_strength":   _d_signal_strength,
    "anom_pct":          _d_anom_pct,
    "category":          _d_category,
    "skill_class":       _d_skill_class,
    "skill_r":           _d_skill_r,
    "bss":               _d_bss,
    "bss_class":         _d_bss_class,
    "enso_composite":    _d_enso_composite,
    "t2m_signal_prob":   _d_t2m_signal_prob,
    "compound_prob":     _d_compound_prob,
    "category_compound": _d_category_compound,
    "dependence_ratio":  _d_dependence_ratio,
    "skill_r_t2m":       _d_skill_r_t2m,
}


def build_explore_image(period, layer, signal, threshold, apply_dry_mask=True):
    """A single-band ee.Image for the map, named "value"."""
    cfg = EXPLORE_MAP.get(layer)
    if not cfg:
        raise ElNinoError(f"Unknown layer '{layer}'.")
    img = _tercile_image(period)
    # `period` is in the context because the skill and composite transforms read
    # OTHER assets keyed by it, rather than bands of `img`.
    ctx = {"signal": signal, "threshold": threshold,
           "apply_dry_mask": apply_dry_mask, "period": period}

    if cfg.get("derived"):
        out = _DERIVED[cfg["derived"]](img, ctx)
    elif cfg.get("needs") == "skill":
        out = _skill_image(period).select(cfg["band"]).rename("value")
    elif cfg.get("needs") == "brier":
        out = _brier_image(period).select(cfg["band"]).rename("value")
    elif cfg.get("source") == "t2m":
        # Plain bands of the TEMPERATURE terciles (anom_c, fc_mean_c). That
        # image has only 7 bands and no dry_mask, which is why the mask below
        # comes from _dry_mask_for() and never from the image being displayed.
        out = _tercile_t2m_image(period).select(cfg["band"]).rename("value")
    elif cfg.get("source") == "compound":
        out = _compound_image(period).select(cfg["band"]).rename("value")
    else:
        out = img.select(cfg["band"]).rename("value")

    # The dry mask hides cells where a tercile is noise. It must NOT be applied
    # to the dry_mask layer itself (which exists to show those cells), to the
    # climatology that defines them, nor to anything describing the FORECAST
    # rather than this season's rainfall — skill, the ENSO composite and the
    # dependence ratio are properties of the model, and masking them by this
    # season's aridity would blank cells whose value is perfectly well known.
    exempt = ("dry_mask", "clim_mean_mm", "skill_class", "skill_r",
              "bss", "bss_class", "enso_composite",
              "skill_r_t2m", "dependence_ratio")
    if apply_dry_mask and cfg["name"] not in exempt:
        # From the precipitation terciles always — the single mask source, so a
        # temperature layer is masked by exactly the same rule as a rainfall
        # one and the 7-band image is never asked for a band it lacks.
        out = out.updateMask(_dry_mask_for(period).eq(1))
    return out


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------

def _exposure_stats(period, signal, threshold, feature_ucode, admin_level,
                    apply_dry_mask=True):
    """Children under the signal, plus the totals, for one threshold.

    Mirrors forecast_core.compute_forecast_exposure — same 1 km population grid,
    same chunk collection, same reduceRegions -> reduceColumns shape — so a
    figure here is comparable with one from the Forecast tab.
    """
    pop = _forecast_pop()
    childpop = pop["childpop"]

    img  = _tercile_image(period)
    mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)

    exposed = childpop.updateMask(mask).rename("exposed")
    combined = (
        ee.Image.cat([exposed])
        # Boys/girls UNDER THE SIGNAL — the breakdown of `exposed`, which is
        # what the panel and workbook present. The region-wide totals below
        # are kept for the denominator, but were once shown in their place.
        .addBands(pop["childpop_m"].updateMask(mask).rename("exposed_male"))
        .addBands(pop["childpop_f"].updateMask(mask).rename("exposed_female"))
        .addBands(childpop.rename("total_population"))
        .addBands(pop["childpop_m"].rename("total_population_male"))
        .addBands(pop["childpop_f"].rename("total_population_female"))
        # Children in cells the dry mask excludes — reported so a small total
        # is never mistaken for "no signal" when it is really "not interpretable".
        .addBands(childpop.updateMask(img.select("dry_mask").eq(0))
                  .rename("masked_population"))
    )

    chunks = ee.FeatureCollection(
        ADMIN_DATA[admin_level]["chunk_asset"]
    ).filter(ee.Filter.eq("ucode", feature_ucode))

    band_names = combined.bandNames()
    tiles = combined.reduceRegions(
        collection=chunks, reducer=ee.Reducer.sum(),
        scale=pop["pop_res"], tileScale=1,
    )
    sums = tiles.reduceColumns(
        ee.Reducer.sum().repeat(band_names.size()), band_names)
    return ee.Dictionary.fromLists(band_names, sums.get("sum"))


@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def compute_elnino_exposure(period, signal, threshold, feature_ucode,
                            admin_level, apply_dry_mask=True):
    """Headline exposure plus the sensitivity spread.

    The spread is not decoration. With 51 members, probability moves in steps of
    1/51 ~ 0.0196, so a cell sitting near the cutoff flips category on arbitrary
    grounds. Reporting 0.40 / 0.50 / 0.60 alongside the chosen value is what
    stops a single number implying precision the ensemble does not have.

    All thresholds are evaluated in ONE getInfo() — a round trip per threshold
    would make the panel three times slower for no benefit.
    """
    thr = float(threshold)
    main = _exposure_stats(period, signal, thr, feature_ucode, admin_level,
                           apply_dry_mask)

    # Keys are the integer percentage, so the UI can compare them to the
    # selected threshold without re-parsing a formatted float.
    # Compound signals read against a 1/9 baseline, so the rainfall ladder
    # (50-80) would sit far above anything a joint count reaches and the row
    # would be all zeros — which says nothing about sensitivity.
    ladder = (SENSITIVITY_COMPOUND if is_compound(signal)
              else SENSITIVITY_THRESHOLDS)
    # Always include the threshold actually in use, so the row brackets the
    # headline figure even at a non-default value.
    ladder = sorted({int(round(t)) for t in ladder} | {int(round(threshold))})
    extras = {str(int(t)): _exposure_stats(
                  period, signal, t, feature_ucode, admin_level, apply_dry_mask
              ).get("exposed")
              for t in ladder}

    try:
        out = ee.Dictionary({
            "main": main, "sensitivity": ee.Dictionary(extras),
        }).getInfo()
    except Exception as e:
        raise ElNinoError(f"Computation failed: {str(e)[:200]}")

    stats = dict(out.get("main") or {})
    stats["_sensitivity"] = {k: (v or 0)
                             for k, v in (out.get("sensitivity") or {}).items()}
    stats["_pop_resolution_m"] = pop_resolution()
    stats["_threshold"] = thr
    return stats


def pop_resolution():
    try:
        return _forecast_pop()["resolution_m"]
    except Exception:
        return 1000


# ---------------------------------------------------------------------------
# Historical context — how unusual is this year?
# ---------------------------------------------------------------------------

@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def forecast_area_share(period, signal, threshold, feature_ucode, admin_level):
    """Share of the region's INTERPRETABLE AREA under the signal, 0-100.

    The forecast-side counterpart of hindcast_area_share, and the only figure
    comparable with it. The results panel's headline percentage is a share of
    CHILDREN, which is a different quantity entirely — for Colombia the two are
    94% and 67%, because children cluster in the Andes while the signal is
    measured over the whole territory including the Amazon. Comparing the
    population share against the hindcast's area distribution silently
    overstates how unusual a year looks.
    """
    img    = _tercile_image(period)
    mask   = _signal_mask(img, signal, threshold, True, period)
    usable = img.select("dry_mask").eq(1)
    region = _admin_region(admin_level, feature_ucode)
    try:
        val = mask.updateMask(usable).rename("m").reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region,
            scale=100000, bestEffort=True, maxPixels=1e9,
        ).get("m").getInfo()
    except Exception as e:
        print(f"[elnino] forecast area share unavailable: {str(e)[:160]}",
              flush=True)
        return None
    return None if val is None else round(float(val) * 100, 1)


@_ttl_cached(cache=TTLCache(maxsize=16, ttl=_RESULT_TTL), lock=_lock)
def hindcast_area_share(period, signal, threshold, feature_ucode, admin_level):
    """Share of the region's usable area under the signal in each hindcast year.

    Answers the question the forecast alone cannot: is 2026 actually unusual, or
    an ordinary year? Runs on AREA rather than population — the population grid
    is 2025 and applying it to 1993 would imply a precision that does not exist.

    Only defined for the aggregate season window: a hindcast image holds every
    lead month as bands, so a per-month variant would need a different band
    selection and is left out rather than guessed at.
    """
    if period != WINDOW_PERIOD:
        return []

    cfg = SIGNAL_MAP.get(signal)
    if not cfg:
        raise ElNinoError(f"Unknown signal '{signal}'.")

    region = _admin_region(admin_level, feature_ucode)
    fc_img = _tercile_image(period)
    clim   = fc_img.select("clim_mean_mm")      # season total, mm
    usable = fc_img.select("dry_mask").eq(1)

    # Hindcast images carry b1..bN, so they need the same rename as the
    # terciles before any L{lead}_m{member} pattern can match. The names are
    # identical across the 24 images, so read them once — and DERIVE the leads
    # and members from them rather than hardcoding: this collection was 4x25
    # when the season was Sep-Dec and is 3x25 now, and a stale constant would
    # select a lead that no longer exists.
    hc_names, by_lead, lead_keys = _hindcast_structure()
    if not lead_keys:
        print(f"[elnino] hindcast bands unrecognised: {hc_names[:4]}", flush=True)
        return []

    # Each band is a MONTHLY total, so the season total for one member is the
    # sum across its lead bands; the ensemble-mean season total is therefore the
    # sum over leads of the per-lead means. Averaging every band at once would
    # give a MONTHLY mean, comparing one month against a multi-month climatology.
    def season_mean(img):
        img = ee.Image(img).rename(hc_names)
        per_lead = [img.select(by_lead[k]).reduce(ee.Reducer.mean())
                    for k in lead_keys]
        total = per_lead[0]
        for extra in per_lead[1:]:
            total = total.add(extra)
        return total.rename("m")

    def year_share(img):
        total = season_mean(img)
        diff  = total.subtract(clim)
        hit   = diff.lt(0) if cfg["name"] == "dry" else diff.gt(0)
        share = hit.updateMask(usable).rename("m").reduceRegion(
            reducer=ee.Reducer.mean(), geometry=region,
            scale=100000, bestEffort=True, maxPixels=1e9,
        ).get("m")
        return ee.Feature(None, {"year": ee.Image(img).get("year"),
                                 "share": share})

    coll = ee.ImageCollection(RAW_HINDCAST_COLLECTION)

    try:
        rows = ee.FeatureCollection(coll.map(year_share)).getInfo()
    except Exception as e:
        # Context is optional — the panel renders without it — but a silent
        # empty list once hid a real band-naming bug, so say so in the log.
        print(f"[elnino] hindcast context unavailable: {str(e)[:160]}",
              flush=True)
        return []

    out = []
    for f in rows.get("features", []):
        p = f.get("properties") or {}
        if p.get("share") is not None and p.get("year") is not None:
            out.append({"year": int(p["year"]),
                        "share": round(float(p["share"]) * 100, 1)})
    return sorted(out, key=lambda r: r["year"])


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def brier_available():
    """Whether the Brier collection exists and holds images.

    Built by a separate script and legitimately absent, so the tab hides its
    layers and caption rather than erroring.
    """
    try:
        return ee.ImageCollection(BRIER_COLLECTION).size().getInfo() > 0
    except Exception:
        return False


# Each of these gates a group of layers the same way brier_available() does, so
# the temperature and compound work can be absent — or half-finished, with the
# terciles uploaded but the skill still exporting — without breaking the tab.
@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def t2m_available():
    try:
        return ee.ImageCollection(
            TERCILES_T2M_COLLECTION).size().getInfo() > 0
    except Exception:
        return False


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def compound_available():
    try:
        return ee.ImageCollection(COMPOUND_COLLECTION).size().getInfo() > 0
    except Exception:
        return False


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def skill_t2m_available():
    try:
        return ee.ImageCollection(SKILL_T2M_COLLECTION).size().getInfo() > 0
    except Exception:
        return False


@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def signal_bss(period, signal, threshold, feature_ucode, admin_level,
               apply_dry_mask=True):
    """Mean Brier skill score over the region's SIGNAL area.

    The sharper companion to signal_skill_share: that reports whether the model
    predicts the AMOUNT well here, this whether its PROBABILITIES verify — the
    quantity the exposure threshold actually reads.

    Returns {"bss", "useful_pct"} or None, failing soft like its sibling.
    """
    if not brier_available():
        return None
    try:
        img = _tercile_image(period)
        mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)
        b = _brier_image(period).select(_brier_band_for(signal))
        region = _admin_region(admin_level, feature_ucode)
        stats = (ee.Image.cat([b.rename("bss"),
                               b.gt(BSS_USEFUL_MIN).unmask(0).rename("useful")])
                 .updateMask(mask)
                 .reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                               scale=100000, bestEffort=True, maxPixels=1e9)
                 .getInfo())
        if stats.get("bss") is None:
            return None
        # Flag the union case so the UI can say the score is dry-only rather
        # than presenting it as a combined figure that does not exist.
        is_union = bool((SIGNAL_MAP.get(signal) or {}).get("union_of"))
        return {"bss": round(float(stats["bss"]), 3),
                "useful_pct": round(float(stats.get("useful") or 0) * 100, 1),
                "is_union": is_union}
    except Exception as e:
        print(f"[elnino] Brier skill unavailable: {str(e)[:160]}", flush=True)
        return None


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def skill_available():
    """Whether the skill collection exists and holds images.

    Absence is a supported state — the collection is built by a separate
    script — so the tab hides the skill layers and caption rather than erroring.
    """
    try:
        return ee.ImageCollection(SKILL_COLLECTION).size().getInfo() > 0
    except Exception:
        return False


@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def signal_skill_share(period, signal, threshold, feature_ucode, admin_level,
                       apply_dry_mask=True):
    """Of the region's SIGNAL area, what share has usable forecast skill.

    The honest qualifier on an exposure figure: a large count in a region the
    model has never predicted well means less than the same count where it has.
    Deliberately area-based and reported only — nothing here filters the
    exposure number, so the published figures stay comparable.

    Returns {"usable_pct", "assessed_pct"} or None. Fails soft like
    forecast_area_share: the panel renders without it.
    """
    if not skill_available():
        return None
    try:
        img = _tercile_image(period)
        mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)
        r = _skill_image(period).select("skill_r")
        region = _admin_region(admin_level, feature_ucode)

        # Over signal cells only: what fraction is scored at all, and of those
        # what fraction clears the usable bar. Two numbers because "40% usable"
        # means something very different when only half the area was assessed.
        assessed = r.mask().rename("assessed")
        usable = r.gte(SKILL_USABLE_MIN).unmask(0).rename("usable")
        stats = (ee.Image.cat([assessed, usable])
                 .updateMask(mask)
                 .reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                               scale=100000, bestEffort=True, maxPixels=1e9)
                 .getInfo())
        a, u = stats.get("assessed"), stats.get("usable")
        if a is None or u is None:
            return None
        return {"assessed_pct": round(float(a) * 100, 1),
                "usable_pct": round(float(u) * 100, 1)}
    except Exception as e:
        print(f"[elnino] skill share unavailable: {str(e)[:160]}", flush=True)
        return None


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def enso_mask_available():
    try:
        return ee.Image(ENSO_MASK_ASSET).bandNames().size().getInfo() > 0
    except Exception:
        return False


@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def signal_anomalies(period, signal, threshold, feature_ucode, admin_level,
                     apply_dry_mask=True):
    """Mean anomaly over the signal area — HOW FAR from normal, not how likely.

    The probability says the season lands in the driest third; this says
    whether that means 10 mm below average or 200. Area-weighted over the
    signal cells only, because an average over the whole country would be
    diluted by everywhere the signal does not hold.

    Returns {"anom_mm", "anom_pct", "anom_c"} with None for anything
    unavailable, or None if the whole reduction fails. Fails soft like
    signal_skill_share: a diagnostic must never cost the user their count.
    """
    try:
        img = _tercile_image(period)
        mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)
        region = _admin_region(admin_level, feature_ucode)

        bands = [img.select("anom_mm").rename("anom_mm")]
        # Percentage of the model's own climatology, which travels better
        # across climates than millimetres: -40 mm means little without
        # knowing whether normal is 50 mm or 500.
        clim = img.select("clim_mean_mm")
        bands.append(img.select("anom_mm").divide(clim.max(1))
                     .multiply(100).rename("anom_pct"))
        want_t2m = False
        try:
            want_t2m = t2m_available()
        except Exception:
            want_t2m = False
        if want_t2m:
            bands.append(
                _tercile_t2m_image(period).select("anom_c").rename("anom_c"))

        stats = (ee.Image.cat(bands)
                 .updateMask(mask)
                 .reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                               scale=100000, bestEffort=True, maxPixels=1e9)
                 .getInfo())
        out = {}
        for k in ("anom_mm", "anom_pct", "anom_c"):
            v = stats.get(k)
            out[k] = round(float(v), 1) if v is not None else None
        return out if any(v is not None for v in out.values()) else None
    except Exception as e:
        print(f"[elnino] anomalies unavailable: {str(e)[:160]}", flush=True)
        return None


@_ttl_cached(cache=TTLCache(maxsize=64, ttl=_RESULT_TTL), lock=_lock)
def enso_attributable(period, signal, threshold, feature_ucode, admin_level,
                      apply_dry_mask=True):
    """Children in the signal area AND where El Nino historically pushes the
    same way.

    Narrows the claim from "unusual weather is forecast" to "unusual weather
    CONSISTENT WITH El Nino is forecast". Reported alongside, never applied —
    the headline count stays comparable with every published figure.

    The mask is DIRECTIONAL: a dry signal counts only on -1 cells, wet only on
    +1. Compound signals inherit their rainfall component's direction, which
    makes "attributable" a weaker claim for them — the mask says nothing about
    whether the heat component is El Nino-related, and the UI must say so.

    Same asset and direction map as export_reliability_table.py, so a figure
    here and a figure in the workbook agree.

    Returns {"children", "pct", "rainfall_only"} or None.
    """
    if not enso_mask_available():
        return None
    direction = ENSO_MASK_DIR.get(signal)
    if direction is None:
        return None
    try:
        img = _tercile_image(period)
        mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)
        enso = ee.Image(ENSO_MASK_ASSET).rename(ENSO_MASK_BAND)
        consistent = enso.eq(direction)
        region = _admin_region(admin_level, feature_ucode)
        pop = _forecast_pop()["childpop"]

        stats = (ee.Image.cat([
                    pop.updateMask(mask).rename("signal"),
                    pop.updateMask(mask.And(consistent)).rename("attrib")])
                 .reduceRegion(reducer=ee.Reducer.sum(), geometry=region,
                               scale=1000, bestEffort=True, maxPixels=1e9)
                 .getInfo())
        sig, att = stats.get("signal"), stats.get("attrib")
        if sig is None or att is None:
            return None
        sig, att = float(sig), float(att)
        return {"children": int(round(att)),
                "pct": round(100.0 * att / sig, 1) if sig else 0.0,
                # True when the signal has a temperature component the mask
                # does not constrain.
                "rainfall_only": bool(is_compound(signal))}
    except Exception as e:
        print(f"[elnino] ENSO attribution unavailable: {str(e)[:160]}",
              flush=True)
        return None


# ---------------------------------------------------------------------------
# ENSO context
# ---------------------------------------------------------------------------

def _nino34_month(year, month):
    """Mean Nino 3.4 SST anomaly for one month, in degrees C.

    OISST stores SST and its anomaly as int16 with a 0.01 scale factor —
    without the multiply this returns ~266 instead of ~2.66.
    """
    start = ee.Date.fromYMD(year, month, 1)
    box = ee.Geometry.Rectangle(NINO34_BOX, None, False)
    return (ee.ImageCollection(OISST_COLLECTION).select("anom")
            .filterDate(start, start.advance(1, "month"))
            .mean().multiply(OISST_SCALE)
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=box,
                          scale=25000, bestEffort=True)
            .get("anom"))


@_ttl_cached(cache=TTLCache(maxsize=4, ttl=_RESULT_TTL), lock=_lock)
def nino34_series(months=NINO34_MONTHS):
    """Monthly Nino 3.4 anomaly for the last `months`, oldest first.

    Every month is packed into ONE ee.Dictionary and fetched in a single
    getInfo(), as compute_elnino_exposure does for its sensitivity spread — a
    round trip per month would make the panel unusably slow.

    The most recent month is often partial or absent; those come back None and
    are dropped rather than plotted as zero.
    """
    today = dt.date.today()
    keys, wanted = [], {}
    y, m = today.year, today.month
    for _ in range(int(months)):
        keys.append(f"{y:04d}-{m:02d}")
        wanted[f"{y:04d}-{m:02d}"] = _nino34_month(y, m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    try:
        got = ee.Dictionary(wanted).getInfo()
    except Exception as e:
        print(f"[elnino] Nino 3.4 series unavailable: {str(e)[:160]}",
              flush=True)
        return []
    out = [{"date": k, "oni": round(float(got[k]), 2)}
           for k in reversed(keys) if got.get(k) is not None]
    return out


def enso_label(value):
    """NOAA-convention wording for an ONI value."""
    if value is None:
        return "unknown"
    if value >= ENSO_STRONG_MIN:
        return "Strong El Niño"
    if value >= ENSO_EVENT_MIN:
        return "El Niño"
    if value <= -ENSO_STRONG_MIN:
        return "Strong La Niña"
    if value <= -ENSO_EVENT_MIN:
        return "La Niña"
    return "Neutral"


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=_RESULT_TTL), lock=_lock)
def enso_state():
    """Current ENSO state from the 3-month running mean of Nino 3.4.

    Returns {"oni", "label", "asof", "series"} or None. The 3-month mean is the
    NOAA convention — a single month is noisy enough to flip the label.
    """
    series = nino34_series()
    if not series:
        return None
    recent = [r["oni"] for r in series[-3:]]
    oni = round(sum(recent) / len(recent), 2)
    return {"oni": oni, "label": enso_label(oni),
            "asof": series[-1]["date"], "latest": series[-1]["oni"],
            "series": series}


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def _vis_for(layer, signal):
    cfg = EXPLORE_MAP[layer]
    palette = cfg.get("palette")
    # The probability layer follows the signal's own colour ramp, so dry reads
    # brown and wet reads blue rather than both sharing one neutral scale.
    if cfg["name"] == "signal_prob":
        palette = SIGNAL_MAP[signal]["palette"]
    return {"min": cfg["min"], "max": cfg["max"], "palette": palette}


@_ttl_cached(cache=TTLCache(maxsize=32, ttl=_TILE_TTL), lock=_lock)
def get_elnino_preview_tile(period, layer, signal, threshold,
                            apply_dry_mask=True):
    """Unclipped global tile — the layer before any region is chosen.

    No percentile stretch: these are fixed, known quantities (probabilities are
    0-1, anomalies are mm) so the catalog ranges make an honest ramp and the
    tile renders in one getMapId call.
    """
    img = build_explore_image(period, layer, signal, threshold, apply_dry_mask)
    vis = _vis_for(layer, signal)
    return img.getMapId(vis)["tile_fetcher"].url_format, vis


@_ttl_cached(cache=TTLCache(maxsize=32, ttl=_TILE_TTL), lock=_lock)
def get_elnino_layer_tile(period, layer, signal, threshold, feature_ucode,
                          admin_level, apply_dry_mask=True):
    """The same layer clipped to the AOI."""
    img = build_explore_image(period, layer, signal, threshold, apply_dry_mask)
    region = _admin_region(admin_level, feature_ucode)
    vis = _vis_for(layer, signal)
    return img.clip(region).getMapId(vis)["tile_fetcher"].url_format, vis


@_ttl_cached(cache=TTLCache(maxsize=32, ttl=_TILE_TTL), lock=_lock)
def get_elnino_exposed_tile(period, signal, threshold, feature_ucode,
                            admin_level, apply_dry_mask=True):
    """Child population under the signal, clipped to the AOI."""
    pop = _forecast_pop()
    img = _tercile_image(period)
    mask = _signal_mask(img, signal, threshold, apply_dry_mask, period)
    region = _admin_region(admin_level, feature_ucode)
    masked = pop["childpop"].updateMask(mask).clip(region).selfMask()
    return masked.getMapId(POP_VIS)["tile_fetcher"].url_format, POP_VIS


@_ttl_cached(cache=TTLCache(maxsize=32, ttl=_TILE_TTL), lock=_lock)
def get_elnino_pop_tile(feature_ucode, admin_level):
    """All children in the AOI, signal or not — the exposure denominator.

    Rendered with the same POP_VIS ramp as the exposed layer on purpose: the
    two are only comparable by eye if identical counts take identical colours,
    so the difference a viewer sees is coverage, not shading.
    """
    pop = _forecast_pop()
    region = _admin_region(admin_level, feature_ucode)
    clipped = pop["childpop"].clip(region).selfMask()
    return clipped.getMapId(POP_VIS)["tile_fetcher"].url_format, POP_VIS


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@_ttl_cached(cache=TTLCache(maxsize=1, ttl=600), lock=_lock)
def elnino_available():
    """(bool, message) — whether the tab can run at all.

    Checked before the panel renders so a missing or unshared asset produces one
    clear sentence instead of a failure inside every callback. The assets are
    created under a personal account by the upload script, so "not shared with
    the service account" is the expected first-run failure and is named as such.
    """
    try:
        n = ee.ImageCollection(TERCILES_COLLECTION).size().getInfo()
    except Exception as e:
        return False, (
            f"The El Niño assets are not readable ({str(e)[:100]}). They are "
            f"created under the uploader's own Earth Engine account, so the "
            f"app's service account needs read access to {TERCILES_COLLECTION}.")
    if not n:
        return False, (f"{TERCILES_COLLECTION} is empty — run "
                       f"scripts/el_nino/download_ecmwf_prec.py.")
    return True, f"{n} period(s) available."
