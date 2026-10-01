from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from wmv2_source import BOOTSTRAP_REPS, PROB_FLOOR, RANDOM_SEED

THETA_MAX = 0.35
THETA_PENALTY = 8.0
MIN_THETA_HISTORY = 100
TAIL_RESIDUAL_LIMIT = 1e-10


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1.0))


def poisson_probs_through(lam: float, kmax: int) -> np.ndarray:
    out = np.zeros(kmax + 1, dtype=float)
    out[0] = math.exp(-lam)
    for k in range(kmax):
        out[k + 1] = out[k] * lam / float(k + 1)
    return out


def _adaptive_kmax(mu_home: float, mu_away: float) -> int:
    k = max(16, int(math.ceil(max(mu_home, mu_away) + 10.0)))
    while True:
        ph = poisson_probs_through(mu_home, k)
        pa = poisson_probs_through(mu_away, k)
        residual = max(0.0, 1.0 - float(ph.sum())) + max(0.0, 1.0 - float(pa.sum()))
        if residual <= 5e-13:
            return k
        k += 4
        if k > 80:
            raise RuntimeError("failed to close Poisson marginal tails by k=80")


def bivariate_poisson_pmf(
    home_goals: int, away_goals: int, mu_home: float, mu_away: float, theta: float
) -> float:
    theta = float(np.clip(theta, 0.0, THETA_MAX))
    kappa = theta * min(mu_home, mu_away)
    lam_h = max(0.0, mu_home - kappa)
    lam_a = max(0.0, mu_away - kappa)
    total = 0.0
    for shared in range(min(home_goals, away_goals) + 1):
        total += (
            poisson_pmf(home_goals - shared, lam_h)
            * poisson_pmf(away_goals - shared, lam_a)
            * poisson_pmf(shared, kappa)
        )
    return total


def independent_poisson_pmf(
    home_goals: int, away_goals: int, mu_home: float, mu_away: float
) -> float:
    return poisson_pmf(home_goals, mu_home) * poisson_pmf(away_goals, mu_away)


def _total_distribution(mu_home: float, mu_away: float, theta: float, bucket_max: int = 7) -> np.ndarray:
    theta = float(np.clip(theta, 0.0, THETA_MAX))
    kappa = theta * min(mu_home, mu_away)
    lam_single = max(0.0, mu_home + mu_away - 2.0 * kappa)
    probs = np.zeros(bucket_max + 1, dtype=float)
    # T = A + B + 2C where A+B ~ Poisson(lam_single), C ~ Poisson(kappa).
    for t in range(bucket_max):
        p = 0.0
        for c in range(t // 2 + 1):
            p += poisson_pmf(c, kappa) * poisson_pmf(t - 2 * c, lam_single)
        probs[t] = p
    probs[bucket_max] = max(0.0, 1.0 - float(probs[:bucket_max].sum()))
    probs /= probs.sum()
    return probs


def _outputs(mu_home: float, mu_away: float, theta: float) -> dict[str, Any]:
    theta = float(np.clip(theta, 0.0, THETA_MAX))
    kmax = _adaptive_kmax(mu_home, mu_away)

    exact = np.zeros((kmax + 1, kmax + 1), dtype=float)
    for h in range(kmax + 1):
        for a in range(kmax + 1):
            exact[h, a] = bivariate_poisson_pmf(h, a, mu_home, mu_away, theta)

    exact_mass = float(exact.sum())
    tail_residual = max(0.0, 1.0 - exact_mass)
    if tail_residual > TAIL_RESIDUAL_LIMIT:
        raise RuntimeError(f"joint tail residual {tail_residual} exceeds {TAIL_RESIDUAL_LIMIT}")

    # 0..6/7+ score buckets. The marginal tails are exact from the frozen marginals.
    matrix = np.zeros((8, 8), dtype=float)
    matrix[:7, :7] = exact[:7, :7]

    ph = poisson_probs_through(mu_home, 6)
    pa = poisson_probs_through(mu_away, 6)
    for h in range(7):
        matrix[h, 7] = max(0.0, float(ph[h]) - float(matrix[h, :7].sum()))
    for a in range(7):
        matrix[7, a] = max(0.0, float(pa[a]) - float(matrix[:7, a].sum()))
    matrix[7, 7] = max(
        0.0,
        1.0 - float(matrix[:7, :].sum()) - float(matrix[7, :7].sum()),
    )
    matrix_mass = float(matrix.sum())
    if abs(matrix_mass - 1.0) > TAIL_RESIDUAL_LIMIT:
        raise RuntimeError(f"bucket matrix conservation failed: {matrix_mass}")
    matrix /= matrix_mass

    # Lower triangle is home-goal row > away-goal column => home win.
    hda = np.asarray(
        [np.tril(exact, -1).sum(), np.trace(exact), np.triu(exact, 1).sum()],
        dtype=float,
    )
    # Any omitted square-tail residual is numerically negligible by construction.
    hda /= hda.sum()

    total = _total_distribution(mu_home, mu_away, theta)
    return {
        "matrix": matrix,
        "hda": hda,
        "total": total,
        "tail_residual": tail_residual,
        "kmax": kmax,
        "theta": theta,
    }


def baseline_outputs(mu_home: float, mu_away: float) -> dict[str, Any]:
    return _outputs(mu_home, mu_away, 0.0)


def candidate_outputs(mu_home: float, mu_away: float, theta: float) -> dict[str, Any]:
    return _outputs(mu_home, mu_away, theta)


def _theta_objective(
    theta: float, history_rows: list[tuple[float, float, int, int]]
) -> float:
    theta = float(np.clip(theta, 0.0, THETA_MAX))
    nll = 0.0
    for mu_home, mu_away, hg, ag in history_rows:
        p = bivariate_poisson_pmf(hg, ag, mu_home, mu_away, theta)
        nll -= math.log(max(p, PROB_FLOOR))
    penalty = THETA_PENALTY * (theta / THETA_MAX) ** 2
    return nll + penalty


def fit_theta(history_rows: list[tuple[float, float, int, int]]) -> float:
    if len(history_rows) < MIN_THETA_HISTORY:
        return 0.0
    lo, hi = 0.0, THETA_MAX
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    c = hi - (hi - lo) / phi
    d = lo + (hi - lo) / phi
    fc = _theta_objective(c, history_rows)
    fd = _theta_objective(d, history_rows)
    for _ in range(72):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - (hi - lo) / phi
            fc = _theta_objective(c, history_rows)
        else:
            lo, c, fc = c, d, fd
            d = lo + (hi - lo) / phi
            fd = _theta_objective(d, history_rows)
    theta = (lo + hi) / 2.0
    # Explicitly include the zero-dependence boundary in the deterministic choice.
    if _theta_objective(0.0, history_rows) <= _theta_objective(theta, history_rows):
        return 0.0
    return float(np.clip(theta, 0.0, THETA_MAX))


def outcome_index(home_goals: int, away_goals: int) -> int:
    return 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2


def multiclass_brier(p: np.ndarray, y: int) -> float:
    one = np.zeros(len(p), dtype=float)
    one[y] = 1.0
    return float(np.sum((p - one) ** 2))


def total_rps(p: np.ndarray, total_goals: int) -> float:
    y = min(total_goals, 7)
    cdf = np.cumsum(p)[:-1]
    truth = np.asarray([1.0 if y <= k else 0.0 for k in range(7)], dtype=float)
    return float(np.mean((cdf - truth) ** 2))


def draw_logloss(pdraw: float, is_draw: bool) -> float:
    p = float(np.clip(pdraw, PROB_FLOOR, 1.0 - PROB_FLOOR))
    return float(-math.log(p if is_draw else 1.0 - p))


def calibration_bins(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, 6)
    out: list[dict[str, Any]] = []
    for i in range(5):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i < 4:
            vals = [r for r in rows if lo <= float(r[key]) < hi]
        else:
            vals = [r for r in rows if lo <= float(r[key]) <= hi]
        out.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(vals),
                "mean_pred": None if not vals else float(np.mean([r[key] for r in vals])),
                "actual_draw_rate": None
                if not vals
                else float(np.mean([1.0 if r["is_draw"] else 0.0 for r in vals])),
            }
        )
    return out


