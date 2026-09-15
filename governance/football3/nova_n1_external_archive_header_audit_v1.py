#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import re
import urllib.request
import zipfile
from datetime import datetime, timezone
from typing import Any

CANDIDATES = [
    {
        "candidate_id": "KAGGLE_MADFERIT_LALIGA_2014_2025",
        "provider": "Madferit94 - La Liga Matches 2014-2025 (Final Dataset)",
        "source_page": "https://www.kaggle.com/datasets/madferit/la-liga-matches-20142025-final-dataset",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/madferit/la-liga-matches-20142025-final-dataset",
        "declared_license": "CC BY 4.0",
        "target_league": "La liga",
        "declared_seasons": "2014/15-2024/25",
        "declared_match_n": 4180,
    },
    {
        "candidate_id": "KAGGLE_REDAALI_EPL_XG_2014_2025",
        "provider": "Reda Ali - EPL Expected Goals stats (2014-2025)",
        "source_page": "https://www.kaggle.com/datasets/redaali009/epl-expected-goals-stats-2014-2025",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/redaali009/epl-expected-goals-stats-2014-2025",
        "declared_license": "CC BY-SA 4.0",
        "target_league": "EPL",
        "declared_seasons": "2014/15-2024/25",
        "declared_match_n": None,
    },
    {
        "candidate_id": "KAGGLE_ABRAR_EPL_UNDERSTAT_2014_PRESENT",
        "provider": "Abrar - Understat Data for Teams + Players (2014-present)",
        "source_page": "https://www.kaggle.com/datasets/abrarhossainhimself/understat-data-for-teams-players-2014-present",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/abrarhossainhimself/understat-data-for-teams-players-2014-present",
        "declared_license": "CC0",
        "target_league": "EPL",
        "declared_seasons": "2014-present",
        "declared_match_n": None,
    },
    {
        "candidate_id": "KAGGLE_PETER_EPL_2024_2025_DETAILED",
        "provider": "Peter Vyboch - EPL 2024-2025 Detailed Match Data",
        "source_page": "https://www.kaggle.com/datasets/petervboch/epl-2024-2025-detailed-match-data",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/petervboch/epl-2024-2025-detailed-match-data",
        "declared_license": "CC0",
        "target_league": "EPL",
        "declared_seasons": "2024/25",
        "declared_match_n": 380,
    },
    {
        "candidate_id": "KAGGLE_MARCEL_SERIEA_2020_2025",
        "provider": "Marcel Biezunski - Serie A Matches Dataset (2020-2025)",
        "source_page": "https://www.kaggle.com/datasets/marcelbiezunski/serie-a-matches-dataset-2020-2025",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/marcelbiezunski/serie-a-matches-dataset-2020-2025",
        "declared_license": "CC BY-NC-SA 4.0",
        "target_league": "Serie A",
        "declared_seasons": "2020-2025",
        "declared_match_n": None,
    },
]

