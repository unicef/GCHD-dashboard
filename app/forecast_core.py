# =============================================================================
# forecast_core.py — GEE logic for the Forecast & Live tab
#
# Kept out of gee_core.build_core_images() on purpose: that function is
# @lru_cache(maxsize=1) and holds the static hazard image graph for the server's
# lifetime. Everything here is parameterised by a date window, so it is built on
# demand and cached per (dataset, window, reducer) tuple instead.
#
# The child-population grid is imported FROM gee_core so the exposure
# denominator is identical to the rest of the dashboard — a forecast result must
# reconcile with an Analysis-tab result for the same region.
# =============================================================================

import os
import datetime as dt
from threading import RLock

import ee
from cachetools import TTLCache, cached as _ttl_cached

from config import ADMIN_DATA, POP_VIS, GLOBAL_GEOMETRY
from forecast_config import (
    FORECAST_DATASETS, FORECAST_MAP, CUSTOM_NAME, CUSTOM_PALETTE,
)
from gee_core import build_core_images, _admin_region

# Same TTL rationale as gee_core: GEE map tokens expire, so tiles are refreshed
# well before then. Reduction results are cached more briefly because live
# collections gain images during the day.
_TILE_TTL   = 3000   # 50 min
_RESULT_TTL = 900    # 15 min
_lock = RLock()


def _tile_cache(maxsize):
    return TTLCache(maxsize=maxsize, ttl=_TILE_TTL)


def _result_cache(maxsize):
    return TTLCache(maxsize=maxsize, ttl=_RESULT_TTL)


class ForecastError(Exception):
    """User-facing problem (empty window, missing band, unreadable asset).
    Callbacks render the message inline rather than showing a traceback."""


# ---------------------------------------------------------------------------
# Runtime enable/disable overrides
#
# A small GEE FeatureCollection lets datasets be switched on or off without a
# code change or redeploy — the same principle the Infrastructure tab already
# uses (gee_core.discover_infra_assets). Edit the asset in the Code Editor and
# the running app reflects it within the TTL; no restart.
#
# Two guardrails:
#   * a read failure falls back to the config defaults, so an unreachable or
#     deleted asset degrades to "what the code says" rather than an empty tab;
#   * only names already in FORECAST_DATASETS are honoured, so the table can
#     never surface a dataset that has no verified code path behind it.
# ---------------------------------------------------------------------------

FORECAST_CONFIG_ASSET = os.environ.get(
    "GCHD_FORECAST_CONFIG_ASSET",
    "projects/unicef-ccri/assets/config/forecast_datasets")

_OVERRIDE_TTL = 600   # 10 min, matching infra discovery


