#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import _candidate_from_baseline, _metric_row
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    derive_score_marginals,
    read_processed_matches,
    score_matrix_rows,
    settle_home_handicap,
)

CONFIG = ROOT / "config" / "recent_xg_forward_shadow_v513.json"
LINK_ROOT = ROOT / "evidence" / "xg" / "understat_2025_26_linked"
OUT = ROOT / "manifests" / "recent_xg_forward_shadow_v513_status.json"
DETAIL = ROOT / "manifests" / "recent_xg_forward_shadow_v513"
EPS = 1e-15


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _date(value: str):
    return datetime.fromisoformat(str(value)).date()


def _weight(history_date: str, target_date: str, half_life_days: float) -> float:
    days = max(0, (_date(target_date) - _date(history_date)).days)
    return math.exp(-math.log(2.0) * days / max(1.0, half_life_days))


def _shrunk(numerator: float, denom: float, prior_rate: float, prior_matches: float) -> float:
    return (numerator + prior_rate * prior_matches) / max(EPS, denom + prior_matches)


def _xg_dynamic_rates(history_rows, target_date: str, home_team: str, away_team: str, state_cfg: dict[str, Any]):
    eligible = [row for row in history_rows if str(row["official_date"]) < target_date]
    if len(eligible) < int(state_cfg["minimum_league_history_matches"]):
        raise PlatformError("xG league history below minimum")

    half_life = float(state_cfg["half_life_days"])
    prior_matches = float(state_cfg["prior_matches"])
    league_w = league_home_sum = league_away_sum = 0.0
    home_rows = []
    away_rows = []

    for row in eligible:
        w = _weight(str(row["official_date"]), target_date, half_life)
        league_w += w
        league_home_sum += w * float(row["home_xg"])
        league_away_sum += w * float(row["away_xg"])
        if str(row["official_home_team"]) == home_team:
            home_rows.append((w, row))
        if str(row["official_away_team"]) == away_team:
            away_rows.append((w, row))

    minimum_team = int(state_cfg["minimum_team_venue_matches"])
    if len(home_rows) < minimum_team or len(away_rows) < minimum_team:
        raise PlatformError(
            f"xG venue history below minimum: home={len(home_rows)} away={len(away_rows)} minimum={minimum_team}"
        )

    league_home = league_home_sum / max(EPS, league_w)
    league_away = league_away_sum / max(EPS, league_w)

    home_w = sum(w for w, _ in home_rows)
    away_w = sum(w for w, _ in away_rows)
    home_xgf = _shrunk(sum(w * float(r["home_xg"]) for w, r in home_rows), home_w, league_home, prior_matches)
    home_xga = _shrunk(sum(w * float(r["away_xg"]) for w, r in home_rows), home_w, league_away, prior_matches)
    away_xgf = _shrunk(sum(w * float(r["away_xg"]) for w, r in away_rows), away_w, league_away, prior_matches)
    away_xga = _shrunk(sum(w * float(r["home_xg"]) for w, r in away_rows), away_w, league_home, prior_matches)

    dynamic_home = league_home * (home_xgf / max(EPS, league_home)) * (away_xga / max(EPS, league_home))
    dynamic_away = league_away * (away_xgf / max(EPS, league_away)) * (home_xga / max(EPS, league_away))
    low = float(state_cfg["minimum_rate"])
    high = float(state_cfg["maximum_rate"])
    dynamic_home = min(high, max(low, dynamic_home))
    dynamic_away = min(high, max(low, dynamic_away))

    return dynamic_home, dynamic_away, {
        "eligible_prior_xg_matches": len(eligible),
        "home_prior_venue_matches": len(home_rows),
        "away_prior_venue_matches": len(away_rows),
        "league_home_xg": league_home,
        "league_away_xg": league_away,
        "home_xgf_shrunk": home_xgf,
        "home_xga_shrunk": home_xga,
        "away_xgf_shrunk": away_xgf,
        "away_xga_shrunk": away_xga,
        "dynamic_home_xg_rate": dynamic_home,
        "dynamic_away_xg_rate": dynamic_away,
        "latest_history_date": max(str(row["official_date"]) for row in eligible),
        "target_date": target_date,
        "same_day_history_used": False,
    }


