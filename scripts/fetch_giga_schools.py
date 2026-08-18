"""
fetch_giga_schools.py — download ALL school locations for a country from the
Giga School Geolocation API, paginating until every record is retrieved.

The Giga Maps UI only lets you download one page at a time; this script walks
the page-number pagination server-side and concatenates every page.

Endpoint / auth / pagination mirror UNICEF's own giga-spatial client
(gigaspatial/handlers/giga/school_locations.py):

    GET https://uni-ooi-giga-maps-service.azurewebsites.net/api/v1
            /schools_location/country/{ISO3}?page={n}&size={size}
    Authorization: Bearer <API_KEY>
    Records live under the "data" key; a page with < size records is the last.

Country scoping is by ISO3 — the same key that names the GEE assets — so no
name lookup is needed.

Get an API key from https://maps.giga.global/ and put it in scripts/.env as
GIGA_SCHOOL_LOCATION_API_KEY (see .env.example).

Output: app/data/infra/{iso3}/raw/giga_schools.csv, with every column the API
returns plus guaranteed longitude/latitude columns.

Usage:
    python scripts/fetch_giga_schools.py --iso3 mwi
    python scripts/fetch_giga_schools.py --iso3 mwi --iso3 eth --iso3 gha
    python scripts/fetch_giga_schools.py --iso3 mdg --size 1000 --max-pages 1
"""
import argparse
import os
import sys

import pandas as pd
import requests

from _common import (load_env, require_key, adm0_countries, country_name,
                     get_with_retries, raw_dir, script_cmd)

API_URL_TEMPLATE = (
    "https://uni-ooi-giga-maps-service.azurewebsites.net/api/v1"
    "/schools_location/country/{iso3}"
)
RECORDS_KEY = "data"       # JSON key holding the list of school records
ENV_API_KEY = "GIGA_SCHOOL_LOCATION_API_KEY"


def fetch_all(sess, iso3, api_key, size=1000, max_pages=None):
    """Page through the Giga school-location endpoint and return all records."""
    url = API_URL_TEMPLATE.format(iso3=iso3.upper())
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    fatal = {
        401: f"check the API key (env {ENV_API_KEY} or scripts/.env).",
        403: f"check the API key (env {ENV_API_KEY} or scripts/.env).",
    }

    all_records = []
    page = 1
    while True:
        params = {"page": page, "size": size}
        resp = get_with_retries(sess, url, params=params, headers=headers,
                                timeout=120, fatal_statuses=fatal)
        if resp.status_code == 404:
            print(f"  404 — Giga has no data for {iso3.upper()}")
            return []

        body = resp.json()
        # The endpoint nests records under "data"; tolerate a bare list too.
        records = body.get(RECORDS_KEY, []) if isinstance(body, dict) else body
        all_records.extend(records)
        print(f"  page {page:>4}: {len(records):>5} records "
              f"(running total {len(all_records)})")

        if len(records) < size:      # partial/empty page -> last page
            break
        if max_pages and page >= max_pages:
            print(f"  reached --max-pages limit ({max_pages}); stopping")
            break
        page += 1

    return all_records


def _ensure_lon_lat(df):
    """Guarantee longitude/latitude columns (the API uses those names, but be
    defensive about lon/lat variants) so the prepped CSV is point-ingestable."""
    lon_aliases = ["longitude", "lon", "lng", "lon_deg", "long"]
    lat_aliases = ["latitude", "lat", "lat_deg"]

    def pick(aliases):
        for a in aliases:
            if a in df.columns:
                return a
        return None

    lon_col, lat_col = pick(lon_aliases), pick(lat_aliases)
    if not lon_col or not lat_col:
        sys.exit("ERROR: could not find longitude/latitude columns in the API "
                 f"response. Columns returned: {list(df.columns)}")

    if lon_col != "longitude":
        df["longitude"] = df[lon_col]
    if lat_col != "latitude":
        df["latitude"] = df[lat_col]

    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["latitude"]  = pd.to_numeric(df["latitude"], errors="coerce")
    dropped = df["longitude"].isna() | df["latitude"].isna()
    if dropped.any():
        print(f"  dropping {int(dropped.sum())} records without valid coordinates")
        df = df[~dropped].copy()
    return df


def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", required=True,
                    help="country ISO3 code; repeat for several")
    ap.add_argument("--api-key", default=None,
                    help=f"Giga API bearer token (default: ${ENV_API_KEY} from "
                         "scripts/.env)")
    ap.add_argument("--size", type=int, default=1000,
                    help="records per page (default 1000)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="cap pages fetched (for testing)")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default app/data/infra/{iso3}/raw); "
                         "only valid with a single --iso3")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip countries whose giga_schools.csv already exists "
                         "(don't re-download or overwrite)")
    args = ap.parse_args()

    iso3s = [c.lower() for c in args.iso3]
    if args.out_dir and len(iso3s) > 1:
        sys.exit("ERROR: --out-dir only works with a single --iso3.")

    api_key = args.api_key or require_key(
        ENV_API_KEY, "Giga school locations",
        "Request access at https://maps.giga.global/.")

    countries = adm0_countries()
    with requests.Session() as sess:
        for iso3 in iso3s:
            name = country_name(iso3, countries)
            out = os.path.join(raw_dir(iso3, args.out_dir), "giga_schools.csv")
            if args.skip_existing and os.path.exists(out):
                print(f"\n{iso3.upper()} — exists, skipping (--skip-existing)")
                continue
            print(f"\n{iso3.upper()} — Giga school locations for {name} ...")
            records = fetch_all(sess, iso3, api_key, size=args.size,
                                max_pages=args.max_pages)
            if not records:
                print("  no records returned — nothing written")
                continue

            df = _ensure_lon_lat(pd.DataFrame(records))
            dest = raw_dir(iso3, args.out_dir)
            os.makedirs(dest, exist_ok=True)
            out = os.path.join(dest, "giga_schools.csv")
            df.to_csv(out, index=False, encoding="utf-8")
            print(f"  wrote {len(df):>6} schools -> {out}")

    print(f"\nDone. Next: {script_cmd('prep_infra.py')} --iso3 <code> --source giga")


if __name__ == "__main__":
    main()