def _truthy(v):
    """GEE columns come back as 1/0, True/False, or "yes"/"no" depending on how
    the table was authored. Accept all three rather than trusting one."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "active")


@_ttl_cached(cache=TTLCache(maxsize=1, ttl=_OVERRIDE_TTL), lock=RLock())
def fetch_active_overrides():
    """{dataset_name: bool} from the GEE config table.

    Returns {} when the asset is absent or unreadable — the caller then uses the
    config defaults. Absence is a normal state, not an error: the table is
    optional and the app is fully functional without it.
    """
    try:
        fc = ee.FeatureCollection(FORECAST_CONFIG_ASSET)
        rows = fc.getInfo().get("features", [])
    except Exception as e:
        # Debug-level in effect: this fires on every TTL expiry when no table
        # exists, which is a supported configuration.
        print(f"[forecast config] no runtime overrides "
              f"({FORECAST_CONFIG_ASSET}): {str(e)[:120]}")
        return {}

    out, unknown = {}, []
    for f in rows:
        props = f.get("properties") or {}
        name = props.get("name") or props.get("dataset") or props.get("id")
        if not name:
            continue
        name = str(name).strip()
        if name not in FORECAST_MAP:
            unknown.append(name)
            continue
        if "active" in props:
            out[name] = _truthy(props["active"])
    if unknown:
        print(f"[forecast config] ignored {len(unknown)} unknown dataset "
              f"name(s) in {FORECAST_CONFIG_ASSET}: {unknown[:8]}")
    if out:
        print(f"[forecast config] applied {len(out)} override(s): "
              f"{ {k: v for k, v in list(out.items())[:8]} }")
    return out


def is_dataset_active(name):
    """Runtime-effective active state: the GEE table wins over the config
    default when it carries a row for this dataset."""
    cfg = FORECAST_MAP.get(name)
    if not cfg:
        return False
    return fetch_active_overrides().get(name, cfg.get("active", True))


def active_datasets_live():
    """Catalog entries active right now, honouring the GEE override table.

    Mirrors forecast_config.active_datasets() but consults the runtime table.
    Kept here rather than in forecast_config because that module deliberately
    imports no GEE.
    """
    ov = fetch_active_overrides()
    return [d for d in FORECAST_DATASETS
            if ov.get(d["name"], d.get("active", True))]


# ---------------------------------------------------------------------------
# Per-dataset band transforms
#
# Each takes (ee.Image, cfg) and returns an ee.Image. These exist because raw
# catalog bands are frequently not in the units a threshold slider can express:
# S5P columns are ~5e-5 mol/m², MODIS LST is a scaled integer in Kelvin, and GFS
# has no wind-speed band at all. Applied AFTER the temporal reduction unless
# noted — the fire/IMERG ones are per-image and applied before.
# ---------------------------------------------------------------------------

def _t_mol_to_umol(img, cfg):
    return img.multiply(1e6)


def _t_mol_to_mmol(img, cfg):
    return img.multiply(1e3)


def _t_modis_lst_c(img, cfg):
    """MODIS LST is uint16 Kelvin x50: raw * 0.02 - 273.15 -> °C."""
    return img.multiply(0.02).subtract(273.15)


def _t_kgm3_to_ugm3(img, cfg):
    """CAMS particulate matter is kg/m³ (~1e-8). x1e9 -> µg/m³, the unit WHO
    guidelines and every national air-quality standard are written in."""
    return img.multiply(1e9)


_POST_TRANSFORMS = {
    "mol_to_umol":  _t_mol_to_umol,
    "mol_to_mmol":  _t_mol_to_mmol,
    "modis_lst_c":  _t_modis_lst_c,
    "kgm3_to_ugm3": _t_kgm3_to_ugm3,
}


def _pre_wind_speed(coll, cfg):
    """GFS has no gust/speed band — derive hypot(u10, v10) per image.

    `needs_all_bands` because this is the one transform that reads two bands;
    the others run after the configured band has been selected.
    """
    def speed(img):
        u = img.select("u_component_of_wind_10m_above_ground")
        v = img.select("v_component_of_wind_10m_above_ground")
        return u.hypot(v).rename("v").copyProperties(img, ["system:time_start"])
    return coll.map(speed)


_pre_wind_speed.needs_all_bands = True


def _pre_imerg_mm(coll, cfg):
    """IMERG `precipitation` is mm/hr on half-hourly images -> mm per image."""
    return coll.map(lambda i: i.multiply(0.5).copyProperties(
        i, ["system:time_start"]))


def _pre_fire_day_count(coll, cfg):
    """FIRMS has no count band: 1 where a fire pixel exists, else 0, per image.
    Summed over the window this yields days-with-fire."""
    return coll.map(lambda i: i.mask().copyProperties(
        i, ["system:time_start"]))


_PRE_TRANSFORMS = {
    "wind_speed":     _pre_wind_speed,
    "imerg_mm":       _pre_imerg_mm,
    "fire_day_count": _pre_fire_day_count,
}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _to_date(v):
    """Accept 'YYYY-MM-DD' or a date/datetime; return a date."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def default_window(name):
    """(start, end) ISO strings for a dataset's initial picker state.

    Forecast datasets look forward from today. NRT datasets end `lag_days` back:
    ending "today" on FIRMS/IMERG/MODIS reliably yields zero images, which is
    the single most confusing failure mode in this tab.
    """
    cfg = FORECAST_MAP.get(name)
    today = dt.date.today()
    if not cfg:
        return (today - dt.timedelta(days=7)).isoformat(), today.isoformat()
    span = int(cfg.get("default_span_days", 7))
    if cfg.get("kind") == "forecast":
        return today.isoformat(), (today + dt.timedelta(days=span)).isoformat()
    end = today - dt.timedelta(days=int(cfg.get("lag_days", 0)))
    return (end - dt.timedelta(days=span)).isoformat(), end.isoformat()


