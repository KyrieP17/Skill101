"""Acquire official Federal Reserve industrial-production series via FRED.

FRED is the distribution source; the underlying series are Federal Reserve
G.17 industrial-production indexes.  This module records raw levels and
retrieval provenance.  It does **not** call them product-level reshoring data.
The HTS-to-series link is maintained separately in
``config/hts_naics_proxy.csv`` as a manual, broad mapping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import re
import time
from typing import Iterable

import pandas as pd
import requests

from .config import ResearchConfig
from .dataweb import RETRYABLE_STATUS, file_sha256


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _validate_series_id(series_id: str) -> str:
    value = str(series_id).strip().upper()
    if not re.fullmatch(r"[A-Z0-9_\-]+", value):
        raise ValueError(f"Unsafe FRED series id: {series_id!r}")
    return value


def parse_fred_csv(text: str, *, series_id: str, retrieved_at: str) -> pd.DataFrame:
    """Normalize one FRED graph CSV response.

    FRED has used both ``DATE`` and ``observation_date`` for the first column;
    the parser recognizes either and rejects any response that lacks the series.
    Missing observations represented by ``.`` remain missing rather than zero.
    """

    series_id = _validate_series_id(series_id)
    frame = pd.read_csv(io.StringIO(text), dtype="string")
    if series_id not in frame.columns:
        raise ValueError(f"FRED CSV does not contain requested series {series_id}; columns={list(frame.columns)}")
    date_candidates = [column for column in frame.columns if column.lower() in {"date", "observation_date"}]
    if not date_candidates:
        raise ValueError("FRED CSV does not contain a recognized date column")
    result = frame[[date_candidates[0], series_id]].rename(
        columns={date_candidates[0]: "date", series_id: "value"}
    )
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["value"] = pd.to_numeric(result["value"].replace(".", pd.NA), errors="coerce")
    result = result[result["date"].notna()].copy()
    result.insert(0, "series_id", series_id)
    result["retrieved_at"] = retrieved_at
    return result[["series_id", "date", "value", "retrieved_at"]]


class FredClient:
    """Small cache-aware FRED CSV client; no API key is required."""

    def __init__(self, config: ResearchConfig, *, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def fetch(self, series_id: str, *, refresh: bool = False) -> pd.DataFrame:
        series_id = _validate_series_id(series_id)
        csv_path = self.config.paths.raw_fred / f"{series_id}.csv"
        meta_path = self.config.paths.raw_fred / f"{series_id}.meta.json"

        if csv_path.exists() and meta_path.exists() and not refresh:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if metadata.get("response_sha256") == file_sha256(csv_path):
                return parse_fred_csv(
                    csv_path.read_text(encoding="utf-8"),
                    series_id=series_id,
                    retrieved_at=str(metadata["retrieved_at_utc"]),
                )

        last_error: Exception | None = None
        response: requests.Response | None = None
        for attempt, delay in enumerate((0, 3, 10, 30), start=1):
            if delay:
                time.sleep(delay)
            try:
                response = self.session.get(
                    FRED_CSV_URL,
                    params={"id": series_id},
                    timeout=self.config.dataweb.timeout_seconds,
                )
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    break
                last_error = requests.HTTPError(f"Retryable FRED HTTP {response.status_code}", response=response)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            response = None
            if attempt == 4:
                break
        if response is None:
            raise RuntimeError(f"FRED request failed for {series_id}") from last_error

        retrieved_at = datetime.now(timezone.utc).isoformat()
        # Parse before writing so an HTML error page can never become a valid
        # cache merely because it arrived with HTTP 200.
        result = parse_fred_csv(response.text, series_id=series_id, retrieved_at=retrieved_at)
        if result.empty:
            raise RuntimeError(f"FRED returned no dated observations for {series_id}")

        raw_bytes = response.content
        raw_hash = sha256(raw_bytes).hexdigest()
        metadata = {
            "series_id": series_id,
            "source_url": response.url,
            "retrieved_at_utc": retrieved_at,
            "response_sha256": raw_hash,
            "observation_count": int(len(result)),
            "first_date": result["date"].min().date().isoformat(),
            "last_date": result["date"].max().date().isoformat(),
        }
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if csv_path.exists():
            archive = csv_path.with_name(f"{series_id}.{file_sha256(csv_path)[:12]}.superseded.csv")
            if not archive.exists():
                csv_path.replace(archive)
            if meta_path.exists():
                meta_path.replace(archive.with_suffix(".meta.json"))

        csv_tmp = csv_path.with_suffix(".csv.tmp")
        meta_tmp = meta_path.with_suffix(".json.tmp")
        csv_tmp.write_bytes(raw_bytes)
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        csv_tmp.replace(csv_path)
        meta_tmp.replace(meta_path)
        return result


def acquire_fred_series(
    config: ResearchConfig,
    series_ids: Iterable[str],
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download/cache several series and concatenate their normalized levels."""

    client = FredClient(config)
    unique = list(dict.fromkeys(_validate_series_id(value) for value in series_ids))
    frames = [client.fetch(series_id, refresh=refresh) for series_id in unique]
    if not frames:
        return pd.DataFrame(columns=["series_id", "date", "value", "retrieved_at"])
    return pd.concat(frames, ignore_index=True).sort_values(["series_id", "date"]).reset_index(drop=True)

