#!/usr/bin/env python
"""
build_brier_skill.py — does the TERCILE PROBABILITY forecast actually verify?

build_skill_mask.py correlates the ensemble MEAN season total against CHIRPS.
That grades the wrong output: the tab's exposure count reads p_below / p_above,
not the mean, and a model can rank years well while being systematically
overconfident in its probabilities. Correlation cannot see that — it never
looks at a probability.

This scores the probabilities themselves, and the difference is not academic.
Measured over the OND window:

    region                  r      BSS
    Maritime continent   +0.58   +0.35    genuinely skillful
    Horn of Africa       +0.47   +0.07    barely beats guessing
    southern Africa      +0.28   +0.04
    Europe               -0.00   -0.05    worse than guessing

The Horn looks respectable on correlation and adds almost nothing
probabilistically. That is the gap this fills.

How the Brier score is computed, per grid cell:

  1. For each hindcast year, each of the 25 members gets a season total
     (sum over its lead bands).

  2. LEAVE-ONE-OUT tercile thresholds. To score year T, the 33rd/67th
     percentiles come from the pooled members of the OTHER 23 years
     (23 x 25 = 575 values). Including T in the thresholds it is judged
     against lets the model see the answer and inflates the score — this is
     the single step that makes the number honest, and the reason this script
     is expensive.

  3. Forecast probability for year T = fraction of T's members below (or
     above) those thresholds. Exactly what the tab thresholds at 70%.

  4. Observed outcome = 1 if CHIRPS for year T fell in the matching third of
     ITS OWN 24-year distribution, else 0. Each side is judged against its own
     normal, so a model that runs systematically wet is not penalised for bias
     — only for getting the DIRECTION wrong.

  5. Brier = mean over years of (probability - outcome)^2. Lower is better;
     confident-and-wrong is penalised hardest, which is precisely the failure
     mode that matters when a 70% threshold decides who is counted.

  6. Brier Skill Score = 1 - Brier / Brier_climatology, where climatology is
     always forecasting 1/3. BSS > 0 beats guessing, BSS <= 0 does not.

Outputs one 4-band image per period into el_nino/brier:
    bss_dry, bss_wet     Brier skill score, dry and wet terciles
    brier_dry, brier_wet the raw Brier scores behind them

Requires the staged CHIRPS observations from build_skill_mask.py --stage-obs.

Requirements:  pip install earthengine-api
Usage:
    python scripts/el_nino/build_brier_skill.py --dry-run
    python scripts/el_nino/build_brier_skill.py --period window_L2_L4 --wait
    python scripts/el_nino/build_brier_skill.py --wait
"""
import argparse
import json
import sys
import time
from pathlib import Path

import ee

HERE = Path(__file__).resolve().parent
APP = HERE.parent.parent / "app"
sys.path.insert(0, str(APP))

from elnino_config import (            # noqa: E402
    ELNINO_ROOT, RAW_HINDCAST_COLLECTION, PERIOD_MAP, WINDOW_PERIOD,
    INIT_TAG, HINDCAST, SYSTEM, BRIER_COLLECTION, BRIER_BANDS,
)

OBS_COLLECTION = f"{ELNINO_ROOT}/chirps_obs"
HC_YEARS = list(range(1993, 2017))
INIT_MONTH = int(INIT_TAG[4:])

# The two terciles scored, as (name, comparison). "dry" is the lower third.
TERCILES = [("dry", 33), ("wet", 67)]


def init_ee():
    key = APP / "credentials" / "service_account.json"
    if key.exists():
        with open(key) as fh:
            info = json.load(fh)
        ee.Initialize(
            credentials=ee.ServiceAccountCredentials(
                email=info["client_email"], key_file=str(key)),
            project="unicef-ccri")
    else:
        ee.Initialize(project="unicef-ccri")


def band_names_of(asset_id):
    prop = ee.Image(asset_id).get("band_names").getInfo()
    if not prop:
        raise SystemExit(f"ERROR: {asset_id} has no band_names property.")
    return [b.strip() for b in str(prop).split(",") if b.strip()]


