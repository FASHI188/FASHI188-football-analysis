#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
OUT = HERE / "results" / "summary_r43q0_sharp_market_score_base.json"

CLASSES = ("home", "draw", "away")
MAX_GOALS = 12
LAMBDA_BOUNDS = (0.05, 4.50)
DRAW_CAL_PENALTY = 25.0
SEED_SETTLED = 30
FOLDS = 3
MIN_SCORED = 45
BREAKTHROUGH_PP = 1.0


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iso(x: str) -> datetime:
    dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def clip01(x: float) -> float:
    return float(min(1.0 - 1e-9, max(1e-9, x)))


def logit(x: float) -> float:
    x = clip01(x)
    return math.log(x / (1.0 - x))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def devig_1x2(odds: dict) -> dict[str, float]:
    inv = {k: 1.0 / float(odds[k]) for k in CLASSES}
    s = sum(inv.values())
    return {k: inv[k] / s for k in CLASSES}


def poisson_pmf(mu: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    p = np.empty(max_goals + 1, dtype=float)
    p[0] = math.exp(-mu)
    for k in range(1, max_goals + 1):
        p[k] = p[k - 1] * mu / k
    return p


def score_matrix(lh: float, la: float) -> np.ndarray:
    ph = poisson_pmf(lh)
    pa = poisson_pmf(la)
    m = np.outer(ph, pa)
    s = float(m.sum())
    if not np.isfinite(s) or s <= 0:
        raise RuntimeError("invalid score matrix")
    return m / s


def split_quarter_line(line: float) -> tuple[float, float]:
    q = round(float(line) * 4.0) / 4.0
    frac = abs(q * 2.0 - round(q * 2.0))
    if frac < 1e-8:
        return q, q
    lo = math.floor(q * 2.0) / 2.0
    hi = lo + 0.5
    return lo, hi


def asian_return_for_margin(margin: int, line: float, odds: float) -> float:
    a, b = split_quarter_line(line)
    total = 0.0
    for h in (a, b):
        z = float(margin) + h
        if z > 1e-9:
            total += float(odds)
        elif z < -1e-9:
            total += 0.0
        else:
            total += 1.0
    return total / 2.0


def ou_return_for_total(total_goals: int, line: float, odds: float, over: bool) -> float:
    a, b = split_quarter_line(line)
    total = 0.0
    for h in (a, b):
        z = float(total_goals) - h
        if not over:
            z = -z
        if z > 1e-9:
            total += float(odds)
        elif z < -1e-9:
            total += 0.0
        else:
            total += 1.0
    return total / 2.0


def expected_returns(m: np.ndarray, ah: dict, ou: dict) -> tuple[float, float, float, float]:
    ehr = ear = eor = eur = 0.0
    ah_line = float(ah["line"])
    ah_home_odds = float(ah["home"])
    ah_away_odds = float(ah["away"])
    ou_line = float(ou["line"])
    over_odds = float(ou["over"])
    under_odds = float(ou["under"])
    for hg in range(m.shape[0]):
        for ag in range(m.shape[1]):
            p = float(m[hg, ag])
            margin = hg - ag
            total = hg + ag
            ehr += p * asian_return_for_margin(margin, ah_line, ah_home_odds)
            ear += p * asian_return_for_margin(-margin, -ah_line, ah_away_odds)
            eor += p * ou_return_for_total(total, ou_line, over_odds, True)
            eur += p * ou_return_for_total(total, ou_line, under_odds, False)
    return ehr, ear, eor, eur


def matrix_1x2(m: np.ndarray) -> dict[str, float]:
    h = d = a = 0.0
    for hg in range(m.shape[0]):
        for ag in range(m.shape[1]):
            p = float(m[hg, ag])
            if hg > ag:
                h += p
            elif hg == ag:
                d += p
            else:
                a += p
    s = h + d + a
    return {"home": h / s, "draw": d / s, "away": a / s}


def latent_objective(x: np.ndarray, ah: dict, ou: dict, market: dict[str, float]) -> float:
    lh, la = float(math.exp(x[0])), float(math.exp(x[1]))
    if not (LAMBDA_BOUNDS[0] <= lh <= LAMBDA_BOUNDS[1] and LAMBDA_BOUNDS[0] <= la <= LAMBDA_BOUNDS[1]):
        return 1e6
    m = score_matrix(lh, la)
    ehr, ear, eor, eur = expected_returns(m, ah, ou)
    raw = matrix_1x2(m)
    vals = (ehr, ear, eor, eur, raw["home"], raw["away"], market["home"], market["away"])
    if any(v <= 0 or not np.isfinite(v) for v in vals):
        return 1e6
    r_ah = math.log(ehr / ear)
    r_ou = math.log(eor / eur)
    r_dir = math.log(raw["home"] / raw["away"]) - math.log(market["home"] / market["away"])
    return r_ah * r_ah + r_ou * r_ou + r_dir * r_dir


def infer_lambdas(ah: dict, ou: dict, market: dict[str, float]) -> tuple[float, float, float]:
    ratio = math.sqrt(max(1e-6, market["home"] / market["away"]))
    total0 = min(4.5, max(1.0, float(ou["line"]) + 0.35))
    h0 = total0 * ratio / (1.0 + ratio)
    a0 = total0 / (1.0 + ratio)
    starts = [
        (h0, a0),
        (max(0.25, h0 * 0.8), max(0.25, a0 * 0.8)),
        (min(3.8, h0 * 1.2), min(3.8, a0 * 1.2)),
        (1.35, 1.10),
    ]
    best = None
    bounds = [(math.log(LAMBDA_BOUNDS[0]), math.log(LAMBDA_BOUNDS[1]))] * 2
    for sh, sa in starts:
        res = minimize(
            latent_objective,
            np.log([sh, sa]),
            args=(ah, ou, market),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 160, "ftol": 1e-12},
        )
        val = float(res.fun)
        if best is None or val < best[0]:
            best = (val, float(math.exp(res.x[0])), float(math.exp(res.x[1])))
    assert best is not None
    return best[1], best[2], best[0]


