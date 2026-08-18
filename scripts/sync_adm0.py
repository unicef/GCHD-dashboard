"""
sync_adm0.py — export the project's canonical ISO3 -> country-name table from
the adm0 boundary asset to scripts/adm0_countries.json.

    projects/unicef-ccri/assets/global_boundary/adm0

adm0 is the single naming authority for the whole project: it supplies the ISO3
codes that name the GEE infrastructure assets and the display names the app
shows in its country dropdowns. The fetch scripts read the JSON this writes so
they never need an external country library whose spellings could drift from the
app's.

Picking one name per ISO3
-------------------------
adm0 has more features (289) than ISO3 codes (262) because some states are split
into their mainland plus dependent territories, each its own feature:

    AUS  'Australia'                   ucode=AUS_V1     <- the country
    AUS  'Ashmore and Cartier Islands' ucode=AUS1_V1    <- a territory of it

The mainland feature's ucode is '{ISO3}_{version}' with no digit between the
code and the version; territories carry one (AUS1_V1). Versions differ per
country (_V1 or _V2), so the rule matches the shape, not a fixed suffix.

Scope
-----
Restricted to the 195 sovereign states in scripts/state_countries.json. adm0
also carries dependent territories and disputed zones (Aksai Chin, Spratly
Islands, 'Sovereignty unsettled', …) that will never have infrastructure data
and have no mWater counterpart; including them would only produce noise. Pass
--all to export every ISO3 in the asset instead.

Usage:
    python scripts/sync_adm0.py
    python scripts/sync_adm0.py --all
    python scripts/sync_adm0.py --out some/other/path.json
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

import ee

ADM0_ASSET = "projects/unicef-ccri/assets/global_boundary/adm0"
_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_LIST = os.path.join(_HERE, "state_countries.json")

# 'AUS_V1' -> the country itself; 'AUS1_V1' -> one of its territories.
_MAIN_UCODE = re.compile(r"^[A-Za-z]{3}_V\d+$")


def _load_state_iso3():
    """The sovereign-state ISO3 codes to keep, from state_countries.json.

    The file holds ucodes ('MWI_V1', 'ATG_V2') whose version differs per
    country, so only the ISO3 prefix is used."""
    if not os.path.exists(STATE_LIST):
        sys.exit(f"ERROR: {STATE_LIST} is missing. Pass --all to skip filtering.")
    with open(STATE_LIST, encoding="utf-8") as fh:
        return {u.split("_")[0].upper() for u in json.load(fh)}


def initialize_gee():
    """Authenticate with the same service account the app uses."""
    here = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(here, "..", "app", "credentials", "service_account.json")
    if not os.path.exists(key_path):
        sys.exit(f"ERROR: no service-account key at {key_path}")
    with open(key_path) as fh:
        info = json.load(fh)
    ee.Initialize(
        credentials=ee.ServiceAccountCredentials(email=info["client_email"],
                                                 key_file=key_path),
        project="unicef-ccri")


def fetch_adm0_names(keep=None):
    """{ISO3: canonical country name} from the adm0 asset.

    `keep`: optional set of ISO3 codes to restrict the output to."""
    rows = (ee.FeatureCollection(ADM0_ASSET)
            .reduceColumns(ee.Reducer.toList(3), ["ISO3", "name", "ucode"])
            .get("list").getInfo())

    by_iso = defaultdict(list)
    for iso3, name, ucode in rows:
        if iso3 and (keep is None or iso3.upper() in keep):
            by_iso[iso3].append((name, ucode))

    canonical, territory_only = {}, {}
    for iso3, feats in by_iso.items():
        main = [n for n, uc in feats if _MAIN_UCODE.match(uc or "")]
        if len(main) == 1:
            canonical[iso3] = main[0]
        elif len(feats) == 1:
            canonical[iso3] = feats[0][0]
        else:
            territory_only[iso3] = [n for n, _ in feats]

    missing = sorted(keep - set(by_iso)) if keep else []
    return canonical, territory_only, missing, len(rows)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(here, "adm0_countries.json"),
                    help="output JSON path (default scripts/adm0_countries.json)")
    ap.add_argument("--all", action="store_true",
                    help="export every ISO3 in adm0, not just the sovereign "
                         "states in state_countries.json")
    args = ap.parse_args()

    keep = None if args.all else _load_state_iso3()

    initialize_gee()
    print(f"Reading {ADM0_ASSET} …")
    canonical, territory_only, missing, n_feats = fetch_adm0_names(keep)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(canonical, fh, indent=1, sort_keys=True, ensure_ascii=False)

    scope = "all ISO3 codes" if args.all else f"{len(keep)} sovereign states"
    print(f"  {n_feats} features, filtered to {scope} -> {len(canonical)} entries")
    if territory_only:
        print(f"  skipped {len(territory_only)} ISO3 codes with no single main "
              "feature:")
        for iso3, names in sorted(territory_only.items()):
            shown = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
            print(f"    {iso3}: {shown}")
    if missing:
        print(f"  WARNING: {len(missing)} requested states absent from adm0: "
              f"{', '.join(missing)}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
