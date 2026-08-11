#!/usr/bin/env python3
"""R42I: pre-match two-half generative challenger for Direct-T.

The target match's actual half-time score is never used to make its prediction. The
challenger learns, only from historical train/policy rows with audited HT/FT fields:
  1) P(FH_total | pre-match core X)
  2) P(SH_total | hypothetical FH_total, pre-match core X)
At prediction time it evaluates every possible FH_total state, integrates it out, and
convolves FH+SH to P(full-time T=0..6,7+).

This is research-only and compared once on a fresh fixed200 after reproducing/excluding
all 2,800 previously consumed exploratory identities.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
    split_for_latest_complete,
)
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import paired_bootstrap
from evaluate_r42f_htft_response_direct_total_fixed200 import load_ht_rows
from evaluate_r42g_discipline_referee_direct_total_fixed200 import reproduce_prior2600, tail_binary
from evaluate_r42h_team_red_foul_direct_total_fixed200 import (
    load_team_red_foul_rows,
    build_team_red_foul_features,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    make_model,
    metric_components,
    metric_summary,
    select_C,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42i_two_half_generative_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42i_two_half_generative_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))
HALF_ONEHOT = [f"fh_state_{i}" for i in TOTAL_CLASSES]


def reproduce_prior2800(raw: pd.DataFrame, seasons: dict[str, list[str]], base_features: pd.DataFrame, cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    """Reproduce R42H's exact 200 identities without reading their labels for selection."""
    r42h_cfg = load_json(ROOT / "config" / "r42h_team_red_foul_direct_total_fixed200.json")
    excluded2600, hashes = reproduce_prior2600(raw, seasons, r42h_cfg)
    if len(excluded2600) != 2600:
        raise ResearchError(f"expected prior2600, got {len(excluded2600)}")

    f = base_features.copy()
    f["split_r42h"] = split_for_latest_complete(f, seasons, r42h_cfg)
    f["date_norm"] = pd.to_datetime(f["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(f.competition_id.astype(str))
    drows, _ = load_team_red_foul_rows(competitions)
    discipline, _ = build_team_red_foul_features(drows, r42h_cfg)
    names = [str(x) for x in r42h_cfg["feature_contract"]["feature_names"]]
    keep = ["competition_id", "season", "date_norm", "home_team", "away_team", "discipline_team_history_ok"] + names
    m = f.merge(discipline[keep], on=["competition_id", "season", "date_norm", "home_team", "away_team"], how="left", validate="one_to_one")
    target = m[
        (m.split_r42h == "target_pool")
        & m[names].notna().all(axis=1)
        & (m.discipline_team_history_ok.fillna(0).astype(int) == 1)
    ].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2600)].copy()
    ids, sha = select_fixed_identities(fresh, int(r42h_cfg["sample_contract"]["sample_size"]), int(r42h_cfg["sample_contract"]["seed"]))
    expected = str(cfg["sample_contract"]["exclude_R42H_identity_sha256"])
    if sha != expected:
        raise ResearchError(f"R42H identity mismatch {sha} != {expected}")
    excluded2800 = excluded2600 | set(ids)
    if len(excluded2800) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42I"]):
        raise ResearchError(f"expected prior2800 identities, got {len(excluded2800)}")
    out = dict(hashes)
    out["R42H"] = sha
    return excluded2800, out


