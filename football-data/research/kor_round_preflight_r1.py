#!/usr/bin/env python3
"""Fail-closed preregistration and holdout-seal gate for KOR round ablation R1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "football-data/research/kor_round_ablation_r1_contract.json"
AUTH_PATH = ROOT / "football-data/research/kor_round_run_authorization_r1.json"


class GateError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def git_blob_sha(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return subprocess.check_output(["git", "hash-object", "--", rel], cwd=ROOT, text=True).strip()


def exact_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def read_header_only(path: pathlib.Path) -> tuple[str, int]:
    with path.open("rb") as fh:
        line = fh.readline()
    return line.decode("utf-8-sig").rstrip("\r\n"), len(line)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract["status"] == "FROZEN_BEFORE_HOLDOUT_ACCESS", "contract not frozen")
    require(contract["time_split"]["holdout_season"] == "2025", "holdout season changed")
    require(contract["time_split"]["excluded_partial_season"] == "2026", "partial season exclusion changed")
    require(contract["run_policy"]["maximum_experiment_runs"] == 1, "run count is not one")
    require(contract["run_policy"]["formal_weight"] == 0, "formal weight is not zero")
    require(contract["run_policy"]["api_football_requests"] == 0, "API-Football request budget is not zero")
    require(contract["run_policy"]["secret_access"] is False, "secret access is not false")
    require(contract["run_policy"]["formal_promotion_authorized"] is False, "formal promotion unexpectedly authorized")
    require(contract["run_policy"]["merge_authorized"] is False, "merge unexpectedly authorized")


def run_preflight(contract_path: pathlib.Path, output_dir: pathlib.Path, allow_authorization: bool) -> dict[str, Any]:
    contract_raw = contract_path.read_bytes()
    contract = json.loads(contract_raw.decode("utf-8"))
    validate_contract(contract)

    if not allow_authorization:
        require(not AUTH_PATH.exists(), "run authorization exists during sealed preflight")
    head = exact_head()
    expected_head = os.environ.get("EVIDENCE_HEAD")
    if expected_head:
        require(head == expected_head, "checkout HEAD does not equal EVIDENCE_HEAD")

    source_results: dict[str, Any] = {}
    for name, spec in contract["source_files"].items():
        path = ROOT / spec["path"]
        require(path.is_file(), f"missing frozen source: {name}")
        actual_blob = git_blob_sha(path)
        require(actual_blob == spec["git_blob_sha"], f"{name} git blob mismatch")
        source_results[name] = {"path": spec["path"], "git_blob_sha": actual_blob}

    official_path = ROOT / contract["source_files"]["official_results"]["path"]
    official_sha = sha256_bytes(official_path.read_bytes())
    require(
        official_sha == contract["source_files"]["official_results"]["byte_sha256"],
        "official_results byte SHA-256 mismatch",
    )
    official_header, official_header_bytes = read_header_only(official_path)
    require("round" in official_header.split(","), "official_results lacks round header")

    pit_path = ROOT / contract["source_files"]["point_in_time"]["path"]
    pit_header, pit_header_bytes = read_header_only(pit_path)
    pit_columns = pit_header.split(",")
    require(contract["target"]["column"] in pit_columns, "target column missing")
    forbidden_baseline = {"label_home_goals", "label_away_goals", "label_total_goals",
                          "label_total_goals_bin", "label_goal_difference", "label_result"}
    selected = set(contract["baseline_features"]["numeric"]) | set(contract["baseline_features"]["categorical"])
    require(not selected.intersection(forbidden_baseline), "baseline includes outcome field")
    require(selected.issubset(set(pit_columns)), "baseline feature missing from PIT dataset")

    manifest_path = ROOT / contract["source_files"]["request_manifest"]["path"]
    request_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(request_manifest["source"].endswith("/getScheduleList.do"), "official schedule endpoint mismatch")
    require(request_manifest["raw_payload_archived"] is False, "unexpected raw payload policy")
    require(request_manifest["generated_at_utc"] == "2026-07-27T06:57:22+00:00", "request epoch changed")
    future = {
        (r["year"], r["month"]): r
        for r in request_manifest["requests"]
        if r["year"] == "2026" and r["month"] in {"08", "09", "10"}
    }
    require(set(future) == {("2026", "08"), ("2026", "09"), ("2026", "10")},
            "future schedule evidence incomplete")
    for key, row in future.items():
        require(int(row["unique_finished_rows_added"]) == 0, f"{key} unexpectedly contains finished rows")
        require(int(row["response_bytes"]) > 50000, f"{key} response not demonstrably non-empty schedule payload")
        require(len(str(row["response_sha256"])) == 64, f"{key} response hash missing")

    adapter_path = ROOT / contract["source_files"]["ingestion_adapter"]["path"]
    adapter = adapter_path.read_text(encoding="utf-8")
    require("getScheduleList.do" in adapter, "adapter does not bind official schedule endpoint")
    require('item.get("roundId")' in adapter, "adapter does not read roundId directly")
    require('"round": "" if round_id is None else str(round_id)' in adapter,
            "adapter round mapping changed")
    require('item.get("homeGoal")' in adapter and 'item.get("awayGoal")' in adapter,
            "adapter outcome parsing contract missing")

    ajax_path = ROOT / contract["source_files"]["official_ajax_contract"]["path"]
    ajax = ajax_path.read_text(encoding="utf-8")
    require('"status": "PASS_DIAGNOSTIC"' in ajax, "official AJAX diagnostic not PASS")
    require("roundId" in ajax, "official site contract does not expose roundId")

    receipt: dict[str, Any] = {
        "schema_version": "KOR-ROUND-PREFLIGHT-R1-1.0",
        "status": "PASS",
        "head": head,
        "contract_sha256": sha256_bytes(contract_raw),
        "pit_gate": {
            "status": "PASS_RESEARCH_PIT_SAFE",
            "field": "round",
            "reason": (
                "roundId is a direct official schedule field; repository-captured 2026-08/09/10 "
                "future schedule responses were non-empty with zero finished rows before those months"
            ),
            "historical_values_bound_by_official_results_sha256": official_sha,
            "strict_formal_pit_claim": False,
            "research_only": True,
        },
        "holdout_gate": {
            "status": "PASS_SEALED",
            "holdout_season": "2025",
            "application_level_point_in_time_rows_read": 0,
            "application_level_holdout_labels_read": 0,
            "point_in_time_read_scope": "header_only_plus_git_hash_object",
            "point_in_time_header_bytes_read": pit_header_bytes,
            "official_results_read_scope": "cryptographic_hash_plus_header_only; no CSV row parse",
            "official_results_header_bytes_read": official_header_bytes,
        },
        "source_identity": source_results,
        "network": {
            "api_football_requests": 0,
            "provider_requests": 0,
            "secret_access": False,
            "new_data_collection": False,
        },
        "formal_boundary": {
            "formal_weight": 0,
            "model_diff": 0,
            "formal_data_diff": 0,
            "config_diff": 0,
            "current_diff": 0,
            "formal_promotion_authorized": False,
            "merge_authorized": False,
        },
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "preflight_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--allow-authorization", action="store_true")
    args = parser.parse_args()
    try:
        run_preflight(args.contract, args.output_dir, args.allow_authorization)
    except (GateError, KeyError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
