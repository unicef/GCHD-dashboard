#!/usr/bin/env python
"""
fetch_wpdx_water.py — download ALL water points for a country from WPdx+ (the
Water Point Data Exchange), paginating until every record is retrieved.

WPdx+ is the dedicated global rural water-point dataset (cleaned/harmonised),
fetched from its Socrata API:

    GET https://data.waterpointdata.org/resource/eqje-vguj.csv
        ?clean_country_id=<ISO3>&$limit=<n>&$offset=<m>
    Socrata offset/limit pagination; a short page is the last.
    Optional $$app_token raises rate limits (works anonymously without one).

Country scoping uses `clean_country_id`, which is already an ISO3 code — the
same key that names the GEE assets — so no ISO3->ISO2 conversion is needed.

Output: app/data/infra/{iso3}/raw/wpdx_water.csv, with every column the API
returns plus guaranteed longitude/latitude columns.

Usage:
    python scripts/fetch_wpdx_water.py --iso3 mwi
    python scripts/fetch_wpdx_water.py --iso3 mwi --iso3 eth --iso3 gha
    python scripts/fetch_wpdx_water.py --iso3 mwi --page-size 1000 --max-pages 1

An optional Socrata app token raises the rate limit; put it in scripts/.env as
WPDX_APP_TOKEN (see .env.example). It is not required.
"""
import argparse
import io
import os
import sys

import pandas as pd
import requests

from _common import (load_env, adm0_countries, country_name, get_with_retries,
                     raw_dir, script_cmd)

RESOURCE_URL = "https://data.waterpointdata.org/resource/eqje-vguj.csv"
DEFAULT_PAGE_SIZE = 50000    # Socrata allows large pages; WPdx handles this fine
ENV_APP_TOKEN = "WPDX_APP_TOKEN"


def fetch_all(sess, iso3, app_token=None, page_size=DEFAULT_PAGE_SIZE,
              max_pages=None):
    """Page through the WPdx+ Socrata CSV endpoint; return a concatenated frame."""
    headers = {"X-App-Token": app_token} if app_token else None

    frames = []
    offset = 0
    page = 1
    while True:
        params = {
            "clean_country_id": iso3.upper(),
            "$limit": page_size,
            "$offset": offset,
            "$order": "row_id",     # stable order so offset paging is consistent
        }
        resp = get_with_retries(sess, RESOURCE_URL, params=params,
                                headers=headers, timeout=600)
        chunk = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
        if chunk.empty:
            break

        frames.append(chunk)
        print(f"  page {page:>3}: {len(chunk):>6} rows "
              f"(running total {sum(len(f) for f in frames)})")

        if len(chunk) < page_size:       # short page -> last page
            break
        if max_pages and page >= max_pages:
            print(f"  reached --max-pages limit ({max_pages}); stopping")
            break
        offset += page_size
        page += 1

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _ensure_lon_lat(df):
    """Guarantee numeric longitude/latitude columns from WPdx's lon_deg/lat_deg."""
    if "lon_deg" not in df.columns or "lat_deg" not in df.columns:
        sys.exit("ERROR: WPdx response has no lon_deg/lat_deg columns. "
                 f"Columns: {list(df.columns)[:20]}")
    df["longitude"] = pd.to_numeric(df["lon_deg"], errors="coerce")
    df["latitude"]  = pd.to_numeric(df["lat_deg"], errors="coerce")
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
    ap.add_argument("--app-token", default=os.environ.get(ENV_APP_TOKEN),
                    help=f"optional Socrata app token (default: ${ENV_APP_TOKEN} "
                         "from scripts/.env)")
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                    help=f"rows per request (default {DEFAULT_PAGE_SIZE})")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="cap pages fetched (for testing)")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default app/data/infra/{iso3}/raw); "
                         "only valid with a single --iso3")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip countries whose wpdx_water.csv already exists "
                         "(don't re-download or overwrite)")
    args = ap.parse_args()

    iso3s = [c.lower() for c in args.iso3]
    if args.out_dir and len(iso3s) > 1:
        sys.exit("ERROR: --out-dir only works with a single --iso3.")

    countries = adm0_countries()
    with requests.Session() as sess:
        for iso3 in iso3s:
            name = country_name(iso3, countries)
            out_path = os.path.join(raw_dir(iso3, args.out_dir), "wpdx_water.csv")
            if args.skip_existing and os.path.exists(out_path):
                print(f"\n{iso3.upper()} — exists, skipping (--skip-existing)")
                continue
            print(f"\n{iso3.upper()} — WPdx+ water points for {name} ...")
            df = fetch_all(sess, iso3, app_token=args.app_token,
                           page_size=args.page_size, max_pages=args.max_pages)
            if df.empty:
                print(f"  no records returned — nothing written")
                continue

            df = _ensure_lon_lat(df)
            dest = raw_dir(iso3, args.out_dir)
            os.makedirs(dest, exist_ok=True)
            out = os.path.join(dest, "wpdx_water.csv")
            df.to_csv(out, index=False, encoding="utf-8")
            print(f"  wrote {len(df):>6} water points -> {out}")

    print(f"\nDone. Next: {script_cmd('prep_infra.py')} --iso3 <code> --source wpdx")


if __name__ == "__main__":
    main()
