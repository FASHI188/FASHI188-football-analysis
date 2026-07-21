#!/usr/bin/env python3
"""Retrospective AH comparison for same-day-safe V5.0.1 dynamic-state shadows.

The source lines are Football-Data historical AH fields without original quote timestamps.
They are retrospective market references only: no formal snapshot, EV or promotion authority.
"""
from __future__ import annotations

import csv
import io
import math
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import bayesian_dynamic_state_oof_v500 as base
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    atomic_write_json,
    load_json,
    normalize_team_token,
    read_processed_matches,
    score_matrix_rows,
)
from retrospective_ah_direction_baseline_v470 import _home_payoff, _line_from_row, _parse_date

ROOT = Path(__file__).resolve().parents[1]
SECOND_STAGE = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v501_status.json"
SECOND_STAGE_DIR = ROOT / "manifests" / "bayesian_dynamic_state_second_stage_v501"
OUT = ROOT / "manifests" / "retrospective_ah_dynamic_state_v501_status.json"

SOURCE_CODES = {
    "ENG_PremierLeague": "E0",
    "GER_Bundesliga": "D1",
    "ITA_SerieA": "I1",
    "FRA_Ligue1": "F1",
    "ESP_LaLiga": "SP1",
    "POR_PrimeiraLiga": "P1",
    "NED_Eredivisie": "N1",
    "SCO_Premiership": "SC0",
}
SEASONS = ("2024/25", "2025/26")
EPS = 1e-12


def _folder(season: str) -> str:
    start = int(season[:4])
    return f"{str(start)[-2:]}{str(start + 1)[-2:]}"