def _selection_objective(rows):
    return (
        mean(float(row["one_x_two_rps"]) for row in rows)
        + 0.25 * mean(float(row["one_x_two_brier"]) for row in rows)
        + 3.0 * mean(float(row["total_rps"]) for row in rows)
        + 0.02 * mean(float(row["joint_log"]) for row in rows)
    )


def _paired_summary(rows):
    metrics = [
        "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
        "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"
    ]
    out = {}
    for metric in metrics:
        base = mean(float(row[f"baseline_{metric}"]) for row in rows)
        cand = mean(float(row[f"candidate_{metric}"]) for row in rows)
        out[metric] = {"baseline": base, "candidate": cand, "candidate_minus_baseline": cand - base}
    return out


def _blocks(rows, block_size: int):
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    return [ordered[i:i + block_size] for i in range(0, len(ordered), block_size)]


def _bootstrap(rows, candidate_key: str, baseline_key: str, seed: int, draws: int, block_size: int):
    blocks = _blocks(rows, block_size)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        samples.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled))
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return {"mean_difference": point, "ci95_lower": lo, "ci95_upper": hi, "blocks": len(blocks), "draws": draws}


def _read_ah_reference(competition_id: str):
    path = ROOT / "processed" / competition_id / "2025-26.csv"
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                dt = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
                hg, ag = int(float(row["FTHG"])), int(float(row["FTAG"]))
                raw = row.get("AHCh") or row.get("AHh") or ""
                if raw == "":
                    continue
                line = float(raw)
            except Exception:
                continue
            key = (dt, str(row.get("HomeTeam") or ""), str(row.get("AwayTeam") or ""), hg, ag)
            out[key] = line
    return out


def _matrix_ah_expected_home(matrix, line: float) -> float:
    value = 0.0
    for h, a, p in score_matrix_rows(matrix):
        settlement = settle_home_handicap(h, a, line)
        value += p * (float(settlement["win"]) - float(settlement["loss"]))
    return value


def _actual_ah_payoff(hg: int, ag: int, line: float, choose_home: bool) -> float:
    settlement = settle_home_handicap(hg, ag, line)
    home_payoff = float(settlement["win"]) - float(settlement["loss"])
    return home_payoff if choose_home else -home_payoff


def _ah_key_for_match(match):
    return (match.date.date().isoformat(), match.home_team, match.away_team, int(match.home_goals), int(match.away_goals))


