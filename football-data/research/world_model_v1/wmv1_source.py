from __future__ import annotations
import hashlib,json,math,time,urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from typing import Any
import numpy as np
MATCHES_URL="https://raw.githubusercontent.com/hudl/open-data/master/data/matches/9/27.json"
EVENT_URL="https://raw.githubusercontent.com/hudl/open-data/master/data/events/{match_id}.json"
LINEUP_URL="https://raw.githubusercontent.com/hudl/open-data/master/data/lineups/{match_id}.json"
USER_AGENT="FASHI188-football-analysis/world-model-v1"
EXPECTED_MATCHES=306
MIN_USABLE_MATCHES=290
MIN_EVENT_SUCCESS_RATE=0.98
MIN_LINEUP_SUCCESS_RATE=0.98
BASE_WINDOW=12
BASE_PRIOR_EQ=6.0
TEAM_WINDOW=8
INITIAL_HOME_XG=1.45
INITIAL_AWAY_XG=1.15
LAMBDA_MIN=0.15
LAMBDA_MAX=4.0
SEGMENTS=18
TEMPORAL_BINS=6
SIMS=10000
RANDOM_SEED=20260818
RIDGE_ALPHA=8.0
IRLS_ITERS=15
COEF_CLIP=2.5
MATRIX_ALPHA=0.25
BOOTSTRAP_REPS=5000
PROB_FLOOR=1e-12
MIN_TEST_MATCHES=140
MIN_FOLD_MATCHES=40
MIN_TRAIN_ROWS=4000
@dataclass(frozen=True)
class MatchMeta:
    match_id: int
    match_date: str
    kick_off: str
    home: str
    away: str

@dataclass(frozen=True)
class Starter:
    player_id: int
    name: str
    position: str

@dataclass(frozen=True)
class TeamLineup:
    team: str
    starters: tuple[Starter, ...]
    role_counts: tuple[int, int, int, int]

@dataclass(frozen=True)
class LineupSnapshot:
    home: TeamLineup
    away: TeamLineup
    sha256: str

@dataclass(frozen=True)
class TeamMatchStats:
    team: str
    opponent: str
    xg_for: float
    xg_against: float
    shots_for: int
    box_entries_for: int
    final_third_entries_for: int
    pressures_for: int
    counterpressures_for: int
    set_piece_xg: float
    counter_xg: float
    xg_bins_for: tuple[float, ...]
    xg_bins_against: tuple[float, ...]
    goal_bins_for: tuple[int, ...]
    goal_bins_against: tuple[int, ...]
    player_attack: dict[int, float]
    player_defense: dict[int, float]
    player_ids_seen: tuple[int, ...]
    starters: tuple[int, ...]
    role_counts: tuple[int, int, int, int]

@dataclass(frozen=True)
class MatchSummary:
    meta: MatchMeta
    home: TeamMatchStats
    away: TeamMatchStats
    event_sha256: str
    lineup_sha256: str

@dataclass(frozen=True)
class PredictionContext:
    meta: MatchMeta
    lh: float
    la: float
    static_home: tuple[float, ...]
    static_away: tuple[float, ...]
    temporal_home: tuple[float, ...]
    temporal_away: tuple[float, ...]
    lineup_sha256: str

def _download_bytes(url: str, attempts: int=4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * 2 ** attempt)
    raise RuntimeError(f'download failed after {attempts} attempts: {url}: {last}')

def _download_json(url: str) -> tuple[Any, str]:
    raw = _download_bytes(url)
    return (json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest())

def _head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT}, method='HEAD')
        with urllib.request.urlopen(req, timeout=60) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False

def load_matches() -> tuple[list[MatchMeta], str]:
    payload, sha = _download_json(MATCHES_URL)
    if not isinstance(payload, list):
        raise RuntimeError('match payload is not a list')
    matches: list[MatchMeta] = []
    for item in payload:
        matches.append(MatchMeta(match_id=int(item['match_id']), match_date=str(item['match_date']), kick_off=str(item.get('kick_off') or ''), home=str(item['home_team']['home_team_name']), away=str(item['away_team']['away_team_name'])))
    matches.sort(key=lambda m: (m.match_date, m.kick_off, m.match_id))
    return (matches, sha)

