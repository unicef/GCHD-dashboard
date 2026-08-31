# Technical Documentation — UNICEF Global Child Hazard Database (GCHD)

**Prepared by:** Dohyung Kim  
**Date:** June 2026  
**Audience:** UNICEF Cloud Platforms Team / ICTD TAO

---

## 1. System overview

The Global Child Hazard Database (GCHD) is a web application that visualises global child exposure to climate and geophysical hazards. It is developed to support the UNICEF Children's Climate Risk Report (CCRR) and is used internally by UNICEF staff to explore hazard layers, compute child population exposure by administrative boundary, and export results.

**Live URL:** https://gchd.unicef.org  
**Active users:** ~46 UNICEF staff on Jun 19 2026 
**Access control:** Restricted to @unicef.org email addresses via Cloudflare Zero Trust

---

## 2. Architecture

### High-level architecture diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        User Browser                         │
│   Plotly Dash (React) + Leaflet map                         │
│   - Renders tile layers from Google Earth Engien tile server│
│   - Parses and displays uploaded GeoJSON client-side        │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Cloudflare Network                             │
│   - SSL/TLS termination                                     │
│   - DDoS protection                                         │
│   - DNS: gchd.unicef.org → CNAME → gchd.pixel-aid.com       │
└──────────────────────┬──────────────────────────────────────┘
                       │ Cloudflare Tunnel (encrypted)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│     On-premises server (Ubuntu, /mnt/pixel_aid_disk1)       │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  cloudflared (Cloudflare Tunnel daemon)              │  │
│   │  Forwards traffic → localhost:8502                   │  │
│   └──────────────────────┬───────────────────────────────┘  │
│                          │                                  │
│   ┌──────────────────────▼───────────────────────────────┐  │
│   │  Gunicorn WSGI server (port 8502, 2 workers)         │  │
│   │  Managed by systemd (hazard-app.service)             │  │
│   │                                                      │  │
│   │  Plotly Dash Application (Python 3.12)               │  │
│   │  ├── Flask  (session management)                     │  │
│   │  ├── dash-leaflet  (interactive map component)       │  │
│   │  └── GEE Python API  (tile URL generation,           │  │
│   │                        zonal statistics)             │  │
│   └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┤
          ▼            ▼
