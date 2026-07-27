#!/usr/bin/env python3
"""V6.45.0 new-information 1X2 challenge: manager + task state.

This challenger deliberately does NOT add another generic classifier over the old
71-feature stack. It asks a narrower question: do two genuinely new, auditable
pre-match information families add stable 1X2 signal beyond the closing market?

New families
------------
1. Manager context from Transfermarkt games: current manager identity, change flag,
   consecutive tenure and strictly prior manager performance. Match-day snapshots
   are computed before any same-day result is applied.
2. League task/standings context reconstructed from the project's full processed
   match history. Table state for a match date uses only earlier completed dates.
   Transfermarkt position columns are intentionally NOT used.

Governance
----------
- CURRENT V5.0.1 unchanged; formal_weight=0.
- Historical selection only: 2022/23->2023/24 and
  2022/23+2023/24->2024/25.
- A_FAST100 labels are read only if the historical gate passes.
- B_CONFIRM300 and C_SEALED100 labels are never read/scored.
- No league dropping, confidence filtering, seed replacement or A100 tuning.
- Retrospective closing odds remain research-only.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as gold0  # noqa: E402
import build_gold500_feature_library_v6361 as gold1  # noqa: E402
import dynamic_strength_oof_screen_v470 as dyn  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402
from platform_core import read_processed_matches  # noqa: E402

OUT = ROOT / "manifests" / "v6_newinfo_manager_task_gold500_v6450_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
ALL_SEASONS = (*TRAIN_SEASONS, "2025/26")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
TEST_SEASON = "2025/26"
PART = "A_FAST100"
EPS = 1e-12
RIDGES = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
FEATURE_SPECS = ("manager_only", "task_only", "manager_task")
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
HIST_MEAN_UPLIFT_MIN_PP = 0.5
HIST_MIN_FOLD_UPLIFT_PP = 0.0
FAST_REQUIRED_UPLIFT_PP = 3.0

MANAGER_NAMES = (
    "home_manager_known",
    "away_manager_known",
    "home_new_manager",
    "away_new_manager",
    "new_manager_diff",
    "home_tenure_log",
    "away_tenure_log",
    "tenure_log_diff",
    "home_manager_club_prior_log",
    "away_manager_club_prior_log",
    "manager_club_ppg_diff",
    "manager_club_gdpg_diff",
    "home_manager_global_prior_log",
    "away_manager_global_prior_log",
    "manager_global_ppg_diff",
    "manager_global_gdpg_diff",
)

TASK_NAMES = (
    "home_games_played",
    "away_games_played",
    "home_ppg",
    "away_ppg",
    "ppg_diff",
    "home_gdpg",
    "away_gdpg",
    "gdpg_diff",
    "home_rank_frac",
    "away_rank_frac",
    "rank_frac_diff",
    "points_diff",
    "home_gap_leader",
    "away_gap_leader",
    "home_gap_top4",
    "away_gap_top4",
    "home_gap_danger_zone",
    "away_gap_danger_zone",
    "home_remaining_frac",
    "away_remaining_frac",
    "remaining_frac_diff",
    "late_season",
    "abs_ppg_diff",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_fast100_labels_only(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("Gold label file ended before A_FAST100 completed")
            row = json.loads(line)
            if str(row.get("partition")) != PART:
                raise RuntimeError(f"non-A label encountered: {row.get('partition')}")
            out[int(row["gold_index"])] = row
    if len(out) != 100 or set(out) != set(range(100)):
        raise RuntimeError("A_FAST100 label contract changed")
    return out


def season_label(raw: Any) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if "/" in s:
        return s
    try:
        y = int(float(s))
    except ValueError:
        return None
    return f"{y}/{str(y + 1)[-2:]}"


def iso_date(raw: Any) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    s = s[:10]
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        return None


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def market_geometry(p: list[float] | np.ndarray, cid: str, leagues: tuple[str, ...]) -> list[float]:
    p = [float(x) for x in p]
    side = math.log(max(EPS, p[0]) / max(EPS, p[2]))
    draw = math.log(max(EPS, p[1]) / max(EPS, 1.0 - p[1]))
    s = sorted(p, reverse=True)
    margin = s[0] - s[1]
    dummies = [1.0 if cid == league else 0.0 for league in leagues[:-1]]
    return [1.0, p[0], p[1], p[2], side, draw, margin, entropy(p), *dummies]


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter()
    actual = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]
        y = int(row["y"])
        if len(p) != 3 or abs(sum(p) - 1.0) > 1e-7 or min(p) < 0.0:
            raise RuntimeError(f"invalid probability vector {p}")
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1
        actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n,
        "brier": brier / n,
        "logloss": logloss / n,
        "rps": rps / n,
        "predicted_counts": dict(predicted),
        "actual_counts": dict(actual),
    }


def _side_manager_snapshot(
    club: str,
    manager: str,
    last_manager: dict[str, str],
    tenure: dict[str, int],
    club_mgr: dict[tuple[str, str], list[float]],
    manager_all: dict[str, list[float]],
) -> dict[str, float]:
    known = 1.0 if manager else 0.0
    prev = last_manager.get(club, "")
    new = 1.0 if manager and prev and manager != prev else 0.0
    t = float(tenure.get(club, 0)) if manager and manager == prev else 0.0
    cs = club_mgr.get((club, manager), [0.0, 0.0, 0.0]) if manager else [0.0, 0.0, 0.0]
    gs = manager_all.get(manager, [0.0, 0.0, 0.0]) if manager else [0.0, 0.0, 0.0]
    cn = float(cs[0]); gn = float(gs[0])
    return {
        "known": known,
        "new": new,
        "tenure_log": math.log1p(t),
        "club_prior_log": math.log1p(cn),
        "club_ppg": float(cs[1]) / cn if cn > 0 else 1.5,
        "club_gdpg": float(cs[2]) / cn if cn > 0 else 0.0,
        "global_prior_log": math.log1p(gn),
        "global_ppg": float(gs[1]) / gn if gn > 0 else 1.5,
        "global_gdpg": float(gs[2]) / gn if gn > 0 else 0.0,
    }


def build_manager_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build same-day-safe manager snapshots directly in Transfermarkt identity space."""
    cfg = v633.load_json(dyn.EVIDENCE_CONFIG)
    out: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}

    for cid in v633.v6280.COMPS:
        route = cfg["competition_mapping"][cid]
        external_id = str(route["transfermarkt_competition_id"])
        path = dyn.download("games", cfg, v633.CACHE)
        raw_games = []
        for raw in dyn.csv_rows(path):
            if str(raw.get("competition_id") or "") != external_id:
                continue
            season = season_label(raw.get("season"))
            if season not in ALL_SEASONS:
                continue
            date = iso_date(raw.get("date"))
            if not date:
                continue
            try:
                gid = int(float(str(raw.get("game_id") or "")))
            except ValueError:
                continue
            hclub = str(raw.get("home_club_id") or "").strip()
            aclub = str(raw.get("away_club_id") or "").strip()
            hname = str(raw.get("home_club_name") or "").strip()
            aname = str(raw.get("away_club_name") or "").strip()
            if not hclub or not aclub or not hname or not aname:
                continue
            hm = str(raw.get("home_club_manager_name") or "").strip()
            am = str(raw.get("away_club_manager_name") or "").strip()
            try:
                hg = int(float(str(raw.get("home_club_goals") or "0")))
                ag = int(float(str(raw.get("away_club_goals") or "0")))
            except ValueError:
                continue
            raw_games.append({
                "competition_id": cid,
                "season": season,
                "date": date,
                "game_id": gid,
                "home_club_id": hclub,
                "away_club_id": aclub,
                "home_name": hname,
                "away_name": aname,
                "home_manager": hm,
                "away_manager": am,
                "home_goals": hg,
                "away_goals": ag,
            })

        raw_games.sort(key=lambda r: (r["date"], r["game_id"]))
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in raw_games:
            by_date[str(r["date"])].append(r)

        last_manager: dict[str, str] = {}
        tenure: dict[str, int] = defaultdict(int)
        club_mgr: dict[tuple[str, str], list[float]] = {}
        manager_all: dict[str, list[float]] = {}
        missing_manager = 0

        for date in sorted(by_date):
            pending: list[dict[str, Any]] = []
            for r in by_date[date]:
                hs = _side_manager_snapshot(
                    str(r["home_club_id"]), str(r["home_manager"]), last_manager, tenure, club_mgr, manager_all
                )
                as_ = _side_manager_snapshot(
                    str(r["away_club_id"]), str(r["away_manager"]), last_manager, tenure, club_mgr, manager_all
                )
                if not r["home_manager"] or not r["away_manager"]:
                    missing_manager += 1
                mf = [
                    hs["known"], as_["known"],
                    hs["new"], as_["new"], hs["new"] - as_["new"],
                    hs["tenure_log"], as_["tenure_log"], hs["tenure_log"] - as_["tenure_log"],
                    hs["club_prior_log"], as_["club_prior_log"],
                    hs["club_ppg"] - as_["club_ppg"], hs["club_gdpg"] - as_["club_gdpg"],
                    hs["global_prior_log"], as_["global_prior_log"],
                    hs["global_ppg"] - as_["global_ppg"], hs["global_gdpg"] - as_["global_gdpg"],
                ]
                rr = dict(r)
                rr["manager_features"] = [float(x) for x in mf]
                out.append(rr)
                pending.append(r)

            # Same date is frozen first; update only after every row on this date is snapshotted.
            for r in pending:
                hg = int(r["home_goals"]); ag = int(r["away_goals"])
                hpts = 3.0 if hg > ag else 1.0 if hg == ag else 0.0
                apts = 3.0 if ag > hg else 1.0 if hg == ag else 0.0
                gd = float(hg - ag)
                for club, manager, pts, team_gd in (
                    (str(r["home_club_id"]), str(r["home_manager"]), hpts, gd),
                    (str(r["away_club_id"]), str(r["away_manager"]), apts, -gd),
                ):
                    if not manager:
                        continue
                    if last_manager.get(club, "") == manager:
                        tenure[club] += 1
                    else:
                        last_manager[club] = manager
                        tenure[club] = 1
                    ck = (club, manager)
                    cs = club_mgr.setdefault(ck, [0.0, 0.0, 0.0])
                    cs[0] += 1.0; cs[1] += pts; cs[2] += team_gd
                    gs = manager_all.setdefault(manager, [0.0, 0.0, 0.0])
                    gs[0] += 1.0; gs[1] += pts; gs[2] += team_gd

        audit[cid] = {
            "rows": len(raw_games),
            "missing_manager_side_or_pair_rows": missing_manager,
            "seasons": dict(Counter(str(r["season"]) for r in raw_games)),
        }

    if any(len(r["manager_features"]) != len(MANAGER_NAMES) for r in out):
        raise RuntimeError("manager feature length mismatch")
    return out, audit


