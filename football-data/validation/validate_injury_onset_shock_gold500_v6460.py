#!/usr/bin/env python3
"""V6.46.0 injury-onset-shock 1X2 challenge.

Research question
-----------------
Does genuinely new player-availability information add stable 1X2 signal beyond the
closing market when we use only injury *onset dates* that precede the match?

Evidence contract
-----------------
The public salimt/football-datasets Transfermarkt injury table exposes player_id,
season, injury_reason, from_date, end_date, days_missed and games_missed. It does not
expose a per-row contemporaneous publication timestamp in the public schema. For this
reason this challenger is retrospective research only and deliberately uses ONLY:
    player_id + from_date
It NEVER uses end_date, days_missed, games_missed or a retrospective "active injury"
flag. Same-day injury onsets are also excluded because their time relative to kickoff
is not known.

Player importance
-----------------
Expected core players are reconstructed using the exact V6.33 pre-match logic:
previous-season starter prior, strictly prior current-season starts, dated transfers
and the latest valuation dated <= target date. The target lineup is never used.

Governance
----------
- CURRENT V5.0.1 unchanged; formal_weight=0.
- historical selection only on 2022/23->2023/24 and
  2022/23+2023/24->2024/25;
- A_FAST100 labels are read only if historical mean uplift >=0.5pp, neither fold is
  negative, and proper-score guard passes;
- B_CONFIRM300 and C_SEALED100 labels are never read/scored;
- no confidence filtering, league dropping, seed replacement or A100 tuning.
"""
from __future__ import annotations

import csv
import json
import math
import sys
import urllib.request
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
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
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402

OUT = ROOT / "manifests" / "v6_injury_onset_shock_gold500_v6460_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"
CACHE = Path("/tmp/football-v6460-cache")
INJURY_FILE = CACHE / "player_injuries.csv"
INJURY_URL = "https://raw.githubusercontent.com/salimt/football-datasets/main/datalake/transfermarkt/player_injuries/player_injuries.csv"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
ALL_SEASONS = (*TRAIN_SEASONS, TEST_SEASON)
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
PART = "A_FAST100"
EPS = 1e-12
WINDOWS = (3, 7, 14, 30)
FEATURE_SPECS = ("count_only", "value_only", "count_value")
RIDGES = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
HIST_MEAN_UPLIFT_MIN_PP = 0.5
HIST_MIN_FOLD_UPLIFT_PP = 0.0
FAST_REQUIRED_UPLIFT_PP = 3.0
MIN_EXPECTED_CORE = 8

COUNT_NAMES = tuple(
    name
    for w in WINDOWS
    for name in (f"home_injury_onset_count_{w}d", f"away_injury_onset_count_{w}d", f"injury_onset_count_diff_{w}d")
)
VALUE_NAMES = tuple(
    name
    for w in WINDOWS
    for name in (f"home_injury_value_share_{w}d", f"away_injury_value_share_{w}d", f"injury_value_share_diff_{w}d")
)
FEATURE_NAMES = (*COUNT_NAMES, *VALUE_NAMES)


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


def parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def download_injuries() -> Path:
    if INJURY_FILE.exists() and INJURY_FILE.stat().st_size > 0:
        return INJURY_FILE
    CACHE.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(INJURY_URL, headers={"User-Agent": "FASHI188-football-analysis/6.46"})
    with urllib.request.urlopen(req, timeout=180) as response, INJURY_FILE.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if not INJURY_FILE.exists() or INJURY_FILE.stat().st_size < 1000:
        raise RuntimeError("injury source download missing or unexpectedly small")
    return INJURY_FILE


