#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MARKET_LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
FOOTBALL_LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
OUT = HERE / "results" / "summary_r43r0_strong_shrink_football_residual.json"

CLASSES = ("home", "draw", "away")
SEED_N = 8
FOLDS = 3
RIDGE_PENALTY = 40.0
BETA_BOUNDS = (-0.5, 0.5)
MIN_MATCHED = 20
MIN_SCORED = 12
BREAKTHROUGH_PP = 1.0


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def norm_team(x: str) -> str:
    s = str(x or "").lower().strip().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", s)


def iso(x: str) -> datetime:
    dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def identity(payload: dict) -> tuple[str, str, str, str]:
    f = payload["fixture_identity"]
    return (
        str(f["competition_id"]),
        iso(f["kickoff_at"]).replace(microsecond=0).isoformat(),
        norm_team(f["home_team"]),
        norm_team(f["away_team"]),
    )


def probs(d: dict) -> dict[str, float]:
    v = {k: float(d[k]) for k in CLASSES}
    if any((not math.isfinite(x) or x <= 0.0) for x in v.values()):
        raise ValueError("invalid probability")
    s = sum(v.values())
    return {k: v[k] / s for k in CLASSES}


def residual_prob(pm: dict[str, float], pf: dict[str, float], beta: float) -> dict[str, float]:
    # Market is the offset. Football contributes only its log-probability residual.
    z = {}
    for k in CLASSES:
        r = math.log(max(pf[k], 1e-15)) - math.log(max(pm[k], 1e-15))
        z[k] = math.log(max(pm[k], 1e-15)) + float(beta) * r
    mx = max(z.values())
    e = {k: math.exp(z[k] - mx) for k in CLASSES}
    s = sum(e.values())
    return {k: e[k] / s for k in CLASSES}


def fit_beta(train: list[dict]) -> float:
    def objective(beta: float) -> float:
        loss = 0.0
        for r in train:
            p = residual_prob(r["market"], r["football"], beta)
            loss -= math.log(max(p[r["y"]], 1e-15))
        loss += 0.5 * RIDGE_PENALTY * beta * beta
        return float(loss)

    res = minimize_scalar(objective, bounds=BETA_BOUNDS, method="bounded", options={"xatol": 1e-10, "maxiter": 300})
    return float(res.x)


def top1(p: dict[str, float]) -> str:
    return max(CLASSES, key=lambda k: (p[k], -CLASSES.index(k)))


def draw_cal(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"n": 0}
    ps = np.array([float(r[key]["draw"]) for r in rows], dtype=float)
    ys = np.array([1.0 if r["y"] == "draw" else 0.0 for r in rows], dtype=float)
    ll = float(np.mean(-(ys * np.log(np.clip(ps, 1e-15, 1.0)) + (1.0 - ys) * np.log(np.clip(1.0 - ps, 1e-15, 1.0)))))
    br = float(np.mean((ps - ys) ** 2))
    order = np.argsort(ps)
    bins = np.array_split(order, min(5, len(order)))
    ece = 0.0
    out = []
    for idx in bins:
        if not len(idx):
            continue
        mp = float(ps[idx].mean()); ar = float(ys[idx].mean()); w = len(idx) / len(rows)
        ece += w * abs(mp - ar)
        out.append({"n": int(len(idx)), "mean_pred": mp, "actual_rate": ar})
    return {
        "n": len(rows), "mean_pred": float(ps.mean()), "actual_rate": float(ys.mean()),
        "logloss": ll, "brier": br, "ece5": float(ece), "bins": out,
    }