HEADER_ALIASES = {
    "date": {"date", "match_date", "datetime", "kickoff", "kickoff_date"},
    "home_team": {"home_team", "hometeam", "team_h", "home", "homeclub"},
    "away_team": {"away_team", "awayteam", "team_a", "away", "awayclub"},
    "home_deep": {"h_deep", "home_deep", "deep_home", "homedeep", "deep_h"},
    "away_deep": {"a_deep", "away_deep", "deep_away", "awaydeep", "deep_a"},
    "home_ppda": {"h_ppda", "home_ppda", "ppda_home", "homeppda", "ppda_h"},
    "away_ppda": {"a_ppda", "away_ppda", "ppda_away", "awayppda", "ppda_a"},
}
FORBIDDEN_LABEL_HINTS = {
    "result", "outcome", "winner", "home_goals", "away_goals", "h_goals", "a_goals",
    "fthg", "ftag", "score", "gf", "ga",
}
UA = {"User-Agent": "Football3-Nova-N1-source-audit/1.0", "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_header(value: str) -> str:
    s = value.strip().lstrip("\ufeff").casefold()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_header(raw: bytes) -> list[str]:
    first = raw.splitlines()[0] if raw.splitlines() else b""
    if not first:
        return []
    try:
        text = first.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = first.decode("latin-1")
    if '"' in text:
        import csv
        return next(csv.reader([text]))
    return [x.strip() for x in text.split(",")]


def classify_header(header: list[str]) -> dict[str, Any]:
    normalized = [norm_header(x) for x in header]
    lookup: dict[str, list[int]] = {}
    for i, name in enumerate(normalized):
        lookup.setdefault(name, []).append(i)
    bindings: dict[str, dict[str, Any] | None] = {}
    for role, aliases in HEADER_ALIASES.items():
        hits = [(name, idx) for name in sorted(aliases) for idx in lookup.get(name, [])]
        bindings[role] = None if len(hits) != 1 else {"column": header[hits[0][1]], "normalized": hits[0][0], "index": hits[0][1]}
    missing = [role for role in HEADER_ALIASES if bindings[role] is None]
    forbidden_present = sorted({name for name in normalized if name in FORBIDDEN_LABEL_HINTS})
    return {
        "column_n": len(header),
        "columns": header,
        "normalized_columns": normalized,
        "bindings": bindings,
        "missing_locked_roles": missing,
        "locked_feature_schema_complete": not missing,
        "forbidden_label_columns_present_in_archive_file": forbidden_present,
        "data_rows_opened": 0,
        "label_values_read": 0,
    }


def download(url: str, timeout: int = 120) -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(url, headers=UA)
    started = now()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        meta = {
            "requested_at": started,
            "retrieved_at": now(),
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "final_url": resp.geturl(),
            "bytes": len(raw),
            "sha256": sha256(raw),
        }
    if len(raw) < 500:
        raise RuntimeError(f"candidate response too small: {len(raw)}")
    return raw, meta


def audit_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    raw, fetch = download(candidate["download_url"])
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("candidate download is not a ZIP") from exc
    bad = zf.testzip()
    if bad:
        raise RuntimeError(f"candidate ZIP CRC failed: {bad}")
    members = []
    schema_candidates = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        suffix = pathlib.PurePosixPath(info.filename).suffix.casefold()
        item: dict[str, Any] = {"path": info.filename, "bytes": info.file_size, "suffix": suffix}
        if suffix in {".csv", ".tsv", ".txt"}:
            with zf.open(info) as f:
                prefix = f.read(131072)
            header = parse_header(prefix)
            cls = classify_header(header)
            item["header_audit"] = cls
            if cls["locked_feature_schema_complete"]:
                schema_candidates.append(info.filename)
        members.append(item)
    status = "HEADER_SCHEMA_QUALIFIED" if len(schema_candidates) == 1 else (
        "HEADER_SCHEMA_AMBIGUOUS" if len(schema_candidates) > 1 else "HEADER_SCHEMA_NOT_QUALIFIED"
    )
    return {
        "schema_version": "football3-nova-n1-external-archive-header-audit-v1",
        "status": status,
        "candidate": candidate,
        "fetch": fetch,
        "zip_crc": "PASS",
        "members": members,
        "locked_schema_candidate_files": schema_candidates,
        "archive_data_rows_opened": 0,
        "archive_label_values_read": 0,
        "test_identity_opened": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "audited_at": now(),
    }


def audit_candidate_safe(candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        return audit_candidate(candidate)
    except Exception as exc:
        return {
            "schema_version": "football3-nova-n1-external-archive-header-audit-v1",
            "status": "DOWNLOAD_OR_ARCHIVE_ERROR",
            "candidate": candidate,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "locked_schema_candidate_files": [],
            "archive_data_rows_opened": 0,
            "archive_label_values_read": 0,
            "test_identity_opened": False,
            "test_result_vault_opened": False,
            "test_labels_read": False,
            "scientific_parameters_changed": False,
            "formal_v2_modified": False,
            "current_modified": False,
            "production_modified": False,
            "audited_at": now(),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    reports = [audit_candidate_safe(c) for c in CANDIDATES]
    payload = {
        "schema_version": "football3-nova-n1-external-source-header-audits-v1",
        "reports": reports,
        "test_labels_read": False,
        "data_rows_opened": 0,
    }
    (args.out / "external_source_header_audit.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({r["candidate"]["candidate_id"]: r["status"] for r in reports}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