def load_injury_onsets(wanted_players: set[int]) -> tuple[dict[int, list[datetime]], dict[str, Any]]:
    path = download_injuries()
    by_player: dict[int, set[datetime]] = defaultdict(set)
    total = matched_rows = bad = future_fields_nonempty = 0
    min_date = max_date = None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {str(x).strip() for x in (reader.fieldnames or [])}
        required = {"player_id", "from_date"}
        forbidden_assumption = {"end_date", "days_missed", "games_missed"}
        if not required.issubset(fields):
            raise RuntimeError(f"unexpected injury schema: {sorted(fields)}")
        for row in reader:
            total += 1
            try:
                pid = int(str(row.get("player_id") or "").strip())
            except ValueError:
                bad += 1
                continue
            onset = parse_date(row.get("from_date"))
            if onset is None:
                bad += 1
                continue
            if any(str(row.get(k) or "").strip() for k in forbidden_assumption if k in fields):
                future_fields_nonempty += 1
            if pid not in wanted_players:
                continue
            by_player[pid].add(onset)
            matched_rows += 1
            min_date = onset if min_date is None or onset < min_date else min_date
            max_date = onset if max_date is None or onset > max_date else max_date
    out = {pid: sorted(rows) for pid, rows in by_player.items()}
    return out, {
        "source_url": INJURY_URL,
        "source_file_bytes": path.stat().st_size,
        "schema_fields": sorted(fields),
        "total_rows": total,
        "wanted_players": len(wanted_players),
        "players_with_onset_history": len(out),
        "matched_onset_rows": matched_rows,
        "bad_rows": bad,
        "min_matched_onset_date": min_date.date().isoformat() if min_date else None,
        "max_matched_onset_date": max_date.date().isoformat() if max_date else None,
        "rows_where_future_fields_exist_but_are_ignored": future_fields_nonempty,
        "used_columns": ["player_id", "from_date"],
        "explicitly_ignored_columns": ["season", "injury_reason", "end_date", "days_missed", "games_missed"],
    }


def expected_core(
    team_id: int,
    season: str,
    cutoff: datetime,
    indexes: dict[str, Any],
    data: dict[str, Any],
    valuations: dict[int, tuple[list[datetime], list[float]]],
) -> list[tuple[int, float, float]] | None:
    previous = indexes["previous"].get(season)
    if not previous or previous not in indexes["by_season"]:
        return None
    prior_counts: Counter = indexes["starter_counts"].get((previous, team_id), Counter())
    if not prior_counts:
        return None
    prior_end = indexes["season_end"][previous]
    incoming, outgoing = v633._transfer_active_adjustments(team_id, prior_end, cutoff, data)

    current_games = [g for g in indexes["by_season"].get(season, []) if g["date"] < cutoff and team_id in {g["home_id"], g["away_id"]}]
    current_scores: dict[int, float] = defaultdict(float)
    for age, game in enumerate(reversed(current_games[-12:])):
        weight = math.exp(-math.log(2.0) * age / 4.0)
        for player in data["starters"].get((game["game_id"], team_id), set()):
            current_scores[player] += weight

    max_prior = max(prior_counts.values()) if prior_counts else 1
    selection_score: dict[int, float] = {}
    pool = set(prior_counts) | set(current_scores) | incoming
    pool -= outgoing
    for player in pool:
        prior_component = 0.35 * float(prior_counts.get(player, 0)) / max(1.0, float(max_prior))
        current_component = float(current_scores.get(player, 0.0))
        incoming_component = 0.10 if player in incoming else 0.0
        selection_score[player] = current_component + prior_component + incoming_component

    valued = []
    for player in pool:
        value = v633._valuation(valuations, player, cutoff)
        if value is not None:
            valued.append((int(player), float(value), float(selection_score.get(player, 0.0))))
    if len(valued) < MIN_EXPECTED_CORE:
        return None
    core = sorted(valued, key=lambda x: (x[2], x[1]), reverse=True)[:11]
    if len(core) < MIN_EXPECTED_CORE:
        return None
    return core


def _has_recent_onset(dates: list[datetime] | None, cutoff: datetime, days: int) -> bool:
    if not dates:
        return False
    lo = cutoff - timedelta(days=int(days))
    idx = bisect_left(dates, lo)
    return idx < len(dates) and dates[idx] < cutoff