def hindcast_structure():
    """({lead: [bands]}, ordered leads) derived from the band names."""
    names = band_names_of(
        f"{RAW_HINDCAST_COLLECTION}/hc_{HC_YEARS[0]}{INIT_MONTH:02d}")
    by_lead = {}
    for nm in names:
        by_lead.setdefault(nm.split("_")[0], []).append(nm)
    keys = sorted(by_lead, key=lambda k: int(k[1:]))
    if not keys:
        raise SystemExit(f"ERROR: unrecognised hindcast bands: {names[:4]}")
    return names, by_lead, keys


def leads_for_period(period, lead_keys):
    if period == WINDOW_PERIOD:
        return list(lead_keys)
    key = f"L{PERIOD_MAP[period]['lead']}"
    if key not in lead_keys:
        raise SystemExit(f"ERROR: period '{period}' wants {key}, hindcast has "
                         f"{lead_keys}.")
    return [key]


def member_totals(year, names, by_lead, leads):
    """One image per ensemble member: that member's season total, mm.

    Per MEMBER, not the ensemble mean — the whole point is to count members
    against a threshold, which is what produces a probability.
    """
    img = ee.Image(
        f"{RAW_HINDCAST_COLLECTION}/hc_{year}{INIT_MONTH:02d}").rename(names)
    n_members = len(by_lead[leads[0]])
    out = []
    for m in range(n_members):
        bands = [f"{k}_m{m:02d}" for k in leads]
        # Sum across leads = this member's season total. Each band is a
        # MONTHLY total, so summing is right and averaging would not be.
        out.append(img.select(bands).reduce(ee.Reducer.sum()).rename("v"))
    return out


def observed(period, year):
    return ee.Image(f"{OBS_COLLECTION}/obs_{period}_{year}").rename("o")


def brier_image(period, names, by_lead, lead_keys):
    """4-band image: bss_dry, bss_wet, brier_dry, brier_wet."""
    leads = leads_for_period(period, lead_keys)
    members = {y: member_totals(y, names, by_lead, leads) for y in HC_YEARS}
    obs = {y: observed(period, y) for y in HC_YEARS}

    # Observed thresholds from CHIRPS's OWN distribution — each side judged
    # against its own normal, so model bias is not penalised, only direction.
    obs_pct = ee.ImageCollection(list(obs.values())).reduce(
        ee.Reducer.percentile([p for _, p in TERCILES]))

    bands = []
    for name, pct in TERCILES:
        o_thr = obs_pct.select(f"o_p{pct}")
        fc_terms, clim_terms = [], []
        for target in HC_YEARS:
            # Leave-one-out: thresholds from the other 23 years only.
            pool = [im for y in HC_YEARS if y != target for im in members[y]]
            f_thr = ee.ImageCollection(pool).reduce(
                ee.Reducer.percentile([pct])).select(f"v_p{pct}")

            if name == "dry":
                hits = [m.lt(f_thr) for m in members[target]]
                outcome = obs[target].lt(o_thr)
            else:
                hits = [m.gt(f_thr) for m in members[target]]
                outcome = obs[target].gt(o_thr)

            prob = ee.ImageCollection(hits).mean()      # fraction of members
            fc_terms.append(prob.subtract(outcome).pow(2).rename("b"))
            # Reference forecast: always 1/3, i.e. no information.
            clim_terms.append(
                ee.Image(1.0 / 3).subtract(outcome).pow(2).rename("b"))

        brier = ee.ImageCollection(fc_terms).mean()
        clim = ee.ImageCollection(clim_terms).mean()
        bss = ee.Image(1).subtract(brier.divide(clim))
        bands += [bss.rename(f"bss_{name}"), brier.rename(f"brier_{name}")]

    return ee.Image.cat(bands).select(BRIER_BANDS).toFloat()


def ensure_collection(asset_id):
    try:
        ee.data.getAsset(asset_id)
    except ee.EEException:
        ee.data.createAsset({"type": "IMAGE_COLLECTION"}, asset_id)
        print(f"  created image collection {asset_id}")


