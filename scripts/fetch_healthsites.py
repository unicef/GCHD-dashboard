"""
fetch_healthsites.py — download ALL health facilities for a country from the
healthsites.io API (Global Healthsites Mapping Project), paginating until every
record is retrieved.

healthsites.io is a dedicated open health-facility dataset (built on OSM), not a
generic points-of-interest layer — so no post-hoc amenity filtering is needed.

    GET https://healthsites.io/api/v3/facilities/
        ?api-key=<KEY>&country=<Name>&output=geojson
        &flat-properties=true&tag-format=osm&page={n}
    A page with < page_size (100) features is the last.
    Each GeoJSON feature carries centroid.coordinates = [lon, lat] and a flat
    "properties" object (name, amenity/healthcare type, osm_id, uuid, ...).

Country scoping is by NAME (the API has no ISO3 filter), taken from the adm0
boundary asset via scripts/adm0_countries.json. adm0 uses formal UN names
("United Republic of Tanzania") which healthsites may spell differently; when a
country returns nothing, pass --country with healthsites' own spelling and add
it to ISO3_HEALTHSITES_NAMES below.

Get an API key: sign in to https://healthsites.io with an OpenStreetMap account
and copy the token from your profile page. Put it in scripts/.env as
HEALTHSITES_API_KEY (see .env.example).

Output: app/data/infra/{iso3}/raw/healthsites.csv

Usage:
    python scripts/fetch_healthsites.py --iso3 mwi
    python scripts/fetch_healthsites.py --iso3 mwi --iso3 eth --iso3 gha
    python scripts/fetch_healthsites.py --iso3 mwi --max-pages 1   # smoke test
"""
import argparse
import os
import sys

import pandas as pd
import requests

from _common import (load_env, require_key, adm0_countries, country_name,
                     get_with_retries, raw_dir, script_cmd)

API_URL = "https://healthsites.io/api/v3/facilities/"
PAGE_SIZE = 100            # healthsites.io returns up to 100 features per page
ENV_API_KEY = "HEALTHSITES_API_KEY"

# ISO3 -> healthsites' country spelling, where it differs from adm0's formal
# name. Add entries here as they're found (a country returning 0 features with a
# valid key is the usual symptom).
ISO3_HEALTHSITES_NAMES = {
    "bol": "Bolivia",
    "cod": "Democratic Republic of the Congo",
    "cog": "Republic of the Congo",
    "cze": "Czechia",
    "fsm": "Micronesia",
    "gbr": "United Kingdom",
    "irn": "Iran",
    "kor": "South Korea",
    "lao": "Laos",
    "prk": "North Korea",
    "pse": "Palestine",
    "rus": "Russia",
    "syr": "Syria",
    "tza": "Tanzania",
    "usa": "United States of America",
    "vat": "Vatican City",
    "vnm": "Vietnam",
}


def fetch_all(sess, country, api_key, max_pages=None):
    """Page through the healthsites facilities endpoint; return all features."""
    base_params = {
        "api-key": api_key,
        "country": country,
        "output": "geojson",
        "flat-properties": "true",
        "tag-format": "osm",
    }
    fatal = {
        401: f"check the API key (env {ENV_API_KEY} or scripts/.env).",
        403: f"check the API key (env {ENV_API_KEY} or scripts/.env). Get a "
             "token from your healthsites.io profile (sign in with OSM).",
    }

    all_features = []
    page = 1
    while True:
        params = {**base_params, "page": page}
        resp = get_with_retries(sess, API_URL, params=params, timeout=120,
                                fatal_statuses=fatal)

        body = resp.json()
        features = body.get("features", []) if isinstance(body, dict) else body
        all_features.extend(features)
        print(f"  page {page:>4}: {len(features):>5} features "
              f"(running total {len(all_features)})")

        if len(features) < PAGE_SIZE:      # partial/empty page -> last page
            break
        if max_pages and page >= max_pages:
            print(f"  reached --max-pages limit ({max_pages}); stopping")
            break
        page += 1

    return all_features


def _features_to_df(features):
    """Flatten GeoJSON features into a DataFrame with longitude/latitude.

    Each feature has flat 'properties' (flat-properties=true) and a 'centroid'
    geometry with coordinates [lon, lat]; fall back to a top-level Point."""
    rows = []
    for feat in features:
        props = dict(feat.get("properties") or {})

        coords = (feat.get("centroid") or {}).get("coordinates")
        if not coords:
            geom = feat.get("geometry") or {}
            if geom.get("type") == "Point":
                coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue  # unmapped facility — skip

        props["longitude"] = coords[0]
        props["latitude"]  = coords[1]
        rows.append(props)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["latitude"]  = pd.to_numeric(df["latitude"], errors="coerce")
    bad = df["longitude"].isna() | df["latitude"].isna()
    if bad.any():
        print(f"  dropping {int(bad.sum())} records without valid coordinates")
        df = df[~bad].copy()
    return df


def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", required=True,
                    help="country ISO3 code; repeat for several")
    ap.add_argument("--country", default=None,
                    help="override the country name sent to healthsites "
                         "(only valid with a single --iso3)")
    ap.add_argument("--api-key", default=None,
                    help=f"healthsites.io API key (default: ${ENV_API_KEY} from "
                         "scripts/.env)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="cap pages fetched (for testing)")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default app/data/infra/{iso3}/raw); "
                         "only valid with a single --iso3")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip countries whose healthsites.csv already exists "
                         "(don't re-download or overwrite)")
    args = ap.parse_args()

    iso3s = [c.lower() for c in args.iso3]
    if len(iso3s) > 1 and (args.out_dir or args.country):
        sys.exit("ERROR: --out-dir/--country only work with a single --iso3.")

    api_key = args.api_key or require_key(
        ENV_API_KEY, "healthsites.io",
        "Sign in at https://healthsites.io with an OpenStreetMap account and "
        "copy the token from your profile page.")

    countries = adm0_countries()
    with requests.Session() as sess:
        for iso3 in iso3s:
            name = (args.country or ISO3_HEALTHSITES_NAMES.get(iso3)
                    or country_name(iso3, countries))
            out_path = os.path.join(raw_dir(iso3, args.out_dir), "healthsites.csv")
            if args.skip_existing and os.path.exists(out_path):
                print(f"\n{iso3.upper()} — exists, skipping (--skip-existing)")
                continue
            print(f"\n{iso3.upper()} — healthsites.io facilities for {name} ...")
            features = fetch_all(sess, name, api_key, max_pages=args.max_pages)
            if not features:
                print(f"  no features returned for {name!r}.\n"
                      f"  If this country should have data, healthsites may "
                      f"spell it differently — retry with\n"
                      f"      --iso3 {iso3} --country \"<their spelling>\"\n"
                      f"  and add it to ISO3_HEALTHSITES_NAMES in "
                      f"{os.path.basename(__file__)}.")
                continue

            df = _features_to_df(features)
            if df.empty:
                print("  no records with valid coordinates — nothing written")
                continue

            dest = raw_dir(iso3, args.out_dir)
            os.makedirs(dest, exist_ok=True)
            out = os.path.join(dest, "healthsites.csv")
            df.to_csv(out, index=False, encoding="utf-8")
            print(f"  wrote {len(df):>6} health facilities -> {out}")

    print(f"\nDone. Next: {script_cmd('prep_infra.py')} --iso3 <code> "
          "--source healthsites")


if __name__ == "__main__":
    main()
