# =============================================================================
# gee_core.py — GEE logic with no Streamlit dependency
# Caches with functools.lru_cache (module-level, server lifetime).
# =============================================================================

import re
import json
import os
from functools import lru_cache
from threading import RLock
import ee
from cachetools import TTLCache, cached as _ttl_cached

_TILE_TTL = 3000  # 50 min — GEE tokens expire after ~24 h; refresh well before then
_tile_lock = RLock()


def _tile_cache(maxsize):
    return TTLCache(maxsize=maxsize, ttl=_TILE_TTL)

from config import (
    HAZARDS, HAZARD_MAP, HAZARD_TOPICS, ALLOW_NEGATIVE,
    HAZARD_VIS_PALETTES, SELF_MASK_HAZARDS, GLOBAL_GEOMETRY,
    ADMIN_DATA, EXPOSURE_ONLY_TOPICS, MHC_EXCLUDED_TOPICS,
    MHC_OPTIONS, MHI_OPTIONS, SUB_TOPIC_DETAIL,
)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def initialize_gee():
    key_path = os.path.join(os.path.dirname(__file__), "credentials", "service_account.json")
    with open(key_path) as f:
        info = json.load(f)
    credentials = ee.ServiceAccountCredentials(email=info["client_email"], key_file=key_path)
    ee.Initialize(credentials=credentials, project="unicef-ccri")


# ---------------------------------------------------------------------------
# Core GEE objects — built once at startup
# ---------------------------------------------------------------------------

def _exposed_for_hazard(hazard, childpop, threshold=None):
    """Child-population image masked to where `hazard` exceeds its threshold.

    `threshold=None` uses the hazard's configured default; passing a value lets
    the Analysis tab recompute a single hazard with a user-chosen threshold
    without rebuilding the whole core image set.
    """
    if hazard.get("isImage"):
        layer = ee.Image(hazard["id"])
    elif re.search(r"flood|storm", hazard["name"]):
        layer = ee.ImageCollection(hazard["id"]).mosaic()
    else:
        layer = ee.Image(hazard["id"])
    if hazard.get("band"):
        layer = layer.select(hazard["band"])

    th = hazard["threshold"] if threshold is None else threshold
    if hazard["name"] == "agricultural_drought_fao_1984-2023":
        layer = layer.updateMask(layer.lte(100))
        exposed = childpop.updateMask(layer.gt(th))
    elif "malaria" in hazard["name"]:
        layer = layer.updateMask(layer.gt(0))
        exposed = childpop.updateMask(layer.gt(th))
    else:
        layer = layer.updateMask(layer.gt(-1000))
        exposed = childpop.updateMask(layer.lt(th) if th < 0 else layer.gt(th))
    return exposed.rename(hazard["name"])


@lru_cache(maxsize=1)
def build_core_images():
    org_childpop   = ee.ImageCollection("projects/unicef-ccri/assets/population/worldpop_T_U18_2025_CN_100m")
    org_childpop_m = ee.ImageCollection("projects/unicef-ccri/assets/population/worldpop_T_M_U18_2025_CN_100m")
    org_childpop_f = ee.ImageCollection("projects/unicef-ccri/assets/population/worldpop_T_F_U18_2025_CN_100m")

    childpop   = org_childpop.mosaic().select(0).rename("population")
    childpop_m = org_childpop_m.mosaic().select(0).rename("population_m")
    childpop_f = org_childpop_f.mosaic().select(0).rename("population_f")

    pop_target_res  = org_childpop.first().projection().nominalScale()
    reference_image = ee.Image("projects/unicef-ccri/assets/hazards/heatwave_frequency_return_level_100yr")
    target_crs      = reference_image.projection()
    target_scale    = reference_image.projection().nominalScale()

    country_boundaries = ee.FeatureCollection("projects/unicef-ccri/assets/misc_boundaries/adm0_simple")
    country_boundaries_reproj = country_boundaries.map(lambda f: f.transform(target_crs))
    global_geom = ee.Geometry.Polygon([GLOBAL_GEOMETRY], None, False)

    exposure_by_hazard = {h["name"]: _exposed_for_hazard(h, childpop) for h in HAZARDS}

    def build_topic_mask(topic_name):
        masks = [exposure_by_hazard[n].mask() for n in HAZARD_TOPICS[topic_name]]
        union = masks[0]
        for m in masks[1:]:
            union = union.Or(m)
        return ee.Image.constant(1).updateMask(union)

    topic_masks = {t: build_topic_mask(t) for t in HAZARD_TOPICS}
    mhc_topics = [t for t in HAZARD_TOPICS if t not in MHC_EXCLUDED_TOPICS]
    stacked = ee.ImageCollection([topic_masks[t] for t in mhc_topics]).toBands()
    topic_count_image = stacked.reduce(ee.Reducer.count()).rename("topic_count")

    def get_raw_mask(hazard):
        if hazard.get("isImage"):
            layer = ee.Image(hazard["id"])
        elif re.search(r"flood|storm", hazard["name"]):
            layer = ee.ImageCollection(hazard["id"]).mosaic()
        else:
            layer = ee.Image(hazard["id"])
        if hazard.get("band"):
            layer = layer.select(hazard["band"])
        return layer.mask()

    def build_coverage_image(topic_name):
        coverages = [get_raw_mask(HAZARD_MAP[n]) for n in HAZARD_TOPICS[topic_name] if n in HAZARD_MAP]
        union = coverages[0]
        for c in coverages[1:]:
            union = union.Or(c)
        safe_key = re.sub(r"[^a-zA-Z0-9]", "_", topic_name)
        return ee.Image.constant(1).updateMask(union).rename(f"cov_{safe_key}")

    topic_coverage = {t: build_coverage_image(t) for t in HAZARD_TOPICS}
    hazard_score   = ee.Image("projects/unicef-ccri/assets/hazards/MHI_climate")

    def load_raw(hazard):
        """Raw hazard intensity image (values kept), for sampling at facilities."""
        if hazard.get("isImage"):
            layer = ee.Image(hazard["id"])
        elif re.search(r"flood|storm", hazard["name"]):
            layer = ee.ImageCollection(hazard["id"]).mosaic()
        else:
            layer = ee.Image(hazard["id"])
        if hazard.get("band"):
            layer = layer.select(hazard["band"])
        # Drop no-data sentinels but keep the real intensity values.
        if hazard["name"] == "agricultural_drought_fao_1984-2023":
            layer = layer.updateMask(layer.lte(100))
        elif "malaria" in hazard["name"]:
            layer = layer.updateMask(layer.gt(0))
        else:
            layer = layer.updateMask(layer.gt(-1000))
        return layer

    raw_hazard = {h["name"]: load_raw(h) for h in HAZARDS}

    return {
        "childpop":                  childpop,
        "childpop_m":                childpop_m,
        "childpop_f":                childpop_f,
        "pop_target_res":            pop_target_res,
        "target_crs":                target_crs,
        "target_scale":              target_scale,
        "country_boundaries_reproj": country_boundaries_reproj,
        "global_geom":               global_geom,
        "exposure_by_hazard":        exposure_by_hazard,
        "topic_masks":               topic_masks,
        "topic_coverage":            topic_coverage,
        "topic_count_image":         topic_count_image,
        "hazard_score":              hazard_score,
        "raw_hazard":                raw_hazard,
    }


