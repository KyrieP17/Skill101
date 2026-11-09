"""Tariff registry (D-026): primary-source measures, validation, and the read-only timeline check."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for entry in (PROJECT_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import test_features  # noqa: E402  (module import, so its tests are not collected twice)
from usitc_pipeline import cli  # noqa: E402
from usitc_pipeline.registry import (  # noqa: E402
    COVERAGE_COLUMNS,
    MEASURE_COLUMNS,
    SOURCE_COLUMNS,
    RegistryError,
    check_timeline,
    heading_ranges,
    load_registry,
    origins_overlap,
)
from usitc_pipeline.timeline import is_primary_source  # noqa: E402

SHA = "a" * 64
SOURCES = [
    {"source_id": "fr-1", "kind": "federal_register", "citation": "1 FR 1", "title": "t", "document_number": "x",
     "publication_date": "2025-01-02", "signing_date": "", "url": "https://www.federalregister.gov/d/1",
     "sha256": SHA, "retrieved": "2026-11-08", "notes": ""},
    {"source_id": "hts-1", "kind": "hts_archive", "citation": "HTS ch99", "title": "t", "document_number": "",
     "publication_date": "", "signing_date": "", "url": "https://hts.usitc.gov/reststop/file?release=x",
     "sha256": SHA, "retrieved": "2026-11-08", "notes": ""},
]


def measure(measure_id: str, day: str, headings: str, origins: str, **extra: str) -> dict[str, str]:
    row = {"measure_id": measure_id, "date": day, "date_type": "effective", "authority": "IEEPA", "headings": headings,
           "change": "+10%", "origins": origins, "products": "all", "sources": "fr-1", "hts_note": "",
           "status": "verified", "notes": ""}
    row.update(extra)
    return row


MEASURES = [
    measure("old-list", "2019-09-01", "9903.88.15", "China", products="list"),
    measure("china-duty", "2025-02-04", "9903.01.20", "China; Hong Kong"),
    measure("baseline", "2025-04-05", "9903.01.25", "All origins"),
    measure("exception", "2025-04-11", "9903.01.32", "All origins", products="list", date_type="signed",
            status="待核对", notes="start date not in the sources"),
    measure("others", "2025-08-07", "9903.02.02-9903.02.73", "All origins except China"),
    measure("chips", "2026-01-15", "9903.79.01", "All origins", products="list"),
    measure("surcharge", "2026-02-24", "9903.03.01", "All origins"),
]
COVERAGE = [
    {"measure_id": "old-list", "hts": "8507.60", "coverage": "covered", "evidence": "hts-1 note 20", "status": "verified", "notes": ""},
    {"measure_id": "exception", "hts": "8517.13", "coverage": "covered", "evidence": "hts-1 note 2(v)", "status": "verified", "notes": ""},
    {"measure_id": "baseline", "hts": "8517.13", "coverage": "exempt", "evidence": "hts-1 note 2(v)", "status": "verified", "notes": ""},
    {"measure_id": "chips", "hts": "8517.13", "coverage": "not_covered", "evidence": "hts-1 note 39", "status": "verified", "notes": ""},
]


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def timeline_row(day: str, measure_text: str, origins: str, layer: str = "country") -> dict[str, str]:
    return {"date": day, "date_type": "effective", "measure": measure_text, "origins": origins, "layer": layer}


class ParsingTests(unittest.TestCase):
    def test_heading_ranges(self) -> None:
        self.assertEqual(heading_ranges("9903.01.43–.62; 9903.02.02-9903.02.73"), [(143, 162), (202, 273)])
        self.assertEqual(heading_ranges("IEEPA fentanyl duty (9903.01.20)"), [(120, 120)])
        self.assertEqual(heading_ranges("9903.01.20 - 2025-03-04"), [(120, 120)])  # a date is not a range end
        self.assertEqual(heading_ranges("no heading"), [])

    def test_origins_overlap(self) -> None:
        self.assertTrue(origins_overlap("All origins", "China"))
        self.assertTrue(origins_overlap("All origins (exceptions apply)", "China"))
        self.assertFalse(origins_overlap("All origins except China", "China"))
        self.assertTrue(origins_overlap("All origins except China", "Vietnam"))
        self.assertTrue(origins_overlap("China; Hong Kong", "China"))
        self.assertFalse(origins_overlap("Canada; Mexico", "China; Hong Kong"))
        self.assertTrue(origins_overlap("60 economies (incl. China and Hong Kong)", "China"))


class RegistryFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.write()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, sources=None, measures=None, coverage=None) -> None:
        write_csv(self.dir / "sources.csv", SOURCE_COLUMNS, SOURCES if sources is None else sources)
        write_csv(self.dir / "measures.csv", MEASURE_COLUMNS, MEASURES if measures is None else measures)
        write_csv(self.dir / "coverage.csv", COVERAGE_COLUMNS, COVERAGE if coverage is None else coverage)

    def test_valid_registry_loads_sorted(self) -> None:
        registry = load_registry(self.dir)
        self.assertEqual([m["measure_id"] for m in registry.measures][:2], ["old-list", "china-duty"])
        self.assertEqual(registry.measure("others")["heading_ranges"], [(202, 273)])

    def test_invalid_rows_are_refused(self) -> None:
        cases = [
            ({"measures": MEASURES + [measure("x", "2025-01-01", "9903.01.20", "China", sources="fr-9")]}, "unknown source ids"),
            ({"measures": MEASURES + [measure("x", "2025-01-01", "9903.1.2", "China")]}, "headings are"),
            ({"measures": MEASURES + [measure("china-duty", "2025-01-01", "", "China")]}, "unique"),
            ({"measures": MEASURES + [measure("x", "2025-01-01", "", "China", status="待核对")]}, "says in notes"),
            ({"measures": MEASURES + [measure("x", "2025-13-01", "", "China")]}, "YYYY-MM-DD"),
            ({"sources": [{**SOURCES[0], "url": "https://example.com/news"}, SOURCES[1]]}, "HTS archive, the Federal Register"),
            ({"sources": [{**SOURCES[0], "sha256": "abc"}, SOURCES[1]]}, "64 lowercase hex"),
            ({"coverage": COVERAGE + [{**COVERAGE[0], "measure_id": "nope"}]}, "unknown measure_id"),
            ({"coverage": COVERAGE + [{**COVERAGE[0], "coverage": "maybe", "hts": "8507.10"}]}, "coverage must be"),
            ({"coverage": COVERAGE + [dict(COVERAGE[0])]}, "repeats"),
        ]
        for kwargs, message in cases:
            with self.subTest(message=message):
                self.write(**kwargs)
                with self.assertRaisesRegex(RegistryError, message):
                    load_registry(self.dir)

    def test_check_reports_match_date_differs_missing_and_not_covered(self) -> None:
        registry = load_registry(self.dir)
        rows = [
            timeline_row("2025-02-04", "Fentanyl duty (9903.01.20)", "China; Hong Kong"),
            timeline_row("2025-04-05", "Reciprocal baseline", "All origins"),
            timeline_row("2025-04-05", "Exception applies retroactively (9903.01.32)", "All origins", "product"),
            timeline_row("2025-06-01", "Something else", "China"),
        ]
        result = check_timeline(registry, rows, ["8517130000"], since="2022-01-01")
        verdicts = [(row["date"], row["verdict"]) for row in result["timeline_rows"]]
        self.assertEqual(
            verdicts,
            [("2025-02-04", "match"), ("2025-04-05", "match"), ("2025-04-05", "date_differs"), ("2025-06-01", "not_in_registry")],
        )
        self.assertEqual(result["timeline_rows"][1]["registry"][0]["coverage"], "exempt")
        self.assertEqual(result["timeline_rows"][2]["registry"][0]["date"], "2025-04-11")
        missing = {m["measure_id"]: m for m in result["missing_from_timeline"]}
        self.assertEqual(set(missing), {"others", "surcharge"})  # chips: not covered; old-list: no 8517.13 row
        self.assertEqual(missing["surcharge"]["coverage"], "not recorded")
        self.assertEqual([m["measure_id"] for m in result["checked_not_covered"]], ["chips"])
        self.assertEqual(result["counts"], {"match": 2, "date_differs": 1, "not_in_registry": 1, "missing_from_timeline": 2})

    def test_measures_before_the_study_are_listed_apart(self) -> None:
        registry = load_registry(self.dir)
        result = check_timeline(registry, [], ["85076000"], since="2022-01-01")
        self.assertEqual(result["before_study_period"], ["old-list"])
        self.assertNotIn("old-list", [m["measure_id"] for m in result["missing_from_timeline"]])
        self.assertIn("old-list", [m["measure_id"] for m in check_timeline(registry, [], ["85076000"])["missing_from_timeline"]])


class ShippedRegistryTests(unittest.TestCase):
    def test_shipped_registry_is_valid_and_primary_only(self) -> None:
        registry = load_registry(PROJECT_ROOT / "config" / "tariff_registry")
        self.assertGreaterEqual(len(registry.measures), 20)
        self.assertTrue(all(is_primary_source(row["url"]) for row in registry.sources.values()))
        for row in registry.measures:
            self.assertIn(row["status"], ("verified", "待核对"))
        self.assertIn("8517.13", {hts for _, hts in registry.coverage})


class CommandTests(unittest.TestCase):
    def test_parser(self) -> None:
        args = cli.build_parser().parse_args(["registry-check"])
        self.assertEqual((args.command, args.study), ("registry-check", None))
        args = cli.build_parser().parse_args(["registry-check", "--study", "s.toml"])
        self.assertEqual(args.study, Path("s.toml"))


class StudyCheckTests(unittest.TestCase):
    """The offline lithium fixture: an approved scope and a study timeline, checked without any download."""

    setUp = test_features.TimelineEndToEndTests.setUp
    tearDown = test_features.TimelineEndToEndTests.tearDown

    def test_study_timeline_is_checked_and_left_unchanged(self) -> None:
        timelines = self.root / "config" / "tariff_timelines"
        product = test_features.write_timeline(timelines / "li.csv", test_features.PRODUCT_ROWS.format(code="8507600010"))
        country = test_features.write_timeline(timelines / "_country.csv", test_features.COUNTRY_ROWS)
        before = (product.read_bytes(), country.read_bytes())
        self.study.write_text(
            self.study.read_text(encoding="utf-8")
            + '\n[timeline]\nfile = "config/tariff_timelines/li.csv"\ncountry = "config/tariff_timelines/_country.csv"\n',
            encoding="utf-8",
        )
        shutil.copytree(PROJECT_ROOT / "config" / "tariff_registry", self.root / "config" / "tariff_registry")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli._run_registry_check(argparse.Namespace(study=self.study), self.config)
        self.assertEqual(code, 0)
        result = json.loads(buffer.getvalue())
        verdicts = {(row["date"], row["verdict"]) for row in result["timeline_rows"]}
        self.assertIn(("2019-09-01", "match"), verdicts)  # Synthetic duty A on China, same day as List 4A
        self.assertIn(("2025-02-04", "match"), verdicts)
        self.assertIn("s122-surcharge", [m["measure_id"] for m in result["missing_from_timeline"]])
        self.assertEqual((product.read_bytes(), country.read_bytes()), before)

    def test_without_a_study_it_prints_the_summary(self) -> None:
        shutil.copytree(PROJECT_ROOT / "config" / "tariff_registry", self.root / "config" / "tariff_registry")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli._run_registry_check(argparse.Namespace(study=None), self.config)
        self.assertEqual(code, 0)
        self.assertIn("measures_待核对", json.loads(buffer.getvalue()))


if __name__ == "__main__":
    unittest.main()
