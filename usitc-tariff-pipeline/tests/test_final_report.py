"""Final report: brief.md plus an appendix, as HTML and PDF, from validated outputs only.

The browser is replaced by a fake printer except in the opt-in test (USITC_TEST_PDF=1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for entry in (PROJECT_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import test_features  # noqa: E402  (module import, so its tests are not collected twice)
from usitc_pipeline import final_report as final_module  # noqa: E402
from usitc_pipeline import scope as scope_module  # noqa: E402
from usitc_pipeline.cli import build_parser  # noqa: E402
from usitc_pipeline.final_report import FinalReportError, markdown_html, report_language  # noqa: E402


def no_images(src: str) -> str:
    raise AssertionError(f"unexpected image {src}")


ZH_SECTIONS = ("1. 核心发现", "2. 关税时间线", "3. 主体", "4. 值得问的问题", "5. 下钻菜单", "6. 口径说明")
EN_SECTIONS = ("1. Key findings", "2. Tariff timeline", "3. Body", "4. Questions", "5. Drill-down menu", "6. Notes on measures")


def seven(title: str, first: str = "", *, sections: tuple[str, ...] = ZH_SECTIONS) -> str:
    """A brief with the six sections brief-check requires (D-024, D-025); ``first`` is the findings body."""

    parts = [f"# {title}\n"]
    for index, heading in enumerate(sections):
        parts.append(f"## {heading}\n\n{first if index == 0 and first else '—'}\n")
    return "\n".join(parts)


class MarkdownTests(unittest.TestCase):
    def test_title_paragraph_bold_and_code(self) -> None:
        title, body = markdown_html("# Title\n\nline one\nline **two `x`**\n", no_images)
        self.assertEqual(title, "Title")
        self.assertIn("<h1>Title</h1>", body)
        self.assertIn("<p>line one line <strong>two <code>x</code></strong></p>", body)

    def test_raw_html_is_escaped(self) -> None:
        _, body = markdown_html("<script>alert(1)</script> & `<b>`", no_images)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; <code>&lt;b&gt;</code>", body)
        self.assertNotIn("<script>", body)

    def test_table_with_alignment(self) -> None:
        _, body = markdown_html("| a | long header text |\n|---|--:|\n| 1 | 2 |\n| x |\n", no_images)
        self.assertIn('<th class="nw">a</th>', body)
        self.assertIn('<th style="text-align:right">long header text</th>', body)
        self.assertIn('<td class="nw" style="text-align:right">2</td>', body)
        self.assertEqual(body.count("<tr>"), 3)

    def test_nested_and_ordered_lists(self) -> None:
        _, body = markdown_html("- a\n  - b\n- c\n\n1. x\n2. y\n   more\n", no_images)
        self.assertIn("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>", body)
        self.assertIn("<ol><li>x</li><li>y more</li></ol>", body)

    def test_images_links_quotes_and_rules(self) -> None:
        seen = []

        def image(src: str) -> str:
            seen.append(src)
            return "data:image/png;base64,AAAA"

        _, body = markdown_html(
            "![03](charts/03.png)\n\nsee [docs](https://example.com) and [file](tables/x.csv)\n\n> note\n\n---\n", image
        )
        self.assertEqual(seen, ["charts/03.png"])
        self.assertIn('<figure><img src="data:image/png;base64,AAAA" alt="03"></figure>', body)
        self.assertIn('<a href="https://example.com">docs</a> and file', body)
        self.assertIn("<blockquote><p>note</p></blockquote>", body)
        self.assertIn("<hr>", body)

    def test_language_defaults_to_english_and_accepts_an_explicit_override(self) -> None:
        self.assertEqual(report_language("# 锂电池"), "en")
        self.assertEqual(report_language("# Batteries"), "en")
        self.assertEqual(report_language("# Batteries with 中文 terminology"), "en")
        self.assertEqual(report_language("# 锂电池", "zh"), "zh")
        self.assertEqual(report_language("# 锂电池", "en"), "en")
        with self.assertRaises(FinalReportError):
            report_language("# x", "fr")


class CommandTests(unittest.TestCase):
    def test_report_pdf_command(self) -> None:
        args = build_parser().parse_args(["report-pdf", "--study", "s.toml", "--lang", "en"])
        self.assertEqual((args.command, args.study, args.lang), ("report-pdf", Path("s.toml"), "en"))

    def test_report_pdf_defaults_to_english(self) -> None:
        args = build_parser().parse_args(["report-pdf", "--study", "s.toml"])
        self.assertEqual(args.lang, "en")

    def test_missing_browser_path_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FinalReportError, "PDF browser not found"):
                final_module.find_browser(Path(tmp) / "nope.exe")


def fake_printer(html_path: Path, pdf_path: Path) -> None:
    pdf_path.write_bytes(b"%PDF-1.4 fake\n")


class FinalReportEndToEndTests(unittest.TestCase):
    """The offline lithium fixture, then a brief, then the final report."""

    setUp = test_features.TimelineEndToEndTests.setUp
    tearDown = test_features.TimelineEndToEndTests.tearDown
    run_study = test_features.TimelineEndToEndTests.run_study

    def build(self, brief: str | None, **options):
        from usitc_pipeline.study_pipeline import final_report_study

        out = self.run_study("")
        if brief is not None:
            (out / "brief.md").write_text(brief, encoding="utf-8")
        options.setdefault("printer", fake_printer)
        return out, (lambda: final_report_study(self.config, self.study, **options))

    def charts(self, out: Path) -> list[str]:
        manifest = json.loads((out / "chart_manifest.json").read_text(encoding="utf-8"))
        return [chart["png"]["path"] for chart in manifest["charts"]]

    def test_brief_becomes_html_pdf_and_record(self) -> None:
        out = self.run_study("")
        first, *rest = self.charts(out)
        brief = seven("合成产品", f"| 来源地 | 说明 |\n|---|---|\n| China | 见图 |\n\n![chart]({first})\n\n- 问题 **[Derived]**")
        (out / "brief.md").write_text(brief, encoding="utf-8")
        before = {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}
        from usitc_pipeline.study_pipeline import final_report_study

        result = final_report_study(self.config, self.study, language="zh", printer=fake_printer)
        self.assertEqual(result["language"], "zh")
        self.assertEqual(result["body_charts"], [first])
        self.assertEqual(result["appendix_charts"], rest)
        page = (out / "final_report.html").read_text(encoding="utf-8")
        self.assertIn("<title>合成产品</title>", page)
        self.assertEqual(page.count("data:image/png;base64,"), 1 + len(rest))
        for label in ("附录", "A. 研究身份", "B. 数据核对", "C. 不能下的结论", "D. 其他图表", "E. 文件与哈希", "正文 brief-check"):
            self.assertIn(label, page)
        self.assertIn("个单位歧义", page)
        self.assertIn("个无单位跳过", page)
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertIn(manifest["study_sha256"], page)
        record = json.loads((out / "final_report.json").read_text(encoding="utf-8"))
        self.assertEqual(record["inputs"]["brief"]["sha256"], final_module._sha256(out / "brief.md"))
        self.assertEqual(record["identity"]["study_sha256"], manifest["study_sha256"])
        self.assertEqual(record["printed_by"], "custom printer")
        self.assertEqual(record["brief_check"]["status"], "pass")
        self.assertTrue((out / "final_report.pdf").read_bytes().startswith(b"%PDF"))
        after = {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}
        self.assertEqual({name: after[name] for name in before}, before)  # only the three new files
        self.assertEqual(set(after) - set(before), set(final_module.OUTPUTS))

    def test_english_brief_gets_an_english_appendix(self) -> None:
        out, build = self.build(seven("Synthetic product", "Text.", sections=EN_SECTIONS))
        build()
        page = (out / "final_report.html").read_text(encoding="utf-8")
        self.assertIn("Appendix", page)
        self.assertIn("ambiguous units", page)
        self.assertIn("unitless skips", page)
        self.assertIn("A. Study identity", page)
        self.assertNotIn("附录", page)

    def test_brief_failing_brief_check_is_refused(self) -> None:
        out, build = self.build("# 合成产品\n\n## 1. 核心发现\n\n中国份额 50%。\n")
        with self.assertRaisesRegex(FinalReportError, "brief-check"):
            build()
        self.assertFalse((out / "final_report.html").exists())
        self.assertFalse((out / "final_report.pdf").exists())

    def test_missing_brief_is_refused(self) -> None:
        _, build = self.build(None)
        with self.assertRaisesRegex(FinalReportError, "No brief.md"):
            build()

    def test_brief_without_a_title_is_refused(self) -> None:
        _, build = self.build("## no title\n")
        with self.assertRaisesRegex(FinalReportError, "level-1 heading"):
            build()

    def test_only_validated_charts_can_be_embedded(self) -> None:
        out, build = self.build("# T\n\n![x](processed/other.png)\n")
        (out / "processed" / "other.png").write_bytes(b"not a chart")
        with self.assertRaisesRegex(FinalReportError, "not a validated chart"):
            build()
        (out / "brief.md").write_text("# T\n\n![x](../escape.png)\n", encoding="utf-8")
        with self.assertRaisesRegex(FinalReportError, "not a validated chart"):
            build()

    def test_changed_chart_is_refused(self) -> None:
        out = self.run_study("")
        first = self.charts(out)[0]
        (out / "brief.md").write_text(f"# T\n\n![x]({first})\n", encoding="utf-8")
        (out / first).write_bytes((out / first).read_bytes() + b"tampered")
        from usitc_pipeline.study_pipeline import final_report_study

        with self.assertRaisesRegex(Exception, "changed"):
            final_report_study(self.config, self.study, printer=fake_printer)
        self.assertFalse((out / "final_report.html").exists())

    def test_chat_approval_is_named_in_the_report_and_its_appendix(self) -> None:
        """Re-approve the fixture scope the chat way; the gate records it and both reports say so."""

        for name in ("scope.csv", "manifest.json"):
            (self.scope_dir / name).unlink()
        card = self.scope_dir / "approval-card.json"
        scope_module.approve_scope(
            self.scope_dir / "reviewed.csv",
            approval_card=card,
            approved_scope=self.scope_dir / "scope.csv",
            manifest_path=self.scope_dir / "manifest.json",
            reviewer="test-reviewer",
            history_evidence=self.scope_dir / "hts-history.json",
            chat_confirmation="批准",
            confirm_prefix=json.loads(card.read_text(encoding="utf-8"))["scope_sha256"][:8],
        )
        out, build = self.build(seven("合成产品", "正文。"), language="zh")
        build()
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["gate"]["approval_method"], "chat")
        self.assertIn("| 批准 | 已批准（对话确认） |", (out / "report.md").read_text(encoding="utf-8"))
        self.assertIn("用户在对话里确认", (out / "final_report.html").read_text(encoding="utf-8"))

    def test_terminal_approval_adds_nothing_to_the_gate(self) -> None:
        out = self.run_study("")
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("approval_method", manifest["gate"])
        self.assertIn("| 批准 | 已批准 |", (out / "report.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("USITC_TEST_PDF") == "1", "set USITC_TEST_PDF=1 to print with the local browser")
    def test_real_browser_prints_a_pdf(self) -> None:
        if final_module.find_browser() is None:
            self.skipTest("no Edge or Chrome")
        out = self.run_study("")
        (out / "brief.md").write_text(seven("合成产品", f"![x]({self.charts(out)[0]})"), encoding="utf-8")
        from usitc_pipeline.study_pipeline import final_report_study

        result = final_report_study(self.config, self.study)
        pdf = Path(result["final_report_pdf"]).read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10_000)


if __name__ == "__main__":
    unittest.main()
