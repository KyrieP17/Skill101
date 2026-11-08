"""Tests for the DataWeb-to-tidy normalization boundary.

These tests deliberately use a tiny, synthetic DataWeb response.  They are not
tests of USITC's live service; their job is to prevent silent breakage in our
parser when we refactor local code.  The fixture retains the nested
``dto.tables -> row_groups -> rowsNew -> rowEntries`` shape seen in real
DataWeb responses, while containing no credentials and no copied research
results.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

import pandas as pd


# The project uses a ``src`` layout.  Adding it explicitly keeps ``python -m
# unittest`` usable from a fresh checkout without first installing the package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from usitc_pipeline.normalize import clean_number, parse_dataweb_response


class CleanNumberTests(unittest.TestCase):
    """Numeric cleaning must be conservative and predictable."""

    def test_currency_and_thousands_separators_are_removed(self) -> None:
        self.assertEqual(clean_number("$1,234.50"), 1234.50)

    def test_dataweb_missing_markers_become_nan(self) -> None:
        for marker in (None, "", "--", "N/A", "null"):
            with self.subTest(marker=marker):
                self.assertTrue(pd.isna(clean_number(marker)))

    def test_invalid_text_does_not_become_zero(self) -> None:
        # Treating an unparseable value as zero would manufacture trade.  The
        # parser should preserve it as missing instead.
        self.assertTrue(pd.isna(clean_number("not-a-number")))


class DataWebParserTests(unittest.TestCase):
    """The three measure tabs should merge into one canonical tidy row."""

    @classmethod
    def setUpClass(cls) -> None:
        fixture = PROJECT_ROOT / "tests" / "fixtures" / "dataweb_sample.json"
        with fixture.open("r", encoding="utf-8") as handle:
            cls.payload = json.load(handle)

    def test_nested_response_is_parsed_and_measure_tabs_are_merged(self) -> None:
        result = parse_dataweb_response(
            self.payload,
            source_file="dataweb_sample.json",
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(set(result["origin_name"]), {"China", "Vietnam"})
        self.assertTrue((result["hts_code"] == "847130").all())
        self.assertTrue((result["hts_level"] == 6).all())
        self.assertTrue((result["frequency"] == "annual").all())

        china = result.loc[result["origin_name"] == "China"].iloc[0]
        self.assertEqual(china["period"], "2025")
        self.assertEqual(china["customs_value_usd"], 60_000)
        self.assertEqual(china["quantity"], 600)
        self.assertEqual(china["quantity_unit"], "number")
        self.assertEqual(china["calculated_duty_usd"], 6_000)

        vietnam = result.loc[result["origin_name"] == "Vietnam"].iloc[0]
        self.assertTrue(pd.isna(vietnam["calculated_duty_usd"]))

    def test_repeated_rows_with_different_units_are_never_added(self) -> None:
        payload = copy.deepcopy(self.payload)
        table = next(item for item in payload["dto"]["tables"] if item["tab_name"] == "First Unit of Quantity")
        labels = [column["label"] for group in table["column_groups"] for column in group["columns"]]
        rows = table["row_groups"][0]["rowsNew"]
        repeated = copy.deepcopy(rows[0])
        repeated["rowEntries"][labels.index("Quantity Description")]["value"] = "kilograms"
        rows.append(repeated)

        china = parse_dataweb_response(payload, source_file="mixed.json").set_index("origin_name").loc["China"]

        self.assertEqual(china["quantity_unit"], "kilograms|number")
        self.assertTrue(pd.isna(china["quantity"]))
        self.assertEqual(china["customs_value_usd"], 60_000)

    def test_second_unit_table_becomes_its_own_columns(self) -> None:
        self.assertNotIn("quantity_2", parse_dataweb_response(self.payload).columns)

        payload = copy.deepcopy(self.payload)
        first = next(item for item in payload["dto"]["tables"] if item["tab_name"] == "First Unit of Quantity")
        second = copy.deepcopy(first)
        second["tab_name"] = "Second Unit of Quantity"
        labels = [column["label"] for group in second["column_groups"] for column in group["columns"]]
        for row in second["row_groups"][0]["rowsNew"]:
            row["rowEntries"][labels.index("Quantity Description")]["value"] = "kilograms"
            row["rowEntries"][labels.index("2025")]["value"] = "1,234"
        payload["dto"]["tables"].append(second)

        china = parse_dataweb_response(payload, source_file="kg.json").set_index("origin_name").loc["China"]

        self.assertEqual((china["quantity"], china["quantity_unit"]), (600, "number"))
        self.assertEqual((china["quantity_2"], china["quantity_2_unit"]), (1234, "kilograms"))
        self.assertEqual(china["customs_value_usd"], 60_000)

    def test_source_file_is_carried_into_audit_trail(self) -> None:
        result = parse_dataweb_response(
            self.payload,
            source_file="immutable/raw/snapshot.json",
        )
        self.assertEqual(
            set(result["source_file"]),
            {"immutable/raw/snapshot.json"},
        )


if __name__ == "__main__":
    unittest.main()
