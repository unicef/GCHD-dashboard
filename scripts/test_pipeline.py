#!/usr/bin/env python
"""
test_pipeline.py — tests for the infrastructure pipeline.

Two tiers:

  offline (default)  pure-logic tests on synthetic data: the output contract,
                     the {iso3}_{source}_{layer} naming rule, .env parsing,
                     country resolution, and the GEE-hostile-CSV guards.
                     No network, no credentials — safe to run anywhere.

  --live             adds tests that call the real APIs and check known counts.
                     Slower, needs a connection; API-key tests self-skip when
                     the key is absent from scripts/.env.

Usage:
    python scripts/test_pipeline.py
    python scripts/test_pipeline.py --live
    python scripts/test_pipeline.py --live -v
"""
import argparse
import csv
import io
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common
import prep_infra
import upload_infra
import fetch_mwater

LIVE = False


# ---------------------------------------------------------------------------
# Naming — the contract the GEE assets and the app's discovery both depend on
# ---------------------------------------------------------------------------
class TestAssetNaming(unittest.TestCase):

    def test_stem_shape(self):
        self.assertEqual(_common.asset_stem("mwi", "mwater", "schools"),
                         "mwi_mwater_schools")

    def test_stem_lowercases_iso3(self):
        """Asset ids are lowercase; an upper-case ISO3 must not leak through."""
        self.assertEqual(_common.asset_stem("MWI", "giga", "schools"),
                         "mwi_giga_schools")

    def test_every_generated_stem_is_uploadable(self):
        """Whatever prep_infra can emit, upload_infra must accept — otherwise a
        layer uploads under a name the app silently cannot discover."""
        for job in prep_infra.JOBS:
            stem = _common.asset_stem("mwi", job["source"], job["layer"])
            self.assertRegex(stem, upload_infra.STEM_RE,
                             f"prep emits {stem!r} but upload rejects it")

    def test_upload_rejects_malformed_stems(self):
        for bad in ["mwi_mwater_school",      # singular
                    "mwi_schools",            # no source
                    "malawi_mwater_schools",  # not ISO3
                    "mwi_unknown_schools",    # unknown source
                    "MWI_mwater_schools"]:    # upper case
            self.assertNotRegex(bad, upload_infra.STEM_RE,
                                f"{bad!r} should not be accepted")

    def test_layers_cover_the_three_facility_types(self):
        self.assertEqual(set(prep_infra.LAYERS),
                         {"schools", "health_facilities", "water_points"})


class TestAppDiscoveryContract(unittest.TestCase):
    """The app parses asset ids back into countries and layers. Its regex and
    labels must stay in step with what the pipeline writes — a mismatch means
    uploaded assets silently never appear in the dropdown."""

    @classmethod
    def setUpClass(cls):
        app_dir = os.path.join(_common.HERE, "..", "app")
        if not os.path.isdir(app_dir):
            raise unittest.SkipTest("app/ not present")
        sys.path.insert(0, app_dir)
        try:
            import gee_core
        except Exception as e:                      # ee missing in this env
            raise unittest.SkipTest(f"cannot import gee_core: {e}")
        cls.gee_core = gee_core

    def test_app_regex_accepts_every_stem_the_pipeline_writes(self):
        for job in prep_infra.JOBS:
            stem = _common.asset_stem("mwi", job["source"], job["layer"])
            self.assertRegex(stem, self.gee_core._INFRA_ASSET_RE,
                             f"app discovery would ignore {stem!r}")

    def test_app_has_a_label_for_every_layer(self):
        for layer in prep_infra.LAYERS:
            self.assertIn(layer, self.gee_core._LAYER_LABELS)

    def test_app_has_a_label_for_every_source(self):
        for source in prep_infra.SOURCES:
            self.assertIn(source, self.gee_core._SOURCE_LABELS,
                          f"source {source!r} would fall back to a title-cased "
                          "label; add it to _SOURCE_LABELS")

    def test_layer_labels_match_the_styling_keys(self):
        """Labels are '<Type> (<Source>)' and styling keys off '<Type>' — if
        they drift, layers render in the fallback colour."""
        from config import INFRA_TYPE_META
        for label in self.gee_core._LAYER_LABELS.values():
            self.assertIn(label, INFRA_TYPE_META)

    def test_unparseable_stems_are_rejected(self):
        for bad in ["eth_schools",            # legacy, no source
                    "mwi_mwater_school",      # singular
                    "adm0_chunked_500km"]:    # not an infra asset at all
            self.assertNotRegex(bad, self.gee_core._INFRA_ASSET_RE)