def clamp_window(name, start, end):
    """Clamp a window to the dataset's max_span_days. Returns (start, end, msg);
    msg is None when nothing was changed."""
    cfg = FORECAST_MAP.get(name) or {}
    s, e = _to_date(start), _to_date(end)
    if e < s:
        s, e = e, s
    cap = int(cfg.get("max_span_days", 30))
    if (e - s).days > cap:
        e = s + dt.timedelta(days=cap)
        return s.isoformat(), e.isoformat(), (
            f"Window capped at {cap} days for this dataset.")
    return s.isoformat(), e.isoformat(), None


# ---------------------------------------------------------------------------
# Collection loading + temporal reduction
# ---------------------------------------------------------------------------

_OPS = {
    "eq": lambda p, v: ee.Filter.eq(p, v),
    "gt": lambda p, v: ee.Filter.gt(p, v),
    "lt": lambda p, v: ee.Filter.lt(p, v),
    "gte": lambda p, v: ee.Filter.gte(p, v),
    "lte": lambda p, v: ee.Filter.lte(p, v),
}


def _resolve_cfg(name, custom_id=None, custom_band=None):
    """Catalog entry, or a synthesised one for a pasted asset id."""
    if name and name != CUSTOM_NAME:
        cfg = FORECAST_MAP.get(name)
        if not cfg:
            raise ForecastError(f"Unknown dataset '{name}'.")
        return cfg
    if not custom_id:
        raise ForecastError("No dataset selected.")
    return {
        "id": custom_id, "name": CUSTOM_NAME, "label": custom_id.split("/")[-1],
        "topic": None, "kind": "nrt", "band": custom_band,
        "reducers": list(_REDUCER_FNS), "max_span_days": 90,
        "default_span_days": 7, "lag_days": 0,
        "threshold": 0, "min": 0, "max": 100, "units": "",
        "palette": CUSTOM_PALETTE, "scale": None,
    }


_REDUCER_FNS = {
    "sum":    lambda c: c.sum(),
    "mean":   lambda c: c.mean(),
    "max":    lambda c: c.max(),
    "min":    lambda c: c.min(),
    "median": lambda c: c.median(),
}


def build_forecast_image(name, start, end, reducer,
                         custom_id=None, custom_band=None):
    """Reduce a dataset over [start, end] to a single ee.Image.

    Returns (image, n_images). `n_images` is surfaced to the UI: an empty
    window is common (satellite latency, forecast run timing) and must be
    reported explicitly rather than silently reducing to an all-masked image
    that sums to zero and reads as "no children exposed".
    """
    cfg = _resolve_cfg(name, custom_id, custom_band)
    s, e = _to_date(start), _to_date(end)
    # filterDate's end is exclusive — add a day so the picker's end date is
    # inclusive, which is what a user selecting a range expects.
    coll = ee.ImageCollection(cfg["id"]).filterDate(
        s.isoformat(), (e + dt.timedelta(days=1)).isoformat())

    for f in cfg.get("filters") or []:
        op = _OPS.get(f.get("op"))
        if op:
            coll = coll.filter(op(f["prop"], f["value"]))

    n = coll.size().getInfo()
    if not n:
        raise ForecastError(
            f"No images for {cfg['label']} between {s} and {e}. "
            + ("Forecast runs publish a few times a day — try a wider window."
               if cfg.get("kind") == "forecast" else
               f"This dataset lags about {cfg.get('lag_days', 0)} day(s); "
               "try an earlier end date."))

    # Select the configured band first so a transform sees a single-band image;
    # wind_speed is the exception — it derives speed from two bands and is
    # flagged needs_all_bands.
    pre = _PRE_TRANSFORMS.get(cfg.get("transform"))
    if cfg.get("band") and not getattr(pre, "needs_all_bands", False):
        coll = coll.select(cfg["band"])
    if pre is not None:
        coll = pre(coll, cfg)

    fn = _REDUCER_FNS.get(reducer)
    if fn is None:
        raise ForecastError(f"Unsupported reducer '{reducer}'.")
    img = fn(coll)

    post = _POST_TRANSFORMS.get(cfg.get("transform"))
    if post is not None:
        img = post(img, cfg)

    # Custom assets with several bands: keep the first so downstream masking is
    # unambiguous. A named band was already selected above.
    if cfg["name"] == CUSTOM_NAME and not cfg.get("band"):
        img = img.select(0)

    return img.rename("forecast_value"), n


