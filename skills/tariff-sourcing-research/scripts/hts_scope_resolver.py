#!/usr/bin/env python3
"""Resolve product text into reviewable USITC HTS candidates.

This helper uses the public HTS search endpoint. It does not read a DataWeb
token, choose a final code, edit research configuration, or approve a scope.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://hts.usitc.gov/reststop/search"
VALID_LEVELS = {2, 4, 6, 8, 10}
COLUMNS = [
    "candidate_id",
    "product_id",
    "product_name",
    "search_queries",
    "best_rank",
    "official_htsno",
    "hts_code",
    "hts_level",
    "hts2",
    "hts4",
    "hts6",
    "hts8",
    "hts10",
    "description",
    "units",
    "general_rate",
    "special_rate",
    "other_rate",
    "statistical_suffix",
    "indent",
    "special_chapter",
    "review_status",
    "source_url",
    "retrieved_at_utc",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    """Return readable text while preserving the source meaning."""

    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = " ".join(html.unescape(text).split())
    # Replacing a tag with a space must not leave "10 kg ," behind.
    return re.sub(r" ([,.;:)])", r"\1", text)


def code_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def code_level(code: str) -> int | None:
    return len(code) if len(code) in VALID_LEVELS else None


def units_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join(clean_text(item) for item in value if clean_text(item))
    return clean_text(value)


def search_url(endpoint: str, keyword: str) -> str:
    return endpoint.rstrip("?") + "?" + urlencode({"keyword": keyword})


def fetch_results(endpoint: str, keyword: str, timeout: int) -> tuple[str, list[dict[str, Any]]]:
    """Return the URL and the result rows (hts_current_check.py uses this too)."""

    url, rows, _ = fetch_response(endpoint, keyword, timeout)
    return url, rows


def fetch_response(endpoint: str, keyword: str, timeout: int) -> tuple[str, list[dict[str, Any]], bytes]:
    """Return the URL, the result rows, and the response body exactly as served."""

    url = search_url(endpoint, keyword)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "usitc-hts-scope-resolver/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
        payload = json.loads(body)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"HTS search failed for {keyword!r}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"HTS search returned {type(payload).__name__}, expected a JSON list")
    return url, [item for item in payload if isinstance(item, dict)], body


def search_terms(keywords: Iterable[str], phrases: Iterable[str]) -> list[str]:
    """Keywords as given, then each phrase in double quotes (no shell quoting needed)."""

    terms = [item.strip() for item in keywords] + [f'"{item.strip().strip(chr(34))}"' for item in phrases]
    return list(dict.fromkeys(term for term in terms if term.strip('"')))


def normalize_results(
    *,
    product_id: str,
    product_name: str,
    searches: Iterable[tuple[str, str, list[dict[str, Any]]]],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    """Normalize and de-duplicate official results without selecting a winner."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for keyword, url, rows in searches:
        for rank, row in enumerate(rows, start=1):
            displayed = clean_text(row.get("htsno"))
            digits = code_digits(displayed)
            level = code_level(digits)
            if level is None:
                continue
            description = clean_text(row.get("description"))
            key = (digits, description)
            if key not in merged:
                candidate_id = sha256(f"{digits}\n{description}".encode("utf-8")).hexdigest()[:16]
                merged[key] = {
                    "candidate_id": candidate_id,
                    "product_id": product_id,
                    "product_name": product_name,
                    "search_queries": [],
                    "best_rank": rank,
                    "official_htsno": displayed,
                    "hts_code": digits,
                    "hts_level": level,
                    "hts2": digits[:2] if level >= 2 else "",
                    "hts4": digits[:4] if level >= 4 else "",
                    "hts6": digits[:6] if level >= 6 else "",
                    "hts8": digits[:8] if level >= 8 else "",
                    "hts10": digits[:10] if level >= 10 else "",
                    "description": description,
                    "units": units_text(row.get("units")),
                    "general_rate": clean_text(row.get("general")),
                    "special_rate": clean_text(row.get("special")),
                    "other_rate": clean_text(row.get("other")),
                    "statistical_suffix": clean_text(row.get("statisticalSuffix")),
                    "indent": clean_text(row.get("indent")),
                    "special_chapter": digits[:2] in {"98", "99"},
                    "review_status": "candidate",
                    "source_url": url,
                    "retrieved_at_utc": retrieved_at,
                }
            record = merged[key]
            record["best_rank"] = min(int(record["best_rank"]), rank)
            if keyword not in record["search_queries"]:
                record["search_queries"].append(keyword)

    candidates = list(merged.values())
    for record in candidates:
        record["search_queries"] = " | ".join(record["search_queries"])
    return sorted(candidates, key=lambda item: (int(item["best_rank"]), str(item["hts_code"]), str(item["description"])))


