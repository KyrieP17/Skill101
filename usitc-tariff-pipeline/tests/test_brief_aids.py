"""D-024 brief aids: [[derived]] calculations, anomaly flags, mention list,
window candidates, the price/mix split and brief-check."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_lithium_e2e as e2e  # noqa: E402
from test_feature_tables import panel  # noqa: E402  (the shared two-code, two-origin panel)
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient  # noqa: E402
from usitc_pipeline.study import StudyError, load_study  # noqa: E402
from usitc_pipeline.study_pipeline import analyze_study, brief_check_study, render_study_charts, report_study  # noqa: E402
from usitc_pipeline import brief_check  # noqa: E402
from usitc_pipeline import features as feature_module  # noqa: E402
from usitc_pipeline import flags  # noqa: E402
from usitc_pipeline.analysis import build_origin_series  # noqa: E402
from usitc_pipeline.calculations import CalculationError, build_calculations  # noqa: E402
from usitc_pipeline.study import Derived  # noqa: E402


def derived(derived_id: str, origins, measures, stats, frequency: str, window, base=None) -> Derived:
    return Derived(derived_id, "", tuple(origins), tuple(measures), tuple(stats), frequency, tuple(window),
                   tuple(base) if base else None)


class CalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.series = build_origin_series(panel(), quantity_comparable=True)

    def build(self, *items) -> pd.DataFrame:
        return build_calculations(self.series, items, focal_origin="China", quantity_unit="No.")

    def row(self, table: pd.DataFrame, origin: str, measure: str, stat: str = "total") -> pd.Series:
        return table[table["origin"].eq(origin) & table["measure"].eq(measure) & table["stat"].eq(stat)].iloc[0]

    def test_window_against_base(self) -> None:
        table = self.build(derived("jan-feb", ["China", "World", "Other origins"], ["value", "share"], ["total"],
                                   "monthly", ("2025-01", "2025-02"), ("2024-01", "2024-02")))
        china_share = self.row(table, "China", "share")
        self.assertAlmostEqual(china_share["value"], 60.0)       # 240 of 400
        self.assertAlmostEqual(china_share["base_value"], 70.0)  # 280 of 400
        self.assertAlmostEqual(china_share["change"], -10.0)
        self.assertEqual(china_share["change_unit"], "pp")
        self.assertEqual(self.row(table, "World", "value")["value"], 400.0)
        others = self.row(table, "Other origins", "value")
        self.assertEqual((others["value"], others["base_value"]), (160.0, 120.0))
        self.assertAlmostEqual(others["ratio"], 160 / 120)
        self.assertIn("World - China", others["formula"])
        self.assertIn("customs_value_usd=", others["components"])

    def test_mean_min_max_and_unobserved_periods(self) -> None:
        table = self.build(
            derived("spread", ["Japan"], ["share"], ["mean", "max"], "monthly", ("2025-01", "2025-02"), ("2024-01", "2024-02")),
            derived("gap", ["China"], ["value"], ["total"], "monthly", ("2024-12", "2025-01")),
        )
        mean = self.row(table, "Japan", "share", "mean")
        self.assertEqual((mean["value"], mean["base_value"], mean["change"]), (40.0, 30.0, 10.0))
        top = self.row(table, "Japan", "share", "max")
        self.assertEqual((top["value"], top["at"], top["base_at"]), (40.0, "2025-01", "2024-01"))
        gap = table[table["id"].eq("gap")].iloc[0]
        self.assertTrue(pd.isna(gap["value"]))
        self.assertIn("not observed: 2024-12", gap["note"])

    def test_unknown_origin_suggests_a_name(self) -> None:
        with self.assertRaisesRegex(CalculationError, "did you mean"):
            self.build(derived("typo", ["Chinaa"], ["value"], ["total"], "annual", ("2025", "2025")))


def months(values: dict[str, dict[str, tuple[float, float]]]) -> pd.DataFrame:
    """Display rows from {period: {origin: (value, duty)}}; World = sum of the origins."""

    columns = ["frequency", "period", "display_origin", "customs_value_usd", "calculated_duty_usd", "world_value_usd"]
    rows = []
    for period, origins in values.items():
        world = sum(value for value, _ in origins.values())
        for origin, (value, duty) in origins.items():
            rows.append({"frequency": "monthly", "period": period, "display_origin": origin, "customs_value_usd": value,
                         "calculated_duty_usd": duty, "world_value_usd": world})
    return pd.DataFrame(rows, columns=columns)


def change(origin: str, base: float, compare: float, **extra) -> dict:
    row = {"comparison": "annual_2024_vs_2025", "base_periods": "2024", "compare_periods": "2025", "origin_name": origin,
           "base_share_pct": base, "compare_share_pct": compare, "share_change_pp": compare - base, "contribution_pp": 0.0,
           "direction": "", "compare_value_usd": compare, "compare_unit_value_usd": 100.0,
           "base_realized_duty_pct": 0.0, "compare_realized_duty_pct": 0.0}
    row.update(extra)
    return row


class AnomalyFlagTests(unittest.TestCase):
    def test_rate_parsing_and_origin_matching(self) -> None:
        self.assertEqual([flags.rate_level(text) for text in ("+10%", "+10% -> +20%", "至 84%", "分国别", "")],
                         [10.0, 20.0, 84.0, None, None])
        self.assertEqual(flags.named_origins("All origins except China", ["China", "India"]), ["India"])
        self.assertEqual(flags.named_origins("China; Hong Kong", ["China", "India"]), ["China"])
        self.assertEqual(flags.named_origins("All origins (exceptions apply)", ["China", "India"]), ["China", "India"])
        self.assertEqual(flags.base_rate([{"hts8_base_rate": "8517.13.00: Free"}]), (0.0, ""))
        self.assertEqual(flags.base_rate([{"hts8_base_rate": "8507.60.00: 3.4%"}])[0], 3.4)
        self.assertIsNone(flags.base_rate([{"hts8_base_rate": "1111.11.10: 2%; 1111.11.20: 3%"}])[0])
        self.assertIsNone(flags.base_rate(None)[0])

    def test_origin_rules(self) -> None:
        changes = pd.DataFrame([
            change("World", 100.0, 100.0),
            change("China", 90.0, 80.0),
            change("Laos", 0.0, 0.4, direction="new_origin"),
            change("India", 0.2, 15.0, compare_unit_value_usd=200.0),
            change("Chile", 2.0, 4.6, compare_unit_value_usd=101.0, compare_realized_duty_pct=1.0),
        ])
        table, skipped = flags.build_anomaly_flags(changes, months({}), focal_origin="China",
                                                   drilldown_menu=[{"hts8_base_rate": "1111.11.10: 3%"}])
        found = set(zip(table["rule"], table["origin"]))
        self.assertIn(("new_origin", "Laos"), found)
        self.assertIn(("grew_from_almost_nothing", "India"), found)
        self.assertIn(("unit_value_far_off", "India"), found, "200 against 100: 2x")
        self.assertNotIn(("unit_value_far_off", "Chile"), found)
        self.assertIn(("duty_below_base_rate", "Chile"), found, "1% against a 3% base rate")
        self.assertIn(("duty_below_base_rate", "China"), found, "0% against 3%")
        self.assertEqual(skipped["timeline_rules"], "the study has no [timeline]")
        self.assertTrue(table["question"].str.len().gt(0).all())

    def test_timeline_rules(self) -> None:
        data = {}
        for year in (2024, 2025):
            for month in range(1, 8):
                china = 50.0 if (year, month) in ((2025, 2), (2025, 3)) else 20.0
                data[f"{year}-{month:02d}"] = {"China": (china, 0.0), "India": (10.0, 0.0)}
        timeline = [
            {"date": "2025-04-05", "date_type": "effective", "measure": "Extra duty", "rate_change": "+20%",
             "origins": "China", "applies_to": "", "needs_check": True, "coverage": "applies"},
            {"date": "2025-04-05", "date_type": "effective", "measure": "Code-only duty", "rate_change": "+20%",
             "origins": "China", "applies_to": "1111111111", "needs_check": False, "coverage": "applies"},
            {"date": "2025-04-05", "date_type": "effective", "measure": "Exempt duty", "rate_change": "+99%",
             "origins": "China", "applies_to": "", "needs_check": False, "coverage": "exempt"},
        ]
        table, _ = flags.build_anomaly_flags(pd.DataFrame(), months(data), focal_origin="China", highlight_origins=["India"],
                                             timeline=timeline)
        duty = table[table["rule"].eq("duty_below_timeline")]
        self.assertEqual(duty["origin"].tolist(), ["China"], "the applies_to row is not a whole-scope rate")
        self.assertNotIn("Exempt duty", " ".join(duty["timeline"].astype(str)))
        self.assertEqual(duty["periods"].iloc[0], "2025-05..2025-06")
        self.assertIn("待核对", duty["question"].iloc[0])
        surge = table[table["rule"].eq("surge_before_timeline_date")].set_index("origin")
        self.assertIn("China", surge.index)
        self.assertEqual(surge.loc["China", "periods"], "2024-02..2024-03 -> 2025-02..2025-03")
        self.assertNotIn("Other origins", surge.index, "India stayed flat")

    def test_mention_list(self) -> None:
        changes = pd.DataFrame([
            change("World", 100.0, 100.0),
            change("China", 70.0, 40.0, compare_realized_duty_pct=12.0),
            change("India", 10.0, 39.0),
            change("Chile", 1.0, 1.5),
        ])
        timeline = [{"user_checked": False, "needs_check": True}]
        table = flags.build_mention_list(changes, focal_origin="China", timeline=timeline, continuity_verified=False)
        items = table["item"].tolist()
        self.assertEqual(items[:3], ["China", "India", "Chile"], "Chile only as a top-3 origin by value")
        self.assertIn("realized duty: China (annual_2024_vs_2025)", items)
        self.assertIn("timeline rows without a verified primary source", items)
        self.assertIn("HTS continuity not verified", items)


class WindowCandidateTests(unittest.TestCase):
    def test_turns_are_suggested_and_one_month_spikes_are_not(self) -> None:
        path = [80, 80, 80, 20, 20, 20, 60, 20, 20]
        data = {f"2025-{index + 1:02d}": {"China": (float(share), 0.0), "India": (100.0 - share, 0.0)}
                for index, share in enumerate(path)}
        table = flags.build_window_candidates(months(data), focal_origin="China", study_windows=[("2025-03", "2025-04")])
        rows = {(row.start, row.end): row for row in table.itertuples()}
        self.assertTrue(rows[("2025-03", "2025-04")].suggested)
        self.assertTrue(rows[("2025-03", "2025-04")].in_study)
        self.assertEqual(rows[("2025-06", "2025-07")].kind, "spike")
        self.assertFalse(rows[("2025-06", "2025-07")].suggested)
        self.assertEqual(flags.turning_points([0, 1, 0], 5.0), [0, 2])

    def test_a_move_that_recurs_in_the_same_month_is_seasonal(self) -> None:
        year = [30, 30, 30, 30, 30, 30, 30, 30, 80, 75, 30, 30]  # September jumps and October stays up
        data = {f"{y}-{m + 1:02d}": {"China": (float(share), 0.0), "India": (100.0 - share, 0.0)}
                for y in (2023, 2024, 2025) for m, share in enumerate(year)}
        table = flags.build_window_candidates(months(data), focal_origin="China")
        rows = {(row.start, row.end): row for row in table.itertuples()}
        jump = rows[("2025-08", "2025-09")]
        self.assertEqual(jump.kind, "seasonal")
        self.assertFalse(jump.suggested)
        self.assertIn("2023, 2024", jump.note)
        self.assertEqual(rows[("2025-10", "2025-11")].kind, "seasonal")
        self.assertFalse(table["suggested"].any())


class PriceMixTests(unittest.TestCase):
    def test_price_and_mix_add_up_to_the_unit_value_part(self) -> None:
        display = feature_module_display()
        table = feature_module.build_share_decomposition(
            display, [], focal_origin="China", highlight_origins=["Japan"], panel=panel(), mix=True
        )
        annual = table[table["window_kind"].eq("annual")].set_index("display_origin")
        for origin in ("China", "Japan"):
            parts = annual.loc[origin, "value_change_from_price_usd"] + annual.loc[origin, "value_change_from_mix_usd"]
            self.assertAlmostEqual(parts, annual.loc[origin, "value_change_from_unit_value_usd"])
        # China: 6 + 8 units at 10 each in 2024; 2 + 9 units at 15 and 10 in 2025.
        self.assertNotAlmostEqual(annual.loc["China", "value_change_from_mix_usd"], 0.0)
        one_code = panel()[lambda frame: frame["hts_code"].eq("1111110010")]
        single = feature_module.build_share_decomposition(
            feature_module_display(one_code), [], focal_origin="China", highlight_origins=["Japan"], panel=one_code, mix=True
        )
        self.assertTrue((single["value_change_from_mix_usd"].dropna().abs() < 1e-9).all(), "one code: no mix across codes")
        plain = feature_module.build_share_decomposition(display, [], focal_origin="China", highlight_origins=["Japan"])
        self.assertNotIn("value_change_from_mix_usd", plain)


def feature_module_display(frame: pd.DataFrame | None = None) -> pd.DataFrame:
    from usitc_pipeline.analysis import build_display_series

    series = build_origin_series(panel() if frame is None else frame, quantity_comparable=True)
    return build_display_series(series, focal_origin="China", highlight_origins=["Japan"])


GOOD_BRIEF = """# Title

