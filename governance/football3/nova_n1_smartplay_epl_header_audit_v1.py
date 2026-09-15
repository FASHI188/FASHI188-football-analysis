#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import urllib.request
from datetime import datetime, timezone
from typing import Any

CANDIDATE_ID = "SMARTPLAYFPL_HF_EPL_2020_2026"
PROVIDER = "SmartPlayFPL / Qazybek"
SOURCE_PROJECT = "https://github.com/qazybekb/smartplayfpl"
SOURCE_PROJECT_COMMIT = "3f1b252da271cc031ed4de4ff7cbd736e57f5e45"
SOURCE_LICENSE_BLOB_SHA = "09e163799efe14f8e2d4f2a723ed3804ca35dc6e"
SOURCE_DATA_README_BLOB_SHA = "c0bd8917f7ff43985a1e06bdd82f91299e3c2ed9"
SOURCE_DOWNLOAD_HELPER_BLOB_SHA = "775284d08b71c297c2f21d6ca8510e0bff8a48ee"
DECLARED_LICENSE = "CC BY-NC 4.0"
DATASET_REPO = "Qazybek/smartplay-fpl-dataset"
DATASET_FILE = "smartplay_data.csv"
DOWNLOAD_URL = f"https://huggingface.co/datasets/{DATASET_REPO}/resolve/main/{DATASET_FILE}"
TARGET_LEAGUE = "EPL"
TARGET_SEASON = "2024-25"
MAX_PREFIX_BYTES = 262144
UA = {
    "User-Agent": "Football3-Nova-N1-SmartPlay-header-audit/1.0",
    "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Encoding": "identity",
    "Range": f"bytes=0-{MAX_PREFIX_BYTES - 1}",
}

# Aliases are ordered by deterministic preference. Multiple different aliases are
# redundant columns, not an ambiguity. Duplicate occurrences of the same selected
# alias fail closed.
ROLE_ALIASES = {
    "season": ("season",),
    "fixture": ("fixture",),
    "team_name": ("team_name",),
    "opponent": ("opponent_team", "us_opponent"),
    "is_home": ("is_home",),
    "match_time": ("kickoff_time", "match_date"),
    "team_ppda": ("us_ppda",),
    "opponent_ppda": ("us_opp_ppda",),
    "team_deep": ("us_deep",),
    "opponent_deep": ("us_deep_allowed",),
}
FORBIDDEN_RESULT_OR_LABEL_COLUMNS = {
    "total_points", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "bonus", "bps", "starts",
    "team_a_score", "team_h_score", "us_goals", "us_assists", "us_xg", "us_xa",
    "us_team_xg", "us_team_xga", "us_team_npxgd", "expected_points", "expected_points_pre_deadline",
}


class HeaderAuditError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalize(name: str) -> str:
    return str(name).strip().lstrip("\ufeff").casefold()


def parse_first_csv_record(prefix: bytes) -> list[str]:
    if not prefix:
        raise HeaderAuditError("empty dataset prefix")
    try:
        text = prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HeaderAuditError("dataset header is not UTF-8") from exc
    sio = io.StringIO(text, newline="")
    try:
        row = next(csv.reader(sio))
    except (StopIteration, csv.Error) as exc:
        raise HeaderAuditError("unable to parse CSV header") from exc
    if not row:
        raise HeaderAuditError("empty CSV header")
    return [str(x).strip() for x in row]


def classify_header(header: list[str]) -> dict[str, Any]:
    normalized = [normalize(x) for x in header]
    positions: dict[str, list[int]] = {}
    for idx, name in enumerate(normalized):
        positions.setdefault(name, []).append(idx)
    bindings: dict[str, dict[str, Any] | None] = {}
    redundant_aliases: dict[str, list[str]] = {}
    ambiguous: list[str] = []
    missing: list[str] = []
    for role, aliases in ROLE_ALIASES.items():
        present = [alias for alias in aliases if positions.get(alias)]
        selected: tuple[str, int] | None = None
        for alias in aliases:
            idxs = positions.get(alias, [])
            if len(idxs) > 1:
                ambiguous.append(role)
                selected = None
                break
            if len(idxs) == 1:
                selected = (alias, idxs[0])
                break
        if role in ambiguous:
            bindings[role] = None
            continue
        if selected is None:
            bindings[role] = None
            missing.append(role)
            continue
        alias, idx = selected
        bindings[role] = {"column": header[idx], "normalized": alias, "index": idx}
        alternates = [x for x in present if x != alias]
        if alternates:
            redundant_aliases[role] = alternates
    forbidden_present = sorted(set(normalized) & {x.casefold() for x in FORBIDDEN_RESULT_OR_LABEL_COLUMNS})
    return {
        "column_n": len(header),
        "columns": header,
        "bindings": bindings,
        "redundant_aliases_present": redundant_aliases,
        "missing_required_roles": missing,
        "ambiguous_required_roles": ambiguous,
        "locked_feature_schema_complete": not missing and not ambiguous,
        "forbidden_result_or_label_columns_present_in_dataset": forbidden_present,
        "header_records_parsed": 1,
        "data_rows_parsed": 0,
        "label_values_read": 0,
        "result_values_read": 0,
    }