def fit_draw_cal(train: list[dict]) -> tuple[float, float]:
    # Identity anchor is direct market draw: logit(p_cal)=logit(p_market)+a+b*(logit(p_raw)-logit(p_market)).
    # Strong L2 shrink a,b -> 0; fixed before outcome scoring.
    def obj(z: np.ndarray) -> float:
        a, b = float(z[0]), float(z[1])
        loss = 0.0
        for r in train:
            xm = logit(r["market"]["draw"])
            dx = logit(r["latent_raw"]["draw"]) - xm
            p = clip01(sigmoid(xm + a + b * dx))
            y = 1.0 if r["y"] == "draw" else 0.0
            loss += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        loss += 0.5 * DRAW_CAL_PENALTY * (a * a + b * b)
        return loss

    res = minimize(obj, np.array([0.0, 0.0]), method="BFGS", options={"maxiter": 300, "gtol": 1e-9})
    return float(res.x[0]), float(res.x[1])


def apply_draw_cal(row: dict, ab: tuple[float, float]) -> tuple[dict[str, float], np.ndarray]:
    a, b = ab
    pm = row["market"]["draw"]
    pr = row["latent_raw"]["draw"]
    pd = clip01(sigmoid(logit(pm) + a + b * (logit(pr) - logit(pm))))
    raw = row["latent_raw"]
    non = raw["home"] + raw["away"]
    ph = (1.0 - pd) * raw["home"] / non
    pa = (1.0 - pd) * raw["away"] / non
    p = {"home": ph, "draw": pd, "away": pa}

    m0 = row["matrix_raw"]
    m = np.array(m0, dtype=float, copy=True)
    scale = {
        "home": ph / max(raw["home"], 1e-15),
        "draw": pd / max(raw["draw"], 1e-15),
        "away": pa / max(raw["away"], 1e-15),
    }
    for hg in range(m.shape[0]):
        for ag in range(m.shape[1]):
            k = "home" if hg > ag else "draw" if hg == ag else "away"
            m[hg, ag] *= scale[k]
    m /= m.sum()
    return p, m


def top1(p: dict[str, float]) -> str:
    return max(CLASSES, key=lambda k: (p[k], -CLASSES.index(k)))


def calibration_draw(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"n": 0}
    ps = np.array([float(r[key]["draw"]) for r in rows], dtype=float)
    ys = np.array([1.0 if r["y"] == "draw" else 0.0 for r in rows], dtype=float)
    ll = float(np.mean(-(ys * np.log(np.clip(ps, 1e-15, 1.0)) + (1.0 - ys) * np.log(np.clip(1.0 - ps, 1e-15, 1.0)))))
    br = float(np.mean((ps - ys) ** 2))
    order = np.argsort(ps)
    bins = np.array_split(order, min(5, len(order)))
    ece = 0.0
    out_bins = []
    for idx in bins:
        if len(idx) == 0:
            continue
        mp = float(ps[idx].mean()); ar = float(ys[idx].mean()); w = len(idx) / len(rows)
        ece += w * abs(mp - ar)
        out_bins.append({"n": int(len(idx)), "mean_pred": mp, "actual_rate": ar})
    return {
        "n": len(rows), "mean_pred": float(ps.mean()), "actual_rate": float(ys.mean()),
        "logloss": ll, "brier": br, "ece5": float(ece), "bins": out_bins,
    }


def metrics(rows: list[dict], key: str) -> dict:
    n = len(rows)
    hits = 0; ll = 0.0; br = 0.0; rps = 0.0
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
        "draw_calibration": calibration_draw(rows, key),
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


