#!/usr/bin/env python3
"""V6.47.0 Gold500 cross-competition schedule-load 1X2 challenge.

Research question
-----------------
Can strictly prior, cross-competition match-load information add stable 1X2 signal
beyond closing market probabilities?

Governance
----------
- Gold500 identity/seed/100+300+100 partition is unchanged.
- Only matches with date strictly BEFORE the target date contribute to load.
- Match results, goals, lineups and future schedule dates are not used as load inputs.
- Transfermarkt domestic position fields are not used.
- Historical selection uses 2022/23->2023/24 and
  2022/23+2023/24->2024/25 only.
- A_FAST100 labels are opened only if the historical gate passes.
- B_CONFIRM300/C_SEALED100 labels are never read.
- Research-only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
import urllib.request
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6361 as gold1  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402

OUT = ROOT / "manifests" / "v6_crosscompetition_load_gold500_v6470_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"
CACHE = Path("/tmp/football-v6470-cache")
GAMES_FILE = CACHE / "games.csv.gz"
GAMES_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
TEST_SEASON = "2025/26"
PART = "A_FAST100"
EPS = 1e-12
RIDGES = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
FEATURE_SPECS = ("counts", "rest_nonleague", "combined")
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
HIST_MEAN_UPLIFT_MIN_PP = 0.5
HIST_MIN_FOLD_UPLIFT_PP = 0.0
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_HITS = 3

TM_COMP = {
    "ENG_PremierLeague": "GB1",
    "GER_Bundesliga": "L1",
    "ITA_SerieA": "IT1",
    "FRA_Ligue1": "FR1",
    "ESP_LaLiga": "ES1",
}
SEASON_START = {"2022/23": 2022, "2023/24": 2023, "2024/25": 2024, "2025/26": 2025}

COUNT_NAMES: list[str] = []
for w in (3, 7, 14, 21):
    COUNT_NAMES += [f"home_games_{w}d", f"away_games_{w}d", f"games_diff_{w}d"]
REST_NONLEAGUE_NAMES = [
    "home_days_since_last", "away_days_since_last", "days_since_last_diff",
    "home_short_rest_3d", "away_short_rest_3d", "short_rest_diff_3d",
    "home_short_rest_4d", "away_short_rest_4d", "short_rest_diff_4d",
    "home_nonleague_7d", "away_nonleague_7d", "nonleague_diff_7d",
    "home_nonleague_14d", "away_nonleague_14d", "nonleague_diff_14d",
    "home_nonleague_21d", "away_nonleague_21d", "nonleague_diff_21d",
    "home_days_since_nonleague", "away_days_since_nonleague", "days_since_nonleague_diff",
    "home_last_match_nonleague", "away_last_match_nonleague", "last_match_nonleague_diff",
]
ALL_FEATURE_NAMES = COUNT_NAMES + REST_NONLEAGUE_NAMES


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
        raise RuntimeError(f"A_FAST100 label contract changed: {len(out)}")
    return out


def _download_games() -> Path:
    if GAMES_FILE.exists() and GAMES_FILE.stat().st_size > 0:
        return GAMES_FILE
    CACHE.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(GAMES_URL, headers={"User-Agent": "FASHI188-football-analysis/6.47"})
    with urllib.request.urlopen(req, timeout=180) as response, GAMES_FILE.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if not GAMES_FILE.exists() or GAMES_FILE.stat().st_size == 0:
        raise RuntimeError("Transfermarkt games download empty")
    return GAMES_FILE


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None


def load_all_games() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _download_games()
    rows: list[dict[str, Any]] = []
    bad = 0
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {"game_id", "competition_id", "season", "date", "home_club_id", "away_club_id", "home_club_name", "away_club_name"}
        if not required.issubset(fields):
            raise RuntimeError(f"unexpected Transfermarkt games schema: {sorted(fields)}")
        for raw in reader:
            d = _parse_date(raw.get("date"))
            hid = _int(raw.get("home_club_id")); aid = _int(raw.get("away_club_id")); season = _int(raw.get("season"))
            comp = str(raw.get("competition_id") or "").strip()
            if d is None or hid is None or aid is None or season is None or not comp:
                bad += 1
                continue
            rows.append({
                "game_id": _int(raw.get("game_id")), "competition_id": comp, "season": season, "date": d,
                "home_id": hid, "away_id": aid,
                "home_name": str(raw.get("home_club_name") or "").strip(),
                "away_name": str(raw.get("away_club_name") or "").strip(),
            })
    return rows, {
        "source_url": GAMES_URL,
        "source_file_bytes": path.stat().st_size,
        "rows": len(rows), "bad_rows": bad,
        "min_date": min((r["date"] for r in rows), default=None).isoformat() if rows else None,
        "max_date": max((r["date"] for r in rows), default=None).isoformat() if rows else None,
        "unique_competitions": len({r["competition_id"] for r in rows}),
        "result_columns_used_for_load": False,
    }


def build_histories(games: list[dict[str, Any]]) -> dict[int, list[tuple[date, str]]]:
    out: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for g in games:
        out[int(g["home_id"])].append((g["date"], str(g["competition_id"])))
        out[int(g["away_id"])].append((g["date"], str(g["competition_id"])))
    for club in out:
        out[club].sort()
    return out


def team_load(club_id: int, target: date, domestic_comp: str, histories: dict[int, list[tuple[date, str]]]) -> list[float]:
    hist = histories.get(int(club_id), [])
    idx = bisect_left(hist, (target, ""))
    prior = hist[:idx]
    recent: list[tuple[int, str]] = []
    for d, comp in reversed(prior):
        age = (target - d).days
        if age > 35:
            break
        if age >= 1:
            recent.append((age, comp))

    counts = {w: sum(1 for age, _ in recent if age <= w) for w in (3, 7, 14, 21)}
    nonleague = {w: sum(1 for age, comp in recent if age <= w and comp != domestic_comp) for w in (7, 14, 21)}
    days_last = min(35.0, float(recent[0][0])) if recent else 35.0
    nl_ages = [age for age, comp in recent if comp != domestic_comp]
    days_nl = min(35.0, float(min(nl_ages))) if nl_ages else 35.0
    last_nonleague = 1.0 if recent and recent[0][1] != domestic_comp else 0.0
    return [
        float(counts[3]), float(counts[7]), float(counts[14]), float(counts[21]),
        days_last, 1.0 if days_last <= 3.0 else 0.0, 1.0 if days_last <= 4.0 else 0.0,
        float(nonleague[7]), float(nonleague[14]), float(nonleague[21]),
        days_nl, last_nonleague,
    ]


def pair_load(h: list[float], a: list[float]) -> list[float]:
    # team vector: count3,count7,count14,count21,dayslast,short3,short4,nl7,nl14,nl21,daysnl,lastnl
    out: list[float] = []
    for i in range(4):
        out += [h[i], a[i], h[i] - a[i]]
    for i in (4, 5, 6):
        out += [h[i], a[i], h[i] - a[i]]
    for i in (7, 8, 9):
        out += [h[i], a[i], h[i] - a[i]]
    for i in (10, 11):
        out += [h[i], a[i], h[i] - a[i]]
    if len(out) != len(ALL_FEATURE_NAMES):
        raise RuntimeError(f"load feature length mismatch {len(out)} != {len(ALL_FEATURE_NAMES)}")
    return out


def tm_domestic_rows(games: list[dict[str, Any]], cid: str, season: str) -> list[dict[str, Any]]:
    comp = TM_COMP[cid]; sy = SEASON_START[season]
    return [
        {"competition_id": cid, "date": g["date"].isoformat(), "home_name": g["home_name"], "away_name": g["away_name"],
         "home_id": g["home_id"], "away_id": g["away_id"]}
        for g in games if g["competition_id"] == comp and int(g["season"]) == sy
    ]


def attach_load(base_rows: list[dict[str, Any]], games: list[dict[str, Any]], histories: dict[int, list[tuple[date, str]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    joined: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    misses = Counter()
    for season in sorted({str(r["season"]) for r in base_rows}):
        bseason = [r for r in base_rows if str(r["season"]) == season]
        tseason: list[dict[str, Any]] = []
        for cid in TM_COMP:
            tseason.extend(tm_domestic_rows(games, cid, season))
        mapping, cross = gold1.build_crosswalk(bseason, tseason)
        audit[season] = {cid: {
            "mapped_teams": int(meta["mapped_teams"]), "tm_teams": int(meta["tm_teams"]), "fd_teams": int(meta["fd_teams"]),
            "min_role_overlap": meta["min_role_overlap"], "min_schedule_margin": meta["min_schedule_margin"],
        } for cid, meta in cross.items()}

        tmap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in tseason:
            cid = str(row["competition_id"])
            mh = mapping.get(cid, {}).get(str(row["home_name"])); ma = mapping.get(cid, {}).get(str(row["away_name"]))
            if not mh or not ma:
                misses["unmapped_tm_game"] += 1
                continue
            key = (cid, str(row["date"]), v632._token(cid, mh), v632._token(cid, ma))
            tmap[key] = row

        for row in bseason:
            cid = str(row["competition_id"])
            key = (cid, str(row["date"]), v632._token(cid, str(row["home_team"])), v632._token(cid, str(row["away_team"])))
            tm = tmap.get(key)
            if tm is None:
                misses["target_tm_join"] += 1
                continue
            d = date.fromisoformat(str(row["date"]))
            hf = team_load(int(tm["home_id"]), d, TM_COMP[cid], histories)
            af = team_load(int(tm["away_id"]), d, TM_COMP[cid], histories)
            z = pair_load(hf, af)
            rr = dict(row)
            rr["load_features"] = z
            rr["tm_home_id"] = int(tm["home_id"]); rr["tm_away_id"] = int(tm["away_id"])
            joined.append(rr)
    return joined, {"crosswalk": audit, "misses": dict(misses)}


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


def spec_indexes(spec: str) -> list[int]:
    if spec == "counts":
        return list(range(len(COUNT_NAMES)))
    if spec == "rest_nonleague":
        return list(range(len(COUNT_NAMES), len(ALL_FEATURE_NAMES)))
    if spec == "combined":
        return list(range(len(ALL_FEATURE_NAMES)))
    raise RuntimeError(f"unknown feature spec {spec}")


class Transform:
    def __init__(self, leagues: tuple[str, ...], spec: str):
        self.leagues = leagues; self.spec = spec
        self.market_coef: np.ndarray | None = None; self.mean: np.ndarray | None = None; self.scale: np.ndarray | None = None
        self.idx = spec_indexes(spec)

    def fit(self, rows: list[dict[str, Any]]) -> "Transform":
        m = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        y = np.asarray([[float(r["load_features"][i]) for i in self.idx] for r in rows], dtype=float)
        self.market_coef = np.linalg.lstsq(m, y, rcond=None)[0]
        raw = y - m @ self.market_coef
        self.mean = raw.mean(axis=0); self.scale = raw.std(axis=0); self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.market_coef is None or self.mean is None or self.scale is None:
            raise RuntimeError("transform not fitted")
        m = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        y = np.asarray([[float(r["load_features"][i]) for i in self.idx] for r in rows], dtype=float)
        z = (y - m @ self.market_coef - self.mean) / self.scale
        return np.column_stack([np.ones(len(z)), z])


def softmax(logits: np.ndarray) -> np.ndarray:
    x = logits - logits.max(axis=1, keepdims=True); e = np.exp(x); return e / e.sum(axis=1, keepdims=True)


def fit_offset(rows: list[dict[str, Any]], z: np.ndarray, ridge: float) -> np.ndarray:
    market = np.asarray([r["market"] for r in rows], dtype=float); y = np.asarray([int(r["y"]) for r in rows], dtype=int)
    offset = np.log(np.clip(market, EPS, 1.0)); d = z.shape[1]
    def fun(flat: np.ndarray) -> tuple[float, np.ndarray]:
        w = flat.reshape(3, d); probs = softmax(offset + z @ w.T); n = len(y)
        loss = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)).mean() + float(ridge) * float(np.sum(w[:, 1:] ** 2))
        diff = probs.copy(); diff[np.arange(n), y] -= 1.0
        grad = (diff.T @ z) / n; grad[:, 1:] += 2.0 * float(ridge) * w[:, 1:]
        return float(loss), grad.ravel()
    res = minimize(lambda q: fun(q), np.zeros((3, d), dtype=float).ravel(), jac=True, method="L-BFGS-B", options={"maxiter": 600, "ftol": 1e-11})
    if not res.success:
        raise RuntimeError(f"offset optimizer failed: {res.message}")
    return np.asarray(res.x, dtype=float).reshape(3, d)


def apply_offset(rows: list[dict[str, Any]], z: np.ndarray, w: np.ndarray, key: str = "candidate") -> None:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    probs = softmax(np.log(np.clip(market, EPS, 1.0)) + z @ w.T)
    for row, p in zip(rows, probs): row[key] = [float(x) for x in p]


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows); hits = 0; brier = logloss = rps = 0.0; predicted = Counter(); actual = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]; y = int(row["y"]); pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y); predicted[str(pick)] += 1; actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3)); logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0); c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0); rps += (c1*c1 + c2*c2)/2.0
    return {"count": n, "hits": hits, "top1": hits/n, "brier": brier/n, "logloss": logloss/n, "rps": rps/n,
            "predicted_counts": dict(predicted), "actual_counts": dict(actual)}


def evaluate_fold(train: list[dict[str, Any]], val: list[dict[str, Any]], spec: str, ridge: float, leagues: tuple[str, ...]) -> dict[str, Any]:
    t = Transform(leagues, spec).fit(train); zt = t.apply(train); zv = t.apply(val); w = fit_offset(train, zt, ridge)
    trial = [dict(r) for r in val]; apply_offset(trial, zv, w)
    cm = metrics(trial, "candidate"); mm = metrics(trial, "market")
    return {"candidate": cm, "market": mm, "top1_uplift_pp": (cm["top1"]-mm["top1"])*100.0,
            "proper_guard": cm["logloss"] <= mm["logloss"] + PROPER_LOG_TOL and cm["rps"] <= mm["rps"] + PROPER_RPS_TOL}


def main() -> int:
    base_rows, base_audit, _ = v632._build_rows()
    base_rows = [dict(r) for r in base_rows if str(r["season"]) in TRAIN_SEASONS or str(r["season"]) == TEST_SEASON]
    games, games_audit = load_all_games(); histories = build_histories(games)
    joined, attach_audit = attach_load(base_rows, games, histories)
    by_season = Counter(str(r["season"]) for r in joined); by_comp = Counter(str(r["competition_id"]) for r in joined)
    if any(by_season.get(s, 0) < 900 for s in TRAIN_SEASONS):
        raise RuntimeError(f"historical joined coverage too small: {dict(by_season)}")
    historical = [r for r in joined if str(r["season"]) in TRAIN_SEASONS]
    leagues = tuple(sorted({str(r["competition_id"]) for r in historical}))

    leaderboard = []
    for spec in FEATURE_SPECS:
        for ridge in RIDGES:
            folds = []
            for train_seasons, val_season in FOLDS:
                tr = [r for r in historical if str(r["season"]) in train_seasons]; va = [r for r in historical if str(r["season"]) == val_season]
                folds.append(evaluate_fold(tr, va, spec, ridge, leagues))
            mean_uplift = sum(f["top1_uplift_pp"] for f in folds) / len(folds)
            leaderboard.append({
                "spec": spec, "ridge": ridge, "mean_uplift_pp": mean_uplift,
                "min_fold_uplift_pp": min(f["top1_uplift_pp"] for f in folds),
                "mean_candidate_top1": sum(f["candidate"]["top1"] for f in folds)/len(folds),
                "mean_market_top1": sum(f["market"]["top1"] for f in folds)/len(folds),
                "mean_candidate_logloss": sum(f["candidate"]["logloss"] for f in folds)/len(folds),
                "mean_candidate_rps": sum(f["candidate"]["rps"] for f in folds)/len(folds),
                "proper_guard": all(f["proper_guard"] for f in folds), "folds": folds,
            })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(eligible, key=lambda x: (-x["mean_candidate_top1"], -x["mean_uplift_pp"], x["mean_candidate_logloss"], x["mean_candidate_rps"], FEATURE_SPECS.index(x["spec"]), RIDGES.index(x["ridge"])))
    leaderboard.sort(key=lambda x: (-x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"]))
    hist_gate = bool(selected["proper_guard"] and selected["mean_uplift_pp"] >= HIST_MEAN_UPLIFT_MIN_PP and selected["min_fold_uplift_pp"] >= HIST_MIN_FOLD_UPLIFT_PP)

    fast_payload: dict[str, Any]
    decision: str
    if not hist_gate:
        fast_payload = {"opened": False, "reason": "historical gate failed; A_FAST100 labels not read"}
        decision = "HISTORICAL_GATE_FAILED_A100_NOT_OPENED"
    else:
        gold_features = load_jsonl(GOLD_FEATURES); fast_features = [r for r in gold_features if str(r["partition"]) == PART]
        if len(gold_features) != 500 or len(fast_features) != 100:
            raise RuntimeError(f"Gold500 contract changed total={len(gold_features)} fast={len(fast_features)}")
        test_joined = [r for r in joined if str(r["season"]) == TEST_SEASON]
        tmap = {(str(r["competition_id"]), str(r["date"]), v632._token(str(r["competition_id"]), str(r["home_team"])), v632._token(str(r["competition_id"]), str(r["away_team"]))): r for r in test_joined}
        labels = load_fast100_labels_only(GOLD_LABELS)
        fast: list[dict[str, Any]] = []
        for row in fast_features:
            cid = str(row["competition_id"]); key = (cid, str(row["date"]), v632._token(cid, str(row["home_team"])), v632._token(cid, str(row["away_team"])))
            src = tmap.get(key)
            if src is None: raise RuntimeError(f"Fast100 load feature join miss {key}")
            lab = labels[int(row["gold_index"])]
            fast.append({"gold_index": int(row["gold_index"]), "competition_id": cid, "date": str(row["date"]), "home_team": str(row["home_team"]), "away_team": str(row["away_team"]),
                         "market": [float(x) for x in row["market"]], "formal": [float(x) for x in row["formal"]], "load_features": list(src["load_features"]),
                         "y": int(lab["label"]), "actual_score": [int(x) for x in lab["actual_score"]]})
        final_train = historical; transform = Transform(leagues, str(selected["spec"])).fit(final_train); zt = transform.apply(final_train); w = fit_offset(final_train, zt, float(selected["ridge"])); zf = transform.apply(fast); apply_offset(fast, zf, w)
        mm = metrics(fast, "market"); fm = metrics(fast, "formal"); cm = metrics(fast, "candidate")
        top1_pass = cm["hits"] >= FAST_REQUIRED_HITS and cm["hits"] >= mm["hits"] + FAST_REQUIRED_UPLIFT_HITS
        proper_pass = cm["logloss"] <= mm["logloss"] + PROPER_LOG_TOL and cm["rps"] <= mm["rps"] + PROPER_RPS_TOL
        gate = bool(top1_pass and proper_pass)
        changed = []
        for r in fast:
            mp = max(range(3), key=lambda i: r["market"][i]); cp = max(range(3), key=lambda i: r["candidate"][i])
            if mp != cp:
                changed.append({"gold_index": r["gold_index"], "competition_id": r["competition_id"], "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"],
                                "actual_result": r["y"], "market_pick": mp, "candidate_pick": cp, "market_correct": mp == r["y"], "candidate_correct": cp == r["y"]})
        fast_payload = {"opened": True, "market": mm, "formal": fm, "candidate": cm, "candidate_vs_market_top1_pp": (cm["top1"]-mm["top1"])*100.0,
                        "top1_gate_pass": top1_pass, "proper_gate_pass": proper_pass, "gate_passed": gate, "changed_pick_count": len(changed), "changed_pick_audit": changed}
        decision = "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"

    payload = {
        "schema_version": "V6.47.0-crosscompetition-load-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_STRICT_PRIOR_CROSSCOMPETITION_LOAD",
        "governance_contract": {
            "CURRENT_unchanged": True, "Gold500_identity_partition_unchanged": True,
            "prior_completed_dates_only": True, "same_date_matches_excluded": True,
            "future_schedule_used": False, "match_results_used_for_load": False, "goals_used_for_load": False,
            "lineups_used_for_load": False, "transfermarkt_position_columns_used": False,
            "A_FAST100_labels_read_only_if_historical_gate_passes": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False, "C_SEALED100_labels_read": False,
            "confidence_filtering": False, "league_dropping": False, "seed_replacement": False, "A100_parameter_tuning": False,
            "retrospective_closing_market_research_only": True,
        },
        "feature_contract": {"feature_names": ALL_FEATURE_NAMES, "feature_specs": list(FEATURE_SPECS), "ridge_grid": list(RIDGES),
                             "market_residualization": "OLS(load_features ~ market_geometry + league) on TRAIN fold only",
                             "probability_model": "softmax(log(p_market)+W*z_load_residual)",
                             "historical_gate": {"mean_uplift_min_pp": HIST_MEAN_UPLIFT_MIN_PP, "min_fold_uplift_pp": HIST_MIN_FOLD_UPLIFT_PP}},
        "build_audit": {"base_audit_status": base_audit.get("status") if isinstance(base_audit, dict) else None,
                        "joined_by_season": dict(by_season), "joined_by_competition": dict(by_comp), "games_source": games_audit, "attach": attach_audit},
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
        "historical_gate_passed": hist_gate, "fast100": fast_payload, "decision": decision,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "joined_by_season": dict(by_season), "games_source": games_audit,
                      "selected": {"spec": selected["spec"], "ridge": selected["ridge"], "mean_uplift_pp": selected["mean_uplift_pp"], "min_fold_uplift_pp": selected["min_fold_uplift_pp"], "proper_guard": selected["proper_guard"]},
                      "historical_gate_passed": hist_gate, "fast100": fast_payload, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