def _domain(competition_id: str, cfg: dict[str, Any]):
    season = "2025/26"
    report = _load_json(REPORT_ROOT / f"{competition_id}.json")
    fold = _fold_for_season(report, season)
    selected_parameters = fold.get("selected_parameters")
    if not isinstance(selected_parameters, dict):
        raise PlatformError("missing formal point-in-time parameters")

    all_matches = read_processed_matches(competition_id)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    if not matches:
        raise PlatformError("no 2025/26 processed matches")
    linked = _load_jsonl(LINK_ROOT / f"{competition_id}.jsonl")
    temperature, calibration_mode = _target_season_temperature(competition_id, season)
    profiles = cfg["profiles"]
    profile_rows = {p["id"]: [] for p in profiles}
    baseline_rows = []
    ah_reference = _read_ah_reference(competition_id)
    max_prob_residual = 0.0
    max_tilt_prob_residual = 0.0
    chronology_violations = 0
    baseline_skipped = 0
    xg_skipped = 0

    split_index = int(math.floor(len(matches) * float(cfg["chronology"]["profile_selection_fraction"])))

    for index, match in enumerate(matches):
        target_date = match.date.date().isoformat()
        try:
            baseline = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, season, selected_parameters
            )
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except Exception:
            baseline_skipped += 1
            continue

        try:
            dynamic_home, dynamic_away, xg_audit = _xg_dynamic_rates(
                linked, target_date, match.home_team, match.away_team, cfg["xg_state"]
            )
        except Exception:
            xg_skipped += 1
            continue

        if str(xg_audit["latest_history_date"]) >= target_date:
            chronology_violations += 1
            continue

        base_metrics = _metric_row(baseline, match)
        match_key = f"{competition_id}:{target_date}:{match.home_team}:{match.away_team}"
        phase = "selection" if index < split_index else "forward"
        base_row = {"match_key": match_key, "date": target_date, "phase": phase, **base_metrics}
        baseline_rows.append(base_row)

        for profile in profiles:
            candidate, tilt_audit = _candidate_from_baseline(baseline, dynamic_home, dynamic_away, profile)
            metrics = _metric_row(candidate, match)
            max_prob_residual = max(max_prob_residual, abs(float(metrics["probability_sum_residual"])))
            max_tilt_prob_residual = max(max_tilt_prob_residual, abs(float(tilt_audit["probability_sum_residual"])))
            profile_rows[profile["id"]].append({
                "match_key": match_key,
                "date": target_date,
                "phase": phase,
                "profile_id": profile["id"],
                **metrics,
                "xg_audit": xg_audit,
                "tilt_audit": tilt_audit,
            })

    selection_scores = []
    for profile in profiles:
        rows = [r for r in profile_rows[profile["id"]] if r["phase"] == "selection"]
        if len(rows) < 40:
            continue
        selection_scores.append({
            "profile_id": profile["id"],
            "selection_rows": len(rows),
            "objective": _selection_objective(rows),
        })
    if not selection_scores:
        raise PlatformError("no profile has enough chronological selection rows")
    selection_scores.sort(key=lambda item: (item["objective"], item["profile_id"]))
    selected_id = selection_scores[0]["profile_id"]

    base_map = {r["match_key"]: r for r in baseline_rows if r["phase"] == "forward"}
    cand_map = {r["match_key"]: r for r in profile_rows[selected_id] if r["phase"] == "forward"}
    keys = sorted(set(base_map) & set(cand_map))
    forward_rows = []
    ah_rows = []

    match_by_key = {
        f"{competition_id}:{m.date.date().isoformat()}:{m.home_team}:{m.away_team}": m for m in matches
    }
    for key in keys:
        base = base_map[key]
        cand = cand_map[key]
        row = {"match_key": key, "date": base["date"], "selected_profile": selected_id}
        for metric in (
            "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
            "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"
        ):
            row[f"baseline_{metric}"] = base[metric]
            row[f"candidate_{metric}"] = cand[metric]
        forward_rows.append(row)

        match = match_by_key.get(key)
        if match is not None:
            ah_line = ah_reference.get(_ah_key_for_match(match))
            if ah_line is not None:
                # Rebuild matrices from saved profile rows is intentionally avoided;
                # use expected AH direction embedded from deterministic replay below.
                pass

    if not forward_rows:
        raise PlatformError("no forward validation rows")

    pooled = _paired_summary(forward_rows)
    bcfg = cfg["bootstrap"]
    draws = int(bcfg["draws"])
    block_size = int(bcfg["block_size"])
    seed = int(bcfg["seed"])
    ci = {
        "one_x_two_brier": _bootstrap(forward_rows, "candidate_one_x_two_brier", "baseline_one_x_two_brier", seed + 1, draws, block_size),
        "one_x_two_rps": _bootstrap(forward_rows, "candidate_one_x_two_rps", "baseline_one_x_two_rps", seed + 2, draws, block_size),
        "joint_log": _bootstrap(forward_rows, "candidate_joint_log", "baseline_joint_log", seed + 3, draws, block_size),
        "total_rps": _bootstrap(forward_rows, "candidate_total_rps", "baseline_total_rps", seed + 4, draws, block_size),
    }

    gate = cfg["forward_gate"]
    minimum_rows = int(gate["minimum_forward_rows_20_team"] if len(matches) >= 350 else gate["minimum_forward_rows_18_team"])
    brier_improves = ci["one_x_two_brier"]["ci95_upper"] < 0.0
    rps_improves = ci["one_x_two_rps"]["ci95_upper"] < 0.0
    noninferior = float(gate["other_1x2_proper_score_ci_upper_noninferiority"])
    other_proper_noninferior = (
        (brier_improves and ci["one_x_two_rps"]["ci95_upper"] <= noninferior)
        or (rps_improves and ci["one_x_two_brier"]["ci95_upper"] <= noninferior)
        or (brier_improves and rps_improves)
    )
    checks = {
        "selected_profile_nonbaseline": selected_id != "baseline_zero",
        "minimum_forward_rows": len(forward_rows) >= minimum_rows,
        "at_least_one_1x2_proper_score_ci_improves": brier_improves or rps_improves,
        "other_1x2_proper_score_ci_noninferior": other_proper_noninferior,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= float(gate["joint_log_ci_upper_noninferiority"]),
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= float(gate["total_rps_ci_upper_noninferiority"]),
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate"] + 1e-12 >= pooled["one_x_two_accuracy"]["baseline"],
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_nonworse": pooled["total_top1"]["candidate"] + 1e-12 >= pooled["total_top1"]["baseline"],
        "total_top2_nonworse": pooled["total_top2"]["candidate"] + 1e-12 >= pooled["total_top2"]["baseline"],
        "probability_conservation": max(max_prob_residual, max_tilt_prob_residual) <= float(gate["probability_sum_tolerance"]),
        "chronology_no_same_day_or_future_xg": chronology_violations == 0,
    }
    pass_signal = all(checks.values())

    return {
        "schema_version": "V5.1.3-recent-xg-forward-shadow-domain-r1",
        "competition_id": competition_id,
        "season": season,
        "status": "RECENT_XG_FORWARD_SIGNAL_PASS_SHADOW_ONLY" if pass_signal else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_xg_eligible": False,
        "formal_pit_market_eligible": False,
        "processed_match_count": len(matches),
        "selection_split_index": split_index,
        "selection_fraction": float(cfg["chronology"]["profile_selection_fraction"]),
        "baseline_skipped_count": baseline_skipped,
        "xg_skipped_count": xg_skipped,
        "selected_profile": selected_id,
        "profile_selection": selection_scores,
        "forward_prediction_count": len(forward_rows),
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "max_probability_sum_residual": max(max_prob_residual, max_tilt_prob_residual),
        "chronology_violation_count": chronology_violations,
        "checks": checks,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "retrospective_ah_status": "REFERENCE_AVAILABLE_NOT_USED_FOR_FORMAL_PROMOTION",
        "policy": "2025/26-only xG shadow. Profile chosen from the first chronological 45%; final metrics use only the later 55%. Every xG feature uses prior dates only. No older-season xG, random split, target-match xG or formal probability mutation."
    }


