#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import heapq
import json
import math
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

ROOT=Path(__file__).resolve().parents[2]
FOOTBALL_DATA=ROOT/"football-data"
for _p in (ROOT,FOOTBALL_DATA,FOOTBALL_DATA/"new_engine_v1"):
    sys.path.insert(0,str(_p))
from historical_xg_challenger_v1 import historical_xg_challenger as hxg
from new_engine_v1 import formal_fusion_v2 as formal
from nova_n1_common_v1 import *

def identity_rows(con: sqlite3.Connection, seasons: Iterable[int]) -> list[IdentityRow]:
    ss = sorted(set(int(x) for x in seasons))
    marks = ",".join("?" for _ in ss)
    lmarks = ",".join("?" for _ in LEAGUES)
    q = f"""SELECT fid,date,league,season,team_h,team_a
            FROM general_game_stats
            WHERE season IN ({marks}) AND league IN ({lmarks})
            ORDER BY date ASC, fid ASC"""
    vals = list(ss) + list(LEAGUES.keys())
    out = []
    seen = set()
    for fid, date, league, season, home, away in con.execute(q, vals):
        if fid in seen:
            raise N1Error(f"duplicate fixture id {fid}")
        seen.add(fid)
        out.append(IdentityRow(int(fid), parse_kickoff(date), str(league), LEAGUES[str(league)], int(season), str(home), str(away)))
    return out

def fixture_obj(r: IdentityRow) -> Any:
    return hxg.FixtureRow(
        fixture_id=f"understat:{r.fid}", competition_id=r.competition_id,
        season=season_label(r.season_key), kickoff=r.kickoff,
        home_team_id=f"{r.competition_id}:{slug(r.home)}",
        away_team_id=f"{r.competition_id}:{slug(r.away)}",
        home_team_name=r.home, away_team_name=r.away,
    )

def query_post_prediction(con: sqlite3.Connection, fids: list[int]) -> dict[int, dict[str, Any]]:
    marks = ",".join("?" for _ in fids)
    q = f"""SELECT fid,h_goals,a_goals,h_xg,a_xg,h_deep,a_deep,h_ppda,a_ppda
            FROM general_game_stats WHERE fid IN ({marks})"""
    got = {}
    for row in con.execute(q, fids):
        fid = int(row[0])
        if any(v is None for v in row[1:]):
            raise N1Error(f"missing post-prediction fields fixture={fid}")
        got[fid] = {
            "h_goals": int(row[1]), "a_goals": int(row[2]),
            "h_xg": float(row[3]), "a_xg": float(row[4]),
            "h_deep": float(row[5]), "a_deep": float(row[6]),
            "h_ppda": float(row[7]), "a_ppda": float(row[8]),
        }
    if set(got) != set(fids):
        raise N1Error("post-prediction identity mismatch")
    return got