def build_half_label_frame(ht_rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    invalid_negative_second_half = 0
    valid = 0
    for r in ht_rows:
        if not bool(r.get("htft_observed")):
            continue
        hthg, htag = int(r["hthg"]), int(r["htag"])
        fthg, ftag = int(r["fthg"]), int(r["ftag"])
        fh = hthg + htag
        sh = (fthg + ftag) - fh
        if min(hthg, htag, fthg, ftag, fh, sh) < 0:
            invalid_negative_second_half += 1
            continue
        valid += 1
        rows.append({
            "competition_id": str(r["competition_id"]),
            "season": str(r["season"]),
            "date_norm": str(r["date_norm"]),
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "fh_total_class": int(min(fh, 7)),
            "sh_total_class": int(min(sh, 7)),
            "half_labels_observed": 1,
        })
    frame = pd.DataFrame(rows)
    if not frame.empty:
        keys = ["competition_id", "season", "date_norm", "home_team", "away_team"]
        if frame.duplicated(keys).any():
            raise ResearchError("duplicate R42I HT/FT label identity")
    return frame, {"valid_half_label_rows": int(valid), "invalid_negative_second_half_rows": int(invalid_negative_second_half)}


def add_fh_onehot(frame: pd.DataFrame, source_col: str = "fh_total_class") -> pd.DataFrame:
    out = frame.copy()
    vals = out[source_col].to_numpy(int)
    for i, name in enumerate(HALF_ONEHOT):
        out[name] = (vals == i).astype(float)
    return out


def conditional_design(sample: pd.DataFrame, core: list[str], fh_state: int) -> pd.DataFrame:
    x = sample[core].copy()
    for i, name in enumerate(HALF_ONEHOT):
        x[name] = float(i == fh_state)
    return x


def combine_half_probabilities(p_fh: np.ndarray, conditional_sh: list[np.ndarray]) -> np.ndarray:
    if p_fh.ndim != 2 or p_fh.shape[1] != 8 or len(conditional_sh) != 8:
        raise ResearchError("invalid R42I half-probability shapes")
    n = p_fh.shape[0]
    out = np.zeros((n, 8), dtype=float)
    for f in TOTAL_CLASSES:
        ps = conditional_sh[f]
        if ps.shape != (n, 8):
            raise ResearchError("invalid R42I conditional SH probability shape")
        for s in TOTAL_CLASSES:
            total_class = 7 if (f == 7 or s == 7 or f + s >= 7) else f + s
            out[:, total_class] += p_fh[:, f] * ps[:, s]
    residual = float(np.max(np.abs(out.sum(axis=1) - 1.0)))
    if residual > 1e-10 or np.any(out < -1e-15):
        raise ResearchError(f"R42I convolution probability failure residual={residual}")
    return out


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    features["date_norm"] = pd.to_datetime(features["date_key"], errors="raise").dt.date.astype(str)
    excluded2800, prior_hashes = reproduce_prior2800(raw, seasons, features, cfg)

    competitions = set(features.competition_id.astype(str))
    ht_rows, source_cov = load_ht_rows(competitions)
    half_labels, half_audit = build_half_label_frame(ht_rows)
    if half_labels.empty:
        raise ResearchError("no valid R42I half labels")
    merged = features.merge(
        half_labels,
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )

    historical = merged[merged.split.isin(["train", "policy"]) & (merged.half_labels_observed.fillna(0).astype(int) == 1)].copy()
    fit_counts = {str(k): int(v) for k, v in historical.groupby("competition_id").size().sort_index().items()}
    min_fit = int(cfg["coverage_gate"]["minimum_train_policy_ht_rows_per_competition"])
    supported = sorted([k for k, v in fit_counts.items() if v >= min_fit])

    target = merged[(merged.split == "target_pool") & merged.competition_id.astype(str).isin(set(supported))].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2800)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior2800_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded2800)), "hashes": prior_hashes},
        "coverage": {
            "raw_htft_source_by_competition": source_cov,
            "half_label_audit": half_audit,
            "train_policy_half_label_rows_by_competition": fit_counts,
            "minimum_train_policy_ht_rows_per_competition": min_fit,
            "supported_competitions": supported,
            "fresh_target_rows_after_prior2800_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_total_labels_used_for_identity_selection": False,
            "target_actual_half_time_used_for_identity_selection": False,
            "target_actual_half_time_used_for_prediction": False,
            "supported_competitions_selected_from_train_policy_half_coverage_only": True,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {
            **base_receipt,
            "status": "STOP_R42I_TWO_HALF_COVERAGE_LT200",
            "scientific_verdict": "DO_NOT_CONSUME_FIXED200_TWO_HALF_COVERAGE_INSUFFICIENT",
            "sample": None,
            "model_fits": 0,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2800:
        raise ResearchError("R42I sample identity contract failed")

    fit_rows = historical[historical.competition_id.astype(str).isin(set(supported))].copy()
    fit_rows = add_fh_onehot(fit_rows)
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty R42I fit split")

    core = select_core_features(merged)
    allowed = [float(x) for x in cfg["fit_contract"]["C_grid"]]

    baseline_C, baseline_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    fh_C, fh_grid = select_C(train, policy, core, "fh_total_class", TOTAL_CLASSES, base_cfg)
    sh_features = core + HALF_ONEHOT
    sh_C, sh_grid = select_C(train, policy, sh_features, "sh_total_class", TOTAL_CLASSES, base_cfg)
    for label, value in [("baseline", baseline_C), ("first_half", fh_C), ("second_half", sh_C)]:
        if float(value) not in allowed:
            raise ResearchError(f"R42I {label} policy selected C outside preregistered grid: {value}")

    baseline = make_model(float(baseline_C), base_cfg)
    fh_model = make_model(float(fh_C), base_cfg)
    sh_model = make_model(float(sh_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    fh_model.fit(fit_rows[core], fit_rows.fh_total_class)
    sh_model.fit(fit_rows[sh_features], fit_rows.sh_total_class)

    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_fh = align_probability(fh_model, sample[core], TOTAL_CLASSES)
    conditional_sh = [align_probability(sh_model, conditional_design(sample, core, f), TOTAL_CLASSES) for f in TOTAL_CLASSES]
    p_gen = combine_half_probabilities(p_fh, conditional_sh)

    # Target labels are first accessed only after the pre-match probability matrices exist.
    y = sample.total_class.to_numpy(int)
    base_comp = metric_components(y, p_base, TOTAL_CLASSES)
    gen_comp = metric_components(y, p_gen, TOTAL_CLASSES)
    base_metrics = metric_summary(base_comp)
    gen_metrics = metric_summary(gen_comp)
    boot = paired_bootstrap(base_comp, gen_comp, cfg)
    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0),
        "brier_nonworse": bool(gen_metrics["brier"] <= base_metrics["brier"]),
        "rps_nonworse": bool(gen_metrics["rps"] <= base_metrics["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {
            "rows": int(draw_mask.sum()),
            "baseline_total_logloss": float(base_comp.loc[draw_mask, "logloss"].mean()),
            "challenger_total_logloss": float(gen_comp.loc[draw_mask, "logloss"].mean()),
            "delta": float(gen_comp.loc[draw_mask, "logloss"].mean() - base_comp.loc[draw_mask, "logloss"].mean()),
        }

    result = {
        **base_receipt,
        "status": "PASS_R42I_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42I_TWO_HALF_GENERATIVE_DIRECT_TOTAL_FIXED200" if gate["all_required"] else "FAIL_R42I_TWO_HALF_GENERATIVE_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": 200,
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_2800": 0,
            "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()),
            "labels_used_for_identity_selection": False,
            "target_actual_half_time_used_for_prediction": False,
            "blind_claim": False,
        },
        "model_contract": {
            "fit_rows": int(len(fit_rows)),
            "train_rows": int(len(train)),
            "policy_rows": int(len(policy)),
            "baseline_policy_selected_C": float(baseline_C),
            "first_half_policy_selected_C": float(fh_C),
            "second_half_policy_selected_C": float(sh_C),
            "baseline_policy_grid": baseline_grid,
            "first_half_policy_grid": fh_grid,
            "second_half_policy_grid": sh_grid,
            "baseline_feature_count": int(len(core)),
            "first_half_feature_count": int(len(core)),
            "second_half_feature_count": int(len(sh_features)),
            "scientific_parameters_selected_on_fixed200": 0,
            "target_actual_half_time_used_for_prediction": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "first_half_max_solver_iterations": int(np.max(fh_model.named_steps["model"].n_iter_)),
            "second_half_max_solver_iterations": int(np.max(sh_model.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "first_half_probability_sum_max_residual": float(np.max(np.abs(p_fh.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_gen.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": base_metrics,
            "challenger": gen_metrics,
            "delta_challenger_minus_baseline": {k: float(gen_metrics[k] - base_metrics[k]) for k in base_metrics},
            "paired_bootstrap": boot,
            "tail_T_ge_4": {"baseline": tail_binary(y, p_base), "challenger": tail_binary(y, p_gen)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "interpretation_limits": [
            "The target match's actual half-time score is never used to make its pre-match prediction.",
            "The challenger is a research decomposition of scoring time, not a formal CURRENT Direct-T replacement.",
            "A PASS would authorize one disjoint fixed200 replication only; it would not authorize promotion.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    p_fh = np.zeros((2, 8), dtype=float)
    p_fh[:, 1] = 1.0
    cond: list[np.ndarray] = []
    for f in TOTAL_CLASSES:
        p = np.zeros((2, 8), dtype=float)
        p[:, 2] = 1.0
        cond.append(p)
    out = combine_half_probabilities(p_fh, cond)
    assert np.allclose(out[:, 3], 1.0)
    assert np.allclose(out.sum(axis=1), 1.0)
    p_fh2 = np.zeros((1, 8), dtype=float); p_fh2[:, 7] = 1.0
    cond2 = [np.eye(8, dtype=float)[[0]] for _ in TOTAL_CLASSES]
    out2 = combine_half_probabilities(p_fh2, cond2)
    assert abs(out2[0, 7] - 1.0) < 1e-12
    print(json.dumps({"status": "PASS_R42I_SELF_TEST", "target_actual_half_time_used_for_prediction": 0, "probability_conservation": True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out)
    print(json.dumps({"status": result["status"], "scientific_verdict": result["scientific_verdict"], "coverage": result["coverage"], "sample": result.get("sample"), "model_contract": result.get("model_contract"), "metrics": result.get("metrics")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
