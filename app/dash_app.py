"""The Dash app instance, served under the /ew subpath."""
import os
import time

import dash
import flask
from werkzeug.middleware.proxy_fix import ProxyFix


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
# This instance is served under the /ew subpath on the shared gchd.unicef.org
# domain (alongside the main instance at the root path) — see TECHNICAL_DOCUMENTATION.md.
_BASE_PATHNAME = "/ew/"

app = dash.Dash(__name__, suppress_callback_exceptions=True, url_base_pathname=_BASE_PATHNAME)
app.title = "UNICEF GCHD — Global Child Hazard Database (EW)"
server = app.server
app.server.wsgi_app = ProxyFix(app.server.wsgi_app, x_for=1, x_proto=1, x_host=1)

# GCHD_TIMING=1 logs every callback request's server time (diagnostics only).
if os.environ.get("GCHD_TIMING"):
    @server.before_request
    def _timing_start():
        flask.g.t0 = time.perf_counter()

    @server.after_request
    def _timing_log(resp):
        if flask.request.path.endswith("_dash-update-component"):
            out = (flask.request.get_json(silent=True) or {}).get("output", "")
            ms = (time.perf_counter() - flask.g.t0) * 1000
            print(f"[timing] {ms:7.0f} ms {resp.status_code} {out[:100]}", flush=True)
        return resp
