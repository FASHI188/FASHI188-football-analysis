#!/usr/bin/env python3
from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

TOTAL_CLASSES = list(range(8))

class ResearchError(RuntimeError):
    pass


@dataclass
class MatchState:
    n: int = 0
    gf: float = 0.0
    ga: float = 0.0
    points: float = 0.0
    draws: int = 0
    clean_sheets: int = 0
    failed_to_score: int = 0
    tail7: int = 0
    sum_total: float = 0.0
    sum_total_sq: float = 0.0
    recent: deque[tuple[int, int, float]] = field(default_factory=lambda: deque(maxlen=10))

    def update(self, gf: int, ga: int) -> None:
        total = gf + ga
        pts = 3.0 if gf > ga else 1.0 if gf == ga else 0.0
        self.n += 1
        self.gf += gf
        self.ga += ga
        self.points += pts
        self.draws += int(gf == ga)
        self.clean_sheets += int(ga == 0)
        self.failed_to_score += int(gf == 0)
        self.tail7 += int(total >= 7)
        self.sum_total += total
        self.sum_total_sq += total * total
        self.recent.append((gf, ga, pts))

    def features(self, prefix: str) -> dict[str, float]:
        out = {f"{prefix}_n_log": math.log1p(self.n)}
        if self.n:
            mean_total = self.sum_total / self.n
            var_total = max(0.0, self.sum_total_sq / self.n - mean_total * mean_total)
            out.update({
                f"{prefix}_gf": self.gf / self.n,
                f"{prefix}_ga": self.ga / self.n,
                f"{prefix}_gd": (self.gf - self.ga) / self.n,
                f"{prefix}_points": self.points / self.n,
                f"{prefix}_draw": self.draws / self.n,
                f"{prefix}_clean_sheet": self.clean_sheets / self.n,
                f"{prefix}_failed_to_score": self.failed_to_score / self.n,
                f"{prefix}_tail7": self.tail7 / self.n,
                f"{prefix}_mean_total": mean_total,
                f"{prefix}_std_total": math.sqrt(var_total),
            })
        else:
            for name in ("gf", "ga", "gd", "points", "draw", "clean_sheet", "failed_to_score", "tail7", "mean_total", "std_total"):
                out[f"{prefix}_{name}"] = np.nan
        rec = list(self.recent)
        if rec:
            out.update({
                f"{prefix}_recent_draw": float(np.mean([x[0] == x[1] for x in rec])),
                f"{prefix}_recent_gf": float(np.mean([x[0] for x in rec])),
                f"{prefix}_recent_ga": float(np.mean([x[1] for x in rec])),
                f"{prefix}_recent_points": float(np.mean([x[2] for x in rec])),
                f"{prefix}_recent_total": float(np.mean([x[0] + x[1] for x in rec])),
            })
        else:
            for name in ("draw", "gf", "ga", "points", "total"):
                out[f"{prefix}_recent_{name}"] = np.nan
        return out


@dataclass
class CompetitionState:
    n: int = 0
    home_goals: float = 0.0
    away_goals: float = 0.0
    draws: int = 0
    zero_zero: int = 0
    tail7: int = 0
    home_wins: int = 0
    sum_total: float = 0.0
    sum_total_sq: float = 0.0
    buckets: Counter[int] = field(default_factory=Counter)

    def update(self, hg: int, ag: int) -> None:
        total = hg + ag
        self.n += 1
        self.home_goals += hg
        self.away_goals += ag
        self.draws += int(hg == ag)
        self.zero_zero += int(total == 0)
        self.tail7 += int(total >= 7)
        self.home_wins += int(hg > ag)
        self.sum_total += total
        self.sum_total_sq += total * total
        self.buckets[min(total, 7)] += 1

    def features(self) -> dict[str, float]:
        out = {"comp_n_log": math.log1p(self.n)}
        if self.n:
            mean_total = self.sum_total / self.n
            var_total = max(0.0, self.sum_total_sq / self.n - mean_total * mean_total)
            out.update({
                "comp_home_goals": self.home_goals / self.n,
                "comp_away_goals": self.away_goals / self.n,
                "comp_mean_total": mean_total,
                "comp_std_total": math.sqrt(var_total),
                "comp_draw": self.draws / self.n,
                "comp_zero_zero": self.zero_zero / self.n,
                "comp_tail7": self.tail7 / self.n,
                "comp_home_win": self.home_wins / self.n,
            })
            for k in TOTAL_CLASSES:
                out[f"comp_total_bucket_{k}"] = self.buckets[k] / self.n
        else:
            for name in ("home_goals", "away_goals", "mean_total", "std_total", "draw", "zero_zero", "tail7", "home_win"):
                out[f"comp_{name}"] = np.nan
            for k in TOTAL_CLASSES:
                out[f"comp_total_bucket_{k}"] = np.nan
        return out