┌──────────────┐ ┌──────────┐ 
│ Google Earth │ │Cloudflare│
│   Engine     │ │   Zero   │
│ (raster data │ │  Trust   │
│  & analysis) │ │  (auth)  │
└──────────────┘ └──────────┘
```

### Key architectural principles

- **No raster data on the app server.** All raster processing (thresholding, masking, zonal statistics) runs in Google Earth Engine. The app server only constructs GEE expressions and returns tile URLs to the browser.
- **No file IO at request time.** All reference data lives as GEE assets (`projects/unicef-ccri/assets/`). No CSV, raster, or vector files are loaded from disk during a request.
- **Boundary files handled client-side.** Uploaded GeoJSON is parsed, validated, and rendered on the browser. Only the geometry and a single name field are sent to the app server for GEE computation.
- **Static hazards are cached for the server's lifetime; live data never is.** `gee_core.build_core_images()` is `@lru_cache(maxsize=1)` and holds the whole static hazard image graph. Anything parameterised by time is built on demand in `forecast_core` instead (see below).

### Forecast & Live subsystem

The Forecast tab (`forecast_config.py` + `forecast_core.py`) is deliberately kept out of the static hazard pipeline. Three design decisions are load-bearing:

1. **Separate module, not extra `HAZARDS` entries.** `build_core_images()` is cached once per server lifetime. A date-parameterised layer inside it would either poison that cache or force a full rebuild on every date change, degrading the whole application. `forecast_core` imports the child-population grid *from* `build_core_images()` so the exposure denominator stays identical, but builds its own hazard image per `(dataset, window, reducer)` and caches that behind a short TTL.

2. **Reduction runs at the population grid's scale, not the forecast grid's.** GFS is ~28 km and Sentinel-5P ~1 km, but `compute_forecast_exposure` reduces at `pop_target_res` (WorldPop 100 m) using the same `chunk_asset` 500 km chunks and the same `reduceRegions` → `reduceColumns` pattern as `gee_core.compute_exposure`. This is what makes a forecast result reconcile with an Analysis-tab result for the same region — verified equal to the digit.

3. **No ADM0 fast path.** The precomputed `Hazard_Population_Exposure_adm0` asset holds static hazards only, so country-level forecast queries run the real reduction. A consequence worth knowing: **thresholds take effect at ADM0 here**, whereas the Analysis tab drops them at country level in favour of the precomputed asset.

**Runtime dataset flags.** Which forecast datasets appear in the tab is overridable at runtime by an optional GEE table (`projects/unicef-ccri/assets/config/forecast_datasets`), read on a 10-minute TTL by `forecast_core.fetch_active_overrides`. Toggling a dataset therefore needs no code change, no redeploy and no restart — the same principle as the Infrastructure tab's asset discovery. The table is optional and a read failure falls back to the `active` flags in `forecast_config.py`, so an absent or broken asset degrades to the code's defaults rather than an empty tab; only dataset names already present in `FORECAST_DATASETS` are honoured. Managed with `scripts/forecast_flags.py` or edited directly in the Code Editor.

Two failure modes get explicit handling rather than silent wrong answers:

- **Empty windows.** Satellite latency and forecast run timing mean a plausible-looking window can contain zero images, which would reduce to an all-masked image summing to zero — indistinguishable from "no children exposed". `build_forecast_image` counts the collection first and raises `ForecastError` with a dataset-specific hint. The image count is also surfaced in the results panel and the JSON export.
- **Unit and band irregularities.** Raw catalog bands are frequently not in units a threshold can express, so per-dataset transforms are declared in config and dispatched in `forecast_core`: Sentinel-5P mol/m² → µmol/mmol, MODIS LST scaled-integer Kelvin → °C, IMERG mm/hr → mm, GFS wind derived as `hypot(u10, v10)` (there is no gust band), and FIRMS fire-day counts derived from mask presence (there is no count band). GFS `forecast_hours=0` images lack the precipitation band entirely and are filtered out.

---

## 3. Technology stack

### Application

| Component | Technology | Version |
|---|---|---|
| Web framework | Plotly Dash | ≥ 2.18 |
| Map component | dash-leaflet (Leaflet.js) | ≥ 1.1.3 |
| WSGI server | Gunicorn | ≥ 21.2 |
| Language | Python | 3.12 |
| Underlying web framework | Flask (via Dash) | — |

### External services

| Service | Provider | Purpose |
|---|---|---|
| Raster computation & tile serving | Google Earth Engine | All spatial analysis and map tile generation |
| Geospatial assets | GEE project `unicef-ccri` | Hazard rasters, admin boundaries, population data |
| Access control | Cloudflare Zero Trust | Email-based identity verification (@unicef.org) |

### Infrastructure

| Component | Details |
|---|---|
| Compute | On-premises Ubuntu server (`/mnt/pixel_aid_disk1`) |
| Tunnel / ingress | Cloudflare Tunnel (no open inbound ports on the server) |
| DNS | `gchd.unicef.org` CNAME → `gchd.pixel-aid.com` (Cloudflare-managed) |
| SSL | Managed by Cloudflare (automatic, no certificates on server) |
| Process management | systemd (`hazard-app.service`) |

---

## 4. Data & GEE assets

All hazard raster data and administrative boundary files are stored as GEE assets under `projects/unicef-ccri/assets/`. No data is stored on or served from the app server.

### Hazard datasets

| Hazard | Source | Period |
|---|---|---|
| River Flood | JRC Global Flood Maps | 2024 |
| Coastal Flood | JRC Global Flood Maps | 2024 |
| Tropical Storm | GIRI / UNDRR | 2024 |
| Drought (Agricultural) | FAO GIEWS | 1984–2023 |
| Drought (Meteorological) | TerraClimate SPI/SPEI | 1958–2025 |
| Heatwave (frequency/duration/severity) | ECMWF | 2014–2024 |
| Extreme Heat | ECMWF | 2014–2024 |
| Fire | NASA FIRMS | 2001–2024 |
| Sand & Dust Storm | UNCCD | 2024 |
| Air Pollution (PM2.5) | ACAG | 1998–2023 |
| Malaria (Pf & Pv) | Malaria Atlas Project | 2012–2022 |
| Landslide | World Bank / GFDRR | 1980–2018 |
| Earthquake | GEM Global Seismic Hazard Map | 2023 |
| Volcanoes | Smithsonian GVP | 1800–2025 |

### Forecast & near-real-time datasets

Unlike the hazard datasets above — which are fixed return-period or climatology assets held in `projects/unicef-ccri/assets/` — these are read live from the **public GEE catalog** and reduced over a user-selected date window. Catalogued in `app/forecast_config.py`.

| Dataset | Collection | Topic | Kind | Latency |
|---|---|---|---|---|
| Rainfall forecast | `NOAA/GFS0P25` | River Flood | forecast (16 d ahead) | ~5 h |
| Wind speed forecast | `NOAA/GFS0P25` (derived U/V) | Tropical Storm | forecast | ~5 h |
| Air temperature forecast | `NOAA/GFS0P25` | Extreme Heat | forecast | ~5 h |
| Observed rainfall | `NASA/GPM_L3/IMERG_V07` | River Flood | near-real-time | ~2 d |
| Land surface temperature | `MODIS/061/MOD11A1` | Heatwave | near-real-time | ~2 d |
| Active fire brightness / fire days | `FIRMS` | Fire | near-real-time | ~2 d |
| NO₂ / SO₂ / CO / O₃ | `COPERNICUS/S5P/NRTI/L3_*` | Air Pollution | near-real-time | 3–20 h |
| Absorbing aerosol index | `COPERNICUS/S5P/NRTI/L3_AER_AI` | Sand & Dust Storm | near-real-time | ~3 h |

Every id, band name and value range was verified against the live catalog before being catalogued. Rejected candidates (Weather Next — not accessible to the service account; CHIRPS-GEFS — no such public collection; IMERG V06 and VIIRS VNP14A1 — superseded/stale) are recorded with their reasons in the `DROPPED` block at the foot of `forecast_config.py`, so they are not re-added from memory.

### Population data

- **WorldPop** global gridded children population (under 18), 2025, 100 m resolution — used for all exposure computations.

### Administrative boundaries

- UNICEF GeoRepo: adm0 (country), adm1 (province/state), adm2 (district/county) — stored as GEE FeatureCollection assets under `projects/unicef-ccri/assets/global_boundary/`.

---

## 5. Authentication & access control

- Access is controlled by **Cloudflare Zero Trust**, which verifies identity using the user's UNICEF email address (@unicef.org) before allowing any request to reach the application.
- Cloudflare Zero Trust enforces email domain restrictions and manages sessions with a configurable seat-based policy (currently up to 50 seats on the free tier).
- No credentials are handled by the application server itself — authentication is fully delegated to Cloudflare.
- During UNICEF cloud migration, Cloudflare Zero Trust can be replaced with UNICEF Azure AD / SSO.

---

## 6. Current resource usage

| Resource | Current spec / notes |
|---|---|
| Compute | On-premises Ubuntu server |
| Storage | `/mnt/pixel_aid_disk1` (mounted disk) |
| GEE quota | Standard GEE service account under `unicef-ccri` GCP project |
| Concurrent users | ~46 registered; light concurrent load (GEE handles compute) |
| Gunicorn workers | 2 |

---

## 7. Source code

The application source code is hosted in a private GitHub repository:

**https://github.com/unicef/GCHD-dashboard**

Credentials and secrets are excluded from the repository and managed as files on the server under `app/credentials/`.

---

## 8. Migration considerations

| Topic | Notes |
|---|---|
| Compute | App server is lightweight; any small Azure VM or App Service instance is sufficient |
| GEE access | The GEE service account (`projects/unicef-ccri`) must remain accessible from the new host |
| Authentication | Cloudflare Zero Trust can be replaced with UNICEF Azure AD SSO during migration |
| Secrets management | Credential files should be migrated to Azure Key Vault |
| Networking | Cloudflare Tunnel can be replaced with Azure-native ingress; no open inbound ports are required on the current setup |
| DNS | `gchd.unicef.org` CNAME will need to be updated to point to the new Azure endpoint |