def _role(position: str) -> str:
    p = position.lower()
    if 'goalkeeper' in p:
        return 'gk'
    if 'back' in p:
        return 'def'
    if 'midfield' in p:
        return 'mid'
    if 'wing' in p or 'forward' in p or 'striker' in p:
        return 'att'
    return 'mid'

def _extract_team_lineup(team_obj: dict[str, Any]) -> TeamLineup:
    starters: list[Starter] = []
    roles = {'gk': 0, 'def': 0, 'mid': 0, 'att': 0}
    for player in team_obj.get('lineup') or []:
        opening = None
        for pos in player.get('positions') or []:
            if str(pos.get('from') or '') == '00:00' and str(pos.get('start_reason') or '') == 'Starting XI':
                opening = pos
                break
        if opening is None:
            continue
        st = Starter(player_id=int(player['player_id']), name=str(player.get('player_name') or player['player_id']), position=str(opening.get('position') or 'Unknown'))
        starters.append(st)
        roles[_role(st.position)] += 1
    if len(starters) != 11:
        raise RuntimeError(f"expected 11 starters for {team_obj.get('team_name')}, got {len(starters)}")
    return TeamLineup(team=str(team_obj.get('team_name') or ''), starters=tuple(starters), role_counts=(roles['gk'], roles['def'], roles['mid'], roles['att']))

def load_lineup(meta: MatchMeta) -> LineupSnapshot:
    payload, sha = _download_json(LINEUP_URL.format(match_id=meta.match_id))
    if not isinstance(payload, list):
        raise RuntimeError('lineup payload is not a list')
    teams: dict[str, TeamLineup] = {}
    for team_obj in payload:
        parsed = _extract_team_lineup(team_obj)
        teams[parsed.team] = parsed
    if meta.home not in teams or meta.away not in teams:
        raise RuntimeError(f'lineup team identity mismatch for {meta.match_id}')
    return LineupSnapshot(home=teams[meta.home], away=teams[meta.away], sha256=sha)

def load_all_lineups(matches: list[MatchMeta]) -> tuple[dict[int, LineupSnapshot], list[dict[str, Any]]]:
    out: dict[int, LineupSnapshot] = {}
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(load_lineup, m): m for m in matches}
        for future in as_completed(futures):
            m = futures[future]
            try:
                out[m.match_id] = future.result()
            except Exception as exc:
                failures.append({'match_id': m.match_id, 'error': f'{type(exc).__name__}: {exc}'})
    return (out, failures)

def event_head_coverage(matches: list[MatchMeta]) -> tuple[int, list[int]]:
    ok = 0
    missing: list[int] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_head_ok, EVENT_URL.format(match_id=m.match_id)): m.match_id for m in matches}
        for future in as_completed(futures):
            mid = futures[future]
            if future.result():
                ok += 1
            else:
                missing.append(mid)
    return (ok, missing)