def apply_threshold_overrides(core, threshold_overrides, topics=None):
    """Return (exposure_by, topic_masks) copies with the given per-hazard
    threshold overrides applied. Only overridden hazards are recomputed via
    _exposed_for_hazard; the topic masks containing them are rebuilt as the
    OR-union across the topic's hazards. Untouched entries reuse cached images.

    Shared by every threshold-aware path (Analysis, Exposure viz, custom
    boundary, Infrastructure) so the override semantics stay identical.
    """
    exposure_by = dict(core["exposure_by_hazard"])
    topic_masks = dict(core["topic_masks"])
    if not threshold_overrides:
        return exposure_by, topic_masks

    childpop = core["childpop"]
    for h_name, thr in threshold_overrides.items():
        if h_name in HAZARD_MAP:
            exposure_by[h_name] = _exposed_for_hazard(HAZARD_MAP[h_name], childpop, thr)

    scope = topics if topics is not None else list(HAZARD_TOPICS)
    affected = {t for t in scope
                if t in HAZARD_TOPICS
                and any(h in threshold_overrides for h in HAZARD_TOPICS[t])}
    for t in affected:
        masks = [exposure_by[n].mask() for n in HAZARD_TOPICS[t]]
        union = masks[0]
        for m in masks[1:]:
            union = union.Or(m)
        topic_masks[t] = ee.Image.constant(1).updateMask(union)
    return exposure_by, topic_masks


# ---------------------------------------------------------------------------
# ADM0 fast path — pre-computed exposure asset
# ---------------------------------------------------------------------------

ADM0_EXPOSURE_ASSET = "projects/unicef-ccri/assets/exposure_sum/Hazard_Population_Exposure_adm0"


@lru_cache(maxsize=256)
def _get_adm0_asset_props(feature_ucode):
    fc = ee.FeatureCollection(ADM0_EXPOSURE_ASSET).filter(
        ee.Filter.eq("ucode", feature_ucode)
    )
    return fc.first().toDictionary().getInfo()


