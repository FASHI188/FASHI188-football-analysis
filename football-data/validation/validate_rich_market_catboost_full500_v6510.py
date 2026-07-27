#!/usr/bin/env python3
"""V6.51.0 rich-market CatBoost 1X2 challenger for V6.49.3 Full500.

Design is selected strictly on historical rolling folds, never on Full500 A100 labels.
It combines the established 53 base features with genuinely richer market information:
- early 1X2 and early->closing probability path;
- early/closing O/U 2.5;
- early/closing Asian-handicap line and quote-side probabilities;
- individual closing bookmaker consensus and disagreement structure.

Historical folds:
  2022/23 -> 2023/24
  2022/23+2023/24 -> 2024/25
Candidate grid is fully declared below. Draw sample weight, tree depth, and the geometric
blend weight back toward the closing market are selected only from those folds.

Historical gate before A100 scoring:
- mean Top-1 uplift >= +0.5pp;
- neither fold negative;
- log-loss and RPS no worse than market by >0.01 on either fold.
Only if that gate passes are the already-fixed A_FAST100 labels read.
B300/C100 are never read here. Research only; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import v6_market_residual_fusion_v620 as marketmod  # noqa: E402

OUT = ROOT / "manifests" / "v6_rich_market_catboost_full500_v6510_status.json"
FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
PART = "A_FAST100"
EPS = 1e-12
DEPTHS = (4, 5, 6)
DRAW_WEIGHTS = (1.0, 1.15, 1.30, 1.45)
ALPHAS = (0.25, 0.40, 0.55, 0.70)
ITERATIONS = 450
LEARNING_RATE = 0.035
L2 = 8.0
RANDOM_SEED = 6510
HIST_REQUIRED_MEAN_UPLIFT_PP = 0.5
PROPER_TOL = 0.01
EXCLUDE_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def fnum(row: dict[str, str], key: str, odds: bool = False) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if odds and x <= 1.0:
        return None
    return x


def devig(values: list[float]) -> np.ndarray:
    inv = 1.0 / np.asarray(values, dtype=float)
    return inv / inv.sum()


def triplet(row: dict[str, str], keys: tuple[str, str, str]) -> np.ndarray | None:
    vals = [fnum(row, k, True) for k in keys]
    if any(v is None for v in vals):
        return None
    return devig([float(v) for v in vals])


def pair(row: dict[str, str], keys: tuple[str, str]) -> np.ndarray | None:
    vals = [fnum(row, k, True) for k in keys]
    if any(v is None for v in vals):
        return None
    return devig([float(v) for v in vals])


def first_triplet(row: dict[str, str], families: tuple[tuple[str, str, str], ...]) -> np.ndarray | None:
    for fam in families:
        p = triplet(row, fam)
        if p is not None:
            return p
    return None


def first_pair(row: dict[str, str], families: tuple[tuple[str, str], ...]) -> np.ndarray | None:
    for fam in families:
        p = pair(row, fam)
        if p is not None:
            return p
    return None


def individual_books(row: dict[str, str]) -> tuple[list[np.ndarray], list[float]]:
    probs = []; overs = []
    for key in row:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_PREFIXES:
            continue
        dk, ak = prefix + "CD", prefix + "CA"
        if dk not in row or ak not in row:
            continue
        h, d, a = fnum(row, key, True), fnum(row, dk, True), fnum(row, ak, True)
        if h is None or d is None or a is None:
            continue
        vals = np.asarray([float(h), float(d), float(a)])
        inv = 1.0 / vals
        overs.append(float(inv.sum()))
        probs.append(inv / inv.sum())
    return probs, overs


def market_extra(raw: dict[str, str], close: list[float]) -> list[float] | None:
    early = first_triplet(raw, (
        ("AvgH", "AvgD", "AvgA"),
        ("B365H", "B365D", "B365A"),
        ("PSH", "PSD", "PSA"),
    ))
    ou_e = first_pair(raw, (("Avg>2.5", "Avg<2.5"), ("B365>2.5", "B365<2.5"), ("P>2.5", "P<2.5")))
    ou_c = first_pair(raw, (("AvgC>2.5", "AvgC<2.5"), ("B365C>2.5", "B365C<2.5"), ("PC>2.5", "PC<2.5")))
    ah_e_line = fnum(raw, "AHh")
    ah_c_line = fnum(raw, "AHCh")
    ah_e = first_pair(raw, (("AvgAHH", "AvgAHA"), ("B365AHH", "B365AHA"), ("PAHH", "PAHA")))
    ah_c = first_pair(raw, (("AvgCAHH", "AvgCAHA"), ("B365CAHH", "B365CAHA"), ("PCAHH", "PCAHA")))
    books, overs = individual_books(raw)
    if early is None or ou_e is None or ou_c is None or ah_e_line is None or ah_c_line is None or ah_e is None or ah_c is None or len(books) < 2:
        return None

    close_arr = np.asarray(close, dtype=float)
    arr = np.asarray(books, dtype=float)
    mean = arr.mean(axis=0)
    med = np.median(arr, axis=0)
    geo = np.exp(np.log(np.clip(arr, EPS, 1.0)).mean(axis=0)); geo /= geo.sum()
    std = arr.std(axis=0)
    rng = arr.max(axis=0) - arr.min(axis=0)
    tops = np.argmax(arr, axis=1)
    topfrac = np.asarray([(tops == k).mean() for k in range(3)], dtype=float)
    delta = close_arr - early
    logmove = np.log(np.clip(close_arr, EPS, 1.0)) - np.log(np.clip(early, EPS, 1.0))
    return [float(x) for x in np.concatenate([
        early, delta, logmove,
        ou_e, ou_c, ou_c-ou_e,
        np.asarray([float(ah_e_line), float(ah_c_line), float(ah_c_line-ah_e_line)]),
        ah_e, ah_c, ah_c-ah_e,
        mean, med, geo, std, rng, topfrac,
        np.asarray([float(len(books)), float(np.mean(overs)), float(np.std(overs))]),
    ])]


def raw_lookups(cids: list[str]) -> dict[str, dict[tuple[str, str, str, str], dict[str, str]]]:
    out = {}
    for cid in cids:
        lookup = {}
        for path in sorted((ROOT / "processed" / cid).glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in TRAIN_SEASONS and season != "2025/26":
                        continue
                    try:
                        date = marketmod._parse_date(str(raw.get("Date") or ""))
                    except Exception:
                        continue
                    h = v632._token(cid, str(raw.get("HomeTeam") or "")); a = v632._token(cid, str(raw.get("AwayTeam") or ""))
                    if not h or not a:
                        continue
                    lookup.setdefault((season, date, h, a), raw)
        out[cid] = lookup
    return out


def build_historical() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base, audit, _names = v632._build_rows()
    cids = sorted({str(r["competition_id"]) for r in base})
    lookups = raw_lookups(cids)
    rows = []; misses = Counter()
    for r in base:
        season = str(r["season"])
        if season not in TRAIN_SEASONS:
            continue
        cid = str(r["competition_id"])
        key = (season, str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
        raw = lookups[cid].get(key)
        if raw is None:
            misses["raw_join"] += 1; continue
        extra = market_extra(raw, [float(x) for x in r["market"]])
        if extra is None:
            misses["market_packet"] += 1; continue
        rows.append({
            "competition_id": cid, "season": season, "date": str(r["date"]),
            "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "x": [float(x) for x in r["x"]] + extra,
            "market": [float(x) for x in r["market"]], "y": int(r["y"]),
        })
    return rows, {"base_audit": audit, "misses": dict(misses), "by_season": dict(Counter(r["season"] for r in rows))}


def load_a100_features() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frows = [r for r in load_jsonl(FEATURES) if str(r.get("partition")) == PART]
    if len(frows) != 100:
        raise RuntimeError(f"expected 100 A features, got {len(frows)}")
    cids = sorted({str(r["competition_id"]) for r in frows}); lookups = raw_lookups(cids)
    out = []; misses = Counter()
    for r in frows:
        cid = str(r["competition_id"])
        key = (str(r["season"]), str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
        raw = lookups[cid].get(key)
        if raw is None:
            misses["raw_join"] += 1; continue
        extra = market_extra(raw, [float(x) for x in r["market"]])
        if extra is None:
            misses["market_packet"] += 1; continue
        out.append({
            "full_index": int(r["full_index"]), "competition_id": cid,
            "x": [float(x) for x in r["base_features"]] + extra,
            "market": [float(x) for x in r["market"]],
        })
    if len(out) != 100:
        raise RuntimeError(f"A100 rich feature coverage incomplete: {dict(misses)} n={len(out)}")
    out.sort(key=lambda r: r["full_index"])
    return out, {"misses": dict(misses)}


def metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = len(y); picks = probs.argmax(axis=1); hits = int(np.sum(picks == y))
    brier = float(np.mean(np.sum((probs - np.eye(3)[y])**2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:,0] - (y == 0); c2 = probs[:,0] + probs[:,1] - (y <= 1)
    rps = float(np.mean((c1*c1+c2*c2)/2.0))
    return {"count": n, "hits": hits, "top1": hits/n, "brier": brier, "logloss": logloss, "rps": rps,
            "predicted_counts": dict(Counter(str(int(x)) for x in picks)), "actual_counts": dict(Counter(str(int(x)) for x in y))}


def blend(market: np.ndarray, model: np.ndarray, alpha: float) -> np.ndarray:
    z = (1.0-alpha)*np.log(np.clip(market, EPS, 1.0)) + alpha*np.log(np.clip(model, EPS, 1.0))
    z -= z.max(axis=1, keepdims=True); p = np.exp(z); return p/p.sum(axis=1, keepdims=True)


def fit_predict(train: list[dict[str, Any]], valid: list[dict[str, Any]], depth: int, draw_weight: float) -> np.ndarray:
    xtr = np.asarray([r["x"] for r in train], dtype=float); ytr = np.asarray([r["y"] for r in train], dtype=int)
    xva = np.asarray([r["x"] for r in valid], dtype=float)
    sw = np.where(ytr == 1, draw_weight, 1.0)
    model = CatBoostClassifier(
        loss_function="MultiClass", iterations=ITERATIONS, depth=depth,
        learning_rate=LEARNING_RATE, l2_leaf_reg=L2, random_seed=RANDOM_SEED,
        verbose=False, allow_writing_files=False, thread_count=-1,
    )
    model.fit(xtr, ytr, sample_weight=sw)
    return np.asarray(model.predict_proba(xva), dtype=float)


def historical_select(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results = defaultdict(list)
    fold_market = []
    for train_seasons, valid_season in FOLDS:
        train = [r for r in rows if r["season"] in train_seasons]
        valid = [r for r in rows if r["season"] == valid_season]
        y = np.asarray([r["y"] for r in valid], dtype=int)
        m = np.asarray([r["market"] for r in valid], dtype=float)
        mm = metrics(y, m); fold_market.append({"valid_season": valid_season, **mm})
        for depth in DEPTHS:
            for dw in DRAW_WEIGHTS:
                pred = fit_predict(train, valid, depth, dw)
                for alpha in ALPHAS:
                    p = blend(m, pred, alpha); met = metrics(y, p)
                    results[(depth,dw,alpha)].append({
                        "valid_season": valid_season, "market": mm, "candidate": met,
                        "uplift_pp": 100.0*(met["top1"]-mm["top1"]),
                        "logloss_delta": met["logloss"]-mm["logloss"],
                        "rps_delta": met["rps"]-mm["rps"],
                    })
    board = []
    for spec, folds in results.items():
        uplifts = [f["uplift_pp"] for f in folds]
        proper = all(f["logloss_delta"] <= PROPER_TOL and f["rps_delta"] <= PROPER_TOL for f in folds)
        board.append({
            "depth": spec[0], "draw_weight": spec[1], "alpha": spec[2], "folds": folds,
            "mean_uplift_pp": float(np.mean(uplifts)), "min_uplift_pp": float(np.min(uplifts)),
            "proper_guard": proper,
        })
    board.sort(key=lambda z: (z["min_uplift_pp"], z["mean_uplift_pp"], z["proper_guard"]), reverse=True)
    selected = board[0]
    selected["historical_gate"] = bool(selected["mean_uplift_pp"] >= HIST_REQUIRED_MEAN_UPLIFT_PP and selected["min_uplift_pp"] >= -1e-12 and selected["proper_guard"])
    return selected, board


def load_a100_labels() -> np.ndarray:
    labels = []
    with LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            line = h.readline()
            if not line: raise RuntimeError("A100 labels truncated")
            r = json.loads(line)
            if r.get("partition") != PART or int(r["full_index"]) != len(labels): raise RuntimeError("A100 label contract changed")
            labels.append(int(r["label"]))
    return np.asarray(labels, dtype=int)


def main() -> int:
    hist, hist_audit = build_historical()
    selected, board = historical_select(hist)
    payload: dict[str, Any] = {
        "schema_version": "V6.51.0-rich-market-catboost-full500-r1", "status": "PASS",
        "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {
            "candidate_grid_predeclared": True, "selection_uses_only_2023_24_and_2024_25_historical_folds": True,
            "A100_values_used_for_selection": False, "B_CONFIRM300_labels_read": False, "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": hist_audit,
        "grid": {"depths": DEPTHS, "draw_weights": DRAW_WEIGHTS, "alphas": ALPHAS, "iterations": ITERATIONS, "learning_rate": LEARNING_RATE, "l2": L2},
        "selected_historical": selected,
        "historical_leaderboard_top10": board[:10],
    }
    if not selected["historical_gate"]:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0

    arows, aaudit = load_a100_features(); y = load_a100_labels()
    market = np.asarray([r["market"] for r in arows], dtype=float)
    model_p = fit_predict(hist, arows, int(selected["depth"]), float(selected["draw_weight"]))
    final = blend(market, model_p, float(selected["alpha"]))
    mm = metrics(y, market); cm = metrics(y, final)
    a_gate = {
        "required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0,
        "candidate_hits": cm["hits"], "market_hits": mm["hits"],
        "uplift_vs_market_pp": 100.0*(cm["top1"]-mm["top1"]),
        "top1_gate": cm["hits"] >= 63,
        "uplift_gate": (cm["top1"]-mm["top1"]) >= 0.03-1e-12,
        "proper_score_guard": cm["logloss"] <= mm["logloss"]+PROPER_TOL and cm["rps"] <= mm["rps"]+PROPER_TOL,
    }
    a_gate["A_FAST100_passed"] = bool(a_gate["top1_gate"] and a_gate["uplift_gate"] and a_gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "feature_audit": aaudit, "market": mm, "candidate": cm, "gate": a_gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if a_gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