def _rank_fraction(states: dict[str, dict[str, float]], team: str) -> float:
    teams = list(states)
    if len(teams) <= 1:
        return 0.0
    me = states[team]
    me_key = (me["pts"], me["gf"] - me["ga"], me["gf"])
    better = 0
    for other in teams:
        if other == team:
            continue
        s = states[other]
        key = (s["pts"], s["gf"] - s["ga"], s["gf"])
        if key > me_key:
            better += 1
    return float(better) / float(len(teams) - 1)


def build_task_lookup() -> tuple[dict[tuple[str, str, str, str, str], list[float]], dict[str, Any]]:
    """Reconstruct strict pre-match table state from prior completed dates only."""
    lookup: dict[tuple[str, str, str, str, str], list[float]] = {}
    audit: dict[str, Any] = {}

    for cid in sorted(v632.DOMAINS):
        all_matches = [m for m in read_processed_matches(cid) if str(m.season) in ALL_SEASONS]
        comp_counts = Counter()
        for season in ALL_SEASONS:
            matches = [m for m in all_matches if str(m.season) == season]
            if not matches:
                continue
            matches.sort(key=lambda m: (m.date, m.home_team, m.away_team))
            teams = sorted({v632._token(cid, m.home_team) for m in matches} | {v632._token(cid, m.away_team) for m in matches})
            total_fixtures = Counter()
            for m in matches:
                total_fixtures[v632._token(cid, m.home_team)] += 1
                total_fixtures[v632._token(cid, m.away_team)] += 1
            state = {t: {"gp": 0.0, "pts": 0.0, "gf": 0.0, "ga": 0.0} for t in teams}
            by_date: dict[str, list[Any]] = defaultdict(list)
            for m in matches:
                by_date[m.date.date().isoformat()].append(m)

            for date in sorted(by_date):
                # Thresholds are based only on the table frozen before this date.
                ordered = sorted(
                    teams,
                    key=lambda t: (
                        -state[t]["pts"],
                        -(state[t]["gf"] - state[t]["ga"]),
                        -state[t]["gf"],
                        t,
                    ),
                )
                leader_pts = state[ordered[0]]["pts"] if ordered else 0.0
                fourth_pts = state[ordered[min(3, len(ordered) - 1)]]["pts"] if ordered else 0.0
                danger_index = max(0, len(ordered) - 3)
                danger_pts = state[ordered[danger_index]]["pts"] if ordered else 0.0

                pending = []
                for m in sorted(by_date[date], key=lambda z: (z.home_team, z.away_team)):
                    h = v632._token(cid, m.home_team); a = v632._token(cid, m.away_team)
                    hs = state[h]; as_ = state[a]
                    hgp = hs["gp"]; agp = as_["gp"]
                    hppg = hs["pts"] / hgp if hgp > 0 else 1.5
                    appg = as_["pts"] / agp if agp > 0 else 1.5
                    hgdpg = (hs["gf"] - hs["ga"]) / hgp if hgp > 0 else 0.0
                    agdpg = (as_["gf"] - as_["ga"]) / agp if agp > 0 else 0.0
                    hrem = max(0.0, float(total_fixtures[h]) - hgp)
                    arem = max(0.0, float(total_fixtures[a]) - agp)
                    htot = max(1.0, float(total_fixtures[h])); atot = max(1.0, float(total_fixtures[a]))
                    hrank = _rank_fraction(state, h); arank = _rank_fraction(state, a)
                    tf = [
                        hgp, agp,
                        hppg, appg, hppg - appg,
                        hgdpg, agdpg, hgdpg - agdpg,
                        hrank, arank, hrank - arank,
                        hs["pts"] - as_["pts"],
                        hs["pts"] - leader_pts, as_["pts"] - leader_pts,
                        hs["pts"] - fourth_pts, as_["pts"] - fourth_pts,
                        hs["pts"] - danger_pts, as_["pts"] - danger_pts,
                        hrem / htot, arem / atot, hrem / htot - arem / atot,
                        1.0 if min(hrem, arem) <= 8.0 else 0.0,
                        abs(hppg - appg),
                    ]
                    key = (cid, season, date, h, a)
                    if key in lookup:
                        raise RuntimeError(f"duplicate task key {key}")
                    lookup[key] = [float(x) for x in tf]
                    pending.append((m, h, a))

                for m, h, a in pending:
                    hg = int(m.home_goals); ag = int(m.away_goals)
                    hpts = 3.0 if hg > ag else 1.0 if hg == ag else 0.0
                    apts = 3.0 if ag > hg else 1.0 if hg == ag else 0.0
                    state[h]["gp"] += 1.0; state[a]["gp"] += 1.0
                    state[h]["pts"] += hpts; state[a]["pts"] += apts
                    state[h]["gf"] += hg; state[h]["ga"] += ag
                    state[a]["gf"] += ag; state[a]["ga"] += hg
            comp_counts[season] = len(matches)
        audit[cid] = {"matches_by_season": dict(comp_counts)}

    if any(len(v) != len(TASK_NAMES) for v in lookup.values()):
        raise RuntimeError("task feature length mismatch")
    return lookup, audit


