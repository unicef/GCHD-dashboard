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
│                        User Browser                          │
│   Plotly Dash (React) + Leaflet map                          │
│   - Renders tile layers from GEE tile server                 │
│   - Parses and displays uploaded GeoJSON client-side         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Cloudflare Network                              │
│   - SSL/TLS termination                                      │
│   - DDoS protection                                          │
│   - DNS: gchd.unicef.org → CNAME → gchd.pixel-aid.com       │
└──────────────────────┬──────────────────────────────────────┘
                       │ Cloudflare Tunnel (encrypted)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│     GCP VM: instance-20260512-205344                         │
│     Region: us-west1-b  │  Project: unicef-ccri              │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  cloudflared (Cloudflare Tunnel daemon)              │  │
│   │  Forwards traffic → localhost:8502                   │  │
│   └──────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│   ┌──────────────────────▼───────────────────────────────┐  │
│   │  Gunicorn WSGI server (port 8502, 2 workers)         │  │
│   │  Managed by systemd (hazard-app.service)             │  │
│   │                                                      │  │
│   │  Plotly Dash Application (Python 3.12)               │  │
│   │  ├── Flask  (session management)                     │  │
│   │  ├── dash-leaflet  (interactive map component)       │  │
│   │  └── GEE Python API  (tile URL generation,          │  │
│   │                        zonal statistics)             │  │
│   └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
┌──────────────┐ ┌──────────┐ ┌──────────┐
│ Google Earth │ │  Google  │ │Cloudflare│
│   Engine     │ │  Gemini  │ │   Zero   │
│ (raster data │ │   API    │ │  Trust   │
│  & analysis) │ │  (AI)    │ │  (auth)  │
└──────────────┘ └──────────┘ └──────────┘
```

### Key architectural principles

- **No raster data on the app server.** All raster processing (thresholding, masking, zonal statistics) runs in Google Earth Engine. The app server only constructs GEE expressions and returns tile URLs to the browser.
- **No file IO at request time.** All reference data lives as GEE assets (`projects/unicef-ccri/assets/`). No CSV, raster, or vector files are loaded from disk during a request.
- **Boundary files handled client-side.** Uploaded GeoJSON is parsed, validated, and rendered on the browser. Only the geometry and a single name field are sent to the app server for GEE computation.

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
| AI assistant | Google Gemini API | Natural language hazard queries (under development) |
| Access control | Cloudflare Zero Trust | Email-based identity verification (@unicef.org) |

### Infrastructure

| Component | Details |
|---|---|
| Compute | GCP VM `instance-20260512-205344`, zone `us-west1-b`, GCP project `unicef-ccri` |
| Tunnel / ingress | Cloudflare Tunnel (no open inbound ports on the VM) |
| DNS | `gchd.unicef.org` CNAME → `gchd.pixel-aid.com` (Cloudflare-managed) |
| SSL | Managed by Cloudflare (automatic, no certificates on VM) |
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
| GCP VM type | To be confirmed — standard GCP Compute Engine instance |
| VM region | `us-west1-b` (Oregon) |
| Persistent disk | `/mnt/pixel_aid_disk1` (mounted external disk) |
| GEE quota | Standard GEE service account under `unicef-ccri` GCP project |
| Concurrent users | ~46 registered; light concurrent load (GEE handles compute) |
| Gunicorn workers | 2 |

---

## 7. Source code

The application source code is hosted in a private GitHub repository:

**https://github.com/unicef/GCHD-dashboard**

Credentials and secrets are excluded from the repository and managed as files on the VM under `app/credentials/`.

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