def grouped_folds(rows: list[dict], k: int) -> list[list[dict]]:
    groups = []
    cur_key = None; cur = []
    for r in rows:
        key = r["kickoff_utc"]
        if cur_key is None or key == cur_key:
            cur.append(r); cur_key = key
        else:
            groups.append(cur); cur = [r]; cur_key = key
    if cur:
        groups.append(cur)
    target = len(rows) / k
    folds = []; acc = []; nacc = 0
    for g in groups:
        if len(folds) < k - 1 and acc and nacc + len(g) > target * (len(folds) + 1):
            folds.append(acc); acc = []; nacc = sum(len(x) for x in folds)
        acc.extend(g)
    folds.append(acc)
    while len(folds) < k:
        folds.append([])
    return folds[:k]


def compact_matrix(m: np.ndarray, n: int = 8) -> list[dict]:
    cells = []
    for hg in range(m.shape[0]):
        for ag in range(m.shape[1]):
            cells.append((float(m[hg, ag]), hg, ag))
    cells.sort(reverse=True)
    return [{"score": f"{h}-{a}", "probability": p} for p, h, a in cells[:n]]


def run() -> dict:
    ledger = load(LEDGER)
    preds = {}; settled = {}
    for e in ledger.get("events", []):
        mid = str(e.get("match_id"))
        if e.get("event_type") == "MARKET_PREDICTION_FROZEN":
            preds[mid] = e
        elif e.get("event_type") == "RESULT_SETTLED":
            settled[mid] = e

    rows = []; errors = []
    for mid, se in settled.items():
        pe = preds.get(mid)
        if pe is None:
            errors.append({"match_id": mid, "reason": "settlement_without_prediction"}); continue
        pld = pe["payload"]; fx = pld["fixture_identity"]; surf = pld["frozen_surfaces"]
        kickoff = iso(fx["kickoff_at"]); frozen = iso(pe["event_timestamp_utc"])
        if not frozen < kickoff:
            errors.append({"match_id": mid, "reason": "not_prematch"}); continue
        y = str(se["payload"]["result"]["actual_result"])
        if y not in CLASSES:
            errors.append({"match_id": mid, "reason": "bad_truth"}); continue
        market = devig_1x2(surf["one_x_two_odds"])
        try:
            lh, la, fit_obj = infer_lambdas(surf["asian_handicap"], surf["over_under"], market)
            mat = score_matrix(lh, la); raw = matrix_1x2(mat)
        except Exception as ex:
            errors.append({"match_id": mid, "reason": "latent_fit_failed", "error": repr(ex)}); continue
        rows.append({
            "match_id": mid, "kickoff_utc": kickoff.isoformat(), "frozen_utc": frozen.isoformat(), "y": y,
            "market": market, "latent_raw": raw, "matrix_raw": mat,
            "lambda_home": lh, "lambda_away": la, "fit_objective": fit_obj,
            "ah_line": float(surf["asian_handicap"]["line"]), "ou_line": float(surf["over_under"]["line"]),
            "prediction_event_hash": pe["event_hash"], "settlement_event_hash": se["event_hash"],
        })
    rows.sort(key=lambda r: (r["kickoff_utc"], r["match_id"]))

    if len(rows) <= SEED_SETTLED:
        raise RuntimeError(f"insufficient rows {len(rows)} <= seed {SEED_SETTLED}")
    seed = rows[:SEED_SETTLED]
    scored = rows[SEED_SETTLED:]
    folds = grouped_folds(scored, FOLDS)
    history = list(seed)
    fold_receipts = []
    scored_out = []
    for i, fold in enumerate(folds, 1):
        if not fold:
            raise RuntimeError(f"empty fold {i}")
        ab = fit_draw_cal(history)
        for r in fold:
            p, m = apply_draw_cal(r, ab)
            r["sharp"] = p
            r["matrix_sharp"] = m
        bm = metrics(fold, "market"); rm = metrics(fold, "latent_raw"); sm = metrics(fold, "sharp")
        fold_receipts.append({
            "fold": i, "train_n": len(history), "test_n": len(fold),
            "test_dates": [fold[0]["kickoff_utc"], fold[-1]["kickoff_utc"]],
            "draw_calibrator": {"intercept_shift": ab[0], "raw_minus_market_slope": ab[1], "penalty": DRAW_CAL_PENALTY},
            "market": bm, "latent_raw": rm, "sharp": sm, "sharp_minus_market": delta(bm, sm),
        })
        scored_out.extend(fold)
        history.extend(fold)

    market_m = metrics(scored_out, "market")
    raw_m = metrics(scored_out, "latent_raw")
    sharp_m = metrics(scored_out, "sharp")
    d = delta(market_m, sharp_m)
    nonneg_top1 = sum(1 for f in fold_receipts if f["sharp_minus_market"]["accuracy_pp"] >= -1e-12)
    positive_ll = sum(1 for f in fold_receipts if f["sharp_minus_market"]["logloss"] < 0)
    architecture_gate = bool(
        len(scored_out) >= MIN_SCORED
        and d["accuracy_pp"] >= 0
        and d["logloss"] < 0 and d["brier"] < 0 and d["rps"] < 0
        and d["draw_logloss"] < 0 and d["draw_brier"] < 0
        and nonneg_top1 >= 2 and positive_ll >= 2
        and sharp_m["top1_picks"]["draw"] > 0
    )
    full_volume_target = bool(sharp_m["top1_accuracy"] >= 0.53)
    breakthrough = bool(architecture_gate and d["accuracy_pp"] >= BREAKTHROUGH_PP)

    examples = []
    for r in scored_out[:10]:
        examples.append({
            "match_id": r["match_id"], "kickoff_utc": r["kickoff_utc"], "y": r["y"],
            "lambda_home": r["lambda_home"], "lambda_away": r["lambda_away"],
            "market": r["market"], "latent_raw": r["latent_raw"], "sharp": r["sharp"],
            "top_scores": compact_matrix(r["matrix_sharp"]),
        })

    result = {
        "schema_version": "football3-r43q0-sharp-market-score-base-v1",
        "status": "COMPLETE",
        "classification": "POSTVIEW_ARCHITECTURE_DEVELOPMENT_ON_GENUINE_PREMATCH_FROZEN_MARKETS",
        "formal_weight": 0,
        "question": "Can same-timestamp frozen 1X2+AH+OU be converted into a sharper full score matrix with separately rolling-calibrated draw probability?",
        "governance": {
            "prematch_frozen_only": True, "same_snapshot_surfaces_only": True,
            "result_used_as_feature": False, "inplay_used": False, "postmatch_market_used": False,
            "settled_rows_previously_consumed_by_prior_research": True,
            "parameter_search": False, "threshold_search": False, "coverage_filter_search": False,
            "lambda_fit_uses_outcomes": False, "draw_calibrator_train_only_prior_settled": True,
            "draw_forced_count": False, "unified_argmax": True,
            "main_merge": False, "publication": False,
        },
        "design": {
            "score_family": "independent Poisson latent matrix",
            "latent_fit": "minimize log expected-return imbalance for Asian handicap and over/under plus 1X2 home-v-away non-draw log-odds residual",
            "asian_quarter_integer_push_handling": "exact split-stake settlement returns",
            "draw_calibration": "logit(p_market_draw)+a+b*(logit(p_raw_draw)-logit(p_market_draw)); a,b prior-only fitted with fixed L2 shrink to zero",
            "draw_cal_penalty": DRAW_CAL_PENALTY,
            "matrix_reconstruction": "scale home-win, diagonal-draw and away-win score regions to calibrated 1X2 masses while preserving within-region score shape",
            "seed_settled": SEED_SETTLED, "folds": FOLDS, "max_goals": MAX_GOALS,
            "minimum_scored": MIN_SCORED, "breakthrough_pp": BREAKTHROUGH_PP,
            "full_volume_target_accuracy_floor": 0.53,
        },
        "coverage": {
            "prediction_count": len(preds), "settled_count": len(settled), "latent_fitted_count": len(rows),
            "seed_n": len(seed), "scored_n": len(scored_out), "error_count": len(errors), "errors": errors[:20],
        },
        "aggregate": {
            "market_direct": market_m, "latent_raw": raw_m, "sharp_market_base": sharp_m,
            "sharp_minus_market": d,
            "nonnegative_top1_folds": nonneg_top1, "positive_logloss_folds": positive_ll,
        },
        "folds": fold_receipts,
        "gate": {
            "architecture_passed": architecture_gate,
            "full_volume_53pct_target_met": full_volume_target,
            "breakthrough_candidate": breakthrough,
            "action": "FREEZE_ARCHITECTURE_FOR_NEW_FORWARD_CONFIRMATION" if architecture_gate else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THESE_SETTLED_MATCHES",
        },
        "examples": examples,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def verify():
    x = load(OUT)
    assert x["status"] == "COMPLETE" and x["formal_weight"] == 0
    g = x["governance"]
    assert g["prematch_frozen_only"] and g["same_snapshot_surfaces_only"]
    assert g["result_used_as_feature"] is False and g["inplay_used"] is False
    assert g["parameter_search"] is False and g["threshold_search"] is False
    assert g["draw_forced_count"] is False and g["unified_argmax"] is True
    assert x["design"]["draw_cal_penalty"] == DRAW_CAL_PENALTY
    assert x["design"]["seed_settled"] == SEED_SETTLED and x["design"]["folds"] == FOLDS
    print("R43Q0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(cmd)