def manager_map_for_base(
    base_rows: list[dict[str, Any]],
    manager_rows: list[dict[str, Any]],
    season: str,
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    bseason = [r for r in base_rows if str(r.get("season")) == season]
    mseason = [r for r in manager_rows if str(r.get("season")) == season]
    mapping, audit = gold1.build_crosswalk(bseason, mseason)
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    misses = Counter()
    for r in mseason:
        cid = str(r["competition_id"])
        mh = mapping.get(cid, {}).get(str(r["home_name"]))
        ma = mapping.get(cid, {}).get(str(r["away_name"]))
        if not mh or not ma:
            misses["unmapped"] += 1
            continue
        key = (cid, str(r["date"]), v632._token(cid, mh), v632._token(cid, ma))
        if key in out:
            misses["duplicate"] += 1
            continue
        out[key] = r
    return out, {"crosswalk": audit, "map_rows": len(out), "misses": dict(misses)}


def build_historical_rows() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    base_rows, base_audit, base_feature_names = v632._build_rows()
    historical_base = [dict(r) for r in base_rows if str(r["season"]) in TRAIN_SEASONS]
    manager_rows, manager_audit = build_manager_rows()
    task_lookup, task_audit = build_task_lookup()

    joined: list[dict[str, Any]] = []
    manager_join_audit: dict[str, Any] = {}
    misses = Counter()
    for season in TRAIN_SEASONS:
        mmap, maudit = manager_map_for_base(historical_base, manager_rows, season)
        manager_join_audit[season] = maudit
        for row in [r for r in historical_base if str(r["season"]) == season]:
            cid = str(row["competition_id"])
            htok = v632._token(cid, str(row["home_team"])); atok = v632._token(cid, str(row["away_team"]))
            key4 = (cid, str(row["date"]), htok, atok)
            mr = mmap.get(key4)
            if mr is None:
                misses["manager"] += 1
                continue
            tk = (cid, season, str(row["date"]), htok, atok)
            tf = task_lookup.get(tk)
            if tf is None:
                misses["task"] += 1
                continue
            if list(row["actual_score"]) != [int(mr["home_goals"]), int(mr["away_goals"])]:
                misses["manager_score_disagreement"] += 1
                continue
            joined.append({
                "competition_id": cid,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "market": [float(x) for x in row["market"]],
                "manager_features": [float(x) for x in mr["manager_features"]],
                "task_features": [float(x) for x in tf],
                "y": int(row["y"]),
                "actual_score": [int(x) for x in row["actual_score"]],
            })

    by_season = Counter(str(r["season"]) for r in joined)
    if any(by_season.get(s, 0) < 1000 for s in TRAIN_SEASONS):
        raise RuntimeError(f"V6.45 historical coverage too small {dict(by_season)} misses={dict(misses)}")
    return joined, {
        "base_feature_count": len(base_feature_names),
        "base_audit_status": base_audit.get("status") if isinstance(base_audit, dict) else None,
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(Counter(str(r["competition_id"]) for r in joined)),
        "misses": dict(misses),
        "manager_source_audit": manager_audit,
        "manager_join_audit": manager_join_audit,
        "task_audit": task_audit,
    }, manager_rows, {"task_lookup": task_lookup, "task_audit": task_audit}


def _raw_features(row: dict[str, Any], spec: str) -> np.ndarray:
    m = np.asarray(row["manager_features"], dtype=float)
    t = np.asarray(row["task_features"], dtype=float)
    if spec == "manager_only":
        return m
    if spec == "task_only":
        return t
    if spec == "manager_task":
        return np.concatenate([m, t])
    raise RuntimeError(f"unknown spec {spec}")


class NewInfoTransform:
    def __init__(self, leagues: tuple[str, ...], spec: str):
        self.leagues = leagues
        self.spec = spec
        self.market_coef: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None

    def fit(self, rows: list[dict[str, Any]]) -> "NewInfoTransform":
        mg = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        raw = np.asarray([_raw_features(r, self.spec) for r in rows], dtype=float)
        self.market_coef = np.linalg.lstsq(mg, raw, rcond=None)[0]
        resid = raw - mg @ self.market_coef
        self.mean = resid.mean(axis=0)
        self.scale = resid.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.market_coef is None or self.mean is None or self.scale is None:
            raise RuntimeError("transform not fitted")
        mg = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        raw = np.asarray([_raw_features(r, self.spec) for r in rows], dtype=float)
        resid = raw - mg @ self.market_coef
        z = (resid - self.mean) / self.scale
        return np.column_stack([np.ones(len(z)), z])


def softmax(logits: np.ndarray) -> np.ndarray:
    x = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)