def main() -> int:
    cfg = _load_json(CONFIG)
    DETAIL.mkdir(parents=True, exist_ok=True)
    reports = {}
    failures = {}
    for competition_id in cfg["domains"]:
        try:
            report = _domain(competition_id, cfg)
            reports[competition_id] = report
            (DETAIL / f"{competition_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    passed = [cid for cid, report in reports.items() if report["status"] == "RECENT_XG_FORWARD_SIGNAL_PASS_SHADOW_ONLY"]
    payload = {
        "schema_version": "V5.1.3-recent-xg-forward-shadow-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "requested_domains": cfg["domains"],
        "completed_domains": list(reports),
        "signal_pass_domains": passed,
        "signal_pass_count": len(passed),
        "reports": {cid: {
            "status": r["status"],
            "selected_profile": r["selected_profile"],
            "forward_prediction_count": r["forward_prediction_count"],
            "pooled_metrics": r["pooled_metrics"],
            "paired_block_bootstrap": r["paired_block_bootstrap"],
            "checks": r["checks"],
        } for cid, r in reports.items()},
        "failures": failures,
        "status": "PASS" if len(reports) == len(cfg["domains"]) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "global_interpretation": "A signal pass is shadow evidence only. Current Understat retrieval is retrospective and cannot establish historical pre-match publication timestamps, so no formal V5 promotion is authorized."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
