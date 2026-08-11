#!/usr/bin/env python3
"""R42G: prior-only team discipline + referee history -> Direct-T.

Research only. The current fixture's referee identity may be used to look up that
referee's strictly prior history, but current-match cards, fouls, goals and result never
enter their own features. Team discipline histories are last-10 prior observations;
referee history is cumulative prior officiated matches. All fixtures on a calendar date
are snapshotted before any observation from that date updates either team or referee
history.

A coverage gate runs before any model fit or fixed200 selection. If >=200 fresh eligible
target rows remain after reproducing and excluding all 2,600 prior exploratory identities,
a fresh identity-hash 200 is selected. The Direct-T baseline chooses C on historical
policy only; challenger uses the same C and differs only by the frozen 22-feature block.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import reproduce_prior2200, paired_bootstrap
from evaluate_r42f_htft_response_direct_total_replication_fixed200 import prepare_parent_frame, eligible_target
from platform_core import canonical_team_name, load_aliases, parse_match_date
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42g_discipline_referee_direct_total_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42g_discipline_referee_direct_total_fixed200_status.json"
TOTAL_CLASSES = list(range(8))
RAW_STATS = ("HF", "AF", "HY", "AY", "HR", "AR")


def num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load_discipline_rows(competition_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    coverage: dict[str, dict[str, int]] = {}
    for cid in sorted(competition_ids):
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        c = {"identity_rows": 0, "rows_with_referee": 0, "rows_with_all_discipline_stats": 0, "rows_with_referee_and_all_stats": 0}
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
                    referee = str(raw.get("Referee") or "").strip()
                    stats = {name: num(raw.get(name)) for name in RAW_STATS}
                    all_stats = all(v is not None for v in stats.values())
                    c["identity_rows"] += 1
                    c["rows_with_referee"] += int(bool(referee))
                    c["rows_with_all_discipline_stats"] += int(all_stats)
                    c["rows_with_referee_and_all_stats"] += int(bool(referee) and all_stats)
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "date_norm": dt.date().isoformat(),
                        "home_team": home,
                        "away_team": away,
                        "referee": referee,
                        **stats,
                        "discipline_observed": bool(all_stats),
                    })
        coverage[cid] = c
    return rows, coverage


def mean_hist(hist: deque[dict[str, float]], key: str) -> float:
    return float(sum(float(x[key]) for x in hist) / len(hist)) if hist else 0.0


def team_values(hist: deque[dict[str, float]], prefix: str) -> dict[str, float]:
    return {
        f"{prefix}_yellow_for_10": mean_hist(hist, "yellow_for"),
        f"{prefix}_yellow_against_10": mean_hist(hist, "yellow_against"),
        f"{prefix}_red_for_10": mean_hist(hist, "red_for"),
        f"{prefix}_red_against_10": mean_hist(hist, "red_against"),
        f"{prefix}_fouls_for_10": mean_hist(hist, "fouls_for"),
        f"{prefix}_fouls_against_10": mean_hist(hist, "fouls_against"),
    }


def referee_values(state: dict[str, float]) -> dict[str, float]:
    n = float(state.get("n", 0.0))
    if n <= 0:
        return {"ref_yellow_total_mean": 0.0, "ref_red_total_mean": 0.0, "ref_fouls_total_mean": 0.0, "ref_matches_log": 0.0}
    return {
        "ref_yellow_total_mean": float(state["yellow_total"] / n),
        "ref_red_total_mean": float(state["red_total"] / n),
        "ref_fouls_total_mean": float(state["fouls_total"] / n),
        "ref_matches_log": math.log1p(n),
    }


def build_discipline_features(raw_rows: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    window = int(cfg["feature_contract"]["team_history_window_matches"])
    min_team = int(cfg["coverage_gate"]["minimum_prior_observed_discipline_matches_per_team"])
    min_ref = int(cfg["coverage_gate"]["minimum_prior_matches_for_current_referee"])
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        by_comp[str(row["competition_id"])].append(row)

    outputs: list[dict[str, Any]] = []
    day_groups = 0
    update_rows = 0
    missing_update_rows = 0
    eligible_snapshots = 0

    for cid, rows in sorted(by_comp.items()):
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_day[str(row["date_norm"])].append(row)
        team_hist: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=window))
        ref_hist: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "yellow_total": 0.0, "red_total": 0.0, "fouls_total": 0.0})

        for date_norm in sorted(by_day):
            day_groups += 1
            todays = sorted(by_day[date_norm], key=lambda r: (str(r["home_team"]), str(r["away_team"]), str(r["season"])))
            for row in todays:
                home, away, referee = str(row["home_team"]), str(row["away_team"]), str(row["referee"])
                hhist, ahist = team_hist[home], team_hist[away]
                rstate = ref_hist[referee] if referee else {"n": 0.0, "yellow_total": 0.0, "red_total": 0.0, "fouls_total": 0.0}
                rec: dict[str, Any] = {
                    "competition_id": cid,
                    "season": str(row["season"]),
                    "date_norm": date_norm,
                    "home_team": home,
                    "away_team": away,
                    "discipline_team_history_ok": int(len(hhist) >= min_team and len(ahist) >= min_team),
                    "discipline_ref_history_ok": int(bool(referee) and float(rstate.get("n", 0.0)) >= min_ref),
                    "home_discipline_history_n": int(len(hhist)),
                    "away_discipline_history_n": int(len(ahist)),
                    "ref_history_n": int(float(rstate.get("n", 0.0))),
                }
                rec.update(team_values(hhist, "home"))
                rec.update(team_values(ahist, "away"))
                rec["yellow_mean_10"] = 0.5 * (rec["home_yellow_for_10"] + rec["away_yellow_for_10"])
                rec["yellow_gap_10"] = rec["home_yellow_for_10"] - rec["away_yellow_for_10"]
                rec["red_mean_10"] = 0.5 * (rec["home_red_for_10"] + rec["away_red_for_10"])
                rec["red_gap_10"] = rec["home_red_for_10"] - rec["away_red_for_10"]
                rec["fouls_mean_10"] = 0.5 * (rec["home_fouls_for_10"] + rec["away_fouls_for_10"])
                rec["fouls_gap_10"] = rec["home_fouls_for_10"] - rec["away_fouls_for_10"]
                rec.update(referee_values(rstate))
                eligible_snapshots += int(rec["discipline_team_history_ok"] and rec["discipline_ref_history_ok"])
                outputs.append(rec)

            # Strict daily freeze: the whole date is predicted before discipline/referee history updates.
            for row in todays:
                if not bool(row["discipline_observed"]):
                    missing_update_rows += 1
                    continue
                home, away, referee = str(row["home_team"]), str(row["away_team"]), str(row["referee"])
                hf, af, hy, ay, hr, ar = [float(row[k]) for k in RAW_STATS]
                team_hist[home].append({"yellow_for": hy, "yellow_against": ay, "red_for": hr, "red_against": ar, "fouls_for": hf, "fouls_against": af})
                team_hist[away].append({"yellow_for": ay, "yellow_against": hy, "red_for": ar, "red_against": hr, "fouls_for": af, "fouls_against": hf})
                if referee:
                    state = ref_hist[referee]
                    state["n"] += 1.0
                    state["yellow_total"] += hy + ay
                    state["red_total"] += hr + ar
                    state["fouls_total"] += hf + af
                update_rows += 1

    frame = pd.DataFrame(outputs)
    names = [str(x) for x in cfg["feature_contract"]["feature_names"]]
    if not frame.empty:
        keys = ["competition_id", "season", "date_norm", "home_team", "away_team"]
        if frame.duplicated(keys).any():
            raise ResearchError("duplicate R42G discipline feature identity")
        if frame[names].isna().any().any() or not np.isfinite(frame[names].to_numpy(float)).all():
            raise ResearchError("R42G discipline features contain NA/nonfinite")
    return frame, {
        "feature_rows": int(len(frame)),
        "same_day_snapshot_groups": int(day_groups),
        "observed_rows_applied_after_day_snapshot": int(update_rows),
        "rows_missing_discipline_stats_skipped_only_for_future_history_update": int(missing_update_rows),
        "snapshots_meeting_team_and_ref_history_gate": int(eligible_snapshots),
        "current_match_cards_fouls_or_result_used_in_own_features": 0,
    }


def reproduce_prior2600(raw: pd.DataFrame, seasons: dict[str, list[str]], cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    excluded2200, hashes = reproduce_prior2200(raw, seasons, cfg)
    if len(excluded2200) != 2200:
        raise ResearchError(f"expected prior2200, got {len(excluded2200)}")

    parent_cfg = load_json(ROOT / "config" / "r42f_htft_response_direct_total_fixed200.json")
    parent_frame, parent_names, _ = prepare_parent_frame(raw, seasons, parent_cfg)
    target = eligible_target(parent_frame, parent_names, parent_cfg)
    parent_pool = target[~target.identity_key.astype(str).isin(excluded2200)].copy()
    parent_ids, parent_sha = select_fixed_identities(parent_pool, 200, int(parent_cfg["sample_contract"]["seed"]))
    expected_parent = str(cfg["sample_contract"]["exclude_R42F_parent_identity_sha256"])
    if parent_sha != expected_parent:
        raise ResearchError(f"R42F parent identity mismatch {parent_sha} != {expected_parent}")
    excluded2400 = excluded2200 | set(parent_ids)

    rep_cfg = load_json(ROOT / "config" / "r42f_htft_response_direct_total_replication_fixed200.json")
    rep_pool = target[~target.identity_key.astype(str).isin(excluded2400)].copy()
    rep_ids, rep_sha = select_fixed_identities(rep_pool, 200, int(rep_cfg["sample_contract"]["seed"]))
    expected_rep = str(cfg["sample_contract"]["exclude_R42F_replication_identity_sha256"])
    if rep_sha != expected_rep:
        raise ResearchError(f"R42F replication identity mismatch {rep_sha} != {expected_rep}")
    excluded2600 = excluded2400 | set(rep_ids)
    if len(excluded2600) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42G"]):
        raise ResearchError(f"expected prior2600 identities, got {len(excluded2600)}")
    out_hashes = dict(hashes)
    out_hashes.update({"R42F_parent": parent_sha, "R42F_replication": rep_sha})
    return excluded2600, out_hashes


def tail_binary(y: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    truth = (y >= 4).astype(float)
    p = np.clip(probs[:, 4:].sum(axis=1), 1e-15, 1 - 1e-15)
    ll = -(truth * np.log(p) + (1 - truth) * np.log(1 - p))
    return {"logloss": float(ll.mean()), "brier": float(np.mean((p - truth) ** 2)), "observed_rate": float(truth.mean()), "mean_probability": float(p.mean())}


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    excluded2600, prior_hashes = reproduce_prior2600(raw, seasons, cfg)

    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    features["date_norm"] = pd.to_datetime(features["date_key"], errors="raise").dt.date.astype(str)
    competitions = set(features.competition_id.astype(str))

    drows, source_cov = load_discipline_rows(competitions)
    discipline, discipline_audit = build_discipline_features(drows, cfg)
    names = [str(x) for x in cfg["feature_contract"]["feature_names"]]
    if discipline.empty:
        merged = features.copy()
        for name in names:
            merged[name] = np.nan
        merged["discipline_team_history_ok"] = 0
        merged["discipline_ref_history_ok"] = 0
    else:
        merged = features.merge(discipline, on=["competition_id", "season", "date_norm", "home_team", "away_team"], how="left", validate="one_to_one")

    target = merged[
        (merged.split == "target_pool")
        & merged[names].notna().all(axis=1)
        & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)
        & (merged.discipline_ref_history_ok.fillna(0).astype(int) == 1)
    ].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded2600)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior2600_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded2600)), "hashes": prior_hashes},
        "coverage": {
            "raw_source_by_competition": source_cov,
            "discipline_feature_build": discipline_audit,
            "fresh_target_rows_after_prior2600_exclusion": int(len(fresh)),
            "fresh_target_rows_by_competition": coverage_by_comp,
            "minimum_required": minimum,
        },
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "current_match_cards_fouls_or_result_used_in_own_features": 0,
            "current_referee_identity_used_only_for_prior_history_lookup": True,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }

    if len(fresh) < minimum:
        result = {**base_receipt, "status": "STOP_R42G_DISCIPLINE_REFEREE_COVERAGE_LT200", "scientific_verdict": "DO_NOT_CONSUME_FIXED200_DISCIPLINE_REFEREE_COVERAGE_INSUFFICIENT", "sample": None, "model_fits": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    selected, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(selected))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded2600:
        raise ResearchError("R42G sample identity contract failed")

    fit_rows = merged[
        merged.split.isin(["train", "policy"])
        & merged[names].notna().all(axis=1)
        & (merged.discipline_team_history_ok.fillna(0).astype(int) == 1)
        & (merged.discipline_ref_history_ok.fillna(0).astype(int) == 1)
    ].copy()
    train = fit_rows[fit_rows.split == "train"].copy()
    policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy), len(fit_rows)) == 0:
        raise ResearchError("empty R42G fit split")

    core = select_core_features(merged)
    selected_C, baseline_policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42G baseline policy selected C outside preregistered grid: {selected_C}")
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
        "status": "PASS_R42G_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42G_DISCIPLINE_REFEREE_DIRECT_TOTAL_FIXED200" if gate["all_required"] else "FAIL_R42G_DISCIPLINE_REFEREE_DIRECT_TOTAL_NO_INCREMENT_FIXED200",
        "sample": {
            "rows": 200, "seed": int(cfg["sample_contract"]["seed"]), "identity_sha256": sample_sha,
            "overlap_with_prior_2600": 0, "competitions_represented": int(sample.competition_id.nunique()),
            "competition_counts": {str(k): int(v) for k, v in sample.groupby("competition_id").size().sort_index().items()},
            "date_min": str(sample.date_key.min()), "date_max": str(sample.date_key.max()),
            "actual_total_bucket_counts": {str(k): int(v) for k, v in sample.total_class.value_counts().sort_index().items()},
            "actual_draw_rows": int(draw_mask.sum()), "labels_used_for_identity_selection": False, "blind_claim": False,
        },
        "model_contract": {
            "baseline_policy_selected_C": float(selected_C), "baseline_policy_grid": baseline_policy_grid,
            "same_C_used_by_challenger": True, "baseline_feature_count": int(len(core)),
            "discipline_referee_feature_count": int(len(names)), "challenger_feature_count": int(len(challenger_features)),
            "scientific_parameters_selected_on_fixed200": 0,
            "baseline_max_solver_iterations": int(np.max(baseline.named_steps["model"].n_iter_)),
            "challenger_max_solver_iterations": int(np.max(challenger.named_steps["model"].n_iter_)),
            "baseline_probability_sum_max_residual": float(np.max(np.abs(p_base.sum(axis=1) - 1.0))),
            "challenger_probability_sum_max_residual": float(np.max(np.abs(p_ch.sum(axis=1) - 1.0))),
        },
        "metrics": {
            "baseline": base_metrics, "challenger": ch_metrics,
            "delta_challenger_minus_baseline": {k: float(ch_metrics[k] - base_metrics[k]) for k in base_metrics},
            "paired_bootstrap": boot,
            "tail_T_ge_4": {"baseline": tail_binary(y, p_base), "challenger": tail_binary(y, p_ch)},
            "actual_draw_subset_total_logloss": draw_diag,
            "gate": gate,
        },
        "interpretation_limits": [
            "This is a retrospective current-referee-identity study; referee announcement timestamp is not proven formal PIT.",
            "Current-match cards/fouls/results are never used in their own features.",
            "A PASS would justify replication only, not promotion or a Draw-solution claim.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    h = deque([{"yellow_for": 2.0, "yellow_against": 1.0, "red_for": 0.1, "red_against": 0.0, "fouls_for": 10.0, "fouls_against": 9.0}] * 3, maxlen=10)
    a = deque([{"yellow_for": 1.0, "yellow_against": 2.0, "red_for": 0.0, "red_against": 0.1, "fouls_for": 9.0, "fouls_against": 10.0}] * 3, maxlen=10)
    x = {}; x.update(team_values(h, "home")); x.update(team_values(a, "away"))
    r = referee_values({"n": 4.0, "yellow_total": 16.0, "red_total": 1.0, "fouls_total": 80.0})
    assert abs(x["home_yellow_for_10"] - 2.0) < 1e-12
    assert abs(r["ref_yellow_total_mean"] - 4.0) < 1e-12
    assert all(np.isfinite(float(v)) for v in {**x, **r}.values())
    print(json.dumps({"status": "PASS_R42G_SELF_TEST"}))


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
