"""The final report: the agent-written ``brief.md`` plus an appendix, as one HTML file and a PDF.

It reads only validated outputs. The study is validated again first; every chart the
brief embeds must be a hash-verified entry of ``chart_manifest.json``; the appendix comes
from ``analysis_manifest.json``, the validation result, ``findings.json`` and
``tables/world_check.csv``. Nothing is recalculated.

The PDF is printed by a local Chromium browser (Edge or Chrome) in headless mode with a
throwaway profile. Writes ``final_report.html``, ``final_report.pdf`` and
``final_report.json`` (what went in, with hashes) beside ``brief.md``; running it again
replaces those three files and nothing else.
"""

from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable

from .proxy_notes import display_quantity_unit

BRIEF = "brief.md"
OUTPUTS = ("final_report.html", "final_report.pdf", "final_report.json")
BROWSER_ENV = "USITC_PDF_BROWSER"
BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
BROWSER_NAMES = ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser")


class FinalReportError(ValueError):
    """The brief or its inputs cannot become a final report."""


LABELS = {
    "zh": {
        "appendix": "附录",
        "identity": "A. 研究身份",
        "checks": "B. 数据核对",
        "boundaries": "C. 不能下的结论",
        "charts": "D. 其他图表",
        "files": "E. 文件与哈希",
        "item": "项目",
        "value": "内容",
        "product": "产品",
        "scope": "scope",
        "study": "study",
        "plan": "plan",
        "data": "数据",
        "data_value": "USITC DataWeb，U.S. imports for consumption；数据截至 {through}（查询范围到 {query}，数据截止之后的期间 DataWeb 还没有数）",
        "approval": "批准",
        "approved": "已批准",
        "approved_in_chat": "（用户在对话里确认，agent 运行 scope-approve 记录；原话见 scope manifest）",
        "not_approved": "未批准",
        "replay": "回放结果，不是已批准研究",
        "continuity": "HTS 连续性",
        "verified": "已核实",
        "unverified": "未核实",
        "quantity": "申报数量口径",
        "quantity_value": "DataWeb 第一统计计量单位 {first}；第二统计计量单位 {second}（选用 basis = {basis}）",
        "generated": "分析生成时间（UTC）",
        "validation": "panel 验证",
        "passed": "通过",
        "failed": "未通过",
        "validation_value": "{status}：{rows} 行，{periods} 个产品期，{errors} 个错误，{warnings} 个警告；{charts} 张图都有同名数据 CSV",
        "world": "来源地加总对 World",
        "world_value": "{rows} 行，状态 {status}；最大偏差：金额 {value}，数量 {quantity}，计算关税 {duty}",
        "world_missing": "没有 tables/world_check.csv",
        "coverage": "编码覆盖",
        "coverage_value": "{codes} 行编码-频率，{review} 行需要复核，{missing} 行有缺口",
        "brief_check": "正文 brief-check",
        "brief_check_value": "{status}：{tokens} 个数字 token，{parsed} 个已识别单位，{traced} 个在所引的已验证文件里找到；{ambiguous} 个单位歧义，{skipped} 个无单位跳过；引用 {sources} 个文件；{warnings} 个警告",
        "chart_data": "图表数据：",
        "no_charts": "正文已经放了全部图表。",
        "file": "文件",
        "path": "路径",
        "sha": "SHA-256（前 12 位）",
        "brief_note": "正文是 brief.md 原文（SHA-256 前 12 位 {sha}），附录由 final_report 从已验证的文件生成。",
        "page": "第 {page} 页",
    },
    "en": {
        "appendix": "Appendix",
        "identity": "A. Study identity",
        "checks": "B. Data checks",
        "boundaries": "C. What this cannot show",
        "charts": "D. Other charts",
        "files": "E. Files and hashes",
        "item": "Item",
        "value": "Value",
        "product": "Product",
        "scope": "Scope",
        "study": "Study",
        "plan": "Plan",
        "data": "Data",
        "data_value": "USITC DataWeb, U.S. imports for consumption; data through {through} (queried through {query}; DataWeb has no data yet after the data end)",
        "approval": "Approval",
        "approved": "approved",
        "approved_in_chat": " (confirmed by the user in chat and recorded by the agent's scope-approve run; their words are in the scope manifest)",
        "not_approved": "not approved",
        "replay": "Replay result, not an approved study",
        "continuity": "HTS continuity",
        "verified": "verified",
        "unverified": "not verified",
        "quantity": "Reported quantity basis",
        "quantity_value": "DataWeb first statistical unit {first}; second statistical unit {second} (selected basis = {basis})",
        "generated": "Analysis generated (UTC)",
        "validation": "Panel validation",
        "passed": "passed",
        "failed": "failed",
        "validation_value": "{status}: {rows} rows, {periods} product periods, {errors} errors, {warnings} warnings; all {charts} charts have their data CSV",
        "world": "Origins against World",
        "world_value": "{rows} rows, status {status}; largest gap: value {value}, quantity {quantity}, calculated duty {duty}",
        "world_missing": "no tables/world_check.csv",
        "coverage": "Code coverage",
        "coverage_value": "{codes} code-frequency rows, {review} need review, {missing} have gaps",
        "brief_check": "Body brief-check",
        "brief_check_value": "{status}: {tokens} numeric tokens, {parsed} with a recognized unit, {traced} found in the validated files they cite; {ambiguous} ambiguous units, {skipped} unitless skips; {sources} files cited; {warnings} warnings",
        "chart_data": "Chart data: ",
        "no_charts": "The body already shows every chart.",
        "file": "File",
        "path": "Path",
        "sha": "SHA-256 (first 12)",
        "brief_note": "The body is brief.md as written (SHA-256 {sha}…); the appendix is generated by final_report from validated files.",
        "page": "Page {page}",
    },
}