def compute_exposure_adm0_from_asset(feature_ucode):
    props = _get_adm0_asset_props(feature_ucode)

    def _v(key):
        v = props.get(key)
        return float(v) if v is not None else 0.0

    def _cov(v):
        return 1 if v > 0 else 0

    result = {
        "total_population":        _v("pop_child_total"),
        "total_population_male":   _v("total_male"),
        "total_population_female": _v("total_female"),
        "River Flood":             _v("pop_exposed_river_flood_100yr_jrc_2024"),
        "Coastal Flood":           _v("pop_exposed_coastal_flood_100yr_jrc_2024"),
        "Tropical Storm":          _v("pop_exposed_tropical_storm_100yr_giri_2024"),
        "Drought":                 _v("pop_exposed_drought_topic"),
        "Heatwave":                _v("pop_exposed_heatwave_topic"),
        "Extreme Heat":            _v("pop_exposed_extreme_heat_ecmwf_2014-2024"),
        "Fire":                    _v("pop_exposed_fire_topic"),
        "Sand and Dust Storm":     _v("pop_exposed_sand_dust_storm_unccd_2024"),
        "Air Pollution":           _v("pop_exposed_air_pollution_pm25_1998-2023"),
        "Malaria":                 _v("pop_exposed_malaria_topic"),
        "vectorborne_malariapf_2012-2022":         _v("pop_exposed_vectorborne_malariapf_2012-2022"),
        "vectorborne_malariapv_2012-2022":         _v("pop_exposed_vectorborne_malariapv_2012-2022"),
        "agricultural_drought_fao_1984-2023":      _v("pop_exposed_agricultural_drought_fao_1984-2023"),
        "drought_spei_terraclimate_1958-2025":     _v("pop_exposed_drought_spei_terraclimate_1958-2025"),
        "drought_spi_terraclimate_1958-2025":      _v("pop_exposed_drought_spi_terraclimate_1958-2025"),
        "heatwave_frequency_ecmwf_2014-2024":      _v("pop_exposed_heatwave_frequency_ecmwf_2014-2024"),
        "heatwave_duration_ecmwf_2014-2024":       _v("pop_exposed_heatwave_duration_ecmwf_2014-2024"),
        "heatwave_severity_ecmwf_2014-2024":       _v("pop_exposed_heatwave_severity_ecmwf_2014-2024"),
        "fire_FRP_nasa_2001-2024":                 _v("pop_exposed_fire_FRP_nasa_2001-2024"),
        "fire_frequency_nasa_2001-2023":           _v("pop_exposed_fire_frequency_nasa_2001-2023"),
    }

    for topic_name in HAZARD_TOPICS:
        if topic_name in EXPOSURE_ONLY_TOPICS:
            continue
        safe_key = "cov_" + re.sub(r"[^a-zA-Z0-9]", "_", topic_name)
        result[safe_key] = _cov(result.get(topic_name, 0))

    for opt in MHC_OPTIONS:
        result[f"count_filter_{opt}"] = _v(f"pop_topic_ge_{opt}")
    for opt in MHI_OPTIONS:
        result[f"intensity_filter_{opt}"] = _v(f"pop_mhi_p{opt}")

    return result


# ---------------------------------------------------------------------------
# Country helpers
# ---------------------------------------------------------------------------

# Display-name overrides for territories whose GEE asset "name" differs from
# the UNSD/ISO name we want shown in the UI (dropdown and result labels).
COUNTRY_NAME_OVERRIDES = {
    "Taiwan": "China, Taiwan Province of China",
    "Macao": "China, Macao SAR",
    "Hong Kong": "China, Hong Kong SAR",
}
_COUNTRY_NAME_TO_ASSET = {v: k for k, v in COUNTRY_NAME_OVERRIDES.items()}


@lru_cache(maxsize=1)
def get_country_names():
    names = (
        ee.FeatureCollection(ADMIN_DATA["adm0 (Country)"]["asset"])
        .filter(ee.Filter.And(
            ee.Filter.neq("type", "Antarctica"),
            ee.Filter.neq("type", "Sovereignty unsettled"),
        ))
        .aggregate_array("name").getInfo()
    )
    return sorted(COUNTRY_NAME_OVERRIDES.get(n, n) for n in names)


@lru_cache(maxsize=512)
def get_country_ucode(country_name):
    fc = ee.FeatureCollection(ADMIN_DATA["adm0 (Country)"]["asset"])
    asset_name = _COUNTRY_NAME_TO_ASSET.get(country_name, country_name)
    return fc.filter(ee.Filter.eq("name", asset_name)).first().get("ucode").getInfo()


@lru_cache(maxsize=512)
def get_country_bounds(country_ucode):
    fc = ee.FeatureCollection(ADMIN_DATA["adm0 (Country)"]["asset"])
    coords = (
        fc.filter(ee.Filter.eq("ucode", country_ucode))
        .first().geometry().bounds(1).coordinates().getInfo()[0]
    )
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    min_lon, max_lon = min(lons), max(lons)
    if max_lon > 180:
        if min_lon > 100:
            # Entire box in extended-east space (e.g. USA — Aleutians push west past dateline)
            # Shift west bound back into [-180, 180]; east bound normalises negative
            min_lon = max(min_lon - 360, -180)
            max_lon = max_lon - 360
        else:
            # Box straddles dateline from the west side (e.g. Russia, Fiji)
            max_lon = 180
    return [[min(lats), min_lon], [max(lats), max_lon]]


# ---------------------------------------------------------------------------
# Tile URL helpers (cached 1 h — GEE tokens expire)
# ---------------------------------------------------------------------------

@_ttl_cached(cache=_tile_cache(64), lock=_tile_lock)
def get_topic_tile_url(topic_name, color):
    core = build_core_images()
    vis  = {"palette": [color], "min": 0, "max": 1}
    mid  = core["topic_masks"][topic_name].getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


def get_topic_tile_url_thr(topic_name, color, threshold_overrides=None):
    """Topic exposure tile honoring per-hazard threshold overrides. Falls back to
    the cached default tile when no override affects this topic (keeps the fast
    path + cache for the common case)."""
    ov = {h: v for h, v in (threshold_overrides or {}).items()
          if h in HAZARD_TOPICS.get(topic_name, [])}
    if not ov:
        return get_topic_tile_url(topic_name, color)
    core = build_core_images()
    _, topic_masks = apply_threshold_overrides(core, ov, [topic_name])
    vis = {"palette": [color], "min": 0, "max": 1}
    mid = topic_masks[topic_name].getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(16), lock=_tile_lock)
