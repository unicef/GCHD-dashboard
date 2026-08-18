#!/usr/bin/env python
"""
run_pipeline.py — fetch, prep and (optionally) upload infrastructure data for a
set of countries in one command.

Runs, per country:
    fetch_mwater.py        schools / health facilities / water points  (no key)
    fetch_wpdx_water.py    water points                                (no key)
    fetch_giga_schools.py  schools                    (GIGA_SCHOOL_LOCATION_API_KEY)
    fetch_healthsites.py   health facilities          (HEALTHSITES_API_KEY)
then prep_infra.py to normalise everything to the standard contract, and
upload_infra.py to push the results to GEE.

Sources whose API key is missing from scripts/.env are SKIPPED with a warning
rather than failing the run, so a partial key set still produces a usable
result. Any source that errors is reported at the end; other countries continue.

Usage:
    # the default 10-country test set, fetch + prep only
    python scripts/run_pipeline.py --top 10

    # specific countries
    python scripts/run_pipeline.py --iso3 mwi --iso3 eth --iso3 mdg

    # all the way through to GEE (needs geeup set up)
    python scripts/run_pipeline.py --top 10 --upload --overwrite

    # see what would run
    python scripts/run_pipeline.py --top 10 --dry-run
"""
import argparse
import os
import subprocess
import sys

from _common import HERE, load_env, adm0_countries, country_name

# Countries with substantial mWater coverage, measured against the live API.
# A sensible default test set; override with --iso3.
DEFAULT_COUNTRIES = ["hti", "ind", "gha", "zmb", "ken",
                     "tza", "nga", "sle", "bgd", "ssd"]

# source key -> (script, env var required or None)
FETCHERS = [
    ("mwater",      "fetch_mwater.py",       None),
    ("wpdx",        "fetch_wpdx_water.py",   None),
    ("giga",        "fetch_giga_schools.py", "GIGA_SCHOOL_LOCATION_API_KEY"),
    ("healthsites", "fetch_healthsites.py",  "HEALTHSITES_API_KEY"),
]


def _short(script, args):
    """A readable one-line label — the full arg list is unusable at 195
    countries, so collapse the repeated --iso3 flags into a count."""
    isos, rest, skip = [], [], False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--iso3":
            isos.append(args[i + 1])
            skip = True
        elif a.startswith("--"):
            # Keep a flag together with its value ("--source giga").
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                rest.append(f"{a} {args[i + 1]}")
                skip = True
            else:
                rest.append(a)
    if len(isos) > 4:
        shown = f"--iso3 x{len(isos)} ({isos[0]}...{isos[-1]})"
    else:
        shown = " ".join(f"--iso3 {i}" for i in isos)
    return " ".join([script, shown] + rest).strip()


def run(script, args, dry_run=False):
    """Run one pipeline script; return (ok, label)."""
    cmd = [sys.executable, os.path.join(HERE, script)] + args
    label = _short(script, args)
    if dry_run:
        print(f"  [dry-run] {label}")
        return True, label
    print(f"\n>>> {label}")
    rc = subprocess.call(cmd)
    return rc == 0, label