class TestUploadSelection(unittest.TestCase):
    """Uploading must be narrowable to specific countries/sources — a country's
    out/ folder usually holds several sources, and you rarely want them all."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root))
        for iso3, stems in {
            "zmb": ["zmb_giga_schools", "zmb_mwater_schools",
                    "zmb_mwater_water_points", "zmb_wpdx_water_points"],
            "mdg": ["mdg_giga_schools", "mdg_mwater_health_facilities"],
        }.items():
            d = os.path.join(self.root, iso3, "out")
            os.makedirs(d)
            for stem in stems:
                with io.open(os.path.join(d, stem + ".csv"), "w",
                             encoding="utf-8") as fh:
                    fh.write("facility_id,name,longitude,latitude\n1,A,1.0,2.0\n")

    def _collect(self, iso3s, **kw):
        found, bad = [], []
        for iso3 in iso3s:
            f, b = upload_infra.collect(
                [iso3], out_root=os.path.join(self.root, iso3, "out"), **kw)
            found += f
            bad += b
        return [s for s, _ in found], bad

    def test_country_filter(self):
        stems, _ = self._collect(["mdg"])
        self.assertEqual(sorted(stems),
                         ["mdg_giga_schools", "mdg_mwater_health_facilities"])

    def test_source_filter_across_countries(self):
        stems, _ = self._collect(["zmb", "mdg"], sources=["giga"])
        self.assertEqual(sorted(stems), ["mdg_giga_schools", "zmb_giga_schools"])

    def test_layer_filter(self):
        stems, _ = self._collect(["zmb"], layers=["water_points"])
        self.assertEqual(sorted(stems),
                         ["zmb_mwater_water_points", "zmb_wpdx_water_points"])

    def test_source_and_layer_combined(self):
        stems, _ = self._collect(["zmb"], sources=["mwater"],
                                 layers=["water_points"])
        self.assertEqual(stems, ["zmb_mwater_water_points"])

    def test_unfiltered_takes_everything(self):
        stems, _ = self._collect(["zmb"])
        self.assertEqual(len(stems), 4)

    def test_malformed_names_are_reported_not_uploaded(self):
        d = os.path.join(self.root, "zmb", "out")
        with io.open(os.path.join(d, "zmb_mwater_school.csv"), "w",
                     encoding="utf-8") as fh:
            fh.write("facility_id,name,longitude,latitude\n")
        stems, bad = self._collect(["zmb"])
        self.assertIn("zmb_mwater_school", [s for s, _ in bad])
        self.assertNotIn("zmb_mwater_school", stems)


# ---------------------------------------------------------------------------
# The output contract
# ---------------------------------------------------------------------------
class TestOutputContract(unittest.TestCase):

    def _prep(self, rows, job_overrides=None, filename="mwater_schools.csv"):
        job = {"source": "mwater", "layer": "schools", "file": filename,
               "id": ["facility_id"], "name": ["name"],
               "lon": ["longitude"], "lat": ["latitude"],
               "extras": {"subtype": ["subtype"]}}
        job.update(job_overrides or {})
        with tempfile.TemporaryDirectory() as d:
            pd.DataFrame(rows).to_csv(os.path.join(d, filename), index=False)
            return prep_infra.prep_one(job, d)

    def test_required_columns_present_and_ordered(self):
        out = self._prep([{"facility_id": "1", "name": "A",
                           "longitude": 34.0, "latitude": -13.0}])
        self.assertEqual(list(out.columns)[:4], prep_infra.SCHEMA)

    def test_implicit_fields_are_not_columns(self):
        """facility_type/source/iso3 live in the asset name, never in the data."""
        out = self._prep([{"facility_id": "1", "name": "A", "longitude": 1.0,
                           "latitude": 2.0, "iso3": "MWI",
                           "facility_type": "school", "source": "mwater"}])
        for col in ("iso3", "facility_type", "source"):
            self.assertNotIn(col, out.columns)

    def test_missing_names_are_empty_never_nan_text(self):
        """A blank name must be "", not the string "nan"/"None" that a naive
        astype(str) produces — those would show up verbatim in the UI."""
        out = self._prep([
            {"facility_id": "1", "name": None,  "longitude": 1.0, "latitude": 2.0},
            {"facility_id": "2", "name": "nan", "longitude": 3.0, "latitude": 4.0},
            {"facility_id": "3", "name": "Real", "longitude": 5.0, "latitude": 6.0},
        ])
        self.assertEqual(list(out["name"]), ["", "", "Real"])

    def test_rows_without_coordinates_are_dropped(self):
        out = self._prep([
            {"facility_id": "1", "name": "keep", "longitude": 1.0, "latitude": 2.0},
            {"facility_id": "2", "name": "nolon", "longitude": None, "latitude": 2.0},
            {"facility_id": "3", "name": "junk", "longitude": "abc", "latitude": 2.0},
        ])
        self.assertEqual(list(out["name"]), ["keep"])

    def test_coordinates_are_numeric(self):
        out = self._prep([{"facility_id": "1", "name": "A",
                           "longitude": "34.5", "latitude": "-13.25"}])
        self.assertEqual(out["longitude"].iloc[0], 34.5)
        self.assertEqual(out["latitude"].iloc[0], -13.25)

    def test_facility_id_falls_back_to_row_index(self):
        out = self._prep([{"name": "A", "longitude": 1.0, "latitude": 2.0}],
                         job_overrides={"id": ["nonexistent"]})
        self.assertEqual(out["facility_id"].iloc[0], "0")

    def test_extras_only_appear_when_the_source_has_them(self):
        with_extra = self._prep([{"facility_id": "1", "name": "A", "subtype": "x",
                                  "longitude": 1.0, "latitude": 2.0}])
        self.assertIn("subtype", with_extra.columns)
        without = self._prep([{"facility_id": "1", "name": "A",
                               "longitude": 1.0, "latitude": 2.0}])
        self.assertNotIn("subtype", without.columns)

    def test_missing_source_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            job = {"source": "giga", "layer": "schools", "file": "absent.csv",
                   "id": ["id"], "name": ["name"],
                   "lon": ["longitude"], "lat": ["latitude"]}
            self.assertIsNone(prep_infra.prep_one(job, d))


class TestGeeCsvSafety(unittest.TestCase):
    """GEE's CSV parser mis-splits quoted multi-line fields, which shifts the
    lon/lat columns and corrupts geometry. Names are the field most at risk."""

    def test_newlines_in_names_are_collapsed(self):
        s = prep_infra._clean_text(pd.Series(["Mpatawamilonde c\nCDSS",
                                              "Tab\tSchool", "  padded  "]))
        self.assertEqual(list(s), ["Mpatawamilonde c CDSS", "Tab School", "padded"])

    def test_written_csv_is_one_physical_line_per_record(self):
        df = pd.DataFrame({
            "facility_id": ["1", "2"],
            "name": prep_infra._clean_text(pd.Series(["A\nB", "C"])),
            "longitude": [1.0, 2.0], "latitude": [3.0, 4.0],
        })
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mwi_mwater_schools.csv")
            df.to_csv(path, index=False, encoding="utf-8")
            with io.open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text.rstrip("\n").count("\n"), len(df))
            with io.open(path, encoding="utf-8") as fh:
                back = list(csv.DictReader(fh))
            self.assertEqual(back[0]["longitude"], "1.0")


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------
class TestEnvLoading(unittest.TestCase):

    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_parses_comments_quotes_and_export(self):
        path = self._write('# comment\n\nA=1\nB="two"\n'
                           "C='three'\nexport D=4\nBAD_LINE\n")
        got = _common.load_env(path)
        self.assertEqual(got, {"A": "1", "B": "two", "C": "three", "D": "4"})

    def test_shell_environment_wins(self):
        """A real env var must not be clobbered by the file, so a one-off
        override on the command line still works."""
        os.environ["PIPELINE_TEST_KEY"] = "from-shell"
        self.addCleanup(os.environ.pop, "PIPELINE_TEST_KEY", None)
        _common.load_env(self._write("PIPELINE_TEST_KEY=from-file\n"))
        self.assertEqual(os.environ["PIPELINE_TEST_KEY"], "from-shell")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(_common.load_env("/nonexistent/.env"), {})


# ---------------------------------------------------------------------------
# Country resolution
# ---------------------------------------------------------------------------
class TestCountryResolution(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.countries = _common.adm0_countries()

    def test_adm0_table_covers_the_sovereign_states(self):
        with open(os.path.join(_common.HERE, "state_countries.json"),
                  encoding="utf-8") as fh:
            states = {u.split("_")[0] for u in json.load(fh)}
        missing = sorted(states - set(self.countries))
        self.assertEqual(missing, [], f"missing from adm0_countries.json: {missing}")

    def test_known_countries_resolve(self):
        self.assertEqual(_common.country_name("mwi", self.countries), "Malawi")
        self.assertEqual(_common.country_name("MWI", self.countries), "Malawi")

    def test_unknown_iso3_exits(self):
        with self.assertRaises(SystemExit):
            _common.country_name("zzz", self.countries)

    def test_every_state_maps_to_a_distinct_mwater_country(self):
        """Guards the alias table: a duplicate level0 would mean two ISO3s
        silently fetching the same country's facilities."""
        cache = os.path.join(_common.HERE, "mwater_countries.json")
        if not os.path.exists(cache):
            self.skipTest("mWater country cache not built yet")
        with open(cache, encoding="utf-8") as fh:
            mw = json.load(fh)

        seen, failures = {}, []
        for iso3 in sorted(self.countries):
            try:
                name, level0 = fetch_mwater._resolve_level0(iso3, mw)
            except SystemExit:
                failures.append(iso3)
                continue
            if level0 in seen:
                failures.append(f"{iso3} collides with {seen[level0]}")
            seen[level0] = iso3
        self.assertEqual(failures, [])

    def test_the_two_congos_are_not_confused(self):
        """COD/COG are substrings of each other — the classic fuzzy-match bug."""
        cache = os.path.join(_common.HERE, "mwater_countries.json")
        if not os.path.exists(cache):
            self.skipTest("mWater country cache not built yet")
        with open(cache, encoding="utf-8") as fh:
            mw = json.load(fh)
        cod = fetch_mwater._resolve_level0("cod", mw)
        cog = fetch_mwater._resolve_level0("cog", mw)
        self.assertNotEqual(cod[1], cog[1])
        self.assertIn("democratic", cod[0].lower())