INLINE = re.compile(
    r"`(?P<code>[^`]+)`"
    r"|!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)"
    r"|\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
IMAGE_LINE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
ImageSource = Callable[[str], str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inline_html(text: str, image: ImageSource) -> str:
    """Escape ``text`` and render code spans, images, links and bold."""

    out: list[str] = []
    position = 0
    for match in INLINE.finditer(text):
        out.append(html.escape(text[position:match.start()]))
        if match["code"] is not None:
            out.append(f"<code>{html.escape(match['code'])}</code>")
        elif match["src"] is not None:
            out.append(f'<img src="{image(match["src"])}" alt="{html.escape(match["alt"])}">')
        elif match["href"] is not None:
            label = inline_html(match["text"], image)
            if re.match(r"https?://", match["href"]):
                out.append(f'<a href="{html.escape(match["href"])}">{label}</a>')
            else:  # a local path means nothing inside a PDF; keep the words
                out.append(label)
        else:
            out.append(f"<strong>{inline_html(match['bold'], image)}</strong>")
        position = match.end()
    out.append(html.escape(text[position:]))
    return "".join(out)


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _alignments(rule: str) -> list[str]:
    aligns = []
    for cell in _cells(rule):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "")
    return aligns


def _table_html(rows: list[str], image: ImageSource) -> str:
    header, aligns, body = _cells(rows[0]), _alignments(rows[1]), [_cells(row) for row in rows[2:]]

    def cell(tag: str, index: int, text: str) -> str:
        align = aligns[index] if index < len(aligns) else ""
        style = f' style="text-align:{align}"' if align else ""
        short = ' class="nw"' if 0 < len(text) <= 10 else ""  # dates, "生效", short numbers stay on one line
        return f"<{tag}{short}{style}>{inline_html(text, image)}</{tag}>"

    head = "".join(cell("th", index, text) for index, text in enumerate(header))
    lines = ["<table>", f"<thead><tr>{head}</tr></thead>", "<tbody>"]
    for row in body:
        row = (row + [""] * len(header))[: max(len(header), len(row))]
        lines.append("<tr>" + "".join(cell("td", index, text) for index, text in enumerate(row)) + "</tr>")
    lines += ["</tbody>", "</table>"]
    return "\n".join(lines)


def _list_html(items: list[tuple[int, bool, str]], image: ImageSource) -> str:
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    for indent, ordered, text in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            out.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0] and stack[-1][1] == tag:
            out.append("</li>")
        else:
            if stack and indent == stack[-1][0]:
                out.append(f"</li></{stack.pop()[1]}>")
            out.append(f"<{tag}>")
            stack.append((indent, tag))
        out.append(f"<li>{inline_html(text, image)}")
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def markdown_html(text: str, image: ImageSource) -> tuple[str, str]:
    """Render the Markdown a brief uses; return (first H1 or "", body HTML).

    Covers headings, paragraphs, pipe tables, nested lists, block quotes, rules,
    images on their own line (as figures), code spans, links and bold. Raw HTML
    in the brief is escaped, never passed through.
    """

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    title = ""

    def flush() -> None:
        if paragraph:
            blocks.append(f"<p>{inline_html(' '.join(part.strip() for part in paragraph), image)}</p>")
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue
        heading = HEADING.match(stripped)
        if heading:
            flush()
            level = len(heading[1])
            if level == 1 and not title:
                title = heading[2]
            blocks.append(f"<h{level}>{inline_html(heading[2], image)}</h{level}>")
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and TABLE_RULE.match(lines[index + 1]):
            flush()
            rows = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index])
                index += 1
            blocks.append(_table_html(rows, image))
            continue
        image_line = IMAGE_LINE.match(line)
        if image_line:
            flush()
            alt = html.escape(image_line[1])
            blocks.append(f'<figure><img src="{image(image_line[2])}" alt="{alt}"></figure>')
            index += 1
            continue
        if LIST_ITEM.match(line):
            flush()
            items: list[tuple[int, bool, str]] = []
            while index < len(lines):
                item = LIST_ITEM.match(lines[index])
                if item:
                    items.append((len(item[1].expandtabs(4)), item[2][0].isdigit(), item[3]))
                elif items and lines[index].strip() and lines[index][:1].isspace():
                    indent, ordered, previous = items[-1]
                    items[-1] = (indent, ordered, f"{previous} {lines[index].strip()}")
                else:
                    break
                index += 1
            blocks.append(_list_html(items, image))
            continue
        if stripped.startswith(">"):
            flush()
            quoted = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quoted.append(lines[index].strip()[1:].strip())
                index += 1
            blocks.append(f"<blockquote><p>{inline_html(' '.join(quoted), image)}</p></blockquote>")
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            blocks.append("<hr>")
            index += 1
            continue
        paragraph.append(line)
        index += 1
    flush()
    return title, "\n".join(blocks)


