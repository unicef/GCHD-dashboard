"""The Dash app instance, served under the /ew subpath."""
import dash
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
