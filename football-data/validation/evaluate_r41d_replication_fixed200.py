#!/usr/bin/env python3
"""R41D replication on a fresh disjoint fixed-200 sample.

The scientific method is frozen from R41D in PR #154. This evaluator only
reproduces the six previously consumed fixed200 identity sets, excludes them,
selects a new identity-only fixed200, and evaluates the unchanged HDA+AH+OU2.5
structural state model against the unchanged calibration-only HDA baseline.
Historical market prices remain retrospective references, not formal PIT data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
)
from evaluate_r41_priority_fixed200_battery import (
    HDA_CLASSES,
    compare,
    materialize_market,
    prepare_features,
    select_method_sample,
)
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, complete_seasons
from v510_historical_structure_model_r1 import align_probability, make_model

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r41d_replication_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r41d_replication_fixed200_status.json"


def digest_ids(ids: set[str] | list[str]) -> str:
    values = sorted(str(x) for x in ids)
    return hashlib.sha256(("\n".join(values) + "\n").encode("utf-8")).hexdigest()


def fixed_model_probability(
    fit: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    C: float,
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    model = make_model(float(C), base_cfg)
    model.fit(fit[features], fit.outcome)
    p = align_probability(model, test[features], HDA_CLASSES)
    return p, {
        "fixed_C": float(C),
        "feature_count": int(len(features)),
        "features": list(features),
        "probability_sum_max_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))),
    }


def reproduce_prior_samples(frame: pd.DataFrame, cfg: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, str]]:
    prior_cfg = {str(x["id"]): x for x in cfg["sample_contract"]["prior_samples"]}
    target_pool = frame[frame.split == "target_pool"].copy()

    sets: dict[str, set[str]] = {}
    hashes: dict[str, str] = {}

    ids_a, sha_a = select_fixed_identities(target_pool, 200, int(prior_cfg["R41A"]["seed"]))
    sets["R41A"] = set(ids_a)
    hashes["R41A"] = sha_a

    pool_b = target_pool[~target_pool.identity_key.isin(sets["R41A"])]
    ids_b, sha_b = select_fixed_identities(pool_b, 200, int(prior_cfg["R41B"]["seed"]))
    sets["R41B"] = set(ids_b)
    hashes["R41B"] = sha_b

    excluded = sets["R41A"] | sets["R41B"]
    eligible_c = frame.book_count.fillna(0).astype(int) >= int(cfg["market_contract"]["cross_book_min_complete_books"])
    eligible_d = (frame.book_count.fillna(0).astype(int) >= 1) & frame.has_ah_ou.fillna(False)
    eligible_e = frame.book_count.fillna(0).astype(int) >= 1

    for key, eligible in (("R41C", eligible_c), ("R41D", eligible_d), ("R41E", eligible_e), ("R41F", eligible_d)):
        p = prior_cfg[key]
        sample, sha = select_method_sample(frame, eligible, excluded, 200, int(p["seed"]))
        sets[key] = set(sample.identity_key.astype(str))
        hashes[key] = sha
        excluded |= sets[key]

    for key, expected in prior_cfg.items():
        actual = hashes[key]
        if actual != str(expected["identity_sha256"]):
            raise ResearchError(f"prior sample identity mismatch {key}: {actual} != {expected['identity_sha256']}")

    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            overlap = len(sets[left] & sets[right])
            if overlap:
                raise ResearchError(f"prior sample overlap {left}/{right}: {overlap}")
    return sets, hashes


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    market = materialize_market(raw, cfg["market_contract"])
    frame = prepare_features(raw, market, seasons, cfg)

    prior_sets, prior_hashes = reproduce_prior_samples(frame, cfg)
    excluded_ids = set().union(*prior_sets.values())
    if len(excluded_ids) != 1200:
        raise ResearchError(f"expected 1200 prior disjoint identities, got {len(excluded_ids)}")

    eligible = (frame.book_count.fillna(0).astype(int) >= 1) & frame.has_ah_ou.fillna(False)
    sample, sample_sha = select_method_sample(
        frame,
        eligible,
        excluded_ids,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    overlaps = {key: int(len(set(sample.identity_key.astype(str)) & ids)) for key, ids in prior_sets.items()}
    if any(overlaps.values()):
        raise ResearchError(f"replication sample overlaps prior fixed200: {overlaps}")

    eligible_fit = frame[eligible].copy()
    fit = eligible_fit[eligible_fit.split.isin(["train", "policy"])].copy()
    if fit.empty:
        raise ResearchError("empty frozen R41D fit set")

    baseline_features = [str(x) for x in cfg["method_contract"]["baseline_features"]]
    challenger_features = [str(x) for x in cfg["method_contract"]["challenger_features"]]
    baseline_p, baseline_receipt = fixed_model_probability(
        fit, sample, baseline_features, float(cfg["fit_contract"]["baseline_fixed_C"]), base_cfg
    )
    challenger_p, challenger_receipt = fixed_model_probability(
        fit, sample, challenger_features, float(cfg["fit_contract"]["challenger_fixed_C"]), base_cfg
    )

    comparison = compare(
        sample.outcome.to_numpy(int),
        baseline_p,
        challenger_p,
        cfg,
        int(cfg["sample_contract"]["seed"]) + 1,
    )
    pass_gate = bool(comparison["gate"]["all_required"])
    verdict = (
        "PASS_R41D_REPLICATION_AH_OU_STRUCTURAL_INCREMENT_FIXED200"
        if pass_gate
        else "FAIL_R41D_REPLICATION_AH_OU_STRUCTURAL_NO_INCREMENT_FIXED200"
    )

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R41D_REPLICATION_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": verdict,
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "parent_binding": cfg["parent_evidence"],
        "prior_fixed200_reproduction": {
            "rows": int(len(excluded_ids)),
            "identity_sha256_by_stage": prior_hashes,
            "all_expected_hashes_match": True,
        },
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "eligible_target_pool_before_exclusions": int(((frame.split == "target_pool") & eligible).sum()),
            "prior_excluded_rows": int(len(excluded_ids)),
            "overlap_with_prior_fixed200": overlaps,
            "competitions_represented": int(sample.competition_id.nunique()),
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "model_contract": {
            "baseline": baseline_receipt,
            "challenger": challenger_receipt,
            "feature_contract_exact_from_parent": True,
            "regularization_contract_frozen_from_parent": True,
            "fixed200_used_for_regularization_selection": False,
        },
        "comparison": comparison,
        "replication_summary": {
            "parent_logloss_delta": -0.003070295688100133,
            "parent_logloss_p95": 0.0019149211914298045,
            "parent_draw_auc_baseline": 0.5974124809741248,
            "parent_draw_auc_challenger": 0.6184677828513445,
            "replication_logloss_delta": comparison["delta_challenger_minus_baseline"]["logloss"],
            "replication_logloss_p95": comparison["paired_bootstrap_logloss_delta_90"]["p95"],
            "replication_draw_auc_baseline": comparison["baseline_draw"]["draw_auc"],
            "replication_draw_auc_challenger": comparison["challenger_draw"]["draw_auc"],
            "same_frozen_gate_passed": pass_gate,
        },
        "market_boundary": {
            "scope": "retrospective closing/reference HDA+AH+OU2.5",
            "formal_PIT_claim": False,
            "reason": "original quote timestamps are absent",
        },
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    assert digest_ids({"b", "a"}) == hashlib.sha256(b"a\nb\n").hexdigest()
    print(json.dumps({"status": "PASS", "self_test": True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "replication_summary": result["replication_summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
