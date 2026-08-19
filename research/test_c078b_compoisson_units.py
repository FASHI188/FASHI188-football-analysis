#!/usr/bin/env python3
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
from scipy.stats import poisson

import c078a_full_support_directt as c078a
import c078b_full_support_compoisson as c078b


def finite_diff(fun, theta, eps=1e-6):
    out = np.zeros_like(theta, dtype=float)
    for j in range(len(theta)):
        a = theta.copy(); b = theta.copy()
        a[j] += eps; b[j] -= eps
        out[j] = (fun(a)[0] - fun(b)[0]) / (2 * eps)
    return out


def main() -> int:
    rng = np.random.default_rng(78100)
    X = np.column_stack([np.ones(80), rng.normal(size=(80, 4))])
    y = rng.poisson(2.5, size=80).astype(int)
    beta = np.array([math.log(2.4), 0.05, -0.04, 0.03, 0.02], dtype=float)
    theta = np.concatenate([beta, np.array([math.log(1.3)])])

    _, grad = c078b.comp_objective(theta, X, y, chunk_size=17)
    num = finite_diff(lambda th: c078b.comp_objective(th, X, y, chunk_size=17), theta)
    err = float(np.max(np.abs(grad - num)))
    assert err <= 1e-5, (err, grad, num)

    # ν=1 is exactly the Poisson family. On the 0..100 numerical grid, omitted mass is negligible here.
    X0 = np.column_stack([np.ones(5), np.zeros((5, 4))])
    lam = 2.7
    beta0 = np.array([math.log(lam), 0, 0, 0, 0], dtype=float)
    th_pois = np.concatenate([beta0, np.array([0.0])])
    comp = c078b.comp_distribution_arrays(th_pois, X0)
    ppois = poisson.pmf(np.arange(c078a.MAX_T + 1)[None, :], lam)
    assert float(np.max(np.abs(comp["pmf"] - ppois))) <= 1e-10
    assert c078b.normalization_bound(th_pois, X0) <= 1e-12

    # Fixed λ: ν>1 must make this synthetic count law thinner / under-dispersed.
    th_thin = np.concatenate([beta0, np.array([math.log(1.5)])])
    thin = c078b.comp_distribution_arrays(th_thin, X0)
    grid = np.arange(c078a.MAX_T + 1, dtype=float)
    mean_p = float((comp["pmf"][0] * grid).sum())
    var_p = float((comp["pmf"][0] * (grid - mean_p) ** 2).sum())
    mean_t = float((thin["pmf"][0] * grid).sum())
    var_t = float((thin["pmf"][0] * (grid - mean_t) ** 2).sum())
    assert var_t < var_p
    assert float(thin["sf6"][0]) < float(comp["sf6"][0])
    assert float(thin["sf7"][0]) < float(comp["sf7"][0])

    pres = c078a.fit_poisson(X, y)
    assert pres.success, pres.message
    cres = c078b.fit_comp(X, y, pres.x)
    assert cres.success, cres.message
    nu = math.exp(float(cres.x[-1]))
    assert 1.0 <= nu <= 5.0
    assert c078b.normalization_bound(cres.x, X) <= 1e-12
    cd = c078b.comp_distribution_arrays(cres.x, X)
    assert float(np.max(cd["conservation"])) <= 1e-10
    assert np.isfinite(cd["pmf"]).all()
    assert np.all(cd["pmf"] >= 0)

    # Same-date PIT remains inherited exactly from C078-A.
    raw = pd.DataFrame([
        {"date": pd.Timestamp("2020-01-01"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "B", "source_file": "x", "home_key": "L|A", "away_key": "L|B", "FTHG": 2, "FTAG": 1, "T_exact": 3, "movement_logit": 0.1},
        {"date": pd.Timestamp("2020-01-01"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "C", "source_file": "x", "home_key": "L|A", "away_key": "L|C", "FTHG": 1, "FTAG": 1, "T_exact": 2, "movement_logit": -0.1},
        {"date": pd.Timestamp("2020-01-02"), "Season": "2019-2020", "season_start": 2019, "league_key": "L", "HomeTeam": "A", "AwayTeam": "B", "source_file": "x", "home_key": "L|A", "away_key": "L|B", "FTHG": 0, "FTAG": 0, "T_exact": 0, "movement_logit": 0.0},
    ])
    ft = c078a.build_history_features(raw)
    day1 = ft[ft.date == pd.Timestamp("2020-01-01")].reset_index(drop=True)
    assert np.isnan(day1.loc[0, "home_goals_for_mean"])
    assert np.isnan(day1.loc[1, "home_goals_for_mean"])
    day2 = ft[ft.date == pd.Timestamp("2020-01-02")].iloc[0]
    assert abs(float(day2.home_goals_for_mean) - 1.5) <= 1e-12
    assert abs(float(day2.competition_total_mean) - 2.5) <= 1e-12

    result = {
        "schema_version": "C078B_COMPOISSON_UNIT_TESTS_V1",
        "status": "PASS",
        "max_gradient_abs_error": err,
        "fitted_synthetic_nu": nu,
        "tests": {
            "analytic_gradient_beta_and_nu": "PASS",
            "nu1_reproduces_poisson": "PASS",
            "underdispersion_variance_direction": "PASS",
            "thin_tail_survival_direction": "PASS",
            "normalization_tail_bound": "PASS",
            "probability_conservation": "PASS",
            "optimizer": "PASS",
            "same_date_predict_before_update": "PASS"
        },
        "C077B_labels_read": False,
        "sealed_pool_opened": False
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