def injury_features(
    home_core: list[tuple[int, float, float]],
    away_core: list[tuple[int, float, float]],
    cutoff: datetime,
    injury_onsets: dict[int, list[datetime]],
) -> list[float]:
    counts: list[float] = []
    values: list[float] = []
    for days in WINDOWS:
        hc = [x for x in home_core if _has_recent_onset(injury_onsets.get(x[0]), cutoff, days)]
        ac = [x for x in away_core if _has_recent_onset(injury_onsets.get(x[0]), cutoff, days)]
        h_count = len(hc) / max(1.0, float(len(home_core)))
        a_count = len(ac) / max(1.0, float(len(away_core)))
        h_total = sum(float(x[1]) for x in home_core)
        a_total = sum(float(x[1]) for x in away_core)
        h_share = sum(float(x[1]) for x in hc) / max(EPS, h_total)
        a_share = sum(float(x[1]) for x in ac) / max(EPS, a_total)
        counts.extend([h_count, a_count, h_count - a_count])
        values.extend([h_share, a_share, h_share - a_share])
    out = [*counts, *values]
    if len(out) != len(FEATURE_NAMES):
        raise RuntimeError("injury feature length mismatch")
    return [float(x) for x in out]


def build_injury_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data_by_comp: dict[str, dict[str, Any]] = {}
    indexes_by_comp: dict[str, dict[str, Any]] = {}
    names_by_comp: dict[str, dict[int, tuple[str, str]]] = {}
    for cid in v633.v6280.COMPS:
        data = v633.load_domain_data(cid, v633.CACHE)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = v633.build_season_indexes(data)
        names_by_comp[cid] = gold0.game_name_lookup(cid, v633.CACHE)

    wanted_players = v633._all_player_ids(data_by_comp)
    valuations, valuation_audit = v633._load_valuations(wanted_players)
    injury_onsets, injury_audit = load_injury_onsets(wanted_players)

    rows: list[dict[str, Any]] = []
    missing_names = core_missing = 0
    by_comp_season = Counter()
    core_players = set()
    core_players_with_injury = set()

    for cid in v633.v6280.COMPS:
        data = data_by_comp[cid]; indexes = indexes_by_comp[cid]
        for season in ALL_SEASONS:
            for target in indexes["by_season"].get(season, []):
                names = names_by_comp[cid].get(int(target["game_id"]))
                if not names:
                    missing_names += 1
                    continue
                cutoff = target["date"]
                hc = expected_core(int(target["home_id"]), season, cutoff, indexes, data, valuations)
                ac = expected_core(int(target["away_id"]), season, cutoff, indexes, data, valuations)
                if hc is None or ac is None:
                    core_missing += 1
                    continue
                for pid, _, _ in (*hc, *ac):
                    core_players.add(pid)
                    if pid in injury_onsets:
                        core_players_with_injury.add(pid)
                fx = injury_features(hc, ac, cutoff, injury_onsets)
                hname, aname = names
                rows.append({
                    "competition_id": cid,
                    "season": season,
                    "date": cutoff.date().isoformat(),
                    "home_name": hname,
                    "away_name": aname,
                    "home_team_id": int(target["home_id"]),
                    "away_team_id": int(target["away_id"]),
                    "injury_features": fx,
                    "y": 0 if int(target["home_goals"]) > int(target["away_goals"]) else 1 if int(target["home_goals"]) == int(target["away_goals"]) else 2,
                    "actual_score": [int(target["home_goals"]), int(target["away_goals"])],
                })
                by_comp_season[f"{cid}:{season}"] += 1

    return rows, {
        "rows": len(rows),
        "rows_by_competition_season": dict(by_comp_season),
        "missing_game_names": missing_names,
        "core_missing": core_missing,
        "unique_expected_core_players": len(core_players),
        "expected_core_players_with_any_injury_onset": len(core_players_with_injury),
        "expected_core_injury_player_coverage": len(core_players_with_injury) / max(1, len(core_players)),
        "valuation_audit": valuation_audit,
        "injury_source_audit": injury_audit,
    }


