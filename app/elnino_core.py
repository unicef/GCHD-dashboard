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
    TERCILES_COLLECTION, RAW_HINDCAST_COLLECTION,
    TERCILE_BANDS, PERIOD_MAP, DEFAULT_PERIOD, WINDOW_PERIOD,
    SIGNAL_MAP, EXPLORE_MAP,
    PROB_DEFAULT, SENSITIVITY_THRESHOLDS, INIT_TAG, to_fraction,
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

@_ttl_cached(cache=TTLCache(maxsize=4, ttl=_RESULT_TTL), lock=_lock)
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


# ---------------------------------------------------------------------------
# Masks and derived layers
# ---------------------------------------------------------------------------

def _signal_mask(img, signal, threshold, apply_dry_mask=True):
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
    """
    cfg = SIGNAL_MAP.get(signal)
    if not cfg:
        raise ElNinoError(f"Unknown signal '{signal}'.")
    p    = img.select(cfg["band"])
    other = img.select(cfg["other"])
    mask = p.gte(to_fraction(threshold)).And(p.gt(other))
    if apply_dry_mask:
        mask = mask.And(img.select("dry_mask").eq(1))
    return mask.rename("signal")


def _d_signal_prob(img, ctx):
    """Selected signal's probability, scaled to 0-100 to match the UI."""
    return img.select(SIGNAL_MAP[ctx["signal"]]["band"]) \
              .multiply(100).rename("value")


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
    """cat recomputed at the live threshold: 1 dry, 3 wet, 0 neither.

    The stored `cat` band is fixed at 0.5, so a user moving the slider would
    otherwise see the map disagree with the numbers.
    """
    thr = float(ctx.get("threshold", PROB_DEFAULT))
    dry = _signal_mask(img, "dry", thr, ctx.get("apply_dry_mask", True))
    wet = _signal_mask(img, "wet", thr, ctx.get("apply_dry_mask", True))
    return ee.Image(0).where(dry, 1).where(wet, 3).rename("value").toFloat()


_DERIVED = {
    "signal_prob":     _d_signal_prob,
    "signal_strength": _d_signal_strength,
    "anom_pct":        _d_anom_pct,
    "category":        _d_category,
}


def build_explore_image(period, layer, signal, threshold, apply_dry_mask=True):
    """A single-band ee.Image for the map, named "value"."""
    cfg = EXPLORE_MAP.get(layer)
    if not cfg:
        raise ElNinoError(f"Unknown layer '{layer}'.")
    img = _tercile_image(period)
    ctx = {"signal": signal, "threshold": threshold,
           "apply_dry_mask": apply_dry_mask}

    if cfg.get("derived"):
        out = _DERIVED[cfg["derived"]](img, ctx)
    else:
        out = img.select(cfg["band"]).rename("value")

    # The dry mask hides cells where a tercile is noise. It must NOT be applied
    # to the dry_mask layer itself (which exists to show those cells) nor to the
    # climatology that defines them.
    if apply_dry_mask and cfg["name"] not in ("dry_mask", "clim_mean_mm"):
        out = out.updateMask(img.select("dry_mask").eq(1))
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
    mask = _signal_mask(img, signal, threshold, apply_dry_mask)

    exposed = childpop.updateMask(mask).rename("exposed")
    combined = (
        ee.Image.cat([exposed])
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
    extras = {str(int(t)): _exposure_stats(
                  period, signal, t, feature_ucode, admin_level, apply_dry_mask
              ).get("exposed")
              for t in SENSITIVITY_THRESHOLDS}

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
    mask   = _signal_mask(img, signal, threshold, True)
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
    hc_names = _band_names(f"{RAW_HINDCAST_COLLECTION}/hc_1993{INIT_TAG[4:]}")
    by_lead = {}
    for nm in hc_names:
        by_lead.setdefault(nm.split("_")[0], []).append(nm)
    lead_keys = sorted(by_lead, key=lambda k: int(k[1:]))
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
    mask = _signal_mask(img, signal, threshold, apply_dry_mask)
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
