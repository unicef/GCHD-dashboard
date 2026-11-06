"""Extract marked strings and report what each catalog is missing.

    cd app && python -m i18n.check             # update catalogs + report
    python -m i18n.check --dry-run             # report only, write nothing
    python -m i18n.check --missing fr          # print fr's untranslated msgids

Every literal argument of tr(), ntr() (singular + plural), trc() and N_() under
app/ is a key. Updating a catalog adds new keys (empty), marks entries whose
English changed as fuzzy (shown in English until re-checked) and drops keys no
longer used. Exits non-zero when a catalog has missing entries or mismatched
{placeholders}.
"""
import argparse
import ast
import os
import re
import sys

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po, write_po

from .languages import DEFAULT_LANG, DRAFT_COMMENT, LANG_CODES

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
KEYWORDS = {"tr": None, "ntr": (1, 2), "trc": None, "N_": None}
_PH = re.compile(r"\{(\w+)\}")


def _po(code):
    return os.path.join(LOCALE_DIR, code, "LC_MESSAGES", "messages.po")


def _literal(node):
    return node.value if (isinstance(node, ast.Constant)
                          and isinstance(node.value, str)) else None


def extract():
    """Literal str arguments of the marker calls. AST-based rather than
    pybabel's tokenizer, which also picks up strings inside expressions such
    as tr(cfg["label"])."""
    template = Catalog(project="GCHD", charset="utf-8")
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("i18n", "__pycache__", "assets")]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, APP_DIR).replace(os.sep, "/")
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                fn = getattr(node.func, "id", None)
                if fn not in KEYWORDS:
                    continue
                if fn == "ntr" and len(node.args) >= 2:
                    sg, pl = _literal(node.args[0]), _literal(node.args[1])
                    if sg and pl:
                        template.add((sg, pl), None, [(rel, node.lineno)])
                    continue
                msg = _literal(node.args[0])
                if msg:
                    template.add(msg, None, [(rel, node.lineno)])
    return template


def load(code):
    with open(_po(code), "rb") as f:
        return read_po(f, locale=code)


def save(code, catalog):
    catalog.fuzzy = False
    catalog.header_comment = (
        "# GCHD dashboard - {} UI catalog.\n"
        "# Entries marked '{}' were machine-assisted and await native review."
        .format(code, DRAFT_COMMENT))
    with open(_po(code), "wb") as f:
        write_po(f, catalog, width=100, sort_output=True, omit_header=False,
                 include_previous=True)


def _ids(msg):
    return msg.id if isinstance(msg.id, tuple) else (msg.id,)


def _strs(msg):
    return msg.string if isinstance(msg.string, tuple) else (msg.string,)


def report(code, catalog):
    missing, fuzzy, bad_ph, draft = [], [], [], 0
    for msg in catalog:
        if not msg.id:
            continue
        if msg.fuzzy:
            fuzzy.append(msg.id)
        if not all(_strs(msg)):
            missing.append(msg.id)
            continue
        if any(DRAFT_COMMENT in c for c in msg.user_comments):
            draft += 1
        want = set(_PH.findall(" ".join(_ids(msg))))
        for s in _strs(msg):
            if set(_PH.findall(s)) != want:
                bad_ph.append(msg.id)
                break
    total = sum(1 for m in catalog if m.id)
    done = total - len(missing)
    print(f"{code}: {total} keys | {len(missing)} missing | {len(fuzzy)} fuzzy | "
          f"{len(bad_ph)} placeholder mismatch | {draft} draft, "
          f"{done - draft} reviewed")
    for m in bad_ph[:10]:
        print(f"   placeholder mismatch: {m!r}")
    return missing, fuzzy, bad_ph


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--missing", metavar="LANG")
    args = ap.parse_args()

    template = extract()
    print(f"{len(template)} keys extracted from app/")
    failed = False
    for code in LANG_CODES:
        if code == DEFAULT_LANG:
            continue
        catalog = load(code)
        catalog.update(template, no_fuzzy_matching=True)
        catalog.obsolete.clear()
        if not args.dry_run:
            save(code, catalog)
        missing, fuzzy, bad_ph = report(code, catalog)
        failed |= bool(missing or bad_ph)
        if args.missing == code:
            for m in missing + fuzzy:
                print(m if isinstance(m, str) else " | ".join(m))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