def fit_offset(rows: list[dict[str, Any]], z: np.ndarray, ridge: float) -> np.ndarray:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    y = np.asarray([int(r["y"]) for r in rows], dtype=int)
    offset = np.log(np.clip(market, EPS, 1.0))
    d = z.shape[1]

    def fun(flat: np.ndarray) -> tuple[float, np.ndarray]:
        w = flat.reshape(3, d)
        probs = softmax(offset + z @ w.T)
        n = len(y)
        loss = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)).mean()
        penalty = float(ridge) * float(np.sum(w[:, 1:] ** 2))
        diff = probs.copy(); diff[np.arange(n), y] -= 1.0
        grad = (diff.T @ z) / n
        grad[:, 1:] += 2.0 * float(ridge) * w[:, 1:]
        return float(loss + penalty), grad.ravel()

    res = minimize(lambda q: fun(q), np.zeros((3, d), dtype=float).ravel(), jac=True, method="L-BFGS-B", options={"maxiter": 600, "ftol": 1e-11})
    if not res.success:
        raise RuntimeError(f"offset optimizer failed: {res.message}")
    return np.asarray(res.x, dtype=float).reshape(3, d)


def apply_offset(rows: list[dict[str, Any]], z: np.ndarray, w: np.ndarray, key: str = "candidate") -> None:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    probs = softmax(np.log(np.clip(market, EPS, 1.0)) + z @ w.T)
    for row, p in zip(rows, probs):
        row[key] = [float(x) for x in p]


