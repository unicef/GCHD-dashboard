# El Niño seasonal outlook — child exposure by country

## Goal
Estimate, per country (land only), how many children live in areas with a forecast
**dry** or **wet** precipitation signal for **Oct–Dec 2026 (OND)**, using the ECMWF SEAS5
seasonal forecast.

Precipitation and **2 m temperature**, plus a member-wise **compound** signal
(hot-dry / hot-wet / cool-wet). Still no flood or drought model: a compound signal is
co-occurring tercile extremes in the forecast ensemble, not a hydrological prediction.
"Wet signal" = elevated flood risk proxy, not flooding. "Dry signal" = drought risk proxy.
"Hot-dry" = compounding heat and water stress; "hot-wet" = the warm, wet window that
governs vector-borne and waterborne disease.

Temperature is **reported alongside** precipitation, not in place of it. No existing
published figure changes — the 122M dry / 215M wet OND headline numbers stand.

Reference product from the team: ECMWF chart `seasonal_system5_standard_rain`, stats `tsum`
(tercile summary), base time 2026-09-01. Outputs should broadly match that chart.

## Data (all from Copernicus CDS, dataset `seasonal-monthly-single-levels`)
Temperature is the **same dataset and the same request shape** — only `variable` changes —
so it is a one-variable addition, not a second pipeline. The dataset also carries
`maximum_2m_temperature_in_the_last_24_hours` and `evaporation` if they are ever needed.
- variable `2m_temperature`, GRIB band `t2m`, **Kelvin** → converted to degC.
- originating_centre `ecmwf`, system `51` (SEAS5), variable `total_precipitation`,
  product_type `monthly_mean` (all ensemble members), init month `09`, leadtime_month **2–4**
  (Oct, Nov, Dec). Lead is months-after-init, so October is always lead 2 — dropping September
  does NOT renumber the rest. Lead 1 (September) is excluded: it is the init month, partly fixed
  by conditions already present at launch, so it behaves unlike a forecast month.
- Forecast: year 2026, 51 members.
- Hindcast: years 1993–2016 (same request, only years change), 25 members/year.
- Grid 1°, global (181 × 360). Variable `tprate` = mean rate in m/s.
- Do NOT use `ensemble_mean` (no spread → no probabilities) or monthly max/min/std.

