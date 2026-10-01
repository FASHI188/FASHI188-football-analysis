from __future__ import annotations
import math
from typing import Any
import numpy as np
from wmv1_source import *
def perspective(summary: MatchSummary, team: str) -> TeamMatchStats:
    if summary.home.team == team:
        return summary.home
    if summary.away.team == team:
        return summary.away
    raise KeyError(team)

def league_xg(history: list[MatchSummary]) -> tuple[float, float, float]:
    if not history:
        return (INITIAL_HOME_XG, INITIAL_AWAY_XG, (INITIAL_HOME_XG + INITIAL_AWAY_XG) / 2.0)
    lh = float(np.mean([m.home.xg_for for m in history]))
    la = float(np.mean([m.away.xg_for for m in history]))
    return (max(lh, 0.2), max(la, 0.2), max((lh + la) / 2.0, 0.2))

def recent_xg_rates(team: str, history: list[MatchSummary], neutral: float) -> tuple[float, float, int]:
    rows: list[TeamMatchStats] = []
    for m in reversed(history):
        if m.home.team == team or m.away.team == team:
            rows.append(perspective(m, team))
        if len(rows) >= BASE_WINDOW:
            break
    n = len(rows)
    xf = sum((r.xg_for for r in rows))
    xa = sum((r.xg_against for r in rows))
    return ((xf + BASE_PRIOR_EQ * neutral) / (n + BASE_PRIOR_EQ), (xa + BASE_PRIOR_EQ * neutral) / (n + BASE_PRIOR_EQ), n)

def estimate_lambdas(meta: MatchMeta, history: list[MatchSummary]) -> tuple[float, float]:
    league_home, league_away, neutral = league_xg(history)
    hf, ha, _ = recent_xg_rates(meta.home, history, neutral)
    af, aa, _ = recent_xg_rates(meta.away, history, neutral)
    h_att, h_weak = (hf / neutral, ha / neutral)
    a_att, a_weak = (af / neutral, aa / neutral)
    lh = float(np.clip(league_home * h_att * a_weak, LAMBDA_MIN, LAMBDA_MAX))
    la = float(np.clip(league_away * a_att * h_weak, LAMBDA_MIN, LAMBDA_MAX))
    return (lh, la)

def _recent_team_rows(team: str, history: list[MatchSummary], window: int=TEAM_WINDOW) -> list[TeamMatchStats]:
    out: list[TeamMatchStats] = []
    for m in reversed(history):
        if m.home.team == team or m.away.team == team:
            out.append(perspective(m, team))
        if len(out) >= window:
            break
    return list(reversed(out))

def team_profile(team: str, history: list[MatchSummary]) -> dict[str, Any]:
    rows = _recent_team_rows(team, history)
    _, _, neutral = league_xg(history)
    if not rows:
        return {'xg_for': neutral, 'xg_against': neutral, 'shots': 10.0, 'box': 8.0, 'final_third': 35.0, 'pressure': 150.0, 'counterpress_share': 0.08, 'shot_quality': neutral / 10.0, 'setpiece_share': 0.18, 'counter_share': 0.08, 'for_profile': np.ones(TEMPORAL_BINS) / TEMPORAL_BINS, 'against_profile': np.ones(TEMPORAL_BINS) / TEMPORAL_BINS}
    xg_for = sum((r.xg_for for r in rows))
    xg_against = sum((r.xg_against for r in rows))
    shots = sum((r.shots_for for r in rows))
    pressure = sum((r.pressures_for for r in rows))
    cp = sum((r.counterpressures_for for r in rows))
    setxg = sum((r.set_piece_xg for r in rows))
    counterxg = sum((r.counter_xg for r in rows))
    for_bins = np.asarray([0.25] * TEMPORAL_BINS, dtype=float)
    against_bins = np.asarray([0.25] * TEMPORAL_BINS, dtype=float)
    for r in rows:
        for_bins += np.asarray(r.xg_bins_for, dtype=float)
        against_bins += np.asarray(r.xg_bins_against, dtype=float)
    for_bins /= for_bins.sum()
    against_bins /= against_bins.sum()
    n = float(len(rows))
    return {'xg_for': xg_for / n, 'xg_against': xg_against / n, 'shots': shots / n, 'box': sum((r.box_entries_for for r in rows)) / n, 'final_third': sum((r.final_third_entries_for for r in rows)) / n, 'pressure': pressure / n, 'counterpress_share': cp / max(pressure, 1), 'shot_quality': xg_for / max(shots, 1), 'setpiece_share': setxg / max(xg_for, 0.25), 'counter_share': counterxg / max(xg_for, 0.25), 'for_profile': for_bins, 'against_profile': against_bins}

