#!/usr/bin/env python3
"""R4 post-view draw-subtype -> conditional GD=0 mechanism diagnostic.

Direct-T is kept exactly flat as in PR #197. The existing conditional-GD model is also
kept intact except for the GD=0 mass when conditioning on T=2,4,6. A fixed five-class
score-subtype model supplies a draw-mechanism score. Only two residual parameters are
fit on the chronological policy split. All nonzero-GD probabilities retain their parent
relative shape.

The fixed500 is already VIEWED and existing processed 1X2 prices have no original quote
timestamp. Therefore this is retrospective research only, formal_weight=0, and cannot
be a scientific/confirmation PASS or authorize B05 label opening.
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
from lightgbm import LGBMClassifier
from scipy.optimize import minimize
from scipy.special import expit, logit

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
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
    paired_bootstrap,
    sample_fixed_n,
    score_metrics,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model

ROOT = Path(__file__).resolve().parents[1]
R4_CONFIG = ROOT / "config" / "direct_t_subtype_gd0_r4.json"
OUT_DIR = ROOT / "manifests" / "direct_t_subtype_gd0_r4"
MARKET_COLS = ["r4_mkt_p_home", "r4_mkt_p_draw", "r4_mkt_p_away", "r4_mkt_overround"]
SUBTYPE_NAMES = ["NON_DRAW", "0_0", "1_1", "2_2", "3_3_PLUS"]
SUBTYPE_INDEX = {name: i for i, name in enumerate(SUBTYPE_NAMES)}
TOTAL_TO_SUBTYPE = {2: "1_1", 4: "2_2", 6: "3_3_PLUS"}
EXPECTED_SAMPLE_SHA = "6e76c2580b03043ef0a6ae003013c70fa328176bd6d3b21c2908c2e5fdf2f375"
EXPECTED_DT_LL = 1.8638447565310998
EXPECTED_HDA_LL = 1.0556508959286397
EXPECTED_HDA_ACC = 0.452


def load_r4() -> dict[str, Any]:
    value = json.loads(R4_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("R4 config root must be object")
    return value


def _identity(row: pd.Series) -> str:
    return "|".join(str(row[k]) for k in KEYS)


def _identity_sha(frame: pd.DataFrame) -> str:
    identities = sorted(frame.apply(_identity, axis=1).astype(str))
    return hashlib.sha256(("\n".join(identities) + "\n").encode("utf-8")).hexdigest()


def _market4_from_prices(prices: list[float]) -> np.ndarray:
    inv = np.asarray([1.0 / float(x) for x in prices], dtype=float)
    overround = float(inv.sum())
    if not np.isfinite(inv).all() or overround <= 0:
        raise ResearchError("invalid 1X2 inverse odds")
    p = inv / overround
    return np.asarray([p[0], p[1], p[2], overround], dtype=float)


def _pick_1x2(row: dict[str, Any], headers: list[str], priorities: list[list[str]]) -> tuple[np.ndarray | None, list[str] | None]:
    for aliases in priorities:
        fields = [field_name(headers, [str(alias)]) for alias in aliases]
        if not all(fields):
            continue
        values = [float_value(row, str(field)) for field in fields]
        if all(valid_price(value) for value in values):
            return _market4_from_prices([float(x) for x in values]), [str(x) for x in fields]
    return None, None


def build_market4(raw: pd.DataFrame, priorities: list[list[str]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = set(KEYS + ["source_file", "row_number"])
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ResearchError(f"R4 ledger missing market provenance fields: {missing}")

    matrix = np.full((len(raw), 4), np.nan, dtype=float)
    source_field_use: Counter[str] = Counter()
    by_source: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for pos, row in enumerate(raw.itertuples(index=False)):
        source = str(getattr(row, "source_file"))
        row_number = int(getattr(row, "row_number"))
        by_source[source][row_number].append(pos)

    source_missing = 0
    row_missing = 0
    for source_file, wanted in sorted(by_source.items()):
        path = ROOT / source_file
        if not path.is_file():
            source_missing += sum(len(v) for v in wanted.values())
            continue
        seen: set[int] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            for row_number, record in enumerate(reader, start=2):
                if row_number not in wanted:
                    continue
                seen.add(row_number)
                vec, fields = _pick_1x2(record, headers, priorities)
                if vec is None:
                    continue
                if fields:
                    source_field_use["|".join(fields)] += len(wanted[row_number])
                for pos in wanted[row_number]:
                    matrix[pos] = vec
        for row_number, positions in wanted.items():
            if row_number not in seen:
                row_missing += len(positions)

    market = raw[KEYS].copy()
    market["season"] = market["season"].astype(str)
    for i, col in enumerate(MARKET_COLS):
        market[col] = matrix[:, i]
    complete = np.isfinite(matrix).all(axis=1)
    audit = {
        "rows": int(len(raw)),
        "complete_rows": int(complete.sum()),
        "complete_rate": float(complete.mean()) if len(raw) else 0.0,
        "source_file_missing_rows": int(source_missing),
        "source_row_missing_rows": int(row_missing),
        "field_triplet_use": dict(source_field_use.most_common()),
    }
    return market, audit


def subtype_label(home: int, away: int) -> int:
    if home != away:
        return 0
    if home == 0:
        return 1
    if home == 1:
        return 2
    if home == 2:
        return 3
    return 4


def attach_r4_columns(
    fold: pd.DataFrame,
    raw: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    out = fold.copy()
    labels = raw[KEYS + ["home_goals_90", "away_goals_90"]].copy()
    labels["season"] = labels["season"].astype(str)
    out["season"] = out["season"].astype(str)
    out = out.merge(labels, on=KEYS, how="left", validate="one_to_one")
    out = out.merge(market, on=KEYS, how="left", validate="one_to_one")
    if out[["home_goals_90", "away_goals_90"]].isna().any().any():
        raise ResearchError("R4 score label attachment failure")
    out["r4_subtype"] = [
        subtype_label(int(h), int(a))
        for h, a in zip(out.home_goals_90, out.away_goals_90)
    ]
    return out


def fit_subtype_model(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    sample: pd.DataFrame,
    features: list[str],
    cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    params = dict(cfg["subtype_contract"]["hyperparameters"])
    model = LGBMClassifier(**params)
    model.fit(train[features], train.r4_subtype.to_numpy(int))
    fitted_classes = [int(x) for x in model.classes_]
    if fitted_classes != list(range(5)):
        raise ResearchError(f"R4 subtype classes mismatch: {fitted_classes}")
    p_policy = np.asarray(model.predict_proba(policy[features]), dtype=float)
    p_sample = np.asarray(model.predict_proba(sample[features]), dtype=float)
    if p_policy.shape != (len(policy), 5) or p_sample.shape != (len(sample), 5):
        raise ResearchError("R4 subtype probability shape mismatch")
    if np.max(np.abs(p_policy.sum(axis=1) - 1.0)) > 1e-10 or np.max(np.abs(p_sample.sum(axis=1) - 1.0)) > 1e-10:
        raise ResearchError("R4 subtype probability conservation failure")

    def ll(y: np.ndarray, p: np.ndarray) -> float:
        return float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0)).mean())

    receipt = {
        "train_rows": int(len(train)),
        "policy_rows": int(len(policy)),
        "fixed500_rows": int(len(sample)),
        "class_counts_train": {SUBTYPE_NAMES[i]: int(np.sum(train.r4_subtype.to_numpy(int) == i)) for i in range(5)},
        "class_counts_policy": {SUBTYPE_NAMES[i]: int(np.sum(policy.r4_subtype.to_numpy(int) == i)) for i in range(5)},
        "class_counts_fixed500": {SUBTYPE_NAMES[i]: int(np.sum(sample.r4_subtype.to_numpy(int) == i)) for i in range(5)},
        "policy_multiclass_logloss": ll(policy.r4_subtype.to_numpy(int), p_policy),
        "fixed500_multiclass_logloss": ll(sample.r4_subtype.to_numpy(int), p_sample),
        "hyperparameters": params,
    }
    return p_policy, p_sample, receipt


def train_only_direct_total(
    fold: pd.DataFrame,
    target: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
    C: float,
) -> np.ndarray:
    classes = [int(x) for x in config["model_contract"]["direct_total_classes"]]
    train = fold[fold.split == "train"]
    model = make_model(C, config)
    model.fit(train[features], train.total_class)
    return align_probability(model, target[features], classes)


def train_only_conditional_p0(
    fold: pd.DataFrame,
    target: pd.DataFrame,
    features: list[str],
    config: dict[str, Any],
    totals: list[int],
    C: float,
) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    for total in totals:
        classes = list(range(-total, total + 1, 2))
        if 0 not in classes:
            raise ResearchError(f"R4 total has no GD0 support: {total}")
        train = fold[(fold.split == "train") & (fold.total_class == total)]
        model = make_model(C, config)
        model.fit(train[features], train.goal_difference)
        prob = align_probability(model, target[features], classes)
        out[total] = prob[:, classes.index(0)]
    return out


def mechanism_score(
    subtype_prob: np.ndarray,
    p_total: np.ndarray,
    total: int,
    eps: float,
) -> tuple[np.ndarray, dict[str, float]]:
    subtype_name = TOTAL_TO_SUBTYPE[total]
    raw = subtype_prob[:, SUBTYPE_INDEX[subtype_name]] / np.clip(p_total[:, total], eps, 1.0)
    clipped = np.clip(raw, eps, 1.0 - eps)
    return clipped, {
        "raw_min": float(np.min(raw)),
        "raw_median": float(np.median(raw)),
        "raw_max": float(np.max(raw)),
        "clipped_low_rate": float(np.mean(raw < eps)),
        "clipped_high_rate": float(np.mean(raw > 1.0 - eps)),
        "mean_score": float(np.mean(clipped)),
    }


def fit_residual(
    p0: np.ndarray,
    score: np.ndarray,
    y: np.ndarray,
    cfg: dict[str, Any],
    use_subtype: bool,
) -> dict[str, Any]:
    eps = float(cfg["epsilon"])
    p0 = np.clip(np.asarray(p0, dtype=float), eps, 1.0 - eps)
    score = np.clip(np.asarray(score, dtype=float), eps, 1.0 - eps)
    y = np.asarray(y, dtype=float)
    z0 = logit(p0)
    zs = logit(score)
    l2 = float(cfg["l2_penalty"])
    bounds_cfg = cfg["bounds"]

    if use_subtype:
        x0 = np.zeros(2, dtype=float)
        bounds = [tuple(float(x) for x in bounds_cfg["b0"]), tuple(float(x) for x in bounds_cfg["b1"])]

        def objective(theta: np.ndarray) -> float:
            eta = z0 + theta[0] + theta[1] * (zs - z0)
            q = np.clip(expit(eta), eps, 1.0 - eps)
            nll = -np.mean(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))
            return float(nll + l2 * np.sum(theta * theta))
    else:
        x0 = np.zeros(1, dtype=float)
        bounds = [tuple(float(x) for x in bounds_cfg["b0"])]

        def objective(theta: np.ndarray) -> float:
            q = np.clip(expit(z0 + theta[0]), eps, 1.0 - eps)
            nll = -np.mean(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))
            return float(nll + l2 * theta[0] * theta[0])

    result = minimize(objective, x0=x0, method="L-BFGS-B", bounds=bounds)
    if not result.success or not np.isfinite(result.fun):
        raise ResearchError(f"R4 residual optimization failed: {result.message}")
    if use_subtype:
        b0, b1 = float(result.x[0]), float(result.x[1])
    else:
        b0, b1 = float(result.x[0]), 0.0
    return {
        "b0": b0,
        "b1": b1,
        "objective": float(result.fun),
        "iterations": int(result.nit),
        "success": bool(result.success),
    }


def transform_p0(p0: np.ndarray, score: np.ndarray, fit: dict[str, Any], eps: float) -> np.ndarray:
    p0 = np.clip(np.asarray(p0, dtype=float), eps, 1.0 - eps)
    score = np.clip(np.asarray(score, dtype=float), eps, 1.0 - eps)
    eta = logit(p0) + float(fit["b0"]) + float(fit["b1"]) * (logit(score) - logit(p0))
    return np.clip(expit(eta), eps, 1.0 - eps)


def adjusted_conditional(
    base: dict[int, tuple[list[int], np.ndarray]],
    subtype_prob: np.ndarray,
    p_total: np.ndarray,
    fit: dict[str, Any],
    cfg: dict[str, Any],
) -> tuple[dict[int, tuple[list[int], np.ndarray]], dict[str, Any]]:
    out: dict[int, tuple[list[int], np.ndarray]] = {}
    eps = float(cfg["epsilon"])
    diagnostics: dict[str, Any] = {}
    for total, (classes, prob) in base.items():
        new = np.asarray(prob, dtype=float).copy()
        if total in TOTAL_TO_SUBTYPE:
            zero = classes.index(0)
            score, score_diag = mechanism_score(subtype_prob, p_total, total, eps)
            p0 = np.clip(new[:, zero], eps, 1.0 - eps)
            q0 = transform_p0(p0, score, fit, eps)
            denom = np.clip(1.0 - p0, eps, None)
            scale = (1.0 - q0) / denom
            for j in range(len(classes)):
                if j != zero:
                    new[:, j] *= scale
            new[:, zero] = q0
            diagnostics[str(total)] = {
                "baseline_mean_p0": float(np.mean(p0)),
                "adjusted_mean_p0": float(np.mean(q0)),
                "mean_delta_p0": float(np.mean(q0 - p0)),
                "mechanism_score": score_diag,
            }
        if np.max(np.abs(new.sum(axis=1) - 1.0)) > 1e-10 or np.any(new < 0):
            raise ResearchError(f"R4 adjusted conditional probability failure T={total}")
        out[total] = (classes, new)
    return out, diagnostics


def add_joint(rows: pd.DataFrame, prefix: str, joint: list[dict[str, Any]]) -> None:
    jf = pd.DataFrame(joint)
    for column in jf.columns:
        rows[f"{prefix}_{column}"] = jf[column].to_numpy()


def clean_hda(value: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    out = dict(value)
    ll_rows = np.asarray(out.pop("_ll_rows"), dtype=float)
    return out, ll_rows


def metric_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, float]:
    return {
        "log_loss": float(a["log_loss"] - b["log_loss"]),
        "brier": float(a["brier"] - b["brier"]),
        "rps": float(a["rps"] - b["rps"]),
        "accuracy_pp": float(100.0 * (a["accuracy"] - b["accuracy"])),
        "macro_f1_pp": float(100.0 * (a["macro_f1"] - b["macro_f1"])),
        "draw_f1_pp": float(100.0 * (a["draw_f1"] - b["draw_f1"])),
    }


def run() -> dict[str, Any]:
    r4 = load_r4()
    config = load_config()
    ledger = ROOT / str(config["input_ledger"])
    if not ledger.is_file():
        raise ResearchError(f"R4 ledger missing: {ledger}")
    raw = pd.read_csv(ledger)
    data_identity = audit_data_identity(raw, config)
    base_features = build_features(raw)
    core = select_core_features(base_features)
    if len(core) != 47:
        raise ResearchError(f"R4 core feature count changed: {len(core)}")
    seasons, excluded = complete_seasons(raw, config)

    market, market_audit_all = build_market4(raw, r4["subtype_contract"]["one_x_two_alias_priority"])
    base_fold = base_features.copy()
    test_position = int(r4["source_contract"]["test_position_zero_based"])
    base_fold["split"] = assign_fold(base_fold, seasons, test_position)
    base_fold["fold"] = f"window_{test_position - 1}_to_{test_position}"
    test = base_fold[base_fold.split == "test"].copy()
    sample_base, sample_hash = sample_fixed_n(test, int(r4["source_contract"]["sample_n"]))
    if sample_hash != EXPECTED_SAMPLE_SHA:
        raise ResearchError(f"R4 parent fixed500 identity mismatch: {sample_hash}")

    fold = attach_exact_total(base_fold, raw)
    fold = attach_r4_columns(fold, raw, market)
    sample = fold.merge(
        sample_base[KEYS + ["match_identity", "identity_hash"]],
        on=KEYS,
        how="inner",
        validate="one_to_one",
    )
    if len(sample) != 500 or _identity_sha(sample) != EXPECTED_SAMPLE_SHA:
        raise ResearchError("R4 sample attachment changed fixed500 identity")

    train = fold[fold.split == "train"].copy()
    policy = fold[fold.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("R4 empty train/policy split")

    subtype_features = core + MARKET_COLS
    if len(subtype_features) != int(r4["subtype_contract"]["feature_count"]):
        raise ResearchError("R4 subtype feature count mismatch")

    # Exact PR197 flat baseline first. No R4 metric is accepted unless this reproduces.
    p_flat, _, flat_receipt = direct_total_probabilities(fold, sample, core, config)
    cond_parent, _, cond_receipt = conditional_probabilities(fold, sample, core, config)
    baseline_joint = assemble_joint(p_flat, cond_parent)

    rows = sample[KEYS + ["match_identity", "identity_hash", "home_goals_90", "away_goals_90", "exact_total", "goal_difference", "r4_subtype"]].copy()
    rows = rows.rename(columns={"exact_total": "actual_total", "goal_difference": "actual_gd"})
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str) + ":" + rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd > 0, "H", np.where(rows.actual_gd == 0, "D", "A"))
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int), 7)
    rows["flat_pred_total_class"] = np.argmax(p_flat, axis=1)
    add_joint(rows, "baseline", baseline_joint)

    baseline_hda, baseline_ll_rows = clean_hda(hda_metrics(rows, "baseline"))
    baseline_score = score_metrics(rows, "baseline")
    y_total = rows.actual_total_class.to_numpy(int)
    flat_dt_ll = float(-np.log(np.clip(p_flat[np.arange(len(rows)), y_total], 1e-15, 1.0)).mean())
    if abs(flat_dt_ll - EXPECTED_DT_LL) > 1e-12:
        raise ResearchError(f"R4 Direct-T parent reproduction mismatch: {flat_dt_ll}")
    if abs(float(baseline_hda["log_loss"]) - EXPECTED_HDA_LL) > 1e-12:
        raise ResearchError(f"R4 HDA parent reproduction mismatch: {baseline_hda['log_loss']}")
    if abs(float(baseline_hda["accuracy"]) - EXPECTED_HDA_ACC) > 1e-12:
        raise ResearchError(f"R4 HDA accuracy parent reproduction mismatch: {baseline_hda['accuracy']}")
    if int(baseline_hda["actual_counts"]["D"]) != 145 or int(baseline_hda["predicted_counts"]["D"]) != 0:
        raise ResearchError("R4 parent draw-count reproduction mismatch")

    p_sub_policy, p_sub_sample, subtype_receipt = fit_subtype_model(
        train, policy, sample, subtype_features, r4
    )

    fixed_C = 0.01
    pT_policy = train_only_direct_total(fold, policy, core, config, fixed_C)
    p0_policy = train_only_conditional_p0(
        fold, policy, core, config, [2, 4, 6], fixed_C
    )
    eps = float(r4["gd0_residual_contract"]["epsilon"])
    mechanism_policy: dict[int, np.ndarray] = {}
    mechanism_sample: dict[int, np.ndarray] = {}
    mechanism_diag: dict[str, Any] = {"policy": {}, "fixed500": {}}
    for total in [2, 4, 6]:
        mechanism_policy[total], mechanism_diag["policy"][str(total)] = mechanism_score(
            p_sub_policy, pT_policy, total, eps
        )
        mechanism_sample[total], mechanism_diag["fixed500"][str(total)] = mechanism_score(
            p_sub_sample, p_flat, total, eps
        )

    fit_mask = policy.total_class.astype(int).isin([2, 4, 6]).to_numpy()
    policy_totals = policy.total_class.to_numpy(int)
    p0_fit = np.empty(int(fit_mask.sum()), dtype=float)
    score_fit = np.empty(int(fit_mask.sum()), dtype=float)
    y_fit = (policy.goal_difference.to_numpy(int)[fit_mask] == 0).astype(float)
    selected_rows = np.flatnonzero(fit_mask)
    for out_i, row_i in enumerate(selected_rows):
        total = int(policy_totals[row_i])
        p0_fit[out_i] = p0_policy[total][row_i]
        score_fit[out_i] = mechanism_policy[total][row_i]

    residual_cfg = r4["gd0_residual_contract"]
    intercept_fit = fit_residual(p0_fit, score_fit, y_fit, residual_cfg, use_subtype=False)
    subtype_fit = fit_residual(p0_fit, score_fit, y_fit, residual_cfg, use_subtype=True)

    # Use the same fixed500 subtype predictions and flat Direct-T denominator for every
    # conditional T head. Direct-T itself is never changed.
    cond_intercept, intercept_diag = adjusted_conditional(
        cond_parent, p_sub_sample, p_flat, intercept_fit, residual_cfg
    )
    cond_subtype, subtype_diag = adjusted_conditional(
        cond_parent, p_sub_sample, p_flat, subtype_fit, residual_cfg
    )
    intercept_joint = assemble_joint(p_flat, cond_intercept)
    subtype_joint = assemble_joint(p_flat, cond_subtype)
    add_joint(rows, "intercept", intercept_joint)
    add_joint(rows, "subtype", subtype_joint)

    intercept_hda, intercept_ll_rows = clean_hda(hda_metrics(rows, "intercept"))
    subtype_hda, subtype_ll_rows = clean_hda(hda_metrics(rows, "subtype"))
    intercept_score = score_metrics(rows, "intercept")
    subtype_score_metrics = score_metrics(rows, "subtype")

    report = r4["reporting_contract"]
    boot_n = int(report["paired_bootstrap_resamples"])
    seed = int(report["bootstrap_seed"])
    boot_vs_base = paired_bootstrap(subtype_ll_rows - baseline_ll_rows, boot_n, seed)
    boot_vs_intercept = paired_bootstrap(subtype_ll_rows - intercept_ll_rows, boot_n, seed + 1)

    gate = report["development_signal_gate"]
    gain_vs_base = float(baseline_hda["log_loss"] - subtype_hda["log_loss"])
    gain_vs_intercept = float(intercept_hda["log_loss"] - subtype_hda["log_loss"])
    checks = {
        "minimum_hda_logloss_gain_vs_baseline": bool(gain_vs_base >= float(gate["minimum_hda_logloss_gain_vs_baseline"])),
        "minimum_hda_logloss_gain_vs_intercept_only": bool(gain_vs_intercept >= float(gate["minimum_hda_logloss_gain_vs_intercept_only"])),
        "bootstrap_logloss_p95_vs_baseline_lt_zero": bool(boot_vs_base["p95"] < 0.0),
        "bootstrap_logloss_p95_vs_intercept_only_lt_zero": bool(boot_vs_intercept["p95"] < 0.0),
        "brier_nonworse_vs_baseline": bool(subtype_hda["brier"] <= baseline_hda["brier"]),
        "rps_nonworse_vs_baseline": bool(subtype_hda["rps"] <= baseline_hda["rps"]),
        "minimum_draw_f1": bool(subtype_hda["draw_f1"] >= float(gate["minimum_draw_f1"])),
        "minimum_natural_top1_draw_calls": bool(subtype_hda["predicted_counts"]["D"] >= int(gate["minimum_natural_top1_draw_calls"])),
    }
    signal = bool(all(checks.values()))

    for i, name in enumerate(SUBTYPE_NAMES):
        rows[f"subtype_p_{name}"] = p_sub_sample[:, i]
    for total in [2, 4, 6]:
        rows[f"mechanism_score_T{total}"] = mechanism_sample[total]

    coverage = {}
    for split_name, frame in (("train", train), ("policy", policy), ("fixed500", sample)):
        complete = np.isfinite(frame[MARKET_COLS].to_numpy(float)).all(axis=1)
        coverage[split_name] = {
            "rows": int(len(frame)),
            "complete_market4_rows": int(complete.sum()),
            "complete_market4_rate": float(complete.mean()) if len(frame) else 0.0,
        }

    result = {
        "schema_version": r4["schema_version"],
        "status": "POSTVIEW_DIRECT_T_SUBTYPE_GD0_R4_COMPLETE_NO_PROMOTION",
        "scientific_verdict": (
            "POSTVIEW_SUBTYPE_GD0_DEVELOPMENT_SIGNAL_DESCRIPTIVE_ONLY"
            if signal else "POSTVIEW_SUBTYPE_GD0_SIGNAL_NOT_ESTABLISHED"
        ),
        "parent_reproduction": {
            "sample_n": 500,
            "sample_identity_sha256": sample_hash,
            "flat_direct_total_logloss": flat_dt_ll,
            "flat_hda": baseline_hda,
            "flat_score": baseline_score,
            "flat_direct_total_receipt": flat_receipt,
            "conditional_gd_receipt": cond_receipt,
        },
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "market4": {
            "evidence_class": r4["subtype_contract"]["market_evidence_class"],
            "formal_pre_match_snapshot": False,
            "all_ledger_audit": market_audit_all,
            "coverage": coverage,
        },
        "subtype_head": subtype_receipt,
        "policy_residual_fit": {
            "fit_rows": int(len(y_fit)),
            "draw_rows": int(y_fit.sum()),
            "draw_rate": float(y_fit.mean()),
            "intercept_only": intercept_fit,
            "subtype_residual": subtype_fit,
            "mechanism_score_diagnostics": mechanism_diag["policy"],
        },
        "fixed500_mechanism_score_diagnostics": mechanism_diag["fixed500"],
        "models": {
            "baseline": {
                "hda": baseline_hda,
                "score": baseline_score,
            },
            "intercept_only": {
                "hda": intercept_hda,
                "score": intercept_score,
                "delta_vs_baseline": metric_delta(intercept_hda, baseline_hda),
                "gd0_adjustment": intercept_diag,
            },
            "subtype_residual": {
                "hda": subtype_hda,
                "score": subtype_score_metrics,
                "delta_vs_baseline": metric_delta(subtype_hda, baseline_hda),
                "delta_vs_intercept_only": metric_delta(subtype_hda, intercept_hda),
                "gd0_adjustment": subtype_diag,
            },
        },
        "paired_bootstrap": {
            "subtype_minus_baseline_hda_logloss": boot_vs_base,
            "subtype_minus_intercept_only_hda_logloss": boot_vs_intercept,
        },
        "development_signal": {
            "passed": signal,
            "gain_hda_logloss_vs_baseline": gain_vs_base,
            "gain_hda_logloss_vs_intercept_only": gain_vs_intercept,
            "checks": checks,
            "scientific_pass": False,
            "future_oos_label_open_authorized": False,
        },
        "boundary": {
            "retrospective_viewed_fixed500": True,
            "market_evidence_not_strict_pit": True,
            "direct_t_changed": False,
            "adjusted_conditional_total_classes": [2, 4, 6],
            "forced_draw": False,
            "manual_hda_threshold": False,
            "post_fixed500_parameter_search": False,
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
