# =============================================================================
# app.py — UNICEF CCRI Global Child Hazard Database (Dash)
# =============================================================================
"""Entry point; Gunicorn serves `app:server`.

Each module registers its own callbacks on import: shared UI in ui/, one
module per section in sections/. ui.layout sets app.layout.
"""
from dash_app import app, server  # noqa: F401
import auth.routes  # noqa: F401
from sections import (  # noqa: F401
    navigation, embargo, layers, analysis, infrastructure, early_warning,
    elnino, elnino_export, ai,
)
from ui import popover, threshold, legend, map_view, layout  # noqa: F401

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8502)