def _five_bin(minute: int) -> int:
    return min(max(int(minute), 0) // 5, 17)

def _fifteen_bin(minute: int) -> int:
    return min(max(int(minute), 0) // 15, 5)

def _empty_acc() -> dict[str, Any]:
    return {'xg': 0.0, 'shots': 0, 'box': 0, 'final_third': 0, 'pressures': 0, 'counterpress': 0, 'set_piece_xg': 0.0, 'counter_xg': 0.0, 'xg_bins': [0.0] * TEMPORAL_BINS, 'goal_bins': [0] * SEGMENTS, 'player_attack': defaultdict(float), 'player_defense': defaultdict(float), 'players_seen': set()}

def _pid(event: dict[str, Any]) -> int | None:
    p = event.get('player') or {}
    value = p.get('id')
    return int(value) if value is not None else None

def _is_complete_pass(event: dict[str, Any]) -> bool:
    p = event.get('pass') or {}
    return p.get('outcome') is None

def summarize_events(meta: MatchMeta, lineup: LineupSnapshot) -> MatchSummary:
    events, event_sha = _download_json(EVENT_URL.format(match_id=meta.match_id))
    if not isinstance(events, list):
        raise RuntimeError(f'events payload not list for {meta.match_id}')
    acc = {meta.home: _empty_acc(), meta.away: _empty_acc()}
    for event in events:
        if not isinstance(event, dict):
            continue
        team = str((event.get('team') or {}).get('name') or '')
        if team not in acc:
            continue
        other = meta.away if team == meta.home else meta.home
        a = acc[team]
        etype = str((event.get('type') or {}).get('name') or '')
        minute = int(event.get('minute') or 0)
        pid = _pid(event)
        if pid is not None:
            a['players_seen'].add(pid)
        if etype == 'Shot':
            shot = event.get('shot') or {}
            xg = float(shot.get('statsbomb_xg') or 0.0)
            a['xg'] += xg
            a['shots'] += 1
            a['xg_bins'][_fifteen_bin(minute)] += xg
            pattern = str((event.get('play_pattern') or {}).get('name') or '')
            if 'Corner' in pattern or 'Free Kick' in pattern:
                a['set_piece_xg'] += xg
            if 'Counter' in pattern:
                a['counter_xg'] += xg
            if pid is not None:
                a['player_attack'][pid] += xg
            if str((shot.get('outcome') or {}).get('name') or '') == 'Goal':
                a['goal_bins'][_five_bin(minute)] += 1
        elif etype == 'Pass' and _is_complete_pass(event):
            p = event.get('pass') or {}
            end = p.get('end_location') or []
            if len(end) >= 2:
                x, y = (float(end[0]), float(end[1]))
                if x >= 80.0:
                    a['final_third'] += 1
                    if pid is not None:
                        a['player_attack'][pid] += 0.01
                if x >= 102.0 and 18.0 <= y <= 62.0:
                    a['box'] += 1
                    if pid is not None:
                        a['player_attack'][pid] += 0.03
            if p.get('assisted_shot_id') and pid is not None:
                a['player_attack'][pid] += 0.08
        elif etype == 'Pressure':
            a['pressures'] += 1
            if bool(event.get('counterpress')):
                a['counterpress'] += 1
            if pid is not None:
                a['player_defense'][pid] += 0.02 + (0.01 if bool(event.get('counterpress')) else 0.0)
        elif etype == 'Ball Recovery':
            if pid is not None:
                a['player_defense'][pid] += 0.04
        elif etype == 'Interception':
            if pid is not None:
                a['player_defense'][pid] += 0.06
        elif etype == 'Duel':
            outcome = str(((event.get('duel') or {}).get('outcome') or {}).get('name') or '')
            if 'Won' in outcome and pid is not None:
                a['player_defense'][pid] += 0.04
        elif etype == 'Own Goal Against':
            acc[other]['goal_bins'][_five_bin(minute)] += 1
        elif etype == 'Own Goal For':
            a['goal_bins'][_five_bin(minute)] += 1

    def build(team: str, opp: str, lu: TeamLineup) -> TeamMatchStats:
        x = acc[team]
        ox = acc[opp]
        return TeamMatchStats(team=team, opponent=opp, xg_for=float(x['xg']), xg_against=float(ox['xg']), shots_for=int(x['shots']), box_entries_for=int(x['box']), final_third_entries_for=int(x['final_third']), pressures_for=int(x['pressures']), counterpressures_for=int(x['counterpress']), set_piece_xg=float(x['set_piece_xg']), counter_xg=float(x['counter_xg']), xg_bins_for=tuple((float(v) for v in x['xg_bins'])), xg_bins_against=tuple((float(v) for v in ox['xg_bins'])), goal_bins_for=tuple((int(v) for v in x['goal_bins'])), goal_bins_against=tuple((int(v) for v in ox['goal_bins'])), player_attack={int(k): float(v) for k, v in x['player_attack'].items()}, player_defense={int(k): float(v) for k, v in x['player_defense'].items()}, player_ids_seen=tuple(sorted((int(v) for v in x['players_seen']))), starters=tuple((s.player_id for s in lu.starters)), role_counts=lu.role_counts)
    return MatchSummary(meta=meta, home=build(meta.home, meta.away, lineup.home), away=build(meta.away, meta.home, lineup.away), event_sha256=event_sha, lineup_sha256=lineup.sha256)
