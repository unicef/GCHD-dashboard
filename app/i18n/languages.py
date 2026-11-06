"""Language registry. Adding a language = one entry here + its messages.po.

`status` is "reviewed" once a native speaker has checked the catalog; a
"draft" language is tagged as such in the menu, with English stated as the
authoritative version.
"""

LANGUAGES = [
    {"code": "en", "name": "English",  "rtl": False, "status": "reviewed"},
    {"code": "fr", "name": "Français", "rtl": False, "status": "draft"},
    {"code": "es", "name": "Español",  "rtl": False, "status": "draft"},
    {"code": "zh", "name": "中文",      "rtl": False, "status": "draft"},
    {"code": "ru", "name": "Русский",  "rtl": False, "status": "draft"},
    {"code": "ar", "name": "العربية",  "rtl": True,  "status": "draft"},
]
LANG_BY_CODE = {lang["code"]: lang for lang in LANGUAGES}
LANG_CODES = [lang["code"] for lang in LANGUAGES]
DEFAULT_LANG = "en"

LANG_COOKIE = "gchd_lang"
# Set by the switcher when the data-use notice was already dismissed, so the
# reload it triggers doesn't show the notice again.
SKIP_NOTICE_COOKIE = "gchd_skip_notice"

# Translator comment on every machine-drafted entry until a reviewer removes it.
DRAFT_COMMENT = "Draft (machine-assisted) - needs native review"