def build_joined() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    base_rows, base_audit, _ = v632._build_rows()
    relevant_base = [dict(r) for r in base_rows if str(r["season"]) in ALL_SEASONS]
    injury_rows, injury_build_audit = build_injury_rows()
    joined: list[dict[str, Any]] = []
    join_audit: dict[str, Any] = {}
    misses = Counter()

    for season in ALL_SEASONS:
        bseason = [r for r in relevant_base if str(r["season"]) == season]
        iseason = [r for r in injury_rows if str(r["season"]) == season]
        mapping, audit = gold1.build_crosswalk(bseason, iseason)
        imap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for r in iseason:
            cid = str(r["competition_id"])
            mh = mapping.get(cid, {}).get(str(r["home_name"]))
            ma = mapping.get(cid, {}).get(str(r["away_name"]))
            if not mh or not ma:
                misses["unmapped_injury_identity"] += 1
                continue
            key = (cid, str(r["date"]), v632._token(cid, mh), v632._token(cid, ma))
            if key in imap:
                misses["duplicate_injury_key"] += 1
                continue
            imap[key] = r
        for row in bseason:
            cid = str(row["competition_id"])
            key = (cid, str(row["date"]), v632._token(cid, str(row["home_team"])), v632._token(cid, str(row["away_team"])))
            ir = imap.get(key)
            if ir is None:
                misses["injury_join_miss"] += 1
                continue
            if int(row["y"]) != int(ir["y"]) or list(row["actual_score"]) != list(ir["actual_score"]):
                raise RuntimeError(f"injury join result disagreement {key}")
            joined.append({
                "competition_id": cid,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "market": [float(x) for x in row["market"]],
                "formal": [float(x) for x in row["formal"]],
                "injury_features": [float(x) for x in ir["injury_features"]],
                "y": int(row["y"]),
                "actual_score": [int(x) for x in row["actual_score"]],
            })
        join_audit[season] = {
            cid: {
                "mapped_teams": int(meta["mapped_teams"]),
                "tm_teams": int(meta["tm_teams"]),
                "fd_teams": int(meta["fd_teams"]),
                "min_role_overlap": meta["min_role_overlap"],
                "min_schedule_margin": meta["min_schedule_margin"],
            }
            for cid, meta in audit.items()
        }

    by_season = Counter(str(r["season"]) for r in joined)
    if any(by_season.get(s, 0) < 900 for s in TRAIN_SEASONS):
        raise RuntimeError(f"V6.46 historical coverage too small: {dict(by_season)} misses={dict(misses)}")
    return joined, {
        "base_audit_status": base_audit.get("status") if isinstance(base_audit, dict) else None,
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(Counter(str(r["competition_id"]) for r in joined)),
        "misses": dict(misses),
        "crosswalk": join_audit,
        "injury_build": injury_build_audit,
    }, injury_rows


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def market_geometry(p: list[float] | np.ndarray, cid: str, leagues: tuple[str, ...]) -> list[float]:
    p = [float(x) for x in p]
    side = math.log(max(EPS, p[0]) / max(EPS, p[2]))
    draw = math.log(max(EPS, p[1]) / max(EPS, 1.0 - p[1]))
    s = sorted(p, reverse=True)
    dummies = [1.0 if cid == league else 0.0 for league in leagues[:-1]]
    return [1.0, p[0], p[1], p[2], side, draw, s[0] - s[1], entropy(p), *dummies]


def raw_features(row: dict[str, Any], spec: str) -> np.ndarray:
    x = np.asarray(row["injury_features"], dtype=float)
    n = len(COUNT_NAMES)
    if spec == "count_only":
        return x[:n]
    if spec == "value_only":
        return x[n:]
    if spec == "count_value":
        return x
    raise RuntimeError(f"unknown spec {spec}")


