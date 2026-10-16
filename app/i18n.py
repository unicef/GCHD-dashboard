# =============================================================================
# i18n.py — UI translations for the six official UN languages
#
# English source text IS the lookup key (gettext style), so call sites read
# naturally — tr("Hazard Layers") — and a string with no translation yet simply
# falls back to English instead of breaking. Catalogs live in locales/<code>.json,
# one flat {english: translation} object per language.
#
# The active language comes from a cookie rather than a Dash Input: every
# layout and callback request carries it, so no callback needs an extra
# language argument. Switching language sets the cookie and reloads the page.
#
# Run `python i18n_check.py` after editing English strings to list keys that
# are missing from a catalog.
# =============================================================================

import json
import os
import re

from flask import has_request_context, request

# (code, native name) in the order shown in the language menu.
LANGUAGES = [
    ("en", "English"),
    ("fr", "Français"),
    ("es", "Español"),
    ("zh", "中文"),
    ("ru", "Русский"),
    ("ar", "العربية"),
]
LANG_CODES = [c for c, _ in LANGUAGES]
DEFAULT_LANG = "en"
RTL_LANGS = {"ar"}

LANG_COOKIE = "gchd_lang"
# Short-lived cookie set by the language switcher when the data-use notice was
# already dismissed, so the reload it triggers doesn't show the notice again.
SKIP_NOTICE_COOKIE = "gchd_skip_notice"

_LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locales")


def _load_catalogs():
    catalogs = {}
    for code in LANG_CODES:
        if code == DEFAULT_LANG:
            continue
        path = os.path.join(_LOCALE_DIR, f"{code}.json")
        try:
            with open(path, encoding="utf-8") as f:
                catalogs[code] = json.load(f)
        except FileNotFoundError:
            catalogs[code] = {}
    return catalogs


CATALOGS = _load_catalogs()


def current_lang():
    """Language for the current request; English outside a request (startup)."""
    if not has_request_context():
        return DEFAULT_LANG
    lang = request.cookies.get(LANG_COOKIE, DEFAULT_LANG)
    return lang if lang in LANG_CODES else DEFAULT_LANG


def is_rtl(lang=None):
    return (lang or current_lang()) in RTL_LANGS


def tr(text, **kwargs):
    """Translate `text` into the current language; `{name}` placeholders are
    filled from kwargs after translation, so translators can reorder them."""
    if not text:
        return text
    s = CATALOGS.get(current_lang(), {}).get(text) or text
    return s.format(**kwargs) if kwargs else s


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def trc(text, **parts):
    """Like tr(), but placeholders may be Dash components (e.g. html.Strong).
    Returns a children list, keeping the translated word order intact."""
    s = CATALOGS.get(current_lang(), {}).get(text) or text
    out, pos = [], 0
    for m in _PLACEHOLDER.finditer(s):
        if m.start() > pos:
            out.append(s[pos:m.start()])
        out.append(parts.get(m.group(1), m.group(0)))
        pos = m.end()
    if pos < len(s):
        out.append(s[pos:])
    return out
