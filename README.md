# UNICEF Global Child Hazard Database (GCHD) Dashboard

A web application for visualising global child exposure to climate and geophysical hazards, built for the UNICEF Children's Climate Risk Report (CCRR). It runs on Google Earth Engine for raster analysis and serves a Plotly Dash frontend via Gunicorn.

---

## Features

The app is organised by disaster-risk-management phase. A phase switch under the title selects the phase; the left rail shows that phase's tasks, and the views with several data sources show a segmented control at the top of the panel. Every view has its own URL (`/ew/<phase>/<view>[/<segment>]`), so it can be bookmarked and shared.

| Phase | Rail view | Segments / description |
|---|---|---|
| **Preparedness** | Visualization | **Hazard** (20 hazard layers), **Multi-hazard** (MHC and MHI indicators), **Exposure** (children under 18 exposed to each hazard topic) |
| | Pop analysis | Compute child exposure by country, province or district; supports custom GeoJSON upload and GEE asset paths |
| | Infra analysis | Correlate schools, health facilities and water points with hazard footprints and child population |
| **Early Warning** | Visualization | **Observed** (GPM IMERG, ERA5-Land, MODIS LST, FIRMS, Sentinel-5P) and **Forecast** (NOAA GFS, CAMS, or any pasted GEE Image/ImageCollection id) over a user-selected window |
| | Pop analysis | Child exposure to the observed or forecast layer in the shared Hazard card (picked in Visualization or right in the analysis form) |
| | Infra analysis | Facility exposure to the Hazard card's layer (same Voronoi catchments and 100 m population grid as Preparedness) |
| | Seasonal | El Niño seasonal rainfall and temperature outlook and children in areas with a dry or wet signal |
| **Damage Assessment** | — | Planned |
| All phases | AI | Gemini-powered assistant for natural language hazard queries (under development) |

### Hazard topics covered
River Flood · Coastal Flood · Tropical Storm · Drought · Heatwave · Extreme Heat · Fire · Sand & Dust Storm · Air Pollution · Malaria · Landslide · Earthquake · Volcanoes

---

## Project structure

```
hazard_database_app/
├── app/
│   ├── app.py              # Entry point (Gunicorn `app:server`); imports the modules below
│   ├── dash_app.py         # The Dash app instance (served under /ew)
│   ├── bootstrap.py        # GEE init and static data shared across sections
│   ├── config/             # Plain data, no GEE import (also read by scripts/)
│   │   ├── hazards.py      #   Hazard definitions, topics, colors, admin levels
│   │   ├── forecast.py     #   Observed / Forecast dataset catalog (ranges, thresholds, sources)
│   │   └── elnino.py       #   El Niño / SEAS5 seasonal outlook catalog
│   ├── core/               # Earth Engine and compute logic, no Dash
│   │   ├── gee.py          #   Tile URLs, exposure and facility computation
│   │   ├── forecast.py     #   Date-windowed loading, model-run selection, reduction, exposure
│   │   ├── elnino.py       #   El Niño outlook layers and exposure
│   │   └── ai.py           #   Google Gemini integration
│   ├── auth/               # Email OTP logic (otp.py) and the Flask login routes (routes.py)
│   ├── ui/                 # Shared UI: navigation registry, components, map, legend,
│   │                       #   threshold editors, info popover, xlsx builder, page layout
│   ├── sections/           # One module per section: layout + its callbacks
│   │                       #   (layers, analysis, infrastructure, early_warning, elnino, ai…)
│   ├── requirements.txt    # Python dependencies
│   ├── assets/             # Static files (CSS, images, favicon)
│   └── credentials/        # Secret files — NOT committed (see setup below)
│       ├── service_account.json
│       ├── gemini_api_key.txt
│       ├── smtp_user.txt
│       ├── smtp_pass.txt
│       └── flask_secret.txt  ← auto-generated on first run
├── docs/                   # Reference catalogs — documentation, never read at runtime
│   ├── Global Hazard Data catalog.xlsx    # The 20 static hazard layers
│   └── Forecast Data catalog.xlsx         # Observed/Forecast layers + rejected candidates
├── scripts/                # Offline tooling — not needed to deploy the app
└── nginx-pixel-aid.conf    # Nginx config reference (not used in current Cloudflare Tunnel setup)
```

