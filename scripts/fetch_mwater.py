#!/usr/bin/env python
"""
fetch_mwater.py — download mWater sites (schools / health facilities / water
points) for a country straight from the mWater REST API.

No portal setup. Earlier versions of this script needed a hand-made Integrator
"Sites" view per country x layer (created in portal.mwater.co, code pasted into
scripts/mwater_sources.json) — roughly 3 portal visits per country, which does
not scale past a handful of countries. This version queries the public v3 REST
API instead, so ANY country works with just its ISO3 code.

How country scoping works
-------------------------
Schools, health facilities and water points are separate entity types:

    GET https://api.mwater.co/v3/entity_types      -> 42 types, incl.
        school | health_facility | water_point

Each entity carries `admin_region`, but that is a *leaf* region id (a village /
fokontany), not the country. Admin regions form a tree and every region records
its country as `level0`, so scoping to a country is two steps:

    1. GET /v3/admin_regions?filter={"level":0}          -> 255 countries,
       stable integer ids (Ethiopia=73, Madagascar=133, Malawi=134)
    2. GET /v3/admin_regions?filter={"level0":<id>}      -> every region in it
    3. GET /v3/entities/{type}?filter={"admin_region":{"$in":[...ids]}}

Only real entity fields are filterable — `country`, `level0`, `ancestry` and
`admin_region.level0` all return HTTP 500.

Auth: the public entities are readable anonymously; no token is required.

Output (matches what prep_infra.py reads):
    app/data/infra/{iso3}/raw/mwater_schools.csv
    app/data/infra/{iso3}/raw/mwater_health.csv
    app/data/infra/{iso3}/raw/mwater_water.csv

Usage:
    python scripts/fetch_mwater.py --iso3 mwi
    python scripts/fetch_mwater.py --iso3 mwi --iso3 eth --iso3 mdg
    python scripts/fetch_mwater.py --iso3 uga --layers schools,health
"""
import argparse
import json
import os
import sys

import pandas as pd
import requests

from _common import (load_env, country_name, get_with_retries, raw_dir,
                     script_cmd)

API_ROOT = "https://api.mwater.co/v3"

# Output stem -> mWater entity type.
LAYERS = {
    "schools": "school",
    "health":  "health_facility",
    "water":   "water_point",
}

# One request returns a whole country's layer comfortably (the largest tested
# was ~80k water points), so this is only an upper bound.
PAGE_LIMIT = 200000

# The country filter lists every admin-region id inline in the URL query string.
# Big countries have a LOT of regions (Madagascar 19,887; Uganda 9,093), and the
# whole list in one URL is rejected: HTTP 414 Request-URI Too Large. POST would
# avoid it but needs an authenticated client token (/v3/entities POST answers
# 401 "Client required"), so the ids are sent in batches and merged instead.
#
# Measured against the live API with Madagascar's region ids:
#     4000 ids ~= 40 kB URL -> HTTP 200
#     5000 ids ~= 50 kB URL -> HTTP 400
# 2000 ids is ~20 kB — about half the largest size that works, leaving room for
# longer filters without landing near the cliff.
REGION_BATCH = 2000

_HERE = os.path.dirname(os.path.abspath(__file__))
_REGION_CACHE = os.path.join(_HERE, "mwater_countries.json")

# ISO3 is the project-wide key: it names the GEE assets and comes from the adm0
# boundary asset. mWater has no ISO3 field — its admin regions are keyed by name
# only — so ISO3 is bridged to mWater's own level-0 spelling here.
#
# The mapping is ISO3 -> mWater name, generated against a live
# /v3/admin_regions?filter={"level":0} pull (255 countries) and restricted to
# the 195 sovereign states in scripts/state_countries.json. 184 of those match
# a standard country name outright; only the entries below differ, and each was
# confirmed against mWater's actual spelling.
#
# Deliberately explicit, never fuzzy: fuzzy matching mis-assigned
# Saint-Barthélemy to FRA and Kosovo to SRB, and "Congo" is a substring of both
# Congos — a wrong match silently yields another country's data.
ISO3_MWATER_NAMES = {
    "bol": "bolivia",
    "brn": "brunei",
    "cod": "democratic republic of the congo",
    "cog": "republic of congo",
    "cpv": "cape verde",
    "cze": "czech republic",
    "fsm": "micronesia",
    "gbr": "united kingdom",
    "irn": "iran",
    "kor": "south korea",
    "lao": "laos",
    "mkd": "macedonia",
    "prk": "north korea",
    "pse": "palestina",
    "rus": "russia",
    "stp": "são tomé and príncipe",
    "syr": "syria",
    "tur": "turkey",
    "tza": "tanzania",
    "usa": "united states",
    "vat": "vatican city",
    "vnm": "vietnam",
}


