from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import numpy as np

MATCH_URLS = (
    "https://raw.githubusercontent.com/hudl/open-data/master/data/matches/49/3.json",
    "https://raw.githubusercontent.com/hudl/open-data/master/data/matches/49/107.json",
)
EVENT_URL = "https://raw.githubusercontent.com/hudl/open-data/master/data/events/{match_id}.json"
USER_AGENT = "FASHI188-football-analysis/world-model-v2"

MIN_USABLE_MATCHES = 220
MIN_EVENT_SUCCESS_RATE = 0.98
BASE_WINDOW = 12
BASE_PRIOR_EQ = 6.0
INITIAL_HOME_XG = 1.45
INITIAL_AWAY_XG = 1.15
LAMBDA_MIN = 0.15
LAMBDA_MAX = 4.0
MIN_TEST_MATCHES = 100
MIN_FOLD_MATCHES = 30
MIN_PRIOR_MATCHES = 100
BOOTSTRAP_REPS = 5000
PROB_FLOOR = 1e-12
RANDOM_SEED = 20260818


@dataclass(frozen=True)
class MatchMeta:
    match_id: int
    match_date: str
    kick_off: str
    home_id: int
    away_id: int
    home: str
    away: str


@dataclass(frozen=True)
class MatchSummary:
    meta: MatchMeta
    home_xg: float
    away_xg: float
    home_goals: int
    away_goals: int
    event_sha256: str


def _download_bytes(url: str, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def _download_json(url: str) -> tuple[Any, str]:
    raw = _download_bytes(url)
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False


def load_matches() -> tuple[list[MatchMeta], str, dict[str, int]]:
    matches_by_id: dict[int, MatchMeta] = {}
    source_shas: list[str] = []
    source_counts: dict[str, int] = {}
    for url in MATCH_URLS:
        payload, sha = _download_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(f"match payload is not a list: {url}")
        source_shas.append(f"{url}:{sha}")
        source_counts[url] = len(payload)
        for item in payload:
            # Deliberately do not parse home_score/away_score. Realized goals are derived
            # only from the event stream after all same-date predictions have been frozen.
            meta = MatchMeta(
                match_id=int(item["match_id"]),
                match_date=str(item["match_date"]),
                kick_off=str(item.get("kick_off") or ""),
                home_id=int(item["home_team"]["home_team_id"]),
                away_id=int(item["away_team"]["away_team_id"]),
                home=str(item["home_team"]["home_team_name"]),
                away=str(item["away_team"]["away_team_name"]),
            )
            if meta.match_id in matches_by_id:
                raise RuntimeError(f"duplicate match_id across V2 development panel: {meta.match_id}")
            matches_by_id[meta.match_id] = meta
    matches = sorted(matches_by_id.values(), key=lambda m: (m.match_date, m.kick_off, m.match_id))
    ledger_sha = hashlib.sha256("\n".join(source_shas).encode("utf-8")).hexdigest()
    return matches, ledger_sha, source_counts


def event_head_coverage(matches: list[MatchMeta]) -> tuple[int, list[int]]:
    ok = 0
    missing: list[int] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {
            pool.submit(_head_ok, EVENT_URL.format(match_id=m.match_id)): m.match_id
            for m in matches
        }
        for future in as_completed(futures):
            mid = futures[future]
            if future.result():
                ok += 1
            else:
                missing.append(mid)
    return ok, sorted(missing)


def summarize_events(meta: MatchMeta) -> MatchSummary:
    events, event_sha = _download_json(EVENT_URL.format(match_id=meta.match_id))
    if not isinstance(events, list):
        raise RuntimeError(f"events payload not list for {meta.match_id}")

    xg = {meta.home_id: 0.0, meta.away_id: 0.0}
    goals = {meta.home_id: 0, meta.away_id: 0}

    for event in events:
        if not isinstance(event, dict):
            continue
        team_obj = event.get("team") or {}
        team_id = team_obj.get("id")
        if team_id is None:
            continue
        team_id = int(team_id)
        if team_id not in xg:
            continue

        etype = str((event.get("type") or {}).get("name") or "")
        if etype == "Shot":
            shot = event.get("shot") or {}
            xg[team_id] += float(shot.get("statsbomb_xg") or 0.0)
            if str((shot.get("outcome") or {}).get("name") or "") == "Goal":
                goals[team_id] += 1
        elif etype == "Own Goal For":
            goals[team_id] += 1
        elif etype == "Own Goal Against":
            other = meta.away_id if team_id == meta.home_id else meta.home_id
            goals[other] += 1

    return MatchSummary(
        meta=meta,
        home_xg=float(xg[meta.home_id]),
        away_xg=float(xg[meta.away_id]),
        home_goals=int(goals[meta.home_id]),
        away_goals=int(goals[meta.away_id]),
        event_sha256=event_sha,
    )


def league_xg(history: list[MatchSummary]) -> tuple[float, float, float]:
    if not history:
        neutral = (INITIAL_HOME_XG + INITIAL_AWAY_XG) / 2.0
        return INITIAL_HOME_XG, INITIAL_AWAY_XG, neutral
    lh = float(np.mean([m.home_xg for m in history]))
    la = float(np.mean([m.away_xg for m in history]))
    lh = max(lh, 0.2)
    la = max(la, 0.2)
    return lh, la, max((lh + la) / 2.0, 0.2)


def recent_xg_rates(
    team_id: int, history: list[MatchSummary], neutral: float
) -> tuple[float, float, int]:
    rows: list[tuple[float, float]] = []
    for match in reversed(history):
        if match.meta.home_id == team_id:
            rows.append((match.home_xg, match.away_xg))
        elif match.meta.away_id == team_id:
            rows.append((match.away_xg, match.home_xg))
        if len(rows) >= BASE_WINDOW:
            break
    n = len(rows)
    xf = sum(r[0] for r in rows)
    xa = sum(r[1] for r in rows)
    denom = n + BASE_PRIOR_EQ
    return (
        (xf + BASE_PRIOR_EQ * neutral) / denom,
        (xa + BASE_PRIOR_EQ * neutral) / denom,
        n,
    )


def estimate_lambdas(meta: MatchMeta, history: list[MatchSummary]) -> tuple[float, float]:
    league_home, league_away, neutral = league_xg(history)
    hf, ha, _ = recent_xg_rates(meta.home_id, history, neutral)
    af, aa, _ = recent_xg_rates(meta.away_id, history, neutral)
    h_att, h_weak = hf / neutral, ha / neutral
    a_att, a_weak = af / neutral, aa / neutral
    lh = float(np.clip(league_home * h_att * a_weak, LAMBDA_MIN, LAMBDA_MAX))
    la = float(np.clip(league_away * a_att * h_weak, LAMBDA_MIN, LAMBDA_MAX))
    return lh, la
