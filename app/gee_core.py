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
    MHC_OPTIONS, MHI_OPTIONS,
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

    def summarize_population(hazard):
        if hazard.get("isImage"):
            layer = ee.Image(hazard["id"])
        elif re.search(r"flood|storm", hazard["name"]):
            layer = ee.ImageCollection(hazard["id"]).mosaic()
        else:
            layer = ee.Image(hazard["id"])
        if hazard.get("band"):
            layer = layer.select(hazard["band"])

        th = hazard["threshold"]
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

    exposure_by_hazard = {h["name"]: summarize_population(h) for h in HAZARDS}

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
    }


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

def compute_exposure(feature_ucode, admin_level, mhc_value=None, mhi_percentile=None):
    if admin_level == "adm0 (Country)":
        return compute_exposure_adm0_from_asset(feature_ucode)
    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    topic_masks = core["topic_masks"]
    topic_cov   = core["topic_coverage"]
    exposure_by = core["exposure_by_hazard"]
    topic_count = core["topic_count_image"]
    hazard_score= core["hazard_score"]
    global_geom = core["global_geom"]
    land_mask_base = core["country_boundaries_reproj"]
    target_crs  = core["target_crs"]
    target_scale= core["target_scale"]

    bands = []
    for topic_name in HAZARD_TOPICS:
        if topic_name in EXPOSURE_ONLY_TOPICS:
            continue
        bands.append(childpop.updateMask(topic_masks[topic_name]).rename(topic_name))
        bands.append(topic_cov[topic_name])

    for topic_name in ["Malaria", "Heatwave", "Fire", "Drought"]:
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


def compute_exposure_asset(asset_id):
    """Run per-hazard exposure for a GEE FeatureCollection asset.
    Returns list of property dicts (geometries stripped).
    """
    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    exposure_by = core["exposure_by_hazard"]

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


def compute_exposure_custom(geojson_dict):
    """Run per-hazard exposure for every feature in a GeoJSON FeatureCollection.
    Returns list of property dicts (geometries stripped to reduce payload size).
    """
    core        = build_core_images()
    childpop    = core["childpop"]
    childpop_m  = core["childpop_m"]
    childpop_f  = core["childpop_f"]
    pop_res     = core["pop_target_res"]
    exposure_by = core["exposure_by_hazard"]

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