# ---------------------------------------------------------------------------
# Exposure — mirrors gee_core.compute_exposure so numbers reconcile
# ---------------------------------------------------------------------------

def compute_forecast_exposure(name, start, end, reducer, threshold,
                              feature_ucode, admin_level,
                              custom_id=None, custom_band=None):
    """Children exposed where the reduced forecast image exceeds `threshold`.

    Deliberately identical in shape to gee_core.compute_exposure: the same
    child-population mosaic, the same 500 km chunk collection, the same
    reduceRegions -> reduceColumns pattern at the population grid's scale.

    Unlike the Analysis tab there is NO adm0 fast path — the precomputed asset
    only holds static hazards — so thresholds take effect at every level.
    """
    core       = build_core_images()
    childpop   = core["childpop"]
    childpop_m = core["childpop_m"]
    childpop_f = core["childpop_f"]
    pop_res    = core["pop_target_res"]

    layer, n_images = build_forecast_image(
        name, start, end, reducer, custom_id, custom_band)

    # Strict > matches _exposed_for_hazard in gee_core.
    exposed = childpop.updateMask(layer.gt(threshold)).rename("exposed")

    combined = (
        ee.Image.cat([exposed])
        .addBands(childpop.rename("total_population"))
        .addBands(childpop_m.rename("total_population_male"))
        .addBands(childpop_f.rename("total_population_female"))
    )

    chunk_asset = ADMIN_DATA[admin_level]["chunk_asset"]
    chunks      = ee.FeatureCollection(chunk_asset).filter(
        ee.Filter.eq("ucode", feature_ucode))
    band_names  = combined.bandNames()

    tile_results = combined.reduceRegions(
        collection=chunks, reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=1,
    )
    sums  = tile_results.reduceColumns(
        ee.Reducer.sum().repeat(band_names.size()), band_names)
    stats = ee.Dictionary.fromLists(band_names, sums.get("sum")).getInfo()

    stats["_n_images"] = n_images
    return stats


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

@_ttl_cached(cache=_tile_cache(32), lock=_lock)
def get_forecast_intensity_tile(name, start, end, reducer, feature_ucode,
                                admin_level, custom_id=None, custom_band=None):
    """The reduced image itself, stretched p2-p98 and clipped to the AOI.

    The percentile stretch is computed over the AOI, not globally: a global
    reduceRegion on a live collection (as get_hazard_tile_url does for static
    assets) would be far too expensive to run per date-range change.
    """
    cfg = _resolve_cfg(name, custom_id, custom_band)
    img, _ = build_forecast_image(name, start, end, reducer,
                                  custom_id, custom_band)
    region = _admin_region(admin_level, feature_ucode)
    scale  = cfg.get("scale") or 5000

    stats = img.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]), geometry=region,
        scale=max(scale, 1000), bestEffort=True, maxPixels=1e10,
    ).getInfo()
    p2  = stats.get("forecast_value_p2")
    p98 = stats.get("forecast_value_p98")
    if p2 is None or p98 is None or p98 <= p2:
        p2, p98 = cfg.get("min", 0), cfg.get("max", 1)

    vis = {"min": p2, "max": p98,
           "palette": cfg.get("palette") or CUSTOM_PALETTE}
    mid = img.clip(region).getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(32), lock=_lock)
