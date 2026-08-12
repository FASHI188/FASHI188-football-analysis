#!/usr/bin/env python3
"""VIEWED common-cohort forensic replay and ex-post oracle diagnostic.

This script does NOT fit a fusion selector, does NOT consume a protected fixed sample,
and does NOT claim scientific/confirmation/formal PASS. It rebuilds already-viewed
historical signals on one deterministic common cohort so their per-match errors can be
compared on identical identities.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
    split_for_latest_complete,
)
from evaluate_r41_priority_fixed200_battery import (
    HDA_CLASSES,
    draw_metrics,
    fit_model,
    fit_sets,
    materialize_market,
    prepare_features,
)
from evaluate_r42d_mutual_draw_utility_fixed200 import build_counterfactual_features
from evaluate_r42f_htft_response_direct_total_fixed200 import build_htft_features, load_ht_rows
from evaluate_r42j_all_history_pair_recovery_direct_total_fixed200 import (
    add_recovered_all_pair_features,
    recovered_feature_names,
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
REPO_ROOT = ROOT.parent
DEFAULT_CONFIG = ROOT / "config" / "viewed_common_cohort_oracle_r1.json"
DEFAULT_OUT_DIR = ROOT / "manifests" / "viewed_common_cohort_oracle_r1"
TOTAL_CLASSES = list(range(8))
LABELS = ("H", "D", "A")
ENC = {"A": -1.0, "D": 0.0, "H": 1.0}


# -------------------------- frozen source identity --------------------------

def git_blob(path: str) -> str:
    return subprocess.check_output(["git", "rev-parse", f"HEAD:{path}"], text=True).strip()


def verify_inherited_source_blobs(cfg: dict[str, Any]) -> dict[str, str]:
    checks = {
        "r42d_evaluator_blob": "football-data/validation/evaluate_r42d_mutual_draw_utility_fixed200.py",
        "r42f_evaluator_blob": "football-data/validation/evaluate_r42f_htft_response_direct_total_fixed200.py",
        "r42j_evaluator_blob": "football-data/validation/evaluate_r42j_all_history_pair_recovery_direct_total_fixed200.py",
    }
    out: dict[str, str] = {}
    for field, path in checks.items():
        got = git_blob(path)
        expected = str(cfg["source_contract"][field])
        if got != expected:
            raise ResearchError(f"SOURCE_BLOB_DRIFT:{field}:{got}:{expected}")
        out[field] = got
    return out


# -------------------------- exact R40F replay core --------------------------

@dataclass(frozen=True)
class CentralityRow:
    competition: str
    season: str
    dt: date
    home: str
    away: str
    label: str
    x: tuple[float, ...]

    @property
    def key(self) -> str:
        return f"{self.competition}|{self.season}|{self.dt.isoformat()}|{self.home}|{self.away}"


def _num(v: Any) -> float:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


def _safe_rate(total: float, n: float) -> float:
    if not (math.isfinite(total) and math.isfinite(n)) or n <= 0:
        return math.nan
    return total / n


def _r40_feature_vector(r: dict[str, str]) -> tuple[float, ...]:
    hh = _num(r.get("home_history_matches", "")); ah = _num(r.get("away_history_matches", ""))
    hgf = _num(r.get("home_history_gf", "")); agf = _num(r.get("away_history_gf", ""))
    hga = _num(r.get("home_history_ga", "")); aga = _num(r.get("away_history_ga", ""))
    hppg = _num(r.get("home_history_ppg", "")); appg = _num(r.get("away_history_ppg", ""))
    h5ppg = _num(r.get("home_last5_ppg", "")); a5ppg = _num(r.get("away_last5_ppg", ""))
    h5gf = _num(r.get("home_last5_gf", "")); a5gf = _num(r.get("away_last5_gf", ""))
    h5ga = _num(r.get("home_last5_ga", "")); a5ga = _num(r.get("away_last5_ga", ""))
    elo = _num(r.get("elo_difference_with_home_advantage", ""))
    return (
        elo,
        h5ppg - a5ppg,
        h5gf - a5gf,
        h5ga - a5ga,
        h5gf + a5gf,
        h5ga + a5ga,
        hppg - appg,
        _safe_rate(hgf, hh) - _safe_rate(agf, ah),
        _safe_rate(hga, hh) - _safe_rate(aga, ah),
    )


def _finite_vec(x: Iterable[float]) -> bool:
    return all(math.isfinite(float(v)) for v in x)


def _quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        raise ResearchError("empty R40F quantile")
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return float(s[lo])
    w = pos - lo
    return float(s[lo] * (1.0 - w) + s[hi] * w)


def _solve_linear(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            M[pivot][col] += 1e-8
        if pivot != col:
            M[col], M[pivot] = M[pivot], M[col]
        div = M[col][col]
        if abs(div) < 1e-15:
            raise ResearchError("R40F singular ridge system")
        for j in range(col, n + 1):
            M[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            f = M[r][col]
            if f == 0.0:
                continue
            for j in range(col, n + 1):
                M[r][j] -= f * M[col][j]
    return [M[i][n] for i in range(n)]


def _ridge_fit(X: list[list[float]], y: list[float], l2: float) -> list[float]:
    if not X or len(X) != len(y):
        raise ResearchError("invalid R40F ridge input")
    d = len(X[0])
    A = [[0.0] * d for _ in range(d)]; b = [0.0] * d
    for row, yy in zip(X, y):
        for j in range(d):
            b[j] += row[j] * yy
            for k in range(j, d):
                A[j][k] += row[j] * row[k]
    for j in range(d):
        for k in range(j):
            A[j][k] = A[k][j]
        if j > 0:
            A[j][j] += l2
        A[j][j] += 1e-9
    return _solve_linear(A, b)


def _side_features(x: tuple[float, ...]) -> tuple[float, ...]:
    return (x[0], x[1], x[2], x[3], x[6], x[7], x[8])


def _load_r40_rows() -> list[CentralityRow]:
    rows: list[CentralityRow] = []
    files = sorted(REPO_ROOT.glob("football-data/training_datasets/*/point_in_time.csv"))
    if not files:
        raise ResearchError("NO_EXISTING_POINT_IN_TIME_DATASETS_FOR_R40F_REPLAY")
    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                label = str(raw.get("label_result", "")).strip().upper()
                if label not in LABELS:
                    continue
                try:
                    dt = date.fromisoformat(str(raw.get("date", "")).strip())
                except Exception:
                    continue
                comp = str(raw.get("competition_id", "")).strip()
                season = str(raw.get("season", "")).strip()
                home = str(raw.get("home_team", "")).strip()
                away = str(raw.get("away_team", "")).strip()
                if not all((comp, season, home, away)):
                    continue
                rows.append(CentralityRow(comp, season, dt, home, away, label, _r40_feature_vector(raw)))
    keys = [r.key for r in rows]
    if len(keys) != len(set(keys)):
        raise ResearchError("DUPLICATE_R40F_MATCH_KEYS")
    return rows


def replay_r40f(cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = [r for r in _load_r40_rows() if _finite_vec(r.x)]
    by_comp: dict[str, list[CentralityRow]] = defaultdict(list)
    for r in rows:
        by_comp[r.competition].append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))

    min_train = int(cfg["r40f_replay_contract"]["minimum_train_rows"])
    min_test = int(cfg["r40f_replay_contract"]["minimum_test_rows"])
    l2 = float(cfg["r40f_replay_contract"]["ridge_l2"])
    records: list[dict[str, Any]] = []
    fold_count = 0
    for comp, comp_rows in sorted(by_comp.items()):
        seasons: dict[str, list[CentralityRow]] = defaultdict(list)
        for r in comp_rows:
            seasons[r.season].append(r)
        season_groups = sorted(seasons.items(), key=lambda kv: (min(r.dt for r in kv[1]), kv[0]))
        for season, test_rows0 in season_groups:
            test_rows = sorted(test_rows0, key=lambda r: (r.dt, r.key))
            test_start = min(r.dt for r in test_rows)
            train_rows = [r for r in comp_rows if r.dt < test_start]
            if len(train_rows) < min_train or len(test_rows) < min_test:
                continue
            fold_count += 1
            train_raw = [_side_features(r.x) for r in train_rows]
            d = len(train_raw[0]); med: list[float] = []; scale: list[float] = []
            for j in range(d):
                vals = [x[j] for x in train_raw]
                m = _quantile(vals, 0.5)
                s = _quantile(vals, 0.75) - _quantile(vals, 0.25)
                if not math.isfinite(s) or s < 1e-9:
                    s = 1.0
                med.append(m); scale.append(s)
            Xtrain = [[1.0] + [(x[j] - med[j]) / scale[j] for j in range(d)] for x in train_raw]
            beta = _ridge_fit(Xtrain, [ENC[r.label] for r in train_rows], l2)
            train_scores = [sum(b * v for b, v in zip(beta, x)) for x in Xtrain]
            train_draw_rate = sum(r.label == "D" for r in train_rows) / len(train_rows)
            q_low = (1.0 - train_draw_rate) / 2.0; q_high = (1.0 + train_draw_rate) / 2.0
            lower = _quantile(train_scores, q_low); upper = _quantile(train_scores, q_high)
            cuts = [_quantile(train_scores, q / 10.0) for q in range(1, 10)]
            for r in test_rows:
                raw = _side_features(r.x)
                xt = [1.0] + [(raw[j] - med[j]) / scale[j] for j in range(d)]
                score = float(sum(b * v for b, v in zip(beta, xt)))
                decile = int(sum(score > cut for cut in cuts))
                # bisect_right semantics for exact ties:
                decile = int(np.searchsorted(np.asarray(cuts, dtype=float), score, side="right"))
                records.append({
                    "identity_key": r.key,
                    "r40f_actual": r.label,
                    "r40f_latent_score": score,
                    "r40f_decile": decile,
                    "r40f_central": bool(lower <= score <= upper),
                })
    frame = pd.DataFrame(records)
    if frame.empty or frame.identity_key.duplicated().any():
        raise ResearchError("R40F replay identity build failed")
    central = frame[frame.r40f_central]
    outer = frame[~frame.r40f_central]
    central_draws = int((central.r40f_actual == "D").sum()); outer_draws = int((outer.r40f_actual == "D").sum())
    lift = float(central_draws / len(central) - outer_draws / len(outer))
    audit = {
        "rows_scored": int(len(frame)),
        "fold_count": int(fold_count),
        "central_rows": int(len(central)),
        "central_draws": central_draws,
        "outer_rows": int(len(outer)),
        "outer_draws": outer_draws,
        "central_draw_lift": lift,
        "source_head": cfg["source_contract"]["r40f_source_head"],
        "source_evaluator_blob": cfg["source_contract"]["r40f_evaluator_blob"],
        "r39w_source_blob": cfg["source_contract"]["r39w_source_blob"],
        "r39u_source_blob": cfg["source_contract"]["r39u_source_blob"],
    }
    expected = cfg["r40f_replay_contract"]
    exact_fields = {
        "rows_scored": "expected_rows_scored",
        "central_rows": "expected_central_rows",
        "central_draws": "expected_central_draws",
        "outer_rows": "expected_outer_rows",
        "outer_draws": "expected_outer_draws",
    }
    for got_field, exp_field in exact_fields.items():
        if int(audit[got_field]) != int(expected[exp_field]):
            raise ResearchError(f"R40F_REPLAY_MISMATCH:{got_field}:{audit[got_field]}:{expected[exp_field]}")
    if abs(lift - float(expected["expected_central_draw_lift"])) > float(expected["lift_tolerance"]):
        raise ResearchError(f"R40F_REPLAY_LIFT_MISMATCH:{lift}:{expected['expected_central_draw_lift']}")
    audit["frozen_summary_reproduced"] = True
    return frame, audit


# ------------------------------- diagnostics -------------------------------

def _safe_corr(a: np.ndarray, b: np.ndarray) -> float | None:
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    if int(mask.sum()) < 3:
        return None
    aa = a[mask]; bb = b[mask]
    if float(np.std(aa)) < 1e-15 or float(np.std(bb)) < 1e-15:
        return None
    return float(np.corrcoef(aa, bb)[0, 1])


def _true_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    return -np.log(np.clip(p[np.arange(len(y)), y.astype(int)], 1e-15, 1.0))


def _hda_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    comp = metric_components(y.astype(int), p, HDA_CLASSES)
    out = metric_summary(comp)
    out["accuracy"] = float((np.argmax(p, axis=1) == y).mean())
    out["draw"] = draw_metrics(y.astype(int), p)
    out["probability_sum_max_residual"] = float(np.max(np.abs(p.sum(axis=1) - 1.0)))
    return out


def _direct_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    comp = metric_components(y.astype(int), p, TOTAL_CLASSES)
    out = metric_summary(comp)
    out["top1_accuracy"] = float((np.argmax(p, axis=1) == y).mean())
    out["probability_sum_max_residual"] = float(np.max(np.abs(p.sum(axis=1) - 1.0)))
    return out


def _fit_direct_pair(
    frame: pd.DataFrame,
    eligible: pd.Series,
    sample: pd.DataFrame,
    challenger_names: list[str],
    cfg: dict[str, Any],
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    fit_rows = frame[eligible & frame.split.isin(["train", "policy"])].copy()
    train = fit_rows[fit_rows.split == "train"].copy(); policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty direct fit split")
    core = select_core_features(frame)
    C, grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(C) not in allowed:
        raise ResearchError(f"selected C outside frozen grid: {C}")
    base = make_model(float(C), base_cfg); ch = make_model(float(C), base_cfg)
    base.fit(fit_rows[core], fit_rows.total_class)
    ch.fit(fit_rows[core + challenger_names], fit_rows.total_class)
    p_base = align_probability(base, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(ch, sample[core + challenger_names], TOTAL_CLASSES)
    return p_base, p_ch, {
        "selected_C": float(C),
        "policy_grid": grid,
        "fit_rows": int(len(fit_rows)),
        "baseline_features": int(len(core)),
        "challenger_features": int(len(core + challenger_names)),
    }


def _oracle_summary(y: np.ndarray, models: dict[str, np.ndarray], top1_key: str) -> dict[str, Any]:
    names = list(models)
    losses = np.vstack([_true_loss(y, models[n]) for n in names])
    top_hits = np.vstack([(np.argmax(models[n], axis=1) == y) for n in names])
    mean_ll = {n: float(losses[i].mean()) for i, n in enumerate(names)}
    mean_acc = {n: float(top_hits[i].mean()) for i, n in enumerate(names)}
    best_single_ll_name = min(names, key=lambda n: mean_ll[n])
    best_single_acc_name = max(names, key=lambda n: mean_acc[n])
    oracle_ll = float(np.min(losses, axis=0).mean())
    oracle_acc = float(np.any(top_hits, axis=0).mean())
    return {
        "models": names,
        "single_model_logloss": mean_ll,
        "single_model_top1_accuracy": mean_acc,
        "best_single_logloss_model": best_single_ll_name,
        "best_single_logloss": mean_ll[best_single_ll_name],
        "oracle_logloss_lower_bound": oracle_ll,
        "oracle_logloss_gap_vs_best_single": float(mean_ll[best_single_ll_name] - oracle_ll),
        "best_single_top1_model": best_single_acc_name,
        "best_single_top1_accuracy": mean_acc[best_single_acc_name],
        top1_key: oracle_acc,
        "oracle_top1_gap_vs_best_single": float(oracle_acc - mean_acc[best_single_acc_name]),
        "oracle_is_ex_post_only": True,
        "learnability_status": "NOT_TESTED",
    }


def run(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    source_blobs = verify_inherited_source_blobs(cfg)
    r42d_cfg = load_json(ROOT / "config" / "r42d_mutual_draw_utility_fixed200.json")
    r42f_cfg = load_json(ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json")
    r42j_cfg = load_json(ROOT / "config" / "r42j_all_history_pair_recovery_direct_total_fixed200.json")
    base_cfg = load_json(ROOT / str(r42d_cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(r42d_cfg["input_ledger"]))
    data_identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    # HDA / R42D path.
    market = materialize_market(raw, r42d_cfg["market_contract"])
    hda = prepare_features(raw, market, seasons, r42d_cfg)
    util_names = [str(x) for x in r42d_cfg["method_contract"]["counterfactual_features"]]
    utility, utility_audit = build_counterfactual_features(raw, util_names)
    hda = hda.merge(utility, on="identity_key", how="left", validate="one_to_one")
    hda_eligible = (hda.book_count.fillna(0).astype(int) >= 1) & hda[util_names].notna().all(axis=1)

    # Direct-T / R42F / R42J path.
    direct = add_identity_key(build_features(raw))
    direct["split"] = split_for_latest_complete(direct, seasons, r42f_cfg)
    direct["date_norm"] = pd.to_datetime(direct["date_key"], errors="raise").dt.date.astype(str)
    ht_rows, ht_source_cov = load_ht_rows(set(direct.competition_id.astype(str)))
    htft, ht_audit = build_htft_features(ht_rows, r42f_cfg)
    direct = direct.merge(
        htft,
        on=["competition_id", "season", "date_norm", "home_team", "away_team"],
        how="left",
        validate="one_to_one",
    )
    f_names = [str(x) for x in r42f_cfg["feature_contract"]["feature_names"]]
    min_trials = float(r42f_cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    f_eligible = (
        direct[f_names].notna().all(axis=1)
        & (direct.home_state_trials_total.fillna(0) >= min_trials)
        & (direct.away_state_trials_total.fillna(0) >= min_trials)
    )
    direct = add_recovered_all_pair_features(direct, r42j_cfg)
    j_names = recovered_feature_names(r42j_cfg)
    j_eligible = direct[j_names].notna().all(axis=1)

    # Exact R40F structural replay and old-summary audit.
    r40f, r40f_audit = replay_r40f(cfg)
    r40f_keys = set(r40f.identity_key.astype(str))

    hda_target = set(hda.loc[(hda.split == "target_pool") & hda_eligible, "identity_key"].astype(str))
    direct_target = set(direct.loc[(direct.split == "target_pool") & f_eligible & j_eligible, "identity_key"].astype(str))
    common = sorted(hda_target & direct_target & r40f_keys)
    minimum = int(cfg["cohort_contract"]["minimum_common_target_pool_rows"])
    if len(common) < minimum:
        raise ResearchError(f"COMMON_COHORT_COVERAGE_LT_MINIMUM:{len(common)}:{minimum}")

    pool = direct[direct.identity_key.astype(str).isin(common)].copy()
    selected, cohort_sha = select_fixed_identities(
        pool,
        int(cfg["cohort_contract"]["sample_size"]),
        int(cfg["cohort_contract"]["identity_hash_seed"]),
    )
    ids = set(selected)
    sample_direct = direct[direct.identity_key.astype(str).isin(ids)].sort_values("identity_key").copy()
    sample_hda = hda[hda.identity_key.astype(str).isin(ids)].sort_values("identity_key").copy()
    sample_r40 = r40f[r40f.identity_key.astype(str).isin(ids)].sort_values("identity_key").copy()
    if not (len(sample_direct) == len(sample_hda) == len(sample_r40) == int(cfg["cohort_contract"]["sample_size"])):
        raise ResearchError("COMMON_COHORT_SAMPLE_ALIGNMENT_FAILED")
    if list(sample_direct.identity_key.astype(str)) != list(sample_hda.identity_key.astype(str)) or list(sample_direct.identity_key.astype(str)) != list(sample_r40.identity_key.astype(str)):
        raise ResearchError("COMMON_COHORT_IDENTITY_ORDER_MISMATCH")

    # HDA predictions.
    train_h, policy_h, fit_h = fit_sets(hda, hda_eligible)
    r42d_cols = [str(x) for x in r42d_cfg["method_contract"]["baseline_features"]] + util_names
    p_r42d, r42d_receipt = fit_model(train_h, policy_h, fit_h, sample_hda, r42d_cols, "outcome", HDA_CLASSES, r42d_cfg, base_cfg)
    p_market = sample_hda[["p_h", "p_d", "p_a"]].to_numpy(float)
    p_market = p_market / p_market.sum(axis=1, keepdims=True)
    y_hda = sample_hda.outcome.to_numpy(int)
    market_loss = _true_loss(y_hda, p_market); r42d_loss = _true_loss(y_hda, p_r42d)
    market_hit = np.argmax(p_market, axis=1) == y_hda; r42d_hit = np.argmax(p_r42d, axis=1) == y_hda
    hda_sets = {
        "both_correct": int(np.sum(market_hit & r42d_hit)),
        "market_only_correct": int(np.sum(market_hit & ~r42d_hit)),
        "r42d_only_correct": int(np.sum(~market_hit & r42d_hit)),
        "neither_correct": int(np.sum(~market_hit & ~r42d_hit)),
        "r42d_lower_loss_rows": int(np.sum(r42d_loss < market_loss)),
        "market_lower_loss_rows": int(np.sum(market_loss < r42d_loss)),
        "equal_loss_rows": int(np.sum(np.isclose(market_loss, r42d_loss, atol=1e-15, rtol=0.0))),
        "actual_draw_market_missed_r42d_hit": int(np.sum((y_hda == 1) & (~market_hit) & r42d_hit)),
    }
    hda_oracle = _oracle_summary(y_hda, {"market_consensus": p_market, "R42D_challenger": p_r42d}, "oracle_top1_accuracy")

    # Direct-T predictions under each original feature-eligibility fit contract.
    p_f_base, p_f_ch, f_receipt = _fit_direct_pair(direct, f_eligible, sample_direct, f_names, r42f_cfg, base_cfg)
    p_j_base, p_j_ch, j_receipt = _fit_direct_pair(direct, j_eligible, sample_direct, j_names, r42j_cfg, base_cfg)
    y_t = sample_direct.total_class.to_numpy(int)
    f_delta = _true_loss(y_t, p_f_ch) - _true_loss(y_t, p_f_base)
    j_delta = _true_loss(y_t, p_j_ch) - _true_loss(y_t, p_j_base)
    direct_sets = {
        "r42f_improves_own_baseline": int(np.sum(f_delta < 0)),
        "r42j_improves_own_baseline": int(np.sum(j_delta < 0)),
        "both_improve": int(np.sum((f_delta < 0) & (j_delta < 0))),
        "r42f_only_improves": int(np.sum((f_delta < 0) & ~(j_delta < 0))),
        "r42j_only_improves": int(np.sum(~(f_delta < 0) & (j_delta < 0))),
        "neither_improves": int(np.sum(~(f_delta < 0) & ~(j_delta < 0))),
        "loss_delta_correlation_r42f_vs_r42j": _safe_corr(f_delta, j_delta),
        "baseline_probability_max_abs_difference": float(np.max(np.abs(p_f_base - p_j_base))),
    }
    direct_models = {
        "R42F_baseline": p_f_base,
        "R42F_challenger": p_f_ch,
        "R42J_baseline": p_j_base,
        "R42J_challenger": p_j_ch,
    }
    direct_oracle = _oracle_summary(y_t, direct_models, "oracle_total_bucket_top1_accuracy")

    # R40F relation to common-cohort residuals.
    central = sample_r40.r40f_central.to_numpy(bool)
    actual_draw = y_hda == 1
    market_missed_draw = actual_draw & (~market_hit)
    r42d_improvement = r42d_loss - market_loss
    r40_common = {
        "central_rows": int(central.sum()),
        "outer_rows": int((~central).sum()),
        "central_draw_rate": float(actual_draw[central].mean()) if central.any() else None,
        "outer_draw_rate": float(actual_draw[~central].mean()) if (~central).any() else None,
        "market_missed_draw_rows": int(market_missed_draw.sum()),
        "market_missed_draw_central_fraction": float(central[market_missed_draw].mean()) if market_missed_draw.any() else None,
        "mean_r42d_minus_market_logloss_delta_central": float(r42d_improvement[central].mean()) if central.any() else None,
        "mean_r42d_minus_market_logloss_delta_outer": float(r42d_improvement[~central].mean()) if (~central).any() else None,
        "central_flag_vs_r42d_minus_market_loss_delta_corr": _safe_corr(central.astype(float), r42d_improvement),
    }

    # Per-match evidence table.
    table = sample_direct[["identity_key", "competition_id", "season", "date_key", "home_team", "away_team", "goal_difference", "total_class"]].copy()
    table["actual_hda"] = np.asarray([LABELS[i] for i in y_hda])
    table["market_pH"] = p_market[:, 0]; table["market_pD"] = p_market[:, 1]; table["market_pA"] = p_market[:, 2]
    table["r42d_pH"] = p_r42d[:, 0]; table["r42d_pD"] = p_r42d[:, 1]; table["r42d_pA"] = p_r42d[:, 2]
    table["r40f_latent_score"] = sample_r40.r40f_latent_score.to_numpy(float)
    table["r40f_decile"] = sample_r40.r40f_decile.to_numpy(int) + 1
    table["r40f_central"] = central.astype(int)
    for prefix, matrix in direct_models.items():
        safe_prefix = prefix.lower()
        for t in range(8):
            table[f"{safe_prefix}_pT_{t if t < 7 else '7plus'}"] = matrix[:, t]
    table["r42f_loss_delta_vs_own_baseline"] = f_delta
    table["r42j_loss_delta_vs_own_baseline"] = j_delta
    table["r42d_loss_delta_vs_market"] = r42d_improvement

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "common_cohort_1000_per_match.csv"
    table.to_csv(csv_path, index=False)
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_VIEWED_COMMON_COHORT_ORACLE_DIAGNOSTIC_COMPLETE",
        "scientific_verdict": "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_SCIENTIFIC_PASS_NO_PROMOTION",
        "source_blobs_verified": source_blobs,
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "cohort": {
            "common_target_pool_rows": int(len(common)),
            "selected_rows": int(len(table)),
            "identity_hash_seed": int(cfg["cohort_contract"]["identity_hash_seed"]),
            "identity_sha256": cohort_sha,
            "selection_uses_labels": False,
            "retrospective_viewed_only": True,
            "protected_confirmation_sample": False,
            "fixed100_or_fixed200_consumption": 0,
            "competition_count": int(table.competition_id.nunique()),
            "date_min": str(table.date_key.min()),
            "date_max": str(table.date_key.max()),
            "actual_H": int((table.actual_hda == "H").sum()),
            "actual_D": int((table.actual_hda == "D").sum()),
            "actual_A": int((table.actual_hda == "A").sum()),
        },
        "replay_audits": {
            "r42d_counterfactual": utility_audit,
            "r42f_htft": ht_audit,
            "r42f_source_coverage": ht_source_cov,
            "r40f": r40f_audit,
        },
        "hda": {
            "market": _hda_metrics(y_hda, p_market),
            "r42d": _hda_metrics(y_hda, p_r42d),
            "r42d_fit_receipt": r42d_receipt,
            "error_sets": hda_sets,
            "oracle": hda_oracle,
        },
        "direct_t": {
            "R42F_baseline": _direct_metrics(y_t, p_f_base),
            "R42F_challenger": _direct_metrics(y_t, p_f_ch),
            "R42J_baseline": _direct_metrics(y_t, p_j_base),
            "R42J_challenger": _direct_metrics(y_t, p_j_ch),
            "r42f_fit_receipt": f_receipt,
            "r42j_fit_receipt": j_receipt,
            "residual_complementarity": direct_sets,
            "oracle": direct_oracle,
        },
        "r40f_common_cohort": r40_common,
        "per_match_evidence": {
            "path": str(csv_path.relative_to(ROOT)),
            "sha256": csv_sha,
            "rows": int(len(table)),
        },
        "oracle_gap_learnability_status": "NOT_TESTED_DO_NOT_INFER_FROM_EX_POST_ORACLE",
        "interpretation": {
            "oracle_low_means": "The frozen signals have little ex-post complementarity on identical viewed matches.",
            "oracle_high_means": "There is ex-post error complementarity only; a separate time-ordered cross-fitted selector would still be required to show that the gap is learnable pre-match.",
            "this_run_cannot_claim": ["new OOS model", "SCIENTIFIC_COMPONENT_PASS", "CONFIRMATION_PASS", "FORMAL_PROMOTION_PASS", "formal PIT validity"],
        },
        "governance": cfg["governance"],
    }
    raw_json = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    json_path = out_dir / "status.json"
    json_path.write_text(raw_json, encoding="utf-8")
    (out_dir / "status.sha256").write_text(hashlib.sha256(raw_json.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return result


def self_test() -> None:
    A = [[2.0, 0.0], [0.0, 3.0]]; b = [4.0, 9.0]
    x = _solve_linear(A, b)
    assert np.allclose(x, [2.0, 3.0])
    y = np.asarray([0, 1, 2]); p = np.asarray([[0.8, 0.1, 0.1], [0.2, 0.6, 0.2], [0.1, 0.2, 0.7]])
    assert np.all(_true_loss(y, p) > 0)
    print(json.dumps({"status": "PASS_VIEWED_COMMON_COHORT_ORACLE_SELF_TEST"}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out_dir)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "cohort": result["cohort"],
        "hda_oracle": result["hda"]["oracle"],
        "direct_t_oracle": result["direct_t"]["oracle"],
        "oracle_gap_learnability_status": result["oracle_gap_learnability_status"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
