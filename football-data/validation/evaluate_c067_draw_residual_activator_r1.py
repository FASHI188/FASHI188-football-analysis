#!/usr/bin/env python3
"""C067 post-view Draw residual activator.

This is deliberately NOT a scientific or confirmation test.  R6 labels were already
viewed before this mechanism was selected.  The purpose is to establish whether a
reproducible, time-safe development mechanism can create *natural* Draw Top-1 calls
while preserving proper-score quality.

The R6 HDA probabilities are treated as the baseline.  A binary Draw-vs-nonDraw
residual model is fit only on strictly earlier viewed R6 OOS rows.  On eligible rows,
its qDraw is blended into R6 pDraw.  The H:A odds ratio is preserved exactly; no
forced Draw label, Top-1 override, 1-1 reward, or manual probability bonus is used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from evaluate_direct_t_gd_joint_fixed200_r1 import LABELS
from evaluate_direct_t_parity_gd_fixed500_r1 import hda_metrics
from evaluate_market6_gd0_integration_r6 import (
    OUT_DIR as R6_OUT_DIR,
    _cluster_bootstrap,
    _metric_delta,
    run as run_r6,
)
from v510_historical_structure_features_r1 import ResearchError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "c067_draw_residual_activator_r1.json"
OUT_DIR = ROOT / "manifests" / "c067_draw_residual_activator_r1"
EPS = 1e-9


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError(f"C067 JSON root must be object: {path}")
    return value


def _logit(values: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(values, dtype=float), EPS, 1.0 - EPS)
    return np.log(x / (1.0 - x))


def _feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ph = frame["candidate_p_home"].to_numpy(float)
    pd_ = frame["candidate_p_draw"].to_numpy(float)
    pa = frame["candidate_p_away"].to_numpy(float)
    bh = frame["baseline_p_home"].to_numpy(float)
    bd = frame["baseline_p_draw"].to_numpy(float)
    ba = frame["baseline_p_away"].to_numpy(float)
    out = pd.DataFrame(
        {
            "cand_logit_draw": _logit(pd_),
            "cand_balance": np.abs(ph - pa),
            "cand_margin": np.maximum(ph, pa) - pd_,
            "cand_max_ha": np.maximum(ph, pa),
            "cand_min_ha": np.minimum(ph, pa),
            "base_logit_draw": _logit(bd),
            "base_balance": np.abs(bh - ba),
            "delta_draw": pd_ - bd,
            "delta_home": ph - bh,
            "delta_away": pa - ba,
            "candidate_p0_T2": frame["candidate_p0_T2"].to_numpy(float),
            "candidate_p0_T4": frame["candidate_p0_T4"].to_numpy(float),
            "candidate_p0_T6": frame["candidate_p0_T6"].to_numpy(float),
            "delta_p0_T2": frame["candidate_p0_T2"].to_numpy(float) - frame["parent_p0_T2"].to_numpy(float),
            "delta_p0_T4": frame["candidate_p0_T4"].to_numpy(float) - frame["parent_p0_T4"].to_numpy(float),
            "delta_p0_T6": frame["candidate_p0_T6"].to_numpy(float) - frame["parent_p0_T6"].to_numpy(float),
            "market6_complete": frame["market6_complete"].astype(int).to_numpy(),
        }
    )
    if not np.isfinite(out.to_numpy(float)).all():
        raise ResearchError("C067 non-finite activation features")
    return out


def _fit_model(train: pd.DataFrame, cfg: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    if len(train) < 1000:
        raise ResearchError(f"C067 insufficient prior training rows: {len(train)}")
    feature_names = list(cfg["activation_model"]["features"])
    x = _feature_frame(train)
    if list(x.columns) != feature_names:
        raise ResearchError("C067 feature contract mismatch")
    y = (train["actual_result"].astype(str) == "D").astype(int).to_numpy()
    if len(np.unique(y)) != 2:
        raise ResearchError("C067 training target lacks both classes")
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=float(cfg["activation_model"]["C"]),
            class_weight=None,
            max_iter=int(cfg["activation_model"]["max_iter"]),
            random_state=67067,
        ),
    )
    model.fit(x, y)
    return model, {
        "rows": int(len(train)),
        "draw_rows": int(y.sum()),
        "non_draw_rows": int(len(y) - y.sum()),
        "min_date": str(train["date_key"].min()),
        "max_date": str(train["date_key"].max()),
        "feature_count": int(x.shape[1]),
        "C": float(cfg["activation_model"]["C"]),
        "class_weight": None,
    }


def _compose(test: pd.DataFrame, qdraw: np.ndarray, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = test.copy()
    p_h = out["candidate_p_home"].to_numpy(float)
    p_d = out["candidate_p_draw"].to_numpy(float)
    p_a = out["candidate_p_away"].to_numpy(float)
    q = np.clip(np.asarray(qdraw, dtype=float), EPS, 1.0 - EPS)
    if len(q) != len(out):
        raise ResearchError("C067 qDraw row mismatch")

    eligibility = cfg["activation_model"]["eligibility"]
    balance = np.abs(p_h - p_a)
    residual_gain = q - p_d
    eligible = (
        (p_d >= float(eligibility["minimum_r6_pdraw"]))
        & (balance <= float(eligibility["maximum_abs_home_away_probability_gap"]))
        & (residual_gain >= float(eligibility["minimum_residual_qdraw_minus_r6_pdraw"]))
    )
    weight = float(cfg["activation_model"]["blend_weight"])
    new_d = p_d.copy()
    new_d[eligible] = (1.0 - weight) * p_d[eligible] + weight * q[eligible]
    new_d = np.clip(new_d, EPS, 1.0 - EPS)

    ha_mass = p_h + p_a
    if np.any(ha_mass <= 0.0):
        raise ResearchError("C067 invalid R6 H+A mass")
    home_share = p_h / ha_mass
    new_h = (1.0 - new_d) * home_share
    new_a = (1.0 - new_d) * (1.0 - home_share)
    probs = np.column_stack([new_h, new_d, new_a])
    probability_residual = float(np.max(np.abs(probs.sum(axis=1) - 1.0)))
    if probability_residual > 1e-12 or np.any(probs <= 0.0):
        raise ResearchError(f"C067 probability conservation failure: {probability_residual}")

    old_ratio = p_h / p_a
    new_ratio = new_h / new_a
    ratio_residual = float(np.max(np.abs(np.log(old_ratio) - np.log(new_ratio))))
    if ratio_residual > 1e-10:
        raise ResearchError(f"C067 H:A odds-ratio preservation failure: {ratio_residual}")

    out["c067_q_draw"] = q
    out["c067_eligible"] = eligible
    out["c067_p_home"] = new_h
    out["c067_p_draw"] = new_d
    out["c067_p_away"] = new_a
    out["c067_pred_result"] = np.asarray(LABELS, dtype=object)[np.argmax(probs, axis=1)]
    return out, {
        "rows": int(len(out)),
        "eligible_rows": int(eligible.sum()),
        "eligible_rate": float(eligible.mean()),
        "mean_qdraw": float(q.mean()),
        "mean_r6_pdraw": float(p_d.mean()),
        "mean_new_pdraw": float(new_d.mean()),
        "mean_residual_gain": float(residual_gain.mean()),
        "mean_residual_gain_eligible": float(residual_gain[eligible].mean()) if eligible.any() else None,
        "probability_sum_max_residual": probability_residual,
        "home_away_log_odds_ratio_max_residual": ratio_residual,
    }


def _clean_metrics(value: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    out = dict(value)
    ll = np.asarray(out.pop("_ll_rows"), dtype=float)
    return out, ll


def _evaluate_fold(rows: pd.DataFrame, fold_name: str, prior: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    test = rows[rows["fold"] == fold_name].copy().sort_values(["date_key", "competition_id", "season", "home_team", "away_team"]).reset_index(drop=True)
    if len(test) == 0:
        raise ResearchError(f"C067 empty evaluation fold: {fold_name}")
    cutoff = str(test["date_key"].min())
    train = prior[prior["date_key"].astype(str) < cutoff].copy().reset_index(drop=True)
    if len(train) == 0 or str(train["date_key"].max()) >= cutoff:
        raise ResearchError(f"C067 global chronology failure for {fold_name}")

    model, fit_receipt = _fit_model(train, cfg)
    qdraw = model.predict_proba(_feature_frame(test))[:, 1]
    scored, compose_receipt = _compose(test, qdraw, cfg)
    baseline, baseline_ll = _clean_metrics(hda_metrics(scored, "candidate"))
    candidate, candidate_ll = _clean_metrics(hda_metrics(scored, "c067"))
    fit_receipt.update(
        {
            "evaluation_fold": fold_name,
            "evaluation_cutoff_min_date": cutoff,
            "evaluation_rows": int(len(test)),
            "global_time_safe": bool(str(train["date_key"].max()) < cutoff),
            "evaluation_fold_labels_used_in_fit": False,
        }
    )
    return scored, {
        "baseline_hda": baseline,
        "c067_hda": candidate,
        "delta_c067_minus_r6": _metric_delta(candidate, baseline),
        "fit_receipt": fit_receipt,
        "composition_receipt": compose_receipt,
        "_baseline_ll": baseline_ll,
        "_candidate_ll": candidate_ll,
    }


def _serializable(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if not k.startswith("_")}


def run() -> dict[str, Any]:
    cfg = load_json(CONFIG)
    # Rebuild R6 exactly on the same already-viewed historical ledger.
    r6_summary = run_r6()
    r6_rows_path = R6_OUT_DIR / "rows.csv"
    if not r6_rows_path.is_file():
        raise ResearchError("C067 missing rebuilt R6 rows")
    rows = pd.read_csv(r6_rows_path)
    if len(rows) != int(cfg["source_contract"]["r6_rows"]):
        raise ResearchError(f"C067 R6 row count mismatch: {len(rows)}")
    required = {
        "position_2", "position_3", "position_4"
    }
    if set(rows["fold"].astype(str).unique()) != required:
        raise ResearchError("C067 R6 fold identity mismatch")

    # Evaluation 1: position_3; training can use only earlier position_2 rows.
    prior3 = rows[rows["fold"] == "position_2"].copy()
    scored3, eval3 = _evaluate_fold(rows, "position_3", prior3, cfg)

    # Evaluation 2: position_4; expanding training may use position_2/3 rows only if
    # their dates are strictly earlier than the earliest position_4 evaluation date.
    prior4 = rows[rows["fold"].isin(["position_2", "position_3"])].copy()
    scored4, eval4 = _evaluate_fold(rows, "position_4", prior4, cfg)

    scored = pd.concat([scored3, scored4], ignore_index=True)
    baseline, baseline_ll = _clean_metrics(hda_metrics(scored, "candidate"))
    candidate, candidate_ll = _clean_metrics(hda_metrics(scored, "c067"))
    boot = _cluster_bootstrap(
        scored,
        baseline_ll,
        candidate_ll,
        int(cfg["reporting_contract"]["bootstrap_resamples"]),
        int(cfg["reporting_contract"]["bootstrap_seed"]),
    )
    delta = _metric_delta(candidate, baseline)

    pchecks = {
        "pooled_logloss_improves": bool(candidate["log_loss"] < baseline["log_loss"]),
        "cluster_bootstrap_logloss_p95_lt_zero": bool(boot["p95"] < 0.0),
        "pooled_brier_nonworse": bool(candidate["brier"] <= baseline["brier"]),
        "pooled_rps_nonworse": bool(candidate["rps"] <= baseline["rps"]),
    }
    draw_cfg = cfg["reporting_contract"]["descriptive_draw_activation_checks"]
    dchecks = {
        "minimum_additional_natural_top1_draw_calls": bool(
            int(candidate["predicted_counts"]["D"] - baseline["predicted_counts"]["D"])
            >= int(draw_cfg["minimum_additional_natural_top1_draw_calls"])
        ),
        "minimum_additional_draw_hits": bool(
            int(candidate["draw_hits"] - baseline["draw_hits"])
            >= int(draw_cfg["minimum_additional_draw_hits"])
        ),
        "minimum_draw_f1_gain": bool(
            float(candidate["draw_f1"] - baseline["draw_f1"])
            >= float(draw_cfg["minimum_draw_f1_gain"])
        ),
    }
    descriptive_signal = bool(all(pchecks.values()) and all(dchecks.values()))

    result = {
        "schema_version": cfg["schema_version"],
        "status": "POSTVIEW_C067_DRAW_RESIDUAL_ACTIVATOR_COMPLETE_NO_PROMOTION",
        "descriptive_verdict": (
            "POSTVIEW_C067_NATURAL_DRAW_TOP1_MECHANISM_REPRODUCED"
            if descriptive_signal
            else "POSTVIEW_C067_DRAW_TOP1_MECHANISM_NOT_ESTABLISHED"
        ),
        "source_contract": cfg["source_contract"],
        "development_origin": cfg["development_origin"],
        "activation_model": cfg["activation_model"],
        "time_contract": cfg["time_contract"],
        "r6_rebuild_status": r6_summary["status"],
        "evaluations": {
            "position_3": _serializable(eval3),
            "position_4": _serializable(eval4),
        },
        "pooled_position_3_4": {
            "rows": int(len(scored)),
            "baseline_r6_hda": baseline,
            "c067_hda": candidate,
            "delta_c067_minus_r6": delta,
            "cluster_bootstrap_hda_logloss": boot,
        },
        "descriptive_development_signal": {
            "probability_checks": pchecks,
            "draw_activation_checks": dchecks,
            "passed": descriptive_signal,
            "scientific_pass": False,
            "confirmation_pass": False,
            "formal_promotion": False,
        },
        "boundary": {
            "viewed_r6_rows_reused": int(len(rows)),
            "new_unseen_label_rows": 0,
            "post_view_parameter_search": True,
            "natural_argmax_draw_only": True,
            "forced_draw": False,
            "manual_top1_override": False,
            "manual_pdraw_bonus": False,
            "home_away_odds_ratio_preserved": True,
            "score_matrix_changed": False,
            "independent_hda_challenger_only": True,
            "market_evidence_not_strict_pit": True,
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
    scored.to_csv(OUT_DIR / "rows.csv", index=False)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (OUT_DIR / "summary.json").write_text(text, encoding="utf-8")
    (OUT_DIR / "summary.sha256").write_text(
        hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="ascii"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