def lineup_features(team: str, current: TeamLineup, history: list[MatchSummary], player_attack: dict[tuple[str, int], float], player_defense: dict[tuple[str, int], float], player_apps: dict[tuple[str, int], int], last_starters: dict[str, tuple[int, ...]], last_roles: dict[str, tuple[int, int, int, int]]) -> dict[str, float]:
    ids = tuple((s.player_id for s in current.starters))
    previous = last_starters.get(team, ())
    continuity = len(set(ids) & set(previous)) / 11.0 if previous else 0.5
    unknown = sum((1 for pid in ids if player_apps.get((team, pid), 0) == 0)) / 11.0
    attack_rates = []
    defense_rates = []
    for pid in ids:
        apps = player_apps.get((team, pid), 0)
        attack_rates.append((player_attack.get((team, pid), 0.0) + 0.05) / (apps + 2.0))
        defense_rates.append((player_defense.get((team, pid), 0.0) + 0.2) / (apps + 2.0))
    prev_roles = last_roles.get(team, current.role_counts)
    def_shift = abs(current.role_counts[1] - prev_roles[1]) / 3.0
    att_shift = abs(current.role_counts[3] - prev_roles[3]) / 3.0
    return {'continuity': continuity, 'unknown_share': unknown, 'starter_attack': float(np.mean(attack_rates)), 'starter_defense': float(np.mean(defense_rates)), 'def_shift': def_shift, 'att_shift': att_shift, 'def_count': float(current.role_counts[1]), 'mid_count': float(current.role_counts[2]), 'att_count': float(current.role_counts[3])}

def temporal_factors(att: dict[str, Any], opp: dict[str, Any]) -> tuple[float, ...]:
    a = np.asarray(att['for_profile'], dtype=float) * TEMPORAL_BINS
    d = np.asarray(opp['against_profile'], dtype=float) * TEMPORAL_BINS
    f = np.sqrt(np.maximum(a, 1e-06) * np.maximum(d, 1e-06))
    f = np.clip(f, 0.45, 2.0)
    f /= max(float(np.mean(f)), 1e-06)
    return tuple((float(v) for v in f))

def static_features(is_home: bool, att: dict[str, Any], opp: dict[str, Any], lu: dict[str, float], opp_lu: dict[str, float], neutral_xg: float) -> tuple[float, ...]:
    return (1.0 if is_home else 0.0, float(att['xg_for'] - neutral_xg), float(opp['xg_against'] - neutral_xg), float(att['box']), float(att['final_third']), float(opp['pressure']), float(att['counterpress_share']), float(att['shot_quality']), float(att['setpiece_share']), float(att['counter_share']), float(lu['continuity']), float(lu['starter_attack']), float(opp_lu['starter_defense']), float(lu['unknown_share']), float(lu['def_shift']), float(lu['att_shift']), float(lu['def_count']), float(lu['att_count']))

