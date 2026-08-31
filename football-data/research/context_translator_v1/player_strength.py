from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

DIMS = (
    "shot_generation", "finishing", "chance_creation", "passing_progression", "carrying_progression",
    "possession_retention_risk", "pressing", "tackling_interception", "defensive_position_protection",
    "aerial", "set_piece", "goalkeeper_shot_stopping", "goalkeeper_sweeping", "goalkeeper_cross_claiming",
    "goalkeeper_distribution", "on_ball_contribution", "off_ball_contribution", "current_form",
)
PROHIBITED = {"market_value", "salary", "game_rating", "media_rating", "fantasy_rating", "reputation", "stars"}


class PlayerStrengthError(RuntimeError):
    pass


def _parse(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise PlayerStrengthError("event timestamp missing timezone")
    return d.astimezone(timezone.utc)


def _decay(days: float, half_life: float) -> float:
    return math.exp(-math.log(2.0) * max(days, 0.0) / half_life)


def _solve_ridge(rows: list[dict[str, float]], y: list[float], keys: list[str], ridge: float) -> dict[str, float]:
    n = len(keys)
    if not rows or n == 0:
        return {k: 0.0 for k in keys}
    a = [[0.0] * (n + 1) for _ in range(n)]
    for r, target in zip(rows, y):
        x = [r.get(k, 0.0) for k in keys]
        for i in range(n):
            a[i][n] += x[i] * target
            for j in range(n):
                a[i][j] += x[i] * x[j]
    for i in range(n):
        a[i][i] += ridge
    for col in range(n):
        pivot = max(range(col, n), key=lambda rr: abs(a[rr][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-12:
            continue
        div = a[col][col]
        a[col] = [v / div for v in a[col]]
        for rr in range(n):
            if rr == col:
                continue
            factor = a[rr][col]
            if factor:
                a[rr] = [v - factor * w for v, w in zip(a[rr], a[col])]
    return {k: a[i][n] for i, k in enumerate(keys)}


@dataclass(frozen=True)
class PlayerVector:
    player_id: str
    team_id: str
    league_id: str
    role: str
    values: dict[str, float]
    effective_exposure: float
    uncertainty: float
    state_timestamp: str
    coverage_grade: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_player_vectors(events: list[dict[str, Any]], segments: list[dict[str, Any]], *,
                            as_of: str, half_life_days: float = 180.0, ridge: float = 8.0,
                            league_strength: dict[str, float] | None = None) -> dict[str, PlayerVector]:
    cutoff = _parse(as_of)
    league_strength = league_strength or {}
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    exposure: dict[str, float] = defaultdict(float)
    meta: dict[str, tuple[str, str, str]] = {}
    for e in events:
        if PROHIBITED.intersection(e):
            raise PlayerStrengthError("prohibited player proxy present")
        required = {"player_id", "team_id", "league_id", "role", "known_at", "minutes_exposure", "possession_opportunity", "values"}
        if not required.issubset(e):
            raise PlayerStrengthError("event strength record missing fields")
        known = _parse(e["known_at"])
        if known >= cutoff:
            raise PlayerStrengthError("future/current-target event reached player estimator")
        vals = e["values"]
        if not isinstance(vals, dict) or any(k not in DIMS for k in vals):
            raise PlayerStrengthError("unknown player dimension")
        minutes = max(float(e["minutes_exposure"]), 0.0)
        poss = max(float(e["possession_opportunity"]), 1e-6)
        w = _decay((cutoff - known).total_seconds() / 86400.0, half_life_days)
        pid = str(e["player_id"])
        meta[pid] = (str(e["team_id"]), str(e["league_id"]), str(e["role"]))
        opportunity = max(minutes / 90.0, 1e-3) * poss
        exposure[pid] += w * max(minutes, 1.0)
        for dim, value in vals.items():
            agg[pid][dim] += w * float(value) / opportunity

    player_ids = sorted(meta)
    apm_rows: list[dict[str, float]] = []
    apm_y: list[float] = []
    for s in segments:
        required = {"known_at", "minutes", "impact", "home_player_ids", "away_player_ids"}
        if not required.issubset(s):
            raise PlayerStrengthError("APM segment missing fields")
        known = _parse(s["known_at"])
        if known >= cutoff:
            raise PlayerStrengthError("future/current-target segment reached APM")
        mins = max(float(s["minutes"]), 0.0)
        w = math.sqrt(max(mins, 1.0) / 90.0) * _decay((cutoff-known).total_seconds()/86400.0, half_life_days)
        row: dict[str, float] = {}
        for pid in s["home_player_ids"]:
            if pid in meta:
                row[pid] = row.get(pid, 0.0) + w
        for pid in s["away_player_ids"]:
            if pid in meta:
                row[pid] = row.get(pid, 0.0) - w
        if row:
            apm_rows.append(row)
            apm_y.append(float(s["impact"]) * w)
    apm = _solve_ridge(apm_rows, apm_y, player_ids, ridge)

    role_sum: dict[tuple[str, str], float] = defaultdict(float)
    role_n: dict[tuple[str, str], int] = defaultdict(int)
    global_sum: dict[str, float] = defaultdict(float)
    global_n: dict[str, int] = defaultdict(int)
    raw_rates: dict[str, dict[str, float]] = {}
    for pid in player_ids:
        denom = max(exposure[pid] / 90.0, 1e-6)
        vals = {d: agg[pid].get(d, 0.0) / denom for d in DIMS}
        raw_rates[pid] = vals
        role = meta[pid][2]
        for d, v in vals.items():
            role_sum[(role, d)] += v; role_n[(role, d)] += 1
            global_sum[d] += v; global_n[d] += 1

    out: dict[str, PlayerVector] = {}
    for pid in player_ids:
        team, league, role = meta[pid]
        eff_matches = exposure[pid] / 90.0
        shrink = eff_matches / (eff_matches + 12.0)
        vals: dict[str, float] = {}
        for d in DIMS:
            role_prior = role_sum[(role, d)] / max(role_n[(role, d)], 1)
            global_prior = global_sum[d] / max(global_n[d], 1)
            prior = 0.7 * role_prior + 0.3 * global_prior
            v = shrink * raw_rates[pid][d] + (1.0 - shrink) * prior
            if d in {"on_ball_contribution", "off_ball_contribution", "current_form"}:
                v += 0.20 * apm.get(pid, 0.0)
            vals[d] = v * float(league_strength.get(league, 1.0))
        unc = min(2.0, 1.0 / math.sqrt(1.0 + eff_matches) + 1.0 / math.sqrt(1.0 + len(apm_rows)))
        out[pid] = PlayerVector(pid, team, league, role, vals, exposure[pid], unc, as_of,
                                "FULL_EVENT" if events else "LINEUP_STATS")
    return out


def lineup_components(vectors: dict[str, PlayerVector], player_ids: list[str]) -> tuple[float, float, float, float]:
    chosen = [vectors[p] for p in player_ids if p in vectors]
    if not chosen:
        return 0.0, 0.0, 0.0, 1.0
    attack_dims = {"shot_generation", "finishing", "chance_creation", "passing_progression", "carrying_progression", "set_piece", "on_ball_contribution"}
    defence_dims = {"pressing", "tackling_interception", "defensive_position_protection", "aerial", "off_ball_contribution"}
    attack = sum(sum(v.values[d] for d in attack_dims) for v in chosen) / (len(chosen) * len(attack_dims))
    defence = sum(sum(v.values[d] for d in defence_dims) for v in chosen) / (len(chosen) * len(defence_dims))
    keeper = max((v.values["goalkeeper_shot_stopping"] + v.values["goalkeeper_sweeping"] + v.values["goalkeeper_cross_claiming"]) / 3.0 for v in chosen)
    unc = sum(v.uncertainty for v in chosen) / len(chosen)
    return max(-0.25, min(0.25, 0.015 * attack)), max(-0.25, min(0.25, 0.015 * defence)), max(-0.20, min(0.20, 0.015 * keeper)), unc
