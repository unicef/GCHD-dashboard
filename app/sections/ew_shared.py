"""Early Warning hazard helpers shared by EW sections."""
from config.forecast import (
    CUSTOM_NAME, CUSTOM_PALETTE, DAILY_REDUCERS, FORECAST_MAP, REDUCERS,
)
from core.forecast import build_live_hazard, forecast_run_label
from i18n import N_, tr

from ui.components import _as_float, _fmt_window


def _ew_source(cfg):
    if cfg["is_custom"]:
        return N_("Custom layer")
    return N_("Forecast") if cfg.get("kind") == "forecast" else N_("Observed")


# ── EW Infra analysis: the live hazard ─────────────────────────────────────

# Live (observed/forecast) footprint on the facility map and result rows; the
# static topics keep their TOPIC_COLORS.
LIVE_HAZARD_COLOR = "#d7301f"


def _live_spec(dataset, asset, start, end, reducer, threshold):
    """JSON-safe description of the EW Visualization layer, or None. Stored in
    the infra viz/pending stores so phase 2 and the tile reveal can rebuild
    the same image."""
    cfg = _forecast_cfg(dataset, asset)
    if not cfg or not start or not end or not reducer:
        return None
    return {"name": cfg["name"], "label": cfg["label"],
            "source": _ew_source(cfg), "units": cfg.get("units", ""),
            "start": start[:10], "end": end[:10], "reducer": reducer,
            "threshold": _as_float(threshold),
            "custom_id": cfg.get("custom_id"),
            "custom_band": cfg.get("custom_band")}


def _build_live(spec):
    live, _n = build_live_hazard(
        spec["name"], spec["start"], spec["end"], spec["reducer"],
        spec["threshold"], custom_id=spec.get("custom_id"),
        custom_band=spec.get("custom_band"))
    return live


def _live_summary(spec):
    units = f" {tr(spec['units'])}" if spec.get("units") else ""
    return tr("Hazard: {label} ({source}), {window}, {aggregation}, exposed "
              "where value > {threshold}",
              label=tr(spec["label"]), source=tr(spec["source"]).lower(),
              window=_fmt_window(spec["start"], spec["end"]),
              aggregation=tr(REDUCERS.get(spec["reducer"], spec["reducer"])).lower(),
              threshold=f"{spec['threshold']:g}{units}")


def _live_provenance(spec):
    run = (forecast_run_label(spec["name"], spec["start"])
           if spec.get("name") else None)
    lines = [_live_summary(spec)]
    if run:
        lines.append(tr("Model run: {run}", run=run))
    lines.append(tr("Children counted on the 100 m population grid, as in "
                    "Preparedness infrastructure analysis."))
    return lines


# ===========================================================================
# Forecast & Live tab
#
# Structurally parallel to the Analysis tab, with two deliberate differences:
#   * every layer is parameterised by a date window, so nothing is precomputed;
#   * there is no ADM0 fast path, so the threshold applies at every level.
# ===========================================================================

def _forecast_cfg(dataset, asset):
    """Resolve the active selection to a config-shaped dict, whether it came
    from the catalog dropdown or the pasted-asset box. Returns None if neither
    is ready."""
    if asset and asset.get("asset_id"):
        return {
            "name": CUSTOM_NAME, "label": asset["asset_id"].split("/")[-1],
            "units": "", "threshold": 0, "min": 0, "max": 100,
            "reducers": [r for r in REDUCERS if r not in DAILY_REDUCERS],
            "palette": CUSTOM_PALETTE,
            "topic": None, "kind": "nrt", "note": "",
            "custom_id": asset["asset_id"], "custom_band": asset.get("band"),
            "is_custom": True,
        }
    if dataset and dataset in FORECAST_MAP:
        cfg = dict(FORECAST_MAP[dataset])
        cfg["custom_id"] = None
        cfg["custom_band"] = None
        cfg["is_custom"] = False
        return cfg
    return None