def export_one(period, img, overwrite):
    asset_id = f"{BRIER_COLLECTION}/brier_{INIT_TAG}_{period}"
    if overwrite:
        try:
            ee.data.deleteAsset(asset_id)
            print(f"  deleted existing {asset_id}")
        except ee.EEException:
            pass
    props = {
        "kind": "brier", "period": period,
        "lead": PERIOD_MAP[period]["lead"],
        "reference": "CHIRPS", "hindcast": HINDCAST,
        "metric": "brier_skill_score_leave_one_out",
        "system": SYSTEM, "init": INIT_TAG,
        "band_names": ",".join(BRIER_BANDS),
    }
    task = ee.batch.Export.image.toAsset(
        image=img.set(props),
        description=f"brier_{INIT_TAG}_{period}"[:100],
        assetId=asset_id,
        region=ee.Geometry.Rectangle([-180, -60, 180, 60], None, False),
        # crsTransform, NOT scale — see the note in build_skill_mask.py:
        # `scale` produces an origin of -180.9992 that runs past the
        # antimeridian and breaks later lon/lat-pinned reductions.
        crs="EPSG:4326", crsTransform=[1, 0, -180, 0, -1, 90],
        maxPixels=1e9,
    )
    task.start()
    print(f"  started {asset_id}  (task {task.id})")
    return task, f"brier_{period}"


def wait_for(tasks, poll=20, timeout=7200):
    print("\nWaiting for exports ...")
    pending = {t.id: name for t, name in tasks}
    waited, failures = 0, []
    while pending and waited < timeout:
        time.sleep(poll)
        waited += poll
        for tid in list(pending):
            st = ee.data.getTaskStatus([tid])[0]
            state = st.get("state")
            if state in ("COMPLETED", "FAILED", "CANCELLED"):
                msg = st.get("error_message", "")
                ok = state == "COMPLETED"
                print(f"  {'OK  ' if ok else 'FAIL'} {pending[tid]}  {state}"
                      + (f"  {msg[:160]}" if msg else ""))
                if not ok:
                    failures.append((pending[tid], state, msg))
                pending.pop(tid)
        if pending:
            print(f"  ... {len(pending)} still running ({waited}s)")
    if pending:
        print(f"\n  TIMED OUT with {len(pending)} running: "
              f"{list(pending.values())}")
    if failures:
        print(f"\n  {len(failures)} export(s) FAILED:")
        for name, state, msg in failures:
            print(f"    {name}: {state} {msg[:200]}")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", action="append", choices=list(PERIOD_MAP),
                    default=None, help="only these periods (default: all)")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    periods = args.period or [p["period"] for p in
                              sorted(PERIOD_MAP.values(),
                                     key=lambda p: p["lead"])]

    print(f"Brier skill — SEAS5 {INIT_TAG} tercile probabilities vs CHIRPS, "
          f"hindcast {HINDCAST}")
    print(f"  periods: {periods}")
    print("  leave-one-out thresholds: 23 years x 25 members per target year")
    init_ee()

    names, by_lead, lead_keys = hindcast_structure()
    print(f"  hindcast leads: {lead_keys}")

    # The staged observations are a hard prerequisite: without them this would
    # regrid 5 km CHIRPS inside a 24-year loop and exhaust memory.
    try:
        staged = {a["id"].split("/")[-1]
                  for a in ee.data.listAssets(
                      {"parent": OBS_COLLECTION}).get("assets", [])}
    except ee.EEException:
        staged = set()
    for period in periods:
        missing = [y for y in HC_YEARS
                   if f"obs_{period}_{y}" not in staged]
        if missing:
            sys.exit(f"ERROR: {len(missing)} staged observation(s) missing for "
                     f"{period} (e.g. obs_{period}_{missing[0]}).\n"
                     f"       Run: python scripts/el_nino/build_skill_mask.py "
                     f"--period {period} --stage-obs --wait")

    if not args.dry_run:
        ensure_collection(BRIER_COLLECTION)

    tasks = []
    for period in periods:
        img = brier_image(period, names, by_lead, lead_keys)
        print(f"\n{period}: leads {leads_for_period(period, lead_keys)}")
        if args.dry_run:
            box = ee.Geometry.Rectangle([30, -5, 45, 15], None, False)
            s = img.reduceRegion(ee.Reducer.mean(), box, 111319,
                                 bestEffort=True).getInfo()
            print("  dry-run OK — Horn of Africa means:")
            for b in BRIER_BANDS:
                v = s.get(b)
                print(f"    {b:12s} {v:+.4f}" if v is not None
                      else f"    {b:12s} n/a")
            continue
        tasks.append(export_one(period, img, args.overwrite))

    if args.dry_run:
        print("\n--dry-run: nothing exported.")
        return
    if args.wait:
        wait_for(tasks)
    else:
        print("\nExports started. Track them with: earthengine task list")


if __name__ == "__main__":
    main()
