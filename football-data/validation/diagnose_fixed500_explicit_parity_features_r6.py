#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from diagnose_fixed500_existing_market_pack_t_r1 import MARKET_OU, add_identity_key, materialize_market
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, load_experiment, paired_bootstrap, sample_fixed_n
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_explicit_parity_features_r6.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_explicit_parity_features_r6_rows.csv"


@dataclass
class ParityState:
    n: int = 0
    total_even: int = 0
    gf_even: int = 0
    ga_even: int = 0
    recent: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=10))

    def update(self, gf: int, ga: int) -> None:
        self.n += 1
        self.total_even += int((gf + ga) % 2 == 0)
        self.gf_even += int(gf % 2 == 0)
        self.ga_even += int(ga % 2 == 0)
        self.recent.append((gf, ga))

    def features(self, prefix: str) -> dict[str, float]:
        out: dict[str, float] = {f"{prefix}_n_log": math.log1p(self.n)}
        if self.n:
            out[f"{prefix}_total_even"] = self.total_even / self.n
            out[f"{prefix}_gf_even"] = self.gf_even / self.n
            out[f"{prefix}_ga_even"] = self.ga_even / self.n
        else:
            out[f"{prefix}_total_even"] = np.nan
            out[f"{prefix}_gf_even"] = np.nan
            out[f"{prefix}_ga_even"] = np.nan
        rec = list(self.recent)
        if rec:
            out[f"{prefix}_recent_total_even"] = float(np.mean([(gf + ga) % 2 == 0 for gf, ga in rec]))
            out[f"{prefix}_recent_gf_even"] = float(np.mean([gf % 2 == 0 for gf, _ in rec]))
            out[f"{prefix}_recent_ga_even"] = float(np.mean([ga % 2 == 0 for _, ga in rec]))
        else:
            out[f"{prefix}_recent_total_even"] = np.nan
            out[f"{prefix}_recent_gf_even"] = np.nan
            out[f"{prefix}_recent_ga_even"] = np.nan
        return out


def same_parity_probability(p_even_a: float, p_even_b: float) -> float:
    if not np.isfinite(p_even_a) or not np.isfinite(p_even_b):
        return np.nan
    return float(p_even_a * p_even_b + (1.0 - p_even_a) * (1.0 - p_even_b))


def average_if_finite(a: float, b: float) -> float:
    if np.isfinite(a) and np.isfinite(b):
        return float((a + b) / 2.0)
    if np.isfinite(a):
        return float(a)
    if np.isfinite(b):
        return float(b)
    return np.nan


def add_match_parity_proxies(features: dict[str, float]) -> None:
    # Expected parity of home goals: home attack scoring parity blended with away defence conceding parity.
    # Expected parity of away goals: away attack scoring parity blended with home defence conceding parity.
    for scope, hp, ap in (
        ("all", "p_home_all", "p_away_all"),
        ("venue", "p_home_venue", "p_away_venue"),
    ):
        home_goal_even = average_if_finite(features.get(f"{hp}_gf_even", np.nan), features.get(f"{ap}_ga_even", np.nan))
        away_goal_even = average_if_finite(features.get(f"{ap}_gf_even", np.nan), features.get(f"{hp}_ga_even", np.nan))
        features[f"p_proxy_{scope}_home_goal_even"] = home_goal_even
        features[f"p_proxy_{scope}_away_goal_even"] = away_goal_even
        features[f"p_proxy_{scope}_total_even"] = same_parity_probability(home_goal_even, away_goal_even)

        home_goal_even_recent = average_if_finite(features.get(f"{hp}_recent_gf_even", np.nan), features.get(f"{ap}_recent_ga_even", np.nan))
        away_goal_even_recent = average_if_finite(features.get(f"{ap}_recent_gf_even", np.nan), features.get(f"{hp}_recent_ga_even", np.nan))
        features[f"p_proxy_{scope}_recent_home_goal_even"] = home_goal_even_recent
        features[f"p_proxy_{scope}_recent_away_goal_even"] = away_goal_even_recent
        features[f"p_proxy_{scope}_recent_total_even"] = same_parity_probability(home_goal_even_recent, away_goal_even_recent)

    # Direct historical total-even agreement between the two clubs.
    for scope, hp, ap in (
        ("all", "p_home_all", "p_away_all"),
        ("venue", "p_home_venue", "p_away_venue"),
    ):
        h = features.get(f"{hp}_total_even", np.nan)
        a = features.get(f"{ap}_total_even", np.nan)
        rh = features.get(f"{hp}_recent_total_even", np.nan)
        ra = features.get(f"{ap}_recent_total_even", np.nan)
        features[f"p_pair_{scope}_total_even_mean"] = average_if_finite(h, a)
        features[f"p_pair_{scope}_total_even_absdiff"] = float(abs(h-a)) if np.isfinite(h) and np.isfinite(a) else np.nan
        features[f"p_pair_{scope}_recent_total_even_mean"] = average_if_finite(rh, ra)
        features[f"p_pair_{scope}_recent_total_even_absdiff"] = float(abs(rh-ra)) if np.isfinite(rh) and np.isfinite(ra) else np.nan