def main():
    load_env()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso3", action="append", default=None,
                    help="country ISO3 code; repeat for several "
                         "(default: the built-in test set)")
    ap.add_argument("--top", type=int, default=None,
                    help="use the first N of the default country set")
    ap.add_argument("--all-countries", action="store_true",
                    help="every sovereign state in adm0_countries.json (195) — "
                         "pair with --skip-existing to resume a partial run")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip countries already downloaded (passed to the "
                         "fetchers), so a re-run only fetches what's missing")
    ap.add_argument("--source", action="append", default=None,
                    choices=[s for s, _, _ in FETCHERS],
                    help="only these sources (default: all available)")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="prep (and upload) whatever is already downloaded")
    ap.add_argument("--upload", action="store_true",
                    help="also upload to GEE via geeup")
    ap.add_argument("--overwrite", action="store_true",
                    help="passed to upload_infra.py: replace existing assets")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without running them")
    args = ap.parse_args()

    countries = adm0_countries()
    if args.all_countries:
        iso3s = sorted(c.lower() for c in countries)
    else:
        iso3s = [c.lower() for c in (args.iso3 or DEFAULT_COUNTRIES)]
    if args.top:
        iso3s = iso3s[:args.top]

    print(f"Countries ({len(iso3s)}):")
    if len(iso3s) <= 15:
        for i in iso3s:
            print(f"  {i.upper()}  {country_name(i, countries)}")
    else:
        print("  " + ", ".join(i.upper() for i in iso3s[:12])
              + f", ... (+{len(iso3s) - 12} more)")

    # Which sources can actually run?
    active, skipped = [], []
    for src, script, env_var in FETCHERS:
        if args.source and src not in args.source:
            continue
        if env_var and not os.environ.get(env_var, "").strip():
            skipped.append((src, env_var))
        else:
            active.append((src, script))

    if skipped:
        print("\nSkipping (no API key in scripts/.env):")
        for src, env_var in skipped:
            print(f"  {src:12s} needs {env_var}")

    if not active and not args.skip_fetch:
        sys.exit("\nERROR: no runnable sources. Add keys to scripts/.env "
                 "(see .env.example).")

    failures = []
    soft_failures = []

    if not args.skip_fetch:
        print(f"\nFetching from: {', '.join(s for s, _ in active)}")
        for src, script in active:
            # Every fetcher takes repeated --iso3 flags.
            flags = []
            for i in iso3s:
                flags += ["--iso3", i]
            if args.skip_existing:
                flags.append("--skip-existing")
            ok, label = run(script, flags, args.dry_run)
            if not ok:
                # One source failing (a rejected API key, an API outage) must
                # not discard the other sources' data — prep still runs and the
                # source is reported at the end.
                print(f"  !! {src} failed — continuing with the other sources")
                soft_failures.append(label)

    prep_flags = []
    for i in iso3s:
        prep_flags += ["--iso3", i]
    if args.source:
        for s in args.source:
            prep_flags += ["--source", s]
    ok, label = run("prep_infra.py", prep_flags, args.dry_run)
    if not ok:
        failures.append(label)

    if args.upload:
        up_flags = list(prep_flags)
        # prep takes --source, upload does not.
        if args.source:
            up_flags = []
            for i in iso3s:
                up_flags += ["--iso3", i]
        if args.overwrite:
            up_flags.append("--overwrite")
        elif args.skip_existing:
            # Same intent on the upload side: leave what's already in GEE alone.
            up_flags.append("--skip-existing")
        ok, label = run("upload_infra.py", up_flags, args.dry_run)
        if not ok:
            failures.append(label)

    print("\n" + "=" * 60)
    if soft_failures:
        print(f"{len(soft_failures)} source(s) failed — their data is missing, "
              "everything else was prepped:")
        for f in soft_failures:
            print(f"  {f}")
    if skipped:
        print(f"{len(skipped)} source(s) skipped for want of an API key: "
              + ", ".join(s for s, _ in skipped))
    if failures:
        print(f"\nFINISHED WITH {len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nPipeline finished." if not soft_failures
          else "\nPipeline finished (with the gaps noted above).")
    if not args.upload:
        hint = ("--all-countries" if len(iso3s) > 12
                else "--iso3 " + " --iso3 ".join(iso3s))
        if args.source:
            hint += "".join(f" --source {s}" for s in args.source)
        if args.skip_existing:
            hint += " --skip-existing"
        # Show the path as the user would type it from where they actually are.
        prefix = ("" if os.path.abspath(os.getcwd()) == os.path.abspath(HERE)
                  else "scripts/")
        print(f"Upload with: python {prefix}upload_infra.py {hint}")


if __name__ == "__main__":
    main()
