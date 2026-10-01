from __future__ import annotations
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
import numpy as np
from wmv1_source import *
from wmv1_features import *
@dataclass(frozen=True)
class FittedHazard:
    beta: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    rows: int
    iterations: int

def fit_hazard(rows: list[tuple[np.ndarray, float, int]]) -> FittedHazard | None:
    if len(rows) < MIN_TRAIN_ROWS:
        return None
    xraw = np.vstack([r[0] for r in rows]).astype(float)
    offset = np.asarray([r[1] for r in rows], dtype=float)
    y = np.asarray([r[2] for r in rows], dtype=float)
    mean = xraw.mean(axis=0)
    std = xraw.std(axis=0)
    std = np.where(std < 1e-06, 1.0, std)
    xs = (xraw - mean) / std
    x = np.column_stack([np.ones(len(xs)), xs])
    beta = np.zeros(x.shape[1], dtype=float)
    penalty = np.eye(x.shape[1], dtype=float) * RIDGE_ALPHA
    penalty[0, 0] = 0.0
    used = 0
    for used in range(1, IRLS_ITERS + 1):
        eta = np.clip(offset + x @ beta, -9.0, math.log(1.5))
        mu = np.exp(eta)
        z = eta + (y - mu) / np.maximum(mu, 1e-06)
        wx = mu[:, None] * x
        a = x.T @ wx + penalty
        b = x.T @ (mu * (z - offset))
        try:
            new_beta = np.linalg.solve(a, b)
        except np.linalg.LinAlgError:
            new_beta = np.linalg.lstsq(a, b, rcond=None)[0]
        new_beta[1:] = np.clip(new_beta[1:], -COEF_CLIP, COEF_CLIP)
        if float(np.max(np.abs(new_beta - beta))) < 1e-06:
            beta = new_beta
            break
        beta = new_beta
    return FittedHazard(beta=beta, mean=mean, std=std, rows=len(rows), iterations=used)

def predict_mu_batch(model: FittedHazard, xraw: np.ndarray, offset: float) -> np.ndarray:
    xs = (xraw - model.mean) / model.std
    x = np.column_stack([np.ones(len(xs)), xs])
    eta = np.clip(offset + x @ model.beta, -9.0, math.log(1.5))
    return np.exp(eta)

def poisson_bucket(lam: float) -> np.ndarray:
    probs = [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(7)]
    tail = max(0.0, 1.0 - sum(probs))
    out = np.asarray(probs + [tail], dtype=float)
    return out / out.sum()

def baseline_outputs(lh: float, la: float) -> dict[str, np.ndarray]:
    ph = poisson_bucket(lh)
    pa = poisson_bucket(la)
    matrix = np.outer(ph, pa)
    max_goal = 16
    exh = np.asarray([math.exp(-lh) * lh ** k / math.factorial(k) for k in range(max_goal + 1)], dtype=float)
    exa = np.asarray([math.exp(-la) * la ** k / math.factorial(k) for k in range(max_goal + 1)], dtype=float)
    exh[-1] += max(0.0, 1.0 - exh.sum())
    exa[-1] += max(0.0, 1.0 - exa.sum())
    exact = np.outer(exh, exa)
    hda = np.asarray([np.tril(exact, -1).sum(), np.trace(exact), np.triu(exact, 1).sum()], dtype=float)
    hda /= hda.sum()
    total = poisson_bucket(lh + la)
    return {'matrix': matrix, 'hda': hda, 'total': total}

