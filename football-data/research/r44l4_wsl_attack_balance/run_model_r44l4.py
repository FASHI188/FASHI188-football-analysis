#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SOURCE_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW = f"https://raw.githubusercontent.com/hudl/open-data/{SOURCE_COMMIT}/data"
COMPETITION_ID = 37
SEASONS = {
    "2018/2019": 4,
    "2019/2020": 42,
    "2020/2021": 90,
    "2023/2024": 281,
}
SEASON_ORDER = list(SEASONS)
TARGET_SEASONS = ["2019/2020", "2020/2021", "2023/2024"]
PREREG_SHA256 = "3091d3eb84e1d1f65ef48d319b61cb542fd9ca076bf79cecd154843519ded494"
SEED = 20260813
BOOTSTRAP_REPS = 5000
OUT = Path(os.environ.get("R44L4_MODEL_OUT", "r44l4_model_output"))
OUT.mkdir(parents=True, exist_ok=True)
PRELABEL = Path(os.environ.get("R44L4_PRELABEL_DIR", "r44l4_prelabel"))

BASE_FEATURES = [
    "home_flag",
    "home_team_roll5_xgf", "home_team_roll5_xga", "home_team_roll10_xgf", "home_team_roll10_xga",
    "away_team_roll5_xgf", "away_team_roll5_xga", "away_team_roll10_xgf", "away_team_roll10_xga",
    "roll5_xgf_diff", "roll5_xgf_absdiff", "roll5_xgf_sum", "roll10_xgf_absdiff",
]
XI_METRICS = [
    "xi_xg90_sum", "xi_xa90_sum", "xi_xgi90_sum", "xi_shots90_sum",
    "xi_shot_assists90_sum", "xi_top3_xgi_share", "xi_low_history_count",
]
PLAYER_FEATURES = []
for metric in XI_METRICS:
    PLAYER_FEATURES.extend([
        f"home_{metric}", f"away_{metric}", f"diff_{metric}", f"absdiff_{metric}", f"sum_{metric}"
    ])
PLAYER_FEATURES.append("min_xi_xgi90_sum")
ALL_FEATURES = BASE_FEATURES + PLAYER_FEATURES


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r44l4-model/1.0"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return resp.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_clock(value: object, default_end: bool = False) -> float | None:
    if value is None or str(value).strip() == "":
        return 90.0 if default_end else None
    text = str(value).strip()
    try:
        parts = text.split(":")
        if len(parts) == 2:
            return float(parts[0]) + float(parts[1]) / 60.0
        if len(parts) == 3:
            return float(parts[0]) * 60.0 + float(parts[1]) + float(parts[2]) / 60.0
    except ValueError:
        return None
    return None


def starts_at_zero(value: object) -> bool:
    x = parse_clock(value, default_end=False)
    return x is not None and abs(x) < 1e-9


