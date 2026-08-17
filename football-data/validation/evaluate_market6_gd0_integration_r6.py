#!/usr/bin/env python3
"""R6 downstream integration of the prespecified R5 market6-only GD0 signal.

Direct-T is unchanged. Parent P(GD|T,X) is unchanged except that, for T=2,4,6,
its GD=0 mass is replaced by a fixed market6-only binary prediction. All nonzero-GD
probabilities retain their parent relative proportions. Market-incomplete rows fall back
to the parent distribution unchanged.

This is retrospective post-view research. Existing market references lack original quote
timestamps, so no strict-PIT or scientific/confirmation PASS may be claimed. B05+ labels
are never accessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_direct_t_gd_joint_fixed200_r1 import (
    KEYS,
    LABELS,
    assemble_joint,
    conditional_probabilities,
    direct_total_probabilities,
    load_config,
)
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    hda_metrics,
    score_metrics,
)
from evaluate_even_t_gd0_market_diagnostic_r5 import (
    MARKET6,
    _binary_probability,
    build_market6,
    load_json,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "market6_gd0_integration_r6.json"
OUT_DIR = ROOT / "manifests" / "market6_gd0_integration_r6"
ADJUSTED_TOTALS = [2, 4, 6]


def _add_joint(frame: pd.DataFrame, prefix: str, joint: list[dict[str, Any]]) -> None:
    values = pd.DataFrame(joint)
    if len(values) != len(frame):
        raise ResearchError(f"R6 joint row mismatch for {prefix}: {len(values)} != {len(frame)}")
    for column in values.columns:
        frame[f"{prefix}_{column}"] = values[column].to_numpy()


def _attach_scores(frame: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    right = raw[KEYS + ["home_goals_90", "away_goals_90"]].copy()
    left = frame.copy()
    left["season"] = left["season"].astype(str)
    right["season"] = right["season"].astype(str)
    out = left.merge(right, on=KEYS, how="left", validate="one_to_one")
    if out[["home_goals_90", "away_goals_90"]].isna().any().any():
        raise ResearchError("R6 score join failure")
    out["actual_total"] = out.home_goals_90.astype(int) + out.away_goals_90.astype(int)
    out["actual_score"] = out.home_goals_90.astype(int).astype(str) + ":" + out.away_goals_90.astype(int).astype(str)
    gd = out.home_goals_90.astype(int) - out.away_goals_90.astype(int)
    out["actual_result"] = np.where(gd > 0, "H", np.where(gd == 0, "D", "A"))
    return out


def _adjust_conditional(
    parent: dict[int, tuple[list[int], np.ndarray]],
    market_predictions: dict[int, np.ndarray],
    market_complete: np.ndarray,
) -> tuple[dict[int, tuple[list[int], np.ndarray]], dict[str, Any], dict[int, np.ndarray], dict[int, np.ndarray]]:
    complete = np.asarray(market_complete, dtype=bool)
    adjusted: dict[int, tuple[list[int], np.ndarray]] = {}
    diagnostics: dict[str, Any] = {}
    parent_p0: dict[int, np.ndarray] = {}
    candidate_p0: dict[int, np.ndarray] = {}

    for total, (classes, probability) in parent.items():
        base = np.asarray(probability, dtype=float)
        new = base.copy()
        if total in ADJUSTED_TOTALS:
            if 0 not in classes:
                raise ResearchError(f"R6 GD0 missing from support T={total}")
            zero = classes.index(0)
            p0 = np.clip(base[:, zero], 1e-12, 1.0 - 1e-12)
            q0 = p0.copy()
            predicted = np.asarray(market_predictions[total], dtype=float)
            if len(predicted) != int(complete.sum()):
                raise ResearchError(f"R6 market prediction count mismatch T={total}")
            q0[complete] = np.clip(predicted, 1e-12, 1.0 - 1e-12)
            scale = (1.0 - q0) / np.clip(1.0 - p0, 1e-12, None)
            for j in range(len(classes)):
                if j != zero:
                    new[:, j] *= scale
            new[:, zero] = q0
            parent_p0[total] = p0
            candidate_p0[total] = q0
            diagnostics[str(total)] = {
                "market_complete_rows": int(complete.sum()),
                "parent_mean_p0_market_complete": float(np.mean(p0[complete])),
                "candidate_mean_p0_market_complete": float(np.mean(q0[complete])),
                "mean_delta_p0_market_complete": float(np.mean(q0[complete] - p0[complete])),
                "mean_absolute_delta_p0_market_complete": float(np.mean(np.abs(q0[complete] - p0[complete]))),
                "fallback_rows": int((~complete).sum()),
                "fallback_exact_parent": True,
            }
        if np.any(new < -1e-12) or np.max(np.abs(new.sum(axis=1) - 1.0)) > 1e-10:
            raise ResearchError(f"R6 adjusted conditional probability invalid T={total}")
        adjusted[total] = (classes, new)
    return adjusted, diagnostics, parent_p0, candidate_p0


def _clean_hda(value: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    out = dict(value)
    ll = np.asarray(out.pop("_ll_rows"), dtype=float)
    return out, ll


def _metric_delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    return {
        "log_loss": float(candidate["log_loss"] - baseline["log_loss"]),
        "brier": float(candidate["brier"] - baseline["brier"]),
        "rps": float(candidate["rps"] - baseline["rps"]),
        "accuracy_pp": float(100.0 * (candidate["accuracy"] - baseline["accuracy"])),
        "macro_f1_pp": float(100.0 * (candidate["macro_f1"] - baseline["macro_f1"])),
        "draw_f1_pp": float(100.0 * (candidate["draw_f1"] - baseline["draw_f1"])),
        "draw_calls": int(candidate["predicted_counts"]["D"] - baseline["predicted_counts"]["D"]),
        "draw_hits": int(candidate["draw_hits"] - baseline["draw_hits"]),
    }


def _cluster_bootstrap(
    frame: pd.DataFrame,
    baseline_ll: np.ndarray,
    candidate_ll: np.ndarray,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    if len(frame) != len(baseline_ll) or len(frame) != len(candidate_ll) or len(frame) == 0:
        raise ResearchError("R6 cluster bootstrap row mismatch")
    delta = np.asarray(candidate_ll, dtype=float) - np.asarray(baseline_ll, dtype=float)
    keys = frame[["fold", "competition_id", "season"]].astype(str).agg("|".join, axis=1).to_numpy()
    groups = sorted(set(keys))
    sums = np.asarray([float(delta[keys == group].sum()) for group in groups], dtype=float)
    counts = np.asarray([int(np.sum(keys == group)) for group in groups], dtype=float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(groups), size=(samples, len(groups)))
    values = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return {
        "clusters": int(len(groups)),
        "rows": int(len(frame)),
        "observed_mean_delta_candidate_minus_baseline": float(delta.mean()),
        "bootstrap_mean": float(values.mean()),
        "p05": float(np.quantile(values, 0.05)),
        "median": float(np.quantile(values, 0.50)),
        "p95": float(np.quantile(values, 0.95)),
        "probability_candidate_better": float(np.mean(values < 0.0)),
    }


def _evaluate(frame: pd.DataFrame, mask: np.ndarray | None = None) -> dict[str, Any]:
    part = frame if mask is None else frame.loc[np.asarray(mask, dtype=bool)].copy()
    if len(part) == 0:
        raise ResearchError("R6 evaluation subset empty")
    baseline_hda, baseline_ll = _clean_hda(hda_metrics(part, "baseline"))
    candidate_hda, candidate_ll = _clean_hda(hda_metrics(part, "candidate"))
    return {
        "rows": int(len(part)),
        "baseline_hda": baseline_hda,
        "candidate_hda": candidate_hda,
        "delta_candidate_minus_baseline": _metric_delta(candidate_hda, baseline_hda),
        "baseline_score": score_metrics(part, "baseline"),
        "candidate_score": score_metrics(part, "candidate"),
        "_baseline_ll": baseline_ll,
        "_candidate_ll": candidate_ll,
        "_frame": part,
    }


def _serializable_eval(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if not k.startswith("_")}


def run() -> dict[str, Any]:
    cfg = load_json(CONFIG)
    base_cfg = load_config()
    ledger = ROOT / str(base_cfg["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"R6 runtime ledger missing: {ledger}")
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, base_cfg)
    if int(data_identity["rows"]) != int(cfg["source_contract"]["expected_historical_rows"]):
        raise ResearchError("R6 historical row count mismatch")

    base = build_features(raw)
    core = select_core_features(base)
    if len(core) != 47:
        raise ResearchError(f"R6 core feature count changed: {len(core)}")
    base["season"] = base["season"].astype(str)
    market, market_audit = build_market6(raw, load_json(ROOT / "config" / "even_t_gd0_market_diagnostic_r5.json")["market_contract"])
    frame = base.merge(market, on=KEYS, how="left", validate="one_to_one")
    if len(frame) != len(base) or frame.market6_complete.isna().any():
        raise ResearchError("R6 market merge mismatch")
    frame["market6_complete"] = frame.market6_complete.astype(bool)
    frame = attach_exact_total(frame, raw)

    seasons, excluded = complete_seasons(raw, base_cfg)
    positions = [int(x) for x in cfg["source_contract"]["rolling_test_positions_zero_based"]]
    fixed_C = float(cfg["market_contract"]["fixed_C"])
    all_rows: list[pd.DataFrame] = []
    fold_results: dict[str, Any] = {}
    fit_receipts: dict[str, Any] = {}
    gd0_diagnostics: dict[str, Any] = {}

    for position in positions:
        fold = frame.copy()
        fold["split"] = assign_fold(fold, seasons, position)
        fold_name = f"position_{position}"
        test = fold[fold.split == "test"].copy()
        if len(test) == 0:
            raise ResearchError(f"R6 empty test fold {fold_name}")
        complete = test.market6_complete.to_numpy(bool)
        if int(complete.sum()) == 0:
            raise ResearchError(f"R6 no market-complete rows {fold_name}")

        p_total, _, dt_receipt = direct_total_probabilities(fold, test, core, base_cfg)
        parent_cond, _, cond_receipt = conditional_probabilities(fold, test, core, base_cfg)

        market_predictions: dict[int, np.ndarray] = {}
        per_total_receipt: dict[str, Any] = {}
        test_complete = test.loc[complete].copy()
        for total in ADJUSTED_TOTALS:
            fit_market = fold[
                fold.split.isin(["train", "policy"])
                & (fold.total_class.astype(int) == total)
                & fold.market6_complete
            ].copy()
            probability, receipt = _binary_probability(
                fit_market, test_complete, MARKET6, base_cfg, fixed_C
            )
            market_predictions[total] = probability
            per_total_receipt[str(total)] = receipt

        candidate_cond, fold_gd0, parent_p0, candidate_p0 = _adjust_conditional(
            parent_cond, market_predictions, complete
        )
        baseline_joint = assemble_joint(p_total, parent_cond)
        candidate_joint = assemble_joint(p_total, candidate_cond)

        rows = _attach_scores(
            test[KEYS + ["competition_id", "season", "date_key", "home_team", "away_team", "market6_complete"]].copy(),
            raw,
        )
        rows["fold"] = fold_name
        _add_joint(rows, "baseline", baseline_joint)
        _add_joint(rows, "candidate", candidate_joint)
        for total in ADJUSTED_TOTALS:
            rows[f"parent_p0_T{total}"] = parent_p0[total]
            rows[f"candidate_p0_T{total}"] = candidate_p0[total]
        all_rows.append(rows)

        fold_primary = _evaluate(rows, rows.market6_complete.to_numpy(bool))
        fold_all = _evaluate(rows)
        fold_results[fold_name] = {
            "market_complete": _serializable_eval(fold_primary),
            "all_row_parent_fallback": _serializable_eval(fold_all),
            "market_complete_rows": int(complete.sum()),
            "test_rows": int(len(test)),
            "market_complete_rate": float(complete.mean()),
        }
        fit_receipts[fold_name] = {
            "direct_total": dt_receipt,
            "parent_conditional_gd": cond_receipt,
            "market6_gd0_heads": per_total_receipt,
        }
        gd0_diagnostics[fold_name] = fold_gd0

    rows = pd.concat(all_rows, ignore_index=True)
    if rows[["fold"] + KEYS].duplicated().any():
        raise ResearchError("R6 duplicate rolling evaluation identities")

    market_mask = rows.market6_complete.to_numpy(bool)
    primary = _evaluate(rows, market_mask)
    secondary = _evaluate(rows)
    report = cfg["reporting_contract"]
    boot_n = int(report["bootstrap_resamples"])
    seed = int(report["bootstrap_seed"])
    primary_bootstrap = _cluster_bootstrap(
        primary["_frame"], primary["_baseline_ll"], primary["_candidate_ll"], boot_n, seed
    )
    secondary_bootstrap = _cluster_bootstrap(
        secondary["_frame"], secondary["_baseline_ll"], secondary["_candidate_ll"], boot_n, seed + 1
    )

    base_hda = primary["baseline_hda"]
    cand_hda = primary["candidate_hda"]
    probability_cfg = report["probability_signal_gate"]
    draw_cfg = report["draw_activation_gate"]
    probability_checks = {
        "minimum_hda_logloss_gain": bool(
            float(base_hda["log_loss"] - cand_hda["log_loss"])
            >= float(probability_cfg["minimum_hda_logloss_gain"])
        ),
        "cluster_bootstrap_logloss_p95_lt_zero": bool(primary_bootstrap["p95"] < 0.0),
        "brier_nonworse": bool(cand_hda["brier"] <= base_hda["brier"]),
        "rps_nonworse": bool(cand_hda["rps"] <= base_hda["rps"]),
    }
    draw_checks = {
        "minimum_draw_f1_gain": bool(
            float(cand_hda["draw_f1"] - base_hda["draw_f1"])
            >= float(draw_cfg["minimum_draw_f1_gain"])
        ),
        "minimum_additional_natural_top1_draw_calls": bool(
            int(cand_hda["predicted_counts"]["D"] - base_hda["predicted_counts"]["D"])
            >= int(draw_cfg["minimum_additional_natural_top1_draw_calls"])
        ),
        "minimum_draw_hits_gain": bool(
            int(cand_hda["draw_hits"] - base_hda["draw_hits"])
            >= int(draw_cfg["minimum_draw_hits_gain"])
        ),
    }
    probability_signal = bool(all(probability_checks.values()))
    draw_activation = bool(all(draw_checks.values()))
    development_signal = bool(probability_signal and draw_activation)

    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_MARKET6_GD0_INTEGRATION_R6_COMPLETE_NO_PROMOTION",
        "scientific_verdict": (
            "POSTVIEW_MARKET6_GD0_DOWNSTREAM_SIGNAL_DESCRIPTIVE_ONLY"
            if development_signal else "POSTVIEW_MARKET6_GD0_DOWNSTREAM_SIGNAL_NOT_ESTABLISHED"
        ),
        "source_contract": cfg["source_contract"],
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "market_evidence": {
            "evidence_class": cfg["market_contract"]["evidence_class"],
            "formal_pre_match_snapshot": False,
            "market6_audit": market_audit,
        },
        "integration_contract": cfg["integration_contract"],
        "coverage": {
            "all_test_rows": int(len(rows)),
            "market_complete_rows": int(market_mask.sum()),
            "market_complete_rate": float(market_mask.mean()),
        },
        "primary_market_complete": _serializable_eval(primary),
        "secondary_all_row_parent_fallback": _serializable_eval(secondary),
        "per_fold": fold_results,
        "market_head_fit_receipts": fit_receipts,
        "gd0_probability_shift": gd0_diagnostics,
        "cluster_bootstrap": {
            "primary_market_complete_hda_logloss": primary_bootstrap,
            "secondary_all_row_hda_logloss": secondary_bootstrap,
        },
        "development_signal": {
            "probability_signal_passed": probability_signal,
            "probability_checks": probability_checks,
            "draw_activation_passed": draw_activation,
            "draw_checks": draw_checks,
            "passed": development_signal,
            "scientific_pass": False,
            "future_oos_label_open_authorized": False,
        },
        "boundary": {
            "retrospective_viewed_rolling_data": True,
            "market_evidence_not_strict_pit": True,
            "direct_total_changed": False,
            "parent_nonzero_gd_relative_shape_changed": False,
            "adjusted_total_classes": ADJUSTED_TOTALS,
            "calibration_layer": False,
            "blend_weight": 1.0,
            "forced_draw": False,
            "manual_hda_threshold": False,
            "post_result_parameter_search": False,
            "new_target_label_access": 0,
            "b05_b07_labels_opened": False,
            "future_oos_label_open_authorized": False,
            "formal_weight": 0,
            "provider_requests": 0,
            "paid_api_requests": 0,
            "new_data_collection": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows.to_csv(OUT_DIR / "rows.csv", index=False)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (OUT_DIR / "summary.json").write_text(text, encoding="utf-8")
    (OUT_DIR / "summary.sha256").write_text(
        hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