def logistic_calibration(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    if not rows:
        return {"intercept": None, "slope": None, "converged": False}
    p = np.clip(np.asarray([float(r[key]) for r in rows]), 1e-6, 1.0 - 1e-6)
    x = np.log(p / (1.0 - p))
    y = np.asarray([1.0 if r["is_draw"] else 0.0 for r in rows], dtype=float)
    X = np.column_stack([np.ones(len(x)), x])
    beta = np.asarray([math.log((float(y.mean()) + 1e-3) / (1.0 - float(y.mean()) + 1e-3)), 1.0])
    converged = False
    for _ in range(50):
        eta = np.clip(X @ beta, -20.0, 20.0)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.maximum(mu * (1.0 - mu), 1e-6)
        z = eta + (y - mu) / w
        A = X.T @ (w[:, None] * X) + np.eye(2) * 1e-8
        b = X.T @ (w * z)
        try:
            new_beta = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            break
        if float(np.max(np.abs(new_beta - beta))) < 1e-8:
            beta = new_beta
            converged = True
            break
        beta = new_beta
    return {
        "intercept": float(beta[0]),
        "slope": float(beta[1]),
        "converged": converged,
    }


def make_folds(test_dates: list[str], match_count_by_date: dict[str, int]) -> dict[str, int]:
    total = sum(match_count_by_date[d] for d in test_dates)
    targets = [total / 3.0, 2.0 * total / 3.0]
    fold = 0
    seen = 0
    out: dict[str, int] = {}
    for date in test_dates:
        if fold < 2 and seen >= targets[fold]:
            fold += 1
        out[date] = fold
        seen += match_count_by_date[date]
    return out


def bootstrap_primary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_date[row["match_date"]].append(float(row["score_delta"]))
    dates = sorted(by_date)
    rng = np.random.default_rng(RANDOM_SEED + 2202)
    samples = np.empty(BOOTSTRAP_REPS, dtype=float)
    for b in range(BOOTSTRAP_REPS):
        chosen = rng.choice(dates, size=len(dates), replace=True)
        vals: list[float] = []
        for date in chosen:
            vals.extend(by_date[str(date)])
        samples[b] = float(np.mean(vals))
    return {
        "clusters": len(dates),
        "replicates": BOOTSTRAP_REPS,
        "observed_mean_delta": float(np.mean([r["score_delta"] for r in rows])),
        "p05": float(np.quantile(samples, 0.05)),
        "median": float(np.quantile(samples, 0.5)),
        "p95": float(np.quantile(samples, 0.95)),
        "probability_candidate_better": float(np.mean(samples < 0.0)),
    }