def lineup_summary(teams: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for team in teams:
        tid = int(team.get("team_id", -1))
        starters = []
        minutes: dict[int, float] = {}
        for player in team.get("lineup", []):
            pid = int(player.get("player_id", -1))
            if pid <= 0:
                continue
            intervals = player.get("positions") or []
            if any(starts_at_zero(pos.get("from")) for pos in intervals):
                starters.append(pid)
            total = 0.0
            valid = True
            for pos in intervals:
                start = parse_clock(pos.get("from"), default_end=False)
                end = parse_clock(pos.get("to"), default_end=True)
                if start is None or end is None or end < start:
                    valid = False
                    continue
                total += end - start
            minutes[pid] = float(total) if valid and total > 0 else 0.0
        out[tid] = {"starters": tuple(sorted(starters)), "minutes": minutes}
    return out


def event_summary(events: list[dict]) -> dict:
    shot_xg_by_id: dict[str, float] = {}
    team_xg: dict[int, float] = defaultdict(float)
    player_stats: dict[int, dict[str, float]] = defaultdict(lambda: {"xg": 0.0, "xa": 0.0, "shots": 0.0, "shot_assists": 0.0})
    for event in events:
        etype = str((event.get("type") or {}).get("name") or "")
        if etype != "Shot":
            continue
        shot = event.get("shot") or {}
        xg = shot.get("statsbomb_xg")
        if not isinstance(xg, (int, float)):
            continue
        xg = float(xg)
        eid = str(event.get("id") or "")
        if eid:
            shot_xg_by_id[eid] = xg
        tid = int((event.get("team") or {}).get("id", -1))
        pid = int((event.get("player") or {}).get("id", -1))
        if tid > 0:
            team_xg[tid] += xg
        if pid > 0:
            player_stats[pid]["xg"] += xg
            player_stats[pid]["shots"] += 1.0
    for event in events:
        etype = str((event.get("type") or {}).get("name") or "")
        if etype != "Pass":
            continue
        p = event.get("pass") or {}
        sid = str(p.get("assisted_shot_id") or "")
        if not sid or sid not in shot_xg_by_id:
            continue
        pid = int((event.get("player") or {}).get("id", -1))
        if pid <= 0:
            continue
        player_stats[pid]["xa"] += shot_xg_by_id[sid]
        player_stats[pid]["shot_assists"] += 1.0
    return {"team_xg": dict(team_xg), "player_stats": dict(player_stats)}


def fetch_match_sources(meta: dict) -> dict:
    mid = int(meta["match_id"])
    lineup_bytes = fetch(f"{RAW}/lineups/{mid}.json")
    event_bytes = fetch(f"{RAW}/events/{mid}.json")
    lineups = json.loads(lineup_bytes.decode("utf-8"))
    events = json.loads(event_bytes.decode("utf-8"))
    return {
        "match_id": mid,
        "lineup": lineup_summary(lineups),
        "events": event_summary(events),
        "lineup_sha256": sha256(lineup_bytes),
        "event_sha256": sha256(event_bytes),
    }


def verify_prelabel() -> dict:
    path = PRELABEL / "zero_label_result_r44l4.json"
    if not path.exists():
        raise RuntimeError(f"missing prelabel result: {path}")
    result = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status": "PASS_R44L4_ZERO_LABEL_COVERAGE",
        "source_commit": SOURCE_COMMIT,
        "competition_id": COMPETITION_ID,
        "label_fields_accessed": 0,
        "model_fits": 0,
    }
    for key, expected in required.items():
        if result.get(key) != expected:
            raise RuntimeError(f"prelabel binding fail {key}: {result.get(key)!r} != {expected!r}")
    if not all(bool(v) for v in result.get("gates", {}).values()):
        raise RuntimeError("prelabel gates not all true")
    return result


def load_target_bearing_match_metadata() -> tuple[list[dict], list[dict]]:
    # Called only after verify_prelabel() has passed. This is the first target-bearing read.
    rows = []
    ledger = []
    for season_name, season_id in SEASONS.items():
        url = f"{RAW}/matches/{COMPETITION_ID}/{season_id}.json"
        data = fetch(url)
        objects = json.loads(data.decode("utf-8"))
        ledger.append({"season": season_name, "url": url, "sha256": sha256(data), "bytes": len(data), "rows": len(objects)})
        for obj in objects:
            comp_id = int((obj.get("competition") or {}).get("competition_id", -1))
            sid = int((obj.get("season") or {}).get("season_id", -1))
            if comp_id != COMPETITION_ID or sid != season_id:
                raise RuntimeError(f"identity drift season={season_name} match={obj.get('match_id')}")
            rows.append({
                "season": season_name,
                "season_id": season_id,
                "match_id": int(obj["match_id"]),
                "match_date": str(obj["match_date"]),
                "kick_off": str(obj.get("kick_off") or ""),
                "home_team_id": int(obj["home_team"]["home_team_id"]),
                "away_team_id": int(obj["away_team"]["away_team_id"]),
                "home_team_name": str(obj["home_team"]["home_team_name"]),
                "away_team_name": str(obj["away_team"]["away_team_name"]),
                "home_score": int(obj["home_score"]),
                "away_score": int(obj["away_score"]),
            })
    rows.sort(key=lambda r: (r["match_date"], r["kick_off"], r["match_id"]))
    if len(rows) != len({r["match_id"] for r in rows}):
        raise RuntimeError("duplicate target-bearing match ids")
    return rows, ledger


def mean_last(hist: deque[dict], key: str, n: int) -> float:
    vals = [float(x[key]) for x in list(hist)[-n:]]
    return float(np.mean(vals)) if vals else float("nan")