def write_csv(path: Path, rows: list[dict[str, Any]], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing candidate file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing raw file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_bytes(path: Path, body: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing structure file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate reviewable HTS candidates from public USITC search")
    parser.add_argument("--product-id", required=True, help="Stable local product identifier")
    parser.add_argument("--product-name", required=True, help="Human-readable product name")
    parser.add_argument("--keyword", action="append", default=[], help="HTS search text; repeat for synonyms")
    parser.add_argument(
        "--phrase",
        action="append",
        default=[],
        help="Exact phrase; the resolver adds the double quotes, so PowerShell cannot strip them. Repeatable",
    )
    parser.add_argument("--output", type=Path, help="Candidate CSV path; omit to print candidate JSON")
    parser.add_argument("--raw-output", type=Path, help="Optional raw search snapshot JSON")
    parser.add_argument(
        "--structure-output",
        type=Path,
        help="With one --keyword (an HTS6 code such as 8517.13): save the response unchanged as the study's "
        "[drilldown] structure file; the summary prints its URL, bytes and SHA-256 for SOURCES.md",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Override only for testing or an official endpoint change")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--max-candidates", type=int, default=200, help="Stop broad searches above this review budget")
    parser.add_argument("--allow-broad", action="store_true", help="Allow output above --max-candidates")
    parser.add_argument("--force", action="store_true", help="Explicitly replace output files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0 or args.max_candidates <= 0:
        raise SystemExit("--timeout and --max-candidates must be positive")
    retrieved_at = utc_now()
    searches: list[tuple[str, str, list[dict[str, Any]]]] = []
    raw_searches: list[dict[str, Any]] = []
    structure: dict[str, Any] | None = None
    try:
        terms = search_terms(args.keyword, args.phrase)
        if not terms:
            raise ValueError("At least one non-empty --keyword or --phrase is required")
        if args.structure_output and len(terms) != 1:
            raise ValueError("--structure-output saves one search: pass exactly one --keyword, the HTS6 code (e.g. 8517.13)")
        if args.structure_output and args.structure_output.exists() and not args.force:
            raise FileExistsError(f"Refusing to overwrite existing structure file: {args.structure_output.resolve()}")
        for keyword in terms:
            url, results, body = fetch_response(args.endpoint, keyword, args.timeout)
            searches.append((keyword, url, results))
            raw_searches.append({"keyword": keyword, "url": url, "results": results})
            if args.structure_output:
                if not any("htsno" in row for row in results):
                    raise ValueError(f"The search for {keyword!r} returned no HTS rows; nothing to save as a structure")
                structure = {"path": args.structure_output.resolve(), "url": url, "body": body}
        candidates = normalize_results(
            product_id=args.product_id.strip(),
            product_name=args.product_name.strip(),
            searches=searches,
            retrieved_at=retrieved_at,
        )
        if len(candidates) > args.max_candidates and not args.allow_broad:
            raise ValueError(
                f"Search returned {len(candidates)} candidates, above the review budget of "
                f"{args.max_candidates}. Use an exact phrase or more product attributes; "
                "pass --allow-broad only when the full broad set is intentional."
            )
        if args.output:
            write_csv(args.output.resolve(), candidates, force=args.force)
        if args.raw_output:
            write_json(
                args.raw_output.resolve(),
                {
                    "retrieved_at_utc": retrieved_at,
                    "endpoint": args.endpoint,
                    "product_id": args.product_id,
                    "product_name": args.product_name,
                    "searches": raw_searches,
                },
                force=args.force,
            )
        if structure is not None:
            write_bytes(structure["path"], structure["body"], force=args.force)
    except (RuntimeError, ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "product_id": args.product_id,
        "product_name": args.product_name,
        "queries": [item[0] for item in searches],
        "candidate_count": len(candidates),
        "review_budget": args.max_candidates,
        "candidate_csv": str(args.output.resolve()) if args.output else None,
        "raw_snapshot": str(args.raw_output.resolve()) if args.raw_output else None,
        "structure_file": None
        if structure is None
        else {
            "path": str(structure["path"]),
            "url": structure["url"],
            "bytes": len(structure["body"]),
            "sha256": sha256(structure["body"]).hexdigest(),
            "retrieved_at_utc": retrieved_at,
        },
        "dataweb_token_used": False,
        "approval_status": "candidate_only",
    }
    if args.output:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"summary": summary, "candidates": candidates}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
