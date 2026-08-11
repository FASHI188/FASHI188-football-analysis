#!/usr/bin/env python3
"""R42D: counterfactual standings / mutual draw utility on one fresh fixed200.

Research only. This is not a Draw threshold or manual diagonal boost. For every match,
standings are frozen before any result from that calendar date is applied. The current
fixture is then counterfactually scored as H/D/A using points only, producing rank-state
changes for both teams. A market baseline and a market+counterfactual challenger are fit
only on historical train/policy seasons. The new fixed200 is identity-selected after
reproducing and excluding 1,800 previously consumed R41/R42 samples; its labels never
choose sample, features, C, thresholds or model form.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json
from evaluate_r41_priority_fixed200_battery import (
    HDA_CLASSES,
    compare,
    fit_model,
    materialize_market,
    prepare_features,
    select_method_sample,
)
from evaluate_r42a_dynamic_diagonal_fixed200 import reproduce_all_prior_fixed200
from evaluate_r42c_favourite_nonwin_fixed200 import add_favourite_features, reproduce_R42A_sample
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, complete_seasons

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42d_mutual_draw_utility_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42d_mutual_draw_utility_fixed200_status.json"


def rank_fraction(points: dict[str, float], team: str, overrides: dict[str, float] | None = None) -> float:
    if team not in points:
        raise ResearchError(f"team missing from standings state: {team}")
    values = dict(points)
    if overrides:
        values.update({str(k): float(v) for k, v in overrides.items()})
    if len(values) <= 1:
        return 0.0
    target = float(values[team])
    better = sum(1 for other, value in values.items() if other != team and float(value) > target)
    return float(better) / float(len(values) - 1)


def points_density(points: dict[str, float], team: str, radius: float = 3.0) -> float:
    if len(points) <= 1:
        return 0.0
    target = float(points[team])
    near = sum(1 for other, value in points.items() if other != team and abs(float(value) - target) <= radius)
    return float(near) / float(len(points) - 1)


def counterfactual_row(
    points: dict[str, float],
    games_played: dict[str, int],
    total_fixtures: dict[str, int],
    home: str,
    away: str,
) -> dict[str, float]:
    hp = float(points[home]); ap = float(points[away])
    h0 = rank_fraction(points, home)
    a0 = rank_fraction(points, away)

    scenario_h = {home: hp + 3.0, away: ap}
    scenario_d = {home: hp + 1.0, away: ap + 1.0}
    scenario_a = {home: hp, away: ap + 3.0}

    h_h = rank_fraction(points, home, scenario_h)
    h_d = rank_fraction(points, home, scenario_d)
    h_a = rank_fraction(points, home, scenario_a)
    a_h = rank_fraction(points, away, scenario_h)
    a_d = rank_fraction(points, away, scenario_d)
    a_a = rank_fraction(points, away, scenario_a)

    h_win_draw = max(0.0, h_d - h_h)
    h_draw_loss = max(0.0, h_a - h_d)
    a_win_draw = max(0.0, a_d - a_a)
    a_draw_loss = max(0.0, a_h - a_d)
    h_accept = h_draw_loss - h_win_draw
    a_accept = a_draw_loss - a_win_draw

    h_total = max(1, int(total_fixtures.get(home, 0)))
    a_total = max(1, int(total_fixtures.get(away, 0)))
    h_progress = min(1.0, float(games_played.get(home, 0)) / float(h_total))
    a_progress = min(1.0, float(games_played.get(away, 0)) / float(a_total))

    return {
        "util_progress_mean": 0.5 * (h_progress + a_progress),
        "util_progress_min": min(h_progress, a_progress),
        "util_current_rank_gap": abs(h0 - a0),
        "util_home_rank_h": h_h,
        "util_home_rank_d": h_d,
        "util_home_rank_a": h_a,
        "util_away_rank_h": a_h,
        "util_away_rank_d": a_d,
        "util_away_rank_a": a_a,
        "util_home_win_vs_draw": h_win_draw,
        "util_home_draw_vs_loss": h_draw_loss,
        "util_away_win_vs_draw": a_win_draw,
        "util_away_draw_vs_loss": a_draw_loss,
        "util_home_acceptance_margin": h_accept,
        "util_away_acceptance_margin": a_accept,
        "util_mutual_acceptance_min": min(h_accept, a_accept),
        "util_mutual_acceptance_sum": h_accept + a_accept,
        "util_mutual_acceptance_product": h_accept * a_accept,
        "util_both_draw_rank_stable": float(abs(h_d - h0) < 1e-12 and abs(a_d - a0) < 1e-12),
        "util_both_win_rank_low_gain": float(h_win_draw < 1e-12 and a_win_draw < 1e-12),
        "util_home_points_density_3": points_density(points, home, 3.0),
        "util_away_points_density_3": points_density(points, away, 3.0),
    }


def build_counterfactual_features(raw: pd.DataFrame, feature_names: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "competition_id", "season", "date_key", "home_team", "away_team",
        "home_goals_90", "away_goals_90",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ResearchError(f"R42D raw ledger missing standings columns: {missing}")

    keyed = add_identity_key(raw)
    outputs: list[dict[str, Any]] = []
    same_day_groups = 0
    update_rows = 0

    for (competition, season), group in keyed.groupby(["competition_id", "season"], sort=True):
        g = group.copy()
        g["date_key"] = g["date_key"].astype(str)
        g = g.sort_values(["date_key", "identity_key"]).reset_index(drop=True)
        teams = sorted(set(g.home_team.astype(str)) | set(g.away_team.astype(str)))
        if len(teams) < 2:
            continue
        total_fixtures: Counter[str] = Counter()
        for row in g.itertuples(index=False):
            total_fixtures[str(row.home_team)] += 1
            total_fixtures[str(row.away_team)] += 1
        points = {team: 0.0 for team in teams}
        games_played = {team: 0 for team in teams}

        for date_key, day in g.groupby("date_key", sort=True):
            same_day_groups += 1
            pending: list[Any] = []
            for row in day.sort_values("identity_key").itertuples(index=False):
                home = str(row.home_team); away = str(row.away_team)
                values = counterfactual_row(points, games_played, total_fixtures, home, away)
                record = {"identity_key": str(row.identity_key)}
                record.update(values)
                outputs.append(record)
                pending.append(row)

            # Hard same-day freeze: no result on this date updates another row on this date.
            for row in pending:
                home = str(row.home_team); away = str(row.away_team)
                hg = int(row.home_goals_90); ag = int(row.away_goals_90)
                if hg > ag:
                    hp, ap = 3.0, 0.0
                elif hg < ag:
                    hp, ap = 0.0, 3.0
                else:
                    hp, ap = 1.0, 1.0
                points[home] += hp; points[away] += ap
                games_played[home] += 1; games_played[away] += 1
                update_rows += 1

    out = pd.DataFrame(outputs)
    if out.empty or out.identity_key.duplicated().any():
        raise ResearchError("R42D counterfactual feature identity build failed")
    missing_features = sorted(set(feature_names) - set(out.columns))
    if missing_features:
        raise ResearchError(f"R42D utility features missing after build: {missing_features}")
    if out[feature_names].isna().any().any() or not np.isfinite(out[feature_names].to_numpy(float)).all():
        raise ResearchError("R42D counterfactual features contain NA/nonfinite values")
    return out, {
        "rows": int(len(out)),
        "competition_seasons": int(keyed[["competition_id", "season"]].drop_duplicates().shape[0]),
        "same_day_snapshot_groups": int(same_day_groups),
        "result_rows_applied_only_after_day_snapshot": int(update_rows),
        "future_outcome_features": 0,
        "current_match_outcome_used_before_snapshot": 0,
        "fixture_identity_used_for_total_schedule_count_only": True,
    }


def reproduce_r42c_sample(
    raw: pd.DataFrame,
    seasons: dict[str, list[str]],
    excluded1600: set[str],
    cfg: dict[str, Any],
) -> tuple[set[str], str]:
    r42c_cfg = load_json(ROOT / "config" / "r42c_favourite_nonwin_fixed200.json")
    market = materialize_market(raw, r42c_cfg["market_contract"])
    parent_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    frame = prepare_features(raw, market, seasons, parent_cfg)
    frame = add_favourite_features(frame)
    eligible = frame.book_count.fillna(0).astype(int) >= 1
    sample, digest = select_method_sample(
        frame,
        eligible,
        excluded1600,
        int(r42c_cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["exclude_R42C_seed"]),
    )
    expected = str(cfg["sample_contract"]["exclude_R42C_identity_sha256"])
    if digest != expected:
        raise ResearchError(f"R42C identity mismatch: {digest} != {expected}")
    ids = set(sample.identity_key.astype(str))
    if ids & excluded1600:
        raise ResearchError("R42C reproduction overlaps prior1600")
    return ids, digest


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)

    r42a_cfg = load_json(ROOT / "config" / "r42a_dynamic_diagonal_fixed200.json")
    prior1400, prior_hashes = reproduce_all_prior_fixed200(raw, seasons, r42a_cfg)
    r42a_ids, r42a_sha = reproduce_R42A_sample(raw, seasons, prior1400, cfg)
    excluded1600 = prior1400 | r42a_ids
    if len(excluded1600) != 1600:
        raise ResearchError(f"expected 1600 identities before R42C, got {len(excluded1600)}")
    r42c_ids, r42c_sha = reproduce_r42c_sample(raw, seasons, excluded1600, cfg)
    excluded_ids = excluded1600 | r42c_ids
    if len(excluded_ids) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42D"]):
        raise ResearchError(f"expected 1800 prior consumed rows, got {len(excluded_ids)}")

    utility_names = [str(x) for x in cfg["method_contract"]["counterfactual_features"]]
    utility, utility_audit = build_counterfactual_features(raw, utility_names)

    market = materialize_market(raw, cfg["market_contract"])
    parent_cfg = load_json(ROOT / "config" / "r41d_replication_fixed200.json")
    frame = prepare_features(raw, market, seasons, parent_cfg)
    frame = frame.merge(utility, on="identity_key", how="left", validate="one_to_one")
    missing = sorted(set(utility_names) - set(frame.columns))
    if missing:
        raise ResearchError(f"R42D merged utility features missing: {missing}")
    eligible = (frame.book_count.fillna(0).astype(int) >= 1) & frame[utility_names].notna().all(axis=1)

    sample, sample_sha = select_method_sample(
        frame,
        eligible,
        excluded_ids,
        int(cfg["sample_contract"]["sample_size"]),
        int(cfg["sample_contract"]["seed"]),
    )
    overlap = int(len(set(sample.identity_key.astype(str)) & excluded_ids))
    if overlap:
        raise ResearchError(f"R42D overlaps prior1800: {overlap}")

    eligible_frame = frame[eligible].copy()
    train = eligible_frame[eligible_frame.split == "train"].copy()
    policy = eligible_frame[eligible_frame.split == "policy"].copy()
    fit = eligible_frame[eligible_frame.split.isin(["train", "policy"])].copy()
    if min(len(train), len(policy), len(fit)) == 0:
        raise ResearchError("empty R42D fit split")

    baseline_features = [str(x) for x in cfg["method_contract"]["baseline_features"]]
    challenger_features = baseline_features + utility_names
    p_base, base_receipt = fit_model(
        train, policy, fit, sample, baseline_features, "outcome", HDA_CLASSES, cfg, base_cfg
    )
    p_challenger, challenger_receipt = fit_model(
        train, policy, fit, sample, challenger_features, "outcome", HDA_CLASSES, cfg, base_cfg
    )

    comparison = compare(
        sample.outcome.to_numpy(int),
        p_base,
        p_challenger,
        cfg,
        int(cfg["sample_contract"]["seed"]) + 1,
    )
    pass_gate = bool(comparison["gate"]["all_required"])
    verdict = (
        "PASS_R42D_MUTUAL_DRAW_UTILITY_INCREMENT_FIXED200"
        if pass_gate
        else "FAIL_R42D_MUTUAL_DRAW_UTILITY_NO_INCREMENT_FIXED200"
    )

    result = {
        "schema_version": cfg["schema_version"],
        "status": "PASS_R42D_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": verdict,
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {
            "rows": int(len(excluded_ids)),
            "R41A_through_R41D_replication_hashes": prior_hashes,
            "R42A_identity_sha256": r42a_sha,
            "R42C_identity_sha256": r42c_sha,
            "all_expected_hashes_match": True,
        },
        "sample": {
            "rows": int(len(sample)),
            "seed": int(cfg["sample_contract"]["seed"]),
            "identity_sha256": sample_sha,
            "overlap_with_prior_1800": overlap,
            "competitions_represented": int(sample.competition_id.nunique()),
            "date_min": str(sample.date_key.min()),
            "date_max": str(sample.date_key.max()),
            "actual_H": int((sample.outcome == 0).sum()),
            "actual_D": int((sample.outcome == 1).sum()),
            "actual_A": int((sample.outcome == 2).sum()),
            "labels_used_for_identity_selection": False,
            "blind_claim": False,
        },
        "counterfactual_standings": {
            "feature_names": utility_names,
            "feature_count": int(len(utility_names)),
            "build_audit": utility_audit,
            "same_day_freeze": True,
            "counterfactual_current_match_goal_difference_used": False,
            "manual_draw_utility_weight": False,
            "manual_draw_bonus": False,
            "manual_zone_weight": False,
        },
        "model_contract": {
            "baseline": base_receipt,
            "challenger": challenger_receipt,
            "challenger_feature_count": int(len(challenger_features)),
            "fixed200_feature_selection": False,
            "fixed200_threshold_selection": False,
        },
        "comparison": comparison,
        "market_boundary": {
            "scope": "retrospective closing/reference 1X2 plus prior-only standings counterfactuals",
            "formal_PIT_claim": False,
            "reason": "market quote timestamps are absent even though standings reconstruction is chronological",
        },
        "interpretation_limits": [
            "R42D tests counterfactual standings leverage, not collusion and not causal strategic intent.",
            "Only points/rank fractions from prior completed dates are used; no result from the current date enters another same-date feature snapshot.",
            "Full-season fixture identities are used only to normalize schedule progress; future fixture outcomes are never used as features.",
            "A PASS would justify replication only; it would not establish formal PIT market validity or solve the Draw problem.",
        ],
        "governance": cfg["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    points = {"A": 10.0, "B": 10.0, "C": 7.0, "D": 6.0}
    gp = {k: 8 for k in points}
    total = {k: 12 for k in points}
    row = counterfactual_row(points, gp, total, "C", "D")
    assert len(row) == 22
    assert all(np.isfinite(float(v)) for v in row.values())
    assert 0.0 <= row["util_progress_mean"] <= 1.0
    assert row["util_home_win_vs_draw"] >= 0.0
    assert row["util_away_draw_vs_loss"] >= 0.0
    assert rank_fraction(points, "A") == rank_fraction(points, "B")
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
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "sample": result["sample"],
        "comparison": result["comparison"],
        "counterfactual_standings": result["counterfactual_standings"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