def report_language(text: str, requested: str | None = None) -> str:
    """Return the explicitly requested report language; default to English."""

    if requested is None:
        return "en"
    if requested not in LABELS:
        raise FinalReportError(f"Unknown report language {requested!r}; use one of {sorted(LABELS)}")
    return requested


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}%"


def _world_summary(path: Path, words: dict[str, str]) -> str:
    if not path.is_file():
        return words["world_missing"]
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    def largest(column: str) -> float | None:
        values = [abs(float(row[column])) for row in rows if row.get(column) not in (None, "")]
        return max(values) if values else None

    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row.get("status") or "?"] = statuses.get(row.get("status") or "?", 0) + 1
    status = ", ".join(f"{key} {count}" for key, count in sorted(statuses.items()))
    return words["world_value"].format(
        rows=len(rows),
        status=status,
        value=_pct(largest("world_value_gap_pct")),
        quantity=_pct(largest("world_quantity_gap_pct")),
        duty=_pct(largest("duty_gap_pct")),
    )


def _coverage_summary(path: Path, words: dict[str, str]) -> str | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    review = sum(str(row.get("needs_review", "")).strip().lower() == "true" for row in rows)
    missing = sum(str(row.get("missing_period_count", "0")).strip() not in ("", "0") for row in rows)
    return words["coverage_value"].format(codes=len(rows), review=review, missing=missing)


