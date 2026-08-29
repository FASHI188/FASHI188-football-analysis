"""Exact research-governance migration of the R43Q0 market score core.

Source commit: d738d5066d5c5a79eb4ee5856c034ee239521706
Source blob: 5f4b1f8a9fba1a5449f789c01f3c76eab814ad1b

This module is disabled for formal inference. It preserves the frozen R43Q0
numerics so the unified engine can later call one governed component instead of
copying experiment code.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import minimize

CLASSES = ("home", "draw", "away")
MAX_GOALS = 12
LAMBDA_BOUNDS = (0.05, 4.50)
DRAW_CAL_PENALTY = 25.0
SOURCE_COMMIT = "d738d5066d5c5a79eb4ee5856c034ee239521706"
SOURCE_BLOB_SHA = "5f4b1f8a9fba1a5449f789c01f3c76eab814ad1b"


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


class R43QMarketScoreCore:
    component_id = "R43Q_market_score_core"
    component_version = "r43gov0-m5c-q-v1"
    enabled = False
    source_commit = SOURCE_COMMIT
    source_blob_sha = SOURCE_BLOB_SHA

    @staticmethod
    def build(one_x_two_odds: dict, asian_handicap: dict, over_under: dict) -> dict[str, Any]:
        market = devig_1x2(one_x_two_odds)
        lh, la, objective = infer_lambdas(asian_handicap, over_under, market)
        matrix = score_matrix(lh, la)
        return {
            "market_1x2": market,
            "lambda_home": lh,
            "lambda_away": la,
            "fit_objective": objective,
            "score_matrix": matrix,
            "latent_1x2": matrix_1x2(matrix),
        }