def _get_with_retries(sess, url, params, max_retries=3, timeout=600):
    """mWater GET — the shared retry policy with mWater's longer timeout."""
    return get_with_retries(sess, url, params=params, max_retries=max_retries,
                            timeout=timeout)


def _load_countries(sess, refresh=False):
    """mWater's 255 level-0 (country) admin regions as {name_lower: id}.

    Cached to a local JSON since the list is effectively static."""
    if not refresh and os.path.exists(_REGION_CACHE):
        with open(_REGION_CACHE, encoding="utf-8") as fh:
            return json.load(fh)

    resp = _get_with_retries(sess, f"{API_ROOT}/admin_regions",
                             {"filter": json.dumps({"level": 0}), "limit": 1000})
    countries = {r["name"].lower(): r["_id"] for r in resp.json() if r.get("name")}
    with open(_REGION_CACHE, "w", encoding="utf-8") as fh:
        json.dump(countries, fh, indent=1, sort_keys=True)
    return countries


def _resolve_level0(iso3, countries):
    """ISO3 -> (mWater country name, level-0 region id).

    ISO3 comes from adm0; the alias table bridges to mWater's spelling where the
    two differ. No fuzzy matching — an unknown country is an actionable error
    telling you which line to add, never a silent guess at the wrong country."""
    iso3 = iso3.lower()
    adm0_name = country_name(iso3)
    mw_name = ISO3_MWATER_NAMES.get(iso3, adm0_name)

    level0 = countries.get(mw_name.lower())
    if level0 is None:
        sys.exit(
            f"ERROR: {iso3.upper()} ({adm0_name!r}) has no matching mWater "
            f"level-0 region (tried {mw_name!r}).\n"
            f"       Find mWater's spelling in {_REGION_CACHE} and add it to "
            f"ISO3_MWATER_NAMES in {os.path.basename(__file__)}:\n"
            f'           "{iso3}": "<mWater name>",')
    return mw_name, level0


def _country_region_ids(sess, iso3, countries):
    """Every admin-region id belonging to a country (the entity filter values)."""
    name, level0 = _resolve_level0(iso3, countries)

    resp = _get_with_retries(sess, f"{API_ROOT}/admin_regions",
                             {"filter": json.dumps({"level0": level0}),
                              "limit": PAGE_LIMIT})
    ids = [r["_id"] for r in resp.json()]
    print(f"  {name} (level0={level0}): {len(ids)} admin regions")
    return ids


def _fetch_region_batch(sess, entity, batch):
    """Entities of one type whose admin_region falls in one batch of ids.

    NOTE: mWater's API silently IGNORES the `offset` parameter — every request
    returns the same first page regardless of it. A limit/offset loop therefore
    never terminates and yields massively duplicated records (a naive version of
    this returned "410,000" Malawi schools, all repeats). Do not reintroduce it.
    One large page is requested and, only if it comes back completely full, we
    fall back to keyset pagination on the sortable `_id` field.
    """
    base = {"admin_region": {"$in": batch}}
    resp = _get_with_retries(sess, f"{API_ROOT}/entities/{entity}",
                             {"filter": json.dumps(base), "limit": PAGE_LIMIT})
    records = resp.json()

    if len(records) >= PAGE_LIMIT:
        seen = {r["_id"] for r in records}
        last = max(seen)
        while True:
            flt = dict(base, _id={"$gt": last})
            resp = _get_with_retries(
                sess, f"{API_ROOT}/entities/{entity}",
                {"filter": json.dumps(flt), "limit": PAGE_LIMIT,
                 "sort": json.dumps(["_id"])})
            page = resp.json()
            fresh = [r for r in page if r["_id"] not in seen]
            if not fresh:
                break
            records.extend(fresh)
            seen.update(r["_id"] for r in fresh)
            last = max(r["_id"] for r in fresh)
            if len(page) < PAGE_LIMIT:
                break

    return records