def fetch_prefix(url: str = DOWNLOAD_URL) -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read(MAX_PREFIX_BYTES)
        meta = {
            "requested_url": url,
            "final_url": resp.geturl(),
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "content_length": resp.headers.get("Content-Length"),
            "content_range": resp.headers.get("Content-Range"),
            "accept_ranges": resp.headers.get("Accept-Ranges"),
            "prefix_bytes_read": len(raw),
            "prefix_sha256": sha256(raw),
            "retrieved_at": now(),
        }
    if len(raw) < 64:
        raise HeaderAuditError(f"dataset prefix too small: {len(raw)}")
    return raw, meta


def build_report(prefix: bytes, fetch_meta: dict[str, Any]) -> dict[str, Any]:
    header = parse_first_csv_record(prefix)
    cls = classify_header(header)
    status = "HEADER_SCHEMA_QUALIFIED" if cls["locked_feature_schema_complete"] else "HEADER_SCHEMA_NOT_QUALIFIED"
    return {
        "schema_version": "football3-nova-n1-smartplay-epl-header-audit-v1",
        "status": status,
        "candidate_id": CANDIDATE_ID,
        "provider": PROVIDER,
        "target_league": TARGET_LEAGUE,
        "target_season": TARGET_SEASON,
        "dataset_repo": DATASET_REPO,
        "dataset_file": DATASET_FILE,
        "download_url": DOWNLOAD_URL,
        "source_project": SOURCE_PROJECT,
        "source_project_commit": SOURCE_PROJECT_COMMIT,
        "declared_license": DECLARED_LICENSE,
        "source_license_blob_sha": SOURCE_LICENSE_BLOB_SHA,
        "source_data_readme_blob_sha": SOURCE_DATA_README_BLOB_SHA,
        "source_download_helper_blob_sha": SOURCE_DOWNLOAD_HELPER_BLOB_SHA,
        "license_scope_evidence": "Pinned SmartPlayFPL repository LICENSE is CC BY-NC 4.0; pinned data README names smartplay_data.csv as training/evaluation data; pinned download helper maps that file to the Hugging Face dataset repository.",
        "source_provenance_note": "Dataset documentation attributes the safe team-match feature columns to the Understat API. This audit qualifies the redistributed SmartPlay dataset license/schema only; it does not assert a direct Understat license.",
        "fetch": fetch_meta,
        "header_audit": cls,
        "raw_dataset_persisted": False,
        "data_rows_parsed": 0,
        "label_values_read": 0,
        "result_values_read": 0,
        "test_identity_opened": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "promotion_authorized": False,
        "audited_at": now(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        prefix, fetch_meta = fetch_prefix()
        report = build_report(prefix, fetch_meta)
    except Exception as exc:
        report = {
            "schema_version": "football3-nova-n1-smartplay-epl-header-audit-v1",
            "status": "DOWNLOAD_OR_HEADER_ERROR",
            "candidate_id": CANDIDATE_ID,
            "provider": PROVIDER,
            "target_league": TARGET_LEAGUE,
            "target_season": TARGET_SEASON,
            "dataset_repo": DATASET_REPO,
            "dataset_file": DATASET_FILE,
            "download_url": DOWNLOAD_URL,
            "source_project_commit": SOURCE_PROJECT_COMMIT,
            "declared_license": DECLARED_LICENSE,
            "source_license_blob_sha": SOURCE_LICENSE_BLOB_SHA,
            "error_type": type(exc).__name__,
            "error": str(exc)[:800],
            "raw_dataset_persisted": False,
            "data_rows_parsed": 0,
            "label_values_read": 0,
            "result_values_read": 0,
            "test_identity_opened": False,
            "test_result_vault_opened": False,
            "test_labels_read": False,
            "scientific_parameters_changed": False,
            "formal_v2_modified": False,
            "current_modified": False,
            "production_modified": False,
            "promotion_authorized": False,
            "audited_at": now(),
        }
    out = args.out / "smartplay_epl_header_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": report["candidate_id"],
        "status": report["status"],
        "target_league": report["target_league"],
        "test_labels_read": report["test_labels_read"],
        "data_rows_parsed": report["data_rows_parsed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