def get_topic_count_tile_url(min_count=1):
    core  = build_core_images()
    n     = len(HAZARD_TOPICS) - len(MHC_EXCLUDED_TOPICS)
    img   = core["topic_count_image"]
    if min_count and int(min_count) > 1:
        img = img.updateMask(img.gte(int(min_count)))
    vis = {"min": 0, "max": n, "palette": ["#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404"]}
    mid = img.getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(1), lock=_tile_lock)
def get_pixel_score_tile_url():
    vis = {"min": 0, "max": 10, "palette": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d"]}
    mid = ee.Image("projects/unicef-ccri/assets/hazards/MHI_climate").getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(8), lock=_tile_lock)
def get_pixel_score_percentile_tile_url(percentile):
    core         = build_core_images()
    hazard_score = core["hazard_score"]
    land_mask_base = core["country_boundaries_reproj"]
    target_crs   = core["target_crs"]
    target_scale = core["target_scale"]
    global_geom  = core["global_geom"]

    land_mask = (
        ee.Image(1).clip(land_mask_base).unmask(0)
        .reproject(crs=target_crs, scale=target_scale)
    )
    threshold = (
        hazard_score.updateMask(land_mask)
        .reduceRegion(
            reducer=ee.Reducer.percentile([int(percentile)]),
            geometry=global_geom,
            scale=hazard_score.projection().nominalScale(),
            bestEffort=True,
        ).values().get(0)
    )
    masked = hazard_score.updateMask(hazard_score.gt(ee.Number(threshold)))
    vis = {"min": 0, "max": 10, "palette": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d"]}
    mid = masked.getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(64), lock=_tile_lock)
def get_hazard_tile_url(hazard_name):
    hazard = HAZARD_MAP.get(hazard_name)
    if not hazard:
        return None, None

    if hazard.get("isImage"):
        image = ee.Image(hazard["id"])
    elif re.search(r"flood|storm", hazard["name"]):
        image = ee.ImageCollection(hazard["id"]).mosaic()
    else:
        image = ee.Image(hazard["id"])
    if hazard.get("band"):
        image = image.select(hazard["band"])

    image = image.updateMask(image.gt(0) if "malaria" in hazard["name"] else image.gt(-1000))
    palette = HAZARD_VIS_PALETTES.get(hazard_name, ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"])

    if hazard_name == "coastal_flood_100yr_jrc_2024":
        vis = {"min": 0, "max": 1, "palette": palette}
    else:
        band_name = image.bandNames().get(0).getInfo()
        stats = image.reduceRegion(
            reducer=ee.Reducer.percentile([2, 98]),
            geometry=ee.Geometry.Polygon([GLOBAL_GEOMETRY], None, False),
            scale=image.projection().nominalScale(),
            bestEffort=True, maxPixels=1e13,
        ).getInfo()
        p2  = stats.get(f"{band_name}_p2", 0)
        p98 = stats.get(f"{band_name}_p98", 1)
        mn  = p2 if hazard_name in ALLOW_NEGATIVE else max(0, p2 or 0)
        vis = {"min": mn, "max": p98, "palette": palette}

    if hazard_name in SELF_MASK_HAZARDS:
        image = image.selfMask()

    mid = image.getMapId(vis)
    return mid["tile_fetcher"].url_format, vis


@_ttl_cached(cache=_tile_cache(256), lock=_tile_lock)
def get_admin_boundary_tile_url(admin_level, country_ucode):
    cfg = ADMIN_DATA[admin_level]
    if admin_level == "adm0 (Country)":
        fc = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("ucode", country_ucode))
    else:
        fc = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("adm0_ucode", country_ucode))
    styled = fc.style(color="2255CC", width=1, fillColor="00000000")
    mid = styled.getMapId({})
    return mid["tile_fetcher"].url_format


@_ttl_cached(cache=_tile_cache(256), lock=_tile_lock)
def get_selected_feature_tile_url(admin_level, feature_ucode):
    cfg    = ADMIN_DATA[admin_level]
    fc     = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("ucode", feature_ucode))
    styled = fc.style(color="FFD700", width=2, fillColor="FFD70030")
    mid    = styled.getMapId({})
    return mid["tile_fetcher"].url_format


# ---------------------------------------------------------------------------
# Feature lookup by click
# ---------------------------------------------------------------------------

def get_feature_at_point(lon, lat, admin_level, country_ucode):
    cfg   = ADMIN_DATA[admin_level]
    point = ee.Geometry.Point([lon, lat])
    if admin_level == "adm0 (Country)":
        fc = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("ucode", country_ucode))
    else:
        fc = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("adm0_ucode", country_ucode))
    props = fc.filterBounds(point).first().toDictionary(["ucode", cfg["name_prop"]]).getInfo()
    if not props:
        return None, None
    return props.get("ucode"), props.get(cfg["name_prop"])


# ---------------------------------------------------------------------------
# Exposure computation
# ---------------------------------------------------------------------------