## Method (decided)
1. Convert `tprate` → mm/month: × 86400 × 1000 × days in the valid month.
2. Longitudes 0–360 → −180–180.
3. Per grid cell and period, pool hindcast (24 yrs × 25 members = 600 values) →
   33rd / 67th percentiles = tercile thresholds (model's own climatology, removes bias).
4. Forecast probabilities = fraction of the 51 members below / above those thresholds.
5. `cat`: 1 = dry (p_below ≥ 0.5 and > p_above), 3 = wet (mirror), 0 = no clear signal.
6. `dry_mask` band = 0 where hindcast mean < 10 mm/month (single month) or < 50 mm (OND window).
   Shipped as a band, NOT applied — `cat` and the probabilities stay unmasked so downstream work
   can choose its own cutoff. Apply it by default when counting exposure.
7. Periods: each lead month (Oct, Nov, Dec) + full OND total.

Temperature runs the identical steps with two differences, both in the `VARS` table at the
top of `download_ecmwf_prec.py`: `convert` is `K − 273.15` with no day-length scaling, and
`window_agg` is **mean** rather than sum. No `dry_mask` — there is no "too cold to
interpret" analogue — so temperature terciles ship **7** bands against precipitation's 8.

## Compound method (member-wise joint counting)
Per cell, thresholds are each variable's **own** pooled-hindcast terciles — the same ones
`terciles()` computes. A compound event is both variables simultaneously in their own
extreme third, which stays directly comparable with the single-variable layers.

    p_hotdry = mean over the 51 members of [ (prec_i < P33_prec) AND (t2m_i > P67_t2m) ]

**Never `p_dry × p_hot`.** That assumes independence. Dry and hot are positively correlated
through land–atmosphere feedback — drier soil means less evaporative cooling means higher
temperature — so the product *understates* compound risk exactly where it matters most,
over semi-arid land. The ensemble member is the only unit that preserves the correlation,
because one member is one physically self-consistent realisation of the season.

`dependence_ratio = p_hotdry / (p_dry × p_hot)` ships as a band so the size of that error
is auditable rather than asserted. Verified on synthetic data: 2.46 when dry and hot
coincide, 0.96 when independent.

**Do not read a global mean of this band.** For OND 2026 the unconditioned global mean is
**0.91** while *every* populated latitude band is **≥ 1.0** — Simpson's paradox. Three
things conspire, and the script's logged figure conditions on all three:
1. **Poleward of ±60° the physics inverts.** No land–atmosphere moisture feedback; warm
   advection brings heat *and* moisture, so dry-hot co-occurrence falls below independence.
   Pooling those cells with tropical land is what drags the mean under 1. Biggest effect.
2. **The hot tercile saturates.** Against a 1993–2016 baseline SEAS5 puts `p_hot ≥ 0.99`
   over ~21% of land, and where `p_hot` is pinned at 1 "dry AND hot" is just "dry", so the
   ratio is mathematically capped at 1.
3. Small `p_dry` denominators add noise.

Conditioned (`|lat| ≤ 60`, `0.20 ≤ p_hot ≤ 0.80`, `p_dry ≥ 0.10`) the four OND 2026 periods
read **1.018–1.081**, and by latitude band 10–30S peaks at **1.14**. The ground truth is the
across-member correlation of prec vs t2m, which is strongly negative exactly where expected:
**Amazon −0.86, Horn −0.84, southern Africa −0.74, India −0.71, Australia −0.53** (negative
= drier members are hotter = the feedback is real). A sub-1 *conditioned* reading means the
feedback has vanished from the ensemble, which in practice means member alignment or joint
logic — `assert_aligned()` cannot catch a logic error, so this is the second opinion.

**Consequence for the feature, not just the diagnostic:** because the hot tercile is
saturated across much of the tropics, a hot-dry signal there is nearly identical to the dry
signal — hot adds no discrimination where every member is hot. The compound layer earns its
keep in the **subtropics** (southern Africa, Australia, and the 10–30S band generally). Say
this when presenting it; do not imply the compound signal sharpens the tropics.

**A compound signal's no-information baseline is 1/9 = 11.1%, not 1/3.** `PROB_THR_COMPOUND`
is 0.25 — 2.25× that baseline, the same margin 0.5 has over 1/3 for a single variable.
Reading a compound probability against 33% makes every map look empty. The compound and
single-variable thresholds are **not comparable** and must never be shown side by side
without both being labelled.

`cat_compound` resolves overlap by **argmax**, not chained `xr.where`: unlike dry/wet, the
three compound classes are not algebraically exclusive (different members can vote
different ways), so a chain would silently let whichever branch is written last win.

## Current code — RUN AND UPLOADED
`download_ecmwf_prec.py` — download (cdsapi) → load (cfgrib,
`time_dims=("forecastMonth","time")`) → terciles → GeoTIFFs (rioxarray, nodata −9999) →
upload via **geeup**. No GCS bucket: this project has none, so geeup stages internally,
matching `scripts/upload_infra.py`.

Own virtualenv: `scripts/el_nino/.venv`, pinned in `requirements.txt`. Run it with that
interpreter directly (no activate). The GRIBs are cached — `download()` skips files that exist,
so a re-run after a config change costs minutes, not a re-download.

Run it with `--var` (default `prec`, so a bare invocation does exactly what it always did):

    PY download_ecmwf_prec.py --var t2m                      # temperature
    PY download_ecmwf_prec.py --var prec --var t2m           # both + compound
    PY download_ecmwf_prec.py --only compound                # implies both vars

Precipitation keeps the **unsuffixed** collection names the dashboard already reads;
temperature lands in parallel `_t2m` collections. So temperature can be absent and every
existing code path is unchanged.

GEE layout under `projects/unicef-ccri/assets/el_nino/`:
- `raw_forecast`: 1 image, 153 bands `L{lead}_m{member:02d}`, mm/month
- `raw_hindcast`: 24 images (one per year), 75 bands, same naming
- `terciles`: 4 images (Oct, Nov, Dec + OND window), 8 bands
  `p_below, p_normal, p_above, cat, fc_mean_mm, clim_mean_mm, anom_mm, dry_mask`
- `raw_forecast_t2m` / `raw_hindcast_t2m`: as above in degC
- `terciles_t2m`: 4 images, **7** bands — `…, fc_mean_c, clim_mean_c, anom_c`, no dry_mask
- `compound`: 4 images, 8 bands `p_hotdry, p_coolwet, p_hotwet, cat_compound,
  p_dry_marginal, p_hot_marginal, dependence_ratio, dry_mask`

The 7-vs-8 band asymmetry is deliberate and protective: a `.rename()` against the wrong
name list fails loudly on a count mismatch instead of silently mislabeling every band, so
`terciles` and `terciles_t2m` can never be confused.
- Image properties: system, init, init_ym, kind, year, lead, period, prob_threshold,
  dry_mask_mm, hindcast, cat_legend, lead_legend, **band_names**.

**geeup drops band names** — every asset arrives as `b1..bN`. Band ORDER is the contract; the
names live in the `band_names` property. Always `.rename()` before selecting by name, hindcast
images included. See the geeup memory note for the other two traps.

## Temperature skill — DONE
`build_skill_mask.py --var t2m` → `el_nino/skill_t2m`, same two bands. Parameterised via an
`OBS` registry mirroring `VARS`; the precipitation path is unchanged and still reproduces
`r = 0.4720394992725713` over the Horn exactly.

Reference is **ERA5-Land** (`ECMWF/ERA5_LAND/MONTHLY_AGGR`, band `temperature_2m`, Kelvin),
staged to `el_nino/era5_obs` (96 assets) by `--var t2m --stage-obs` first — the same hard
prerequisite CHIRPS has, for the same OOM reason.

**Temperature skill is genuinely higher than rainfall skill**, as the literature predicts:
over the Horn of Africa r = **0.596** (window), 0.571 / 0.494 / 0.469 for Oct / Nov / Dec,
against 0.472 for rainfall. That is the core justification for the feature — the more skilful
variable constrains the less skilful one.

**But ERA5-Land is not an independent observation.** It shares IFS physics and assimilation
lineage with SEAS5, so temperature skill scored this way is **optimistically biased** relative
to CHIRPS-scored rainfall skill, and ERA5 runs cool over Africa. Partial mitigation, and why
it is still worth doing: the tercile framing judges each side against its own distribution, so
a uniform bias cancels exactly and only direction errors are penalised — a spatially varying
bias does not cancel. Both halves of that belong in any caption. CHIRTS would be the
independent alternative but is not a maintained GEE collection.

**Dashboard default threshold is now 70%**, matching the published methodological note and the
exported workbooks. It was 50% while every written figure used 70, so the same region read
differently depending on which artefact you looked at.

## Traps added by the temperature work
- **Sum vs mean.** Precipitation accumulates over a period; temperature averages. This
  appears in three places: `window_agg` (terciles window), `season_agg` (observations), and
  lead-aggregation in the skill scripts. A *summed* OND temperature is ~80 °C and obvious.
  A *lead-averaged* precipitation total is 1/3 too small and **not** obvious — that is the
  dangerous direction, and the precipitation regression below is what catches it.
- **Member alignment.** The compound signal is arithmetically meaningless if member *i* of
  the temperature GRIB is not the same realisation as member *i* of the precipitation one.
  CDS returns them as separate retrievals and promises no ordering. `assert_aligned()` is a
  hard exit on `number`/`forecastMonth`/`latitude`/`longitude`; `dependence_ratio` is the
  independent second check. Both are needed — the gate catches reordering, the ratio
  catches a logic error the gate cannot see. The gate has been tested by deliberately
  shuffling member order, so it is known to fire rather than merely written.
- **`dry_mask_mm` must be 0, not None, for temperature.** Every image in a GEE collection
  must carry the same property *type*; an empty CSV cell arrives as a string where the
  others are numbers and ingestion fails for the odd one out.
- **Regression test for any future refactor of this script:** rebuild precipitation into a
  scratch directory and `sha256sum` the four `terciles_202609_*.tif` against the files in
  `data/seas5_202609/`. They must be **byte-identical** — that is the proof a change to the
  shared code path did not perturb what is already uploaded. Verified identical after the
  `VARS` parameterisation.

## Exposure — DONE, in the dashboard
Built as an app section, not a batch script: `app/elnino_config.py` + `app/elnino_core.py`,
wired into `app/app.py` as the El Niño tab. Uses the project's own under-18 grid via
`forecast_core._forecast_pop` (1 km) so figures reconcile with the other tabs — not WorldPop
direct — and GCHD adm0/1/2 boundaries rather than GAUL.

Exposure is re-derived from `p_below`/`p_above` at a user-set threshold, never from the stored
`cat` band (which is frozen at 50%). `dry_mask` is applied by default.

## Open items
- **Probability threshold**: 0.5 default; ECMWF charts shade from ~0.4. The tab reports 40/50/60%
  side by side rather than one figure, because 51 members quantise probability in ~2-point steps.
  Ethiopia spanned 44.6M → 19.3M children across that range.
- ~~**No skill mask.**~~ **DONE — two measures, and they disagree, which is the point.**

  `build_skill_mask.py` → `el_nino/skill`, 2 bands (`skill_r`, `skill_p`): Pearson correlation
  between the hindcast **ensemble mean** season total and CHIRPS, 1993–2016.

  `build_brier_skill.py` → `el_nino/brier`, 4 bands (`bss_dry`, `bss_wet`, `brier_dry`,
  `brier_wet`): Brier skill score on the **tercile probabilities**, with leave-one-out
  thresholds. This grades what the exposure count actually reads; the correlation grades the
  mean. A model can pass the first and fail the second by being consistently overconfident.

  | region | r | BSS |
  |---|---|---|
  | Maritime continent | +0.58 | **+0.35** |
  | Horn of Africa | +0.47 | **+0.07** |
  | southern Africa | +0.28 | +0.04 |
  | Europe | −0.00 | −0.05 |

  Correlation systematically overstates probabilistic skill — the Horn looks respectable at
  r=0.47 and adds almost nothing over guessing. Zero is BSS's meaningful boundary
  (beats always-forecasting-33% or does not), so the continuous layer's range is symmetric
  −0.4..0.4 to put zero on the palette's neutral midpoint.

  **DECIDED (Oct 2026): the dashboard ships the CORRELATION, not BSS.** The module's earlier
  position was "prefer BSS", and the technical argument above still holds — BSS grades the
  probabilities the threshold actually reads, correlation only grades the ensemble mean, and
  correlation is therefore the more generous measure. But reviewers of the rainfall report
  compared both and chose correlation: BSS did not sit alongside the classified historical
  values they use to read a signal's **direction**, and it did not survive contact with a
  non-specialist audience. That is a legitimate call about interpretability, so the two BSS
  layers, the BSS results row and the `brier_*` export keys are removed from the UI.

  Nothing is deleted. `el_nino/brier`, `build_brier_skill.py`, `signal_bss()` and the
  `BSS_*`/`BRIER_*` constants all still exist and still work; restoring the layers means
  re-adding two `EXPLORE_LAYERS` entries. The UI now states plainly that the correlation is
  the forgiving measure, so dropping BSS does not quietly upgrade how much confidence a
  figure implies.

  **No compound BSS was built.** `_brier_band_for()` returns `None` for a compound signal
  rather than falling back to the rainfall score, because showing `bss_dry` beside a hot-dry
  figure would read as though the joint forecast had been verified. Doing it properly needs a
  leave-one-out Brier on the joint outcome against a joint base rate (1/9, not 1/3) — a
  separate script, not a band rename.

  Both are **reported, never applied** — no exposure figure changes. Four map layers plus a
  two-line caption on each result. CHIRPS is land-only (50N–50S), so ocean/high-latitude cells
  are class 0 "not assessed", deliberately distinct from "no skill".

  **Prerequisite:** `build_brier_skill.py` needs the staged CHIRPS observations —
  `build_skill_mask.py --stage-obs --wait` first. Without staging, a global export that regrids
  5 km CHIRPS inside a 24-year loop dies with "out of memory"; staging the coarse observations as
  their own assets (`el_nino/chirps_obs`) is what makes both scripts viable. Verified
  arithmetically neutral: r = 0.4720394992725713 before and after.
- ~~**Attribution.**~~ **ADDRESSED, not solved.** Nothing in the pipeline isolates ENSO as a
  cause — SEAS5 output is its net response to all initial conditions. Two things now make the
  link checkable rather than assumed: a live Niño 3.4 index from NOAA OISST (observed, wholly
  independent of the forecast), and an `enso_composite` layer = the model's own El Niño-minus-
  neutral rainfall pattern from 8 vs 9 hindcast seasons. Composite signs verified against
  teleconnection theory (maritime continent −161 mm, east Pacific +88, Horn +58, S Africa −17).
  Framing throughout is now conditional: "during a period when El Niño conditions are present".
  True attribution needs counterfactual ENSO-neutral model runs, which SEAS5's archive does not
  support.
- **October initialisation** publishes ~5 Oct and would give a sharper OND (leads 1–3, 1–3 months
  ahead instead of 2–4). That is a genuine re-download — new initial conditions AND an
  October-initialised hindcast — landing under a `202610` tag. `elnino_config.INIT_TAG` currently
  assumes one initialisation; showing both would need a small change.
- QGIS visual check against the ECMWF `seasonal_system5_standard_rain` tsum chart: still not done.

## Conventions
- Python: xarray, cfgrib, rioxarray, earthengine-api, geeup (NOT google-cloud-storage —
  there is no bucket).
- Concise, practical code; config constants at top of scripts.
- Validate outputs visually (QGIS) against the ECMWF chart before computing exposure.

## Dashboard UX pass (Oct 2026)
Reviewing the running tab showed the data was right but the interface asked too much of the
reader. Seven changes, and the reasoning behind the non-obvious ones:

- **Signal is now a rainfall picker + "also require unusual heat" toggle**, not six radios.
  The resolved signal name goes into a hidden `dcc.Store(id="elnino-signal")`, so all nine
  `_signal_mask` call sites and every `State("elnino-signal")` reader are unchanged — the
  control got readable without the computation moving. `either` + heat is refused: a union
  has no single joint probability band to threshold.
- **`coolwet` stays in `SIGNALS` and the data but is off the picker.** "Cooler and wetter" is
  the flood proxy `wet` alone already serves; offering both is a distinction without a
  decision. The band still exists in the compound asset.
- **Map layers moved out of the control stack onto the map.** They sat as "5. Map layer"
  implying they affected the computation; they never did. The panel header says so. The
  control keeps the id `elnino-layer`, so every consumer is untouched.
- **Threshold gained a chance-baseline bar.** The compound range looked arbitrary because the
  baseline it is read against (11.1%) was invisible. Both minima were lowered so each
  baseline sits ON its scale: rainfall 34→**32**, compound 12→**10**. A scale that hides its
  own reference point cannot be read.
- **`SENSITIVITY_THRESHOLDS` 40/50/60 → 50/60/70/80**, and the active threshold is always
  injected. The old ladder sat entirely below the 70% default, so the row that exists to show
  how much the headline moves could not show it. Compound signals use their own 20/25/33.
- **Zero-exposure empty state.** Zimbabwe dry is 7.2M children at 50% and exactly 0 at 65%+.
  A bare `0` reads as "no risk" rather than "nothing clears this bar", so when a lower
  threshold would return children the panel names it. This is a direct consequence of the
  70% default and the default is wrong without it.
- **ENSO attribution is now in the dashboard**, reusing the report's mask and direction map
  (`ENSO_MASK_DIR`) so a dashboard figure and a workbook figure agree. Reported, never
  applied. Measured: Ethiopia wet @70% is 100% attributable; Ethiopia **dry** @70% is 0%,
  because El Niño makes the Horn wetter — the mask working as intended.
- **(i) popovers** reuse the existing `prov-info-btn` / `#hazard-info-panel` machinery under
  two new scopes (`en:` for concepts, `enl:` for layers). Methodology deliberately stays
  visible in its own block rather than moving into tooltips.
- **Excel replaces the JSON link** (JSON kept as a secondary for scripts).
  `build_elnino_xlsx` is separate from `build_xlsx` rather than an extension of it: that
  helper writes a single grid for two other tabs and widening its contract would risk their
  formatting. Five sheets, caveats first.

### Follow-up round (same day)
- **`p_cooldry` added — the 2x2 is now complete.** It had been omitted, not ruled out, and the
  omission was mine: the early scoping question asked about hot-dry and hot-wet and never
  raised cold. Compound images are now **9 bands** and `cat_compound` has **5 classes**.
  Rarer than hot-dry because the feedback works against it (drier members tend to be hotter),
  but far from empty — and it surfaces a signal that was invisible before: **Fiji 279,579 and
  Vanuatu 123,890 children**, close to their entire child populations, from the El Niño dry
  signal over the South Pacific. 1,571 land cells reach class 4 against cool-wet's 788.
- The temperature control is now a three-way radio (not considered / hot / cool) rather than a
  checkbox, so all four corners are reachable. `either` + any temperature is still refused.
- **Both threshold minima lowered again is NOT what happened** — they were already 32 and 10
  from the previous round; the red marker simply had no caption. It now says what it is.
- Popovers **flip left** when there is no room on the right. The map-layer panel sits against
  the right edge, so the previous fixed `left: r.right + 10` put its popovers off-screen
  entirely.
- `.info-icon-btn` was rendering at ~8px inside a 0.58rem uppercase `.ps-label`; section
  headings now give the icon its own 0.78rem floor instead of inheriting a fraction.
- Long prose moved into `<details>` blocks (`_en_collapse`): the tab intro, the numbered
  method steps and the caveats list. Plain HTML, no callback — it keeps working if a callback
  errors and is accessible by default. Methodology gained a four-sentence plain-language
  summary ABOVE the collapsed steps, so the auditable detail is still one click away rather
  than gone.
- Layer rows gained an eye toggle (clientside, opacity only — never touches which layer is
  selected) and a collapsible inline colour key.

### Follow-up round 2
- **Cyan heading hover was near-invisible.** `--cyan-lt` is `rgba(28,171,226,0.08)` — a
  translucent *background* tint — and I had used it as `color`. Added `--cyan-hi: #7FD4F2`
  for text hover. Every other use of `--cyan-lt` in the stylesheet is a `background`; mine
  was the only `color` misuse.
- **Temperature is a checkbox that reveals hot/cool when ticked**, not three radios up
  front. Three visible options made "temperature" read as mandatory; the direction is a
  second question that does not exist until the first is answered. An unticked box ignores
  whatever direction is remembered (tested).
- **One click target per layer row.** The (i) is now a marker *inside* the row button rather
  than a second adjacent control — two neighbouring targets doing different things was the
  confusing part. Clicking anywhere on the row selects the layer and reveals its description.
  `_en_layer_info` deleted; the `enl:` popover scope stays wired in `toggle_hazard_info` in
  case a per-layer popover is wanted again.
- **The inline key is now shown for the SELECTED row only** (`_en_layer_detail`). Previously
  every row could expand its own key, so the panel could display a colour scale for a layer
  that was not on screen — the opposite of helpful, and it competed with the map's own legend.
- **The map legend states the counts.** It already showed the selected layer plus the
  population rows; the rows now carry the figures ("All children — 60.98M", "Children in a
  drier-than-normal area — 2.58M (4%)") so a reader looking at the map does not have to
  scroll back to the panel. `meta["exposed"]`/`meta["total"]` are carried in the viz payload
  for this.

### Follow-up round 3 — the map-layer model, corrected
The earlier rounds had the explore layer *replacing* the signal on a single shared tile
(`elnino-layer-tile`), which was wrong: the signal is what the panel's numbers describe, so
it must always be on screen. Now **two tiles**:

- `elnino-layer-tile` — the SIGNAL, always drawn from section 2's selection at opacity 0.8.
  `SIGNAL_LAYER = "signal_prob"` for every signal; the union falls back to `cat`, which is
  the only readable form for two opposite directions.
- `elnino-extra-tile` — an optional ADDITIVE layer chosen in the map dialog, drawn above the
  signal at 0.6 so both read at once. One at a time. Switched off with opacity 0 rather than
  an empty url, so re-enabling is instant.

Consequences worth knowing:
- **`_d_signal_prob` had to learn about compound signals.** It selected the signal's band
  from the terciles image, but `p_hotdry` and friends live in the compound asset — so making
  `signal_prob` THE signal layer for all signals would have thrown for every compound one.
- **A layer is "enabled" only when chosen AND switched on.** The eye and the title do the
  same thing (enable); the eye additionally turns it back off. The inline colour key expands
  only when enabled, because a key for an undrawn layer is noise.
- **The (i) is a separate control again** and changes nothing on the map — it only explains.
  Collapsing it into the row button (round 2) conflated "show me this" with "tell me about
  this".
- **The legend always leads with the signal**, then the extra layer marked "(extra layer)",
  then the population rows with their counts. It previously labelled the first row from
  `viz["layer"]`, so choosing a dialog layer made the legend claim the signal had changed.

### Follow-up round 4
- **"This signal" group dropped from the map-layer panel.** It offered `signal_prob` and
  `cat`, which conflicts with section 2 — the panel is for ADDITIVE extras only, and the
  signal has its own tile. 16 extra layers remain across five groups.
- **Skill layers now name their variable**: "Rainfall skill (correlation/classified)" beside
  "Temperature skill (correlation)", and the results heading reads "Rainfall forecast skill
  here". Previously "Forecast skill" was ambiguous once a second skill surface existed.
- **The chance-baseline bar was removed.** An unlabelled red line read as an artefact; the
  same point is carried by the "N× more likely than chance" readout and the (i) beside the
  section title. Its four callback outputs and CSS went with it.
- **Confirmed: the El Niño rainfall fingerprint is in mm** — `_hindcast_season_mean` returns
  an OND season TOTAL in mm, and the layer is the difference of two means of that (8 El Niño
  hindcast seasons minus 9 neutral), ranged ±300 mm. Matches the recorded verification
  figures: maritime continent −161, east Pacific +88, Horn +58.
