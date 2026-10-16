# =============================================================================
# i18n_check.py — report translation keys missing from (or stale in) locales/
#
#   python i18n_check.py            # summary per language
#   python i18n_check.py --keys     # dump every expected key as JSON
#
# Keys are English source strings: every literal tr()/trc() argument in app.py,
# plus the display text that lives as data in config.py (topics, hazard name
# words, descriptions, units, population labels, admin levels). Exits non-zero
# when a catalog is missing keys or has mismatched {placeholders}.
# =============================================================================

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config  # noqa: E402  (no GEE imports — loads instantly)
from i18n import CATALOGS, LANG_CODES, DEFAULT_LANG  # noqa: E402


def _app_keys():
    tree = ast.parse(open(os.path.join(HERE, "app.py"), encoding="utf-8").read())
    keys = set()
    for node in ast.walk(tree):
        # tr("...") / trc("...")
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("tr", "trc")
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
        # TABS entries and _JS_STRINGS are translated where they are rendered.
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) in ("TABS", "_JS_STRINGS") for t in node.targets):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Dict):
                    for k, v in zip(sub.keys, sub.values):
                        if (isinstance(k, ast.Constant) and k.value in ("label", "title", "desc")
                                and isinstance(v, ast.Constant)):
                            keys.add(v.value)
                elif isinstance(sub, ast.List):
                    keys.update(e.value for e in sub.elts
                                if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return keys


def _config_keys():
    keys = set(config.HAZARD_TOPICS) | set(config.ADMIN_DATA)
    keys |= {"Multi Hazard Count", "Multi Hazard Intensity"}
    keys |= set(config.INFRA_TYPE_META)
    for name in config.HAZARD_NAME_WORDS:
        desc, _ = config.hazard_name_words(name)
        keys.add(" ".join(desc))
    for info in config.HAZARD_INFO.values():
        for field in ("description", "units"):
            if info.get(field):
                keys.add(info[field])
    for p in config.POPULATION_LAYERS:
        keys.update((p["label"], p["group"]))
    return keys


def expected_keys():
    return sorted(_app_keys() | _config_keys())


_PH = re.compile(r"\{(\w+)\}")


def main():
    keys = expected_keys()
    if "--keys" in sys.argv:
        print(json.dumps(keys, ensure_ascii=False, indent=2))
        return 0
    ok = True
    print(f"{len(keys)} translatable strings")
    for code in LANG_CODES:
        if code == DEFAULT_LANG:
            continue
        cat = CATALOGS.get(code, {})
        missing = [k for k in keys if not cat.get(k)]
        stale   = [k for k in cat if k not in keys]
        bad_ph  = [k for k in keys if cat.get(k)
                   and set(_PH.findall(k)) != set(_PH.findall(cat[k]))]
        print(f"  {code}: {len(keys) - len(missing)}/{len(keys)} translated"
              f", {len(stale)} stale, {len(bad_ph)} placeholder mismatches")
        for k in missing:
            print(f"      missing: {k[:90]!r}")
        for k in bad_ph:
            print(f"      placeholders differ: {k[:90]!r}")
        for k in stale:
            print(f"      stale (English text changed or removed?): {k[:90]!r}")
        ok = ok and not missing and not bad_ph
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
