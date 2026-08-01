#!/usr/bin/env python3
"""Fail-closed integrity validator for the frozen draw-composite preregistration.

This validator checks only governance, document identity, frozen source identities,
PIT boundaries and execution-contract completeness. It does not train, score,
read result labels, access Provider/API/Secret, or modify formal assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "football-data" / "research"
FILES = {
    "route": RESEARCH / "draw_composite_route_inventory_r1.json",
    "field": RESEARCH / "draw_composite_raw_field_pit_ledger_r1.json",
    "prereg": RESEARCH / "draw_composite_preregistration_r1.json",
    "contract": RESEARCH / "draw_composite_execution_contract_r1.json",
}
AUTH = RESEARCH / "draw_composite_run_authorization_r1.json"


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json_sha256(path: pathlib.Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load(path: pathlib.Path) -> dict[str, Any]:
    require(path.is_file(), f"missing file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"top-level JSON must be object: {path}")
    return value


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validate_documents() -> dict[str, Any]:
    route = load(FILES["route"])
    field = load(FILES["field"])
    prereg = load(FILES["prereg"])
    contract = load(FILES["contract"])

    rows = route.get("rows") or []
    require(len(rows) == 42, f"route count mismatch: {len(rows)}")
    route_ids = [str(row.get("id")) for row in rows]
    require(len(route_ids) == len(set(route_ids)), "duplicate canonical route IDs")
    require(route["counts"]["canonical_routes"] == 42, "canonical route count not 42")
    require(route["counts"]["UNRESOLVED"] == 14, "UNRESOLVED count not 14")
    require(route["counts"]["missing_result_evidence"] == 4, "missing result evidence count not 4")
    require(route["counts"]["candidate_improvement"] == 6, "candidate-improvement count not 6")
    require(len(route.get("unresolved") or []) == 14, "unresolved list length mismatch")
    require(len(route.get("missing_result_evidence") or []) == 4, "missing-result list length mismatch")
    require(len(route.get("candidate_improvements") or []) == 6, "candidate list length mismatch")
    require(route.get("exhaustion_claim_allowed") is False, "exhaustion claim must remain prohibited")

    groups = field.get("groups") or []
    require(sum(int(group["count"]) for group in groups) == 176, "raw-field count not 176")
    all_fields: list[str] = []
    by_class: dict[str, dict[str, Any]] = {}
    for group in groups:
        classification = str(group["classification"])
        require(classification not in by_class, f"duplicate field classification: {classification}")
        by_class[classification] = group
        fields = [str(item) for item in group.get("fields") or []]
        require(len(fields) == int(group["count"]), f"field count mismatch: {classification}")
        all_fields.extend(fields)
    require(len(all_fields) == len(set(all_fields)), "raw field appears in multiple groups")
    require(by_class["RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN"]["count"] == 139,
            "market exclusion count not 139")
    require(by_class["POSTMATCH_FORBIDDEN"]["count"] == 18, "postmatch count not 18")
    require(by_class["PIT_SAFE_STRUCTURAL"]["count"] == 17, "structural count not 17")
    require(by_class["PIT_UNPROVEN_CONTEXT"]["fields"] == ["Referee"], "Referee classification changed")
    require(by_class["PIT_RECONSTRUCTED_SCHEDULE_FIELD_RESEARCH_ONLY"]["fields"] == ["round"],
            "round classification changed")
    require(field.get("unknown_pit_policy") == "FAIL_CLOSED_EXCLUDE", "unknown PIT policy weakened")
    require(field.get("formal_promotion_authorized") is False, "formal promotion must be false")

    require(prereg.get("recommended_challenger") == "C5_DRAW_COMPOSITE_PIT_R1_CORE",
            "recommended challenger changed")
    models = prereg.get("frozen_candidate_models") or []
    recommended = [row for row in models if row.get("recommended") is True]
    require(len(recommended) == 1 and recommended[0]["id"] == "C5_DRAW_COMPOSITE_PIT_R1_CORE",
            "must have exactly one recommended challenger")
    primary = recommended[0]
    require(primary["families"] == [
        "S1_STRENGTH_CLOSENESS",
        "S2_RECENT_FORM_ATTACK_DEFENCE",
        "S3_HISTORICAL_DRAW_PROPENSITY",
        "S4_LOW_GOAL_ENVIRONMENT",
        "S5_BASELINE_PROBABILITY_UNCERTAINTY",
        "S6_LEAGUE_STAGE_INTERACTION",
    ], "C5 family set changed")
    require(prereg["time_validation"]["random_split"] is False, "random split must be false")
    require(prereg["comparison_policy"]["report_all_candidates"] is True, "all candidates must be reported")
    require(prereg["comparison_policy"]["no_cherry_picking"] is True, "cherry-picking gate missing")
    require(prereg["holdout_status"]["2025"] == "VIEWED_NOT_BLIND", "2025 must remain viewed")
    require(prereg["holdout_status"]["existing_completed_data"] ==
            "NO_PROVABLY_UNTOUCHED_EXISTING_RESULT_SET", "holdout claim changed")
    require(prereg["holdout_status"]["formal_promotion_possible_from_next_run"] is False,
            "formal promotion cannot be possible")

    gap = prereg.get("gap_closure") or {}
    require(gap.get("execution_contract_path") ==
            "football-data/research/draw_composite_execution_contract_r1.json",
            "execution contract path mismatch")
    require(gap.get("execution_contract_sha256") == sha256(FILES["contract"]),
            "execution contract SHA mismatch")
    require(contract["status"] == "FROZEN_EXECUTION_SPEC_NOT_AUTHORIZED_NOT_RUN",
            "contract status changed")
    require(contract["base_main_sha"] == "605abf2d9f98c46f063106c7bd47193b96e588e4",
            "base main SHA changed")

    universe = contract["dataset_universe"]
    require(universe["manifest_total_rows"] == 27616, "manifest row count changed")
    require(len(universe["competitions"]) == 17, "competition count not 17")
    require(universe["expected_total_outer_folds"] == 51, "expected fold count not 51")
    for cid, item in universe["competitions"].items():
        require(len(item["complete_seasons"]) == 5, f"{cid}: complete-season count not 5")
        require(all(str(season) != "2026" for season in item["complete_seasons"]),
                f"{cid}: partial 2026 entered complete seasons")
        require(len(str(item["sha256"])) == 64, f"{cid}: invalid dataset SHA")
    assets = contract["frozen_source_assets"]
    for name, item in assets.items():
        require(str(item["path"]).startswith("football-data/"), f"{name}: invalid path")
        require(len(str(item["git_blob_sha"])) == 40, f"{name}: invalid blob SHA")

    order = contract["row_identity_and_order"]
    require(order["same_day_policy"].startswith("predict every match"), "same-day gate weakened")
    require(order["date_semantics"].startswith("date-only sources are gated conservatively"),
            "date-only PIT gate weakened")
    rolling = contract["rolling_origin"]
    require(rolling["random_split"] is False, "contract random split must be false")
    require(rolling["within_target_season_candidate_refit"] is False,
            "within-target-season refit must be false")
    common = contract["common_cohort"]
    require(common["minimum_rows_per_outer_fold"] == 100, "minimum fold rows changed")

    feature_text = json.dumps(contract["feature_generation"], ensure_ascii=False)
    forbidden_tokens = [
        '"Referee"', "lineup", "player_value", "market_home", "market_draw",
        "market_away", "bookmaker", "closing_odds"
    ]
    for token in forbidden_tokens:
        require(token not in feature_text, f"forbidden core input token present: {token}")
    require(contract["feature_generation"]["global_rules"]["current_target_row_in_history"] is False,
            "target row may not enter history")
    require(contract["feature_generation"]["global_rules"]["same_day_result_in_history"] is False,
            "same-day result may not enter history")
    require(contract["candidate_model"]["randomness"] == "none", "model randomness changed")
    require(contract["candidate_model"]["hyperparameter_search"] == "prohibited",
            "hyperparameter search must remain prohibited")
    require(contract["candidate_model"]["nonconvergence"].startswith("FAIL_CLOSED"),
            "non-convergence must fail closed")
    require(contract["support_gate"]["formal_promotion"] ==
            "PROHIBITED_NO_GENUINELY_UNTOUCHED_HOLDOUT",
            "formal-promotion gate weakened")

    required_metrics = {
        "Accuracy", "Macro-F1", "Draw Precision", "Draw Recall", "Draw F1",
        "Log Loss", "Brier", "RPS"
    }
    require(required_metrics.issubset(set(contract["metrics_and_calibration"]["metrics"])),
            "required metric missing")
    require(contract["report_contract"]["report_all_candidates"] is True,
            "report-all contract missing")
    require(len(contract["report_contract"]["required_candidates"]) == 20,
            "required candidate/ablation count changed")

    auth = contract["authorization_and_run"]
    require(auth["separate_user_authorization_required"] is True,
            "separate user authorization must be required")
    require(auth["maximum_comprehensive_runs"] == 1, "run maximum must be one")
    require(auth["provider_requests"] == 0 and auth["secret_access"] is False,
            "Provider/Secret boundary changed")
    require(auth["new_data_collection"] is False, "new-data collection must be false")
    require(auth["formal_model_diff"] == auth["formal_data_diff"] ==
            auth["config_diff"] == auth["current_diff"] == 0,
            "formal asset diff must remain zero")
    require(auth["formal_weight"] == 0, "formal weight must remain zero")
    require(auth["ready_authorized"] is False and auth["merge_authorized"] is False,
            "Ready/merge must remain unauthorized")

    return {
        "route_count": 42,
        "unresolved_count": 14,
        "missing_result_evidence_count": 4,
        "candidate_improvement_count": 6,
        "raw_field_count": 176,
        "competition_count": 17,
        "dataset_manifest_rows": 27616,
        "expected_outer_folds": 51,
        "recommended_challenger": "C5_DRAW_COMPOSITE_PIT_R1_CORE",
        "holdout_status": "NO_PROVABLY_UNTOUCHED_EXISTING_RESULT_SET",
    }


def validate_repository(contract: dict[str, Any]) -> dict[str, Any]:
    require((ROOT / ".git").exists(), "full validation requires a Git checkout")
    current_head = git("rev-parse", "HEAD")
    base = contract["base_main_sha"]
    merge_base = git("merge-base", base, current_head)
    require(merge_base == base, f"HEAD is not descended from frozen base: {base}")
    changed = [line for line in git("diff", "--name-only", f"{base}..{current_head}").splitlines() if line]
    allowed = set(contract["source_tree_policy"]["allowed_post_base_paths"])
    unexpected = sorted(set(changed) - allowed)
    require(not unexpected, f"unexpected post-base paths: {unexpected}")
    for name, item in contract["frozen_source_assets"].items():
        path = ROOT / item["path"]
        require(path.is_file(), f"{name}: source asset missing: {path}")
        blob = git("hash-object", str(path.relative_to(ROOT)))
        require(blob == item["git_blob_sha"],
                f"{name}: blob mismatch expected {item['git_blob_sha']} got {blob}")
    require(not AUTH.exists(), "run authorization file exists before separate authorization")
    return {
        "exact_head": current_head,
        "frozen_base": base,
        "changed_paths": changed,
        "unexpected_paths": unexpected,
        "authorization_file_present": AUTH.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-only", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    try:
        document = validate_documents()
        contract = load(FILES["contract"])
        repository = None if args.document_only else validate_repository(contract)
        result = {
            "schema_version": "DRAW-COMPOSITE-PREREG-INTEGRITY-R1.1",
            "status": "PASS_DOCUMENT_CONTRACT" if args.document_only else "PASS_FULL_PREFLIGHT",
            "document_only": bool(args.document_only),
            "experiment_executed": False,
            "labels_read": 0,
            "provider_requests": 0,
            "secret_access": False,
            "formal_asset_changes": 0,
            "documents": document,
            "repository": repository,
            "canonical_json_sha256": {key: canonical_json_sha256(path) for key, path in FILES.items()},
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, KeyError, TypeError, ValueError,
            subprocess.CalledProcessError) as exc:
        print(json.dumps({
            "schema_version": "DRAW-COMPOSITE-PREREG-INTEGRITY-R1.1",
            "status": "FAIL_CLOSED",
            "error": str(exc),
            "experiment_executed": False,
            "labels_read": 0,
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