def compute_exposure(feature_ucode, admin_level, mhc_value=None, mhi_percentile=None,
                     topics=None, threshold_overrides=None):
    """Children exposed per hazard topic for an admin region.

    `topics`: optional list of topic names to restrict the analysis to (None =
    all climate topics, the historical default). `threshold_overrides`: optional
    {hazard_name: threshold} — only those hazards are recomputed with the new
    threshold; every other topic reuses the cached default masks.
    """
    threshold_overrides = threshold_overrides or {}
    is_default = (topics is None) and (not threshold_overrides)

    # The adm0 asset fast path holds precomputed exposure at fixed thresholds and
    # for all topics — it can only serve the default request.
    if admin_level == "adm0 (Country)" and is_default:
        return compute_exposure_adm0_from_asset(feature_ucode)

    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    topic_cov   = core["topic_coverage"]
    topic_count = core["topic_count_image"]
    hazard_score= core["hazard_score"]
    global_geom = core["global_geom"]
    land_mask_base = core["country_boundaries_reproj"]
    target_crs  = core["target_crs"]
    target_scale= core["target_scale"]

    # Selected topics: default = all climate topics (as before). A provided list
    # is honored as-is (may include geophysical EXPOSURE_ONLY_TOPICS).
    if topics is None:
        sel_topics = [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]
    else:
        sel_topics = [t for t in topics if t in HAZARD_TOPICS]

    # Recompute only the overridden hazards + rebuild affected topic masks
    # (shared helper keeps override semantics identical across the app).
    exposure_by, topic_masks = apply_threshold_overrides(
        core, threshold_overrides, sel_topics)

    bands = []
    for topic_name in sel_topics:
        bands.append(childpop.updateMask(topic_masks[topic_name]).rename(topic_name))
        bands.append(topic_cov[topic_name])

    for topic_name in ["Malaria", "Heatwave", "Fire", "Drought"]:
        if topic_name not in sel_topics:
            continue
        for h_name in HAZARD_TOPICS[topic_name]:
            bands.append(exposure_by[h_name].rename(h_name))

    combined = (
        ee.Image.cat(bands)
        .addBands(childpop.rename("total_population"))
        .addBands(childpop_m.rename("total_population_male"))
        .addBands(childpop_f.rename("total_population_female"))
    )

    for opt in MHC_OPTIONS:
        count_mask = topic_count.gte(ee.Number(int(opt)))
        combined = combined.addBands(childpop.updateMask(count_mask).rename(f"count_filter_{opt}"))

    if mhi_percentile:
        land_mask = (
            ee.Image(1).clip(land_mask_base).unmask(0)
            .reproject(crs=target_crs, scale=target_scale)
        )
        mhi_threshold = (
            hazard_score.updateMask(land_mask)
            .reduceRegion(
                reducer=ee.Reducer.percentile([int(mhi_percentile)]),
                geometry=global_geom,
                scale=hazard_score.projection().nominalScale(),
                bestEffort=True,
            ).values().get(0)
        )
        intensity_mask = hazard_score.gt(ee.Number(mhi_threshold))
        combined = combined.addBands(childpop.updateMask(intensity_mask).rename("active_intensity_filter"))

    chunk_asset = ADMIN_DATA[admin_level]["chunk_asset"]
    chunks      = ee.FeatureCollection(chunk_asset).filter(ee.Filter.eq("ucode", feature_ucode))
    band_names  = combined.bandNames()
    num_bands   = band_names.size()

    tile_results = combined.reduceRegions(
        collection=chunks, reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=1,
    )
    sums  = tile_results.reduceColumns(ee.Reducer.sum().repeat(num_bands), band_names)
    stats = ee.Dictionary.fromLists(band_names, sums.get("sum"))
    return stats.getInfo()


def compute_topic_overlap(feature_ucode, admin_level, topic_names):
    """Children exposed to ALL of the listed hazard topics simultaneously (intersection)."""
    core        = build_core_images()
    childpop    = core["childpop"]
    pop_res     = core["pop_target_res"]
    topic_masks = core["topic_masks"]

    combined_mask = topic_masks[topic_names[0]]
    for t in topic_names[1:]:
        combined_mask = combined_mask.And(topic_masks[t])

    combined = (
        childpop.updateMask(combined_mask).rename("overlap")
        .addBands(childpop.rename("total_population"))
    )
    chunk_asset = ADMIN_DATA[admin_level]["chunk_asset"]
    chunks      = ee.FeatureCollection(chunk_asset).filter(ee.Filter.eq("ucode", feature_ucode))
    band_names  = combined.bandNames()
    tile_results = combined.reduceRegions(
        collection=chunks, reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=1,
    )
    sums  = tile_results.reduceColumns(ee.Reducer.sum().repeat(band_names.size()), band_names)
    stats = ee.Dictionary.fromLists(band_names, sums.get("sum"))
    return stats.getInfo()


@lru_cache(maxsize=32)
def get_asset_info(asset_id):
    """Return (feature_count, [property_names]) for a GEE FeatureCollection asset."""
    fc    = ee.FeatureCollection(asset_id)
    n     = fc.size().getInfo()
    props = fc.first().propertyNames().getInfo()
    return n, props


@lru_cache(maxsize=32)
def get_asset_bounds(asset_id):
    """Return [[minlat, minlon], [maxlat, maxlon]] for a GEE FeatureCollection asset."""
    fc     = ee.FeatureCollection(asset_id)
    coords = fc.geometry().bounds(1).coordinates().getInfo()[0]
    lons   = [c[0] for c in coords]
    lats   = [c[1] for c in coords]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