def _kv_table(rows: list[tuple[str, str]], words: dict[str, str]) -> str:
    body = "".join(f"<tr><th>{html.escape(key)}</th><td>{value}</td></tr>" for key, value in rows)
    return (
        f'<table class="kv"><thead><tr><th>{words["item"]}</th><th>{words["value"]}</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _code(value: Any) -> str:
    return f"<code>{html.escape(str(value))}</code>"


def appendix_html(
    *,
    manifest: dict[str, Any],
    validation: dict[str, Any],
    findings: dict[str, Any],
    other_charts: list[tuple[dict[str, Any], str]],
    output_dir: Path,
    brief_sha256: str,
    language: str,
    brief_check: dict[str, Any] | None = None,
) -> str:
    words = LABELS[language]
    gate = manifest.get("gate") or {}
    through = ", ".join(f"{key} {value}" for key, value in (manifest.get("data_through") or {}).items() if value)
    query = ", ".join(f"{key} {value}" for key, value in (manifest.get("query_through") or {}).items() if value)
    codes = ", ".join(manifest.get("included_codes") or [])
    approval = words["approved"] if gate.get("scope_approved") else f"<strong>{words['not_approved']}</strong>"
    if gate.get("scope_approved") and gate.get("approval_method") == "chat":
        approval += words["approved_in_chat"]
    if gate.get("mode") == "legacy_replay":
        approval += ("；" if language == "zh" else "; ") + f"<strong>{words['replay']}</strong>"
    identity = [
        (words["product"], html.escape(f"{manifest.get('product_name')} (HTS{manifest.get('hts_level')}: {codes})")),
        (words["scope"], f"{html.escape(str(manifest.get('scope_id')))} v{html.escape(str(manifest.get('scope_version')))}"
                         f"<br>scope_sha256 {_code(manifest.get('scope_sha256'))}"),
        (words["study"], f"{html.escape(str(manifest.get('study_id')))}<br>study_sha256 {_code(manifest.get('study_sha256'))}"),
        (words["plan"], f"plan_sha256 {_code(manifest.get('plan_sha256'))}"),
        (words["data"], html.escape(words["data_value"].format(through=through or "—", query=query or "—"))),
        (words["approval"], approval),
        (words["continuity"], words["verified"] if gate.get("continuity_verified") else f"<strong>{words['unverified']}</strong>"),
    ]
    if manifest.get("quantity_unit"):
        identity.append((words["quantity"], html.escape(words["quantity_value"].format(
            first=display_quantity_unit(manifest.get("quantity_unit")),
            second=display_quantity_unit(manifest.get("second_unit")) or "—",
            basis=manifest.get("quantity_basis") or "first",
        ))))
    identity.append((words["generated"], html.escape(str(manifest.get("generated_at_utc", "—")))))

    checks = [
        (words["validation"], html.escape(words["validation_value"].format(
            status=words["passed"] if validation.get("ok") else words["failed"],
            rows=validation.get("rows"),
            periods=validation.get("product_periods"),
            errors=len(validation.get("errors") or []),
            warnings=len(validation.get("warnings") or []),
            charts=validation.get("charts"),
        ))),
        (words["world"], html.escape(_world_summary(output_dir / "tables" / "world_check.csv", words))),
    ]
    coverage = _coverage_summary(output_dir / "tables" / "coverage_audit.csv", words)
    if coverage:
        checks.append((words["coverage"], html.escape(coverage)))
    if brief_check is not None:
        counts = brief_check.get("numbers") or {}
        checks.append((words["brief_check"], html.escape(words["brief_check_value"].format(
            status=words["passed"] if brief_check.get("status") == "pass" else words["failed"],
            tokens=counts.get("numeric_tokens", counts.get("checked", 0) + counts.get("without_unit", 0)),
            parsed=counts.get("parsed", counts.get("checked", 0)),
            traced=counts.get("traced", 0),
            ambiguous=counts.get("ambiguous", 0),
            skipped=counts.get("skipped", counts.get("without_unit", 0)),
            sources=len(brief_check.get("sources_cited") or []),
            warnings=len(brief_check.get("warnings") or []),
        ))))

    boundaries = findings.get("boundaries") if language == "zh" else manifest.get("interpretation_boundaries")
    boundaries = boundaries or findings.get("boundaries") or manifest.get("interpretation_boundaries") or []

    parts = [
        f'<section class="appendix"><h1>{words["appendix"]}</h1>',
        f'<p class="note">{html.escape(words["brief_note"].format(sha=brief_sha256[:12]))}</p>',
        f"<h2>{words['identity']}</h2>",
        _kv_table(identity, words),
        f"<h2>{words['checks']}</h2>",
        _kv_table(checks, words),
        f"<h2>{words['boundaries']}</h2>",
        "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in boundaries) + "</ul>",
        f"<h2>{words['charts']}</h2>",
    ]
    if other_charts:
        for chart, data_uri in other_charts:
            parts.append(
                f'<figure class="chart"><img src="{data_uri}" alt="{html.escape(chart["stem"])}">'
                f'<figcaption>{html.escape(chart["stem"])} · {words["chart_data"]}{_code(chart["chart_data"]["path"])}'
                "</figcaption></figure>"
            )
    else:
        parts.append(f"<p>{words['no_charts']}</p>")
    files = [("brief", BRIEF, brief_sha256)] + [
        (name, item["path"], item["sha256"])
        for name, item in sorted((manifest.get("outputs") or {}).items())
        if isinstance(item, dict)
    ]
    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{_code(path)}</td><td>{_code(sha[:12])}</td></tr>" for name, path, sha in files
    )
    parts += [
        f"<h2>{words['files']}</h2>",
        f'<table class="files"><thead><tr><th>{words["file"]}</th><th>{words["path"]}</th><th>{words["sha"]}</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>",
        "</section>",
    ]
    return "\n".join(parts)