def make_context(meta: MatchMeta, lineup: LineupSnapshot, history: list[MatchSummary], player_attack: dict[tuple[str, int], float], player_defense: dict[tuple[str, int], float], player_apps: dict[tuple[str, int], int], last_starters: dict[str, tuple[int, ...]], last_roles: dict[str, tuple[int, int, int, int]]) -> PredictionContext:
    lh, la = estimate_lambdas(meta, history)
    _, _, neutral = league_xg(history)
    hp = team_profile(meta.home, history)
    ap = team_profile(meta.away, history)
    hlu = lineup_features(meta.home, lineup.home, history, player_attack, player_defense, player_apps, last_starters, last_roles)
    alu = lineup_features(meta.away, lineup.away, history, player_attack, player_defense, player_apps, last_starters, last_roles)
    return PredictionContext(meta=meta, lh=lh, la=la, static_home=static_features(True, hp, ap, hlu, alu, neutral), static_away=static_features(False, ap, hp, alu, hlu, neutral), temporal_home=temporal_factors(hp, ap), temporal_away=temporal_factors(ap, hp), lineup_sha256=lineup.sha256)

def feature_vector(static: tuple[float, ...], segment: int, score_diff: float) -> np.ndarray:
    t = (segment + 0.5) / SEGMENTS
    d = float(np.clip(score_diff, -2.0, 2.0) / 2.0)
    return np.asarray(static + (2.0 * t - 1.0, (2.0 * t - 1.0) ** 2, d, 1.0 if d > 0 else 0.0, 1.0 if d < 0 else 0.0), dtype=float)

def feature_batch(static: tuple[float, ...], segment: int, score_diff: np.ndarray) -> np.ndarray:
    n = len(score_diff)
    base = np.asarray(static, dtype=float)
    out = np.empty((n, len(base) + 5), dtype=float)
    out[:, :len(base)] = base
    t = (segment + 0.5) / SEGMENTS
    tn = 2.0 * t - 1.0
    d = np.clip(score_diff.astype(float), -2.0, 2.0) / 2.0
    out[:, len(base)] = tn
    out[:, len(base) + 1] = tn * tn
    out[:, len(base) + 2] = d
    out[:, len(base) + 3] = (d > 0).astype(float)
    out[:, len(base) + 4] = (d < 0).astype(float)
    return out

def add_training_rows(ctx: PredictionContext, summary: MatchSummary, rows: list[tuple[np.ndarray, float, int]]) -> None:
    hs = 0
    as_ = 0
    for seg in range(SEGMENTS):
        period = min(seg // 3, TEMPORAL_BINS - 1)
        hy = int(summary.home.goal_bins_for[seg])
        ay = int(summary.away.goal_bins_for[seg])
        hfeat = feature_vector(ctx.static_home, seg, hs - as_)
        afeat = feature_vector(ctx.static_away, seg, as_ - hs)
        hoff = math.log(max(ctx.lh / SEGMENTS * ctx.temporal_home[period], 1e-08))
        aoff = math.log(max(ctx.la / SEGMENTS * ctx.temporal_away[period], 1e-08))
        rows.append((hfeat, hoff, hy))
        rows.append((afeat, aoff, ay))
        hs += hy
        as_ += ay

def update_player_state(summary: MatchSummary, player_attack: dict[tuple[str, int], float], player_defense: dict[tuple[str, int], float], player_apps: dict[tuple[str, int], int], last_starters: dict[str, tuple[int, ...]], last_roles: dict[str, tuple[int, int, int, int]]) -> None:
    for ts in (summary.home, summary.away):
        appeared = set(ts.player_ids_seen) | set(ts.starters)
        for pid in appeared:
            player_apps[ts.team, pid] = player_apps.get((ts.team, pid), 0) + 1
        for pid, value in ts.player_attack.items():
            player_attack[ts.team, pid] = player_attack.get((ts.team, pid), 0.0) + value
        for pid, value in ts.player_defense.items():
            player_defense[ts.team, pid] = player_defense.get((ts.team, pid), 0.0) + value
        last_starters[ts.team] = ts.starters
        last_roles[ts.team] = ts.role_counts
