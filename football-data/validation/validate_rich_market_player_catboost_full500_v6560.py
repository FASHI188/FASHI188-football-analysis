#!/usr/bin/env python3
"""V6.56.0 rich-market + strict-PIT player-core CatBoost challenger.

This closes a real information gap in V6.51: its historical rich-market model used the
53 base features plus market path/microstructure, but not the 18 strict-PIT player-core
features available in Full500.

Player feature contract (unchanged from V6.33):
- previous-season starter frequency as prior;
- current-season starts strictly before target date;
- transfers strictly before target date;
- latest player valuation with valuation date <= target date;
- target-match lineup never used;
- same-day target games do not update each other.

Historical identity is joined result-blind with the schedule-fingerprint crosswalk used
by V6.36.1. Model selection uses only rolling historical folds:
  2022/23 -> 2023/24
  2022/23+2023/24 -> 2024/25
A_FAST100 labels are opened only if mean uplift >= +0.5pp, neither fold is negative,
and proper-score guard passes. B300/C100 remain unread.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as g0  # noqa: E402
import build_gold500_feature_library_v6361 as g1  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402
import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT = ROOT / "manifests" / "v6_rich_market_player_catboost_full500_v6560_status.json"
TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
DEPTHS = (4, 5)
L2S = (8.0, 20.0)
DRAW_WEIGHTS = (1.0, 1.25, 1.5)
ALPHAS = (0.25, 0.50, 0.75)
ITERATIONS = 450
LEARNING_RATE = 0.035
SEED = 6560
EPS = 1e-12
PROPER_TOL = 0.01
HIST_REQUIRED_MEAN_UPLIFT_PP = 0.5
PLAYER_FEATURE_COUNT = 18


def build_player_rows(base_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str, str], list[float]], dict[str, Any]]:
    data_by_comp: dict[str, dict[str, Any]] = {}
    indexes_by_comp: dict[str, dict[str, Any]] = {}
    names_by_comp: dict[str, dict[int, tuple[str, str]]] = {}
    for cid in v633.v6280.COMPS:
        data = v633.load_domain_data(cid, v633.CACHE)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = v633.build_season_indexes(data)
        names_by_comp[cid] = g0.game_name_lookup(cid, v633.CACHE)

    wanted = v633._all_player_ids(data_by_comp)
    valuations, valuation_audit = v633._load_valuations(wanted)

    tm_rows: list[dict[str, Any]] = []
    by_season = Counter()
    misses = Counter()
    for cid in v633.v6280.COMPS:
        data = data_by_comp[cid]
        indexes = indexes_by_comp[cid]
        for season in TRAIN_SEASONS:
            for target in indexes["by_season"].get(season, []):
                hp = v633._team_player_features(int(target["home_id"]), season, target["date"], indexes, data, valuations)
                ap = v633._team_player_features(int(target["away_id"]), season, target["date"], indexes, data, valuations)
                if hp is None or ap is None:
                    misses[f"{season}:player_context"] += 1
                    continue
                names = names_by_comp[cid].get(int(target["game_id"]))
                if not names:
                    misses[f"{season}:game_name"] += 1
                    continue
                pf = [float(x) for x in v633._pair_features(hp, ap)]
                if len(pf) != PLAYER_FEATURE_COUNT:
                    raise RuntimeError(f"unexpected player feature count {len(pf)}")
                tm_rows.append({
                    "competition_id": cid,
                    "season": season,
                    "date": target["date"].date().isoformat(),
                    "home_name": names[0],
                    "away_name": names[1],
                    "player_features": pf,
                })
                by_season[season] += 1

    crosswalk, crosswalk_audit = g1.build_crosswalk(base_rows, tm_rows)
    pmap: dict[tuple[str, str, str, str], list[float]] = {}
    unmapped = duplicates = 0
    for r in tm_rows:
        cid = str(r["competition_id"])
        mh = crosswalk.get(cid, {}).get(str(r["home_name"]))
        ma = crosswalk.get(cid, {}).get(str(r["away_name"]))
        if not mh or not ma:
            unmapped += 1
            continue
        key = (cid, str(r["date"]), v651.v632._token(cid, mh), v651.v632._token(cid, ma))
        if key in pmap:
            duplicates += 1
            continue
        pmap[key] = list(r["player_features"])
    return pmap, {
        "tm_player_rows_by_season": dict(by_season),
        "player_context_misses": dict(misses),
        "unmapped_tm_rows": unmapped,
        "duplicate_player_keys": duplicates,
        "valuation_audit": valuation_audit,
        "crosswalk": crosswalk_audit,
        "target_lineup_used": False,
        "future_valuation_used": False,
        "future_transfer_used": False,
    }


def build_historical() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rich, rich_audit = v651.build_historical()
    pmap, player_audit = build_player_rows(rich)
    rows = []
    misses = Counter()
    for r in rich:
        cid = str(r["competition_id"])
        key = (
            cid,
            str(r["date"]),
            v651.v632._token(cid, str(r["home_team"])),
            v651.v632._token(cid, str(r["away_team"])),
        )
        pf = pmap.get(key)
        if pf is None:
            misses[f"{r['season']}:player_join"] += 1
            continue
        z = dict(r)
        z["x"] = [float(x) for x in r["x"]] + [float(x) for x in pf]
        rows.append(z)
    by_season = Counter(r["season"] for r in rows)
    if any(by_season.get(s, 0) < 1000 for s in TRAIN_SEASONS):
        raise RuntimeError(f"combined historical coverage too small: {dict(by_season)}")
    return rows, {
        "rich_market": rich_audit,
        "player": player_audit,
        "joined_by_season": dict(by_season),
        "join_misses": dict(misses),
        "feature_count": len(rows[0]["x"]) if rows else 0,
    }


def soft_blend(market: np.ndarray, model: np.ndarray, alpha: float) -> np.ndarray:
    z = (1.0-alpha)*np.log(np.clip(market, EPS, 1.0)) + alpha*np.log(np.clip(model, EPS, 1.0))
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


def metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = len(y)
    pick = probs.argmax(axis=1)
    hits = int(np.sum(pick == y))
    brier = float(np.mean(np.sum((probs - np.eye(3)[y])**2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:,0] - (y == 0)
    c2 = probs[:,0] + probs[:,1] - (y <= 1)
    rps = float(np.mean((c1*c1 + c2*c2)/2.0))
    return {
        "count": n, "hits": hits, "top1": hits/n,
        "brier": brier, "logloss": logloss, "rps": rps,
        "predicted_counts": dict(Counter(str(int(x)) for x in pick)),
        "actual_counts": dict(Counter(str(int(x)) for x in y)),
    }


def fit_predict(train: list[dict[str, Any]], valid: list[dict[str, Any]], depth: int, l2: float, draw_weight: float) -> np.ndarray:
    xtr = np.asarray([r["x"] for r in train], dtype=float)
    ytr = np.asarray([r["y"] for r in train], dtype=int)
    xva = np.asarray([r["x"] for r in valid], dtype=float)
    sw = np.where(ytr == 1, draw_weight, 1.0)
    model = CatBoostClassifier(
        loss_function="MultiClass", iterations=ITERATIONS, depth=depth,
        learning_rate=LEARNING_RATE, l2_leaf_reg=l2, random_seed=SEED,
        random_strength=0.5, bootstrap_type="Bayesian", bagging_temperature=0.5,
        verbose=False, allow_writing_files=False, thread_count=-1,
    )
    model.fit(xtr, ytr, sample_weight=sw)
    return np.asarray(model.predict_proba(xva), dtype=float)


def historical_select(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    board = []
    for depth in DEPTHS:
        for l2 in L2S:
            for dw in DRAW_WEIGHTS:
                fold_raw = []
                cached = {}
                for train_seasons, valid_season in FOLDS:
                    train = [r for r in rows if r["season"] in train_seasons]
                    valid = [r for r in rows if r["season"] == valid_season]
                    key = (depth, l2, dw, valid_season)
                    pred = fit_predict(train, valid, depth, l2, dw)
                    y = np.asarray([r["y"] for r in valid], dtype=int)
                    market = np.asarray([r["market"] for r in valid], dtype=float)
                    cached[key] = (y, market, pred)
                for alpha in ALPHAS:
                    folds = []
                    for _train_seasons, valid_season in FOLDS:
                        y, market, pred = cached[(depth, l2, dw, valid_season)]
                        cand = soft_blend(market, pred, alpha)
                        mm, cm = metrics(y, market), metrics(y, cand)
                        folds.append({
                            "valid_season": valid_season,
                            "market": mm, "candidate": cm,
                            "uplift_pp": 100.0*(cm["top1"]-mm["top1"]),
                            "logloss_delta": cm["logloss"]-mm["logloss"],
                            "rps_delta": cm["rps"]-mm["rps"],
                        })
                    uplifts = [f["uplift_pp"] for f in folds]
                    proper = all(f["logloss_delta"] <= PROPER_TOL and f["rps_delta"] <= PROPER_TOL for f in folds)
                    board.append({
                        "depth": depth, "l2": l2, "draw_weight": dw, "alpha": alpha,
                        "folds": folds,
                        "mean_uplift_pp": float(np.mean(uplifts)),
                        "min_uplift_pp": float(np.min(uplifts)),
                        "proper_guard": proper,
                    })
    board.sort(key=lambda z: (z["min_uplift_pp"], z["mean_uplift_pp"], z["proper_guard"]), reverse=True)
    selected = board[0]
    selected["historical_gate"] = bool(
        selected["mean_uplift_pp"] >= HIST_REQUIRED_MEAN_UPLIFT_PP
        and selected["min_uplift_pp"] >= -1e-12
        and selected["proper_guard"]
    )
    return selected, board


def load_a100() -> tuple[list[dict[str, Any]], np.ndarray]:
    feats = [json.loads(x) for x in v651.FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    feats = [r for r in feats if r.get("partition") == v651.PART]
    feats.sort(key=lambda r: int(r["full_index"]))
    if len(feats) != 100:
        raise RuntimeError(f"A100 features {len(feats)}")
    rich_rows, audit = v651.load_a100_features()
    by_idx = {int(r["full_index"]): r for r in rich_rows}
    out = []
    for f in feats:
        idx = int(f["full_index"])
        rr = by_idx[idx]
        pf = [float(x) for x in f["player_features"]]
        if len(pf) != PLAYER_FEATURE_COUNT:
            raise RuntimeError(f"A100 player feature count {len(pf)} at {idx}")
        z = dict(rr)
        z["x"] = [float(x) for x in rr["x"]] + pf
        out.append(z)
    labels = []
    with v651.LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            r = json.loads(h.readline())
            if r.get("partition") != v651.PART or int(r["full_index"]) != len(labels):
                raise RuntimeError("A100 label contract changed")
            labels.append(int(r["label"]))
    return out, np.asarray(labels, dtype=int)


def main() -> int:
    rows, audit = build_historical()
    selected, board = historical_select(rows)
    payload: dict[str, Any] = {
        "schema_version": "V6.56.0-rich-market-player-catboost-full500-r1",
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {
            "player_target_lineup_used": False,
            "player_future_valuation_used": False,
            "player_future_transfer_used": False,
            "selection_only_historical_folds": True,
            "A100_values_used_for_selection": False,
            "B_CONFIRM300_labels_read": False,
            "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": audit,
        "grid": {"depths": DEPTHS, "l2": L2S, "draw_weights": DRAW_WEIGHTS, "alphas": ALPHAS,
                 "iterations": ITERATIONS, "learning_rate": LEARNING_RATE},
        "selected_historical": selected,
        "historical_leaderboard_top10": board[:10],
    }
    if not selected["historical_gate"]:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    arows, y = load_a100()
    pred = fit_predict(rows, arows, int(selected["depth"]), float(selected["l2"]), float(selected["draw_weight"]))
    market = np.asarray([r["market"] for r in arows], dtype=float)
    cand = soft_blend(market, pred, float(selected["alpha"]))
    mm, cm = metrics(y, market), metrics(y, cand)
    uplift = 100.0*(cm["top1"]-mm["top1"])
    gate = {
        "required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0,
        "market_hits": mm["hits"], "candidate_hits": cm["hits"], "uplift_vs_market_pp": uplift,
        "top1_gate": cm["hits"] >= 63,
        "uplift_gate": uplift >= 3.0-1e-12,
        "proper_score_guard": cm["logloss"] <= mm["logloss"]+PROPER_TOL and cm["rps"] <= mm["rps"]+PROPER_TOL,
    }
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "market": mm, "candidate": cm, "gate": gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
