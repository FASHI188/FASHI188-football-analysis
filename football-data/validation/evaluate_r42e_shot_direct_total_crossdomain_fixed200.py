#!/usr/bin/env python3
"""R42E: cross-domain strict-prior shot-process increment for Direct-T.

This is an exact existing-data challenge motivated by the strict daily-PIT V6.18.1c
result. It deliberately excludes the eight competitions used by V6.18.1c and first
checks whether the remaining V5.1 ledger domains provide >=200 fresh target rows after
excluding all 2,200 R41/R42 exploratory identities.

No current-match shot/SOT/corner value enters its own features. All matches on a date
are snapshotted before any shot history from that date is applied. The fixed200 is
selected only by identity hash among coverage-eligible target rows. Baseline and
challenger use the same fixed C=0.01; only the frozen 27 shot-process features differ.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict, deque
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
from evaluate_r41_priority_fixed200_battery import materialize_market, prepare_features, select_method_sample
from evaluate_r42a_dynamic_diagonal_fixed200 import reproduce_all_prior_fixed200
from evaluate_r42c_favourite_nonwin_fixed200 import reproduce_R42A_sample
from evaluate_r42d_mutual_draw_utility_fixed200 import build_counterfactual_features, reproduce_r42c_sample
from platform_core import canonical_team_name, load_aliases, parse_match_date
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42e_shot_direct_total_crossdomain_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42e_shot_direct_total_crossdomain_fixed200_status.json"
STAT_COLS = ("HS", "AS", "HST", "AST", "HC", "AC")
TOTAL_CLASSES = list(range(8))


def num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def mean(items: list[dict[str, float]], key: str) -> float:
    vals = [float(x[key]) for x in items if x.get(key) is not None]
    return float(sum(vals) / len(vals)) if vals else 0.0


def profile(history: deque[dict[str, float]], n: int) -> dict[str, float]:
    xs = list(history)[-n:]
    return {
        "sf": mean(xs, "sf"), "sa": mean(xs, "sa"),
        "sotf": mean(xs, "sotf"), "sota": mean(xs, "sota"),
        "cf": mean(xs, "cf"), "ca": mean(xs, "ca"),
    }


def freeze_features(hh: deque[dict[str, float]], ah: deque[dict[str, float]]) -> dict[str, float]:
    h5, a5 = profile(hh, 5), profile(ah, 5)
    h10, a10 = profile(hh, 10), profile(ah, 10)
    return {
        "h_sf5": h5["sf"], "h_sa5": h5["sa"], "h_sotf5": h5["sotf"], "h_sota5": h5["sota"],
        "a_sf5": a5["sf"], "a_sa5": a5["sa"], "a_sotf5": a5["sotf"], "a_sota5": a5["sota"],
        "h_sf10": h10["sf"], "h_sa10": h10["sa"], "h_sotf10": h10["sotf"], "h_sota10": h10["sota"],
        "a_sf10": a10["sf"], "a_sa10": a10["sa"], "a_sotf10": a10["sotf"], "a_sota10": a10["sota"],
        "h_cf5": h5["cf"], "h_ca5": h5["ca"], "a_cf5": a5["cf"], "a_ca5": a5["ca"],
        "h_sot_rate5": h5["sotf"] / max(h5["sf"], 1e-6),
        "a_sot_rate5": a5["sotf"] / max(a5["sf"], 1e-6),
        "expected_shots5": 0.5 * (h5["sf"] + a5["sa"] + a5["sf"] + h5["sa"]),
        "expected_sot5": 0.5 * (h5["sotf"] + a5["sota"] + a5["sotf"] + h5["sota"]),
        "expected_corners5": 0.5 * (h5["cf"] + a5["ca"] + a5["cf"] + h5["ca"]),
        "shot_balance5": (h5["sf"] - h5["sa"]) - (a5["sf"] - a5["sa"]),
        "sot_balance5": (h5["sotf"] - h5["sota"]) - (a5["sotf"] - a5["sota"]),
    }


def raw_processed_rows(competition_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    coverage = Counter()
    for cid in sorted(competition_ids):
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw0 in csv.DictReader(handle):
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if not season or not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                        continue
                    try:
                        dt = parse_match_date(raw["Date"], season)
                    except Exception:
                        continue
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    key = (cid, season, dt.date().isoformat(), home, away)
                    if key in seen:
                        continue
                    seen.add(key)
                    stats = {name: num(raw.get(name)) for name in STAT_COLS}
                    rows.append({
                        "competition_id": cid, "season": season, "date_norm": dt.date().isoformat(),
                        "home_team": home, "away_team": away, **stats,
                    })
                    coverage[f"{cid}::{season}"] += 1
    return rows, {k: int(v) for k, v in sorted(coverage.items())}


def build_shot_features(raw_rows: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        groups[(str(row["competition_id"]), str(row["season"]))].append(row)
    minimum = int(cfg["shot_feature_contract"]["minimum_prior_observed_matches_per_team"])
    records: list[dict[str, Any]] = []
    day_groups = 0
    updates = 0
    missing_update_rows = 0
    for (cid, season), rows in sorted(groups.items()):
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_day[str(row["date_norm"])].append(row)
        hist: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=10))
        for date_norm in sorted(by_day):
            day_groups += 1
            todays = sorted(by_day[date_norm], key=lambda r: (str(r["home_team"]), str(r["away_team"])))
            for row in todays:
                home, away = str(row["home_team"]), str(row["away_team"])
                if len(hist[home]) >= minimum and len(hist[away]) >= minimum:
                    rec = {
                        "competition_id": cid, "season": season, "date_norm": date_norm,
                        "home_team": home, "away_team": away,
                    }
                    rec.update(freeze_features(hist[home], hist[away]))
                    records.append(rec)
            # Strict daily PIT: current-date shot stats become history only after all snapshots.
            for row in todays:
                stats = [row.get(name) for name in STAT_COLS]
                if any(value is None for value in stats):
                    missing_update_rows += 1
                    continue
                home, away = str(row["home_team"]), str(row["away_team"])
                hs, ass, hst, ast, hc, ac = [float(x) for x in stats]
                hist[home].append({"sf": hs, "sa": ass, "sotf": hst, "sota": ast, "cf": hc, "ca": ac})
                hist[away].append({"sf": ass, "sa": hs, "sotf": ast, "sota": hst, "cf": ac, "ca": hc})
                updates += 1
    frame = pd.DataFrame(records)
    names = [str(x) for x in cfg["shot_feature_contract"]["feature_names"]]
    if not frame.empty:
        if frame.duplicated(["competition_id", "season", "date_norm", "home_team", "away_team"]).any():
            raise ResearchError("duplicate R42E shot feature identity")
        if frame[names].isna().any().any() or not np.isfinite(frame[names].to_numpy(float)).all():
            raise ResearchError("R42E shot features contain NA/nonfinite values")
    return frame, {
        "feature_rows": int(len(frame)),
        "same_day_snapshot_groups": int(day_groups),
        "observed_rows_applied_after_day_snapshot": int(updates),
        "rows_missing_shot_stats_skipped_only_for_future_history_update": int(missing_update_rows),
        "current_match_shot_values_used_in_own_features": 0,
    }


def reproduce_prior2200(raw: pd.DataFrame, seasons: dict[str, list[str]], cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    r42a_cfg = load_json(ROOT / "config" / "r42a_dynamic_diagonal_fixed200.json")
    prior1400, old_hashes = reproduce_all_prior_fixed200(raw, seasons, r42a_cfg)
    r42d_cfg = load_json(ROOT / "config" / "r42d_mutual_draw_utility_fixed200.json")
    r42a_ids, r42a_sha = reproduce_R42A_sample(raw, seasons, prior1400, r42d_cfg)
    excluded1600 = prior1400 | r42a_ids
    r42c_ids, r42c_sha = reproduce_r42c_sample(raw, seasons, excluded1600, r42d_cfg)
    excluded1800 = excluded1600 | r42c_ids
    if len(excluded1800) != 1800:
        raise ResearchError(f"expected 1800 before R42D, got {len(excluded1800)}")

    utility_names = [str(x) for x in r42d_cfg["method_contract"]["counterfactual_features"]]
    utility, _ = build_counterfactual_features(raw, utility_names)
    market = materialize_market(raw, r42d_cfg["market_contract"])
    r41d_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    frame = prepare_features(raw, market, seasons, r41d_cfg)
    frame = frame.merge(utility, on="identity_key", how="left", validate="one_to_one")
    eligible = (frame.book_count.fillna(0).astype(int) >= 1) & frame[utility_names].notna().all(axis=1)

    parent, parent_sha = select_method_sample(frame, eligible, excluded1800, 200, int(r42d_cfg["sample_contract"]["seed"]))
    expected_parent = str(cfg["sample_contract"]["exclude_R42D_parent_identity_sha256"])
    if parent_sha != expected_parent:
        raise ResearchError(f"R42D parent identity mismatch {parent_sha} != {expected_parent}")
    excluded2000 = excluded1800 | set(parent.identity_key.astype(str))

    rep_cfg = load_json(ROOT / "config" / "r42d_mutual_draw_utility_replication_fixed200.json")
    replica, replica_sha = select_method_sample(frame, eligible, excluded2000, 200, int(rep_cfg["sample_contract"]["seed"]))
    expected_rep = str(cfg["sample_contract"]["exclude_R42D_replication_identity_sha256"])
    if replica_sha != expected_rep:
        raise ResearchError(f"R42D replication identity mismatch {replica_sha} != {expected_rep}")
    excluded2200 = excluded2000 | set(replica.identity_key.astype(str))
    if len(excluded2200) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42E"]):
        raise ResearchError(f"expected prior2200 identities, got {len(excluded2200)}")
    hashes = dict(old_hashes)
    hashes.update({"R42A": r42a_sha, "R42C": r42c_sha, "R42D": parent_sha, "R42D_replication": replica_sha})
    return excluded2200, hashes


def paired_bootstrap(base: pd.DataFrame, challenger: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    n = len(base)
    rng = np.random.default_rng(int(cfg["sample_contract"]["seed"]) + 1)
    picks = rng.integers(0, n, size=(int(cfg["decision_contract"]["bootstrap_samples"]), n))
    q0, q1 = [float(x) for x in cfg["decision_contract"]["bootstrap_interval"]]
    out: dict[str, Any] = {}
    for metric in ("logloss", "brier", "rps", "top1", "top2"):
        delta = challenger[metric].to_numpy(float) - base[metric].to_numpy(float)
        means = delta[picks].mean(axis=1)
        lower_is_better = metric in {"logloss", "brier", "rps"}
        out[metric] = {
            "point_delta": float(delta.mean()),
            "bootstrap_mean": float(means.mean()),
            "p05": float(np.quantile(means, q0)),
            "p95": float(np.quantile(means, q1)),
            "probability_challenger_better": float((means < 0).mean() if lower_is_better else (means > 0).mean()),
        }
    return out


def binary_low_event_metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    truth = (y <= 2).astype(float)
    p = np.clip(probs[:, :3].sum(axis=1), 1e-15, 1 - 1e-15)
    ll = -(truth * np.log(p) + (1 - truth) * np.log(1 - p))
    return {"logloss": float(ll.mean()), "brier": float(np.mean((p - truth) ** 2)), "observed_rate": float(truth.mean()), "mean_probability": float(p.mean())}


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    excluded2200, prior_hashes = reproduce_prior2200(raw, seasons, cfg)

    feature_frame = add_identity_key(build_features(raw))
    feature_frame["split"] = split_for_latest_complete(feature_frame, seasons, cfg)
    feature_frame["date_norm"] = pd.to_datetime(feature_frame["date_key"], errors="raise").dt.date.astype(str)
    current_comps = set(feature_frame.competition_id.astype(str))
    old_comps = {str(x) for x in cfg["cross_domain_contract"]["exclude_previously_studied_v6181c_competitions"]}
    cross_comps = current_comps - old_comps

    processed, processed_counts = raw_processed_rows(current_comps)
    shot, shot_audit = build_shot_features(processed, cfg)
    names = [str(x) for x in cfg["shot_feature_contract"]["feature_names"]]
    if shot.empty:
        merged = feature_frame.copy()
        for name in names:
            merged[name] = np.nan
    else:
        merged = feature_frame.merge(
            shot,
            on=["competition_id", "season", "date_norm", "home_team", "away_team"],
            how="left",
            validate="one_to_one",
        )

    cross = merged[merged.competition_id.astype(str).isin(cross_comps)].copy()
    target = cross[(cross.split == "target_pool") & cross[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2200)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["cross_domain_contract"]["minimum_fresh_target_rows_after_prior2200_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": len(excluded2200), "hashes": prior_hashes},
        "cross_domain_coverage": {
            "current_competitions": sorted(current_comps),
            "excluded_old_v6181c_competitions": sorted(old_comps & current_comps),
            "cross_domain_competitions": sorted(cross_comps),
            "processed_identity_rows_by_competition_season": processed_counts,
            "shot_feature_build": shot_audit,
            "fresh_target_rows_after_prior2200_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "current_match_shot_values_used_in_own_features": 0,
            "current_match_shot_values_required_for_eligibility": False,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {
            **base_receipt,
            "status": "STOP_R42E_CROSSDOMAIN_SHOT_COVERAGE_LT200",
            "scientific_verdict": "DO_NOT_CONSUME_FIXED200_CROSSDOMAIN_COVERAGE_INSUFFICIENT",
            "sample": None,
            "model_fits": 0,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected_ids, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected_ids))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2200:
        raise ResearchError("R42E fixed200 identity contract failed")

    fit_rows = cross[cross.split.isin(["train", "policy"]) & cross[names].notna().all(axis=1)].copy()
    if len(fit_rows) < 500:
        raise ResearchError(f"R42E cross-domain fit rows too small: {len(fit_rows)}")
    core = select_core_features(merged)
    challenger_features = core + names
    fixed_C = float(cfg["fit_contract"]["fixed_C"])

    baseline = make_model(fixed_C, base_cfg)
    challenger = make_model(fixed_C, base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)
    y = sample.total_class.to_numpy(int)
    base_components = metric_components(y, p_base, TOTAL_CLASSES)
    ch_components = metric_components(y, p_ch, TOTAL_CLASSES)
    boot = paired_bootstrap(base_components, ch_components, cfg)
    point_base = metric_summary(base_components)
    point_ch = metric_summary(ch_components)
    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0),
        "brier_nonworse": bool(point_ch["brier"] <= point_base["brier"]),
        "rps_nonworse": bool(point_ch["rps"] <= point_base["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_total = None
    if np.any(draw_mask):
        draw_total = {
            "rows": int(draw_mask.sum()),
            "baseline_logloss": float(base_components.loc[draw_mask, "logloss"].mean()),
            "challenger_logloss": float(ch_components.loc[draw_mask, "logloss"].mean()),
            "delta": float(ch_components.loc[draw_mask, "logloss"].mean() - base_components.loc[draw_mask, "logloss"].mean()),
        }

    result = {
        **base_receipt,
        "status": "PASS_R42E_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42E_SHOT_DIRECT_TOTAL_CROSSDOMAIN_FIXED200" if gate["all_required"] else "FAIL_R42E_SHOT_DIRECT_TOTAL_CROSSDOMAIN_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": int(len(sample)), "seed": int(cfg["sample_contract"]["seed"]), "identity_sha256": sample_sha,
            "overlap_with_prior_2200": 0, "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()), "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()), "labels_used_for_identity_selection": False, "blind_claim": False,
        },
        "model_contract": {
            "fixed_C": fixed_C, "baseline_feature_count": len(core), "shot_feature_count": len(names),
            "challenger_feature_count": len(challenger_features), "scientific_parameters_selected_on_fixed200": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": point_base, "challenger": point_ch,
            "delta_challenger_minus_baseline": {k: float(point_ch[k] - point_base[k]) for k in point_base},
            "paired_bootstrap": boot,
            "low_event_T_le_2": {"baseline": binary_low_event_metrics(y, p_base), "challenger": binary_low_event_metrics(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_total,
            "gate": gate,
        },
        "interpretation_limits": [
            "This tests lagged shot/SOT/corner process information, not true xG or shot-quality distributions.",
            "The sample is retrospective/viewed and cannot authorize formal promotion by itself.",
            "Current-match shot statistics never enter their own features; same-date histories are frozen.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    h = deque([{"sf": 10.0, "sa": 8.0, "sotf": 4.0, "sota": 3.0, "cf": 5.0, "ca": 4.0}] * 3, maxlen=10)
    a = deque([{"sf": 9.0, "sa": 11.0, "sotf": 3.0, "sota": 4.0, "cf": 4.0, "ca": 5.0}] * 3, maxlen=10)
    x = freeze_features(h, a)
    assert len(x) == 27 and all(np.isfinite(list(x.values())))
    assert abs(x["expected_shots5"] - 19.0) < 1e-12
    print(json.dumps({"status": "PASS_R42E_SELF_TEST", "feature_count": len(x)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    result = run(load_json(args.config), args.out)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "cross_domain_coverage": result["cross_domain_coverage"],
        "sample": result.get("sample"),
        "metrics": result.get("metrics"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
