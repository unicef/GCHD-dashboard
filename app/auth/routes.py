"""Flask session secret, OTP login page and the request guard."""
import datetime as _dt
import os
import secrets as _secrets
from auth.otp import request_otp, SESSION_HOURS, verify_otp
from flask import redirect as _fredirect, request as _freq, session as _fsess

from dash_app import app, _BASE_PATHNAME


# Scope the session cookie to /ew and give it its own name so it can never be
# confused with the main instance's session cookie on the same domain.
app.server.config["SESSION_COOKIE_PATH"] = _BASE_PATHNAME.rstrip("/") + "/"
app.server.config["SESSION_COOKIE_NAME"] = "gchd_ew_session"

_key_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "credentials", "flask_secret.txt")
if os.path.exists(_key_path):
    app.server.secret_key = open(_key_path).read().strip()
else:
    _key = _secrets.token_hex(32)
    open(_key_path, "w").write(_key)
    app.server.secret_key = _key

_PUBLIC_PATHS = tuple(_BASE_PATHNAME.rstrip("/") + p
                      for p in ("/login", "/assets/", "/_dash-component-suites/", "/favicon.ico"))


def _render_login(step, email, error):
    _CSS = (
        "body{display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;margin:0;background:var(--bg)}"
        ".lc{background:var(--panel);border:1px solid var(--border2);"
        "border-radius:10px;padding:36px 32px;width:340px;"
        "box-shadow:0 4px 24px rgba(0,0,0,.3)}"
        ".lc-icon{color:var(--cyan);font-size:2rem;text-align:center;margin-bottom:6px}"
        ".lc-title{font-size:.88rem;font-weight:700;color:var(--hi);text-align:center;"
        "text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}"
        ".lc-sub{font-size:.74rem;color:var(--lo);text-align:center;margin-bottom:28px}"
        "label{font-size:.7rem;color:var(--lo);text-transform:uppercase;"
        "letter-spacing:.06em;display:block;margin-bottom:5px}"
        "input{width:100%;box-sizing:border-box;padding:9px 12px;font-size:.82rem;"
        "border:1px solid var(--border2);border-radius:6px;"
        "background:var(--panel-h);color:var(--hi);"
        "font-family:'Source Sans Pro',sans-serif;outline:none;margin-bottom:18px}"
        "input:focus{border-color:var(--cyan)}"
        ".lc-btn{width:100%;padding:10px;font-size:.84rem;font-weight:600;"
        "background:var(--cyan);color:#fff;border:none;"
        "border-radius:6px;cursor:pointer;font-family:inherit}"
        ".lc-btn:hover{opacity:.85}"
        ".lc-err{font-size:.74rem;color:var(--red);margin-bottom:12px;min-height:16px}"
        ".lc-hint{font-size:.7rem;color:var(--lo);text-align:center;margin-top:18px}"
        ".lc-hint a{color:var(--cyan);text-decoration:none}"
    )
    safe_email = (email or "").replace('"', "").replace("<", "").replace(">", "")
    if step == "email":
        form_inner = (
            '<label>UNICEF Email Address</label>'
            '<input type="email" name="email" placeholder="yourname@unicef.org"'
            ' value="' + safe_email + '" autofocus required>'
        )
        btn_label = "Send Verification Code"
        hint = "A 6-digit code will be sent to your inbox."
    else:
        form_inner = (
            '<div style="font-size:.74rem;color:var(--mid);margin-bottom:16px">'
            "Code sent to <strong>" + safe_email + "</strong></div>"
            '<input type="hidden" name="email" value="' + safe_email + '">'
            "<label>6-Digit Verification Code</label>"
            '<input type="text" name="code" placeholder="000000" maxlength="6"'
            ' autofocus required inputmode="numeric"'
            ' style="letter-spacing:.3em;font-size:1.1rem;text-align:center">'
        )
        btn_label = "Sign In"
        hint = f'<a href="{_BASE_PATHNAME}login">Use a different email</a>'
    err_html = '<div class="lc-err">' + error + "</div>" if error else '<div class="lc-err"></div>'
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>UNICEF Hazard DB — Sign In</title>"
        f'<link rel="stylesheet" href="{_BASE_PATHNAME}assets/style.css">'
        "<style>" + _CSS + "</style>"
        "</head><body>"
        '<div class="lc">'
        '<div class="lc-icon"><i class="bi bi-globe2"></i></div>'
        '<div class="lc-title">UNICEF Global Child Hazard Database</div>'
        '<div class="lc-sub">Staff access only · @unicef.org required</div>'
        f'<form method="POST" action="{_BASE_PATHNAME}login">'
        + form_inner + err_html
        + '<button type="submit" class="lc-btn">' + btn_label + "</button>"
        "</form>"
        '<div class="lc-hint">' + hint + "</div>"
        "</div></body></html>"
    )


# @app.server.before_request
# def _require_login():
#     if any(_freq.path.startswith(p) for p in _PUBLIC_PATHS):
#         return
#     if not _fsess.get("authed"):
#         return _fredirect(_BASE_PATHNAME + "login")


@app.server.route(_BASE_PATHNAME + "login", methods=["GET", "POST"])
def _login_page():
    error = ""
    step  = "email"
    email = ""
    if _freq.method == "POST":
        email = _freq.form.get("email", "").strip().lower()
        code  = _freq.form.get("code", "").strip()
        if code:
            if verify_otp(email, code):
                _fsess["authed"] = True
                _fsess.permanent = True
                app.server.permanent_session_lifetime = _dt.timedelta(hours=SESSION_HOURS)
                return _fredirect(_BASE_PATHNAME)
            error = "Incorrect or expired code. Please try again."
            step  = "code"
        else:
            ok, msg = request_otp(email)
            if ok:
                step = "code"
            else:
                error = msg or "Failed to send code."
    return _render_login(step, email, error)