def evaluate_fold(train: list[dict[str, Any]], val: list[dict[str, Any]], spec: str, ridge: float, leagues: tuple[str, ...]) -> dict[str, Any]:
    transform = NewInfoTransform(leagues, spec).fit(train)
    zt = transform.apply(train); zv = transform.apply(val)
    w = fit_offset(train, zt, ridge)
    trial = [dict(r) for r in val]
    apply_offset(trial, zv, w)
    cm = metrics(trial, "candidate"); mm = metrics(trial, "market")
    uplift = (cm["top1"] - mm["top1"]) * 100.0
    return {
        "candidate": cm,
        "market": mm,
        "top1_uplift_pp": uplift,
        "proper_guard": cm["logloss"] <= mm["logloss"] + PROPER_LOG_TOL and cm["rps"] <= mm["rps"] + PROPER_RPS_TOL,
    }


def build_gold_newinfo(
    gold_features: list[dict[str, Any]],
    manager_rows: list[dict[str, Any]],
    task_lookup: dict[tuple[str, str, str, str, str], list[float]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    base_like = [{
        "competition_id": str(r["competition_id"]),
        "season": TEST_SEASON,
        "date": str(r["date"]),
        "home_team": str(r["home_team"]),
        "away_team": str(r["away_team"]),
    } for r in gold_features]
    mmap, maudit = manager_map_for_base(base_like, manager_rows, TEST_SEASON)
    out: dict[int, dict[str, Any]] = {}
    misses = Counter()
    for r in gold_features:
        cid = str(r["competition_id"]); date = str(r["date"])
        htok = v632._token(cid, str(r["home_team"])); atok = v632._token(cid, str(r["away_team"]))
        mr = mmap.get((cid, date, htok, atok))
        if mr is None:
            misses["manager"] += 1
            continue
        tf = task_lookup.get((cid, TEST_SEASON, date, htok, atok))
        if tf is None:
            misses["task"] += 1
            continue
        out[int(r["gold_index"])] = {
            "manager_features": [float(x) for x in mr["manager_features"]],
            "task_features": [float(x) for x in tf],
        }
    return out, {"manager_join": maudit, "attached": len(out), "misses": dict(misses)}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    historical, build_audit, manager_rows, task_bundle = build_historical_rows()
    leagues = tuple(sorted({str(r["competition_id"]) for r in historical}))
    if len(leagues) != 5:
        raise RuntimeError(f"expected five leagues, got {leagues}")

    leaderboard = []
    for spec in FEATURE_SPECS:
        for ridge in RIDGES:
            folds = []
            for train_seasons, val_season in FOLDS:
                tr = [r for r in historical if str(r["season"]) in train_seasons]
                va = [r for r in historical if str(r["season"]) == val_season]
                folds.append(evaluate_fold(tr, va, spec, ridge, leagues))
            mean_uplift = sum(x["top1_uplift_pp"] for x in folds) / len(folds)
            min_uplift = min(x["top1_uplift_pp"] for x in folds)
            mean_candidate = sum(x["candidate"]["top1"] for x in folds) / len(folds)
            mean_market = sum(x["market"]["top1"] for x in folds) / len(folds)
            mean_log = sum(x["candidate"]["logloss"] for x in folds) / len(folds)
            mean_rps = sum(x["candidate"]["rps"] for x in folds) / len(folds)
            leaderboard.append({
                "spec": spec,
                "ridge": ridge,
                "mean_candidate_top1": mean_candidate,
                "mean_market_top1": mean_market,
                "mean_uplift_pp": mean_uplift,
                "min_fold_uplift_pp": min_uplift,
                "proper_guard": all(x["proper_guard"] for x in folds),
                "mean_candidate_logloss": mean_log,
                "mean_candidate_rps": mean_rps,
                "folds": folds,
            })

    eligible = [x for x in leaderboard if x["proper_guard"]]
    if not eligible:
        eligible = leaderboard
    selected = min(eligible, key=lambda x: (-x["mean_uplift_pp"], -x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"], FEATURE_SPECS.index(x["spec"]), RIDGES.index(x["ridge"])))
    leaderboard.sort(key=lambda x: (-x["mean_uplift_pp"], -x["mean_candidate_top1"], x["mean_candidate_logloss"]))
    historical_gate = bool(
        selected["proper_guard"]
        and float(selected["mean_uplift_pp"]) >= HIST_MEAN_UPLIFT_MIN_PP
        and float(selected["min_fold_uplift_pp"]) >= HIST_MIN_FOLD_UPLIFT_PP
    )

    payload: dict[str, Any] = {
        "schema_version": "V6.45.0-newinfo-manager-task-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_NEW_INFORMATION_MANAGER_TASK_MARKET_OFFSET",
        "research_question": "Do strict-PIT manager context and reconstructed pre-match league task state add stable 1X2 signal beyond closing market probabilities?",
        "governance_contract": {
            "CURRENT_unchanged": True,
            "transfermarkt_position_columns_used": False,
            "standings_reconstructed_from_prior_completed_dates_only": True,
            "same_day_updates_after_all_snapshots": True,
            "manager_history_prior_matches_only": True,
            "A_FAST100_labels_read_only_if_historical_gate_passes": True,
            "B_CONFIRM300_labels_read": False,
            "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_read": False,
            "confidence_filtering": False,
            "league_dropping": False,
            "seed_replacement": False,
            "A100_parameter_tuning": False,
            "retrospective_closing_market_research_only": True,
        },
        "feature_contract": {
            "manager_feature_names": list(MANAGER_NAMES),
            "task_feature_names": list(TASK_NAMES),
            "feature_specs": list(FEATURE_SPECS),
            "market_residualization": "OLS(new_information ~ market_geometry + league) on TRAIN fold only",
            "probability_model": "softmax(log(p_market) + W*z_new_information_residual)",
            "ridge_grid": list(RIDGES),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in FOLDS],
            "historical_gate": {"mean_uplift_min_pp": HIST_MEAN_UPLIFT_MIN_PP, "min_fold_uplift_pp": HIST_MIN_FOLD_UPLIFT_PP},
        },
        "build_audit": build_audit,
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
        "historical_gate_passed": historical_gate,
    }

    if not historical_gate:
        payload["fast100"] = {"opened": False, "reason": "historical gate failed; A_FAST100 labels not read"}
        payload["decision"] = "HISTORICAL_GATE_FAILED_A100_NOT_OPENED"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": "PASS",
            "decision": payload["decision"],
            "selected": {k: selected[k] for k in ("spec", "ridge", "mean_uplift_pp", "min_fold_uplift_pp", "proper_guard")},
            "historical_rows": build_audit["joined_by_season"],
        }, ensure_ascii=False, indent=2))
        return 0

    # Only now build/evaluate A_FAST100 labels.
    final_train = [r for r in historical if str(r["season"]) in TRAIN_SEASONS]
    transform = NewInfoTransform(leagues, str(selected["spec"])).fit(final_train)
    ztrain = transform.apply(final_train)
    w = fit_offset(final_train, ztrain, float(selected["ridge"]))

    features = load_jsonl(GOLD_FEATURES)
    if len(features) != 500:
        raise RuntimeError(f"Gold500 feature contract changed: {len(features)}")
    newinfo, gold_audit = build_gold_newinfo(features, manager_rows, task_bundle["task_lookup"])
    fast_features = [r for r in features if str(r["partition"]) == PART]
    if len(fast_features) != 100 or any(int(r["gold_index"]) not in newinfo for r in fast_features):
        raise RuntimeError(f"A100 new-information coverage incomplete: {gold_audit}")

    labels = load_fast100_labels_only(GOLD_LABELS)
    fast = []
    for r in fast_features:
        idx = int(r["gold_index"]); ni = newinfo[idx]; lab = labels[idx]
        fast.append({
            "gold_index": idx,
            "competition_id": str(r["competition_id"]),
            "date": str(r["date"]),
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "market": [float(x) for x in r["market"]],
            "formal": [float(x) for x in r["formal"]],
            "manager_features": ni["manager_features"],
            "task_features": ni["task_features"],
            "y": int(lab["label"]),
            "actual_score": [int(x) for x in lab["actual_score"]],
        })
    zfast = transform.apply(fast); apply_offset(fast, zfast, w)
    market_m = metrics(fast, "market"); formal_m = metrics(fast, "formal"); candidate_m = metrics(fast, "candidate")
    uplift_pp = (candidate_m["top1"] - market_m["top1"]) * 100.0
    top1_pass = candidate_m["hits"] >= 63 and candidate_m["hits"] >= market_m["hits"] + 3
    proper_pass = candidate_m["logloss"] <= market_m["logloss"] + PROPER_LOG_TOL and candidate_m["rps"] <= market_m["rps"] + PROPER_RPS_TOL
    gate = bool(top1_pass and proper_pass)

    changed = []
    for r in fast:
        mp = max(range(3), key=lambda i: r["market"][i]); cp = max(range(3), key=lambda i: r["candidate"][i])
        if mp != cp:
            changed.append({
                "gold_index": r["gold_index"], "competition_id": r["competition_id"], "date": r["date"],
                "home_team": r["home_team"], "away_team": r["away_team"], "actual_result": r["y"],
                "market_pick": mp, "candidate_pick": cp, "market_correct": mp == r["y"], "candidate_correct": cp == r["y"],
            })
    payload["gold_newinfo_audit"] = gold_audit
    payload["fast100"] = {
        "opened": True,
        "market": market_m,
        "formal": formal_m,
        "candidate": candidate_m,
        "candidate_vs_market_top1_pp": uplift_pp,
        "required_candidate_hits": 63,
        "required_market_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "top1_gate_pass": top1_pass,
        "proper_gate_pass": proper_pass,
        "gate_passed": gate,
        "changed_pick_count": len(changed),
        "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "decision": payload["decision"],
        "selected": {k: selected[k] for k in ("spec", "ridge", "mean_uplift_pp", "min_fold_uplift_pp", "proper_guard")},
        "fast100": {"market_hits": market_m["hits"], "formal_hits": formal_m["hits"], "candidate_hits": candidate_m["hits"], "uplift_pp": uplift_pp, "gate": gate},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