class Transform:
    def __init__(self, leagues: tuple[str, ...], spec: str):
        self.leagues = leagues
        self.spec = spec
        self.coef: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None

    def fit(self, rows: list[dict[str, Any]]) -> "Transform":
        mg = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        raw = np.asarray([raw_features(r, self.spec) for r in rows], dtype=float)
        self.coef = np.linalg.lstsq(mg, raw, rcond=None)[0]
        resid = raw - mg @ self.coef
        self.mean = resid.mean(axis=0)
        self.scale = resid.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.coef is None or self.mean is None or self.scale is None:
            raise RuntimeError("transform not fitted")
        mg = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        raw = np.asarray([raw_features(r, self.spec) for r in rows], dtype=float)
        resid = raw - mg @ self.coef
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
        loss = -np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1.0)).mean()
        penalty = float(ridge) * float(np.sum(w[:, 1:] ** 2))
        diff = probs.copy(); diff[np.arange(len(y)), y] -= 1.0
        grad = (diff.T @ z) / len(y)
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


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    hits = 0; brier = logloss = rps = 0.0
    predicted = Counter(); actual = Counter()
    for r in rows:
        p = [float(x) for x in r[key]]; y = int(r["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y); predicted[str(pick)] += 1; actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    n = len(rows)
    return {"count": n, "hits": hits, "top1": hits / n, "brier": brier / n, "logloss": logloss / n, "rps": rps / n, "predicted_counts": dict(predicted), "actual_counts": dict(actual)}


def evaluate_fold(train: list[dict[str, Any]], val: list[dict[str, Any]], spec: str, ridge: float, leagues: tuple[str, ...]) -> dict[str, Any]:
    transform = Transform(leagues, spec).fit(train)
    zt = transform.apply(train); zv = transform.apply(val)
    w = fit_offset(train, zt, ridge)
    trial = [dict(r) for r in val]; apply_offset(trial, zv, w)
    cm = metrics(trial, "candidate"); mm = metrics(trial, "market")
    return {
        "candidate": cm,
        "market": mm,
        "top1_uplift_pp": (cm["top1"] - mm["top1"]) * 100.0,
        "proper_guard": cm["logloss"] <= mm["logloss"] + PROPER_LOG_TOL and cm["rps"] <= mm["rps"] + PROPER_RPS_TOL,
    }


def gold_injury_map(features: list[dict[str, Any]], injury_rows: list[dict[str, Any]]) -> tuple[dict[int, list[float]], dict[str, Any]]:
    test_base = [{
        "competition_id": str(r["competition_id"]), "season": TEST_SEASON, "date": str(r["date"]),
        "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
    } for r in features]
    test_injury = [r for r in injury_rows if str(r["season"]) == TEST_SEASON]
    mapping, audit = gold1.build_crosswalk(test_base, test_injury)
    imap: dict[tuple[str, str, str, str], list[float]] = {}
    misses = Counter()
    for r in test_injury:
        cid = str(r["competition_id"])
        mh = mapping.get(cid, {}).get(str(r["home_name"])); ma = mapping.get(cid, {}).get(str(r["away_name"]))
        if not mh or not ma:
            misses["unmapped"] += 1
            continue
        key = (cid, str(r["date"]), v632._token(cid, mh), v632._token(cid, ma))
        imap[key] = [float(x) for x in r["injury_features"]]
    out = {}
    for r in features:
        cid = str(r["competition_id"]); key = (cid, str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
        fx = imap.get(key)
        if fx is None:
            misses["gold_join"] += 1
            continue
        out[int(r["gold_index"])] = fx
    return out, {"attached": len(out), "misses": dict(misses), "crosswalk": audit}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    joined, build_audit, injury_rows = build_joined()
    historical = [r for r in joined if str(r["season"]) in TRAIN_SEASONS]
    leagues = tuple(sorted({str(r["competition_id"]) for r in historical}))
    if len(leagues) != 5:
        raise RuntimeError(f"expected five Gold500 leagues, found {leagues}")

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
            leaderboard.append({
                "spec": spec, "ridge": ridge,
                "mean_candidate_top1": sum(x["candidate"]["top1"] for x in folds) / len(folds),
                "mean_market_top1": sum(x["market"]["top1"] for x in folds) / len(folds),
                "mean_uplift_pp": mean_uplift,
                "min_fold_uplift_pp": min_uplift,
                "proper_guard": all(x["proper_guard"] for x in folds),
                "mean_candidate_logloss": sum(x["candidate"]["logloss"] for x in folds) / len(folds),
                "mean_candidate_rps": sum(x["candidate"]["rps"] for x in folds) / len(folds),
                "folds": folds,
            })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(eligible, key=lambda x: (-x["mean_uplift_pp"], -x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"], FEATURE_SPECS.index(x["spec"]), RIDGES.index(x["ridge"])))
    leaderboard.sort(key=lambda x: (-x["mean_uplift_pp"], -x["mean_candidate_top1"], x["mean_candidate_logloss"]))
    historical_gate = bool(selected["proper_guard"] and selected["mean_uplift_pp"] >= HIST_MEAN_UPLIFT_MIN_PP and selected["min_fold_uplift_pp"] >= HIST_MIN_FOLD_UPLIFT_PP)

    payload: dict[str, Any] = {
        "schema_version": "V6.46.0-injury-onset-shock-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_RETROSPECTIVE_INJURY_ONSET_ONLY",
        "governance_contract": {
            "CURRENT_unchanged": True,
            "source_has_per_row_publication_timestamp": False,
            "formal_prematch_injury_snapshot_claimed": False,
            "used_injury_columns": ["player_id", "from_date"],
            "ignored_future_information_columns": ["end_date", "days_missed", "games_missed"],
            "injury_reason_used": False,
            "same_day_injury_onset_used": False,
            "target_match_lineup_used": False,
            "future_valuation_used": False,
            "future_transfer_used": False,
            "A_FAST100_labels_read_only_if_historical_gate_passes": True,
            "B_CONFIRM300_labels_read": False,
            "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_read": False,
            "confidence_filtering": False,
            "league_dropping": False,
            "seed_replacement": False,
            "A100_parameter_tuning": False,
        },
        "feature_contract": {
            "windows_days": list(WINDOWS),
            "feature_names": list(FEATURE_NAMES),
            "feature_specs": list(FEATURE_SPECS),
            "player_core": "V6.33 expected core reconstructed from prior lineups, dated transfers and PIT valuation",
            "market_residualization": "OLS(injury_features ~ market_geometry + league) on TRAIN fold only",
            "probability_model": "softmax(log(p_market) + W*z_injury_residual)",
            "ridge_grid": list(RIDGES),
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
            "status": "PASS", "decision": payload["decision"],
            "selected": {k: selected[k] for k in ("spec", "ridge", "mean_uplift_pp", "min_fold_uplift_pp", "proper_guard")},
            "historical_rows": build_audit["joined_by_season"],
            "injury_source": build_audit["injury_build"]["injury_source_audit"],
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = historical
    transform = Transform(leagues, str(selected["spec"])).fit(final_train)
    ztrain = transform.apply(final_train); w = fit_offset(final_train, ztrain, float(selected["ridge"]))
    features = load_jsonl(GOLD_FEATURES)
    if len(features) != 500:
        raise RuntimeError("Gold500 feature contract changed")
    injury_map, gold_audit = gold_injury_map(features, injury_rows)
    fast_features = [r for r in features if str(r["partition"]) == PART]
    if len(fast_features) != 100 or any(int(r["gold_index"]) not in injury_map for r in fast_features):
        raise RuntimeError(f"A100 injury coverage incomplete {gold_audit}")
    labels = load_fast100_labels_only(GOLD_LABELS)
    fast = []
    for r in fast_features:
        idx = int(r["gold_index"]); lab = labels[idx]
        fast.append({
            "gold_index": idx, "competition_id": str(r["competition_id"]), "date": str(r["date"]),
            "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "market": [float(x) for x in r["market"]], "formal": [float(x) for x in r["formal"]],
            "injury_features": injury_map[idx], "y": int(lab["label"]), "actual_score": [int(x) for x in lab["actual_score"]],
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
            changed.append({"gold_index": r["gold_index"], "competition_id": r["competition_id"], "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"], "actual_result": r["y"], "market_pick": mp, "candidate_pick": cp, "market_correct": mp == r["y"], "candidate_correct": cp == r["y"]})
    payload["gold_injury_audit"] = gold_audit
    payload["fast100"] = {
        "opened": True, "market": market_m, "formal": formal_m, "candidate": candidate_m,
        "candidate_vs_market_top1_pp": uplift_pp, "required_candidate_hits": 63,
        "required_market_uplift_pp": FAST_REQUIRED_UPLIFT_PP, "top1_gate_pass": top1_pass,
        "proper_gate_pass": proper_pass, "gate_passed": gate, "changed_pick_count": len(changed), "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "decision": payload["decision"],
        "selected": {k: selected[k] for k in ("spec", "ridge", "mean_uplift_pp", "min_fold_uplift_pp", "proper_guard")},
        "fast100": {"market_hits": market_m["hits"], "formal_hits": formal_m["hits"], "candidate_hits": candidate_m["hits"], "uplift_pp": uplift_pp, "gate": gate},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
