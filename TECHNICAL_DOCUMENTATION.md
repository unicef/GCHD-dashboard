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

### Observed & Forecast subsystem

Two tabs, **Observed** and **Forecast**, share one dataset registry (`forecast_config.py`) and one exposure module (`forecast_core.py`); the `kind` field decides which tab lists a dataset. Each tab has its own controls and resets when entered. Design decisions:

1. **Kept out of the static pipeline.** `build_core_images()` is cached once per server lifetime, so date-parameterised layers would poison it. `forecast_core` builds its own image per `(dataset, window, reducer)` behind a short TTL.
2. **One model run per forecast.** GFS and CAMS hold overlapping runs. `_select_run` takes the latest run initialised at or before the window start, and images are filtered by valid time (`run` in config). The run is shown in the results and exported as `model_run`. GFS rainfall keeps only 6-hour block ends (it accumulates within each block).
3. **Daily rainfall.** GFS and IMERG offer *Wettest day* and *Average daily total* (mm/day, UTC days). A day's total is its mean step × `steps_per_day`, so missing IMERG half-hours do not lower it.
4. **Shared scales.** Same-variable datasets share one range and threshold set (`RAINFALL`, `AIR_TEMPERATURE`). Each reducer has its own default threshold with a source (`thresholds`), shown under the slider; ranges come from a populated-land probe (`range_source`). Only international sources are cited.
5. **Exposure on the 1 km population grid.** `compute_forecast_exposure` reduces the 1 km child-population grid (rescaled to the 100 m totals, within 0.05%) with the same `chunk_asset` / `reduceRegions` pattern as `gee_core.compute_exposure`. There is no ADM0 fast path, so **thresholds take effect at ADM0 here**, unlike the Analysis tab.

**Runtime dataset flags.** An optional GEE table (`projects/unicef-ccri/assets/config/forecast_datasets`, 10-minute TTL) overrides the `active` flags without a redeploy; a read failure falls back to the config. Managed with `scripts/forecast_flags.py`.

**Failure handling.** An empty window raises `ForecastError` instead of reporting zero exposed; the image count is shown in the results and export. Per-dataset transforms handle units (Sentinel-5P mol/m², MODIS scaled Kelvin, IMERG mm/hr, CAMS kg/m³) and derived bands (GFS wind from U/V, FIRMS fire days from mask presence).

**Metadata.** `docs/Forecast Data catalog.xlsx` is generated by `scripts/build_forecast_catalog.py`, which reads every config-held field from `forecast_config.py`. Each dataset's (i) panel shows resolution from `_META`.

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

### Observed & Forecast datasets

Read live from the **public GEE catalog** and reduced over a user-selected window, unlike the fixed assets above. Catalogued in `app/forecast_config.py`; full metadata in `docs/Forecast Data catalog.xlsx`.

| Dataset | Collection | Topic | Tab | Resolution | Latency |
|---|---|---|---|---|---|
| Rainfall forecast | `NOAA/GFS0P25` | Rainfall | Forecast | 0.25° · hourly, 3-hourly after 5 d | ~5 h |
| Wind speed forecast | `NOAA/GFS0P25` (derived U/V) | Tropical Storm | Forecast | 0.25° · hourly, 3-hourly after 5 d | ~5 h |
| Air temperature forecast | `NOAA/GFS0P25` | Extreme Heat | Forecast | 0.25° · hourly, 3-hourly after 5 d | ~5 h |
| PM2.5 / PM10 / dust forecast | `ECMWF/CAMS/NRT` | Air Pollution; Sand & Dust Storm | Forecast | 0.4° · 3-hourly | ~0 |
| Observed rainfall | `NASA/GPM_L3/IMERG_V07` | Rainfall | Observed | 0.1° · 30 min | ~2 d |
| Observed air temperature | `ECMWF/ERA5_LAND/HOURLY` | Extreme Heat | Observed | 0.1° · hourly | ~6 d |
| Land surface temperature | `MODIS/061/MOD11A1` | Extreme Heat | Observed | 1 km · daily | ~2 d |
| Active fire brightness / fire days | `FIRMS` | Fire | Observed | 1 km · daily | ~2 d |
| Absorbing aerosol index | `COPERNICUS/S5P/NRTI/L3_AER_AI` | Sand & Dust Storm | Observed | ~5.5 × 3.5 km · daily | ~3 h |

Inactive (kept in config, superseded by CAMS): Sentinel-5P NO₂ / SO₂ / CO / O₃. Rejected candidates and their reasons are in the `DROPPED` block of `forecast_config.py`.

### Population data

- **WorldPop** global gridded children population (under 18), 2025, 100 m resolution — used for all exposure computations (Observed/Forecast use its 1 km aggregate).

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
