from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import bayesian_dynamic_state_oof_v500 as v500  # type: ignore
from backtest_last_complete_season_all_domains_v470 import (  # type: ignore
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix  # type: ignore
from platform_core import derive_score_marginals, load_json, read_processed_matches  # type: ignore

FROZEN_V500_BLOB = "9c302506c49aa1847e60cd7896fc1a80f3b6b457"
V500_REPORT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v500"


class LegacyUnavailable(RuntimeError):
    pass


def completed_seasons(competition_id: str) -> list[str]:
    path = V500_REPORT_DIR / f"{competition_id}.json"
    if not path.exists():
        return []
    report = load_json(path)
    return [str(x) for x in report.get("completed_outer_seasons") or []]


def selected_profile(competition_id: str, season: str) -> dict[str, Any]:
    path = V500_REPORT_DIR / f"{competition_id}.json"
    report = load_json(path)
    folds = [x for x in report.get("folds") or [] if str(x.get("target_season")) == season and x.get("status") == "EVALUATED_FORWARD_FROZEN_PROFILE"]
    if len(folds) != 1:
        raise LegacyUnavailable(f"no unique frozen V500 profile for {competition_id} {season}")
    pid = str(folds[0].get("selected_profile") or "")
    profiles = [p for p in v500.PROFILES if str(p.get("id")) == pid]
    if len(profiles) != 1:
        raise LegacyUnavailable(f"V500 profile {pid!r} unavailable")
    return dict(profiles[0])


@dataclass
class SeasonRunner:
    competition_id: str
    season: str
    all_matches: list[Any]
    profile: dict[str, Any]
    selected_parameters: dict[str, Any]
    temperature: float
    states: dict[str, Any]
    league: dict[str, float]
    current_time: datetime | None = None

    @classmethod
    def build(cls, competition_id: str, season: str, all_matches: list[Any]) -> "SeasonRunner":
        profile = selected_profile(competition_id, season)
        formal_report = load_json(REPORT_ROOT / f"{competition_id}.json")
        fold = _fold_for_season(formal_report, season)
        selected_parameters = fold.get("selected_parameters")
        if not isinstance(selected_parameters, dict):
            raise LegacyUnavailable(f"missing frozen V500 base parameters for {competition_id} {season}")
        temperature, _ = _target_season_temperature(competition_id, season)
        prior_home, prior_away, _ = v500._prior_league_rates(all_matches, season)
        pm = float(profile["league_prior_matches"])
        league = {
            "home_alpha": prior_home * pm,
            "home_beta": pm,
            "away_alpha": prior_away * pm,
            "away_beta": pm,
        }
        return cls(competition_id, season, all_matches, profile, selected_parameters, temperature, {}, league)

    def predict(self, match: Any) -> dict[str, Any]:
        if str(match.season) != self.season:
            raise LegacyUnavailable("season runner mismatch")
        cutoff = match.date
        if self.current_time is not None and cutoff < self.current_time:
            raise LegacyUnavailable("legacy state time reversal")
        baseline = _predict_from_loaded_matches(
            self.all_matches, match.home_team, match.away_team, cutoff, self.season, self.selected_parameters
        )
        if abs(self.temperature - 1.0) > 1e-15:
            baseline = temperature_scale_matrix(baseline, self.temperature)
        lh = self.league["home_alpha"] / self.league["home_beta"]
        la = self.league["away_alpha"] / self.league["away_beta"]
        dyn_h, dyn_a, _ = v500._dynamic_rates(self.states, match.home_team, match.away_team, cutoff, lh, la, self.profile)
        matrix, _ = v500._candidate_from_baseline(baseline, dyn_h, dyn_a, self.profile)
        marg = derive_score_marginals(matrix)
        p = marg["1x2"]
        return {
            "engine": "V500_frozen_comparator",
            "competition_id": self.competition_id,
            "season": self.season,
            "date": cutoff.date().isoformat(),
            "home_team": match.home_team,
            "away_team": match.away_team,
            "p_home": float(p["home"]),
            "p_draw": float(p["draw"]),
            "p_away": float(p["away"]),
            "score_matrix": matrix,
        }

    def apply_batch(self, matches: list[Any]) -> None:
        if not matches:
            return
        dates = {m.date for m in matches}
        if len(dates) != 1:
            raise LegacyUnavailable("legacy batch must share cutoff")
        now = next(iter(dates))
        if self.current_time is not None and now < self.current_time:
            raise LegacyUnavailable("legacy batch time reversal")
        for match in matches:
            lh = self.league["home_alpha"] / self.league["home_beta"]
            la = self.league["away_alpha"] / self.league["away_beta"]
            v500._update_states(
                self.states, match.home_team, match.away_team, match.date,
                int(match.home_goals), int(match.away_goals), lh, la, self.profile,
            )
        for match in matches:
            self.league["home_alpha"] += int(match.home_goals)
            self.league["home_beta"] += 1.0
            self.league["away_alpha"] += int(match.away_goals)
            self.league["away_beta"] += 1.0
        self.current_time = now


class LegacyCompetition:
    def __init__(self, competition_id: str):
        self.competition_id = competition_id
        self.all_matches = read_processed_matches(competition_id)
        self.runners: dict[str, SeasonRunner] = {}

    def runner(self, season: str) -> SeasonRunner:
        if season not in self.runners:
            self.runners[season] = SeasonRunner.build(self.competition_id, season, self.all_matches)
        return self.runners[season]

    def prewarm_before(self, cutoff: datetime, allowed_seasons: set[str] | None = None) -> None:
        by: dict[tuple[str, datetime], list[Any]] = defaultdict(list)
        for match in self.all_matches:
            if match.date >= cutoff:
                continue
            if allowed_seasons is not None and str(match.season) not in allowed_seasons:
                continue
            try:
                self.runner(str(match.season))
            except Exception:
                continue
            by[(str(match.season), match.date)].append(match)
        for (season, _), batch in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            self.runner(season).apply_batch(batch)