def get_forecast_preview_tile(name, start, end, reducer,
                              custom_id=None, custom_band=None):
    """Unclipped global tile for inspecting a layer before choosing a region.

    Returns (url, vis, n_images).

    Deliberately does NOT compute a percentile stretch. get_forecast_intensity_tile
    bounds its stretch to the AOI precisely because a global reduceRegion over a
    live collection is expensive; doing it globally here would put that cost on
    every dataset/date change, before the user has committed to anything. The
    catalog's min/max are probe-verified real ranges, so they make an honest
    fixed ramp and the tile renders in one getMapId call.

    For a pasted custom asset there is no catalog range, so fall back to a
    cheap coarse percentile over a global sample — one reduction, ~50 km scale.
    """
    cfg = _resolve_cfg(name, custom_id, custom_band)
    img, n_images = build_forecast_image(name, start, end, reducer,
                                         custom_id, custom_band)

    lo, hi = cfg.get("min"), cfg.get("max")
    if cfg["name"] == CUSTOM_NAME or lo is None or hi is None or hi <= lo:
        stats = img.reduceRegion(
            reducer=ee.Reducer.percentile([2, 98]),
            geometry=ee.Geometry.Polygon([GLOBAL_GEOMETRY], None, False),
            scale=50000, bestEffort=True, maxPixels=1e9,
        ).getInfo()
        p2, p98 = stats.get("forecast_value_p2"), stats.get("forecast_value_p98")
        lo, hi = (p2, p98) if (p2 is not None and p98 is not None and p98 > p2) else (0, 1)

    vis = {"min": lo, "max": hi,
           "palette": cfg.get("palette") or CUSTOM_PALETTE}
    mid = img.getMapId(vis)
    return mid["tile_fetcher"].url_format, vis, n_images


@_ttl_cached(cache=_tile_cache(32), lock=_lock)
def get_forecast_exposed_tile(name, start, end, reducer, threshold,
                              feature_ucode, admin_level,
                              custom_id=None, custom_band=None):
    """Child population where the forecast exceeds the threshold, clipped to the
    AOI — mirrors gee_core.get_exposed_pop_tile_url."""
    core     = build_core_images()
    childpop = core["childpop"]
    img, _   = build_forecast_image(name, start, end, reducer,
                                    custom_id, custom_band)
    region   = _admin_region(admin_level, feature_ucode)

    masked = childpop.updateMask(img.gt(threshold)).clip(region).selfMask()
    mid    = masked.getMapId(POP_VIS)
    return mid["tile_fetcher"].url_format, POP_VIS


# ---------------------------------------------------------------------------
# Custom asset probing (paste-a-GEE-id path)
# ---------------------------------------------------------------------------

@_ttl_cached(cache=_result_cache(32), lock=_lock)
def probe_custom_asset(asset_id):
    """Inspect a pasted GEE id so the UI can offer bands and a sane window.

    Returns {kind, bands, n_images, start, end}. `kind` is "ImageCollection" or
    "Image"; a plain Image needs no date range or reducer. Raises ForecastError
    with a readable message, mirroring how app.load_gee_asset surfaces errors.
    """
    asset_id = (asset_id or "").strip()
    if not asset_id:
        raise ForecastError("Enter a GEE asset id.")
    try:
        meta = ee.data.getAsset(asset_id)
    except Exception as e:
        raise ForecastError(
            f"Cannot read '{asset_id}'. Check the id and that the asset is "
            f"shared publicly. ({str(e)[:120]})")

    a_type = (meta.get("type") or "").upper()
    try:
        if a_type in ("IMAGE_COLLECTION", "IMAGECOLLECTION"):
            coll  = ee.ImageCollection(asset_id)
            bands = coll.first().bandNames().getInfo()
            n     = coll.size().getInfo()
            rng   = coll.reduceColumns(
                ee.Reducer.minMax(), ["system:time_start"]).getInfo()
            to_iso = lambda ms: (
                dt.datetime.utcfromtimestamp(ms / 1000).date().isoformat()
                if ms else None)
            return {"kind": "ImageCollection", "bands": bands, "n_images": n,
                    "start": to_iso(rng.get("min")),
                    "end":   to_iso(rng.get("max"))}
        if a_type == "IMAGE":
            return {"kind": "Image",
                    "bands": ee.Image(asset_id).bandNames().getInfo(),
                    "n_images": 1, "start": None, "end": None}
    except Exception as e:
        raise ForecastError(f"Could not inspect '{asset_id}': {str(e)[:140]}")

    raise ForecastError(
        f"'{asset_id}' is a {a_type or 'unknown'} asset. This tab needs an "
        "Image or ImageCollection (use the Analysis tab for boundary tables).")
