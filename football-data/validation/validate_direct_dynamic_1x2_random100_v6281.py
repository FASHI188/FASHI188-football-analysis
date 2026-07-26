#!/usr/bin/env python3
"""V6.28.1 direct multinomial W/D/L head from dynamic football features.

This is the first direct 1X2 head in the V6.28 rebuild. It does NOT derive W/D/L probabilities by
summing an exact-score matrix and it uses NO bookmaker 1X2 probability as an input.

Per-match pre-kickoff features
------------------------------
- logit(dynamic home allocation share): attack/defence strength difference after continuity-aware
  prior-season borrowing;
- log(dynamic expected total): scoring environment, useful for draw propensity;
- home minus away borrowing weight: asymmetric continuity signal;
- mean borrowing weight: overall continuity / prior commensurability.

The dynamic feature generator is the existing V4.7 research module, using roster continuity,
manager continuity, promotion/relegation and structural-break evidence strictly before the target.

Training / test contract
------------------------
- candidate borrowing rule is selected on 2024/25 only, exactly as V6.28.0;
- direct multinomial coefficients are trained per competition on 2022/23, 2023/24 and 2024/25;
- fixed L2 lambda=1/n, deterministic gradient descent + Armijo backtracking; no hyperparameter grid;
- 2025/26 is untouched test;
- same deterministic target ordering and seed 628100 as V6.28.0, first 100 eligible rows.

The head is assessed only on its own 1X2 module gate here. Any later final-matrix reconciliation must
pass a separate system gate. Random100 cannot promote CURRENT.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_architecture_order_v6190 as met  # noqa: E402
import validate_dynamic_strength_1x2_random100_v6280 as v6280  # noqa: E402
from dynamic_strength_oof_screen_v470 import (  # noqa: E402
    MODEL_ROOT,
    build_season_indexes,
    challenger_matrix,
    load_domain_data,
    team_features,
    to_match,
)
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    build_score_matrix,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, derive_score_marginals, load_json  # noqa: E402

OUT = ROOT / "manifests" / "v6_direct_dynamic_1x2_random100_v6281_status.json"
TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
SEED = 628100
TARGET = 100
CACHE = Path("/tmp/football-v6281-dynamic-cache")
EPS = 1e-15


def sigmoid_clip_logit(p: float) -> float:
    p = min(1.0 - 1e-8, max(1e-8, float(p)))
    return math.log(p / (1.0 - p))


def one_vec(matrix: list[dict[str, Any]]) -> list[float]:
    one = derive_score_marginals(matrix)["1x2"]
    return [float(one[k]) for k in ("home", "draw", "away")]


def actual_result(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def raw_features(audit: dict[str, Any]) -> list[float]:
    hw = float(audit.get("home_borrowing_weight") or 0.0)
    aw = float(audit.get("away_borrowing_weight") or 0.0)
    share = float(audit["home_share"])
    total = max(1e-8, float(audit["mu_total"]))
    return [sigmoid_clip_logit(share), math.log(total), hw - aw, 0.5 * (hw + aw)]


def softmax2(z_h: float, z_d: float) -> list[float]:
    m = max(z_h, z_d, 0.0)
    h = math.exp(z_h - m); d = math.exp(z_d - m); a = math.exp(-m)
    s = h + d + a
    return [h / s, d / s, a / s]


def standardize(rows: list[dict[str, Any]]):
    xs = [r["x"] for r in rows]
    means = [sum(x[j] for x in xs) / len(xs) for j in range(4)]
    scales = []
    for j in range(4):
        var = sum((x[j] - means[j]) ** 2 for x in xs) / len(xs)
        scales.append(max(1e-6, math.sqrt(var)))
    prepared = []
    for r in rows:
        x = [1.0] + [(r["x"][j] - means[j]) / scales[j] for j in range(4)]
        prepared.append((x, int(r["actual"])))
    return prepared, means, scales


def objective_gradient(beta: list[float], rows: list[tuple[list[float], int]], ridge: float):
    # 5 coefficients for home logit, 5 for draw logit; away is reference.
    obj = 0.0
    g = [0.0] * 10
    n = len(rows)
    for x, y in rows:
        zh = sum(beta[j] * x[j] for j in range(5))
        zd = sum(beta[5 + j] * x[j] for j in range(5))
        q = softmax2(zh, zd)
        obj -= math.log(max(EPS, q[y]))
        eh = q[0] - (1.0 if y == 0 else 0.0)
        ed = q[1] - (1.0 if y == 1 else 0.0)
        for j in range(5):
            g[j] += eh * x[j]
            g[5 + j] += ed * x[j]
    obj /= n
    g = [v / n for v in g]
    # Penalize slopes only; outcome base rates/intercepts stay free.
    for i in list(range(1, 5)) + list(range(6, 10)):
        obj += 0.5 * ridge * beta[i] * beta[i]
        g[i] += ridge * beta[i]
    return obj, g


def fit_model(rows: list[dict[str, Any]]):
    prepared, means, scales = standardize(rows)
    n = len(prepared)
    ridge = 1.0 / max(1, n)
    beta = [0.0] * 10
    converged = False
    iterations = 0
    for it in range(1, 501):
        iterations = it
        obj, g = objective_gradient(beta, prepared, ridge)
        g2 = sum(v * v for v in g)
        if math.sqrt(g2) <= 1e-8:
            converged = True
            break
        step = 1.0
        accepted = False
        for _ in range(35):
            cand = [b - step * gg for b, gg in zip(beta, g)]
            cobj, _ = objective_gradient(cand, prepared, ridge)
            if cobj <= obj - 1e-4 * step * g2:
                beta = cand
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
    final_obj, final_g = objective_gradient(beta, prepared, ridge)
    gn = math.sqrt(sum(v * v for v in final_g))
    converged = converged or gn <= 1e-7
    return {"beta": beta, "means": means, "scales": scales}, {
        "training_rows": n,
        "ridge_lambda": ridge,
        "iterations": iterations,
        "converged": converged,
        "objective": final_obj,
        "gradient_norm": gn,
    }


def predict(model: dict[str, Any], xraw: list[float]) -> list[float]:
    x = [1.0] + [(xraw[j] - model["means"][j]) / model["scales"][j] for j in range(4)]
    b = model["beta"]
    return softmax2(sum(b[j] * x[j] for j in range(5)), sum(b[5 + j] * x[j] for j in range(5)))


def season_feature_rows(cid: str, season: str, candidate: dict[str, Any], data: dict[str, Any], indexes: dict[str, Any], params: dict[str, float]) -> list[dict[str, Any]]:
    config = load_config()
    games = indexes["by_season"].get(season, [])
    previous = indexes["previous"].get(season)
    if not games or not previous or previous not in indexes["by_season"]:
        return []
    prior_rows = [to_match(g, cid) for g in indexes["by_season"][previous]]
    prior_cutoff = max(g["date"] for g in indexes["by_season"][previous]) + timedelta(days=1)
    try:
        prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
    except PlatformError:
        prior_state = None
    out = []
    for target in games:
        history_games = [g for g in games if g["date"] < target["date"]]
        history = [to_match(g, cid) for g in history_games]
        try:
            current_state = fit_current_season_state(history, target["date"], params, config)
            base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
            baseline = build_score_matrix(
                float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"],
                params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params),
            )
        except PlatformError:
            continue
        hf = team_features(target["home_id"], season, target["date"], indexes, data["transfers"])
        af = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
        if not hf.get("feature_complete") or not af.get("feature_complete"):
            continue
        try:
            dynamic_matrix, audit = challenger_matrix(current_state, prior_state, target["home_id"], target["away_id"], hf, af, candidate, params, config)
        except PlatformError:
            continue
        out.append({
            "match_key": f"{cid}:{season}:{target['game_id']}",
            "competition_id": cid,
            "date": target["date"].date().isoformat(),
            "actual": actual_result(int(target["home_goals"]), int(target["away_goals"])),
            "actual_score": [int(target["home_goals"]), int(target["away_goals"])],
            "x": raw_features(audit),
            "baseline_1x2": one_vec(baseline),
            "dynamic_1x2": one_vec(dynamic_matrix),
        })
    return out


def rps3(p: list[float], actual: int) -> float:
    return ((p[0] - (1.0 if actual == 0 else 0.0)) ** 2 + (p[0] + p[1] - (1.0 if actual <= 1 else 0.0)) ** 2) / 2.0


def scored(row: dict[str, Any], prefix: str, p: list[float]):
    y = int(row["actual"])
    row[f"{prefix}_top1"] = int(max(range(3), key=lambda i: p[i]) == y)
    row[f"{prefix}_brier"] = met.brier3(p, y)
    row[f"{prefix}_logloss"] = met.logloss3(p, y)
    row[f"{prefix}_rps"] = rps3(p, y)


def avg(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(r[key]) for r in rows) / len(rows)


def main() -> int:
    config = load_config()
    all_test = []
    audits = {}
    failures = {}
    for cid in v6280.COMPS:
        try:
            data = load_domain_data(cid, CACHE)
            indexes = build_season_indexes(data)
            artifact = load_json(MODEL_ROOT / cid / "model.json")
            test_params_raw = artifact["point_in_time_parameters"].get(TEST_SEASON)
            if not test_params_raw:
                raise PlatformError("missing 2025/26 parameter set")
            test_params = _merge_parameters(config, test_params_raw)
            selection = v6280.choose_candidate(cid, test_params, data, indexes)
            candidate = selection["selected"]
            training = []
            training_counts = {}
            for season in TRAIN_SEASONS:
                raw = artifact["point_in_time_parameters"].get(season)
                if not raw:
                    continue
                params = _merge_parameters(config, raw)
                rows = season_feature_rows(cid, season, candidate, data, indexes, params)
                training_counts[season] = len(rows)
                training.extend(rows)
            if len(training) < 300:
                raise PlatformError(f"insufficient direct-head training rows: {len(training)}")
            model, fit_audit = fit_model(training)
            test_rows = season_feature_rows(cid, TEST_SEASON, candidate, data, indexes, test_params)
            for r in test_rows:
                dp = predict(model, r["x"])
                r["direct_1x2"] = dp
                scored(r, "baseline", r["baseline_1x2"])
                scored(r, "dynamic", r["dynamic_1x2"])
                scored(r, "direct", dp)
            audits[cid] = {
                "selected_dynamic_candidate": candidate["id"],
                "training_counts": training_counts,
                "training_total": len(training),
                "fit": fit_audit,
                "test_eligible": len(test_rows),
            }
            all_test.extend(test_rows)
        except Exception as exc:
            failures[cid] = str(exc)

    ordered = sorted(all_test, key=lambda r: (r["competition_id"], r["date"], r["match_key"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if not sample:
        raise RuntimeError("no direct-head test sample")
    summary = {"count": len(sample)}
    for prefix in ("baseline", "dynamic", "direct"):
        for metric in ("top1", "brier", "logloss", "rps"):
            summary[f"{prefix}_{metric}"] = avg(sample, f"{prefix}_{metric}")
    summary["dynamic_vs_baseline_top1_pp"] = (summary["dynamic_top1"] - summary["baseline_top1"]) * 100.0
    summary["direct_vs_baseline_top1_pp"] = (summary["direct_top1"] - summary["baseline_top1"]) * 100.0
    summary["direct_vs_dynamic_top1_pp"] = (summary["direct_top1"] - summary["dynamic_top1"]) * 100.0
    by_comp = {}
    for cid in v6280.COMPS:
        rs = [r for r in sample if r["competition_id"] == cid]
        if rs:
            by_comp[cid] = {
                "count": len(rs),
                "baseline_top1": avg(rs, "baseline_top1"),
                "dynamic_top1": avg(rs, "dynamic_top1"),
                "direct_top1": avg(rs, "direct_top1"),
                "direct_rps": avg(rs, "direct_rps"),
            }
    checks = {
        "sample_100": len(sample) == TARGET,
        "direct_top1_at_least_baseline_plus_5pp": summary["direct_top1"] >= summary["baseline_top1"] + 0.05 - 1e-12,
        "direct_top1_not_below_dynamic": summary["direct_top1"] >= summary["dynamic_top1"] - 1e-12,
        "direct_brier_not_worse_than_dynamic": summary["direct_brier"] <= summary["dynamic_brier"] + 1e-12,
        "direct_logloss_not_worse_than_dynamic": summary["direct_logloss"] <= summary["dynamic_logloss"] + 1e-12,
        "direct_rps_not_worse_than_dynamic": summary["direct_rps"] <= summary["dynamic_rps"] + 1e-12,
        "all_fits_converged": all(a["fit"]["converged"] for a in audits.values()),
    }
    report = {
        "schema_version": "V6.28.1-direct-dynamic-multinomial-1x2-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(sample) == TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_DIRECT_1X2_HEAD_CHRONOLOGICAL_TRAIN_2022_25_TEST_2025_26_RANDOM100",
        "train_seasons": list(TRAIN_SEASONS),
        "test_season": TEST_SEASON,
        "seed": SEED,
        "target": TARGET,
        "eligible_population": len(all_test),
        "competition_failures": failures,
        "fit_audits": audits,
        "summary": summary,
        "by_competition_in_sample": by_comp,
        "module_gate": {
            "checks": checks,
            "passed": all(checks.values()),
            "on_pass": "RUN_SEPARATE_FINAL_MATRIX_SYSTEM_GATE",
            "on_failure": "REJECT_THIS_DIRECT_FEATURE_SET; DO_NOT_TUNE_ON_TEST_RANDOM100",
        },
        "sample": sample,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "bookmaker_1x2_input_used": False,
            "exact_score_matrix_used_as_prediction_target": False,
            "dynamic_matrix_used_only_to_generate_pre_match_strength_features_and_comparator": True,
            "test_outcomes_used_for_training": False,
            "hyperparameter_grid_on_test": False,
            "random100_is_diagnostic_only": True,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failures": failures, "summary": summary, "module_gate": report["module_gate"], "fit": {k:v["fit"] for k,v in audits.items()}}, ensure_ascii=False, indent=2))
    return 0 if len(sample) == TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