# ---------------------------------------------------------------------------
# Live API tests
# ---------------------------------------------------------------------------
class TestLiveApis(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not LIVE:
            raise unittest.SkipTest("live tests disabled (pass --live)")
        import requests
        _common.load_env()
        cls.sess = requests.Session()
        cls.mw = fetch_mwater._load_countries(cls.sess)

    def test_mwater_offset_is_still_ignored(self):
        """The trap this pipeline is built around: if mWater ever honours
        `offset`, this fails and the batching comment should be revisited."""
        ids = fetch_mwater._country_region_ids(self.sess, "mwi", self.mw)[:200]
        flt = json.dumps({"admin_region": {"$in": ids}})
        a = self.sess.get(f"{fetch_mwater.API_ROOT}/entities/school",
                          params={"filter": flt, "limit": 5}, timeout=300).json()
        b = self.sess.get(f"{fetch_mwater.API_ROOT}/entities/school",
                          params={"filter": flt, "limit": 5, "offset": 5},
                          timeout=300).json()
        self.assertEqual([r["_id"] for r in a], [r["_id"] for r in b])

    def test_mwater_batching_is_size_independent(self):
        """Different batch sizes must return the same records — otherwise the
        country filter is losing or duplicating facilities at boundaries."""
        ids = fetch_mwater._country_region_ids(self.sess, "mwi", self.mw)
        original = fetch_mwater.REGION_BATCH
        try:
            fetch_mwater.REGION_BATCH = 200
            small = {r["_id"] for r in
                     fetch_mwater.fetch_entities(self.sess, "health_facility", ids)}
            fetch_mwater.REGION_BATCH = 2000
            large = {r["_id"] for r in
                     fetch_mwater.fetch_entities(self.sess, "health_facility", ids)}
        finally:
            fetch_mwater.REGION_BATCH = original
        self.assertEqual(small, large)
        self.assertGreater(len(small), 1000)

    def test_mwater_records_carry_names_and_coordinates(self):
        ids = fetch_mwater._country_region_ids(self.sess, "mwi", self.mw)
        recs = fetch_mwater.fetch_entities(self.sess, "health_facility", ids)
        df = fetch_mwater._entities_to_df(recs)
        self.assertEqual(len(df), len(recs))          # all have coordinates
        named = (df["name"].str.strip() != "").mean()
        self.assertGreater(named, 0.9, f"only {named:.1%} named")

    def test_wpdx_filters_by_iso3(self):
        import fetch_wpdx_water
        df = fetch_wpdx_water.fetch_all(self.sess, "mwi", page_size=1000,
                                        max_pages=1)
        self.assertFalse(df.empty)
        self.assertEqual(set(df["clean_country_id"].unique()), {"MWI"})

    def test_giga_key_works_if_present(self):
        if not os.environ.get("GIGA_SCHOOL_LOCATION_API_KEY", "").strip():
            self.skipTest("no GIGA_SCHOOL_LOCATION_API_KEY in scripts/.env")
        import fetch_giga_schools
        recs = fetch_giga_schools.fetch_all(
            self.sess, "mwi", os.environ["GIGA_SCHOOL_LOCATION_API_KEY"],
            size=100, max_pages=1)
        self.assertTrue(recs)

    def test_healthsites_key_works_if_present(self):
        if not os.environ.get("HEALTHSITES_API_KEY", "").strip():
            self.skipTest("no HEALTHSITES_API_KEY in scripts/.env")
        import fetch_healthsites
        try:
            feats = fetch_healthsites.fetch_all(
                self.sess, "Malawi", os.environ["HEALTHSITES_API_KEY"],
                max_pages=1)
        except SystemExit as e:
            # The fetcher exits on 401/403 so a bad key stops a real run early;
            # in a test that should read as a plain failure, not a crash.
            self.fail(f"healthsites rejected the key — regenerate it at "
                      f"https://healthsites.io/enrol ({e})")
        self.assertTrue(feats, "healthsites returned nothing — key may be invalid")


def main():
    global LIVE
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="also run tests that call the real APIs")
    ap.add_argument("-v", "--verbose", action="store_true")
    args, rest = ap.parse_known_args()
    LIVE = args.live

    if not args.live:
        print("Running offline tests only (pass --live for API tests).\n")

    unittest.main(argv=[sys.argv[0]] + rest,
                  verbosity=2 if args.verbose else 1)


if __name__ == "__main__":
    main()