def fetch_entities(sess, entity, region_ids):
    """All entities of one type inside a country, de-duplicated by _id.

    Region ids are sent in batches (see REGION_BATCH) so the query URL stays
    under the server's length limit; results are merged. A facility belongs to
    exactly one admin region and the batches partition the id list, so no record
    can be split across batches — but merging by _id keeps it correct even if
    mWater were to return overlaps.
    """
    unique = {}
    batches = [region_ids[i:i + REGION_BATCH]
               for i in range(0, len(region_ids), REGION_BATCH)]
    for n, batch in enumerate(batches, 1):
        for rec in _fetch_region_batch(sess, entity, batch):
            unique[rec["_id"]] = rec
        if len(batches) > 1:
            print(f"    batch {n}/{len(batches)}: {len(unique)} unique so far")
    return list(unique.values())


def _entities_to_df(records):
    """Flatten mWater entities to the columns prep_infra.py expects.

    `code` is mWater's human-facing site id and is what the Integrator export
    used, so it stays the primary id; `_id` is the UUID fallback.
    """
    rows = []
    for e in records:
        coords = (e.get("location") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue                      # unmapped site — no geometry to plot
        rows.append({
            "facility_id": e.get("code") or e.get("_id"),
            "name":        (e.get("name") or "").strip(),
            "subtype":     (e.get("type") or "").strip(),
            "longitude":   coords[0],
            "latitude":    coords[1],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
    bad = df["longitude"].isna() | df["latitude"].isna()
    if bad.any():
        print(f"    dropping {int(bad.sum())} records without valid coordinates")
        df = df[~bad].copy()
    return df


def fetch_country(sess, iso3, countries, layers, out_dir=None, skip_existing=False):
    """Fetch every requested layer for one country into its raw folder."""
    out_dir = raw_dir(iso3, out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{iso3.upper()}")
    region_ids = _country_region_ids(sess, iso3, countries)
    if not region_ids:
        print(f"  no admin regions — skipping {iso3.upper()}")
        return

    for layer in layers:
        entity = LAYERS[layer]
        out = os.path.join(out_dir, f"mwater_{layer}.csv")
        if skip_existing and os.path.exists(out):
            print(f"  {layer}: exists — skipping (--skip-existing)")
            continue

        print(f"  {layer} ({entity}) ...")
        records = fetch_entities(sess, entity, region_ids)
        df = _entities_to_df(records)
        if df.empty:
            print(f"    0 usable records — nothing written")
            continue
        df.to_csv(out, index=False, encoding="utf-8")
        named = (df["name"].str.strip() != "").sum()
        print(f"    wrote {len(df):>6} rows ({named} named) -> {out}")


def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", required=True,
                    help="country ISO3 code; repeat for several (e.g. "
                         "--iso3 mwi --iso3 eth)")
    ap.add_argument("--layers", default=",".join(LAYERS),
                    help=f"comma-separated subset of {','.join(LAYERS)} "
                         "(default: all)")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default app/data/infra/{iso3}/raw); "
                         "only valid with a single --iso3")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip layers whose mwater_*.csv already exists")
    ap.add_argument("--refresh-countries", action="store_true",
                    help="re-download the cached mWater country region list")
    args = ap.parse_args()

    layers = [l.strip() for l in args.layers.split(",") if l.strip()]
    unknown = [l for l in layers if l not in LAYERS]
    if unknown:
        sys.exit(f"ERROR: unknown layer(s) {unknown}. Choose from {list(LAYERS)}.")

    iso3s = [c.lower() for c in args.iso3]
    if args.out_dir and len(iso3s) > 1:
        sys.exit("ERROR: --out-dir only works with a single --iso3.")

    with requests.Session() as sess:
        countries = _load_countries(sess, refresh=args.refresh_countries)
        for iso3 in iso3s:
            fetch_country(sess, iso3, countries, layers,
                          out_dir=args.out_dir, skip_existing=args.skip_existing)

    print(f"\nDone. Next: {script_cmd('prep_infra.py')} --iso3 <code> "
          "--source mwater")


if __name__ == "__main__":
    main()