def _url(season: str, code: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/{_folder(season)}/{code}.csv"


def _download(season: str, code: str) -> list[dict[str, str]]:
    request = urllib.request.Request(
        _url(season, code),
        headers={"User-Agent": "Mozilla/5.0 football-research-audit"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))


def _expected_home_payoff(matrix, line: float) -> float:
    return sum(float(probability) * _home_payoff(home, away, line) for home, away, probability in score_matrix_rows(matrix))


def _profile(profile_id: str) -> dict[str, Any]:
    for profile in base.PROFILES:
        if profile["id"] == profile_id:
            return profile
    raise PlatformError(f"unknown frozen profile {profile_id!r}")


def _line_lookup(season: str, code: str) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    rows = _download(season, code)
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    field_counts: dict[str, int] = {}
    for row in rows:
        if not row.get("Date") or not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        try:
            date = _parse_date(row["Date"])
        except PlatformError:
            continue
        line, field = _line_from_row(row)
        if line is None or field is None:
            continue
        key = (
            date.date().isoformat(),
            normalize_team_token(row["HomeTeam"]),
            normalize_team_token(row["AwayTeam"]),
        )
        lookup[key] = {"line": float(line), "field": field}
        field_counts[field] = field_counts.get(field, 0) + 1
    return lookup, {
        "source_url": _url(season, code),
        "source_rows": len(rows),
        "rows_with_line": len(lookup),
        "line_field_usage": field_counts,
    }


def _settlement(matrix, line: float, match) -> dict[str, Any]:
    edge = _expected_home_payoff(matrix, line)
    if abs(edge) <= EPS:
        return {"abstain": True, "edge": edge, "selected_payoff": None, "picked_home": None}
    picked_home = edge > 0.0
    actual_home = _home_payoff(int(match.home_goals), int(match.away_goals), line)
    selected = actual_home if picked_home else -actual_home
    return {
        "abstain": False,
        "edge": edge,
        "selected_payoff": selected,
        "picked_home": picked_home,
    }


def _summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    payoffs = [float(row[f"{prefix}_payoff"]) for row in rows if row[f"{prefix}_payoff"] is not None]
    wins = sum(value > EPS for value in payoffs)
    pushes = sum(abs(value) <= EPS for value in payoffs)
    losses = sum(value < -EPS for value in payoffs)
    decided = wins + losses
    return {
        "wins": wins,
        "pushes": pushes,
        "losses": losses,
        "abstains": len(rows) - len(payoffs),
        "decided_count_excluding_pushes": decided,
        "hit_rate_excluding_pushes": wins / decided if decided else None,
        "non_loss_rate_including_pushes": (wins + pushes) / len(payoffs) if payoffs else None,
        "mean_settlement_payoff": mean(payoffs) if payoffs else None,
    }


def _domain(competition_id: str, profile_id: str, code: str) -> dict[str, Any]:
    profile = _profile(profile_id)
    formal_report = load_json(base.REPORT_ROOT / f"{competition_id}.json")
    all_matches = read_processed_matches(competition_id)
    all_rows: list[dict[str, Any]] = []
    season_reports = []

    for season in SEASONS:
        line_lookup, source_audit = _line_lookup(season, code)
        fold = base._fold_for_season(formal_report, season)
        selected_parameters = fold.get("selected_parameters")
        if not isinstance(selected_parameters, dict):
            raise PlatformError(f"missing frozen formal parameters {competition_id} {season}")
        temperature, calibration_mode = base._target_season_temperature(competition_id, season)
        prior_home, prior_away, prior_count = base._prior_league_rates(all_matches, season)
        target_matches = sorted(
            [match for match in all_matches if str(match.season) == season],
            key=lambda match: (match.date, match.home_team, match.away_team),
        )
        states: dict[str, Any] = {}
        league = {
            "home_alpha": prior_home * float(profile["league_prior_matches"]),
            "home_beta": float(profile["league_prior_matches"]),
            "away_alpha": prior_away * float(profile["league_prior_matches"]),
            "away_beta": float(profile["league_prior_matches"]),
        }
        by_day: dict[str, list[Any]] = defaultdict(list)
        for match in target_matches:
            by_day[match.date.date().isoformat()].append(match)
        season_rows: list[dict[str, Any]] = []

        for day in sorted(by_day):
            day_matches = sorted(by_day[day], key=lambda match: (match.date, match.home_team, match.away_team))
            for match in day_matches:
                key = (
                    match.date.date().isoformat(),
                    normalize_team_token(match.home_team),
                    normalize_team_token(match.away_team),
                )
                line_item = line_lookup.get(key)
                if line_item is None:
                    continue
                try:
                    baseline = base._predict_from_loaded_matches(
                        all_matches,
                        match.home_team,
                        match.away_team,
                        match.date,
                        season,
                        selected_parameters,
                    )
                except PlatformError:
                    continue
                if abs(temperature - 1.0) > EPS:
                    baseline = temperature_scale_matrix(baseline, temperature)
                league_home = league["home_alpha"] / league["home_beta"]
                league_away = league["away_alpha"] / league["away_beta"]
                dynamic_home, dynamic_away, _ = base._dynamic_rates(
                    states,
                    match.home_team,
                    match.away_team,
                    match.date,
                    league_home,
                    league_away,
                    profile,
                )
                candidate, audit = base._candidate_from_baseline(baseline, dynamic_home, dynamic_away, profile)
                baseline_settlement = _settlement(baseline, float(line_item["line"]), match)
                candidate_settlement = _settlement(candidate, float(line_item["line"]), match)
                row = {
                    "season": season,
                    "date": match.date.date().isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "line": float(line_item["line"]),
                    "line_field": line_item["field"],
                    "baseline_payoff": baseline_settlement["selected_payoff"],
                    "candidate_payoff": candidate_settlement["selected_payoff"],
                    "baseline_picked_home": baseline_settlement["picked_home"],
                    "candidate_picked_home": candidate_settlement["picked_home"],
                    "baseline_edge": baseline_settlement["edge"],
                    "candidate_edge": candidate_settlement["edge"],
                    "probability_sum_residual": audit["probability_sum_residual"],
                    "total_marginal_residual": audit["max_total_marginal_residual"],
                }
                season_rows.append(row)
                all_rows.append(row)

            for match in day_matches:
                league_home = league["home_alpha"] / league["home_beta"]
                league_away = league["away_alpha"] / league["away_beta"]
                base._update_states(
                    states,
                    match.home_team,
                    match.away_team,
                    match.date,
                    int(match.home_goals),
                    int(match.away_goals),
                    league_home,
                    league_away,
                    profile,
                )
                league["home_alpha"] += int(match.home_goals)
                league["home_beta"] += 1.0
                league["away_alpha"] += int(match.away_goals)
                league["away_beta"] += 1.0

        season_reports.append({
            "season": season,
            "profile": profile_id,
            "source": source_audit,
            "prior_league_match_count": prior_count,
            "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
            "matched_model_rows_with_ah_line": len(season_rows),
            "baseline": _summary(season_rows, "baseline"),
            "candidate": _summary(season_rows, "candidate"),
            "direction_changed_count": sum(
                row["baseline_picked_home"] is not None
                and row["candidate_picked_home"] is not None
                and row["baseline_picked_home"] != row["candidate_picked_home"]
                for row in season_rows
            ),
        })

    comparable = [
        row for row in all_rows
        if row["baseline_payoff"] is not None and row["candidate_payoff"] is not None
    ]
    candidate_better = sum(float(row["candidate_payoff"]) > float(row["baseline_payoff"]) + EPS for row in comparable)
    candidate_worse = sum(float(row["candidate_payoff"]) < float(row["baseline_payoff"]) - EPS for row in comparable)
    candidate_same = len(comparable) - candidate_better - candidate_worse
    baseline_summary = _summary(all_rows, "baseline")
    candidate_summary = _summary(all_rows, "candidate")
    hit_diff = (
        candidate_summary["hit_rate_excluding_pushes"] - baseline_summary["hit_rate_excluding_pushes"]
        if candidate_summary["hit_rate_excluding_pushes"] is not None and baseline_summary["hit_rate_excluding_pushes"] is not None
        else None
    )
    payoff_diff = (
        candidate_summary["mean_settlement_payoff"] - baseline_summary["mean_settlement_payoff"]
        if candidate_summary["mean_settlement_payoff"] is not None and baseline_summary["mean_settlement_payoff"] is not None
        else None
    )
    return {
        "competition_id": competition_id,
        "frozen_profile": profile_id,
        "seasons": list(SEASONS),
        "same_day_outcomes_withheld": True,
        "source_classification": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
        "original_quote_timestamp_available": False,
        "formal_market_snapshot": False,
        "formal_ev_authorized": False,
        "season_reports": season_reports,
        "aggregate": {
            "row_count": len(all_rows),
            "baseline": baseline_summary,
            "candidate": candidate_summary,
            "candidate_minus_baseline_hit_rate": hit_diff,
            "candidate_minus_baseline_mean_settlement_payoff": payoff_diff,
            "candidate_better_rows": candidate_better,
            "candidate_worse_rows": candidate_worse,
            "candidate_same_rows": candidate_same,
        },
        "max_probability_sum_residual": max((float(row["probability_sum_residual"]) for row in all_rows), default=0.0),
        "max_total_marginal_residual": max((abs(float(row["total_marginal_residual"])) for row in all_rows), default=0.0),
    }


def main() -> int:
    second_stage = load_json(SECOND_STAGE)
    if second_stage.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("second-stage receipt is not same-day-safe")
    passed = second_stage.get("second_stage_shadow_pass_ah_blocked") or []
    eligible = [competition_id for competition_id in passed if competition_id in SOURCE_CODES]
    unavailable = [competition_id for competition_id in passed if competition_id not in SOURCE_CODES]
    reports = {}
    failures = {}

    for competition_id in eligible:
        try:
            domain_receipt = load_json(SECOND_STAGE_DIR / f"{competition_id}.json")
            profile_id = str(domain_receipt.get("frozen_profile") or "")
            reports[competition_id] = _domain(competition_id, profile_id, SOURCE_CODES[competition_id])
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    research_positive = []
    research_negative = []
    for competition_id, report in reports.items():
        aggregate = report["aggregate"]
        hit_diff = aggregate["candidate_minus_baseline_hit_rate"]
        payoff_diff = aggregate["candidate_minus_baseline_mean_settlement_payoff"]
        if hit_diff is not None and payoff_diff is not None and hit_diff >= 0.0 and payoff_diff >= 0.0:
            research_positive.append(competition_id)
        else:
            research_negative.append(competition_id)

    payload = {
        "schema_version": "V5.0.1-retrospective-ah-dynamic-state-comparison-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(eligible) and not failures else "PARTIAL",
        "same_day_outcomes_withheld": True,
        "eligible_domains": eligible,
        "completed_domains": sorted(reports),
        "unsupported_no_public_ah_source": unavailable,
        "research_positive_retrospective_only": research_positive,
        "research_negative": research_negative,
        "reports": reports,
        "failures": failures,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "governance": {
            "retrospective_market_reference_only": True,
            "original_quote_timestamps_available": False,
            "formal_snapshot_count": 0,
            "formal_ev_authorized": False,
            "formal_promotion_authorized": False,
            "point_in_time_handicap_gate_remains_blocked": True,
        },
    }
    atomic_write_json(OUT, payload)
    print({"status": payload["status"], "positive": research_positive, "negative": research_negative, "unavailable": unavailable, "failures": failures})
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
