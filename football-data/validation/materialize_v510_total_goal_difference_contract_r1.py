#!/usr/bin/env python3
"""Materialize the V5.1 direct-total and conditional-goal-difference input contract.

This stage does not fit probabilities. It joins the existing frozen web/market packets to
settled 90-minute scores, builds auditable inputs for P(T) and P(D|T,X), verifies the legal
T/D-to-score mapping, and fails closed when the 7+ aggregate tail cannot be decomposed.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import audit_v510_pit_context_readiness_r1 as audit
import materialize_v510_context_feature_packets_r1 as packets_module

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_total_goal_difference_contract_r1.json"
PACKET_CONFIG = ROOT / "config" / "v510_context_feature_packet_r1.json"
CONTEXT = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_total_goal_difference_contract_r1_status.json"


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"JSON root is not an object: {path.relative_to(ROOT)}")
    return value


def two_way_devig(raw: dict[str, Any]) -> dict[str, float]:
    try:
        left = float(raw["side_a_price"])
        right = float(raw["side_b_price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid two-way prices: {raw}") from exc
    if not all(math.isfinite(x) and x > 1.0 for x in (left, right)):
        raise ContractError(f"non-decimal two-way prices: {raw}")
    inverse = (1.0 / left, 1.0 / right)
    total = sum(inverse)
    result = {"side_a": inverse[0] / total, "side_b": inverse[1] / total}
    if abs(sum(result.values()) - 1.0) > 1e-12:
        raise ContractError("two-way probability conservation failure")
    return result


def total_bucket(total: int) -> str:
    if total < 0:
        raise ContractError(f"negative total: {total}")
    return str(total) if total <= 6 else "7+"


def legal_d_support(total: int) -> list[int]:
    if total < 0:
        raise ContractError(f"negative total: {total}")
    return list(range(-total, total + 1, 2))


def map_total_difference(total: int, difference: int) -> tuple[int, int]:
    if difference not in legal_d_support(total):
        raise ContractError(f"illegal T/D parity or range: T={total}, D={difference}")
    home_numerator = total + difference
    away_numerator = total - difference
    if home_numerator % 2 or away_numerator % 2:
        raise ContractError(f"non-integer score mapping: T={total}, D={difference}")
    home = home_numerator // 2
    away = away_numerator // 2
    if home < 0 or away < 0:
        raise ContractError(f"negative score mapping: T={total}, D={difference}")
    return home, away


def packet_identity(packet: dict[str, Any]) -> tuple[str, str, str, str]:
    fixture = packet.get("fixture_identity") or {}
    key = (
        str(fixture.get("competition_id") or "").strip(),
        str(fixture.get("kickoff_at_utc") or "").strip(),
        str(fixture.get("normalized_home_team") or "").strip(),
        str(fixture.get("normalized_away_team") or "").strip(),
    )
    if not all(key):
        raise ContractError(f"incomplete packet identity: {fixture}")
    return key


def score_label(result: dict[str, Any]) -> dict[str, Any]:
    try:
        home = int(result["home_goals_90"])
        away = int(result["away_goals_90"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid 90-minute score: {result}") from exc
    if home < 0 or away < 0:
        raise ContractError(f"negative score: {home}-{away}")
    total = home + away
    difference = home - away
    mapped = map_total_difference(total, difference)
    if mapped != (home, away):
        raise ContractError("score mapping reversibility failure")
    return {
        "home_goals_90": home,
        "away_goals_90": away,
        "total_goals": total,
        "total_bucket": total_bucket(total),
        "goal_difference": difference,
        "actual_result": "home" if home > away else "away" if away > home else "draw",
    }


def structural_support() -> dict[str, Any]:
    exact: dict[str, list[dict[str, int]]] = {}
    for total in range(7):
        exact[str(total)] = [
            {"D": difference, "H": map_total_difference(total, difference)[0], "A": map_total_difference(total, difference)[1]}
            for difference in legal_d_support(total)
        ]
    return {
        "exact_total_support_0_to_6": exact,
        "aggregate_7plus": {
            "exact_score_mapping_available": False,
            "reason": "7+ does not identify exact T; P(T=t|T>=7) is missing",
            "manual_tail_allocation_allowed": False,
        },
    }


def run_contract(
    config: dict[str, Any],
    packet_config: dict[str, Any],
    context: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    packet_status = packets_module.materialize(packet_config, context)
    if not packet_status.get("audit", {}).get("passed"):
        raise ContractError("context packet audit did not pass")
    packet_rows = packet_status.get("packets")
    result_rows = results.get("results")
    if not isinstance(packet_rows, list):
        raise ContractError("packet materializer returned no packet list")
    if not isinstance(result_rows, list):
        raise ContractError("result inbox missing results list")

    result_index, result_conflicts = audit.build_result_index(result_rows)
    result_label_errors: list[dict[str, Any]] = []
    all_result_labels: list[dict[str, Any]] = []
    for key, result in result_index.items():
        try:
            all_result_labels.append({"identity": list(key), **score_label(result)})
        except Exception as exc:
            result_label_errors.append({"identity": list(key), "reason": f"{type(exc).__name__}: {exc}"})

    rows: list[dict[str, Any]] = []
    packet_errors: list[dict[str, Any]] = []
    total_counts: Counter[str] = Counter()
    difference_counts: defaultdict[str, Counter[int]] = defaultdict(Counter)
    settled_count = 0
    synchronized_market_count = 0

    for index, packet in enumerate(packet_rows):
        try:
            key = packet_identity(packet)
            market = packet.get("market_features") or {}
            asian = market.get("asian_handicap") or {}
            over_under = market.get("over_under") or {}
            if not bool(market.get("synchronized_three_market_complete")):
                raise ContractError("synchronized 1X2/AH/OU market incomplete")
            ah_probs = two_way_devig(asian)
            ou_probs = two_way_devig(over_under)
            synchronized_market_count += 1
            result = result_index.get(key)
            label = score_label(result) if result is not None else None
            if label is not None:
                settled_count += 1
                total_counts[label["total_bucket"]] += 1
                difference_counts[label["total_bucket"]][label["goal_difference"]] += 1

            fixture = packet["fixture_identity"]
            context_features = packet.get("context_features") or {}
            common_x = {
                "competition_id": fixture.get("competition_id"),
                "home_team": fixture.get("home_team"),
                "away_team": fixture.get("away_team"),
                "freeze": packet.get("freeze"),
                "packet_sha256": packet.get("packet_sha256"),
                "devig_1x2": market.get("devig_probabilities"),
                "asian_handicap": {
                    "line": asian.get("line"),
                    "home_probability": ah_probs["side_a"],
                    "away_probability": ah_probs["side_b"],
                },
                "over_under": {
                    "line": over_under.get("line"),
                    "over_probability": ou_probs["side_a"],
                    "under_probability": ou_probs["side_b"],
                },
                "context": {
                    "source_tier": context_features.get("source_tier"),
                    "source_tier_weight": context_features.get("source_tier_weight"),
                    "home_availability_state": context_features.get("home_availability_state"),
                    "away_availability_state": context_features.get("away_availability_state"),
                    "home_availability_count": context_features.get("home_availability_count"),
                    "away_availability_count": context_features.get("away_availability_count"),
                    "home_predicted_xi_state": context_features.get("home_predicted_xi_state"),
                    "away_predicted_xi_state": context_features.get("away_predicted_xi_state"),
                    "both_predicted_xi_complete": context_features.get("both_predicted_xi_complete"),
                },
            }
            rows.append({
                "identity": list(key),
                "settled": label is not None,
                "direct_total_input": common_x,
                "conditional_goal_difference_input": {
                    **common_x,
                    "conditioning_total_goals": label.get("total_goals") if label else None,
                    "legal_goal_difference_support": legal_d_support(label["total_goals"]) if label else None,
                },
                "label": label,
            })
        except Exception as exc:
            packet_errors.append({"packet_index": index, "reason": f"{type(exc).__name__}: {exc}"})

    direct_cfg = config.get("direct_total_goals") or {}
    cond_cfg = config.get("conditional_goal_difference") or {}
    observed_nonempty_buckets = sum(count > 0 for count in total_counts.values())
    settled_draws = sum(row.get("label", {}).get("actual_result") == "draw" for row in rows if row.get("label"))
    rows_per_observed_total = {
        bucket: sum(counter.values()) for bucket, counter in sorted(difference_counts.items())
    }

    direct_fit_checks = {
        "minimum_settled_context_scores": settled_count >= int(direct_cfg.get("minimum_settled_context_scores", 60)),
        "minimum_settled_draws": settled_draws >= int(direct_cfg.get("minimum_settled_draws", 15)),
        "minimum_observed_total_buckets": observed_nonempty_buckets >= int(direct_cfg.get("minimum_observed_total_buckets", 6)),
        "minimum_7plus_rows": total_counts["7+"] >= int(direct_cfg.get("minimum_7plus_rows", 8)),
    }
    conditional_fit_checks = {
        "minimum_settled_context_scores": settled_count >= int(cond_cfg.get("minimum_settled_context_scores", 60)),
        "minimum_rows_per_observed_total": bool(rows_per_observed_total) and all(
            count >= int(cond_cfg.get("minimum_rows_per_fitted_total", 10)) for count in rows_per_observed_total.values()
        ),
        "all_observed_labels_map_legally": not packet_errors and not result_label_errors,
    }
    input_checks = {
        "context_packet_layer_passed": bool(packet_status.get("audit", {}).get("passed")),
        "all_packets_materialized": len(rows) == len(packet_rows),
        "no_packet_errors": not packet_errors,
        "all_packets_have_synchronized_1x2_ah_ou": synchronized_market_count == len(packet_rows),
        "no_result_conflicts": not result_conflicts,
        "all_result_scores_legal": not result_label_errors,
        "exact_support_0_to_6_reversible": all(
            map_total_difference(t, d)[0] + map_total_difference(t, d)[1] == t
            and map_total_difference(t, d)[0] - map_total_difference(t, d)[1] == d
            for t in range(7) for d in legal_d_support(t)
        ),
    }
    input_passed = all(input_checks.values())
    direct_fit_ready = all(direct_fit_checks.values())
    conditional_fit_ready = all(conditional_fit_checks.values())
    tail_decomposition_available = False
    unified_matrix_ready = direct_fit_ready and conditional_fit_ready and tail_decomposition_available

    status = "PASS_INPUT_CONTRACT_SCORE_MODEL_NOT_IDENTIFIABLE" if input_passed else "FAIL_INPUT_CONTRACT"
    return {
        "schema_version": "V5.1.0-total-goal-difference-contract-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "classification": "INPUT_CONTRACT_AND_IDENTIFIABILITY_AUDIT_FORMAL_WEIGHT_0",
        "counts": {
            "context_packets": len(packet_rows),
            "contract_rows": len(rows),
            "synchronized_1x2_ah_ou_rows": synchronized_market_count,
            "result_inbox_unique_scores": len(result_index),
            "settled_context_score_overlap": settled_count,
            "settled_context_draws": settled_draws,
            "packet_errors": len(packet_errors),
            "result_conflicts": len(result_conflicts),
            "result_label_errors": len(result_label_errors),
        },
        "label_distributions": {
            "all_result_inbox_total_buckets": dict(sorted(Counter(row["total_bucket"] for row in all_result_labels).items())),
            "settled_context_total_buckets": dict(sorted(total_counts.items())),
            "settled_context_goal_difference_by_total": {
                bucket: {str(d): count for d, count in sorted(counter.items())}
                for bucket, counter in sorted(difference_counts.items())
            },
        },
        "structural_support": structural_support(),
        "input_audit": {"passed": input_passed, "checks": input_checks},
        "direct_total_fit_gate": {"passed": direct_fit_ready, "checks": direct_fit_checks},
        "conditional_goal_difference_fit_gate": {"passed": conditional_fit_ready, "checks": conditional_fit_checks},
        "tail_gate": {
            "passed": tail_decomposition_available,
            "required": "P(T=t|T>=7) and P(D=d|T=t,X) for exact tail totals",
            "reason": "no executable frozen tail decomposition exists",
        },
        "unified_score_matrix_gate": {
            "passed": unified_matrix_ready,
            "checks": {
                "direct_total_fit_ready": direct_fit_ready,
                "conditional_goal_difference_fit_ready": conditional_fit_ready,
                "7plus_tail_decomposition_ready": tail_decomposition_available,
                "probability_conservation_audit": False,
                "market_constraint_residual_audit": False,
            },
        },
        "module_states": {
            "direct_total_input_contract": "通过" if input_passed else "失败",
            "conditional_goal_difference_input_contract": "通过" if input_passed else "失败",
            "direct_total_model": "不可用" if not direct_fit_ready else "未启用",
            "conditional_goal_difference_model": "不可用" if not conditional_fit_ready else "未启用",
            "unified_score_matrix": "不可用" if not unified_matrix_ready else "未启用",
        },
        "fixed_outputs": {
            "total_goals": "总进球分布不可用。",
            "exact_score": "精确比分不可用。",
        },
        "errors": {
            "packet_errors": packet_errors,
            "result_conflicts": result_conflicts,
            "result_label_errors": result_label_errors,
        },
        "rows": rows,
        "contract": config,
        "governance": {
            "formal_weight": 0,
            "model_fit": False,
            "probability_generation": False,
            "manual_total_distribution": False,
            "manual_goal_difference_distribution": False,
            "manual_tail_allocation": False,
            "formal_total_output": False,
            "exact_score_output": False,
            "ev_available": False,
            "provider_requests": 0,
            "new_data_collection": False,
            "current_change": False,
            "main_change": False,
        },
        "next_action": "retain the passed input contract; acquire sufficient settled frozen context labels and implement an explicit 7+ tail model before any unified score matrix",
    }


def self_test() -> None:
    assert legal_d_support(0) == [0]
    assert legal_d_support(1) == [-1, 1]
    assert legal_d_support(2) == [-2, 0, 2]
    assert map_total_difference(2, 0) == (1, 1)
    assert map_total_difference(3, -1) == (1, 2)
    assert total_bucket(6) == "6"
    assert total_bucket(7) == "7+"
    probs = two_way_devig({"side_a_price": 1.91, "side_b_price": 1.99})
    assert abs(sum(probs.values()) - 1.0) < 1e-12
    support = structural_support()
    assert support["aggregate_7plus"]["exact_score_mapping_available"] is False
    try:
        map_total_difference(2, 1)
    except ContractError:
        pass
    else:
        raise AssertionError("illegal parity must fail")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--packet-config", type=Path, default=PACKET_CONFIG)
    parser.add_argument("--context", type=Path, default=CONTEXT)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}, ensure_ascii=False))
        return 0
    payload = run_contract(
        load_json(args.config),
        load_json(args.packet_config),
        load_json(args.context),
        load_json(args.results),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "counts": payload["counts"],
        "label_distributions": payload["label_distributions"],
        "input_audit": payload["input_audit"],
        "direct_total_fit_gate": payload["direct_total_fit_gate"],
        "conditional_goal_difference_fit_gate": payload["conditional_goal_difference_fit_gate"],
        "tail_gate": payload["tail_gate"],
        "unified_score_matrix_gate": payload["unified_score_matrix_gate"],
        "fixed_outputs": payload["fixed_outputs"],
    }, ensure_ascii=False, indent=2))
    return 0 if payload["input_audit"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
