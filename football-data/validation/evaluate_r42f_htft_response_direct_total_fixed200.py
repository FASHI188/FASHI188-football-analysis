#!/usr/bin/env python3
"""R42F: prior-only HT-to-FT response features for Direct-T on one fresh fixed200.

The feature family is intentionally narrow and comes from the previously audited E3f-1A
internal PIT construction: how often each team, in prior completed matches, (a) held a
half-time lead through full time, (b) recovered a half-time deficit to at least a draw,
and (c) finished level after being level at half time. No current-match HT/FT state is
used in its own features. All matches on a calendar date are frozen before any result
from that date updates future history.

The target is Direct-T P(0..7+), not a Draw classifier. A baseline Direct-T core chooses
C on the historical policy season only; the challenger uses the exact same C and differs
only by the frozen HT-to-FT feature block. A fresh fixed200 is identity-selected after
excluding the already-consumed 2,200 R41/R42 identities.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
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
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import reproduce_prior2200, paired_bootstrap, binary_low_event_metrics
from platform_core import canonical_team_name, load_aliases, parse_match_date
from v510_historical_structure_features_r1 import (
    ResearchError,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42f_htft_response_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load_ht_rows(competition_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    coverage: dict[str, dict[str, int]] = {}
    for cid in sorted(competition_ids):
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        totals = {"identity_rows": 0, "rows_with_ht_and_ft": 0}
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
                    totals["identity_rows"] += 1
                    hthg, htag = num(raw.get("HTHG")), num(raw.get("HTAG"))
                    fthg, ftag = num(raw.get("FTHG")), num(raw.get("FTAG"))
                    if fthg is None or ftag is None:
                        fthg, ftag = num(raw.get("home_goals")), num(raw.get("away_goals"))
                    valid = all(v is not None for v in (hthg, htag, fthg, ftag))
                    totals["rows_with_ht_and_ft"] += int(valid)
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "date_norm": dt.date().isoformat(),
                        "home_team": home,
                        "away_team": away,
                        "hthg": int(hthg) if hthg is not None else None,
                        "htag": int(htag) if htag is not None else None,
                        "fthg": int(fthg) if fthg is not None else None,
                        "ftag": int(ftag) if ftag is not None else None,
                        "htft_observed": bool(valid),
                    })
        coverage[cid] = totals
    return rows, coverage


def new_state() -> dict[str, float]:
    return {"lead_n": 0.0, "lead_hold": 0.0, "trail_n": 0.0, "trail_recover": 0.0, "draw_n": 0.0, "draw_finish": 0.0}


def rate(success: float, trials: float, prior: float) -> float:
    return float(success / trials) if trials > 0 else float(prior)


def team_snapshot(state: dict[str, float], prefix: str, prior: float) -> dict[str, float]:
    return {
        f"{prefix}_lead_hold_rate": rate(state["lead_hold"], state["lead_n"], prior),
        f"{prefix}_lead_hold_trials_log": math.log1p(state["lead_n"]),
        f"{prefix}_trail_recover_rate": rate(state["trail_recover"], state["trail_n"], prior),
        f"{prefix}_trail_recover_trials_log": math.log1p(state["trail_n"]),
        f"{prefix}_draw_finish_rate": rate(state["draw_finish"], state["draw_n"], prior),
        f"{prefix}_draw_finish_trials_log": math.log1p(state["draw_n"]),
    }


def update_state(state: dict[str, float], ht_for: int, ht_against: int, ft_for: int, ft_against: int) -> None:
    if ht_for > ht_against:
        state["lead_n"] += 1.0
        state["lead_hold"] += float(ft_for > ft_against)
    elif ht_for < ht_against:
        state["trail_n"] += 1.0
        state["trail_recover"] += float(ft_for >= ft_against)
    else:
        state["draw_n"] += 1.0
        state["draw_finish"] += float(ft_for == ft_against)


def build_htft_features(raw_rows: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    prior = float(cfg["feature_contract"]["rate_prior_when_no_trials"])
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        by_comp[str(row["competition_id"])].append(row)
    outputs: list[dict[str, Any]] = []
    day_groups = 0
    updates = 0
    missing_updates = 0
    for cid, rows in sorted(by_comp.items()):
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_day[str(row["date_norm"])].append(row)
        states: dict[str, dict[str, float]] = defaultdict(new_state)
        for date_norm in sorted(by_day):
            day_groups += 1
            todays = sorted(by_day[date_norm], key=lambda r: (str(r["home_team"]), str(r["away_team"]), str(r["season"])))
            for row in todays:
                home, away = str(row["home_team"]), str(row["away_team"])
                hs, aas = states[home], states[away]
                rec = {
                    "competition_id": cid,
                    "season": str(row["season"]),
                    "date_norm": date_norm,
                    "home_team": home,
                    "away_team": away,
                    "home_state_trials_total": hs["lead_n"] + hs["trail_n"] + hs["draw_n"],
                    "away_state_trials_total": aas["lead_n"] + aas["trail_n"] + aas["draw_n"],
                }
                rec.update(team_snapshot(hs, "home", prior))
                rec.update(team_snapshot(aas, "away", prior))
                rec.update({
                    "lead_hold_gap": rec["home_lead_hold_rate"] - rec["away_lead_hold_rate"],
                    "trail_recover_gap": rec["home_trail_recover_rate"] - rec["away_trail_recover_rate"],
                    "draw_finish_gap": rec["home_draw_finish_rate"] - rec["away_draw_finish_rate"],
                    "lead_hold_mean": 0.5 * (rec["home_lead_hold_rate"] + rec["away_lead_hold_rate"]),
                    "trail_recover_mean": 0.5 * (rec["home_trail_recover_rate"] + rec["away_trail_recover_rate"]),
                    "draw_finish_mean": 0.5 * (rec["home_draw_finish_rate"] + rec["away_draw_finish_rate"]),
                })
                outputs.append(rec)

            # Hard same-day freeze: only after all snapshots may the current date update history.
            for row in todays:
                if not bool(row["htft_observed"]):
                    missing_updates += 1
                    continue
                home, away = str(row["home_team"]), str(row["away_team"])
                hthg, htag = int(row["hthg"]), int(row["htag"])
                fthg, ftag = int(row["fthg"]), int(row["ftag"])
                update_state(states[home], hthg, htag, fthg, ftag)
                update_state(states[away], htag, hthg, ftag, fthg)
                updates += 1

    frame = pd.DataFrame(outputs)
    names = [str(x) for x in cfg["feature_contract"]["feature_names"]]
    if frame.empty:
        return frame, {"feature_rows": 0, "same_day_snapshot_groups": day_groups, "observed_updates": updates, "missing_updates": missing_updates}
    keys = ["competition_id", "season", "date_norm", "home_team", "away_team"]
    if frame.duplicated(keys).any():
        raise ResearchError("duplicate R42F HTFT feature identity")
    if frame[names].isna().any().any() or not np.isfinite(frame[names].to_numpy(float)).all():
        raise ResearchError("R42F HTFT features contain NA/nonfinite")
    return frame, {
        "feature_rows": int(len(frame)),
        "same_day_snapshot_groups": int(day_groups),
        "observed_rows_applied_after_day_snapshot": int(updates),
        "rows_missing_htft_skipped_only_for_future_history_update": int(missing_updates),
        "current_match_half_time_or_full_time_used_in_own_features": 0,
    }


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    excluded2200, prior_hashes = reproduce_prior2200(raw, seasons, cfg)

    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    features["date_norm"] = pd.to_datetime(features["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(features.competition_id.astype(str))

    ht_rows, source_cov = load_ht_rows(competitions)
    htft, ht_audit = build_htft_features(ht_rows, cfg)
    names = [str(x) for x in cfg["feature_contract"]["feature_names"]]
    if htft.empty:
        merged = features.copy()
        for name in names:
            merged[name] = np.nan
        merged["home_state_trials_total"] = np.nan
        merged["away_state_trials_total"] = np.nan
    else:
        merged = features.merge(
            htft,
            on=["competition_id", "season", "date_norm", "home_team", "away_team"],
            how="left",
            validate="one_to_one",
        )

    min_trials = float(cfg["coverage_gate"]["minimum_prior_state_trials_per_team_any_state"])
    target = merged[
        (merged.split == "target_pool")
        & merged[names].notna().all(axis=1)
        & (merged.home_state_trials_total.fillna(0) >= min_trials)
        & (merged.away_state_trials_total.fillna(0) >= min_trials)
    ].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2200)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior2200_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded2200)), "hashes": prior_hashes},
        "coverage": {
            "raw_htft_source_by_competition": source_cov,
            "htft_feature_build": ht_audit,
            "fresh_target_rows_after_prior2200_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "current_match_half_time_or_full_time_used_in_own_features": 0,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {
            **base_receipt,
            "status": "STOP_R42F_HTFT_COVERAGE_LT200",
            "scientific_verdict": "DO_NOT_CONSUME_FIXED200_HTFT_COVERAGE_INSUFFICIENT",
            "sample": None,
            "model_fits": 0,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2200:
        raise ResearchError("R42F sample identity contract failed")

    fit_rows = merged[
        merged.split.isin(["train", "policy"])
        & merged[names].notna().all(axis=1)
        & (merged.home_state_trials_total.fillna(0) >= min_trials)
        & (merged.away_state_trials_total.fillna(0) >= min_trials)
    ].copy()
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty R42F fit split")

    core = select_core_features(merged)
    # Select C using baseline core and policy only, then use the exact same C in both arms.
    selected_C, baseline_policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"baseline policy selected C outside preregistered grid: {selected_C}")
    challenger_features = core + names
    baseline = make_model(float(selected_C), base_cfg)
    challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class)
    challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES)
    p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)
    y = sample.total_class.to_numpy(int)
    base_comp = metric_components(y, p_base, TOTAL_CLASSES)
    ch_comp = metric_components(y, p_ch, TOTAL_CLASSES)
    base_metrics = metric_summary(base_comp)
    ch_metrics = metric_summary(ch_comp)
    boot = paired_bootstrap(base_comp, ch_comp, cfg)
    gate = {
        "logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0),
        "brier_nonworse": bool(ch_metrics["brier"] <= base_metrics["brier"]),
        "rps_nonworse": bool(ch_metrics["rps"] <= base_metrics["rps"]),
    }
    gate["all_required"] = bool(all(gate.values()))

    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {
            "rows": int(draw_mask.sum()),
            "baseline_total_logloss": float(base_comp.loc[draw_mask, "logloss"].mean()),
            "challenger_total_logloss": float(ch_comp.loc[draw_mask, "logloss"].mean()),
            "delta": float(ch_comp.loc[draw_mask, "logloss"].mean() - base_comp.loc[draw_mask, "logloss"].mean()),
        }

    result = {
        **base_receipt,
        "status": "PASS_R42F_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42F_HTFT_RESPONSE_DIRECT_TOTAL_FIXED200" if gate["all_required"] else "FAIL_R42F_HTFT_RESPONSE_DIRECT_TOTAL_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_2200": 0,
            "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C),
            "baseline_policy_grid": baseline_policy_grid,
            "same_C_used_by_challenger": True,
            "baseline_feature_count": int(len(core)),
            "htft_feature_count": int(len(names)),
            "challenger_feature_count": int(len(challenger_features)),
            "scientific_parameters_selected_on_fixed200": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": base_metrics,
            "challenger": ch_metrics,
            "delta_challenger_minus_baseline": {k: float(ch_metrics[k] - base_metrics[k]) for k in base_metrics},
            "paired_bootstrap": boot,
            "low_event_T_le_2": {"baseline": binary_low_event_metrics(y, p_base), "challenger": binary_low_event_metrics(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "interpretation_limits": [
            "The features are coarse prior behavioral response proxies, not knowledge of the current match's half-time state.",
            "This tests Direct-T only; it does not itself predict Draw or alter conditional-D.",
            "The sample is retrospective/viewed and cannot authorize formal promotion by itself.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    s = new_state()
    update_state(s, 1, 0, 1, 0)
    update_state(s, 0, 1, 1, 1)
    update_state(s, 0, 0, 1, 1)
    x = team_snapshot(s, "home", 0.5)
    assert abs(x["home_lead_hold_rate"] - 1.0) < 1e-12
    assert abs(x["home_trail_recover_rate"] - 1.0) < 1e-12
    assert abs(x["home_draw_finish_rate"] - 1.0) < 1e-12
    assert all(np.isfinite(float(v)) for v in x.values())
    print(json.dumps({"status": "PASS_R42F_SELF_TEST", "state": s}))


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
        "coverage": result["coverage"],
        "sample": result.get("sample"),
        "model_contract": result.get("model_contract"),
        "metrics": result.get("metrics"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
