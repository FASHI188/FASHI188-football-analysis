#!/usr/bin/env python3
"""R42D replication: exact-method disjoint fixed200.

No scientific degree of freedom changes from the parent R42D method. The first R42D
fixed200 is reproduced by identity and added to the exclusion set. The replication uses
the exact same 22 counterfactual standings features, baseline/challenger model form,
policy-only C grid and PASS gate on a fresh identity-selected 200 rows.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import load_json
from evaluate_r41_priority_fixed200_battery import HDA_CLASSES, compare, fit_model, materialize_market, prepare_features, select_method_sample
from evaluate_r42a_dynamic_diagonal_fixed200 import reproduce_all_prior_fixed200
from evaluate_r42c_favourite_nonwin_fixed200 import reproduce_R42A_sample
from evaluate_r42d_mutual_draw_utility_fixed200 import build_counterfactual_features, reproduce_r42c_sample
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, complete_seasons

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42d_mutual_draw_utility_replication_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42d_mutual_draw_utility_replication_fixed200_status.json"


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    parent_cfg = load_json(ROOT / str(cfg["parent_config"]))
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    r42a_cfg = load_json(ROOT / "config" / "r42a_dynamic_diagonal_fixed200.json")
    prior1400, prior_hashes = reproduce_all_prior_fixed200(raw, seasons, r42a_cfg)
    r42a_ids, r42a_sha = reproduce_R42A_sample(raw, seasons, prior1400, parent_cfg)
    excluded1600 = prior1400 | r42a_ids
    r42c_ids, r42c_sha = reproduce_r42c_sample(raw, seasons, excluded1600, parent_cfg)
    excluded1800 = excluded1600 | r42c_ids
    if len(excluded1800) != 1800:
        raise ResearchError(f"expected 1800 identities before parent R42D, got {len(excluded1800)}")

    utility_names = [str(x) for x in parent_cfg["method_contract"]["counterfactual_features"]]
    utility, utility_audit = build_counterfactual_features(raw, utility_names)
    market = materialize_market(raw, parent_cfg["market_contract"])
    r41d_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    frame = prepare_features(raw, market, seasons, r41d_cfg)
    frame = frame.merge(utility, on="identity_key", how="left", validate="one_to_one")
    eligible = (frame.book_count.fillna(0).astype(int) >= 1) & frame[utility_names].notna().all(axis=1)

    parent_sample, parent_sha = select_method_sample(
        frame,
        eligible,
        excluded1800,
        int(parent_cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["exclude_parent_seed"]),
    )
    expected_parent = str(cfg["sample_contract"]["exclude_parent_identity_sha256"])
    if parent_sha != expected_parent:
        raise ResearchError(f"parent R42D identity mismatch: {parent_sha} != {expected_parent}")
    parent_ids = set(parent_sample.identity_key.astype(str))
    if parent_ids & excluded1800:
        raise ResearchError("parent R42D reproduction overlaps prior1800")

    excluded_ids = excluded1800 | parent_ids
    if len(excluded_ids) != int(cfg["sample_contract"]["prior_consumed_rows_before_replication"]):
        raise ResearchError(f"expected 2000 prior consumed rows, got {len(excluded_ids)}")

    sample, sample_sha = select_method_sample(
        frame,
        eligible,
        excluded_ids,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    overlap = int(len(set(sample.identity_key.astype(str)) & excluded_ids))
    if overlap:
        raise ResearchError(f"R42D replication overlaps prior2000: {overlap}")

    eligible_frame = frame[eligible].copy()
    train = eligible_frame[eligible_frame.split == "train"].copy()
    policy = eligible_frame[eligible_frame.split == "policy"].copy()
    fit = eligible_frame[eligible_frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit)) == 0:
        raise ResearchError("empty R42D replication fit split")

    baseline_features = [str(x) for x in parent_cfg["method_contract"]["baseline_features"]]
    challenger_features = baseline_features + utility_names
    # Exact parent fit contract and market contract; only sample identity changes.
    fit_cfg = dict(parent_cfg)
    fit_cfg["decision_contract"] = cfg["decision_contract"]
    fit_cfg["governance"] = cfg["governance"]

    p_base, base_receipt = fit_model(train, policy, fit, sample, baseline_features, "outcome", HDA_CLASSES, fit_cfg, base_cfg)
    p_challenger, challenger_receipt = fit_model(train, policy, fit, sample, challenger_features, "outcome", HDA_CLASSES, fit_cfg, base_cfg)
    comparison = compare(sample.outcome.to_numpy(int), p_base, p_challenger, fit_cfg, int(cfg["sample_contract"]["seed"]) + 1)
    passed = bool(comparison["gate"]["all_required"])
    verdict = (
        "PASS_R42D_MUTUAL_DRAW_UTILITY_REPLICATION_FIXED200"
        if passed
        else "FAIL_R42D_MUTUAL_DRAW_UTILITY_REPLICATION_NO_INCREMENT_FIXED200"
    )

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42D_REPLICATION_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": verdict,
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {
            "rows": int(len(excluded_ids)),
            "R41A_through_R41D_replication_hashes": prior_hashes,
            "R42A_identity_sha256": r42a_sha,
            "R42C_identity_sha256": r42c_sha,
            "parent_R42D_identity_sha256": parent_sha,
            "all_expected_hashes_match": True,
        },
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_2000": overlap,
            "competitions_represented": int(sample.competition_id.nunique()),
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_H": int((sample.outcome == 0).sum()),
            "actual_D": int((sample.outcome == 1).sum()),
            "actual_A": int((sample.outcome == 2).sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "exact_method_replication": {
            "parent_schema": parent_cfg["schema_version"],
            "parent_seed": int(parent_cfg["sample_contract"]["seed"]),
            "feature_names": utility_names,
            "feature_count": int(len(utility_names)),
            "baseline_features": baseline_features,
            "challenger_feature_count": int(len(challenger_features)),
            "fit_contract": parent_cfg["fit_contract"],
            "method_contract": parent_cfg["method_contract"],
            "market_contract": parent_cfg["market_contract"],
            "utility_build_audit": utility_audit,
            "scientific_parameters_changed_from_parent": 0,
        },
        "model_contract": {"baseline": base_receipt, "challenger": challenger_receipt},
        "comparison": comparison,
        "market_boundary": {
            "scope": "retrospective closing/reference 1X2 plus prior-only standings counterfactuals",
            "formal_PIT_claim": False,
            "reason": "market quote timestamps are absent",
        },
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    parent = load_json(ROOT / "config" / "r42d_mutual_draw_utility_fixed200.json")
    assert len(parent["method_contract"]["counterfactual_features"]) == 22
    assert parent["method_contract"]["manual_draw_bonus"] is False
    assert parent["method_contract"]["manual_threshold"] is None
    print(json.dumps({"status": "PASS", "self_test": True, "scientific_parameters_changed": 0}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "comparison": result["comparison"],
        "exact_method_replication": result["exact_method_replication"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