@_ttl_cached(cache=_tile_cache(32), lock=_tile_lock)
def get_custom_asset_tile_url(asset_id):
    """Return tile URL for a styled GEE FeatureCollection asset."""
    fc     = ee.FeatureCollection(asset_id)
    styled = fc.style(color="e67e22", width=2, fillColor="e67e2208")
    mid    = styled.getMapId({})
    return mid["tile_fetcher"].url_format


def compute_exposure_asset(asset_id, threshold_overrides=None):
    """Run per-hazard exposure for a GEE FeatureCollection asset.
    Returns list of property dicts (geometries stripped).
    `threshold_overrides`: optional {hazard_name: value} applied per hazard.
    """
    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    exposure_by, _ = apply_threshold_overrides(core, threshold_overrides)

    hazard_names = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]
    bands = [exposure_by[n].rename(n) for n in hazard_names if n in exposure_by]
    combined = (
        ee.Image.cat(bands)
        .addBands(childpop.rename("total_population"))
        .addBands(childpop_m.rename("total_population_male"))
        .addBands(childpop_f.rename("total_population_female"))
    )
    fc      = ee.FeatureCollection(asset_id)
    results = combined.reduceRegions(
        collection=fc, reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=8,
    )
    props_only = results.map(lambda f: ee.Feature(None, f.toDictionary()))
    return props_only.getInfo()["features"]


def compute_exposure_custom(geojson_dict, threshold_overrides=None):
    """Run per-hazard exposure for every feature in a GeoJSON FeatureCollection.
    Returns list of property dicts (geometries stripped to reduce payload size).
    `threshold_overrides`: optional {hazard_name: value} applied per hazard.
    """
    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    exposure_by, _ = apply_threshold_overrides(core, threshold_overrides)

    hazard_names = [h["name"] for h in HAZARDS if h["name"] != "Pixel Based Hazard Score"]

    bands = [exposure_by[n].rename(n) for n in hazard_names if n in exposure_by]
    combined = (
        ee.Image.cat(bands)
        .addBands(childpop.rename("total_population"))
        .addBands(childpop_m.rename("total_population_male"))
        .addBands(childpop_f.rename("total_population_female"))
    )

    fc      = ee.FeatureCollection(geojson_dict)
    results = combined.reduceRegions(
        collection=fc, reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=8,
    )
    # Strip geometries before getInfo() to keep payload small for large feature sets
    props_only = results.map(lambda f: ee.Feature(None, f.toDictionary()))
    return props_only.getInfo()["features"]


# ---------------------------------------------------------------------------
# Infrastructure analysis (Infrastructure tab)
# Point assets (schools / health facilities / water points) correlated with
# the existing hazard footprints and child-population grid.
# All reductions run at the population grid's native scale (pop_target_res)
# so results reconcile with the rest of the dashboard.
# ---------------------------------------------------------------------------

# Topics not flagged as facility hazards (geophysical / non-climate exposure-only)
_INFRA_TOPICS = [t for t in HAZARD_TOPICS if t not in EXPOSURE_ONLY_TOPICS]


def _adm2_region(adm2_ucode):
    """Resolve the geometry of an adm2 unit by ucode (the analysis bound)."""
    cfg = ADMIN_DATA["adm2 (Districts/Counties)"]
    fc  = ee.FeatureCollection(cfg["asset"]).filter(ee.Filter.eq("ucode", adm2_ucode))
    return fc.geometry()


_ADM2_POP_ASSET = "projects/unicef-ccri/assets/global_boundary/admin2_pop_geom"


@lru_cache(maxsize=512)
def _adm2_child_pop(adm2_ucode):
    """Precomputed under-18 population for an adm2 unit (no GEE aggregation).
    Reads pop_under_18_total from admin2_pop_geom; None if unavailable."""
    try:
        feat = (ee.FeatureCollection(_ADM2_POP_ASSET)
                .filter(ee.Filter.eq("adm2_ucode", adm2_ucode)).first())
        return ee.Feature(feat).get("pop_under_18_total").getInfo()
    except Exception:
        return None


@_ttl_cached(cache=_tile_cache(64), lock=_tile_lock)
def get_infra_tile_url(asset_id, color, adm2_ucode=None):
    """Return tile URL for a styled infrastructure point FeatureCollection.
    When adm2_ucode is given, only facilities inside that district are shown."""
    fc = ee.FeatureCollection(asset_id)
    if adm2_ucode:
        fc = fc.filterBounds(_adm2_region(adm2_ucode))
    # Small points with a thin white edge so they read over the population raster.
    styled = fc.style(color="ffffff", fillColor=color, pointSize=3, width=1)
    mid    = styled.getMapId({})
    return mid["tile_fetcher"].url_format


def _topic_union_mask(core, topics, topic_masks=None):
    """Constant-1 image masked to the OR-union of the given topic footprints.
    Falls back to the union of all infra topics when none specified.
    `topic_masks` lets callers pass threshold-adjusted masks; defaults to cached."""
    tmasks = topic_masks if topic_masks is not None else core["topic_masks"]
    names = [t for t in (topics or _INFRA_TOPICS) if t in tmasks]
    if not names:
        names = _INFRA_TOPICS
    union = tmasks[names[0]].mask()
    for n in names[1:]:
        union = union.Or(tmasks[n].mask())
    return ee.Image.constant(1).updateMask(union)


