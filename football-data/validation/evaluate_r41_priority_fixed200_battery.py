#!/usr/bin/env python3
"""R41 priority battery: four disjoint fixed-200 retrospective market-method screens.

This is research-only retrospective evidence. Historical market prices in the repository
have no original quote timestamps and are therefore NEVER treated as formal PIT snapshots.
Each fixed200 is identity-selected without labels, is disjoint from R41A/R41B and from the
other methods in this battery, and is not used for C/threshold/feature selection.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from evaluate_r41a_fixed200_joint_error_decomposition import (
    add_identity_key,
    load_json,
    select_fixed_identities,
    split_for_latest_complete,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
)
from v510_historical_structure_model_r1 import (
    align_probability,
    make_model,
    metric_components,
    metric_summary,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r41_priority_fixed200_battery.json"
DEFAULT_OUT = ROOT / "manifests" / "r41_priority_fixed200_battery_status.json"
HDA_CLASSES = [0, 1, 2]  # H, D, A

BOOK_FIELDS = {
    "B365": ("B365CH", "B365CD", "B365CA"),
    "BW": ("BWCH", "BWCD", "BWCA"),
    "IW": ("IWCH", "IWCD", "IWCA"),
    "PS": ("PSCH", "PSCD", "PSCA"),
    "WH": ("WHCH", "WHCD", "WHCA"),
    "VC": ("VCCH", "VCCD", "VCCA"),
}
AH_PAIRS = {
    "PS": ("PCAHH", "PCAHA"),
    "B365": ("B365CAHH", "B365CAHA"),
    "AVG": ("AvgCAHH", "AvgCAHA"),
    "MAX": ("MaxCAHH", "MaxCAHA"),
}
OU_PAIRS = {
    "PS": ("PC>2.5", "PC<2.5"),
    "B365": ("B365C>2.5", "B365C<2.5"),
    "AVG": ("AvgC>2.5", "AvgC<2.5"),
    "MAX": ("MaxC>2.5", "MaxC<2.5"),
}


def devig(values: list[float]) -> np.ndarray:
    inv = 1.0 / np.asarray(values, dtype=float)
    if not np.all(np.isfinite(inv)) or np.any(inv <= 0):
        raise ResearchError("invalid odds in de-vig")
    return inv / inv.sum()


def complete_values(row: dict[str, Any], headers: list[str], aliases: tuple[str, ...]) -> list[float] | None:
    vals: list[float] = []
    for alias in aliases:
        name = field_name(headers, [alias])
        value = float_value(row, name) if name else None
        if not valid_price(value):
            return None
        vals.append(float(value))
    return vals


def market_record(row: dict[str, Any], headers: list[str], contract: dict[str, Any]) -> dict[str, Any]:
    books: dict[str, np.ndarray] = {}
    for book in contract["cross_book_independent_closing_books"]:
        aliases = BOOK_FIELDS.get(str(book))
        vals = complete_values(row, headers, aliases) if aliases else None
        if vals is not None:
            books[str(book)] = devig(vals)

    if books:
        stack = np.vstack([books[key] for key in sorted(books)])
        consensus = stack.mean(axis=0)
        std = stack.std(axis=0)
        ranges = stack.max(axis=0) - stack.min(axis=0)
    else:
        consensus = np.asarray([np.nan, np.nan, np.nan], dtype=float)
        std = np.asarray([np.nan, np.nan, np.nan], dtype=float)
        ranges = np.asarray([np.nan, np.nan, np.nan], dtype=float)

    ah_line_name = field_name(headers, ["AHCh", "AHh", "asian_handicap_line", "handicap_line"])
    ah_line = float_value(row, ah_line_name) if ah_line_name else None
    ah_home_prob = None
    ah_source = None
    for source in contract["asian_reference_preference"]:
        aliases = AH_PAIRS.get(str(source))
        vals = complete_values(row, headers, aliases) if aliases else None
        if vals is not None:
            p = devig(vals)
            ah_home_prob = float(p[0])
            ah_source = str(source)
            break

    ou_over_prob = None
    ou_source = None
    for source in contract["totals_reference_preference"]:
        aliases = OU_PAIRS.get(str(source))
        vals = complete_values(row, headers, aliases) if aliases else None
        if vals is not None:
            p = devig(vals)
            ou_over_prob = float(p[0])
            ou_source = str(source)
            break

    return {
        "book_count": int(len(books)),
        "p_h": float(consensus[0]),
        "p_d": float(consensus[1]),
        "p_a": float(consensus[2]),
        "std_h": float(std[0]),
        "std_d": float(std[1]),
        "std_a": float(std[2]),
        "range_h": float(ranges[0]),
        "range_d": float(ranges[1]),
        "range_a": float(ranges[2]),
        "ah_line": float(ah_line) if ah_line is not None and math.isfinite(float(ah_line)) else np.nan,
        "ah_home_prob": float(ah_home_prob) if ah_home_prob is not None else np.nan,
        "ou_over_prob": float(ou_over_prob) if ou_over_prob is not None else np.nan,
        "ah_source": ah_source,
        "ou_source": ou_source,
        "has_ah_ou": bool(ah_line is not None and ah_home_prob is not None and ou_over_prob is not None),
    }


def materialize_market(ledger: pd.DataFrame, contract: dict[str, Any]) -> pd.DataFrame:
    keyed = add_identity_key(ledger)
    wanted: dict[str, dict[int, str]] = {}
    for row in keyed[["source_file", "row_number", "identity_key"]].itertuples(index=False):
        wanted.setdefault(str(row.source_file), {})[int(row.row_number)] = str(row.identity_key)

    records: list[dict[str, Any]] = []
    for source_file in sorted(wanted):
        path = ROOT / source_file
        if not path.is_file():
            continue
        row_map = wanted[source_file]
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            for row_number, row in enumerate(reader, start=2):
                identity = row_map.get(row_number)
                if identity is None:
                    continue
                rec = market_record(row, headers, contract)
                rec.update({"identity_key": identity, "source_file": source_file, "row_number": row_number})
                records.append(rec)

    if not records:
        raise ResearchError("no market records materialized")
    frame = pd.DataFrame(records)
    frame["quality"] = frame.book_count.astype(int) * 10 + frame.has_ah_ou.astype(int)
    frame = frame.sort_values(["identity_key", "quality", "source_file", "row_number"], ascending=[True, False, True, True])
    frame = frame.drop_duplicates("identity_key", keep="first").drop(columns=["quality"])
    return frame.reset_index(drop=True)


def prepare_features(raw: pd.DataFrame, market: pd.DataFrame, seasons: dict[str, list[str]], cfg: dict[str, Any]) -> pd.DataFrame:
    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    merged = features.merge(market, on="identity_key", how="left", validate="one_to_one")
    eps = 1e-12
    for col in ("p_h", "p_d", "p_a"):
        merged[f"log_{col}"] = np.log(np.clip(merged[col].astype(float), eps, 1.0))
    merged["hda_strength"] = merged.p_h - merged.p_a
    merged["hda_decisive_home_share"] = merged.p_h / np.clip(merged.p_h + merged.p_a, eps, None)
    merged["abs_ah_line"] = merged.ah_line.abs()
    merged["ah_vs_hda_strength"] = merged.ah_home_prob - merged.hda_decisive_home_share
    merged["draw_under_interaction"] = merged.p_d * (1.0 - merged.ou_over_prob)
    merged["outcome"] = np.where(merged.goal_difference > 0, 0, np.where(merged.goal_difference == 0, 1, 2)).astype(int)
    merged["is_draw"] = (merged.outcome == 1).astype(int)
    return merged


def fit_model(
    train: pd.DataFrame,
    policy: pd.DataFrame,
    fit: pd.DataFrame,
    test: pd.DataFrame,
    cols: list[str],
    target: str,
    classes: list[int],
    cfg: dict[str, Any],
    base_cfg: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for C in cfg["fit_contract"]["regularization_C_grid"]:
        model = make_model(float(C), base_cfg)
        model.fit(train[cols], train[target])
        p = align_probability(model, policy[cols], classes)
        comp = metric_components(policy[target].to_numpy(int), p, classes)
        receipts.append({"C": float(C), "policy_logloss": float(comp.logloss.mean())})
    best = min(receipts, key=lambda x: (x["policy_logloss"], x["C"]))
    model = make_model(float(best["C"]), base_cfg)
    model.fit(fit[cols], fit[target])
    p = align_probability(model, test[cols], classes)
    return p, {
        "selected_C": float(best["C"]),
        "policy_grid": receipts,
        "feature_count": int(len(cols)),
        "features": cols,
        "probability_sum_max_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))),
    }


def draw_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    truth = (y == 1).astype(int)
    pd_ = np.clip(p[:, 1], 1e-15, 1 - 1e-15)
    ll = -(truth * np.log(pd_) + (1 - truth) * np.log(1 - pd_))
    pred_draw = np.argmax(p, axis=1) == 1
    tp = int(np.sum(pred_draw & (truth == 1)))
    fp = int(np.sum(pred_draw & (truth == 0)))
    fn = int(np.sum((~pred_draw) & (truth == 1)))
    auc = float(roc_auc_score(truth, pd_)) if len(np.unique(truth)) == 2 else None
    return {
        "actual_draws": int(truth.sum()),
        "predicted_top1_draws": int(pred_draw.sum()),
        "draw_logloss": float(ll.mean()),
        "draw_auc": auc,
        "top1_draw_precision": float(tp / (tp + fp)) if tp + fp else None,
        "top1_draw_recall": float(tp / (tp + fn)) if tp + fn else None,
    }


def paired_bootstrap(delta: np.ndarray, cfg: dict[str, Any], seed: int) -> dict[str, float]:
    delta = np.asarray(delta, dtype=float)
    n = len(delta)
    rng = np.random.default_rng(seed)
    samples = int(cfg["decision_contract"]["bootstrap_samples"])
    picks = rng.integers(0, n, size=(samples, n))
    means = delta[picks].mean(axis=1)
    q0, q1 = [float(x) for x in cfg["decision_contract"]["bootstrap_interval"]]
    return {
        "mean": float(means.mean()),
        "p05": float(np.quantile(means, q0)),
        "p95": float(np.quantile(means, q1)),
        "probability_challenger_better": float((means < 0).mean()),
    }


def compare(y: np.ndarray, baseline: np.ndarray, challenger: np.ndarray, cfg: dict[str, Any], seed: int) -> dict[str, Any]:
    b = metric_components(y, baseline, HDA_CLASSES)
    c = metric_components(y, challenger, HDA_CLASSES)
    delta = {name: float(c[name].mean() - b[name].mean()) for name in b.columns}
    boot = paired_bootstrap(c.logloss.to_numpy(float) - b.logloss.to_numpy(float), cfg, seed)
    pass_gate = bool(delta["logloss"] < 0 and boot["p95"] < 0 and delta["brier"] <= 0 and delta["rps"] <= 0)
    return {
        "baseline": metric_summary(b),
        "challenger": metric_summary(c),
        "delta_challenger_minus_baseline": delta,
        "paired_bootstrap_logloss_delta_90": boot,
        "baseline_draw": draw_metrics(y, baseline),
        "challenger_draw": draw_metrics(y, challenger),
        "gate": {
            "logloss_mean_better": bool(delta["logloss"] < 0),
            "logloss_p95_below_zero": bool(boot["p95"] < 0),
            "brier_nonworse": bool(delta["brier"] <= 0),
            "rps_nonworse": bool(delta["rps"] <= 0),
            "all_required": pass_gate,
        },
    }


def select_method_sample(
    frame: pd.DataFrame,
    eligible: pd.Series,
    excluded: set[str],
    size: int,
    seed: int,
) -> tuple[pd.DataFrame, str]:
    pool = frame[(frame.split == "target_pool") & eligible & (~frame.identity_key.isin(excluded))].copy()
    ids, digest = select_fixed_identities(pool, size, seed)
    sample = frame[frame.identity_key.isin(set(ids))].copy().sort_values("identity_key")
    if len(sample) != size:
        raise ResearchError(f"fixed200 reproduction failed seed={seed}: {len(sample)}")
    if not (sample.split == "target_pool").all():
        raise ResearchError("sample contains non-target row")
    return sample, digest


def fit_sets(frame: pd.DataFrame, eligible: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    e = frame[eligible].copy()
    train = e[e.split == "train"]
    policy = e[e.split == "policy"]
    fit = e[e.split.isin(["train", "policy"])]
    if min(len(train), len(policy), len(fit)) == 0:
        raise ResearchError("empty fit split")
    return train, policy, fit


def method_receipt(method: dict[str, Any], sample: pd.DataFrame, digest: str, eligible_pool: int) -> dict[str, Any]:
    return {
        "id": method["id"],
        "name": method["name"],
        "rows": int(len(sample)),
        "seed": int(method["seed"]),
        "identity_sha256": digest,
        "eligible_target_pool_before_exclusions": int(eligible_pool),
        "competitions_represented": int(sample.competition_id.nunique()),
        "date_min": str(sample.date_key.min()),
        "date_max": str(sample.date_key.max()),
        "labels_used_for_identity_selection": False,
        "blind_claim": False,
    }


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    market = materialize_market(raw, cfg["market_contract"])
    frame = prepare_features(raw, market, seasons, cfg)

    target_pool = frame[frame.split == "target_pool"].copy()
    prior_a, prior_a_sha = select_fixed_identities(target_pool, 200, int(cfg["sample_contract"]["exclude_R41A_seed"]))
    prior_b_pool = target_pool[~target_pool.identity_key.isin(set(prior_a))]
    prior_b, prior_b_sha = select_fixed_identities(prior_b_pool, 200, int(cfg["sample_contract"]["exclude_R41B_seed"]))
    excluded_ids: set[str] = set(prior_a) | set(prior_b)

    sample_size = int(cfg["sample_contract"]["sample_size_each"])
    methods = {str(x["id"]): x for x in cfg["sample_contract"]["methods"]}
    outputs: dict[str, Any] = {}
    sample_sets: dict[str, set[str]] = {}

    cal_cols = ["log_p_h", "log_p_d", "log_p_a"]
    disagreement_cols = cal_cols + ["std_h", "std_d", "std_a", "range_h", "range_d", "range_a", "book_count"]
    cross_cols = cal_cols + [
        "ah_line", "abs_ah_line", "ah_home_prob", "ou_over_prob", "hda_strength",
        "hda_decisive_home_share", "ah_vs_hda_strength", "draw_under_interaction",
    ]
    hist_candidates = [
        "comp_draw", "comp_zero_zero", "comp_mean_total", "comp_std_total",
        "pair_draw_sum", "pair_recent_draw_sum", "pair_mean_total_sum", "pair_recent_total_sum",
        "pair_gf_sum", "pair_ga_sum", "rest_days_diff",
    ]
    hist_cols = [x for x in hist_candidates if x in frame.columns]

    # R41C: cross-book disagreement incremental to calibration-only consensus.
    m = methods["R41C"]
    eligible_c = frame.book_count.fillna(0).astype(int) >= int(cfg["market_contract"]["cross_book_min_complete_books"])
    sample, digest = select_method_sample(frame, eligible_c, excluded_ids, sample_size, int(m["seed"]))
    sample_sets["R41C"] = set(sample.identity_key)
    excluded_ids |= sample_sets["R41C"]
    train, policy, fit = fit_sets(frame, eligible_c)
    p_cal, r_cal = fit_model(train, policy, fit, sample, cal_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    p_dis, r_dis = fit_model(train, policy, fit, sample, disagreement_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    cmp = compare(sample.outcome.to_numpy(int), p_cal, p_dis, cfg, int(m["seed"]) + 1)
    outputs["R41C"] = {
        "sample": method_receipt(m, sample, digest, int(((frame.split == "target_pool") & eligible_c).sum())),
        "baseline_model": r_cal,
        "challenger_model": r_dis,
        "comparison": cmp,
        "scientific_verdict": "PASS_R41C_CROSS_BOOK_DISAGREEMENT_INCREMENT_FIXED200" if cmp["gate"]["all_required"] else "FAIL_R41C_CROSS_BOOK_DISAGREEMENT_NO_INCREMENT_FIXED200",
        "scope": "retrospective closing cross-book 1X2 reference; not formal PIT",
    }

    # R41D: AH+OU structural state incremental to calibration-only 1X2.
    m = methods["R41D"]
    eligible_d = (frame.book_count.fillna(0).astype(int) >= 1) & frame.has_ah_ou.fillna(False)
    sample, digest = select_method_sample(frame, eligible_d, excluded_ids, sample_size, int(m["seed"]))
    sample_sets["R41D"] = set(sample.identity_key)
    excluded_ids |= sample_sets["R41D"]
    train, policy, fit = fit_sets(frame, eligible_d)
    p_cal, r_cal = fit_model(train, policy, fit, sample, cal_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    p_cross, r_cross = fit_model(train, policy, fit, sample, cross_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    cmp = compare(sample.outcome.to_numpy(int), p_cal, p_cross, cfg, int(m["seed"]) + 1)
    outputs["R41D"] = {
        "sample": method_receipt(m, sample, digest, int(((frame.split == "target_pool") & eligible_d).sum())),
        "baseline_model": r_cal,
        "challenger_model": r_cross,
        "comparison": cmp,
        "scientific_verdict": "PASS_R41D_AH_OU_STRUCTURAL_INCREMENT_FIXED200" if cmp["gate"]["all_required"] else "FAIL_R41D_AH_OU_STRUCTURAL_NO_INCREMENT_FIXED200",
        "scope": "retrospective closing/reference HDA+AH+OU2.5; not synchronized timestamped PIT",
    }

    # R41E: pure market log-prob calibration versus raw de-vig consensus.
    m = methods["R41E"]
    eligible_e = frame.book_count.fillna(0).astype(int) >= 1
    sample, digest = select_method_sample(frame, eligible_e, excluded_ids, sample_size, int(m["seed"]))
    sample_sets["R41E"] = set(sample.identity_key)
    excluded_ids |= sample_sets["R41E"]
    train, policy, fit = fit_sets(frame, eligible_e)
    p_cal, r_cal = fit_model(train, policy, fit, sample, cal_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    raw_p = sample[["p_h", "p_d", "p_a"]].to_numpy(float)
    raw_p /= raw_p.sum(axis=1, keepdims=True)
    cmp = compare(sample.outcome.to_numpy(int), raw_p, p_cal, cfg, int(m["seed"]) + 1)
    outputs["R41E"] = {
        "sample": method_receipt(m, sample, digest, int(((frame.split == "target_pool") & eligible_e).sum())),
        "challenger_model": r_cal,
        "comparison": cmp,
        "scientific_verdict": "PASS_R41E_MARKET_CALIBRATION_INCREMENT_FIXED200" if cmp["gate"]["all_required"] else "FAIL_R41E_MARKET_CALIBRATION_NO_INCREMENT_FIXED200",
        "scope": "method-class screen of log-probability calibration; not an exact reproduction of any named paper",
    }

    # R41F: explicit dynamic draw gate, same information as generic multiclass model.
    m = methods["R41F"]
    eligible_f = eligible_d.copy()
    sample, digest = select_method_sample(frame, eligible_f, excluded_ids, sample_size, int(m["seed"]))
    sample_sets["R41F"] = set(sample.identity_key)
    excluded_ids |= sample_sets["R41F"]
    train, policy, fit = fit_sets(frame, eligible_f)
    draw_cols = cross_cols + hist_cols
    p_generic, r_generic = fit_model(train, policy, fit, sample, draw_cols, "outcome", HDA_CLASSES, cfg, base_cfg)
    p_draw_binary, r_draw = fit_model(train, policy, fit, sample, draw_cols, "is_draw", [0, 1], cfg, base_cfg)
    p_draw = p_draw_binary[:, 1]
    decisive = np.clip(p_generic[:, 0] + p_generic[:, 2], 1e-15, None)
    home_share = p_generic[:, 0] / decisive
    p_dynamic = np.column_stack([(1 - p_draw) * home_share, p_draw, (1 - p_draw) * (1 - home_share)])
    residual = float(np.max(np.abs(p_dynamic.sum(axis=1) - 1.0)))
    if residual > 1e-10:
        raise ResearchError(f"R41F probability conservation failed: {residual}")
    cmp = compare(sample.outcome.to_numpy(int), p_generic, p_dynamic, cfg, int(m["seed"]) + 1)
    outputs["R41F"] = {
        "sample": method_receipt(m, sample, digest, int(((frame.split == "target_pool") & eligible_f).sum())),
        "generic_multiclass_model": r_generic,
        "dynamic_draw_gate_model": r_draw,
        "probability_sum_max_residual": residual,
        "historical_draw_state_features": hist_cols,
        "comparison": cmp,
        "scientific_verdict": "PASS_R41F_DYNAMIC_DRAW_MASS_INCREMENT_FIXED200" if cmp["gate"]["all_required"] else "FAIL_R41F_DYNAMIC_DRAW_MASS_NO_INCREMENT_FIXED200",
        "scope": "dynamic draw-mass architecture screen; not an exact bivariate-Poisson/Skellam paper reproduction",
    }

    all_names = list(sample_sets)
    pairwise: dict[str, int] = {}
    for i, left in enumerate(all_names):
        for right in all_names[i + 1:]:
            pairwise[f"{left}_{right}"] = int(len(sample_sets[left] & sample_sets[right]))
    prior_overlap = {
        name: int(len(ids & (set(prior_a) | set(prior_b)))) for name, ids in sample_sets.items()
    }
    if any(pairwise.values()) or any(prior_overlap.values()):
        raise ResearchError(f"sample overlap detected: pairwise={pairwise}, prior={prior_overlap}")

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R41_PRIORITY_FIXED200_BATTERY_EXECUTION_COMPLETE",
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "market_materialization": {
            "identities": int(len(market)),
            "with_any_closing_independent_1x2": int((market.book_count >= 1).sum()),
            "with_three_plus_independent_closing_1x2": int((market.book_count >= int(cfg["market_contract"]["cross_book_min_complete_books"])).sum()),
            "with_ah_ou": int(market.has_ah_ou.sum()),
            "formal_PIT_claim": False,
        },
        "prior_fixed200_exclusions": {
            "R41A_identity_sha256": prior_a_sha,
            "R41B_identity_sha256": prior_b_sha,
            "R41A_rows": int(len(prior_a)),
            "R41B_rows": int(len(prior_b)),
        },
        "sample_overlap_audit": {"pairwise_battery": pairwise, "with_R41A_R41B": prior_overlap},
        "methods": outputs,
        "summary": {
            method: {
                "scientific_verdict": data["scientific_verdict"],
                "logloss_delta": data["comparison"]["delta_challenger_minus_baseline"]["logloss"],
                "logloss_p95": data["comparison"]["paired_bootstrap_logloss_delta_90"]["p95"],
                "draw_logloss_delta": data["comparison"]["challenger_draw"]["draw_logloss"] - data["comparison"]["baseline_draw"]["draw_logloss"],
                "draw_auc_baseline": data["comparison"]["baseline_draw"]["draw_auc"],
                "draw_auc_challenger": data["comparison"]["challenger_draw"]["draw_auc"],
            }
            for method, data in outputs.items()
        },
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    p = devig([2.0, 3.0, 4.0])
    assert abs(float(p.sum()) - 1.0) < 1e-12
    y = np.asarray([0, 1, 2, 1])
    probs = np.asarray([[.6,.2,.2],[.2,.6,.2],[.2,.2,.6],[.3,.4,.3]])
    d = draw_metrics(y, probs)
    assert d["actual_draws"] == 2
    assert np.isfinite(d["draw_logloss"])
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
    print(json.dumps({"status": result["status"], "summary": result["summary"], "sample_overlap_audit": result["sample_overlap_audit"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