def add_pair_features(features: dict[str, float], left: str, right: str, names: Iterable[str]) -> None:
    for name in names:
        lv = features.get(f"{left}_{name}", np.nan)
        rv = features.get(f"{right}_{name}", np.nan)
        valid = np.isfinite(lv) and np.isfinite(rv)
        features[f"pair_{name}_sum"] = lv + rv if valid else np.nan
        features[f"pair_{name}_diff"] = lv - rv if valid else np.nan


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    required = {
        "competition_id", "season", "date_key", "home_team", "away_team", "home_goals_90",
        "away_goals_90", "result_consistent", "total_goals", "goal_difference",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ResearchError(f"ledger fields missing: {missing}")
    if not raw["result_consistent"].fillna(False).all():
        raise ResearchError("inconsistent result rows are not allowed")

    work = raw.copy()
    work["date"] = pd.to_datetime(work["date_key"], errors="raise")
    work["total_class"] = np.minimum(work["total_goals"].astype(int), 7)
    work = work.sort_values(["competition_id", "date", "home_team", "away_team", "source_file", "row_number"]).reset_index(drop=True)
    output: list[dict[str, Any]] = []

    for competition, matches in work.groupby("competition_id", sort=True):
        competition_state = CompetitionState()
        team_all: dict[str, MatchState] = defaultdict(MatchState)
        team_home: dict[str, MatchState] = defaultdict(MatchState)
        team_away: dict[str, MatchState] = defaultdict(MatchState)
        head_to_head: dict[tuple[str, str], MatchState] = defaultdict(MatchState)
        last_date: dict[str, pd.Timestamp] = {}
        season_matches: Counter[str] = Counter()

        for date, day in matches.groupby("date", sort=True):
            frozen: list[tuple[int, dict[str, float]]] = []
            for idx, row in day.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                features: dict[str, float] = {}
                features.update(competition_state.features())
                features.update(team_all[home].features("home_all"))
                features.update(team_home[home].features("home_venue"))
                features.update(team_all[away].features("away_all"))
                features.update(team_away[away].features("away_venue"))
                features.update(head_to_head[(home, away)].features("h2h_home_view"))
                pair_names = (
                    "gf", "ga", "gd", "points", "draw", "clean_sheet", "failed_to_score",
                    "tail7", "mean_total", "recent_gf", "recent_ga", "recent_points",
                    "recent_draw", "recent_total",
                )
                add_pair_features(features, "home_all", "away_all", pair_names)
                add_pair_features(features, "home_venue", "away_venue", pair_names)
                features["home_rest_days"] = float((date - last_date[home]).days) if home in last_date else np.nan
                features["away_rest_days"] = float((date - last_date[away]).days) if away in last_date else np.nan
                features["rest_days_diff"] = (
                    features["home_rest_days"] - features["away_rest_days"]
                    if np.isfinite(features["home_rest_days"]) and np.isfinite(features["away_rest_days"])
                    else np.nan
                )
                features["season_matches_before"] = float(season_matches[str(row.season)])
                features["calendar_month_sin"] = math.sin(2 * math.pi * (date.month - 1) / 12)
                features["calendar_month_cos"] = math.cos(2 * math.pi * (date.month - 1) / 12)
                frozen.append((idx, features))

            for idx, features in frozen:
                row = work.loc[idx]
                output.append({
                    "row_id": idx,
                    "competition_id": competition,
                    "season": str(row.season),
                    "date_key": row.date_key,
                    "home_team": row.home_team,
                    "away_team": row.away_team,
                    "total_class": int(row.total_class),
                    "goal_difference": int(row.goal_difference),
                    **features,
                })

            # Hard no-leakage rule: all matches on the date are updated only after every packet is frozen.
            for _, row in day.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                hg, ag = int(row.home_goals_90), int(row.away_goals_90)
                competition_state.update(hg, ag)
                team_all[home].update(hg, ag)
                team_all[away].update(ag, hg)
                team_home[home].update(hg, ag)
                team_away[away].update(ag, hg)
                head_to_head[(home, away)].update(hg, ag)
                last_date[home] = date
                last_date[away] = date
                season_matches[str(row.season)] += 1

    return pd.DataFrame(output).sort_values("row_id").reset_index(drop=True)


def select_core_features(features: pd.DataFrame) -> list[str]:
    selected: list[str] = []
    for name in features.columns:
        if name.startswith("comp_"):
            selected.append(name)
        elif name.startswith("pair_") and any(token in name for token in (
            "gf_", "ga_", "gd_", "draw_", "mean_total_", "recent_gf_", "recent_ga_", "recent_draw_", "recent_total_",
        )):
            selected.append(name)
        elif name.startswith("h2h_home_view_") and any(token in name for token in (
            "n_log", "gd", "draw", "mean_total", "recent_total",
        )):
            selected.append(name)
        elif name in {
            "home_rest_days", "away_rest_days", "rest_days_diff", "season_matches_before",
            "calendar_month_sin", "calendar_month_cos",
        }:
            selected.append(name)
    return sorted(set(selected))


def complete_seasons(raw: pd.DataFrame, config: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    ratio = float(config["split_contract"]["latest_season_completeness_ratio"])
    need = int(config["split_contract"]["complete_seasons_per_competition"])
    mapping: dict[str, list[str]] = {}
    excluded: list[str] = []
    for competition, matches in raw.groupby("competition_id"):
        counts = matches.groupby("season").size()
        ordered = matches.groupby("season")["date_key"].min().sort_values().index.astype(str).tolist()
        complete = ordered.copy()
        if len(ordered) > need:
            prior_median = float(counts.loc[ordered[:-1]].median())
            if float(counts.loc[ordered[-1]]) < ratio * prior_median:
                excluded.append(f"{competition}:{ordered[-1]}:{int(counts.loc[ordered[-1]])}")
                complete = ordered[:-1]
        if len(complete) < need:
            raise ResearchError(f"{competition} has fewer than {need} complete seasons")
        mapping[str(competition)] = complete[-need:]
    return mapping, excluded


def assign_fold(features: pd.DataFrame, seasons: dict[str, list[str]], test_position: int) -> pd.Series:
    values: list[str] = []
    for row in features[["competition_id", "season"]].itertuples(index=False):
        sequence = seasons[str(row.competition_id)]
        season = str(row.season)
        split = "excluded"
        if season in sequence[: test_position - 1]:
            split = "train"
        elif season == sequence[test_position - 1]:
            split = "policy"
        elif season == sequence[test_position]:
            split = "test"
        values.append(split)
    return pd.Series(values, index=features.index)


def audit_data_identity(raw: pd.DataFrame, config: dict[str, Any]) -> dict[str, int]:
    actual = {
        "rows": int(len(raw)),
        "competitions": int(raw.competition_id.nunique()),
        "draws": int((raw.goal_difference == 0).sum()),
        "zero_zero": int(((raw.home_goals_90 == 0) & (raw.away_goals_90 == 0)).sum()),
        "tail_7plus": int((raw.total_goals >= 7).sum()),
    }
    expected = {key: int(value) for key, value in config["expected_data_identity"].items()}
    if actual != expected:
        raise ResearchError(f"data identity mismatch: expected={expected}, actual={actual}")
    return actual