def build_explicit_parity_features(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"competition_id", "season", "date_key", "home_team", "away_team", "home_goals_90", "away_goals_90", "source_file", "row_number"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ResearchError(f"R6 missing ledger fields: {missing}")
    work = raw.copy()
    work["date"] = pd.to_datetime(work.date_key, errors="raise")
    work = work.sort_values(["competition_id", "date", "home_team", "away_team", "source_file", "row_number"]).reset_index(drop=True)
    out: list[dict[str, Any]] = []

    for competition, matches in work.groupby("competition_id", sort=True):
        comp = ParityState()
        team_all: dict[str, ParityState] = defaultdict(ParityState)
        team_home: dict[str, ParityState] = defaultdict(ParityState)
        team_away: dict[str, ParityState] = defaultdict(ParityState)
        h2h: dict[tuple[str, str], ParityState] = defaultdict(ParityState)

        for date, day in matches.groupby("date", sort=True):
            frozen: list[tuple[int, dict[str, float]]] = []
            for idx, row in day.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                f: dict[str, float] = {}
                f.update(comp.features("p_comp"))
                f.update(team_all[home].features("p_home_all"))
                f.update(team_all[away].features("p_away_all"))
                f.update(team_home[home].features("p_home_venue"))
                f.update(team_away[away].features("p_away_venue"))
                f.update(h2h[(home, away)].features("p_h2h"))
                add_match_parity_proxies(f)
                frozen.append((idx, f))

            for idx, f in frozen:
                out.append({"row_id": int(idx), **f})

            # Freeze all packets on a date before any result on that date is learned.
            for _, row in day.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                hg, ag = int(row.home_goals_90), int(row.away_goals_90)
                comp.update(hg, ag)
                team_all[home].update(hg, ag)
                team_all[away].update(ag, hg)
                team_home[home].update(hg, ag)
                team_away[away].update(ag, hg)
                h2h[(home, away)].update(hg, ag)

    result = pd.DataFrame(out).sort_values("row_id").reset_index(drop=True)
    if len(result) != len(work) or result.row_id.nunique() != len(work):
        raise ResearchError("R6 explicit parity feature row identity failure")
    return result


def parity_metrics(y_parity: np.ndarray, p: np.ndarray) -> dict[str, float]:
    y = np.asarray(y_parity, int)
    pp = np.asarray(p, float)
    p_even = np.clip(pp[:, 0], 1e-15, 1 - 1e-15)
    y_even = (y == 0).astype(int)
    chosen = np.argmax(pp, axis=1)
    ll = -np.log(np.clip(pp[np.arange(len(y)), y], 1e-15, 1.0))
    brier = (p_even - y_even) ** 2 + ((1-p_even) - (1-y_even)) ** 2
    return {
        "accuracy": float(np.mean(chosen == y)),
        "logloss": float(np.mean(ll)),
        "brier": float(np.mean(brier)),
        "even_auc": float(roc_auc_score(y_even, p_even)) if len(np.unique(y_even)) == 2 else float("nan"),
        "mean_p_even": float(np.mean(p_even)),
        "actual_even_rate": float(np.mean(y_even)),
    }


def parity_components(y_parity: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y_parity, int)
    pp = np.asarray(p, float)
    p_even = np.clip(pp[:, 0], 1e-15, 1 - 1e-15)
    y_even = (y == 0).astype(int)
    return {
        "logloss": -np.log(np.clip(pp[np.arange(len(y)), y], 1e-15, 1.0)),
        "brier": (p_even-y_even)**2 + ((1-p_even)-(1-y_even))**2,
    }


def fit_parity_model(fold: pd.DataFrame, sample: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    train = fold[fold.split == "train"].copy()
    policy = fold[fold.split == "policy"].copy()
    fit = fold[fold.split.isin(["train", "policy"])].copy()
    selected_C, grid = select_C(train, policy, features, "exact_parity", [0, 1], config)
    model = make_model(selected_C, config)
    model.fit(fit[features], fit.exact_parity)
    p = align_probability(model, sample[features], [0, 1])
    return p, {
        "train_rows": int(len(train)), "policy_rows": int(len(policy)), "fit_rows": int(len(fit)),
        "feature_count": int(len(features)), "selected_C": selected_C, "policy_grid": grid,
    }


def evaluate_cohort(fold: pd.DataFrame, sample: pd.DataFrame, packs: dict[str, list[str]], config: dict[str, Any], seed_base: int) -> dict[str, Any]:
    probs: dict[str, np.ndarray] = {}; metrics: dict[str, Any] = {}; receipts: dict[str, Any] = {}; comps: dict[str, dict[str, np.ndarray]] = {}
    y = sample.exact_parity.to_numpy(int)
    for name, features in packs.items():
        p, receipt = fit_parity_model(fold, sample, features, config)
        probs[name] = p; receipts[name] = receipt; metrics[name] = parity_metrics(y, p); comps[name] = parity_components(y, p)
    boot: dict[str, Any] = {}
    baseline = list(packs)[0]
    for k, challenger in enumerate(list(packs)[1:]):
        boot[f"{challenger}_minus_{baseline}"] = {
            metric: paired_bootstrap(comps[challenger][metric] - comps[baseline][metric], 5000, seed_base + 20*k + i)
            for i, metric in enumerate(("logloss", "brier"))
        }
    return {"n": int(len(sample)), "actual_even": int(np.sum(y == 0)), "actual_odd": int(np.sum(y == 1)), "metrics": metrics, "bootstrap": boot, "receipts": receipts, "probs": probs}


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = build_features(raw)
    parity_frame = build_explicit_parity_features(raw)
    base = base.merge(parity_frame, on="row_id", how="left", validate="one_to_one")
    base = add_identity_key(base)
    base = attach_exact_total(base, raw)
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    pos = int(exp["test_position_zero_based"])
    latest = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest:
        raise ResearchError("R6 must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base, seasons, pos)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    sample = base.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("R6 fixed500 reconstruction mismatch")

    compact = [
        "p_comp_total_even",
        "p_home_all_total_even", "p_away_all_total_even",
        "p_home_all_recent_total_even", "p_away_all_recent_total_even",
        "p_home_venue_total_even", "p_away_venue_total_even",
        "p_h2h_total_even", "p_h2h_recent_total_even",
        "p_proxy_all_total_even", "p_proxy_all_recent_total_even",
        "p_proxy_venue_total_even", "p_proxy_venue_recent_total_even",
        "p_pair_all_total_even_mean", "p_pair_all_recent_total_even_mean",
        "p_pair_venue_total_even_mean", "p_pair_venue_recent_total_even_mean",
    ]
    full = sorted([c for c in parity_frame.columns if c != "row_id"])
    if not set(compact).issubset(full):
        raise ResearchError("R6 compact feature contract missing")

    full_result = evaluate_cohort(
        base,
        sample,
        {"core": core, "core_plus_compact_parity": core + compact, "core_plus_full_parity": core + full},
        config,
        900100,
    )

    market = materialize_market(raw)
    base_m = base.merge(market, on="identity_key", how="left", validate="one_to_one")
    sample_m = sample.merge(market, on="identity_key", how="left", validate="one_to_one")
    ou_mask_fit = base_m[MARKET_OU].notna().all(axis=1)
    ou_mask_sample = sample_m[MARKET_OU].notna().all(axis=1)
    fold_ou = base_m[ou_mask_fit].copy()
    sample_ou = sample_m[ou_mask_sample].copy()
    if len(sample_ou) != 220:
        raise ResearchError(f"R6 expected same 220 OU cohort, got {len(sample_ou)}")
    sync_result = evaluate_cohort(
        fold_ou,
        sample_ou,
        {
            "core": core,
            "core_plus_single_ou": core + MARKET_OU,
            "core_plus_compact_parity": core + compact,
            "core_plus_single_ou_compact_parity": core + MARKET_OU + compact,
            "core_plus_full_parity": core + full,
            "core_plus_single_ou_full_parity": core + MARKET_OU + full,
        },
        config,
        901100,
    )

    # Additional direct comparison against single OU, because R5 says parity routing is the bottleneck.
    sync_comps = {name: parity_components(sample_ou.exact_parity.to_numpy(int), p) for name, p in sync_result["probs"].items()}
    ou_boot = {}
    for k, challenger in enumerate(("core_plus_single_ou_compact_parity", "core_plus_single_ou_full_parity")):
        ou_boot[f"{challenger}_minus_core_plus_single_ou"] = {
            metric: paired_bootstrap(sync_comps[challenger][metric] - sync_comps["core_plus_single_ou"][metric], 5000, 902100 + 20*k + i)
            for i, metric in enumerate(("logloss", "brier"))
        }
    sync_result["bootstrap_vs_single_ou"] = ou_boot

    rows = sample_m[KEYS + ["match_identity", "identity_hash", "exact_total", "exact_parity"] + MARKET_OU + compact].copy()
    # Export probabilities for the same 220 cohort only where all comparison packs exist.
    sync_idx = sample_ou.index.to_list()
    for name, p in sync_result["probs"].items():
        mp_even = dict(zip(sync_idx, p[:, 0]))
        rows[f"{name}_p_even"] = [mp_even.get(i, np.nan) for i in rows.index]
    full_result.pop("probs")
    sync_result.pop("probs")

    best_sync_name = min(sync_result["metrics"], key=lambda n: sync_result["metrics"][n]["logloss"])
    best_full_name = min(full_result["metrics"], key=lambda n: full_result["metrics"][n]["logloss"])
    ou_base = sync_result["metrics"]["core_plus_single_ou"]
    best_sync = sync_result["metrics"][best_sync_name]
    if best_sync_name in ("core_plus_single_ou_compact_parity", "core_plus_single_ou_full_parity"):
        key = f"{best_sync_name}_minus_core_plus_single_ou"
        stable_vs_ou = sync_result["bootstrap_vs_single_ou"][key]["logloss"]["p95"] <= 0.0
    else:
        stable_vs_ou = False
    if stable_vs_ou and best_sync["logloss"] < ou_base["logloss"]:
        verdict = "EXPLICIT_PARITY_INFORMATION_ADDS_STABLE_SIGNAL_OVER_SINGLE_OU"
    elif best_sync["logloss"] < ou_base["logloss"]:
        verdict = "EXPLICIT_PARITY_INFORMATION_DIRECTIONAL_ONLY"
    else:
        verdict = "EXPLICIT_PARITY_HISTORY_DOES_NOT_BEAT_SINGLE_OU"

    result = {
        "schema_version": "FIXED500_EXPLICIT_PARITY_FEATURES_R6",
        "status": "COMPLETED_RESEARCH_ONLY",
        "scientific_verdict": verdict,
        "question": "Can explicit pre-match historical scoring-parity structure improve P(T even) on the same fixed500 without changing the model family?",
        "sample": {
            "parent_fixed500_n": 500,
            "parent_fixed500_identity_sha256": sample_hash,
            "full_fixed500_n": 500,
            "same_ou_cohort_n": 220,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
        },
        "feature_contract": {
            "compact_feature_count": len(compact),
            "full_feature_count": len(full),
            "compact_features": compact,
            "full_features": full,
            "same_day_freeze_before_updates": True,
            "uses_only_prior_match_scores": True,
            "uses_future_result": False,
            "model_family_unchanged": True,
            "manual_threshold": False,
        },
        "full_fixed500": full_result,
        "same_220_ou_cohort": sync_result,
        "best_models": {"full_fixed500": best_full_name, "same_220": best_sync_name},
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "interpretation_guard": {
            "retrospective_same_fixed500_already_viewed": True,
            "formal_PIT_claim": False,
            "can_authorize_promotion": False,
            "R5_oracle_parity_not_used_as_training_feature": True,
            "no_true_parity_at_inference": True,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
            "post_result_threshold_search": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "verdict": x["scientific_verdict"],
        "sample": x["sample"],
        "full500": x["full_fixed500"],
        "same220": x["same_220_ou_cohort"],
        "best_models": x["best_models"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
