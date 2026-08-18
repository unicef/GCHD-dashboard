#!/usr/bin/env python
"""
_common.py — shared helpers for the infrastructure fetch/prep/upload scripts.

Three things every script needs and should agree on:

  load_env()          read scripts/.env so API keys live in a file, not in
                      shell environment variables
  adm0_countries()    the project's canonical {ISO3: country name}, produced by
                      sync_adm0.py from the adm0 boundary asset
  get_with_retries()  one HTTP retry/backoff policy for every source API

Not a CLI — import it.
"""
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
ADM0_PATH = os.path.join(HERE, "adm0_countries.json")

# ---------------------------------------------------------------------------
# On-disk layout — everything for one country lives under one folder:
#
#   app/data/infra/{iso3}/raw/{source}_{layer}.csv    exactly as fetched
#   app/data/infra/{iso3}/out/{iso3}_{source}_{layer}.csv   normalised, upload-ready
#                                                    (+ .geojson for inspection)
#
# The out/ filenames repeat the ISO3 because geeup derives each GEE asset id
# from the CSV's filename stem — the stem IS the asset name, so it must be
# globally unique, not just unique within its folder.
# ---------------------------------------------------------------------------
DATA_ROOT = os.path.join(HERE, "..", "app", "data", "infra")

# Standard layer names used in file stems and GEE asset ids.
LAYER_SCHOOLS = "schools"
LAYER_HEALTH  = "health_facilities"
LAYER_WATER   = "water_points"


def load_env(path=ENV_PATH):
    """Load KEY=VALUE lines from scripts/.env into os.environ.

    Deliberately tiny — no python-dotenv dependency. Existing environment
    variables win, so a shell export can still override the file. Blank lines
    and #-comments are skipped; surrounding quotes are stripped; a leading
    "export " is tolerated so the file can be sourced by a shell too.
    """
    if not os.path.exists(path):
        return {}

    loaded = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            loaded[key] = val
            os.environ.setdefault(key, val)   # shell env wins
    return loaded


def require_key(env_var, source_name, how):
    """Return an API key or exit with an actionable message."""
    val = os.environ.get(env_var)
    if not val:
        sys.exit(
            f"ERROR: no {source_name} API key.\n"
            f"       Add this line to {ENV_PATH}:\n"
            f"           {env_var}=<your key>\n"
            f"       {how}")
    return val


def adm0_countries():
    """{ISO3: canonical country name} from the adm0 boundary asset.

    adm0 is the project's single naming authority — the same asset drives the
    app's country dropdowns — so every script resolves names through this rather
    than an external country library whose spellings could drift.
    """
    if not os.path.exists(ADM0_PATH):
        sys.exit(f"ERROR: {ADM0_PATH} is missing.\n"
                 "       Run: python scripts/sync_adm0.py")
    with open(ADM0_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def country_name(iso3, countries=None):
    """ISO3 -> adm0 country name, or exit if the code isn't a known state."""
    countries = countries if countries is not None else adm0_countries()
    name = countries.get(iso3.upper())
    if not name:
        sys.exit(f"ERROR: '{iso3.upper()}' is not in {os.path.basename(ADM0_PATH)}. "
                 "It may be a territory rather than a sovereign state, or "
                 "sync_adm0.py may need re-running.")
    return name


def get_with_retries(sess, url, params=None, headers=None, max_retries=3,
                     timeout=300, fatal_statuses=None):
    """GET with exponential backoff on transient errors.

    `fatal_statuses`: {status: message} for codes worth failing fast on (401 on
    a bad API key, say) instead of burning retries on a permanent error.
    """
    fatal = fatal_statuses or {}
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            wait = 2 ** attempt
            print(f"    request error ({e}); retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code in fatal:
            sys.exit(f"ERROR: HTTP {resp.status_code} — {fatal[resp.status_code]}")

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                resp.raise_for_status()
            wait = 2 ** attempt
            print(f"    HTTP {resp.status_code}; retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp

    raise RuntimeError("exhausted retries")


def script_cmd(script):
    """`python <script>` as the user would type it from their current directory.

    The scripts are run both from the repo root and from scripts/ itself, so a
    hardcoded "scripts/..." in a next-step hint is wrong half the time.
    """
    prefix = ("" if os.path.abspath(os.getcwd()) == os.path.abspath(HERE)
              else "scripts/")
    return f"python {prefix}{script}"


def raw_dir(iso3, override=None):
    """Per-country folder the fetchers write into, as fetched."""
    return override or os.path.join(DATA_ROOT, iso3.lower(), "raw")


def out_dir(iso3, override=None):
    """Per-country folder for normalised, upload-ready files."""
    return override or os.path.join(DATA_ROOT, iso3.lower(), "out")


def asset_stem(iso3, source, layer):
    """The one place the {iso3}_{source}_{layer} rule is defined.

    This string is the prepped CSV's filename, the GEE asset id, and what the
    app's discovery regex parses back into a country and layer — so every
    producer and consumer must derive it here rather than formatting its own.
    """
    return f"{iso3.lower()}_{source}_{layer}"


def iter_countries(iso3s):
    """Yield (iso3_lower, adm0_name), validating each code once up front."""
    countries = adm0_countries()
    for iso3 in iso3s:
        code = iso3.lower()
        yield code, country_name(code, countries)
