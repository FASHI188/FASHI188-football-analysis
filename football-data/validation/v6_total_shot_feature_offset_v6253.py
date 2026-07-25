#!/usr/bin/env python3
"""V6.25.3 strict-PIT shot-feature total-goal offset challenger.

Research only; formal_weight=0.

The processed CSV files for several competitions retain HS/AS/HST/AST even
though the formal MatchRow parser ignores them. This challenger uses only
historical match statistics that were settled before the prediction date.

Model:
- Baseline direct total mean mu_0 and NB dispersion k come from the frozen core.
- Pre-match rolling shot/SOT features are built from the current season only.
- A ridge Poisson regression with offset log(mu_0) is fitted on strictly prior
  seasons in the same competition.
- Correction strength alpha is selected only by nested prior-season OOS total
  RPS from a fixed grid including alpha=0 (exact baseline fallback).
- Candidate mu = mu_0 * exp(alpha * eta_shots), with a fixed safety cap on eta.
- The new NB total marginal is embedded into the baseline joint matrix while
  preserving P(score | total).

No target-season outcome or same-day match statistic enters a prediction.
Competitions without adequate historical shot coverage fall back to alpha=0.
"""
from __future__ import annotations

import csv
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    build_score_matrix,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
    negative_binomial_pmf,
)
from platform_core import (  # noqa: E402
    PlatformError,
    canonical_team_name,
    load_aliases,
    load_json,
    normalize_team_token,
    parse_match_date,
    read_processed_matches,
    score_matrix_rows,
)
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_shot_feature_offset_v6253_status.json"
SEED = 20260725
SAMPLE_N = 100
RIDGE_LAMBDA = 10.0
ALPHAS = (0.0, 0.25, 0.50, 0.75, 1.0)
ETA_CAP = 0.60
MIN_SHOT_COVERAGE = 0.90
PRIOR_MATCHES = 4.0
EPS = 1e-9


@dataclass(frozen=True)
class StatRow:
    season: str
    date: datetime
    home_team: str
    away_team: str
    hs: float
    as_: float
    hst: float
    ast: float


def _team_key(name: str) -> str:
    return normalize_team_token(name)


def _num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0.0 else None