def outcome(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2

def safe_log_ppda(x: float) -> float:
    if not math.isfinite(x) or x <= 0:
        raise N1Error("invalid PPDA")
    return math.log(x)

def feature_update_payload(r: IdentityRow, post: dict[str, Any]) -> dict[str, tuple[float, float, float, float]]:
    h = (math.log1p(post["h_deep"]), math.log1p(post["a_deep"]), safe_log_ppda(post["h_ppda"]), safe_log_ppda(post["a_ppda"]))
    a = (math.log1p(post["a_deep"]), math.log1p(post["h_deep"]), safe_log_ppda(post["a_ppda"]), safe_log_ppda(post["h_ppda"]))
    return {f"{r.competition_id}:{slug(r.home)}": h, f"{r.competition_id}:{slug(r.away)}": a}

def team_state(hist: dict[str, deque[tuple[float,float,float,float]]], team_id: str, n: int) -> tuple[float,float,float,float] | None:
    d = hist.get(team_id)
    if not d:
        return None
    vals = list(d)[-n:]
    if not vals:
        return None
    return tuple(math.fsum(v[i] for v in vals) / len(vals) for i in range(4))

def raw_matchup(hist: dict[str, deque], home_id: str, away_id: str, n: int) -> dict[str, float] | None:
    h = team_state(hist, home_id, n); a = team_state(hist, away_id, n)
    if h is None or a is None:
        return None
    return {"D1": h[0] - a[0], "D2": h[1] - a[1], "P1": h[2] - a[2], "P2": h[3] - a[3]}

def canonical_matrix(pred: dict[str, Any]) -> list[tuple[int,int,float]]:
    cells = pred.get("score_matrix")
    if not isinstance(cells, list) or not cells:
        raise N1Error("formal score matrix missing")
    out = []; total = 0.0; seen = set()
    for c in cells:
        hg, ag = int(c["home_goals"]), int(c["away_goals"]); p = float(c["probability"])
        if (hg, ag) in seen or p < 0 or not math.isfinite(p):
            raise N1Error("invalid formal score matrix")
        seen.add((hg,ag)); total += p; out.append((hg,ag,p))
    if total <= 0:
        raise N1Error("zero matrix mass")
    return [(hg,ag,p/total) for hg,ag,p in out]

def probs_from_matrix(matrix: list[tuple[int,int,float]]) -> list[float]:
    h = math.fsum(p for hg,ag,p in matrix if hg > ag)
    d = math.fsum(p for hg,ag,p in matrix if hg == ag)
    a = math.fsum(p for hg,ag,p in matrix if hg < ag)
    s = h+d+a
    return [h/s,d/s,a/s]

def baseline_pack(pred: dict[str, Any]) -> dict[str, Any]:
    m = canonical_matrix(pred); p = probs_from_matrix(m)
    declared = [float(pred["p_home"]), float(pred["p_draw"]), float(pred["p_away"])]
    ds = sum(declared); declared = [x/ds for x in declared]
    if max(abs(x-y) for x,y in zip(p,declared)) > 5e-10:
        raise N1Error("formal matrix/marginal mismatch")
    return {"p": p, "matrix": m}

def build_replay(con: sqlite3.Connection, seasons: set[int]) -> tuple[list[ReplayRow], dict[str, Any]]:
    ids = identity_rows(con, seasons)
    if not ids:
        raise N1Error("empty development universe")
    state = formal.new_candidate_state()
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=max(WINDOWS)))
    pending: list[tuple] = []
    seq = 0; rows: list[ReplayRow] = []; i = 0
    baseline_prediction_sha = hashlib.sha256()
    while i < len(ids):
        kickoff = ids[i].kickoff
        while pending and pending[0][0] <= kickoff:
            release_at, _, fixtures, labels, updates = heapq.heappop(pending)
            formal.apply_completed_xg_batch(state, fixtures, labels, release_at)
            for tid, val in updates.items():
                hist[tid].append(val)
        j = i; batch_ids = []
        while j < len(ids) and ids[j].kickoff == kickoff:
            batch_ids.append(ids[j]); j += 1
        fixtures = [fixture_obj(r) for r in batch_ids]
        preds = formal.predict_formal_batch(state, fixtures)
        if len(preds) != len(batch_ids):
            raise N1Error("formal prediction length mismatch")
        snapshots = []
        for r in batch_ids:
            hid = f"{r.competition_id}:{slug(r.home)}"; aid = f"{r.competition_id}:{slug(r.away)}"
            snapshots.append({n: raw_matchup(hist, hid, aid, n) for n in WINDOWS})
        post = query_post_prediction(con, [r.fid for r in batch_ids])
        labels = {}; updates = {}
        for r, fixture, pred, snap in zip(batch_ids, fixtures, preds, snapshots):
            pp = post[r.fid]
            labels[fixture.fixture_id] = hxg.ReleasedLabel(home_goals=pp["h_goals"], away_goals=pp["a_goals"], home_xg=pp["h_xg"], away_xg=pp["a_xg"], release_at=kickoff + RELEASE_LAG)
            for tid,val in feature_update_payload(r, pp).items():
                if tid in updates:
                    raise N1Error("team appears twice in same exact-kickoff batch")
                updates[tid] = val
            b = baseline_pack(pred["prediction"])
            baseline_prediction_sha.update(json.dumps({"fid": r.fid, "p": b["p"], "matrix": b["matrix"]}, separators=(",",":"), sort_keys=True).encode())
            rows.append(ReplayRow(fid=r.fid, kickoff=r.kickoff, competition_id=r.competition_id, league=r.league, season_key=r.season_key, home=r.home, away=r.away, baseline=b, raw_features=snap, y=outcome(pp["h_goals"], pp["a_goals"])))
        seq += 1
        heapq.heappush(pending, (kickoff + RELEASE_LAG, seq, fixtures, labels, updates))
        i = j
    audit = {
        "fixtures": len(rows), "first_kickoff": rows[0].kickoff.isoformat(), "last_kickoff": rows[-1].kickoff.isoformat(),
        "formal_state_digest_after_last_prediction": state.state_digest(), "baseline_prediction_sha256": baseline_prediction_sha.hexdigest(),
        "current_match_postfields_read_only_after_prediction": True,
        "same_kickoff_batch_predicted_before_labels_or_features_read": True,
        "release_lag_minutes": 180,
    }
    return rows, audit