def simulate_candidate(ctx: PredictionContext, model: FittedHazard) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(RANDOM_SEED ^ int(ctx.meta.match_id))
    hg = np.zeros(SIMS, dtype=np.int16)
    ag = np.zeros(SIMS, dtype=np.int16)
    for seg in range(SEGMENTS):
        period = min(seg // 3, TEMPORAL_BINS - 1)
        hdiff = hg.astype(float) - ag.astype(float)
        adiff = -hdiff
        hx = feature_batch(ctx.static_home, seg, hdiff)
        ax = feature_batch(ctx.static_away, seg, adiff)
        hoff = math.log(max(ctx.lh / SEGMENTS * ctx.temporal_home[period], 1e-08))
        aoff = math.log(max(ctx.la / SEGMENTS * ctx.temporal_away[period], 1e-08))
        hmu = predict_mu_batch(model, hx, hoff)
        amu = predict_mu_batch(model, ax, aoff)
        hnew = rng.poisson(hmu).astype(np.int16)
        anew = rng.poisson(amu).astype(np.int16)
        hg += hnew
        ag += anew
    hb = np.minimum(hg, 7)
    ab = np.minimum(ag, 7)
    counts = np.full((8, 8), MATRIX_ALPHA, dtype=float)
    np.add.at(counts, (hb, ab), 1.0)
    matrix = counts / counts.sum()
    hda = np.asarray([(hg > ag).mean(), (hg == ag).mean(), (hg < ag).mean()], dtype=float)
    total_idx = np.minimum(hg + ag, 7)
    total_counts = np.bincount(total_idx, minlength=8).astype(float) + MATRIX_ALPHA
    total = total_counts / total_counts.sum()
    return {'matrix': matrix, 'hda': hda, 'total': total}

def outcome_index(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2

def multiclass_brier(p: np.ndarray, y: int) -> float:
    one = np.zeros(len(p), dtype=float)
    one[y] = 1.0
    return float(np.sum((p - one) ** 2))

def total_rps(p: np.ndarray, total: int) -> float:
    y = min(total, 7)
    cdf = np.cumsum(p)[:-1]
    truth = np.asarray([1.0 if y <= k else 0.0 for k in range(7)], dtype=float)
    return float(np.mean((cdf - truth) ** 2))

def draw_logloss(pdraw: float, is_draw: bool) -> float:
    p = float(np.clip(pdraw, PROB_FLOOR, 1.0 - PROB_FLOOR))
    return float(-math.log(p if is_draw else 1.0 - p))

def calibration_bins(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, 6)
    out = []
    for i in range(5):
        lo, hi = (float(edges[i]), float(edges[i + 1]))
        vals = [r for r in rows if lo <= float(r[key]) < hi or (i == 4 and float(r[key]) <= hi)]
        if not vals:
            out.append({'lo': lo, 'hi': hi, 'n': 0, 'mean_pred': None, 'actual_draw_rate': None})
        else:
            out.append({'lo': lo, 'hi': hi, 'n': len(vals), 'mean_pred': float(np.mean([r[key] for r in vals])), 'actual_draw_rate': float(np.mean([1.0 if r['is_draw'] else 0.0 for r in vals]))})
    return out

def make_folds(test_dates: list[str], match_count_by_date: dict[str, int]) -> dict[str, int]:
    total = sum((match_count_by_date[d] for d in test_dates))
    targets = [total / 3.0, 2.0 * total / 3.0]
    fold = 0
    seen = 0
    out: dict[str, int] = {}
    for d in test_dates:
        if fold < 2 and seen >= targets[fold]:
            fold += 1
        out[d] = fold
        seen += match_count_by_date[d]
    return out

def bootstrap_primary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_date[r['match_date']].append(float(r['score_delta']))
    dates = sorted(by_date)
    rng = np.random.default_rng(RANDOM_SEED + 991)
    samples = np.empty(BOOTSTRAP_REPS, dtype=float)
    for b in range(BOOTSTRAP_REPS):
        chosen = rng.choice(dates, size=len(dates), replace=True)
        vals: list[float] = []
        for d in chosen:
            vals.extend(by_date[str(d)])
        samples[b] = float(np.mean(vals))
    return {'clusters': len(dates), 'replicates': BOOTSTRAP_REPS, 'observed_mean_delta': float(np.mean([r['score_delta'] for r in rows])), 'p05': float(np.quantile(samples, 0.05)), 'median': float(np.quantile(samples, 0.5)), 'p95': float(np.quantile(samples, 0.95)), 'probability_candidate_better': float(np.mean(samples < 0.0))}