def _read_stat_rows(cid: str) -> tuple[list[StatRow], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    aliases = load_aliases()
    rows: list[StatRow] = []
    eligible = 0
    complete = 0
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("FTHG") or not raw.get("FTAG"):
                    continue
                eligible += 1
                values = [_num(raw.get(k)) for k in ("HS", "AS", "HST", "AST")]
                if any(v is None for v in values):
                    continue
                season = str(raw.get("season") or raw.get("Season") or "")
                try:
                    date = parse_match_date(str(raw.get("Date") or ""), season)
                except Exception:
                    continue
                hs, away_shots, hst, away_sot = [float(v) for v in values]  # type: ignore[arg-type]
                rows.append(StatRow(
                    season=season,
                    date=date,
                    home_team=canonical_team_name(cid, str(raw["HomeTeam"]), aliases),
                    away_team=canonical_team_name(cid, str(raw["AwayTeam"]), aliases),
                    hs=hs,
                    as_=away_shots,
                    hst=hst,
                    ast=away_sot,
                ))
                complete += 1
    rows.sort(key=lambda r: (r.date, r.home_team, r.away_team))
    return rows, {
        "eligible_rows": eligible,
        "complete_shot_rows": complete,
        "coverage": complete / eligible if eligible else 0.0,
    }


def _stat_history(rows: list[StatRow], season: str, cutoff: datetime) -> list[StatRow]:
    return [r for r in rows if r.season == season and r.date.date() < cutoff.date()]


def _team_rates(history: list[StatRow], team: str, league_shots: float, league_sot: float) -> dict[str, float]:
    key = _team_key(team)
    n = 0.0
    sf = sa = sotf = sota = 0.0
    for r in history:
        if _team_key(r.home_team) == key:
            n += 1.0; sf += r.hs; sa += r.as_; sotf += r.hst; sota += r.ast
        elif _team_key(r.away_team) == key:
            n += 1.0; sf += r.as_; sa += r.hs; sotf += r.ast; sota += r.hst
    return {
        "n": n,
        "sf": (sf + league_shots * PRIOR_MATCHES) / max(EPS, n + PRIOR_MATCHES),
        "sa": (sa + league_shots * PRIOR_MATCHES) / max(EPS, n + PRIOR_MATCHES),
        "sotf": (sotf + league_sot * PRIOR_MATCHES) / max(EPS, n + PRIOR_MATCHES),
        "sota": (sota + league_sot * PRIOR_MATCHES) / max(EPS, n + PRIOR_MATCHES),
    }


def _shot_features(history: list[StatRow], home: str, away: str) -> list[float] | None:
    if len(history) < 20:
        return None
    league_shots = sum(r.hs + r.as_ for r in history) / max(EPS, 2.0 * len(history))
    league_sot = sum(r.hst + r.ast for r in history) / max(EPS, 2.0 * len(history))
    if league_shots <= 0.0 or league_sot <= 0.0:
        return None
    h = _team_rates(history, home, league_shots, league_sot)
    a = _team_rates(history, away, league_shots, league_sot)
    if h["n"] < 2 or a["n"] < 2:
        return None
    home_shot_chance = 0.5 * (h["sf"] + a["sa"])
    away_shot_chance = 0.5 * (a["sf"] + h["sa"])
    home_sot_chance = 0.5 * (h["sotf"] + a["sota"])
    away_sot_chance = 0.5 * (a["sotf"] + h["sota"])
    league_accuracy = league_sot / max(EPS, league_shots)
    return [
        math.log(max(EPS, home_shot_chance / league_shots)),
        math.log(max(EPS, away_shot_chance / league_shots)),
        math.log(max(EPS, home_sot_chance / league_sot)),
        math.log(max(EPS, away_sot_chance / league_sot)),
        math.log(max(EPS, (home_shot_chance + away_shot_chance) / (2.0 * league_shots))),
        math.log(max(EPS, (home_sot_chance + away_sot_chance) / (2.0 * league_sot))),
        math.log(max(EPS, (home_sot_chance / max(EPS, home_shot_chance)) / league_accuracy)),
        math.log(max(EPS, (away_sot_chance / max(EPS, away_shot_chance)) / league_accuracy)),
    ]


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _nb_bucket_probs(mu: float, k: float) -> dict[str, float]:
    out = {key: 0.0 for key in TOTAL_BUCKETS}
    exact = 0.0
    for total in range(7):
        p = negative_binomial_pmf(total, max(EPS, mu), max(EPS, k))
        out[str(total)] = p
        exact += p
    out["7+"] = max(0.0, 1.0 - exact)
    norm = sum(out.values())
    return {key: value / max(EPS, norm) for key, value in out.items()}


def _reweight_matrix(matrix: list[dict[str, Any]], mu: float, k: float) -> list[dict[str, Any]]:
    target = _nb_bucket_probs(mu, k)
    base = {key: 0.0 for key in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        base[_bucket(int(h + a))] += float(p)
    factors = {key: target[key] / max(EPS, base[key]) for key in TOTAL_BUCKETS}
    output = []
    mass = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * factors[_bucket(int(h + a))]
        output.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        mass += value
    for cell in output:
        cell["probability"] = float(cell["probability"]) / max(EPS, mass)
    return output


def _build_rows(cid: str, season: str, params: dict[str, float], config: dict[str, Any], stats: list[StatRow]) -> list[dict[str, Any]]:
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    rows: list[dict[str, Any]] = []
    for match in target:
        try:
            hist_season, history = current_season_history(all_matches, match.date, season)
            if hist_season != season:
                continue
            state = fit_current_season_state(history, match.date, params, config)
            means = expected_goals(state, match.home_team, match.away_team, params, config)
            factors = low_score_factors(state, params)
            matrix = build_score_matrix(
                float(means["mu_home"]), float(means["mu_away"]), float(state["nb_dispersion_k"]),
                float(params["beta_binomial_concentration"]), int(config["max_total_goals_exact"]), factors,
            )
            feature = _shot_features(_stat_history(stats, season, match.date), match.home_team, match.away_team)
            if feature is None:
                continue
            rows.append({
                "competition_id": cid,
                "season": season,
                "date": match.date.isoformat(),
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_goals": int(match.home_goals),
                "away_goals": int(match.away_goals),
                "feature": feature,
                "base_mu": float(means["mu_total"]),
                "k": float(state["nb_dispersion_k"]),
                "matrix": matrix,
            })
        except PlatformError:
            continue
    return rows


def _standardize(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    dim = len(rows[0]["feature"])
    means = []
    sds = []
    for j in range(dim):
        xs = [float(r["feature"][j]) for r in rows]
        means.append(sum(xs) / len(xs))
        sds.append(max(1e-6, statistics.pstdev(xs) if len(xs) > 1 else 1.0))
    return means, sds


def _design(feature: list[float], means: list[float], sds: list[float]) -> list[float]:
    return [1.0] + [(float(x) - means[i]) / sds[i] for i, x in enumerate(feature)]


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [list(a[i]) + [float(b[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-10:
            aug[pivot][col] += 1e-6
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0.0:
                continue
            for j in range(col, n + 1):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _fit_poisson_offset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    means, sds = _standardize(rows)
    xrows = [_design(r["feature"], means, sds) for r in rows]
    p = len(xrows[0])
    beta = [0.0] * p
    for _ in range(30):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for row, x in zip(rows, xrows):
            eta = max(-ETA_CAP, min(ETA_CAP, sum(beta[j] * x[j] for j in range(p))))
            mu = max(EPS, float(row["base_mu"]) * math.exp(eta))
            y = float(int(row["home_goals"]) + int(row["away_goals"]))
            residual = mu - y
            for j in range(p):
                grad[j] += residual * x[j]
                for k in range(p):
                    hess[j][k] += mu * x[j] * x[k]
        for j in range(1, p):
            grad[j] += RIDGE_LAMBDA * beta[j]
            hess[j][j] += RIDGE_LAMBDA
        step = _solve_linear(hess, grad)
        beta = [beta[j] - step[j] for j in range(p)]
        if max(abs(v) for v in step) < 1e-6:
            break
    return {"means": means, "sds": sds, "beta": beta}


def _predict_eta(model: dict[str, Any], feature: list[float]) -> float:
    x = _design(feature, model["means"], model["sds"])
    return max(-ETA_CAP, min(ETA_CAP, sum(float(b) * x[i] for i, b in enumerate(model["beta"]))))


def _candidate_rows(rows: list[dict[str, Any]], model: dict[str, Any] | None, alpha: float) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if model is None or alpha <= 0.0:
            candidate = row["matrix"]
            eta = 0.0
        else:
            eta = _predict_eta(model, row["feature"])
            mu = float(row["base_mu"]) * math.exp(float(alpha) * eta)
            candidate = _reweight_matrix(row["matrix"], mu, float(row["k"]))
        out.append({**row, "baseline_matrix": row["matrix"], "candidate_matrix": candidate, "eta": eta, "alpha": alpha})
    return out


def _load_season(cid: str, season: str, report: dict[str, Any], config: dict[str, Any], stats: list[StatRow]) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        return []
    return _build_rows(cid, season, _merge_parameters(config, selected), config, stats)


def _select_alpha(cid: str, prior: list[str], report: dict[str, Any], config: dict[str, Any], stats: list[StatRow]) -> tuple[float, dict[str, Any]]:
    scores = {a: {"sum": 0.0, "count": 0, "folds": []} for a in ALPHAS}
    for idx in range(1, len(prior)):
        training = []
        for season in prior[:idx]:
            training.extend(_load_season(cid, season, report, config, stats))
        validation = _load_season(cid, prior[idx], report, config, stats)
        if len(training) < 100 or not validation:
            continue
        model = _fit_poisson_offset(training)
        for alpha in ALPHAS:
            cand = _candidate_rows(validation, model, alpha)
            metric = _score(cand, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            scores[alpha]["sum"] += rps * n
            scores[alpha]["count"] += n
            scores[alpha]["folds"].append({"validation_season": prior[idx], "count": n, "mean_rps": rps})
    eligible = []
    for alpha in ALPHAS:
        n = int(scores[alpha]["count"])
        mean = scores[alpha]["sum"] / n if n else None
        scores[alpha]["mean_rps"] = mean
        if mean is not None:
            eligible.append((float(mean), float(alpha)))
    if not eligible:
        return 0.0, {"fallback": "insufficient_nested_shot_folds", "alpha_scores": scores}
    best, alpha = min(eligible, key=lambda item: (item[0], item[1]))
    return alpha, {"selected_alpha": alpha, "selected_mean_rps": best, "alpha_scores": scores}


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    stats, coverage = _read_stat_rows(cid)
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError("invalid target parameters")
    target_params = _merge_parameters(config, target_selected)
    prior = [str(s) for s in (target_fold.get("prior_seasons") or [])]
    if float(coverage["coverage"]) < MIN_SHOT_COVERAGE:
        # Baseline fallback with the normal target prediction rows is not created here;
        # caller will record the domain as unavailable for this challenger.
        return {"competition_id": cid, "applied": False, "coverage": coverage, "reason": "shot_coverage_below_gate"}, []
    alpha, selection = _select_alpha(cid, prior, report, config, stats)
    training = []
    for season in prior:
        training.extend(_load_season(cid, season, report, config, stats))
    target = _build_rows(cid, target_season, target_params, config, stats)
    model = _fit_poisson_offset(training) if len(training) >= 100 and alpha > 0.0 else None
    rows = _candidate_rows(target, model, alpha)
    base = _score(rows, "baseline_matrix")
    cand = _score(rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "applied": True,
        "coverage": coverage,
        "target_season": target_season,
        "training_prediction_count": len(training),
        "target_prediction_count": len(rows),
        "selected_alpha": alpha,
        "selection": selection,
        "model_beta": model["beta"] if model else None,
        "baseline": base,
        "candidate": cand,
        "delta": _delta(base, cand),
        "baseline_top1_bucket_counts": _top1_counts(rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(rows, "candidate_matrix"),
    }, rows


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports = {}
    pool = []
    failures = {}
    alpha_counts = Counter()
    for cid in competitions:
        try:
            result, rows = _domain(cid)
            reports[cid] = result
            if result.get("applied"):
                pool.extend(rows)
                alpha_counts[str(result.get("selected_alpha"))] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not pool:
        raise PlatformError("no shot-feature target predictions")
    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(pool))
    sampled = random.Random(SEED).sample(pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")
    payload = {
        "schema_version": "V6.25.3-shot-feature-poisson-offset-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_SHOT_FEATURE_FORMAL_WEIGHT_0",
        "applied_domain_count": sum(1 for r in reports.values() if r.get("applied")),
        "eligible_target_pool_count": len(pool),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "full_pool": {
            "baseline": full_base, "candidate": full_cand, "delta": _delta(full_base, full_cand),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED, "count": sample_n,
            "baseline": sample_base, "candidate": sample_cand, "delta": _delta(sample_base, sample_cand),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "shot_stats_prior_matches_only": True,
            "same_day_stats_excluded": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_selected_nested_prior_season_oos": True,
            "alpha_zero_exact_baseline_fallback": True,
            "historical_market_odds_used": False,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "applied_domain_count", "eligible_target_pool_count", "selected_alpha_domain_counts", "full_pool", "random100", "failures"
    )}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
