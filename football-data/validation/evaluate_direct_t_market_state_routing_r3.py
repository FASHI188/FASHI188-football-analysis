#!/usr/bin/env python3
"""R3 post-view market-state routing anatomy.

Tests whether a fixed current-match bookmaker market-state block explains the same-base
Direct-T row-oracle heterogeneity that probability geometry and historical state failed
to explain in R2. Existing processed market columns are retrospective closing/reference
evidence without original quote timestamps, so this can never be a strict-PIT or
confirmation result.

The script first freezes market coverage using identity/source-row provenance only. If the
pre-registered coverage gate fails, it writes a coverage-only STOP result and does not run
routing metrics. B05+ labels are never accessed.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from evaluate_direct_t_expected_loss_routing_r2 import (
    _fit_relative,
    _identity_sha,
    _raw_block,
    _relative_diagnostics,
    _selected_probabilities,
)
from evaluate_direct_t_oracle_learnability_r1 import (
    DEFAULT_PARENT_COHORT,
    DEFAULT_PARENT_CONFIG,
    DEFAULT_PARENT_STATUS,
    EXPERTS,
    ROOT,
    bootstrap_delta,
    build_frame,
    fit_experts,
    meta_features,
    metrics,
)
from evaluate_r41a_fixed200_joint_error_decomposition import load_json
from evaluate_viewed_common_cohort_oracle_r1 import _true_loss, replay_r40f
from v510_historical_structure_features_r1 import ResearchError, select_core_features

CONFIG = ROOT / "config" / "direct_t_market_state_routing_r3.json"
OUT_DIR = ROOT / "manifests" / "direct_t_market_state_routing_r3"
EXPECTED_ROWS = 1584
EXPECTED_ID_SHA = "0e7c57c5168280cd8b3264fe3c04d46d5caa51b6b6f4218aee84826bf7d7908c"
EXPECTED_COMMON_LL = 1.852486746078988
EXPECTED_ORACLE_LL = 1.817277840385054


def _key(row: Any) -> tuple[str, str, str, str]:
    return (
        str(row.competition_id),
        str(row.date_key),
        str(row.home_team),
        str(row.away_team),
    )


def _devig(values: list[float]) -> tuple[np.ndarray, float]:
    inv = np.asarray([1.0 / float(x) for x in values], dtype=float)
    total = float(inv.sum())
    if not np.isfinite(inv).all() or total <= 0.0:
        raise ResearchError("INVALID_MARKET_INVERSE_ODDS")
    return inv / total, total


def _first_complete_values(
    row: dict[str, Any], headers: list[str], candidates: list[list[Any]]
) -> tuple[list[float], list[str], Any] | None:
    for candidate in candidates:
        aliases = [str(x) for x in candidate[:2 if len(candidate) == 3 else len(candidate)]]
        fields = [field_name(headers, [alias]) for alias in aliases]
        if not all(fields):
            continue
        values = [float_value(row, field) for field in fields]
        if all(valid_price(value) for value in values):
            extra = candidate[2] if len(candidate) == 3 else None
            return [float(value) for value in values], [str(field) for field in fields], extra
    return None


def _market_vector(
    row: dict[str, Any], headers: list[str], market_cfg: dict[str, Any]
) -> tuple[np.ndarray | None, str | None, dict[str, Any]]:
    one = _first_complete_values(row, headers, market_cfg["one_x_two_alias_priority"])
    if one is None:
        return None, "one_x_two_missing", {}
    one_values, one_fields, _ = one
    p1, over1 = _devig(one_values)

    ah_line_field = field_name(headers, [str(x) for x in market_cfg["asian_line_alias_priority"]])
    ah_line = float_value(row, ah_line_field) if ah_line_field else None
    if ah_line is None or not np.isfinite(float(ah_line)):
        return None, "asian_line_missing", {"one_x_two_fields": one_fields}
    ah = _first_complete_values(row, headers, market_cfg["asian_price_alias_priority"])
    if ah is None:
        return None, "asian_prices_missing", {"one_x_two_fields": one_fields}
    ah_values, ah_fields, _ = ah
    pah, overah = _devig(ah_values)

    ou = _first_complete_values(row, headers, market_cfg["ou_price_alias_priority"])
    if ou is None:
        return None, "ou_prices_missing", {
            "one_x_two_fields": one_fields,
            "asian_fields": ah_fields,
            "asian_line_field": ah_line_field,
        }
    ou_values, ou_fields, implicit_line = ou
    ou_line = implicit_line
    ou_line_field = None
    if ou_line is None:
        ou_line_field = field_name(headers, [str(x) for x in market_cfg["ou_line_alias_priority"]])
        ou_line = float_value(row, ou_line_field) if ou_line_field else None
    if ou_line is None or not np.isfinite(float(ou_line)):
        return None, "ou_line_missing", {
            "one_x_two_fields": one_fields,
            "asian_fields": ah_fields,
            "asian_line_field": ah_line_field,
            "ou_fields": ou_fields,
        }
    pou, overou = _devig(ou_values)

    vector = np.asarray(
        [
            p1[0], p1[1], p1[2], over1,
            float(ah_line), pah[0], overah,
            float(ou_line), pou[0], overou,
        ],
        dtype=float,
    )
    if vector.shape != (10,) or not np.isfinite(vector).all():
        raise ResearchError("INVALID_MARKET10_VECTOR")
    provenance = {
        "one_x_two_fields": one_fields,
        "asian_line_field": str(ah_line_field),
        "asian_fields": ah_fields,
        "ou_line_field": str(ou_line_field) if ou_line_field else "implicit_2.5",
        "ou_fields": ou_fields,
    }
    return vector, None, provenance


def _ledger_source_map(ledger: pd.DataFrame) -> dict[tuple[str, str, str, str], tuple[str, int]]:
    required = {
        "competition_id", "date_key", "home_team", "away_team", "source_file", "row_number"
    }
    missing = sorted(required - set(ledger.columns))
    if missing:
        raise ResearchError(f"MARKET_LEDGER_FIELDS_MISSING:{missing}")
    mapping: dict[tuple[str, str, str, str], tuple[str, int]] = {}
    duplicates: list[tuple[str, str, str, str]] = []
    for row in ledger.itertuples(index=False):
        k = _key(row)
        if k in mapping:
            duplicates.append(k)
            continue
        mapping[k] = (str(row.source_file), int(row.row_number))
    if duplicates:
        raise ResearchError(f"MARKET_LEDGER_DUPLICATE_IDENTITIES:{len(duplicates)}")
    return mapping


def _market_matrix(
    frame: pd.DataFrame,
    ledger_map: dict[tuple[str, str, str, str], tuple[str, int]],
    market_cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    n = len(frame)
    matrix = np.full((n, 10), np.nan, dtype=float)
    reasons: Counter[str] = Counter()
    provenance_rows: list[dict[str, Any]] = [{} for _ in range(n)]
    wanted_by_source: dict[str, dict[int, list[tuple[int, tuple[str, str, str, str]]]]] = defaultdict(lambda: defaultdict(list))

    for pos, row in enumerate(frame.itertuples(index=False)):
        k = _key(row)
        source = ledger_map.get(k)
        if source is None:
            reasons["ledger_identity_missing"] += 1
            provenance_rows[pos] = {"identity_key": str(row.identity_key), "complete": False, "reason": "ledger_identity_missing"}
            continue
        source_file, row_number = source
        wanted_by_source[source_file][row_number].append((pos, k))
        provenance_rows[pos] = {
            "identity_key": str(row.identity_key),
            "source_file": source_file,
            "row_number": int(row_number),
        }

    for source_file, wanted in sorted(wanted_by_source.items()):
        path = ROOT / source_file
        if not path.is_file():
            for entries in wanted.values():
                for pos, _ in entries:
                    reasons["source_file_missing"] += 1
                    provenance_rows[pos].update({"complete": False, "reason": "source_file_missing"})
            continue
        seen: set[int] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            for row_number, raw in enumerate(reader, start=2):
                if row_number not in wanted:
                    continue
                seen.add(row_number)
                vector, reason, fields = _market_vector(raw, headers, market_cfg)
                for pos, _ in wanted[row_number]:
                    if vector is None:
                        reasons[str(reason)] += 1
                        provenance_rows[pos].update({"complete": False, "reason": str(reason), **fields})
                    else:
                        matrix[pos] = vector
                        provenance_rows[pos].update({"complete": True, "reason": None, **fields})
        for row_number, entries in wanted.items():
            if row_number in seen:
                continue
            for pos, _ in entries:
                reasons["source_row_missing"] += 1
                provenance_rows[pos].update({"complete": False, "reason": "source_row_missing"})

    complete = np.isfinite(matrix).all(axis=1)
    if int(complete.sum()) + int((~complete).sum()) != n:
        raise ResearchError("MARKET_COVERAGE_ACCOUNTING_MISMATCH")
    audit = {
        "rows": int(n),
        "complete_rows": int(complete.sum()),
        "complete_rate": float(complete.mean()) if n else 0.0,
        "incomplete_rows": int((~complete).sum()),
        "incomplete_reason_counts": dict(sorted(reasons.items())),
    }
    return matrix, complete, audit, provenance_rows


def _subset_probs(probs: dict[str, np.ndarray], mask: np.ndarray) -> dict[str, np.ndarray]:
    return {name: np.asarray(value)[mask] for name, value in probs.items()}


def _compose(
    df: pd.DataFrame,
    probs: dict[str, np.ndarray],
    market: np.ndarray,
    family: str,
    core: list[str],
    fnames: list[str],
    jnames: list[str],
) -> tuple[np.ndarray, bool]:
    g = meta_features(probs)
    if market.shape != (len(df), 10) or not np.isfinite(market).all():
        raise ResearchError(f"INVALID_COMPLETE_MARKET_MATRIX:{market.shape}")
    impute = False
    if family == "relative_geometry39":
        x = g
    elif family == "relative_geometry_market49":
        x = np.concatenate([g, market], axis=1)
    elif family == "relative_geometry_core_market96":
        x = np.concatenate([g, _raw_block(df, core, "CORE47"), market], axis=1)
        impute = True
    elif family == "relative_all_market132":
        x = np.concatenate(
            [g, _raw_block(df, core, "CORE47"), _raw_block(df, fnames, "R42F18"), _raw_block(df, jnames, "R42J18"), market],
            axis=1,
        )
        impute = True
    else:
        raise ResearchError(f"UNKNOWN_R3_FAMILY:{family}")
    expected = {
        "relative_geometry39": 39,
        "relative_geometry_market49": 49,
        "relative_geometry_core_market96": 96,
        "relative_all_market132": 132,
    }[family]
    if x.shape != (len(df), expected) or np.isinf(x).any():
        raise ResearchError(f"INVALID_R3_ROUTER_MATRIX:{family}:{x.shape}")
    return x, impute


def _write_summary(out_dir: Path, result: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out_dir / "summary.json").write_text(text, encoding="utf-8")
    (out_dir / "summary.sha256").write_text(
        hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii"
    )


def run(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    parent_cfg = load_json(DEFAULT_PARENT_CONFIG)
    parent = load_json(DEFAULT_PARENT_STATUS)
    cohort = pd.read_csv(DEFAULT_PARENT_COHORT)
    if len(cohort) != 1000 or parent["cohort"]["identity_sha256"] != "d5c14a3deb65150db4181716db38f767dfffe05efea7279677c66dd4303ff404":
        raise ResearchError("PARENT_1000_DIAGNOSTIC_IDENTITY_MISMATCH")
    excluded_ids = set(cohort.identity_key.astype(str))

    frame, base_cfg, fcfg, _jcfg, fnames, jnames, common_ok, _seasons = build_frame()
    core = select_core_features(frame)
    ecfg = cfg["expert_contract"]
    if len(core) != int(ecfg["core_feature_count"]) or len(fnames) != int(ecfg["r42f_feature_count"]) or len(jnames) != int(ecfg["r42j_feature_count"]):
        raise ResearchError("R3_EXPERT_FEATURE_COUNT_MISMATCH")

    r40f, r40_audit = replay_r40f(parent_cfg)
    if not bool(r40_audit["frozen_summary_reproduced"]):
        raise ResearchError("R40F_FROZEN_SUMMARY_NOT_REPRODUCED")
    r40_keys = set(r40f.identity_key.astype(str))
    target_all = frame[(frame.split == "target_pool") & common_ok].copy()
    target_all = target_all[target_all.identity_key.astype(str).isin(r40_keys)].copy()
    evaluation = target_all[~target_all.identity_key.astype(str).isin(excluded_ids)].sort_values("identity_key").copy()
    if len(evaluation) != EXPECTED_ROWS or _identity_sha(evaluation.identity_key) != EXPECTED_ID_SHA:
        raise ResearchError("R3_PRIMARY_EVALUATION_IDENTITY_MISMATCH")

    train = frame[common_ok & (frame.split == "train")].copy()
    policy = frame[common_ok & (frame.split == "policy")].copy()
    fit_target = frame[common_ok & frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit_target)) == 0:
        raise ResearchError("R3_EMPTY_CHRONOLOGICAL_SPLIT")

    ledger_path = ROOT / str(fcfg["input_ledger"])
    if not ledger_path.is_file():
        raise ResearchError(f"R3_MARKET_LEDGER_MISSING:{ledger_path}")
    ledger = pd.read_csv(ledger_path)
    ledger_map = _ledger_source_map(ledger)
    market_cfg = cfg["market_contract"]
    market_policy, policy_complete, policy_audit, policy_prov = _market_matrix(policy, ledger_map, market_cfg)
    market_eval, eval_complete, eval_audit, eval_prov = _market_matrix(evaluation, ledger_map, market_cfg)

    coverage_cfg = cfg["coverage_gate"]
    coverage_checks = {
        "policy_rate": bool(policy_audit["complete_rate"] >= float(coverage_cfg["minimum_complete_market_rate_policy"])),
        "evaluation_rate": bool(eval_audit["complete_rate"] >= float(coverage_cfg["minimum_complete_market_rate_evaluation"])),
        "policy_rows": bool(policy_audit["complete_rows"] >= int(coverage_cfg["minimum_complete_market_rows_policy"])),
        "evaluation_rows": bool(eval_audit["complete_rows"] >= int(coverage_cfg["minimum_complete_market_rows_evaluation"])),
    }
    coverage_pass = bool(all(coverage_checks.values()))
    coverage_result = {
        "policy": policy_audit,
        "evaluation": eval_audit,
        "checks": coverage_checks,
        "passed": coverage_pass,
        "selection_uses_outcomes": False,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(policy_prov).to_csv(out_dir / "policy_market_coverage.csv", index=False)
    pd.DataFrame(eval_prov).to_csv(out_dir / "evaluation_market_coverage.csv", index=False)

    boundary = {
        "retrospective_viewed_only": True,
        "market_evidence_not_strict_pit": True,
        "scientific_pass_claim_allowed": False,
        "confirmation_pass_claim_allowed": False,
        "formal_promotion_allowed": False,
        "future_oos_label_open_authorized": False,
        "b01_b04_reused_as_confirmatory": False,
        "b05_b07_labels_opened": False,
        "new_target_label_access": 0,
        "new_data_collection": 0,
        "provider_requests": 0,
        "paid_api_requests": 0,
        "formal_weight": 0,
        "formal_model_mutation": False,
        "formal_data_mutation": False,
        "formal_config_mutation": False,
        "current_mutation": False,
        "main_mutation": False,
    }

    if not coverage_pass:
        result = {
            "schema_version": cfg["schema_version"],
            "status": "STOP_MARKET10_COVERAGE_GATE_BEFORE_ROUTING_METRICS",
            "scientific_verdict": "MARKET_STATE_ROUTING_R3_NOT_EVALUATED_COVERAGE_INSUFFICIENT",
            "source_contract": {
                "parent_pr": 204,
                "parent_head": cfg["source_contract"]["parent_head"],
                "parent_rows": int(len(evaluation)),
                "parent_identity_sha256": _identity_sha(evaluation.identity_key),
                "new_target_label_access": 0,
                "b05_b07_labels_opened": False,
            },
            "market_coverage": coverage_result,
            "routing_metrics_computed": False,
            "boundary": boundary,
        }
        _write_summary(out_dir, result)
        return result

    p_policy_all = fit_experts(train, policy, core, fnames, jnames, float(ecfg["fixed_C"]), base_cfg)
    p_eval_all = fit_experts(fit_target, evaluation, core, fnames, jnames, float(ecfg["fixed_C"]), base_cfg)
    y_eval_all = evaluation.total_class.to_numpy(int)
    eval_losses_all = {n: _true_loss(y_eval_all, p_eval_all[n]) for n in EXPERTS}
    full_common_ll = float(np.mean(eval_losses_all["common_baseline"]))
    full_oracle_ll = float(np.mean(np.min(np.column_stack([eval_losses_all[n] for n in EXPERTS]), axis=1)))
    if abs(full_common_ll - EXPECTED_COMMON_LL) > 1e-12 or abs(full_oracle_ll - EXPECTED_ORACLE_LL) > 1e-12:
        raise ResearchError(f"R3_PARENT_REPRODUCTION_MISMATCH:{full_common_ll}:{full_oracle_ll}")

    policy_pos = np.flatnonzero(policy_complete)
    eval_pos = np.flatnonzero(eval_complete)
    policy_sub = policy.iloc[policy_pos].copy()
    eval_sub = evaluation.iloc[eval_pos].copy()
    p_policy = _subset_probs(p_policy_all, policy_complete)
    p_eval = _subset_probs(p_eval_all, eval_complete)
    market_policy_sub = market_policy[policy_complete]
    market_eval_sub = market_eval[eval_complete]

    y_policy = policy_sub.total_class.to_numpy(int)
    policy_losses = {n: _true_loss(y_policy, p_policy[n]) for n in EXPERTS}
    policy_ll = {n: float(np.mean(policy_losses[n])) for n in EXPERTS}
    best_static_name = min(EXPERTS, key=lambda name: policy_ll[name])

    y = eval_sub.total_class.to_numpy(int)
    eval_losses = {n: _true_loss(y, p_eval[n]) for n in EXPERTS}
    loss_matrix = np.column_stack([eval_losses[n] for n in EXPERTS])
    oracle_loss = np.min(loss_matrix, axis=1)
    common_p = p_eval["common_baseline"]
    static_p = p_eval[best_static_name]
    equal_p = sum(p_eval[n] for n in EXPERTS) / float(len(EXPERTS))
    common_metrics = metrics(y, common_p)
    static_metrics = metrics(y, static_p)
    equal_metrics = metrics(y, equal_p)
    expert_metrics = {n: metrics(y, p_eval[n]) for n in EXPERTS}
    oracle_ll = float(np.mean(oracle_loss))
    full_gap = float(common_metrics["logloss"] - oracle_ll)
    subset_sha = _identity_sha(eval_sub.identity_key)

    feature_names = list(market_cfg["feature_names"])
    per_match = eval_sub[["identity_key", "competition_id", "season", "date_key", "home_team", "away_team", "total_class"]].copy()
    for idx, name in enumerate(feature_names):
        per_match[name] = market_eval_sub[:, idx]
    for n in EXPERTS:
        per_match[f"loss_{n}"] = eval_losses[n]
    per_match["row_oracle_expert"] = [EXPERTS[i] for i in np.argmin(loss_matrix, axis=1)]
    per_match["row_oracle_loss"] = oracle_loss

    families = [
        "relative_geometry39",
        "relative_geometry_market49",
        "relative_geometry_core_market96",
        "relative_all_market132",
    ]
    alpha = float(cfg["router_contract"]["ridge_alpha"])
    report = cfg["reporting_contract"]
    resamples = int(report["paired_bootstrap_resamples"])
    seed = int(report["bootstrap_seed"])
    family_results: dict[str, Any] = {}

    for k, family in enumerate(families):
        x_policy, impute = _compose(policy_sub, p_policy, market_policy_sub, family, core, fnames, jnames)
        x_eval, impute_eval = _compose(eval_sub, p_eval, market_eval_sub, family, core, fnames, jnames)
        if impute != impute_eval:
            raise ResearchError("R3_IMPUTE_CONTRACT_MISMATCH")
        choices, predicted = _fit_relative(x_policy, policy_losses, x_eval, alpha, impute_missing=impute)
        p_sel = _selected_probabilities(p_eval, choices)
        m = metrics(y, p_sel)
        boot_common = bootstrap_delta(y, common_p, p_sel, resamples, seed + k)
        boot_static = bootstrap_delta(y, static_p, p_sel, resamples, seed + 100 + k)
        counts = {n: int(np.sum(choices == i)) for i, n in enumerate(EXPERTS)}
        selected_n = int(sum(value > 0 for value in counts.values()))
        gain_common = float(common_metrics["logloss"] - m["logloss"])
        captured = float(gain_common / full_gap) if full_gap > 1e-15 else None
        signal = (
            gain_common >= float(report["development_signal_threshold_logloss_gain_vs_common"])
            and boot_common["logloss"]["p95"] < 0.0
            and m["brier"] <= common_metrics["brier"]
            and m["rps"] <= common_metrics["rps"]
            and selected_n >= 2
        )
        family_results[family] = {
            "feature_count": int(x_eval.shape[1]),
            "historical_missing_imputation": bool(impute),
            "metrics": m,
            "choice_counts": counts,
            "selected_expert_count": selected_n,
            "delta_vs_common": {
                "logloss": float(m["logloss"] - common_metrics["logloss"]),
                "brier": float(m["brier"] - common_metrics["brier"]),
                "rps": float(m["rps"] - common_metrics["rps"]),
                "top1_accuracy_pp": float(100.0 * (m["top1_accuracy"] - common_metrics["top1_accuracy"])),
            },
            "delta_vs_policy_best_static": {
                "static_name": best_static_name,
                "logloss": float(m["logloss"] - static_metrics["logloss"]),
                "brier": float(m["brier"] - static_metrics["brier"]),
                "rps": float(m["rps"] - static_metrics["rps"]),
                "top1_accuracy_pp": float(100.0 * (m["top1_accuracy"] - static_metrics["top1_accuracy"])),
            },
            "bootstrap_vs_common": boot_common,
            "bootstrap_vs_policy_best_static": boot_static,
            "fraction_of_common_to_row_oracle_gap_captured": captured,
            "relative_loss_prediction_diagnostics": _relative_diagnostics(predicted, eval_losses),
            "development_signal_for_future_oos_design": bool(signal),
            "development_signal_is_scientific_pass": False,
        }
        per_match[f"choice_{family}"] = [EXPERTS[i] for i in choices]
        per_match[f"pred_delta_R42F_{family}"] = predicted[:, 1]
        per_match[f"pred_delta_R42J_{family}"] = predicted[:, 2]

    primary = family_results["relative_geometry_market49"]
    verdict = (
        "POSTVIEW_MARKET_STATE_ROUTING_SIGNAL_DESCRIPTIVE_ONLY"
        if primary["development_signal_for_future_oos_design"]
        else "POSTVIEW_MARKET_STATE_ROUTING_SIGNAL_NOT_ESTABLISHED"
    )
    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_DIRECT_T_MARKET_STATE_ROUTING_R3_COMPLETE_NO_PROMOTION",
        "scientific_verdict": verdict,
        "source_contract": {
            "parent_pr": 204,
            "parent_head": cfg["source_contract"]["parent_head"],
            "parent_rows": int(len(evaluation)),
            "parent_identity_sha256": _identity_sha(evaluation.identity_key),
            "market_complete_evaluation_rows": int(len(eval_sub)),
            "market_complete_evaluation_identity_sha256": subset_sha,
            "new_target_label_access": 0,
            "b05_b07_labels_opened": False,
        },
        "market_evidence_ruling": {
            "evidence_class": market_cfg["evidence_class"],
            "formal_pre_match_snapshot": False,
            "reason": "existing processed market references have no original quote timestamp; mechanism diagnosis only",
        },
        "market_coverage": coverage_result,
        "parent_reproduction": {
            "full_1584_common_baseline_logloss": full_common_ll,
            "full_1584_row_oracle_logloss": full_oracle_ll,
            "full_1584_row_oracle_gap": float(full_common_ll - full_oracle_ll),
        },
        "market_complete_subset": {
            "rows": int(len(eval_sub)),
            "identity_sha256": subset_sha,
            "common_baseline_logloss": float(common_metrics["logloss"]),
            "row_oracle_logloss": oracle_ll,
            "common_to_row_oracle_gap": full_gap,
            "policy_selected_best_static": best_static_name,
            "policy_logloss": policy_ll,
        },
        "comparators": {
            "common_baseline": common_metrics,
            "policy_best_static": static_metrics,
            "equal_weight_average": equal_metrics,
            "experts": expert_metrics,
        },
        "router_families": family_results,
        "primary_mechanism_family": "relative_geometry_market49",
        "routing_metrics_computed": True,
        "boundary": boundary,
    }
    per_match.to_csv(out_dir / "per_match.csv", index=False)
    _write_summary(out_dir, result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(load_json(CONFIG), OUT_DIR), ensure_ascii=False, indent=2))
