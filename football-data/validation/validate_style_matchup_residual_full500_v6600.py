#!/usr/bin/env python3
"""V6.60.0 strict-PIT actual-style matchup residual for Full500.

V6.59 showed that prior formation LABELS are well covered but do not beat market.
V6.60 therefore tests the more meaningful observable style state already frozen in the
project: pre-match xG/npxG/xPTS/PPDA/deep, rolling shots/SOT/accuracy, Elo/form/rest.

This is not a new data source and not another general CatBoost. The hypothesis is that
existing models mostly consumed marginal differences; explicit cross-style interactions
may capture matchup mechanics (pressing x territorial depth, attacking pace x opponent
finishing/shot profile, strength parity x style mismatch, rest x pressing intensity).

All base inputs inherit V6.32 strict-PIT construction. The target result never enters
its own features and same-date rolling states are frozen before updates.

Model: multinomial ridge residual around closing market probabilities. Only the curated
style / interaction vector may move market logits; away residual is reference.

Historical folds:
- train 2022/23 -> validate 2023/24
- train 2022/23+2023/24 -> validate 2024/25
Gate before A100: mean Top1 uplift >= +0.5pp, neither fold negative, LogLoss/RPS each
no worse than market by >0.005. A100 is opened only after this historical gate.
B300/C100 remain unread unless staged gate permits them.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402

OUT = ROOT / "manifests" / "v6_style_matchup_residual_full500_v6600_status.json"
FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
HIST_SEASONS = ("2022/23", "2023/24", "2024/25")
PART = "A_FAST100"
RIDGES = (0.1, 0.3, 1.0, 3.0, 10.0)
ALPHAS = (0.25, 0.50, 0.75, 1.0)
HIST_REQUIRED_MEAN_UPLIFT_PP = 0.5
PROPER_TOL = 0.005
EPS = 1e-10

RAW_NAMES = (
    "xg_edge_diff", "xg_edge_sum", "xg_edge_absdiff",
    "npxg_edge_diff", "npxg_edge_sum", "xpts_diff",
    "ppda_diff", "oppda_diff", "deep_edge_diff", "deep_edge_sum",
    "home_shot_chance", "away_shot_chance", "home_sot_chance", "away_sot_chance",
    "total_shot_chance", "total_sot_chance", "home_accuracy", "away_accuracy",
    "elo_slow_diff", "elo_fast_diff", "form_pts5_diff", "form_gd5_diff", "rest_diff_scaled",
)
INTERACTION_NAMES = (
    "ppda_x_oppda",
    "deepdiff_x_ppda", "deepdiff_x_oppda", "deepsum_x_absppda",
    "xgdiff_x_ppda", "npxgdiff_x_ppda", "xgsum_x_shots", "xgsum_x_sot",
    "home_shots_x_away_accuracy", "away_shots_x_home_accuracy",
    "home_sot_x_away_accuracy", "away_sot_x_home_accuracy",
    "xptsdiff_x_absppda", "formgd_x_ppda", "rest_x_ppda",
    "deepdiff_x_sotdiff", "xgdiff_x_sotdiff",
    "press_mismatch_abs", "shotpace_mismatch_abs", "accuracy_mismatch_abs",
    "territory_x_shotpace", "press_x_shotpace",
)
STYLE_NAMES = RAW_NAMES + INTERACTION_NAMES


def _style_vector(x: list[float], idx: dict[str, int]) -> list[float]:
    def f(name: str) -> float:
        return float(x[idx[name]])

    raw = [f(n) for n in RAW_NAMES]
    ppda = f("ppda_diff"); oppda = f("oppda_diff")
    deepd = f("deep_edge_diff"); deeps = f("deep_edge_sum")
    xgd = f("xg_edge_diff"); xgs = f("xg_edge_sum"); npxgd = f("npxg_edge_diff")
    hs = f("home_shot_chance"); a_s = f("away_shot_chance")
    hst = f("home_sot_chance"); ast = f("away_sot_chance")
    hacc = f("home_accuracy"); aacc = f("away_accuracy")
    sotdiff = hst - ast
    inter = [
        ppda * oppda,
        deepd * ppda, deepd * oppda, deeps * abs(ppda),
        xgd * ppda, npxgd * ppda, xgs * f("total_shot_chance"), xgs * f("total_sot_chance"),
        hs * aacc, a_s * hacc,
        hst * aacc, ast * hacc,
        f("xpts_diff") * abs(ppda), f("form_gd5_diff") * ppda, f("rest_diff_scaled") * ppda,
        deepd * sotdiff, xgd * sotdiff,
        abs(ppda - oppda), abs(hs - a_s), abs(hacc - aacc),
        deeps * f("total_shot_chance"), abs(ppda) * f("total_shot_chance"),
    ]
    out = raw + inter
    if len(out) != len(STYLE_NAMES):
        raise RuntimeError(f"V6.60 style feature length {len(out)} != {len(STYLE_NAMES)}")
    return out


def _build_historical() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    rows, audit, names = v632._build_rows()
    idx = {n: i for i, n in enumerate(names)}
    missing = [n for n in RAW_NAMES if n not in idx]
    if missing:
        raise RuntimeError(f"V6.60 missing required base features: {missing}")
    out = []
    for r in rows:
        if str(r["season"]) not in HIST_SEASONS:
            continue
        z = dict(r)
        z["style_features"] = _style_vector([float(v) for v in r["x"]], idx)
        out.append(z)
    by = Counter(str(r["season"]) for r in out)
    return out, {"base_audit": audit, "style_feature_names": list(STYLE_NAMES), "style_feature_count": len(STYLE_NAMES), "by_season": dict(by)}, names


def _metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = len(y); picks = probs.argmax(axis=1); hits = int(np.sum(picks == y))
    one = np.eye(3)[y]
    brier = float(np.mean(np.sum((probs - one) ** 2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:, 0] - (y == 0); c2 = probs[:, 0] + probs[:, 1] - (y <= 1)
    rps = float(np.mean((c1*c1 + c2*c2) / 2.0))
    return {
        "count": int(n), "hits": hits, "top1": hits/n,
        "brier": brier, "logloss": logloss, "rps": rps,
        "predicted_counts": dict(Counter(str(int(z)) for z in picks)),
        "actual_counts": dict(Counter(str(int(z)) for z in y)),
    }


def _fit(train: list[dict[str, Any]], ridge: float) -> dict[str, Any]:
    X = np.asarray([r["style_features"] for r in train], dtype=float)
    y = np.asarray([int(r["y"]) for r in train], dtype=int)
    market = np.asarray([r["market"] for r in train], dtype=float)
    mean = X.mean(axis=0); std = X.std(axis=0); std = np.where(std < 1e-8, 1.0, std)
    Z = (X - mean) / std; A = np.column_stack([np.ones(len(Z)), Z]); p = A.shape[1]

    def calc(theta: np.ndarray) -> tuple[float, np.ndarray]:
        B = theta.reshape(2, p)
        residual = A @ B.T
        eta = np.log(np.clip(market, EPS, 1.0))
        eta[:, 0] += residual[:, 0]; eta[:, 1] += residual[:, 1]
        eta -= eta.max(axis=1, keepdims=True)
        probs = np.exp(eta); probs /= probs.sum(axis=1, keepdims=True)
        nll = -float(np.sum(np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1.0))))
        penalty = 0.5 * float(ridge) * float(np.sum(B[:, 1:]**2))
        target = np.zeros((len(y), 2), dtype=float); target[:, 0] = (y == 0); target[:, 1] = (y == 1)
        grad = (probs[:, :2] - target).T @ A
        grad[:, 1:] += float(ridge) * B[:, 1:]
        return nll + penalty, grad.reshape(-1)

    theta0 = np.zeros(2*p, dtype=float)
    res = minimize(lambda t: calc(t)[0], theta0, jac=lambda t: calc(t)[1], method="L-BFGS-B", options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        raise RuntimeError(f"V6.60 fit failed ridge={ridge}: {res.message}")
    return {
        "ridge": float(ridge), "mean": mean, "std": std,
        "B": np.asarray(res.x, dtype=float).reshape(2, p),
        "iterations": int(res.nit), "objective": float(res.fun),
        "gradient_max_abs": float(np.max(np.abs(calc(np.asarray(res.x, dtype=float))[1]))),
    }


def _predict(rows: list[dict[str, Any]], model: dict[str, Any], alpha: float) -> np.ndarray:
    X = np.asarray([r["style_features"] for r in rows], dtype=float)
    Z = (X - model["mean"]) / model["std"]
    A = np.column_stack([np.ones(len(Z)), Z])
    residual = A @ model["B"].T
    market = np.asarray([r["market"] for r in rows], dtype=float)
    eta = np.log(np.clip(market, EPS, 1.0))
    eta[:, 0] += float(alpha)*residual[:, 0]; eta[:, 1] += float(alpha)*residual[:, 1]
    eta -= eta.max(axis=1, keepdims=True)
    probs = np.exp(eta); probs /= probs.sum(axis=1, keepdims=True)
    return probs


def _json_model(model: dict[str, Any]) -> dict[str, Any]:
    bh = model["B"][0]; bd = model["B"][1]
    return {
        "ridge": model["ridge"], "iterations": model["iterations"], "objective": model["objective"], "gradient_max_abs": model["gradient_max_abs"],
        "home_residual_coefficients": {"intercept": float(bh[0]), **{n: float(v) for n, v in zip(STYLE_NAMES, bh[1:])}},
        "draw_residual_coefficients": {"intercept": float(bd[0]), **{n: float(v) for n, v in zip(STYLE_NAMES, bd[1:])}},
    }


def _load_a100(base_names: list[str]) -> tuple[list[dict[str, Any]], np.ndarray]:
    idx = {n: i for i, n in enumerate(base_names)}
    feats = [json.loads(x) for x in FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    feats = [r for r in feats if r.get("partition") == PART]
    feats.sort(key=lambda r: int(r["full_index"]))
    if len(feats) != 100:
        raise RuntimeError(f"V6.60 A100 features {len(feats)}")
    rows = []
    for f in feats:
        x = [float(v) for v in f["base_features"]]
        rows.append({"full_index": int(f["full_index"]), "market": [float(v) for v in f["market"]], "style_features": _style_vector(x, idx)})
    labels = []
    with LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            r = json.loads(h.readline())
            if r.get("partition") != PART or int(r["full_index"]) != len(labels):
                raise RuntimeError("V6.60 A100 label contract changed")
            labels.append(int(r["label"]))
    return rows, np.asarray(labels, dtype=int)


def main() -> int:
    hist, audit, base_names = _build_historical()
    folds = (({"2022/23"}, "2023/24"), ({"2022/23", "2023/24"}, "2024/25"))
    board = []
    for ridge in RIDGES:
        cached = []
        for train_seasons, valid_season in folds:
            train = [r for r in hist if str(r["season"]) in train_seasons]
            valid = [r for r in hist if str(r["season"]) == valid_season]
            cached.append((valid_season, valid, _fit(train, ridge)))
        for alpha in ALPHAS:
            recs = []; proper = True
            for valid_season, valid, model in cached:
                y = np.asarray([int(r["y"]) for r in valid], dtype=int)
                market = np.asarray([r["market"] for r in valid], dtype=float)
                cand = _predict(valid, model, alpha)
                mm = _metrics(y, market); cm = _metrics(y, cand)
                rec = {
                    "valid_season": valid_season, "market": mm, "candidate": cm,
                    "uplift_pp": 100.0*(cm["top1"]-mm["top1"]),
                    "logloss_delta": cm["logloss"]-mm["logloss"], "rps_delta": cm["rps"]-mm["rps"],
                }
                proper = proper and rec["logloss_delta"] <= PROPER_TOL+1e-12 and rec["rps_delta"] <= PROPER_TOL+1e-12
                recs.append(rec)
            ups = [r["uplift_pp"] for r in recs]
            board.append({"ridge": ridge, "alpha": alpha, "folds": recs, "mean_uplift_pp": float(np.mean(ups)), "min_uplift_pp": float(min(ups)), "proper_guard": bool(proper)})
    board.sort(key=lambda z: (z["proper_guard"], z["min_uplift_pp"], z["mean_uplift_pp"], -z["ridge"], -z["alpha"]), reverse=True)
    chosen = board[0]
    hist_gate = bool(chosen["proper_guard"] and chosen["mean_uplift_pp"] >= HIST_REQUIRED_MEAN_UPLIFT_PP-1e-12 and chosen["min_uplift_pp"] >= -1e-12)
    final_model = _fit(hist, float(chosen["ridge"]))
    payload: dict[str, Any] = {
        "schema_version": "V6.60.0-style-matchup-residual-full500-r1", "status": "PASS",
        "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {"strict_PIT_base_inherited": True, "explicit_style_interactions": True, "general_tree_model": False, "A100_values_used_for_selection": False, "B_CONFIRM300_labels_read": False, "C_SEALED100_labels_read": False, "CURRENT_unchanged": True},
        "historical_audit": audit,
        "grid": {"ridges": list(RIDGES), "alphas": list(ALPHAS), "historical_required_mean_uplift_pp": HIST_REQUIRED_MEAN_UPLIFT_PP, "proper_tolerance": PROPER_TOL},
        "selected_historical": chosen, "historical_gate": hist_gate, "historical_leaderboard": board,
        "final_historical_fit": _json_model(final_model),
    }
    if not hist_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_GATE_FAILED"}; payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0
    arows, y = _load_a100(base_names)
    market = np.asarray([r["market"] for r in arows], dtype=float); cand = _predict(arows, final_model, float(chosen["alpha"]))
    mm = _metrics(y, market); cm = _metrics(y, cand); uplift = 100.0*(cm["top1"]-mm["top1"])
    proper = bool(cm["logloss"] <= mm["logloss"]+0.01 and cm["rps"] <= mm["rps"]+0.01)
    gate = {"required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0, "candidate_hits": cm["hits"], "market_hits": mm["hits"], "uplift_vs_market_pp": uplift, "top1_gate": cm["hits"] >= 63, "uplift_gate": uplift >= 3.0-1e-12, "proper_score_guard": proper}
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "market": mm, "candidate": cm, "gate": gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