`app/` holds only what the running server needs: code, static assets and credentials. Reference spreadsheets live in `docs/`, and anything that is run by hand lives in `scripts/`.

---

## Local setup

### 1. Clone the repository

```bash
git clone git@github.com:unicef/GCHD-dashboard.git
cd GCHD-dashboard
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r app/requirements.txt
```

### 4. Add credentials

Create the `app/credentials/` directory and add the following files:

| File | Description |
|---|---|
| `service_account.json` | GEE service account key (download from Google Cloud Console) |
| `gemini_api_key.txt` | Google Gemini API key (one line, no newline) |


```bash
mkdir -p app/credentials
# Copy your credential files into app/credentials/
```

The GEE service account must have access to the `projects/unicef-ccri` GEE assets.

### 5. Authenticate Google Earth Engine

The app authenticates using the service account automatically via `service_account.json`. No manual `earthengine authenticate` is required.

---

## Running locally

```bash
cd app
python app.py
```

The app will start on `http://localhost:8050`.

---

## Running in production (Gunicorn)

```bash
cd app
gunicorn app:server \
  --workers 4 \
  --worker-class gthread --threads 8 \
  --bind 0.0.0.0:8502 \
  --timeout 120 \
  --log-file gunicorn.log
```

Use threaded workers (`gthread`). A single click fires a few dozen small
callback requests, and a Compute holds its request for tens of seconds while
GEE works; with sync workers each of those occupies a whole worker, so other
requests queue behind them. Threads share the worker's memory, so this costs
no extra RAM. `GCHD_TIMING=1` logs each callback request's server time.

The production deployment uses a **Cloudflare Tunnel** to expose port `8502` at `gchd.pixel-aid.com` (aliased as `gchd.unicef.org`).

---

## Authentication

Access is restricted to `@unicef.org` email addresses via a one-time passcode (OTP) flow:

1. User enters their UNICEF email address
2. A 6-digit code is sent via SMTP
3. User enters the code to access the app
4. Session persists for the duration configured in `auth/otp.py`

---

## Design guidelines for new features

The core principle is to keep the app server as light as possible. It acts only as a thin coordinator — no heavy computation, no data storage, no file IO.

### 1. All raster processing happens in GEE

Any operation on raster data (thresholding, masking, zonal statistics, compositing) must be expressed as a GEE computation and returned as a tile URL or a scalar result. The app server constructs the GEE expression and hands a tile URL to the Leaflet map on the client — it never holds raster data in memory.

```
Client browser  ──tile URL──▶  GEE tile server
App server      ──builds GEE expression, returns URL──▶  client
```

### 2. No data IO on the app server

The app server must not read or write files at request time. If a feature requires pre-calculated data (lookup tables, pre-aggregated statistics, reference grids), that data must live as a GEE asset and be queried via the GEE API at runtime — not loaded from disk into server memory.

**Allowed:** `ee.FeatureCollection("projects/unicef-ccri/assets/...")` at request time  
**Not allowed:** `pd.read_csv(...)`, `open(...)`, or any file read inside a callback

### 3. Boundary files are handled client-side

When a user uploads a GeoJSON boundary, all parsing, validation, and geometry processing happens in the browser (see the `clientside_callback` in `sections/analysis.py`). Only the minimal stripped GeoJSON (geometry + one name field) is sent to the app server for the GEE exposure computation. Property tables, large feature collections, and display rendering stay on the client.

```
Client:  parse → validate → render on Leaflet → strip to geometry + name field
Server:  receives stripped GeoJSON → sends to GEE → returns scalar results
```

### Summary table

| Concern | Where it runs |
|---|---|
| Raster tile rendering | GEE tile server → client browser |
| Zonal statistics / exposure computation | GEE (triggered by app server) |
| Pre-calculated reference data | GEE assets |
| Boundary file parsing & display | Client browser |
| Boundary data sent to server | Geometry + one name field only |
| App server role | Build GEE expressions, return URLs or scalar results |

---

## Environment notes

- Python 3.12+
- Google Earth Engine Python API (`earthengine-api`)
- Population data: WorldPop under-18 global grid, 100 m resolution, 2025
- Admin boundaries: UNICEF GeoRepo (`projects/unicef-ccri/assets/global_boundary/`)
