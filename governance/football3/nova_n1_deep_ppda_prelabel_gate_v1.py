#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PrelabelGateError(RuntimeError):
    pass


def canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PrelabelGateError(message)


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(obj, dict), f"NOT_JSON_OBJECT:{path}")
    return obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            require(isinstance(row, dict), f"JSONL_ROW_NOT_OBJECT:{path}:{line_number}")
            rows.append(row)
    return rows


def normalize_league(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        "La liga": "La_liga",
        "La_Liga": "La_liga",
        "La_liga": "La_liga",
        "Ligue 1": "Ligue_1",
        "Ligue_1": "Ligue_1",
        "Serie A": "Serie_A",
        "Serie_A": "Serie_A",
        "EPL": "EPL",
        "Bundesliga": "Bundesliga",
    }
    require(text in aliases, f"UNKNOWN_LEAGUE:{text!r}")
    return aliases[text]


def normalize_dt(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PrelabelGateError(f"BAD_DATETIME:{value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def prob_vector(value: Any, label: str) -> list[float]:
    require(isinstance(value, list) and len(value) == 3, f"{label}:BAD_1X2")
    out = []
    for item in value:
        require(not isinstance(item, bool), f"{label}:BOOLEAN_PROB")
        try:
            p = float(item)
        except (TypeError, ValueError) as exc:
            raise PrelabelGateError(f"{label}:NONNUMERIC_PROB") from exc
        require(math.isfinite(p) and p >= 0.0, f"{label}:INVALID_PROB")
        out.append(p)
    require(abs(sum(out) - 1.0) <= 1e-9, f"{label}:PROB_SUM")
    return out


def matrix_1x2(value: Any, label: str) -> tuple[list[list[float]], list[float]]:
    require(isinstance(value, list) and len(value) == 15, f"{label}:BAD_MATRIX_ROWS")
    matrix: list[list[float]] = []
    h = d = a = 0.0
    total = 0.0
    for i, raw_row in enumerate(value):
        require(isinstance(raw_row, list) and len(raw_row) == 15, f"{label}:BAD_MATRIX_COLS:{i}")
        row: list[float] = []
        for j, item in enumerate(raw_row):
            require(not isinstance(item, bool), f"{label}:BOOLEAN_MATRIX_PROB")
            try:
                p = float(item)
            except (TypeError, ValueError) as exc:
                raise PrelabelGateError(f"{label}:NONNUMERIC_MATRIX_PROB") from exc
            require(math.isfinite(p) and p >= 0.0, f"{label}:INVALID_MATRIX_PROB")
            row.append(p)
            total += p
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
        matrix.append(row)
    require(abs(total - 1.0) <= 1e-9, f"{label}:MATRIX_SUM")
    return matrix, [h, d, a]


def verify_manifest_file(root: Path, manifest: dict[str, Any], rel: str, expected_sha: str) -> None:
    entry = manifest.get("files", {}).get(rel)
    require(isinstance(entry, dict), f"MANIFEST_FILE_MISSING:{rel}")
    require(entry.get("sha256") == expected_sha, f"MANIFEST_SHA_DECLARATION_MISMATCH:{rel}")
    actual = sha256_file(root / rel)
    require(actual == expected_sha, f"FILE_SHA_MISMATCH:{rel}:{actual}")


def expected_data_payloads(data_manifest: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data_manifest["per_league_season"].items():
        league, season = key.split("|", 1)
        out[f"{normalize_league(league)}|{season}"] = str(value["league_payload_sha256"])
    return out


def run(prereg_path: Path, source_dir: Path, data_dir: Path, stress_dir: Path, formal_out: Path, receipt_out: Path) -> dict[str, Any]:
    prereg = read_json(prereg_path)
    require(prereg.get("status") == "DESIGN_LOCKED_PRELABEL", "PREREG_STATUS")
    require(prereg["legacy_v3"]["candidate_code_parameters_weights_inherited"] is False, "LEGACY_INHERITANCE_FORBIDDEN")
    require(prereg["legacy_v3"]["candidate_predictions_consumption_allowed"] is False, "LEGACY_PREDICTIONS_FORBIDDEN")

    source_receipt_path = source_dir / "receipt.json"
    source_projection_path = source_dir / "state_projection.jsonl"
    source_receipt = read_json(source_receipt_path)
    source_spec = prereg["source_features"]
    require(source_receipt.get("status") == source_spec["receipt_status"], "SOURCE_RECEIPT_STATUS")
    require(source_receipt.get("match_count") == prereg["cohort"]["fixture_n"], "SOURCE_COUNT")
    require(source_receipt.get("fixture_id_unique_count") == prereg["cohort"]["fixture_n"], "SOURCE_UNIQUE_COUNT")
    require(source_receipt.get("fixture_identity_sha256") == prereg["cohort"]["fixture_identity_sha256"], "SOURCE_IDENTITY")
    require(source_receipt.get("state_projection_sha256") == source_spec["state_projection_sha256"], "SOURCE_PROJECTION_DECLARED_SHA")
    require(sha256_file(source_projection_path) == source_spec["state_projection_sha256"], "SOURCE_PROJECTION_FILE_SHA")
    for key in ("label_values_read", "result_values_used", "score_values_used", "xg_values_used"):
        require(source_receipt.get(key) == 0, f"SOURCE_GOVERNANCE:{key}")
    require(source_receipt.get("training_performed") is False, "SOURCE_TRAINING_OCCURRED")
    require(source_receipt.get("candidate_probabilities_generated") is False, "SOURCE_CANDIDATE_PROBS_OCCURRED")
    require(source_receipt.get("candidate_weight") == 0 and source_receipt.get("matrix_delta") == 0, "SOURCE_CANDIDATE_ACTIVE")
    require(source_receipt.get("formal_v2_changed") is False, "SOURCE_FORMAL_CHANGED")
    require(source_receipt.get("current_changed") is False, "SOURCE_CURRENT_CHANGED")
    require(source_receipt.get("production_changed") is False, "SOURCE_PRODUCTION_CHANGED")

    source_rows = read_jsonl(source_projection_path)
    source_ids = [str(row.get("fixture_id")) for row in source_rows]
    require(len(source_rows) == prereg["cohort"]["fixture_n"], "SOURCE_ROW_COUNT")
    require(sha256_bytes(canon(source_ids)) == prereg["cohort"]["fixture_identity_sha256"], "SOURCE_ID_SEQUENCE")
    season_counts: dict[int, int] = {}
    for row in source_rows:
        season = int(row["season_start"])
        season_counts[season] = season_counts.get(season, 0) + 1
        require(normalize_league(row["league"]) in prereg["cohort"]["competitions"], "SOURCE_COMPETITION")
    require(season_counts == {2024: 1752, 2025: 1752}, f"SOURCE_SEASON_COUNTS:{season_counts}")

    data_manifest_art = read_json(data_dir / "artifact_manifest.json")
    hist = prereg["historical_fixture_evidence"]
    require(data_manifest_art.get("head") == hist["head"], "DATA_HEAD")
    require(data_manifest_art.get("fixture_n") == hist["fixture_n"], "DATA_ARTIFACT_COUNT")
    require(data_manifest_art.get("fixture_identity_sha256") == hist["fixture_identity_sha256"], "DATA_ARTIFACT_IDENTITY")
    require(data_manifest_art.get("formal_v2_head") == prereg["formal_v2"]["head"], "DATA_FORMAL_HEAD")
    require(data_manifest_art.get("fresh_confirmation") is False, "DATA_FRESH_CONFIRMATION_MUST_BE_FALSE")
    require(data_manifest_art.get("post_view_historical_stress_test") is True, "DATA_HISTORICAL_CLASSIFICATION")
    verify_manifest_file(data_dir, data_manifest_art, "data/data_manifest.json", hist["data_manifest_sha256"])
    verify_manifest_file(data_dir, data_manifest_art, "data/fixtures.jsonl", hist["fixture_store_sha256"])
    verify_manifest_file(data_dir, data_manifest_art, "contracts/HISTORICAL_REPLAY_CONTRACT.json", hist["contract_sha256"])
    label_entry = data_manifest_art.get("files", {}).get(hist["label_vault_path"])
    require(isinstance(label_entry, dict), "LABEL_VAULT_MANIFEST_ENTRY_MISSING")
    require(label_entry.get("sha256") == hist["label_vault_declared_sha256"], "LABEL_VAULT_DECLARED_SHA_MISMATCH")

    data_manifest = read_json(data_dir / "data/data_manifest.json")
    require(data_manifest.get("fixture_n") == hist["fixture_n"], "DATA_MANIFEST_COUNT")
    require(data_manifest.get("fixture_identity_sha256") == hist["fixture_identity_sha256"], "DATA_MANIFEST_IDENTITY")
    require(data_manifest.get("label_vault_sha256") == hist["label_vault_declared_sha256"], "DATA_MANIFEST_LABEL_DECLARATION")
    source_payloads = {str(k): str(v["payload_sha256"]) for k, v in source_receipt["source_payloads"].items()}
    require(source_payloads == expected_data_payloads(data_manifest), "SOURCE_PAYLOAD_SHA_SET_MISMATCH")

    fixtures = read_jsonl(data_dir / "data/fixtures.jsonl")
    fixture_ids = [str(row.get("fixture_id")) for row in fixtures]
    require(fixture_ids == source_ids, "SOURCE_DATA_FIXTURE_ORDER_MISMATCH")
    require(sha256_bytes(canon(fixture_ids)) == hist["fixture_identity_sha256"], "DATA_FIXTURE_ID_SEQUENCE")

    stress_manifest = read_json(stress_dir / "artifact_manifest.json")
    formal = prereg["formal_v2"]
    baseline_spec = formal["baseline_projection_source"]
    require(stress_manifest.get("formal_v2_head") == formal["head"], "STRESS_FORMAL_HEAD")
    require(stress_manifest.get("formal_v2_unchanged") is True, "STRESS_FORMAL_CHANGED")
    require(stress_manifest.get("formal_weights_changed") is False, "STRESS_FORMAL_WEIGHTS_CHANGED")
    require(stress_manifest.get("formal_enablement_changed") is False, "STRESS_FORMAL_ENABLEMENT_CHANGED")
    require(stress_manifest.get("CURRENT_changed") is False, "STRESS_CURRENT_CHANGED")
    require(stress_manifest.get("production_pointer_changed") is False, "STRESS_PRODUCTION_CHANGED")
    require(stress_manifest.get("fixture_n") == hist["fixture_n"], "STRESS_COUNT")
    require(stress_manifest.get("fixture_identity_sha256") == hist["fixture_identity_sha256"], "STRESS_IDENTITY")
    require(stress_manifest.get("data_artifact_id") == hist["artifact_id"], "STRESS_DATA_ARTIFACT_ID")
    require(stress_manifest.get("data_head") == hist["head"], "STRESS_DATA_HEAD")
    verify_manifest_file(stress_dir, stress_manifest, baseline_spec["contract_path"], baseline_spec["contract_sha256"])
    verify_manifest_file(stress_dir, stress_manifest, baseline_spec["predictions_path"], baseline_spec["predictions_sha256"])
    contract = read_json(stress_dir / baseline_spec["contract_path"])
    formal_contract = contract.get("formal_v2", {})
    require(formal_contract.get("head") == formal["head"], "CONTRACT_FORMAL_HEAD")
    require(float(formal_contract.get("xg_weight")) == float(formal["xg_weight"]), "CONTRACT_XG_WEIGHT")
    require(float(formal_contract.get("v1_weight")) == float(formal["frozen_v1_weight"]), "CONTRACT_V1_WEIGHT")
    require(formal_contract.get("must_remain_unchanged") is True, "CONTRACT_FORMAL_UNCHANGED")

    pred_rows = read_jsonl(stress_dir / baseline_spec["predictions_path"])
    require(len(pred_rows) == hist["fixture_n"], "FORMAL_PRED_COUNT")
    allowed = set(baseline_spec["allowed_fields"])
    forbidden_prefixes = tuple(baseline_spec["forbidden_prefixes"])
    stripped: list[dict[str, Any]] = []
    candidate_fields_detected = 0
    for index, (pred, source, fixture) in enumerate(zip(pred_rows, source_rows, fixtures)):
        fid = str(pred.get("fixture_id"))
        require(fid == source["fixture_id"] == fixture["fixture_id"], f"ROW_IDENTITY:{index}")
        require(normalize_dt(pred.get("kickoff")) == normalize_dt(source["kickoff"]) == normalize_dt(fixture["kickoff"]), f"ROW_KICKOFF:{fid}")
        require(normalize_league(pred.get("league")) == normalize_league(source["league"]) == normalize_league(fixture["league"]), f"ROW_LEAGUE:{fid}")
        require(int(pred.get("season")) == int(source["season_start"]) == int(fixture["season"]), f"ROW_SEASON:{fid}")
        require(pred.get("label_read_after_prediction_freeze") is True, f"BASELINE_FREEZE_ORDER:{fid}")
        for key in pred:
            if key.startswith(forbidden_prefixes):
                candidate_fields_detected += 1
        p_1x2 = prob_vector(pred.get("formal_v2_1x2"), f"{fid}:formal_v2_1x2")
        matrix, derived = matrix_1x2(pred.get("formal_matrix"), f"{fid}:formal_matrix")
        require(all(abs(a - b) <= 1e-10 for a, b in zip(p_1x2, derived)), f"{fid}:MATRIX_1X2_MISMATCH")
        matrix_sha = sha256_bytes(canon(matrix))
        require(matrix_sha == pred.get("formal_matrix_sha256"), f"{fid}:MATRIX_SHA")
        stripped_row = {
            "fixture_id": fid,
            "kickoff": normalize_dt(pred["kickoff"]),
            "league": normalize_league(pred["league"]),
            "season_start": int(pred["season"]),
            "formal_v2_1x2": p_1x2,
            "formal_matrix": matrix,
            "formal_matrix_sha256": matrix_sha,
            "label_read_after_prediction_freeze": True,
        }
        require(set(stripped_row).issubset(allowed | {"season_start"}), "STRIPPED_ALLOWLIST_INTERNAL")
        stripped.append(stripped_row)

    formal_ids = [row["fixture_id"] for row in stripped]
    require(formal_ids == source_ids == fixture_ids, "FORMAL_FIXTURE_ORDER_MISMATCH")
    require(sha256_bytes(canon(formal_ids)) == hist["fixture_identity_sha256"], "FORMAL_ID_SEQUENCE")
    formal_out.parent.mkdir(parents=True, exist_ok=True)
    with formal_out.open("w", encoding="utf-8") as handle:
        for row in stripped:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")

    receipt = {
        "schema_version": "football3-nova-n1-deep-ppda-prelabel-gate-receipt-v1",
        "status": "N1_DEEP_PPDA_PRELABEL_GATE_PASS",
        "design_lock_sha256": sha256_file(prereg_path),
        "fixture_n": len(stripped),
        "fixture_identity_sha256": hist["fixture_identity_sha256"],
        "development_n": season_counts[2024],
        "isolated_test_n": season_counts[2025],
        "source_state_projection_sha256": source_spec["state_projection_sha256"],
        "formal_v2_head": formal["head"],
        "formal_v2_baseline_projection_sha256": sha256_file(formal_out),
        "formal_matrix_policy": prereg["model"]["score_matrix_policy"],
        "source_payload_sha_match_complete": True,
        "baseline_matrix_integrity_complete": True,
        "label_vault_opened": False,
        "label_values_read": 0,
        "result_values_used": 0,
        "score_values_used": 0,
        "xg_values_used": 0,
        "candidate_fit_performed": False,
        "candidate_probabilities_generated": False,
        "old_v3_candidate_fields_detected": candidate_fields_detected,
        "old_v3_candidate_fields_used": 0,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "isolated_test_opened": False,
        "allowed_next_phase": "IMPLEMENTING",
    }
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stress-dir", type=Path, required=True)
    parser.add_argument("--formal-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.prereg, args.source_dir, args.data_dir, args.stress_dir, args.formal_out, args.receipt_out), sort_keys=True))


if __name__ == "__main__":
    main()
