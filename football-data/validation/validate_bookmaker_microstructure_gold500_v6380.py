#!/usr/bin/env python3
"""V6.38.0 Gold500 cross-bookmaker microstructure 1X2 challenge.

This challenger adds a genuinely different information source from V6.37:
the disagreement structure across individual closing bookmakers. It does not
reuse A_FAST100 labels for selection and never reads B_CONFIRM300 labels.

For every historical row it discovers complete individual C-suffixed 1X2
bookmaker triplets (e.g. B365CH/CD/CA, PSCH/CD/CA) dynamically, de-vigs each
book separately, and derives consensus/dispersion features. Aggregate Avg/Max
fields are excluded from the individual-book panel.

Candidates:
- direct probability mean across de-vigged books;
- direct probability median;
- direct geometric pool;
- market-offset multinomial ridge using only bookmaker-panel microstructure.

Selection uses only rolling historical validation:
2022/23 -> 2023/24 and 2022/23+2023/24 -> 2024/25.
Gold500 A_FAST100 is scored once after selection. B300/C100 stay closed.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_market_residual_fusion_v620 as marketmod  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402

OUT = ROOT / "manifests" / "v6_bookmaker_microstructure_gold500_v6380_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
TEST_SEASON = "2025/26"
PART = "A_FAST100"
EPS = 1e-12
RIDGES = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3)
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
REQUIRED_UPLIFT_PP = 3.0
EXCLUDE_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_fast100_labels_only(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("Gold label file ended inside A_FAST100")
            row = json.loads(line)
            if str(row.get("partition")) != PART:
                raise RuntimeError(f"non-A label in first 100 rows: {row.get('partition')}")
            out[int(row["gold_index"])] = row
    if len(out) != 100 or set(out) != set(range(100)):
        raise RuntimeError("A_FAST100 label contract changed")
    return out


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def devig(odds: tuple[float, float, float]) -> tuple[list[float], float]:
    inv = [1.0 / x for x in odds]
    overround = sum(inv)
    return [x / overround for x in inv], overround


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.maximum(v, EPS)
    return v / v.sum()


def individual_books(row: dict[str, str]) -> tuple[list[list[float]], list[float], list[str]]:
    prefixes = []
    for key in row:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_PREFIXES:
            continue
        if prefix + "CD" not in row or prefix + "CA" not in row:
            continue
        prefixes.append(prefix)
    probs: list[list[float]] = []
    overs: list[float] = []
    used: list[str] = []
    for prefix in sorted(set(prefixes)):
        h, d, a = (fnum(row, prefix + "CH"), fnum(row, prefix + "CD"), fnum(row, prefix + "CA"))
        if h is None or d is None or a is None:
            continue
        p, o = devig((h, d, a))
        probs.append(p)
        overs.append(o)
        used.append(prefix)
    return probs, overs, used


def panel_payload(base_market: list[float], probs: list[list[float]], overs: list[float]) -> dict[str, Any] | None:
    if len(probs) < 2:
        return None
    arr = np.asarray(probs, dtype=float)
    mean = normalize(arr.mean(axis=0))
    med = normalize(np.median(arr, axis=0))
    geo = normalize(np.exp(np.log(np.clip(arr, EPS, 1.0)).mean(axis=0)))
    std = arr.std(axis=0)
    rng = arr.max(axis=0) - arr.min(axis=0)
    top = np.argmax(arr, axis=1)
    top_frac = np.asarray([(top == k).mean() for k in range(3)], dtype=float)
    ent = np.asarray([entropy(p) for p in arr], dtype=float)
    margins = np.sort(arr, axis=1)[:, -1] - np.sort(arr, axis=1)[:, -2]
    base = np.asarray(base_market, dtype=float)
    feature = np.concatenate([
        mean, med, geo, std, rng, top_frac,
        np.asarray([
            math.log1p(len(probs)),
            float(np.mean(overs)),
            float(np.std(overs)),
            float(ent.mean()),
            float(ent.std()),
            float(margins.mean()),
            float(margins.std()),
            float(1.0 - top_frac.max()),
        ]),
        mean - base,
        med - base,
        geo - base,
    ])
    return {
        "mean": [float(x) for x in mean],
        "median": [float(x) for x in med],
        "geo": [float(x) for x in geo],
        "micro_features": [float(x) for x in feature],
        "book_count": len(probs),
    }


def micro_lookup(cid: str) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    stats = Counter()
    prefix_counts = Counter()
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                stats["rows"] += 1
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season not in TRAIN_SEASONS and season != TEST_SEASON:
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                    market, _family = marketmod._closing_market(raw)
                except Exception:
                    stats["parse_rejected"] += 1
                    continue
                if market is None:
                    stats["missing_base_market"] += 1
                    continue
                probs, overs, prefixes = individual_books(raw)
                for p in prefixes:
                    prefix_counts[p] += 1
                payload = panel_payload([market[k] for k in ("home", "draw", "away")], probs, overs)
                if payload is None:
                    stats["insufficient_individual_books"] += 1
                    continue
                home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if not home or not away:
                    stats["identity_rejected"] += 1
                    continue
                key = (season, date, home, away)
                if key in lookup:
                    stats["duplicate_key"] += 1
                    continue
                payload["prefixes"] = prefixes
                lookup[key] = payload
                stats["attached"] += 1
    return lookup, {"stats": dict(stats), "prefix_counts": dict(prefix_counts)}


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter()
    actual = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]
        y = int(row["y"])
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
        "top1": hits / n if n else None,
        "brier": brier / n if n else None,
        "logloss": logloss / n if n else None,
        "rps": rps / n if n else None,
        "predicted_counts": dict(predicted),
        "actual_counts": dict(actual),
    }


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, _audit, _names = v632._build_rows()
    lookups = {}
    audit = {}
    for cid in sorted({str(r["competition_id"]) for r in base_rows}):
        lookups[cid], audit[cid] = micro_lookup(cid)
    rows = []
    misses = Counter()
    for r in base_rows:
        season = str(r["season"])
        if season not in TRAIN_SEASONS:
            continue
        cid = str(r["competition_id"])
        key = (
            season,
            str(r["date"]),
            v632._token(cid, str(r["home_team"])),
            v632._token(cid, str(r["away_team"])),
        )
        micro = lookups[cid].get(key)
        if micro is None:
            misses["micro_join_miss"] += 1
            continue
        rows.append({
            "competition_id": cid,
            "season": season,
            "date": str(r["date"]),
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "market": [float(x) for x in r["market"]],
            "mean": micro["mean"],
            "median": micro["median"],
            "geo": micro["geo"],
            "micro_features": micro["micro_features"],
            "book_count": int(micro["book_count"]),
            "y": int(r["y"]),
            "actual_score": [int(x) for x in r["actual_score"]],
        })
    by_season = Counter(r["season"] for r in rows)
    if any(by_season.get(s, 0) < 1000 for s in TRAIN_SEASONS):
        raise RuntimeError(f"microstructure historical coverage too small: {dict(by_season)}")
    return rows, {
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(Counter(r["competition_id"] for r in rows)),
        "misses": dict(misses),
        "market_panel_audit": audit,
    }


class Scaler:
    def __init__(self):
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None

    def fit(self, rows: list[dict[str, Any]]) -> "Scaler":
        x = np.asarray([r["micro_features"] for r in rows], dtype=float)
        self.mean = x.mean(axis=0)
        self.scale = x.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.mean is None or self.scale is None:
            raise RuntimeError("scaler not fitted")
        x = np.asarray([r["micro_features"] for r in rows], dtype=float)
        z = (x - self.mean) / self.scale
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
        diff = probs.copy()
        diff[np.arange(n), y] -= 1.0
        grad = (diff.T @ z) / n
        grad[:, 1:] += 2.0 * float(ridge) * w[:, 1:]
        return float(loss + penalty), grad.ravel()

    res = minimize(fun, np.zeros(3 * d), jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-11})
    if not res.success:
        raise RuntimeError(f"optimizer failed: {res.message}")
    return np.asarray(res.x).reshape(3, d)


def apply_offset(rows: list[dict[str, Any]], z: np.ndarray, w: np.ndarray, key: str = "offset") -> None:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    probs = softmax(np.log(np.clip(market, EPS, 1.0)) + z @ w.T)
    for r, p in zip(rows, probs):
        r[key] = [float(x) for x in p]


def proper(candidate: dict[str, Any], market: dict[str, Any]) -> bool:
    return (
        candidate["logloss"] <= market["logloss"] + PROPER_LOG_TOL
        and candidate["rps"] <= market["rps"] + PROPER_RPS_TOL
    )


def main() -> int:
    historical, build_audit = build_rows()
    leaderboard = []

    for direct in ("mean", "median", "geo"):
        folds = []
        for _train_seasons, val_season in FOLDS:
            va = [dict(r) for r in historical if r["season"] == val_season]
            cm = metrics(va, direct)
            mm = metrics(va, "market")
            folds.append({"candidate": cm, "market": mm, "proper_guard": proper(cm, mm)})
        leaderboard.append({
            "kind": "direct",
            "name": direct,
            "ridge": None,
            "mean_candidate_top1": sum(x["candidate"]["top1"] for x in folds) / len(folds),
            "mean_market_top1": sum(x["market"]["top1"] for x in folds) / len(folds),
            "mean_candidate_logloss": sum(x["candidate"]["logloss"] for x in folds) / len(folds),
            "mean_candidate_rps": sum(x["candidate"]["rps"] for x in folds) / len(folds),
            "proper_guard": all(x["proper_guard"] for x in folds),
            "folds": folds,
        })

    for ridge in RIDGES:
        folds = []
        for train_seasons, val_season in FOLDS:
            tr = [r for r in historical if r["season"] in train_seasons]
            va = [dict(r) for r in historical if r["season"] == val_season]
            scaler = Scaler().fit(tr)
            zt = scaler.apply(tr)
            zv = scaler.apply(va)
            w = fit_offset(tr, zt, ridge)
            apply_offset(va, zv, w)
            cm = metrics(va, "offset")
            mm = metrics(va, "market")
            folds.append({"candidate": cm, "market": mm, "proper_guard": proper(cm, mm)})
        leaderboard.append({
            "kind": "offset",
            "name": "micro_offset",
            "ridge": ridge,
            "mean_candidate_top1": sum(x["candidate"]["top1"] for x in folds) / len(folds),
            "mean_market_top1": sum(x["market"]["top1"] for x in folds) / len(folds),
            "mean_candidate_logloss": sum(x["candidate"]["logloss"] for x in folds) / len(folds),
            "mean_candidate_rps": sum(x["candidate"]["rps"] for x in folds) / len(folds),
            "proper_guard": all(x["proper_guard"] for x in folds),
            "folds": folds,
        })

    for x in leaderboard:
        x["mean_uplift_pp"] = (x["mean_candidate_top1"] - x["mean_market_top1"]) * 100.0
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(
        eligible,
        key=lambda x: (-x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"], 0 if x["kind"] == "direct" else 1),
    )
    leaderboard.sort(key=lambda x: (-x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"]))

    test_lookups = {}
    for cid in sorted({str(r["competition_id"]) for r in historical}):
        test_lookups[cid], _ = micro_lookup(cid)

    features = load_jsonl(GOLD_FEATURES)
    fast_features = [r for r in features if str(r["partition"]) == PART]
    labels = load_fast100_labels_only(GOLD_LABELS)
    fast = []
    for row in fast_features:
        idx = int(row["gold_index"])
        cid = str(row["competition_id"])
        key = (
            TEST_SEASON,
            str(row["date"]),
            v632._token(cid, str(row["home_team"])),
            v632._token(cid, str(row["away_team"])),
        )
        micro = test_lookups[cid].get(key)
        if micro is None:
            raise RuntimeError(f"Gold A microstructure missing for {key}")
        lab = labels[idx]
        fast.append({
            "gold_index": idx,
            "competition_id": cid,
            "date": str(row["date"]),
            "home_team": str(row["home_team"]),
            "away_team": str(row["away_team"]),
            "market": [float(x) for x in row["market"]],
            "formal": [float(x) for x in row["formal"]],
            "mean": micro["mean"],
            "median": micro["median"],
            "geo": micro["geo"],
            "micro_features": micro["micro_features"],
            "book_count": int(micro["book_count"]),
            "y": int(lab["label"]),
            "actual_score": [int(x) for x in lab["actual_score"]],
        })

    if selected["kind"] == "direct":
        for r in fast:
            r["candidate"] = list(r[str(selected["name"])])
    else:
        final_train = [r for r in historical if r["season"] in TRAIN_SEASONS]
        scaler = Scaler().fit(final_train)
        zt = scaler.apply(final_train)
        zf = scaler.apply(fast)
        w = fit_offset(final_train, zt, float(selected["ridge"]))
        apply_offset(fast, zf, w, "candidate")

    market_m = metrics(fast, "market")
    formal_m = metrics(fast, "formal")
    candidate_m = metrics(fast, "candidate")
    top1_pass = candidate_m["hits"] >= market_m["hits"] + 3 and candidate_m["hits"] >= 63
    proper_pass = proper(candidate_m, market_m)
    gate = bool(top1_pass and proper_pass)

    changed = []
    for r in fast:
        mp = max(range(3), key=lambda i: r["market"][i])
        cp = max(range(3), key=lambda i: r["candidate"][i])
        if mp != cp:
            changed.append({
                "gold_index": r["gold_index"],
                "competition_id": r["competition_id"],
                "date": r["date"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "book_count": r["book_count"],
                "actual_result": r["y"],
                "market_pick": mp,
                "candidate_pick": cp,
                "market_correct": mp == r["y"],
                "candidate_correct": cp == r["y"],
            })

    payload = {
        "schema_version": "V6.38.0-bookmaker-microstructure-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_GOLD500_FAST100_CROSS_BOOKMAKER_MICROSTRUCTURE",
        "governance_contract": {
            "A_FAST100_only": True,
            "B_CONFIRM300_labels_read": False,
            "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "confidence_filtering": False,
            "league_dropping": False,
            "seed_replacement": False,
            "A100_parameter_tuning": False,
            "retrospective_closing_market_research_only": True,
            "CURRENT_unchanged": True,
        },
        "build_audit": build_audit,
        "architecture": {
            "individual_book_detection": "dynamic complete *CH/*CD/*CA triplets excluding aggregate Avg/Max families",
            "minimum_individual_books": 2,
            "direct_candidates": ["mean", "median", "geo"],
            "offset_candidate": "softmax(log(p_market)+W*z_micro)",
            "ridge_grid": list(RIDGES),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in FOLDS],
        },
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
        "fast100": {
            "market": market_m,
            "formal": formal_m,
            "candidate": candidate_m,
            "candidate_vs_market_top1_pp": (candidate_m["top1"] - market_m["top1"]) * 100.0,
            "required_market_top1_uplift_pp": REQUIRED_UPLIFT_PP,
            "top1_gate_pass": top1_pass,
            "proper_gate_pass": proper_pass,
            "gate_passed": gate,
            "book_count_summary": {
                "min": min(r["book_count"] for r in fast),
                "median": float(np.median([r["book_count"] for r in fast])),
                "max": max(r["book_count"] for r in fast),
            },
            "changed_pick_count": len(changed),
            "changed_pick_audit": changed,
        },
        "decision": "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "decision": payload["decision"],
        "historical_rows": build_audit["joined_by_season"],
        "selected": {
            "kind": selected["kind"],
            "name": selected["name"],
            "ridge": selected["ridge"],
            "mean_candidate_top1": selected["mean_candidate_top1"],
            "mean_market_top1": selected["mean_market_top1"],
            "mean_uplift_pp": selected["mean_uplift_pp"],
            "proper_guard": selected["proper_guard"],
        },
        "fast100": {
            "market": market_m,
            "formal": formal_m,
            "candidate": candidate_m,
            "book_count_summary": payload["fast100"]["book_count_summary"],
            "changed_pick_count": len(changed),
            "gate_passed": gate,
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
