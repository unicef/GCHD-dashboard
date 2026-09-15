# Submitting infrastructure points — guide for country offices

Send us a CSV of facility locations and they become a selectable layer in the
dashboard's **Infra Analysis** tab, showing how many facilities sit in hazard
areas and how many children they serve.

**Templates are downloaded from the app**: Infra Analysis → *Add your own
facility data*. They live in `app/assets/templates/` and are served statically,
so the dialog links straight to them — there is no separate copy to keep in
step.

| Facility type | Template |
|---|---|
| Schools | `co_schools_template.csv` |
| Health facilities | `co_health_template.csv` |
| Water points | `co_water_template.csv` |

## Check the file before sending it

```bash
python scripts/validate_co_submission.py my_schools.csv
```

Needs only pandas — no Earth Engine credentials — so an office can run it
locally. It reports swapped latitude/longitude columns, coordinates in metres
rather than degrees, missing minus signs, blank 0,0 rows, line breaks inside
names, and duplicate points, in plain language. Exit code is 0 for clean,
1 for warnings, 2 for errors.

---

## Required columns

| Column | Required | Notes |
|---|---|---|
| `facility_id` | recommended | Your own identifier. If you leave it blank we generate one, but supplying it means a re-submission updates records instead of duplicating them. |
| `name` | recommended | Facility name. May be blank — it is only used for display. |
| `longitude` | **yes** | Decimal degrees, WGS84 (EPSG:4326). |
| `latitude` | **yes** | Decimal degrees, WGS84 (EPSG:4326). |

Optional extras, if you hold them: `subtype` (all types), `education_level`
(schools), `status` (water points). Anything else is ignored.

### Coordinates

This is where submissions most often go wrong, so it is worth checking twice:

- **Decimal degrees only.** `-13.9626` — not `13°57'45"S`, not `13 57 45 S`.
- **Southern and western hemispheres are negative.** A missing minus sign puts
  the facility in the wrong hemisphere, and it will silently fall outside the
  country boundary and be dropped from the analysis.
- **Longitude then latitude**, not the reverse. Longitude runs −180 to 180;
  latitude runs −90 to 90. If a "latitude" is greater than 90 the columns have
  been swapped.
- **WGS84 (EPSG:4326).** If your GIS uses a national grid or UTM, reproject
  before exporting. Coordinates in metres (e.g. `512345, 8456789`) are not
  usable.
- Rows with a missing or non-numeric coordinate are dropped, and we report how
  many.

Column names are flexible — `lon`/`long`/`x` and `lat`/`y` are accepted, in any
capitalisation — so an export that already uses those spellings needs no
renaming.

---

## What NOT to include

**Do not add columns for country, facility type, or data source.** Those are
recorded when we register the layer. A column that disagrees with the
registration creates two conflicting answers to the same question.

**Do not send personal or sensitive data.** Facility locations only — no staff
names, no contact details, no patient or pupil records. Anything beyond the
columns listed above is discarded, but it is better not to send it.

Consider whether any facility location is itself sensitive in your context
(for example, facilities serving displaced populations). If so, flag it when
you send the file rather than including it and hoping.

---

## Format

- **CSV**, UTF-8. Excel's "CSV UTF-8 (Comma delimited)" export works; so does a
  plain CSV export from QGIS, KoBo or ODK.
- One header row, then one row per facility.
- Keep each record on a single line. Line breaks inside a facility name are the
  single most common cause of a corrupted file — we strip them, but a name
  containing a newline can shift the coordinate columns before we get to it.
- No merged cells, no totals row, no notes above the header.

---

## How to send it

Send the file(s) to the GCHD team through your usual channel, with:

1. **The country** the data covers.
2. **The source and date** — which registry or survey, and when it was compiled.
   This is shown to users as the layer's provenance.
3. **Whether it may be shown publicly**, or is for UNICEF staff only.

We validate, load it to Earth Engine and confirm once the layer is live. If
rows are dropped we tell you how many and why, so the next submission is
cleaner.

---

## For the GCHD team — processing a submission

1. Save the files as `app/data/infra/{iso3}/raw/co_schools.csv`,
   `co_health.csv`, `co_water.csv` (lowercase ISO3, from `adm0_countries.json`).
2. `python scripts/prep_infra.py --iso3 {ISO3}` — normalises to the standard
   contract and writes `out/{iso3}_co_{layer}.csv`.
3. `python scripts/upload_infra.py --iso3 {ISO3}` — validates the asset stem and
   uploads. The stem must be `{iso3}_co_{layer}`: `geeup` derives the GEE asset
   id from the filename, and the app parses it back, so a wrong name uploads
   cleanly and is then invisible in the dashboard.
4. The Infra tab discovers new assets on a 10-minute TTL — no redeploy needed.

The `co` source is registered in `prep_infra.SOURCES`, has three `JOBS` entries
(one per layer), and is labelled "Country Office" in `gee_core._SOURCE_LABELS`.
A test asserts every source has a label.