def player_profile(hist: deque[dict]) -> dict[str, float]:
    rows = list(hist)[-10:]
    minutes = float(sum(float(r["minutes"]) for r in rows))
    if minutes <= 0:
        return {"xg90": 0.0, "xa90": 0.0, "xgi90": 0.0, "shots90": 0.0, "shot_assists90": 0.0, "minutes": 0.0}
    scale = 90.0 / minutes
    xg = sum(float(r["xg"]) for r in rows)
    xa = sum(float(r["xa"]) for r in rows)
    shots = sum(float(r["shots"]) for r in rows)
    sa = sum(float(r["shot_assists"]) for r in rows)
    return {
        "xg90": float(xg * scale),
        "xa90": float(xa * scale),
        "xgi90": float((xg + xa) * scale),
        "shots90": float(shots * scale),
        "shot_assists90": float(sa * scale),
        "minutes": minutes,
    }


def xi_aggregate(starters: tuple[int, ...], player_hist: dict[int, deque[dict]]) -> dict[str, float]:
    profiles = [player_profile(player_hist[pid]) for pid in starters]
    xgis = [p["xgi90"] for p in profiles]
    total_xgi = float(sum(xgis))
    top3 = float(sum(sorted(xgis, reverse=True)[:3]))
    return {
        "xi_xg90_sum": float(sum(p["xg90"] for p in profiles)),
        "xi_xa90_sum": float(sum(p["xa90"] for p in profiles)),
        "xi_xgi90_sum": total_xgi,
        "xi_shots90_sum": float(sum(p["shots90"] for p in profiles)),
        "xi_shot_assists90_sum": float(sum(p["shot_assists90"] for p in profiles)),
        "xi_top3_xgi_share": float(top3 / total_xgi) if total_xgi > 1e-12 else 0.0,
        "xi_low_history_count": float(sum(p["minutes"] < 180.0 for p in profiles)),
    }


def build_feature_rows(matches: list[dict], sources: dict[int, dict]) -> list[dict]:
    team_hist: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=10))
    player_hist: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=10))
    rows: list[dict] = []

    by_date: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        by_date[m["match_date"]].append(m)

    for day in sorted(by_date):
        batch = sorted(by_date[day], key=lambda r: (r["kick_off"], r["match_id"]))
        pending_updates = []
        for m in batch:
            mid = int(m["match_id"])
            src = sources[mid]
            home = int(m["home_team_id"])
            away = int(m["away_team_id"])
            line = src["lineup"]
            home_line = line.get(home, {})
            away_line = line.get(away, {})
            home_xi = tuple(home_line.get("starters", ()))
            away_xi = tuple(away_line.get("starters", ()))
            eligible = len(team_hist[home]) >= 1 and len(team_hist[away]) >= 1 and len(home_xi) == 11 and len(away_xi) == 11
            row = {
                **m,
                "model_eligible": int(eligible),
                "home_prior_team_matches": len(team_hist[home]),
                "away_prior_team_matches": len(team_hist[away]),
            }
            if eligible:
                h5f = mean_last(team_hist[home], "xgf", 5); h5a = mean_last(team_hist[home], "xga", 5)
                h10f = mean_last(team_hist[home], "xgf", 10); h10a = mean_last(team_hist[home], "xga", 10)
                a5f = mean_last(team_hist[away], "xgf", 5); a5a = mean_last(team_hist[away], "xga", 5)
                a10f = mean_last(team_hist[away], "xgf", 10); a10a = mean_last(team_hist[away], "xga", 10)
                row.update({
                    "home_flag": 1.0,
                    "home_team_roll5_xgf": h5f, "home_team_roll5_xga": h5a,
                    "home_team_roll10_xgf": h10f, "home_team_roll10_xga": h10a,
                    "away_team_roll5_xgf": a5f, "away_team_roll5_xga": a5a,
                    "away_team_roll10_xgf": a10f, "away_team_roll10_xga": a10a,
                    "roll5_xgf_diff": h5f - a5f,
                    "roll5_xgf_absdiff": abs(h5f - a5f),
                    "roll5_xgf_sum": h5f + a5f,
                    "roll10_xgf_absdiff": abs(h10f - a10f),
                })
                hagg = xi_aggregate(home_xi, player_hist)
                aagg = xi_aggregate(away_xi, player_hist)
                for metric in XI_METRICS:
                    hv = float(hagg[metric]); av = float(aagg[metric])
                    row[f"home_{metric}"] = hv
                    row[f"away_{metric}"] = av
                    row[f"diff_{metric}"] = hv - av
                    row[f"absdiff_{metric}"] = abs(hv - av)
                    row[f"sum_{metric}"] = hv + av
                row["min_xi_xgi90_sum"] = min(hagg["xi_xgi90_sum"], aagg["xi_xgi90_sum"])
            score_diff = int(m["home_score"]) - int(m["away_score"])
            row["binary_target"] = 1 if score_diff == 0 else 0 if abs(score_diff) == 1 else None
            rows.append(row)
            pending_updates.append((m, src))

        # Same-day embargo: histories update only after every row for this day is frozen.
        for m, src in pending_updates:
            home = int(m["home_team_id"]); away = int(m["away_team_id"])
            team_xg = src["events"]["team_xg"]
            hxg = float(team_xg.get(home, 0.0)); axg = float(team_xg.get(away, 0.0))
            team_hist[home].append({"xgf": hxg, "xga": axg})
            team_hist[away].append({"xgf": axg, "xga": hxg})
            pstats = src["events"]["player_stats"]
            for tid in (home, away):
                tline = src["lineup"].get(tid, {})
                for pid, minutes in tline.get("minutes", {}).items():
                    minutes = float(minutes)
                    if minutes <= 0:
                        continue
                    ps = pstats.get(pid, {"xg": 0.0, "xa": 0.0, "shots": 0.0, "shot_assists": 0.0})
                    player_hist[int(pid)].append({
                        "minutes": minutes,
                        "xg": float(ps.get("xg", 0.0)),
                        "xa": float(ps.get("xa", 0.0)),
                        "shots": float(ps.get("shots", 0.0)),
                        "shot_assists": float(ps.get("shot_assists", 0.0)),
                    })
    return rows


