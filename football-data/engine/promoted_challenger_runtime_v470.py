#!/usr/bin/env python3
"""Receipt-gated runtime application for formally promoted V4.7 challengers.

Current formal activation:
- USA_MLS, target season 2026, conditional_allocation_v470 only.

The promoted D|T transform is applied AFTER the optional OOF full-matrix
calibration so the final calibrated total-goal marginal P(T) is preserved exactly.
Any receipt, competition, season, code or bound-artifact mismatch fails closed and
leaves the current formal matrix unchanged.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from conditional_allocation_challenger_v470 import apply_conditional_exponential_tilt
from platform_core import (
    ROOT,
    PlatformError,
    derive_score_marginals,
    load_json,
    score_matrix_rows,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    top_scores,
)

MODULE_PATH = Path(__file__).resolve()
CONDITIONAL_CODE = ROOT / "engine" / "conditional_allocation_challenger_v470.py"
COMPETITION_CONFIG = ROOT / "config" / "competition_independent_v470.json"
RUNTIME_MAINTENANCE = ROOT / "manifests" / "runtime_maintenance_v473_status.json"
FINAL_CHAIN_REPLAY = ROOT / "manifests" / "final_chain_replay_v463_status.json"
PROMOTION_RECEIPTS = {
    "USA_MLS": ROOT / "manifests" / "promotions" / "USA_MLS_d_conditional_v470.json",
}


def _set_state(calculation: dict[str, Any], status: str, reason: str, *, receipt_path: Path | None = None) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    output.setdefault("module_states", {})["conditional_allocation_v470"] = status
    audit: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "method": "competition_specific_post_oof_D_given_T_exponential_tilt",
    }
    if receipt_path is not None and receipt_path.exists():
        audit["receipt_path"] = str(receipt_path.relative_to(ROOT))
        audit["receipt_sha256"] = sha256_file(receipt_path)
    output["conditional_allocation_v470_audit"] = audit
    return output


def _derive_line_market(matrix: list[dict[str, Any]], line: float, settlement_fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for home, away, probability in score_matrix_rows(matrix):
        settlement = settlement_fn(home, away, line)
        for key in result:
            result[key] += probability * settlement[key]
    return result


def _conditional_goal_difference_by_total(matrix: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for home, away, probability in score_matrix_rows(matrix):
        grouped[home + away].append((home - away, probability))
    output: dict[str, dict[str, float]] = {}
    for total, items in sorted(grouped.items()):
        total_probability = sum(probability for _, probability in items)
        if total_probability <= 0:
            continue
        distribution: Counter[str] = Counter()
        for difference, probability in items:
            distribution[str(difference)] += probability / total_probability
        output[str(total)] = {
            key: float(value)
            for key, value in sorted(distribution.items(), key=lambda item: int(item[0]))
        }
    return output


def _minimum_score_set(matrix: list[dict[str, Any]], target: float) -> dict[str, Any]:
    ranking = top_scores(matrix, len(matrix))
    cumulative = 0.0
    selected = []
    for item in ranking:
        selected.append(item)
        cumulative += float(item["probability"])
        if cumulative + 1e-12 >= target:
            break
    return {
        "target": target,
        "size": len(selected),
        "cumulative_probability": cumulative,
        "scores": selected,
    }


def _structural_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {"btts": 0.0, "home_zero": 0.0, "away_zero": 0.0, "margin2plus": 0.0}
    for home, away, probability in score_matrix_rows(matrix):
        out["btts"] += probability if home > 0 and away > 0 else 0.0
        out["home_zero"] += probability if home == 0 else 0.0
        out["away_zero"] += probability if away == 0 else 0.0
        out["margin2plus"] += probability if abs(home - away) >= 2 else 0.0
    return out


def _verify_receipt(competition_id: str, season: str, receipt_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not receipt_path.exists():
        return None, "promotion receipt missing"
    receipt = load_json(receipt_path)
    if receipt.get("promotion_status") != "PROMOTED":
        return None, f"receipt is not promoted: {receipt.get('promotion_status')}"
    if receipt.get("competition_id") != competition_id:
        return None, "promotion receipt competition mismatch"
    if str(receipt.get("target_season")) != season:
        return None, f"promotion receipt is not valid for target season {season}"
    if receipt.get("module") != "conditional_allocation_v470":
        return None, "promotion receipt module mismatch"
    if float(receipt.get("formal_weight", 0.0)) != 1.0:
        return None, "promotion receipt does not activate the fully validated transform"
    if receipt.get("activation_mode") != "full_validated_transform":
        return None, "unsupported promotion activation mode"
    if receipt.get("activation_order") != "post_oof_matrix_calibration":
        return None, "promotion receipt activation order mismatch"

    evidence = receipt.get("evidence") or {}
    bound_files = {
        "conditional_code_sha256": CONDITIONAL_CODE,
        "priority_artifact_sha256": ROOT / str(evidence.get("priority_artifact_path") or ""),
        "oof_calibrator_sha256": ROOT / str(evidence.get("oof_calibrator_path") or ""),
        "final_chain_review_sha256": ROOT / str(evidence.get("final_chain_review_path") or ""),
        "competition_independence_config_sha256": COMPETITION_CONFIG,
    }
    for hash_field, path in bound_files.items():
        expected = str(evidence.get(hash_field) or "")
        if not expected or not path.exists():
            return None, f"bound promotion artifact missing: {hash_field}"
        if sha256_file(path) != expected:
            return None, f"bound promotion artifact hash mismatch: {hash_field}"

    maintenance = load_json(RUNTIME_MAINTENANCE)
    if maintenance.get("status") != "PASS" or int(maintenance.get("hard_error_count") or 0) != 0:
        return None, "runtime maintenance is not PASS"
    replay = load_json(FINAL_CHAIN_REPLAY)
    if ((replay.get("reports") or {}).get(competition_id) or {}).get("status") != "通过":
        return None, "current formal core final-chain replay is not passing"
    config = load_json(COMPETITION_CONFIG)
    default_policy = config.get("default_competition_policy") or {}
    if competition_id not in set(config.get("competitions") or []):
        return None, "competition missing from independence registry"
    if default_policy.get("allow_cross_competition_training_rows") is not False:
        return None, "cross-competition training isolation gate failed"
    if default_policy.get("allow_cross_competition_calibrator") is not False:
        return None, "cross-competition calibrator isolation gate failed"
    if default_policy.get("allow_cross_competition_challenger_weight") is not False:
        return None, "cross-competition challenger-weight isolation gate failed"
    return receipt, None


def apply_promoted_v470_post_calibration_challengers(
    context: dict[str, Any], calculation: dict[str, Any]
) -> dict[str, Any]:
    identity = context.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    season = str(identity.get("season") or calculation.get("model_audit", {}).get("season") or "")
    receipt_path = PROMOTION_RECEIPTS.get(competition_id)
    if receipt_path is None:
        return _set_state(calculation, "未启用", "no competition-specific V4.7 D|T promotion receipt")

    receipt, reason = _verify_receipt(competition_id, season, receipt_path)
    if receipt is None:
        status = "未启用" if "target season" in str(reason) else "不可用"
        return _set_state(calculation, status, str(reason), receipt_path=receipt_path)

    matrix = calculation.get("probabilities", {}).get("score_matrix")
    if not isinstance(matrix, list) or not matrix:
        return _set_state(calculation, "不可用", "final unified score matrix missing before promoted D|T", receipt_path=receipt_path)

    before_marginals = derive_score_marginals(matrix)
    before_structural = _structural_probs(matrix)
    try:
        promoted_matrix, transform_audit = apply_conditional_exponential_tilt(matrix, receipt.get("parameters") or {})
    except (PlatformError, KeyError, TypeError, ValueError) as exc:
        return _set_state(calculation, "不可用", f"promoted D|T transform failed: {exc}", receipt_path=receipt_path)

    marginals = derive_score_marginals(promoted_matrix)
    if abs(float(marginals["probability_sum"]) - 1.0) > 1e-10:
        raise PlatformError("promoted V4.7 D|T matrix failed probability conservation")
    total_keys = ("0", "1", "2", "3", "4", "5", "6", "7+")
    max_total_residual = max(
        abs(float(marginals["total_goals"][key]) - float(before_marginals["total_goals"][key]))
        for key in total_keys
    )
    if max_total_residual > 1e-10:
        raise PlatformError("promoted V4.7 D|T matrix changed the calibrated total-goal marginal")

    output = copy.deepcopy(calculation)
    output.setdefault("module_states", {})["conditional_allocation_v470"] = "通过"
    output["probabilities"] = {
        "one_x_two": marginals["1x2"],
        "total_goals": marginals["total_goals"],
        "btts_yes": marginals["btts_yes"],
        "score_matrix": promoted_matrix,
    }

    derived = output.get("derived_markets") or {}
    if isinstance(derived.get("home_handicap"), dict) and isinstance(derived["home_handicap"].get("line"), (int, float)):
        line = float(derived["home_handicap"]["line"])
        derived["home_handicap"] = {"line": line, **_derive_line_market(promoted_matrix, line, settle_home_handicap)}
    if isinstance(derived.get("over_total"), dict) and isinstance(derived["over_total"].get("line"), (int, float)):
        line = float(derived["over_total"]["line"])
        derived["over_total"] = {"line": line, **_derive_line_market(promoted_matrix, line, settle_over_total)}
    output["derived_markets"] = derived

    ranking = top_scores(promoted_matrix, 10)
    total_rank = sorted(marginals["total_goals"].items(), key=lambda item: (-item[1], item[0]))
    score_sets = {
        "80": _minimum_score_set(promoted_matrix, 0.80),
        "90": _minimum_score_set(promoted_matrix, 0.90),
    }
    output["conditional_goal_difference_audit"] = _conditional_goal_difference_by_total(promoted_matrix)
    output["score_set_audit"] = score_sets

    matrix_publishable = output.get("module_states", {}).get("unified_score_matrix") == "通过"
    conclusions = output.setdefault("conclusions", {})
    direction = max(marginals["1x2"], key=marginals["1x2"].get)
    conclusions.update({
        "result_direction": direction,
        "result_text": (
            f"90分钟最终统一矩阵概率：主胜{marginals['1x2']['home']:.1%}、"
            f"平局{marginals['1x2']['draw']:.1%}、客胜{marginals['1x2']['away']:.1%}。"
        ),
        "total_goals_text": f"最终总进球中心：{total_rank[0][0]}球；D|T结构校正保持0—7+总进球边际不变。",
        "total_goals_primary": total_rank[0][0],
        "total_goals_secondary": total_rank[1][0],
        "top_score": ranking[0]["score"] if matrix_publishable else None,
        "second_score": ranking[1]["score"] if matrix_publishable and len(ranking) > 1 else None,
        "top3_cumulative": sum(item["probability"] for item in ranking[:3]) if matrix_publishable else None,
        "top1_top2_gap": (ranking[0]["probability"] - ranking[1]["probability"]) if matrix_publishable and len(ranking) > 1 else None,
        "score_set_80": score_sets["80"],
        "score_set_90": score_sets["90"],
        "score_text": f"模型中心比分 {ranking[0]['score']}；EXACT独立门控未通过。" if matrix_publishable else "精确比分不可用。",
        "score_label": "模型中心比分" if matrix_publishable else "精确比分不可用",
    })
    confidence = conclusions.get("confidence_grade", "D")
    price_status = conclusions.get("price_status", "No Bet")
    conclusions["final_line"] = (
        f"{direction}；可信等级{confidence}；{price_status}；"
        + ("比分标签为模型中心比分。" if matrix_publishable else "精确比分不可用。")
    )

    output["conditional_allocation_v470_audit"] = {
        "status": "通过",
        "registration_status": "V4.7挑战层逐赛事域已晋级",
        "competition_id": competition_id,
        "target_season": season,
        "activation_mode": receipt.get("activation_mode"),
        "activation_order": receipt.get("activation_order"),
        "formal_weight": receipt.get("formal_weight"),
        "parameters": receipt.get("parameters"),
        "receipt_path": str(receipt_path.relative_to(ROOT)),
        "receipt_sha256": sha256_file(receipt_path),
        "runtime_module_sha256": sha256_file(MODULE_PATH),
        "transform_audit": transform_audit,
        "probability_sum_residual": float(marginals["probability_sum"]) - 1.0,
        "max_final_total_marginal_residual": max_total_residual,
        "before_structural_probabilities": before_structural,
        "after_structural_probabilities": _structural_probs(promoted_matrix),
        "policy": "USA_MLS 2026 only; receipt-gated post-OOF D|T correction; total-goal marginal preserved exactly within floating-point tolerance.",
    }
    return output