## 1. 核心发现

1. **China 份额下降。** 份额 70.0% → 60.0%，进口额 2.4 亿美元。 **[Derived]** — `tables/calculations.csv`

## 2. 关税时间线

没有时间线。

## 3. 主体

| 来源地 | 进口额（亿美元） |
|---|---|
| China | 2.4 |

**[Measured]** — `tables/calculations.csv`

## 4. 值得关注的问题

1. **为什么份额会变，是因为价格吗？** 这份数据回答不了。 **[Unknown]**

## 5. 下钻菜单

无。

## 6. 口径说明

- 实际税率不是法定税率。
"""


class BriefCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "tables").mkdir()
        pd.DataFrame([
            {"id": "x", "origin": "China", "measure": "share", "unit": "pct", "value": 60.0, "base_value": 70.0, "change": -10.0},
            {"id": "y", "origin": "China", "measure": "value", "unit": "USD", "value": 2.4e8, "base_value": 2.8e8, "change": -0.4e8},
        ]).to_csv(self.out / "tables" / "calculations.csv", index=False)
        self.manifest = {"scope_sha256": "ab" * 32, "plan_sha256": "cd" * 32, "study_sha256": "ef" * 32,
                         "outputs": {"calculations": {"path": "tables/calculations.csv"}}}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check(self, text: str, chart_manifest: dict | None = None) -> dict:
        (self.out / "brief.md").write_text(text, encoding="utf-8")
        return brief_check.check_brief(
            self.out, manifest=self.manifest, chart_manifest=chart_manifest or {"charts": []}
        )

    def checks(self, text: str) -> set[str]:
        return {item["check"] for item in self.check(text)["errors"]}

    def test_a_traced_brief_passes(self) -> None:
        report = self.check(GOOD_BRIEF)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["numbers"]["untraced"], 0)
        self.assertGreaterEqual(report["numbers"]["traced"], 4)
        numbers = report["numbers"]
        self.assertEqual(numbers["numeric_tokens"], numbers["parsed"] + numbers["ambiguous"] + numbers["skipped"])
        self.assertEqual(numbers["ambiguous"], 0)
        self.assertEqual([item["check"] for item in report["warnings"]], ["findings"], "one finding, not 4-6")

    def test_untraced_unsourced_and_unlabeled_numbers(self) -> None:
        self.assertEqual(self.checks(GOOD_BRIEF.replace("70.0%", "71.0%")), {"untraced"})
        self.assertEqual(self.checks(GOOD_BRIEF.replace(" **[Derived]** — `tables/calculations.csv`", "")),
                         {"unsourced", "unlabeled"})
        self.assertEqual(self.checks(GOOD_BRIEF.replace("`tables/calculations.csv`", "`tables/made_up.csv`", 1)),
                         {"source", "untraced"})
        self.assertEqual(self.checks(GOOD_BRIEF.replace("[Derived]", "[Guess]")), {"label"})

    def test_sections_title_and_hashes(self) -> None:
        self.assertIn("sections", self.checks(GOOD_BRIEF.replace("## 5. 下钻菜单\n\n无。\n\n", "")))
        swapped = GOOD_BRIEF.replace("## 5. 下钻菜单", "## 5. TMP").replace("## 6. 口径说明", "## 5. 下钻菜单").replace("## 5. TMP", "## 6. 口径说明")
        self.assertIn("sections", self.checks(swapped))
        # D-025: the HTS4 map left the brief; an old brief that still has one only gets a warning.
        old = self.check(GOOD_BRIEF.replace("## 3. 主体", "## 3. HTS4 地图\n\nHTS4 地图：未取数。\n\n## 3. 主体"))
        self.assertEqual(old["errors"], [])
        self.assertIn("none of the six", " ".join(item["message"] for item in old["warnings"]))
        self.assertIn("title", self.checks(GOOD_BRIEF.replace("# Title", "Title")))
        self.assertEqual(self.checks(GOOD_BRIEF + "\nstudy `abababab`\n"), set())
        self.assertEqual(self.checks(GOOD_BRIEF + "\nstudy `abcdef12`\n"), {"hash"})

    def test_causal_wording_outside_questions(self) -> None:
        self.assertEqual(self.checks(GOOD_BRIEF), set(), "因为 inside a question is allowed")
        self.assertEqual(self.checks(GOOD_BRIEF.replace("China 份额下降。", "China 份额下降，因为关税。")), {"causal"})
        self.assertEqual(self.checks(GOOD_BRIEF.replace("China 份额下降。", "可能是关税。")), {"causal"})
        for phrase in ("likely tariffs", "possibly tariffs", "may be tariffs", "suggests tariffs", "driven by tariffs",
                       "shifted production", "moved production"):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.checks(GOOD_BRIEF.replace("China 份额下降。", phrase + ".")), {"causal"})

    def test_mention_list_items_must_appear(self) -> None:
        pd.DataFrame([{"item": "India", "keywords": "India", "reason": "top 2", "source": "tables/origin_change.csv"}]).to_csv(
            self.out / "tables" / "mention_list.csv", index=False)
        self.manifest["outputs"]["mention_list"] = {"path": "tables/mention_list.csv"}
        self.assertEqual(self.checks(GOOD_BRIEF), {"mention"})
        self.assertEqual(self.checks(GOOD_BRIEF.replace("无。", "India 可以作为焦点来源地重跑。")), set())

    def test_product_mix_chart_requires_codebook_with_official_descriptions(self) -> None:
        (self.out / "charts").mkdir()
        pd.DataFrame([
            {"hts10_code": "1111110010", "hts10_display_label_en": "Product A",
             "hts10_description": "Synthetic product A",
             "product_share_status": "measured"},
            {"hts10_code": "1111110020", "hts10_display_label_en": "Product B",
             "hts10_description": "Synthetic product B",
             "product_share_status": "measured"},
        ]).to_csv(self.out / "charts" / "12_product_mix.csv", index=False)
        manifest = {"charts": [{
            "png": {"path": "charts/12_product_mix.png"},
            "chart_data": {"path": "charts/12_product_mix.csv"},
        }]}

        def with_body(extra: str) -> str:
            return GOOD_BRIEF.replace(
                "\n## 4. 值得关注的问题",
                f"\n\n![mix](charts/12_product_mix.png)\n\n{extra}\n\n## 4. 值得关注的问题",
            )

        missing = self.check(with_body(""), manifest)
        self.assertEqual({item["check"] for item in missing["errors"]}, {"hts10_codebook"})
        code_only = self.check(with_body(
            "| HTS10 | 中文类目 |\n|---|---|\n"
            "| `1111.11.00.10` | 合成产品甲 |\n| `1111.11.00.20` | 合成产品乙 |"
        ), manifest)
        self.assertEqual({item["check"] for item in code_only["errors"]}, {"hts10_codebook"})
        official_only = self.check(with_body(
            "| HTS10 | Official HTS description (English) |\n|---|---|\n"
            "| `1111.11.00.10` | Synthetic product A |\n"
            "| `1111.11.00.20` | Synthetic product B |"
        ), manifest)
        self.assertEqual({item["check"] for item in official_only["errors"]}, {"hts10_codebook"})
        complete = self.check(with_body(
            "| HTS10 | Readable category (English) | Official HTS description (English) |\n|---|---|---|\n"
            "| `1111.11.00.10` | Product A | Synthetic product A |\n"
            "| `1111.11.00.20` | Product B | Synthetic product B |"
        ), manifest)
        self.assertEqual(complete["status"], "pass", complete["errors"])

    def test_number_parsing(self) -> None:
        found = brief_check.numbers("份额 70.0% → 60.0%，2025-04 只有 28.6 亿美元，T3、8517.13 和 `abc 12`")
        self.assertEqual([(item.value, item.unit) for item in found], [(70.0, "%"), (60.0, "%"), (28.6, "亿美元")],
                         "months, T numbers, HTS codes and code spans are not numbers")
        ranged = brief_check.numbers("8.7–16.2 亿美元")
        self.assertEqual([item.unit for item in ranged], ["亿美元", "亿美元"], "a range takes the unit after it")

        english = brief_check.numbers(
            "$519 per unit; US$520/unit; USD 521; 58.9 USD bn; USD 120 mn; $58.9bn; 4.4x; 4.4×"
        )
        self.assertEqual(
            [(item.value, item.unit) for item in english],
            [(519.0, "per unit"), (520.0, "/unit"), (521.0, "USD"), (58.9, "USD bn"),
             (120.0, "mn"), (58.9, "bn"), (4.4, "x"), (4.4, "×")],
        )
        self.assertEqual(brief_check.SCALES[english[3].unit], (1e9,))
        capacity = brief_check.numbers("1 Gb; 512 Mb; 8 GB")
        self.assertEqual(
            [(item.value, item.unit, item.ambiguous_unit) for item in capacity],
            [(1.0, "Gb", None), (512.0, "Mb", None), (8.0, None, "GB")],
            "bit-capacity abbreviations are case-sensitive and must not turn GB/MB into bits",
        )
        ambiguous = brief_check.numbers("Value 7 EUR and 3 widgets")
        self.assertEqual([(item.value, item.unit, item.ambiguous_unit) for item in ambiguous],
                         [(7.0, None, "EUR"), (3.0, None, None)])

    def test_unknown_adjacent_unit_is_an_error_not_a_silent_skip(self) -> None:
        report = self.check(GOOD_BRIEF + "\n- Value 7 EUR. **[Derived]** — `tables/calculations.csv`\n")
        self.assertIn("ambiguous_unit", {item["check"] for item in report["errors"]})
        self.assertEqual(report["numbers"]["ambiguous"], 1)
        self.assertGreaterEqual(report["numbers"]["skipped"], 0)

    def test_column_kinds(self) -> None:
        kind = brief_check.column_kind
        for name in ("value_change_from_unit_value_usd", "value_change_from_price_usd", "value_change_from_quantity_usd",
                     "value_change_usd", "calculated_duty_usd"):
            self.assertEqual(kind(name), brief_check.USD, name)
        for name in ("base_unit_value_usd", "unit_value_usd", "world_unit_value_usd"):
            self.assertEqual(kind(name), brief_check.UNIT_VALUE, name)
        self.assertEqual(kind("unit_value_change_pct"), brief_check.PCT)
        self.assertEqual(kind("share_change_from_own_pp"), brief_check.PP)
        self.assertEqual(kind("realized_duty_rate"), brief_check.PCT)



class BriefAidsEndToEndTests(unittest.TestCase):
    """The offline lithium fixture (annual 2017-2025) with every D-024 option on."""

    OPTIONS = (
        '\n[[derived]]\nid = "china-2025"\norigin = ["China", "World"]\nmeasure = ["value", "share"]\nwindow = "2025"\nbase = "2024"\n'
        "\n[decomposition]\nenabled = true\nmix = true\n"
        "\n[anomalies]\nenabled = true\n"
        "\n[brief]\ncandidates = true\n"
    )

    def setUp(self) -> None:
        e2e.LithiumIonOfflineE2ETests.setUp(self)

    def tearDown(self) -> None:
        e2e.LithiumIonOfflineE2ETests.tearDown(self)

    def load(self):
        return load_study(self.study, project_root=self.config.paths.root)

    def run_study(self, extra: str) -> Path:
        self.study.write_text(self.study.read_text(encoding="utf-8") + extra, encoding="utf-8")
        _, plan_path = plan_module.plan_study(self.config, self.study)
        client = DataWebClient(self.config, token=e2e.TOKEN, session=e2e.LithiumFixtureSession(self.fixture))
        plan_module.acquire_plan(self.config, plan_path, client=client, today=e2e.FIXED_TODAY)
        out = Path(analyze_study(self.config, self.study)["analysis_dir"])
        render_study_charts(self.config, self.study)
        report_study(self.config, self.study)
        return out

    def test_options_are_off_by_default(self) -> None:
        original = self.study.read_text(encoding="utf-8")
        default = self.load().study_sha256
        self.study.write_text(original + "\n[anomalies]\nenabled = false\n\n[brief]\ncandidates = false\n", encoding="utf-8")
        self.assertEqual(self.load().study_sha256, default)
        self.study.write_text(original + self.OPTIONS, encoding="utf-8")
        self.assertNotEqual(self.load().study_sha256, default)

    def test_derived_entries_are_validated(self) -> None:
        original = self.study.read_text(encoding="utf-8")
        head = '[[derived]]\nid = "a"\norigin = "China"\nmeasure = "value"\n'
        for extra, message in (
            (head + 'window = ["2024", "2025-01"]\n', "not a mix"),
            # annual-only fixture: a monthly base is outside [periods] before the kinds are compared
            (head + 'window = "2025"\nbase = "2024-01"\n', r"inside \[periods\] monthly|both be months or both be years"),
            ('[[derived]]\nid = "a"\norigin = "China"\nmeasure = "margin"\nwindow = "2025"\n', "must be one of"),
            (head + 'window = "2030"\n', "inside"),
            (head + 'window = "2025"\n' + head + 'window = "2024"\n', "unique"),
            ('[[derived]]\nid = "a"\norigin = "China"\nwindow = "2025"\n', "measure is required"),
            (head, "window is required"),
        ):
            with self.subTest(extra=extra):
                self.study.write_text(original + "\n" + extra, encoding="utf-8")
                with self.assertRaisesRegex(StudyError, message):
                    self.load()

    def test_tables_reach_the_manifest_and_a_traced_brief_passes(self) -> None:
        out = self.run_study(self.OPTIONS)
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        for name in ("calculations", "share_decomposition", "anomaly_flags", "mention_list", "window_candidates"):
            self.assertIn(name, manifest["outputs"], name)
        calculations = pd.read_csv(out / "tables" / "calculations.csv")
        self.assertEqual(len(calculations), 4)
        share = calculations[calculations["origin"].eq("China") & calculations["measure"].eq("share")].iloc[0]
        value = calculations[calculations["origin"].eq("China") & calculations["measure"].eq("value")].iloc[0]
        decomposition = pd.read_csv(out / "tables" / "share_decomposition.csv")
        self.assertIn("value_change_from_mix_usd", decomposition)
        self.assertTrue(pd.read_csv(out / "tables" / "window_candidates.csv").empty, "no monthly data")
        mention = pd.read_csv(out / "tables" / "mention_list.csv", keep_default_na=False)
        keywords = "、".join(row.split(";")[0] for row in mention["keywords"])

        def brief(share_text: str) -> str:
            return (
                "# 锂电池\n\n## 1. 核心发现\n\n"
                f"1. **China 份额变化。** 2025 年 China 份额 {share_text}，进口额 {value['value'] / 1e8:.1f} 亿美元。"
                " **[Derived]** — `tables/calculations.csv`\n\n"
                "## 2. 关税时间线\n\n无。\n\n## 3. 主体\n\n无。\n\n"
                "## 4. 值得关注的问题\n\n无。\n\n## 5. 下钻菜单\n\n无。\n\n"
                f"## 6. 口径说明\n\n- 提到：{keywords}。\n"
            )

        (out / "brief.md").write_text(brief(f"{share['value']:.1f}%"), encoding="utf-8")
        report = brief_check_study(self.config, self.study)
        self.assertEqual(report["status"], "pass", report["errors"])
        (out / "brief.md").write_text(brief(f"{share['value'] + 1:.1f}%"), encoding="utf-8")
        report = brief_check_study(self.config, self.study)
        self.assertEqual({item["check"] for item in report["errors"]}, {"untraced"})


if __name__ == "__main__":
    unittest.main()