STYLE = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-center { content: counter(page) " / " counter(pages); font: 8pt "Segoe UI", sans-serif; color: #777; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #1d1d1f; background: #fff; font: 10pt/1.55 "Segoe UI", "Microsoft YaHei", "PingFang SC",
  "Noto Sans CJK SC", "Hiragino Sans", sans-serif; }
main { max-width: 180mm; margin: 0 auto; }
h1 { font-size: 17pt; line-height: 1.3; margin: 0 0 8pt; padding-bottom: 6pt; border-bottom: 2px solid #1f4e79; color: #12324f; }
h2 { font-size: 12.5pt; margin: 16pt 0 6pt; color: #12324f; break-after: avoid; }
h3, h4 { font-size: 11pt; margin: 12pt 0 4pt; break-after: avoid; }
p { margin: 0 0 6pt; }
ul, ol { margin: 0 0 6pt; padding-left: 18pt; }
li { margin: 2pt 0; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 8.5pt; background: #f1f3f5; padding: 0 2pt; border-radius: 2pt; }
strong { color: #000; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt; font-size: 8.3pt; line-height: 1.35; break-inside: auto; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td { border: 0.6pt solid #c9d1d9; padding: 2.5pt 4pt; vertical-align: top; text-align: left; }
thead th { background: #e8eef5; font-weight: 600; }
th.nw, td.nw { white-space: nowrap; }
table.kv tbody th { width: 24%; background: #f6f8fa; font-weight: 600; }
table.files { font-size: 7.6pt; }
figure { margin: 6pt 0 10pt; text-align: center; break-inside: avoid; }
figure img, p img { max-width: 100%; max-height: 118mm; }
figcaption { font-size: 8pt; color: #555; margin-top: 2pt; }
blockquote { margin: 6pt 0; padding: 4pt 8pt; border-left: 3pt solid #c0392b; background: #fdf3f2; }
hr { border: 0; border-top: 0.6pt solid #c9d1d9; margin: 10pt 0; }
.appendix { break-before: page; }
.appendix h1 { font-size: 15pt; }
.note { font-size: 8.5pt; color: #555; }
"""


def page_html(title: str, body: str, appendix: str, language: str) -> str:
    lang = "zh-CN" if language == "zh" else "en"
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n"
        f"<body>\n<main>\n{body}\n{appendix}\n</main>\n</body>\n</html>\n"
    )


def find_browser(explicit: str | Path | None = None) -> Path | None:
    """The Chromium browser that prints the PDF: ``explicit``, then $USITC_PDF_BROWSER, then Edge or Chrome."""

    for candidate in (explicit, os.environ.get(BROWSER_ENV)):
        if candidate:
            path = Path(candidate)
            if not path.is_file():
                raise FinalReportError(f"PDF browser not found: {path}")
            return path
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def print_pdf(html_path: Path, pdf_path: Path, browser: Path, *, timeout: int = 180) -> None:
    """Print ``html_path`` to ``pdf_path`` with headless Chromium and a throwaway profile."""

    partial = pdf_path.with_name(pdf_path.stem + ".partial.pdf")
    partial.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="usitc-pdf-", ignore_cleanup_errors=True) as profile:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={partial}",
            html_path.resolve().as_uri(),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FinalReportError(f"PDF printing timed out after {timeout}s ({browser.name})") from exc
    if not partial.is_file() or partial.stat().st_size == 0 or not partial.read_bytes().startswith(b"%PDF"):
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:] or ["no output"]
        partial.unlink(missing_ok=True)
        raise FinalReportError(f"{browser.name} did not write a PDF (exit {completed.returncode}): {detail[0]}")
    os.replace(partial, pdf_path)


def build_final_report(
    output_dir: Path,
    *,
    manifest: dict[str, Any],
    validation: dict[str, Any],
    findings: dict[str, Any],
    chart_manifest: dict[str, Any],
    language: str | None = None,
    browser: str | Path | None = None,
    printer: Callable[[Path, Path], None] | None = None,
    brief_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write final_report.html, final_report.pdf and final_report.json from brief.md.

    The caller has already validated the study and verified every chart artifact in
    ``chart_manifest``. ``printer`` replaces the browser (tests). ``brief_check`` is the
    summary of a passing brief-check of this same brief; it is kept in final_report.json.
    """

    output_dir = Path(output_dir)
    brief_path = output_dir / BRIEF
    if not brief_path.is_file():
        raise FinalReportError(f"No {BRIEF} in {output_dir}; write the brief first (deliverables.md), then run report-pdf")
    text = brief_path.read_text(encoding="utf-8")
    language = report_language(text, language)
    charts = {chart["png"]["path"]: chart for chart in chart_manifest.get("charts") or []}
    embedded: list[str] = []

    def data_uri(relative: str) -> str:
        key = relative.replace("\\", "/").removeprefix("./")
        chart = charts.get(key)
        if chart is None:
            raise FinalReportError(f"{BRIEF} embeds {relative!r}, which is not a validated chart in chart_manifest.json")
        path = output_dir / key
        if _sha256(path) != chart["png"]["sha256"]:
            raise FinalReportError(f"Chart changed after chart_manifest.json was written: {key}")
        if key not in embedded:
            embedded.append(key)
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    title, body = markdown_html(text, data_uri)
    if not title:
        raise FinalReportError(f"{BRIEF} needs a level-1 heading (# Title)")
    body_charts = list(embedded)
    others = [(chart, data_uri(path)) for path, chart in charts.items() if path not in body_charts]
    appendix_charts = [chart["png"]["path"] for chart, _ in others]
    brief_sha = _sha256(brief_path)
    if brief_check is not None and brief_check.get("brief_sha256") != brief_sha:
        raise FinalReportError(f"{BRIEF} changed after brief-check; run report-pdf again")
    appendix = appendix_html(
        manifest=manifest,
        validation=validation,
        findings=findings,
        other_charts=others,
        output_dir=output_dir,
        brief_sha256=brief_sha,
        language=language,
        brief_check=brief_check,
    )
    html_path, pdf_path, record_path = (output_dir / name for name in OUTPUTS)
    html_path.write_text(page_html(title, body, appendix, language), encoding="utf-8")
    if printer is None:
        found = find_browser(browser)
        if found is None:
            raise FinalReportError(
                f"Wrote {html_path.name}, but found no Edge or Chrome to print the PDF; pass --browser or set {BROWSER_ENV}"
            )
        printer = lambda source, target: print_pdf(source, target, found)  # noqa: E731
        printed_by = found.name
    else:
        printed_by = "custom printer"
    printer(html_path, pdf_path)
    if not pdf_path.is_file():
        raise FinalReportError("The PDF printer finished without writing final_report.pdf")
    record = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "language": language,
        "title": title,
        "printed_by": printed_by,
        "inputs": {
            "brief": {"path": BRIEF, "sha256": brief_sha},
            "analysis_manifest_sha256": _sha256(output_dir / "analysis_manifest.json"),
            "chart_manifest_sha256": _sha256(output_dir / "chart_manifest.json"),
            "findings_sha256": _sha256(output_dir / "findings.json"),
        },
        "identity": {key: manifest.get(key) for key in ("study_id", "scope_sha256", "plan_sha256", "study_sha256")},
        "body_charts": body_charts,
        "appendix_charts": appendix_charts,
        "outputs": {
            name: {"path": name, "sha256": _sha256(output_dir / name)} for name in OUTPUTS[:2]
        },
    }
    if brief_check is not None:
        record["brief_check"] = brief_check
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "final_report_pdf": str(pdf_path),
        "final_report_html": str(html_path),
        "final_report_record": str(record_path),
        "language": language,
        "body_charts": body_charts,
        "appendix_charts": appendix_charts,
    }