def _name_prop(props):
    """Pick the best available name property from an asset's property list."""
    for c in ("name", "facility_id", "id"):
        if c in props:
            return c
    return props[0] if props else "name"


def _sel_topics(topic_masks, topics):
    sel = [t for t in (topics or _INFRA_TOPICS) if t in topic_masks]
    return sel or list(_INFRA_TOPICS)


def _subhazards_for(sel_topics):
    """Ordered (topic, [hazard_name,...]) for topics that have a detail breakdown."""
    return [(t, HAZARD_TOPICS[t]) for t in sel_topics if t in SUB_TOPIC_DETAIL]


def _voronoi_cells(points, adm2_geojson):
    """Nearest-facility (Voronoi) partition of the adm2, client-side (shapely).

    points: list of (lon, lat, fname). Returns a GeoJSON FeatureCollection of
    cells (one per facility, clipped to the adm2), each with {fname, lon, lat}.
    Cells tile the district with no gaps/overlaps → per-facility pop is summable.
    """
    from shapely.geometry import MultiPoint, shape, Point, mapping
    from shapely.ops import voronoi_diagram

    adm2 = shape(adm2_geojson)
    seeds = [Point(x, y) for x, y, _ in points]
    feats = []
    if len(points) == 1:
        # Single facility → the whole district is its cell.
        x, y, nm = points[0]
        cell = adm2
        if not cell.is_empty:
            feats.append({"type": "Feature", "geometry": mapping(cell),
                          "properties": {"fname": nm, "lon": x, "lat": y}})
        return {"type": "FeatureCollection", "features": feats}

    mp = MultiPoint([(x, y) for x, y, _ in points])
    vor = voronoi_diagram(mp, envelope=adm2)
    for cell in vor.geoms:
        c = cell.intersection(adm2)
        if c.is_empty:
            continue
        ctr = c.representative_point()
        j = min(range(len(seeds)), key=lambda i: seeds[i].distance(ctr))
        x, y, nm = points[j]
        feats.append({"type": "Feature", "geometry": mapping(c),
                      "properties": {"fname": nm, "lon": x, "lat": y}})
    return {"type": "FeatureCollection", "features": feats}


def compute_facility_combined(asset_id, adm2_ucode, topics=None, threshold_overrides=None):
    """Single combined analysis for one adm2 district:

      • Exposed population per hazard — aggregated over the WHOLE adm2 (Voronoi
        partitions the district, so the district total IS the served total).
      • Facility-site exposure — which facilities sit in each hazard, at what
        raw intensity (reuses compute_facility_site).
      • Per-facility Voronoi population — each facility's nearest-neighbour
        catchment child population + hazard-exposed share (summable, no overlap).

    `threshold_overrides`: optional {hazard_name: value} applied per hazard so the
    facility summary uses the same custom thresholds as the Analysis tab.

    Returns a merged dict (see keys below). Kept to a small number of sequential
    getInfo calls (never fan-out) to respect the GEE concurrency limit.
    """
    core        = build_core_images()
    childpop    = core["childpop"]
    pop_res     = core["pop_target_res"]
    region      = _adm2_region(adm2_ucode)

    sel_topics = _sel_topics(core["topic_masks"], topics)
    exposure_by, topic_masks = apply_threshold_overrides(
        core, threshold_overrides, sel_topics)
    sub_names  = [h for _t, hs in _subhazards_for(sel_topics)
                  for h in hs if h in exposure_by]
    union_mask = _topic_union_mask(core, sel_topics, topic_masks)

    # ── (1) Aggregate exposed population per hazard over the whole adm2 ──
    def _bn(prefix, i):
        return f"b{prefix}{i}"

    band_imgs = [childpop.rename("bTotal"),
                 childpop.updateMask(union_mask).rename("bExposed")]
    band_keys = [None, None]
    for i, t in enumerate(sel_topics):
        band_imgs.append(childpop.updateMask(topic_masks[t]).rename(_bn("t", i)))
        band_keys.append(("topic", t))
    for i, h in enumerate(sub_names):
        band_imgs.append(childpop.updateMask(exposure_by[h].mask()).rename(_bn("s", i)))
        band_keys.append(("sub", h))
    band_names = ["bTotal", "bExposed"] + \
                 [_bn("t", i) for i in range(len(sel_topics))] + \
                 [_bn("s", i) for i in range(len(sub_names))]

    stats = ee.Image.cat(band_imgs).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=pop_res,
        bestEffort=True, maxPixels=int(1e10),
    ).getInfo()

    per_topic, per_sub = {}, {}
    for key, bname in zip(band_keys, band_names):
        if isinstance(key, tuple):
            (per_topic if key[0] == "topic" else per_sub)[key[1]] = stats.get(bname)

    # ── (2) Facility-site exposure (flags + raw intensity per facility) ──
    site = compute_facility_site(asset_id, adm2_ucode, topics, threshold_overrides)
    facilities = site["facilities"]
    site_tally = site["tally"]

    # ── (3) Voronoi per-facility population (nearest-neighbour catchments) ──
    points = [(p["properties"].get("lon"), p["properties"].get("lat"),
               p["properties"].get("fname"))
              for p in facilities
              if p["properties"].get("lon") is not None]
    adm2_geo = region.getInfo()
    cells = _voronoi_cells(points, adm2_geo)

    pop_bands = [childpop.rename("vpop"),
                 childpop.updateMask(union_mask).rename("vexp")]
    cell_stats = ee.Image.cat(pop_bands).reduceRegions(
        collection=ee.FeatureCollection(cells), reducer=ee.Reducer.sum(),
        scale=pop_res, tileScale=8,
    ).map(lambda f: ee.Feature(None, ee.Feature(f).toDictionary(
        ["fname", "lon", "lat", "vpop", "vexp"])))
    cell_rows = cell_stats.getInfo()["features"]

    return {
        "n_facilities":             len(facilities),
        "subregion_children_total": _adm2_child_pop(adm2_ucode),
        "served_children_total":    stats.get("bTotal"),
        "served_children_exposed":  stats.get("bExposed"),
        "per_topic_exposed":        per_topic,
        "per_subhazard_exposed":    per_sub,
        "facility_site_tally":      site_tally,
        "facilities_site":          facilities,     # flags + intensity per facility
        "facilities_voronoi":       cell_rows,      # vpop / vexp per facility
        "voronoi_geojson":          cells,
        "topic_cols":               site["topic_cols"],
        "intensity_cols":           site["intensity_cols"],
        "topics":                   sel_topics,
    }


