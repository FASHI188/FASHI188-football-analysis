from __future__ import annotations

import hashlib
import json
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
ATTACK_DIMS = ("shot_generation", "finishing", "chance_creation", "passing_progression", "carrying_progression", "set_piece", "on_ball_contribution")
DEFENCE_DIMS = ("pressing", "tackling_interception", "defensive_position_protection", "aerial", "off_ball_contribution")
GK_DIMS = ("goalkeeper_shot_stopping", "goalkeeper_sweeping", "goalkeeper_cross_claiming", "goalkeeper_distribution")
ESTIMATOR_VERSION = "football3-player-capability-v1-replacement-relative-20260831"


class PlayerStrengthError(RuntimeError):
    pass


def _parse(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise PlayerStrengthError("event timestamp missing timezone")
    return d.astimezone(timezone.utc)


def _decay(days: float, half_life: float) -> float:
    return math.exp(-math.log(2.0) * max(days, 0.0) / half_life)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


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
    role_distribution: dict[str, float]
    values: dict[str, float]
    effective_exposure: float
    uncertainty: float
    state_timestamp: str
    coverage_grade: str
    source_sha256s: list[str]
    estimator_sha256: str
    migration_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_player_vectors(events: list[dict[str, Any]], segments: list[dict[str, Any]], *,
                            as_of: str, half_life_days: float = 180.0, ridge: float = 8.0,
                            league_strength: dict[str, float] | None = None) -> dict[str, PlayerVector]:
    cutoff = _parse(as_of)
    league_strength = league_strength or {}
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    exposure: dict[str, float] = defaultdict(float)
    meta_history: dict[str, list[tuple[datetime, str, str, str]]] = defaultdict(list)
    role_weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_shas: dict[str, set[str]] = defaultdict(set)
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
        decay = _decay((cutoff - known).total_seconds() / 86400.0, half_life_days)
        match_equiv = decay * max(minutes / 90.0, 1e-3)
        pid = str(e["player_id"]); team = str(e["team_id"]); league = str(e["league_id"]); role = str(e["role"])
        meta_history[pid].append((known, team, league, role))
        role_weight[pid][role] += match_equiv
        exposure[pid] += match_equiv
        sha = str(e.get("source_sha256", ""))
        if len(sha) == 64:
            source_shas[pid].add(sha)
        for dim, value in vals.items():
            agg[pid][dim] += decay * float(value) / poss

    player_ids = sorted(meta_history)
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
            if pid in meta_history:
                row[pid] = row.get(pid, 0.0) + w
        for pid in s["away_player_ids"]:
            if pid in meta_history:
                row[pid] = row.get(pid, 0.0) - w
        if row:
            apm_rows.append(row); apm_y.append(float(s["impact"]) * w)
    apm = _solve_ridge(apm_rows, apm_y, player_ids, ridge)

    raw_rates: dict[str, dict[str, float]] = {}
    latest_meta: dict[str, tuple[str, str, str]] = {}
    for pid in player_ids:
        latest = max(meta_history[pid], key=lambda x: x[0])
        latest_meta[pid] = (latest[1], latest[2], latest[3])
        denom = max(exposure[pid], 1e-6)
        raw_rates[pid] = {d: agg[pid].get(d, 0.0) / denom for d in DIMS}

    role_sum: dict[tuple[str, str], float] = defaultdict(float); role_n: dict[tuple[str, str], int] = defaultdict(int)
    team_sum: dict[tuple[str, str], float] = defaultdict(float); team_n: dict[tuple[str, str], int] = defaultdict(int)
    league_sum: dict[tuple[str, str], float] = defaultdict(float); league_n: dict[tuple[str, str], int] = defaultdict(int)
    global_sum: dict[str, float] = defaultdict(float); global_n: dict[str, int] = defaultdict(int)
    for pid in player_ids:
        team, league, role = latest_meta[pid]
        for d, v in raw_rates[pid].items():
            role_sum[(role,d)] += v; role_n[(role,d)] += 1
            team_sum[(team,d)] += v; team_n[(team,d)] += 1
            league_sum[(league,d)] += v; league_n[(league,d)] += 1
            global_sum[d] += v; global_n[d] += 1

    estimator_sha = _sha({"version": ESTIMATOR_VERSION, "dims": DIMS, "half_life_days": half_life_days, "ridge": ridge})
    out: dict[str, PlayerVector] = {}
    for pid in player_ids:
        team, league, role = latest_meta[pid]
        eff_matches = exposure[pid]
        shrink = eff_matches / (eff_matches + 12.0)
        vals: dict[str, float] = {}
        for d in DIMS:
            rp = role_sum[(role,d)] / max(role_n[(role,d)],1)
            tp = team_sum[(team,d)] / max(team_n[(team,d)],1)
            lp = league_sum[(league,d)] / max(league_n[(league,d)],1)
            gp = global_sum[d] / max(global_n[d],1)
            prior = 0.45*rp + 0.25*tp + 0.20*lp + 0.10*gp
            v = shrink*raw_rates[pid][d] + (1.0-shrink)*prior
            if d in {"on_ball_contribution", "off_ball_contribution", "current_form"}:
                v += 0.20*apm.get(pid,0.0)
            vals[d] = v*float(league_strength.get(league,1.0))
        rw = role_weight[pid]; rtot = max(sum(rw.values()),1e-9); rdist = {k:v/rtot for k,v in sorted(rw.items()) if v>0}
        contexts = {(x[1],x[2]) for x in meta_history[pid]}; migrations = max(0,len(contexts)-1)
        unc = 1.0/math.sqrt(1.0+eff_matches) + 0.75/math.sqrt(1.0+len(apm_rows)) + min(0.35,0.12*migrations)
        out[pid] = PlayerVector(pid,team,league,role,rdist,vals,eff_matches,min(2.0,unc),as_of,
                                "FULL_EVENT" if events else "LINEUP_STATS",sorted(source_shas[pid]),estimator_sha,migrations)
    return out


def _score(v: PlayerVector) -> tuple[float,float,float]:
    attack = sum(v.values[d] for d in ATTACK_DIMS)/len(ATTACK_DIMS)
    defence = sum(v.values[d] for d in DEFENCE_DIMS)/len(DEFENCE_DIMS)
    keeper = sum(v.values[d] for d in GK_DIMS)/len(GK_DIMS) if v.role == "GK" else 0.0
    return attack,defence,keeper


def player_replacement_deltas(vectors: dict[str, PlayerVector], player_ids: list[str]) -> list[dict[str, float | str]]:
    chosen = [vectors[p] for p in player_ids if p in vectors]
    chosen_ids = {v.player_id for v in chosen}
    out: list[dict[str,float|str]] = []
    for v in chosen:
        pool = [x for x in vectors.values() if x.team_id == v.team_id and x.role == v.role and x.player_id not in chosen_ids]
        if not pool:
            pool = [x for x in vectors.values() if x.team_id == v.team_id and x.player_id not in chosen_ids]
        va,vd,vg = _score(v)
        if pool:
            ps = [_score(x) for x in pool]
            ba = sum(x[0] for x in ps)/len(ps); bd = sum(x[1] for x in ps)/len(ps); bg = sum(x[2] for x in ps)/len(ps)
        else:
            ba,bd,bg = va,vd,vg
        out.append({"player_id":v.player_id,"role":v.role,"attack_delta":va-ba,"defence_delta":vd-bd,"keeper_delta":vg-bg})
    return out


def lineup_components(vectors: dict[str, PlayerVector], player_ids: list[str]) -> tuple[float, float, float, float]:
    chosen = [vectors[p] for p in player_ids if p in vectors]
    if not chosen:
        return 0.0,0.0,0.0,1.0
    deltas = player_replacement_deltas(vectors,player_ids)
    denom = max(len(chosen),1)
    attack = sum(float(x["attack_delta"]) for x in deltas)/denom
    defence = sum(float(x["defence_delta"]) for x in deltas)/denom
    keeper = sum(float(x["keeper_delta"]) for x in deltas)/denom
    unc = sum(v.uncertainty for v in chosen)/denom
    return max(-0.25,min(0.25,0.020*attack)), max(-0.25,min(0.25,0.020*defence)), max(-0.20,min(0.20,0.020*keeper)), unc