def metrics(rows: list[dict], key: str) -> dict:
    n = len(rows); hits = 0; ll = br = rps = 0.0
    picks = {k: 0 for k in CLASSES}; hit_by = {k: 0 for k in CLASSES}; actuals = {k: 0 for k in CLASSES}
    for r in rows:
        p = r[key]; y = r["y"]; t = top1(p)
        hits += int(t == y); picks[t] += 1; hit_by[t] += int(t == y); actuals[y] += 1
        ll -= math.log(max(float(p[y]), 1e-15))
        br += sum((float(p[k]) - (1.0 if y == k else 0.0)) ** 2 for k in CLASSES)
        ph = float(p["home"]); pd = float(p["draw"])
        rps += ((ph - (1.0 if y == "home" else 0.0)) ** 2 + ((ph + pd) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0
    return {
        "count": n, "hits": hits, "top1_accuracy": hits / n if n else None,
        "logloss": ll / n if n else None, "brier": br / n if n else None, "rps": rps / n if n else None,
        "top1_picks": picks, "top1_hits": hit_by, "actuals": actuals,
        "draw_calibration": draw_cal(rows, key),
    }


def delta(base: dict, cand: dict) -> dict:
    return {
        "hits": cand["hits"] - base["hits"],
        "accuracy_pp": 100.0 * (cand["top1_accuracy"] - base["top1_accuracy"]),
        "logloss": cand["logloss"] - base["logloss"],
        "brier": cand["brier"] - base["brier"],
        "rps": cand["rps"] - base["rps"],
        "draw_logloss": cand["draw_calibration"]["logloss"] - base["draw_calibration"]["logloss"],
        "draw_brier": cand["draw_calibration"]["brier"] - base["draw_calibration"]["brier"],
    }


def chronological_folds(rows: list[dict], k: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur_key = None; cur: list[dict] = []
    for r in rows:
        key = r["kickoff_utc"]
        if cur_key is None or key == cur_key:
            cur.append(r); cur_key = key
        else:
            groups.append(cur); cur = [r]; cur_key = key
    if cur:
        groups.append(cur)
    if len(groups) < k:
        raise RuntimeError(f"insufficient kickoff groups {len(groups)}")
    total = sum(len(g) for g in groups)
    folds: list[list[dict]] = []; acc: list[dict] = []; cumulative = 0
    for g in groups:
        boundary = total * (len(folds) + 1) / k
        if len(folds) < k - 1 and acc and cumulative + len(g) > boundary:
            folds.append(acc); acc = []
        acc.extend(g); cumulative += len(g)
    if acc:
        folds.append(acc)
    if len(folds) != k or any(not f for f in folds):
        raise RuntimeError(f"bad fold sizes {[len(f) for f in folds]}")
    return folds


def run() -> dict:
    ml = load(MARKET_LEDGER); fl = load(FOOTBALL_LEDGER)
    mpred = {}; settled = {}
    for e in ml.get("events", []):
        mid = str(e.get("match_id"))
        if e.get("event_type") == "MARKET_PREDICTION_FROZEN":
            mpred[mid] = e
        elif e.get("event_type") == "RESULT_SETTLED":
            settled[mid] = e

    fby = {}; duplicates = []
    for e in fl.get("events", []):
        if e.get("event_type") != "PREDICTION_FROZEN":
            continue
        key = identity(e["payload"])
        if key in fby:
            duplicates.append(key)
        else:
            fby[key] = e

    rows = []; unmatched = []; bad = []
    for mid, se in settled.items():
        me = mpred.get(mid)
        if me is None:
            bad.append({"match_id": mid, "reason": "settlement_without_market_prediction"}); continue
        key = identity(me["payload"])
        fe = fby.get(key)
        if fe is None:
            unmatched.append({"match_id": mid, "key": list(key)}); continue
        kickoff = iso(me["payload"]["fixture_identity"]["kickoff_at"])
        mt = iso(me["event_timestamp_utc"]); ft = iso(fe["event_timestamp_utc"])
        if not (mt < kickoff and ft < kickoff):
            bad.append({"match_id": mid, "reason": "prediction_not_prematch"}); continue
        y = str(se["payload"]["result"]["actual_result"])
        if y not in CLASSES:
            bad.append({"match_id": mid, "reason": "invalid_truth"}); continue
        rows.append({
            "match_id": mid, "kickoff_utc": kickoff.replace(microsecond=0).isoformat(), "y": y,
            "market": probs(me["payload"]["prediction"]["probabilities"]),
            "football": probs(fe["payload"]["prediction"]["formal_probabilities"]),
            "market_event_hash": me["event_hash"], "football_event_hash": fe["event_hash"],
            "settlement_event_hash": se["event_hash"],
        })
    rows.sort(key=lambda r: (r["kickoff_utc"], r["match_id"]))
    if len(rows) < MIN_MATCHED:
        raise RuntimeError(f"insufficient matched rows {len(rows)} < {MIN_MATCHED}")
    if len(rows) <= SEED_N:
        raise RuntimeError("insufficient rows after seed")

    seed = rows[:SEED_N]; scored = rows[SEED_N:]
    folds = chronological_folds(scored, FOLDS)
    history = list(seed); out_rows = []; receipts = []
    for i, fold in enumerate(folds, 1):
        beta = fit_beta(history)
        for r in fold:
            r["residual"] = residual_prob(r["market"], r["football"], beta)
        mm = metrics(fold, "market"); fm = metrics(fold, "football"); rm = metrics(fold, "residual")
        receipts.append({
            "fold": i, "train_n": len(history), "test_n": len(fold),
            "test_dates": [fold[0]["kickoff_utc"], fold[-1]["kickoff_utc"]],
            "beta": beta, "ridge_penalty": RIDGE_PENALTY,
            "market": mm, "football": fm, "market_plus_football_residual": rm,
            "residual_minus_market": delta(mm, rm),
        })
        out_rows.extend(fold); history.extend(fold)

    mm = metrics(out_rows, "market"); fm = metrics(out_rows, "football"); rm = metrics(out_rows, "residual")
    dm = delta(mm, rm); df = delta(fm, rm)
    nonneg_top1 = sum(1 for f in receipts if f["residual_minus_market"]["accuracy_pp"] >= -1e-12)
    positive_ll = sum(1 for f in receipts if f["residual_minus_market"]["logloss"] < 0)
    gate = bool(
        len(out_rows) >= MIN_SCORED
        and dm["accuracy_pp"] >= 0
        and dm["logloss"] < 0 and dm["brier"] < 0 and dm["rps"] < 0
        and dm["draw_logloss"] <= 0 and dm["draw_brier"] <= 0
        and nonneg_top1 >= 2 and positive_ll >= 2
        and rm["top1_picks"]["draw"] > 0
    )
    breakthrough = bool(gate and dm["accuracy_pp"] >= BREAKTHROUGH_PP)

    result = {
        "schema_version": "football3-r43r0-strong-shrink-football-residual-v1",
        "status": "COMPLETE", "classification": "POSTVIEW_DEVELOPMENT_ON_EXISTING_PREMATCH_FROZEN_OVERLAP", "formal_weight": 0,
        "question": "Does strongly shrunk football log-probability residual add stable information on top of the frozen direct market distribution without fixed 50/50 fusion?",
        "governance": {
            "only_existing_frozen_market_predictions": True, "only_existing_frozen_football_predictions": True,
            "only_existing_market_settlements": True, "prematch_only": True,
            "football_predictions_recomputed": False, "market_predictions_recomputed": False,
            "fixed_50_50_fusion": False, "parameter_search": False, "ridge_search": False,
            "threshold_search": False, "coverage_filter_search": False, "draw_override": False,
            "beta_fit_prior_settled_only": True, "main_merge": False, "publication": False,
        },
        "design": {
            "market_base": "frozen market-first de-vigged 1X2 probabilities",
            "football_signal": "frozen formal football probabilities",
            "residual_formula": "normalize(P_market * exp(beta * (log(P_football)-log(P_market))))",
            "beta_bounds": list(BETA_BOUNDS), "ridge_penalty": RIDGE_PENALTY,
            "seed_n": SEED_N, "folds": FOLDS, "minimum_matched": MIN_MATCHED, "minimum_scored": MIN_SCORED,
            "breakthrough_pp": BREAKTHROUGH_PP, "full_volume_target_accuracy_floor": 0.53,
        },
        "coverage": {
            "market_prediction_count": len(mpred), "market_settled_count": len(settled),
            "football_frozen_prediction_count": len(fby), "matched_settled_count": len(rows),
            "seed_n": len(seed), "scored_n": len(out_rows), "unmatched_settled_count": len(unmatched),
            "bad_count": len(bad), "football_duplicate_identity_keys": len(duplicates),
        },
        "aggregate": {
            "pure_football": fm, "pure_market": mm, "market_plus_football_residual": rm,
            "residual_minus_market": dm, "residual_minus_football": df,
            "nonnegative_top1_folds": nonneg_top1, "positive_logloss_folds": positive_ll,
        },
        "folds": receipts,
        "gate": {
            "architecture_passed": gate, "full_volume_53pct_target_met": bool(rm["top1_accuracy"] >= 0.53),
            "breakthrough_candidate": breakthrough,
            "action": "FREEZE_RESIDUAL_ARCHITECTURE_FOR_NEW_FORWARD_CONFIRMATION" if gate else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THIS_SETTLED_OVERLAP",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def verify():
    x = load(OUT); g = x["governance"]
    assert x["status"] == "COMPLETE" and x["formal_weight"] == 0
    assert g["prematch_only"] and g["football_predictions_recomputed"] is False and g["market_predictions_recomputed"] is False
    assert g["fixed_50_50_fusion"] is False and g["parameter_search"] is False and g["ridge_search"] is False
    assert g["draw_override"] is False and g["beta_fit_prior_settled_only"] is True
    assert x["design"]["ridge_penalty"] == RIDGE_PENALTY and x["design"]["beta_bounds"] == list(BETA_BOUNDS)
    print("R43R0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