def compute_facility_site(asset_id, adm2_ucode, topics=None, threshold_overrides=None):
    """Concept 2: whether each facility SITE sits in a hazard footprint, and at
    what raw intensity (native units) — sampled at the facility point.

    `threshold_overrides`: optional {hazard_name: value} so the in-hazard flags
    honor the same custom thresholds as the rest of the analysis.

    Returns {
      "facilities": [{fname, lon, lat, <topic>:0|1, <subhazard>:intensity, ...}],
      "topic_cols":     [topics],
      "intensity_cols": [subhazard hazard_names],
      "tally":          {topic: n_facilities_flagged, ..., "total": n},
      "topics":         sel_topics,
    }
    Single reduceRegions + one server-side tally (concurrency-safe).
    """
    core         = build_core_images()
    raw_hazard   = core["raw_hazard"]
    target_scale = core["target_scale"]
    region       = _adm2_region(adm2_ucode)

    sel_topics = _sel_topics(core["topic_masks"], topics)
    _, topic_masks = apply_threshold_overrides(core, threshold_overrides, sel_topics)
    # Sub-hazard intensity layers for the selected topics (skip missing).
    sub_names = [h for t in sel_topics for h in HAZARD_TOPICS[t] if h in raw_hazard]

    try:
        _, props = get_asset_info(asset_id)
    except Exception:
        props = []
    name_prop = _name_prop(props)

    fc = ee.FeatureCollection(asset_id).filterBounds(region)

    # Safe band names; map back after reduction.
    flag_names = [f"tf{i}" for i in range(len(sel_topics))]
    int_names  = [f"hi{i}" for i in range(len(sub_names))]
    bands = [topic_masks[t].unmask(0).rename(flag_names[i])
             for i, t in enumerate(sel_topics)]
    bands += [raw_hazard[h].rename(int_names[i]) for i, h in enumerate(sub_names)]
    stack = ee.Image.cat(bands)

    sampled = stack.reduceRegions(
        collection=fc, reducer=ee.Reducer.first(),
        scale=target_scale, tileScale=8,
    )

    def _tidy(f):
        f = ee.Feature(f)
        coords = f.geometry().centroid(1).coordinates()
        out = {"fname": f.get(name_prop),
               "lon": coords.get(0), "lat": coords.get(1)}
        for i, t in enumerate(sel_topics):
            out[t] = f.get(flag_names[i])
        for i, h in enumerate(sub_names):
            out[h] = f.get(int_names[i])
        return ee.Feature(None, out)

    tidy = sampled.map(_tidy)

    # Server-side tally: facilities flagged per topic + total (one getInfo).
    tally = {t: sampled.aggregate_sum(flag_names[i])
             for i, t in enumerate(sel_topics)}
    tally["total"] = fc.size()

    return {
        "facilities":     tidy.getInfo()["features"],
        "topic_cols":     sel_topics,
        "intensity_cols": sub_names,
        "tally":          ee.Dictionary(tally).getInfo(),
        "topics":         sel_topics,
    }


# Population raster vis (yellow -> red); shared with the map legend.
INFRA_POP_VIS = {
    "min": 0, "max": 50,
    "palette": ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
}


@_ttl_cached(cache=_tile_cache(32), lock=_tile_lock)
def get_clipped_pop_tile_url(asset_id, adm2_ucode):
    """Child-population raster clipped to the adm2 district.
    Returns (tile_url, vis_dict) so the legend can reuse the exact stops."""
    core    = build_core_images()
    region  = _adm2_region(adm2_ucode)
    clipped = core["childpop"].clip(region).selfMask()
    mid = clipped.getMapId(INFRA_POP_VIS)
    return mid["tile_fetcher"].url_format, INFRA_POP_VIS
