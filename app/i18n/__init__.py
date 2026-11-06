"""UI translations: the only module that knows about languages, cookies or catalogs.

English source text is the lookup key (gettext style), so call sites read
naturally (`tr("Hazard Layers")`) and an untranslated string falls back to
English. Catalogs are plain-text `locales/<code>/LC_MESSAGES/messages.po`,
read at startup straight from the .po (no compiled .mo, no deploy step).

The language comes from a cookie, not a Dash Input: every layout and callback
request carries it, so no callback takes a language argument. Switching
language sets the cookie and reloads the page (assets/i18n.js).

`python -m i18n.check` extracts every marked string and reports what each
catalog is missing.
"""
import gettext
import io
import os
import re
from functools import lru_cache

# Flask and Babel are imported where used, so `from i18n.markers import N_`
# (config/, scripts/) stays dependency-free.
from .languages import (  # noqa: F401  (re-exported)
    DEFAULT_LANG, LANG_BY_CODE, LANG_CODES, LANG_COOKIE, LANGUAGES,
    SKIP_NOTICE_COOKIE,
)
from .markers import N_  # noqa: F401

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")


def po_path(code):
    return os.path.join(LOCALE_DIR, code, "LC_MESSAGES", "messages.po")


@lru_cache(maxsize=None)
def _translations(code):
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po
    path = po_path(code)
    if not os.path.exists(path):
        return gettext.NullTranslations()
    with open(path, "rb") as f:
        catalog = read_po(f, locale=code)
    buf = io.BytesIO()
    # Fuzzy entries (English changed since translation) fall back to English.
    write_mo(buf, catalog, use_fuzzy=False)
    buf.seek(0)
    return gettext.GNUTranslations(buf)


_NULL = gettext.NullTranslations()


def current_lang():
    """Language of the current request; English outside a request (startup)."""
    from flask import has_request_context, request
    if not has_request_context():
        return DEFAULT_LANG
    lang = request.cookies.get(LANG_COOKIE, DEFAULT_LANG)
    return lang if lang in LANG_BY_CODE else DEFAULT_LANG


def is_rtl(lang=None):
    return LANG_BY_CODE[lang or current_lang()]["rtl"]


def _t():
    lang = current_lang()
    return _NULL if lang == DEFAULT_LANG else _translations(lang)


def _fill(s, fallback, kw):
    if not kw:
        return s
    try:
        return s.format(**kw)
    except (KeyError, IndexError, ValueError):
        return fallback.format(**kw)


def tr(text, **kw):
    """Translate `text`; `{name}` placeholders are filled after translation,
    so translators can reorder them."""
    if not text:
        return text
    return _fill(_t().gettext(text), text, kw)


def ntr(singular, plural, n, **kw):
    """Plural-aware tr(). `{n}` is filled with the locale-formatted count
    unless given explicitly."""
    kw.setdefault("n", fmt_int(n))
    english = singular if n == 1 else plural
    return _fill(_t().ngettext(singular, plural, n), english, kw)


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def trc(text, **parts):
    """tr() whose placeholders may be Dash components (e.g. html.Strong).
    Returns a children list, keeping the translated word order."""
    s = _t().gettext(text)
    out, pos = [], 0
    for m in _PLACEHOLDER.finditer(s):
        if m.start() > pos:
            out.append(s[pos:m.start()])
        out.append(parts.get(m.group(1), m.group(0)))
        pos = m.end()
    if pos < len(s):
        out.append(s[pos:])
    return out


_JS_KEYS = []


def js_strings(*texts):
    """Register text a clientside callback shows (wrap each in N_ so it is
    extracted). Shipped to the browser in store-i18n; read with gchdT()."""
    _JS_KEYS.extend(t for t in texts if t not in _JS_KEYS)


def js_catalog():
    return {"lang": current_lang(), "rtl": is_rtl(),
            "strings": {k: tr(k) for k in _JS_KEYS}}


@lru_cache(maxsize=None)
def _locale(code):
    from babel import Locale
    return Locale.parse(code)


# Western digits everywhere: UN Arabic documents and every figure in the
# data use them, and mixing digit systems between UI and exports misleads.
def fmt_int(n):
    from babel.numbers import format_decimal
    return format_decimal(int(round(n or 0)), format="#,##0",
                          locale=_locale(current_lang()), numbering_system="latn")


def fmt_num(x, digits=1):
    from babel.numbers import format_decimal
    pattern = "#,##0." + "0" * digits if digits else "#,##0"
    return format_decimal(x or 0, format=pattern,
                          locale=_locale(current_lang()), numbering_system="latn")


def fmt_pct(pct, digits=1):
    """`pct` is a percentage (12.3 -> "12.3%", "12,3 %" in French)."""
    from babel.numbers import format_percent
    loc = _locale(current_lang())
    pattern = loc.percent_formats[None].pattern
    if digits:
        pattern = re.sub(r"0(?=[^0]*$)", "0." + "0" * digits, pattern, count=1)
    return format_percent((pct or 0) / 100.0, format=pattern, locale=loc,
                          numbering_system="latn")


def fmt_month(d):
    """'October 2026' / '2026年10月' / 'octobre 2026'."""
    from babel.dates import format_skeleton
    return format_skeleton("yMMMM", d, locale=_locale(current_lang()))


def fmt_month_range(a, b):
    """'Oct–Dec 2026' / '2026年10月至12月' — the locale's own interval form."""
    if current_lang() == DEFAULT_LANG:
        left = a.strftime("%b") if a.year == b.year else a.strftime("%b %Y")
        return f"{left}–{b.strftime('%b %Y')}"
    from babel.dates import format_interval
    return format_interval(a, b, "yMMM", locale=_locale(current_lang()))


def fmt_day(d):
    """'04 Nov 2026' / '2026年11月4日' / '4 nov. 2026'."""
    if current_lang() == DEFAULT_LANG:
        return d.strftime("%d %b %Y")
    from babel.dates import format_skeleton
    return format_skeleton("yMMMd", d, locale=_locale(current_lang()))


def fmt_day_range(a, b):
    """'01 – 07 Nov 2026' in the locale's own interval form."""
    if a == b:
        return fmt_day(a)
    if current_lang() == DEFAULT_LANG:
        left = a.strftime("%d %b" if a.year == b.year else "%d %b %Y")
        return f"{left} – {b.strftime('%d %b %Y')}"
    from babel.dates import format_interval
    return format_interval(a, b, "yMMMd", locale=_locale(current_lang()))


def fmt_date(d, fmt="medium"):
    from babel.dates import format_date
    return format_date(d, format=fmt, locale=_locale(current_lang()))
