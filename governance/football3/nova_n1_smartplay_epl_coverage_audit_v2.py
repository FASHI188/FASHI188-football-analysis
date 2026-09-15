#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import pathlib
from datetime import datetime, timezone
from typing import Any, Iterable

import nova_n1_smartplay_epl_coverage_audit_v1 as v1

SCHEMA_VERSION = "football3-nova-n1-smartplay-epl-coverage-audit-v2"
IDENTITY_MATCH_CONTRACT = "unique UTC calendar-date + canonical home/away teams; target kickoff retained; source kickoff drift audited fail-closed"
MAX_KICKOFF_DRIFT_SECONDS = 6 * 60 * 60


class IdentityCoverageError(v1.CoverageAuditError):
    pass


def _dt(value: str) -> datetime:
    normalized = v1.parse_kickoff(value)
    return datetime.fromisoformat(normalized)


def _day_team_key(kickoff: str, home: str, away: str) -> tuple[str, str, str]:
    dt = _dt(kickoff)
    return (dt.date().isoformat(), v1.canonical_team(home), v1.canonical_team(away))


def compare_to_locked_identity(
    target: Iterable[dict[str, Any]], source: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Match a historical fixture without trusting exact kickoff equality.

    Historical archives often preserve an announced/scheduled kickoff while the locked
    cohort preserves a revised kickoff. Fixture identity therefore uses the invariant
    combination of UTC calendar date + canonical home/away teams. The contract is
    fail-closed: keys must be unique on both sides and source/target kickoff drift must
    not exceed six hours. The locked target kickoff is retained in the final projection.
    """
    target_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in target:
        key = _day_team_key(str(row["kickoff"]), str(row["home_team"]), str(row["away_team"]))
        if key in target_map:
            raise IdentityCoverageError(f"duplicate locked target date/team key: {key}")
        target_map[key] = row

    source_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in source:
        key = _day_team_key(str(row["kickoff"]), str(row["home_team"]), str(row["away_team"]))
        if key in source_map:
            raise IdentityCoverageError(f"duplicate source date/team key: {key}")
        source_map[key] = row

    shared = sorted(set(target_map) & set(source_map))
    missing_keys = sorted(set(target_map) - set(source_map))
    extra_keys = sorted(set(source_map) - set(target_map))

    projection: list[dict[str, Any]] = []
    kickoff_deltas: list[dict[str, Any]] = []
    drift_exceeded: list[dict[str, Any]] = []
    for key in shared:
        target_row = target_map[key]
        source_row = source_map[key]
        target_kickoff = v1.parse_kickoff(str(target_row["kickoff"]))
        source_kickoff = v1.parse_kickoff(str(source_row["kickoff"]))
        delta_seconds = int((_dt(source_kickoff) - _dt(target_kickoff)).total_seconds())
        abs_delta = abs(delta_seconds)
        audit_row = {
            "date": key[0],
            "home_team": key[1],
            "away_team": key[2],
            "target_kickoff": target_kickoff,
            "source_kickoff": source_kickoff,
            "delta_seconds": delta_seconds,
        }
        if abs_delta:
            kickoff_deltas.append(audit_row)
        if abs_delta > MAX_KICKOFF_DRIFT_SECONDS:
            drift_exceeded.append(audit_row)
            continue
        projection.append({
            "fixture_id": str(target_row["fixture_id"]),
            "kickoff": target_kickoff,
            "league": v1.TARGET_LEAGUE,
            "season": v1.TARGET_SEASON,
            "home_team_id": str(target_row["home_team_id"]),
            "away_team_id": str(target_row["away_team_id"]),
            "home_team": key[1],
            "away_team": key[2],
            "h_deep": source_row["h_deep"],
            "a_deep": source_row["a_deep"],
            "h_ppda": source_row["h_ppda"],
            "a_ppda": source_row["a_ppda"],
        })

    qualified = (
        len(target_map) == v1.TARGET_N
        and len(source_map) == v1.TARGET_N
        and len(projection) == v1.TARGET_N
        and not missing_keys
        and not extra_keys
        and not drift_exceeded
    )
    max_abs_delta = max((abs(int(x["delta_seconds"])) for x in kickoff_deltas), default=0)
    coverage = {
        "status": "IDENTITY_COVERAGE_QUALIFIED" if qualified else "STOP_DATA_COVERAGE",
        "identity_match_contract": IDENTITY_MATCH_CONTRACT,
        "max_kickoff_drift_seconds_allowed": MAX_KICKOFF_DRIFT_SECONDS,
        "target_n": len(target_map),
        "source_n": len(source_map),
        "matched_date_team_n": len(shared),
        "matched_n": len(projection),
        "missing_n": len(missing_keys),
        "extra_n": len(extra_keys),
        "feature_complete_n": len(projection),
        "coverage_fraction": len(projection) / len(target_map) if target_map else 0.0,
        "kickoff_mismatch_n": len(kickoff_deltas),
        "max_abs_kickoff_delta_seconds": max_abs_delta,
        "kickoff_drift_exceeded_n": len(drift_exceeded),
        "kickoff_delta_examples": kickoff_deltas[:20],
        "kickoff_drift_exceeded_examples": drift_exceeded[:20],
        "missing_examples": [list(x) for x in missing_keys[:20]],
        "extra_examples": [list(x) for x in extra_keys[:20]],
    }
    return coverage, projection


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    target, identity_audit = v1.load_locked_identity(args.identity)
    source_rows, source_audit = v1.fetch_and_project()
    coverage, projection = compare_to_locked_identity(target, source_rows)
    projection_sha = v1.sha256_bytes(v1.canon(sorted(projection, key=lambda x: x["fixture_id"])))
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": coverage["status"],
        "identity_match_contract": IDENTITY_MATCH_CONTRACT,
        "provider": v1.PROVIDER,
        "dataset_repo": v1.DATASET_REPO,
        "dataset_file": v1.DATASET_FILE,
        "download_url": v1.DOWNLOAD_URL,
        "declared_license": v1.DECLARED_LICENSE,
        "source_project_commit": v1.SOURCE_PROJECT_COMMIT,
        "source_license_blob_sha": v1.SOURCE_LICENSE_BLOB_SHA,
        "source_data_readme_blob_sha": v1.SOURCE_DATA_README_BLOB_SHA,
        "source_download_helper_blob_sha": v1.SOURCE_DOWNLOAD_HELPER_BLOB_SHA,
        "license_scope_evidence": "Pinned SmartPlayFPL LICENSE is CC BY-NC 4.0; pinned data documentation names this training/evaluation CSV and documents the safe Understat team-match feature columns; pinned helper maps the CSV to the Hugging Face dataset repository.",
        "source_provenance_note": "This audit uses the redistributed SmartPlay dataset under its declared CC BY-NC 4.0 terms. It does not rely on or assert a direct Understat license.",
        "locked_test_identity": identity_audit,
        "source_audit": source_audit,
        "coverage": coverage,
        "safe_feature_projection_sha256": projection_sha,
        "safe_feature_projection_n": len(projection),
        "test_result_vault_opened": False,
        "test_raw_pages_opened": False,
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "promotion_authorized": False,
        "audited_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    receipt_path = args.out / "smartplay_epl_coverage_audit.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if coverage["status"] == "IDENTITY_COVERAGE_QUALIFIED":
        feature_path = args.out / "smartplay_epl_feature_projection.jsonl"
        with feature_path.open("w", encoding="utf-8") as f:
            for row in sorted(projection, key=lambda x: x["fixture_id"]):
                f.write(v1.canon(row).decode("utf-8") + "\n")
    print(json.dumps({
        "status": coverage["status"],
        "source_n": coverage["source_n"],
        "target_n": coverage["target_n"],
        "matched_n": coverage["matched_n"],
        "missing_n": coverage["missing_n"],
        "extra_n": coverage["extra_n"],
        "feature_complete_n": coverage["feature_complete_n"],
        "kickoff_mismatch_n": coverage["kickoff_mismatch_n"],
        "max_abs_kickoff_delta_seconds": coverage["max_abs_kickoff_delta_seconds"],
        "kickoff_drift_exceeded_n": coverage["kickoff_drift_exceeded_n"],
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "projection_sha256": projection_sha,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