def matrix(rows: list[dict], features: list[str]) -> np.ndarray:
    return np.asarray([[float(r.get(f, np.nan)) for f in features] for r in rows], dtype=float)


def model() -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=4000, random_state=SEED)),
    ])


def safe_auc(y: np.ndarray, p: np.ndarray) -> float:
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")


def safe_pr(y: np.ndarray, p: np.ndarray) -> float:
    return float(average_precision_score(y, p)) if len(np.unique(y)) == 2 else float("nan")


def calibration(y: np.ndarray, p: np.ndarray) -> dict:
    if len(y) < 3 or float(np.std(p)) < 1e-12:
        return {"intercept": None, "slope": None}
    slope, intercept = np.polyfit(p, y, 1)
    return {"intercept": float(intercept), "slope": float(slope)}


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "log_loss": float(log_loss(y, np.clip(p, 1e-9, 1 - 1e-9), labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "roc_auc": safe_auc(y, p),
        "pr_auc": safe_pr(y, p),
        "accuracy_at_0_5": float(np.mean((p >= 0.5).astype(int) == y)),
        "calibration": calibration(y, p),
    }


def row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    pp = np.clip(p, 1e-12, 1 - 1e-12)
    return -(y * np.log(pp) + (1 - y) * np.log(1 - pp))


def enrich(y: np.ndarray, p: np.ndarray, coverage: float) -> dict:
    n = max(1, int(math.ceil(len(y) * coverage)))
    idx = np.argsort(-p)[:n]
    return {"coverage": coverage, "selected": int(n), "rate": float(np.mean(y[idx])), "draws": int(np.sum(y[idx]))}


def main() -> int:
    prelabel = verify_prelabel()
    prereg_path = Path("football-data/research/r44l4_wsl_attack_balance/prereg_r44l4.md")
    if sha256(prereg_path.read_bytes()) != PREREG_SHA256:
        raise RuntimeError("prereg hash drift")

    # First target-bearing read happens here, after the exact zero-label gate is verified.
    matches, match_ledger = load_target_bearing_match_metadata()

    source_rows = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(fetch_match_sources, m): int(m["match_id"]) for m in matches}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            mid = futures[fut]
            source_rows[mid] = fut.result()
            if i % 50 == 0:
                print(f"FETCHED_MATCH_SOURCES {i}/{len(matches)}", flush=True)
    if len(source_rows) != len(matches):
        raise RuntimeError(f"source coverage drift {len(source_rows)} != {len(matches)}")

    features = build_feature_rows(matches, source_rows)
    eligible = [r for r in features if int(r["model_eligible"]) == 1 and r["binary_target"] is not None]

    sample_audit = {}
    total_target = 0
    total_draws = 0
    for target in TARGET_SEASONS:
        rr = [r for r in eligible if r["season"] == target]
        draws = sum(int(r["binary_target"]) for r in rr)
        sample_audit[target] = {"binary_rows": len(rr), "draws": draws}
        total_target += len(rr); total_draws += draws
    sample_gates = {
        "target_binary_ge_250": total_target >= 250,
        "target_draws_ge_60": total_draws >= 60,
        "each_fold_binary_ge_50": all(v["binary_rows"] >= 50 for v in sample_audit.values()),
        "each_fold_draws_ge_10": all(v["draws"] >= 10 for v in sample_audit.values()),
    }
    if not all(sample_gates.values()):
        result = {
            "study_id": "r44l4_wsl_attack_balance_external_domain",
            "status": "STOP_UNDERPOWERED_AFTER_LABEL_OPEN",
            "formal_weight": 0,
            "sample_audit": sample_audit,
            "sample_gates": sample_gates,
            "labels_opened": True,
            "sample_consumed": True,
            "prelabel_binding": prelabel,
        }
        (OUT / "model_result_r44l4.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 3

    fold_rows = []
    pred_rows = []
    for target in TARGET_SEASONS:
        ti = SEASON_ORDER.index(target)
        train_seasons = set(SEASON_ORDER[:ti])
        train = [r for r in eligible if r["season"] in train_seasons]
        test = [r for r in eligible if r["season"] == target]
        ytr = np.asarray([int(r["binary_target"]) for r in train], dtype=int)
        yte = np.asarray([int(r["binary_target"]) for r in test], dtype=int)
        if len(train) < 40 or len(np.unique(ytr)) < 2:
            raise RuntimeError(f"training insufficiency for {target}: n={len(train)} classes={np.unique(ytr)}")

        b0 = model(); l4 = model()
        b0.fit(matrix(train, BASE_FEATURES), ytr)
        l4.fit(matrix(train, ALL_FEATURES), ytr)
        pb = b0.predict_proba(matrix(test, BASE_FEATURES))[:, 1]
        pl = l4.predict_proba(matrix(test, ALL_FEATURES))[:, 1]
        mb = metrics(yte, pb); ml = metrics(yte, pl)
        fold_rows.append({
            "target_season": target,
            "train_seasons": sorted(train_seasons),
            "train_rows": len(train), "test_rows": len(test), "test_draws": int(yte.sum()),
            "baseline": mb, "challenger": ml,
            "delta_log_loss": ml["log_loss"] - mb["log_loss"],
            "delta_brier": ml["brier"] - mb["brier"],
            "delta_roc_auc": ml["roc_auc"] - mb["roc_auc"],
            "delta_pr_auc": ml["pr_auc"] - mb["pr_auc"],
        })
        llb = row_logloss(yte, pb); lll = row_logloss(yte, pl)
        for r, yy, p0, p1, d0, d1 in zip(test, yte, pb, pl, llb, lll):
            pred_rows.append({
                "match_id": int(r["match_id"]), "season": target, "match_date": r["match_date"],
                "home_team": r["home_team_name"], "away_team": r["away_team_name"],
                "binary_target": int(yy), "baseline_score": float(p0), "challenger_score": float(p1),
                "baseline_row_logloss": float(d0), "challenger_row_logloss": float(d1),
                "delta_row_logloss": float(d1 - d0),
            })

    y = np.asarray([r["binary_target"] for r in pred_rows], dtype=int)
    pb = np.asarray([r["baseline_score"] for r in pred_rows], dtype=float)
    pl = np.asarray([r["challenger_score"] for r in pred_rows], dtype=float)
    pooled_b = metrics(y, pb); pooled_l = metrics(y, pl)
    delta_ll = pooled_l["log_loss"] - pooled_b["log_loss"]
    delta_brier = pooled_l["brier"] - pooled_b["brier"]
    delta_auc = pooled_l["roc_auc"] - pooled_b["roc_auc"]
    delta_pr = pooled_l["pr_auc"] - pooled_b["pr_auc"]

    deltas = np.asarray([r["delta_row_logloss"] for r in pred_rows], dtype=float)
    rng = np.random.default_rng(SEED)
    boot = np.empty(BOOTSTRAP_REPS, dtype=float)
    for i in range(0, BOOTSTRAP_REPS, 500):
        k = min(500, BOOTSTRAP_REPS - i)
        idx = rng.integers(0, len(deltas), size=(k, len(deltas)))
        boot[i:i+k] = deltas[idx].mean(axis=1)
    boot_res = {
        "reps": BOOTSTRAP_REPS, "seed": SEED,
        "mean": float(boot.mean()), "p05": float(np.quantile(boot, 0.05)), "p95": float(np.quantile(boot, 0.95)),
    }
    fold_improve = sum(float(r["delta_log_loss"]) < 0 for r in fold_rows)
    gates = {
        "pooled_delta_logloss_negative": delta_ll < 0,
        "bootstrap_90_upper_negative": boot_res["p95"] < 0,
        "fold_logloss_improve_at_least_2_of_3": fold_improve >= 2,
        "pooled_brier_delta_le_0_002": delta_brier <= 0.002,
        "pooled_auc_delta_ge_minus_0_01": delta_auc >= -0.01,
    }
    passed = all(gates.values())

    enrichment = {
        "challenger_top10": enrich(y, pl, 0.10),
        "challenger_top15": enrich(y, pl, 0.15),
        "baseline_top10": enrich(y, pb, 0.10),
        "baseline_top15": enrich(y, pb, 0.15),
        "overall_draw_rate": float(np.mean(y)),
    }

    result = {
        "study_id": "r44l4_wsl_attack_balance_external_domain",
        "status": "EXTERNAL_DOMAIN_SIGNAL_PASS" if passed else "EXTERNAL_DOMAIN_SIGNAL_FAIL",
        "formal_weight": 0,
        "formal_pit_eligible": False,
        "source_commit": SOURCE_COMMIT,
        "prereg_sha256": PREREG_SHA256,
        "prelabel_binding": {
            "status": prelabel["status"], "total_identity_count": prelabel["total_identity_count"],
            "label_fields_accessed": prelabel["label_fields_accessed"], "model_fits": prelabel["model_fits"],
        },
        "labels_opened": True,
        "sample_consumed": True,
        "sample_audit": sample_audit,
        "sample_gates": sample_gates,
        "feature_counts": {"baseline": len(BASE_FEATURES), "challenger_total": len(ALL_FEATURES), "player_increment": len(PLAYER_FEATURES)},
        "folds": fold_rows,
        "pooled": {
            "baseline": pooled_b, "challenger": pooled_l,
            "delta_log_loss": delta_ll, "delta_brier": delta_brier,
            "delta_roc_auc": delta_auc, "delta_pr_auc": delta_pr,
        },
        "bootstrap_delta_logloss": boot_res,
        "fold_improve_count": fold_improve,
        "gates": gates,
        "enrichment": enrichment,
        "permissions": {
            "can_claim_formal_confirmation": False,
            "can_promote": False,
            "reason": "historical external-domain scientific check only; target lineup available_at is not provable pre-match PIT",
        },
    }

    (OUT / "model_result_r44l4.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "fold_metrics_r44l4.json").write_text(json.dumps(fold_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "oos_predictions_r44l4.json").write_text(json.dumps(pred_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "match_source_ledger_r44l4.json").write_text(json.dumps(match_ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_hashes = [{"match_id": mid, "lineup_sha256": src["lineup_sha256"], "event_sha256": src["event_sha256"]} for mid, src in sorted(source_rows.items())]
    (OUT / "per_match_source_hashes_r44l4.json").write_text(json.dumps(source_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"], "target_binary_rows": total_target, "target_draws": total_draws,
        "delta_log_loss": delta_ll, "bootstrap90": [boot_res["p05"], boot_res["p95"]],
        "fold_deltas": [r["delta_log_loss"] for r in fold_rows],
        "delta_auc": delta_auc, "delta_pr_auc": delta_pr, "gates": gates,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
