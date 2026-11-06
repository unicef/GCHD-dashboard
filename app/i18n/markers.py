"""Extraction marker for display text held as data (config, registries).

`N_` returns its argument unchanged: the text stays English where it is
defined and is translated with `tr()` where it is rendered. It exists so the
extractor finds the string. No Flask import, so config/ and scripts/ can use it.
"""


def N_(text):
    return text
