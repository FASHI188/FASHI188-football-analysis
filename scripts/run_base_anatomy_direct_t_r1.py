#!/usr/bin/env python3
"""Stage-3 base anatomy: add Direct-T to the Stage-2 cross-season Poisson bottom.

Isolation:
- Stage 2: cross-season equal-weight team state -> independent Poisson.
- Stage 3: same cross-season team state and same home-share signal, but replace the implied
  Poisson total marginal with the formal direct venue-total signal and Negative-Binomial total.
- Conditional H|T allocation is the *Poisson-implied Binomial* split, so Beta-Binomial /
  conditional-GD is not introduced early.
- No multi-OU, low-score residual, OOF matrix calibration, or market input.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FOOTBALL_DATA = REPO_ROOT / "football-data"
ENGINE_ROOT = FOOTBALL_DATA / "engine"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (ENGINE_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from football_v460_engine import (  # noqa: E402
    _shrunk_rate,
    current_season_history,
    fit_current_season_state,
    load_config,
    negative_binomial_pmf,
)
from platform_core import PlatformError, load_json, normalize_team_token, parse_iso_datetime, read_processed_matches  # noqa: E402
from run_base_anatomy_cross_season_r1 import cross_season_score_signals, paired_metric_delta  # noqa: E402
from run_base_anatomy_poisson_r1 import (  # noqa: E402
    BENCHMARK_DEFAULT,
    OUTCOMES,
    calibration,
    independent_poisson_1x2,
    log_loss,
    point_in_time_parameters,
    summarize,
)

OUTPUT_DEFAULT = FOOTBALL_DATA / "research" / "base_anatomy_20260817"
EPS = 1e-15


def binomial_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    p = min(1.0 - 1e-12, max(1e-12, float(p)))
    return math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))


def direct_t_parameters(
    cross_signals: dict[str, Any],
    current_state: dict[str, Any],
    params: dict[str, float],
) -> dict[str, float]:
    """Formal Direct-T formula, with Stage-2 cross-season team state as its team-state source."""
    home = cross_signals["home_state"]["mixed"]
    away = cross_signals["away_state"]["mixed"]
    league_total = float(current_state["mean_total_goals"])
    prior = float(params["team_prior_matches"])
    home_total_rate = _shrunk_rate(
        float(home["home_gf"]) + float(home["home_ga"]), float(home["home_matches"]), league_total, prior
    )
    away_total_rate = _shrunk_rate(
        float(away["away_gf"]) + float(away["away_ga"]), float(away["away_matches"]), league_total, prior
    )
    pair_total_rate = math.sqrt(max(1e-12, home_total_rate) * max(1e-12, away_total_rate))
    signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
    mu_total = math.exp(
        (1.0 - signal_weight) * math.log(max(1e-12, league_total))
        + signal_weight * math.log(max(1e-12, pair_total_rate))
    )
    minimum_mu = float(params["minimum_goal_mean"])
    maximum_mu = float(params["maximum_goal_mean"])
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
    stage2_sum = float(cross_signals["mu_home"]) + float(cross_signals["mu_away"])
    home_share = float(cross_signals["mu_home"]) / max(1e-12, stage2_sum)
    return {
        "mu_total": mu_total,
        "home_share": home_share,
        "dispersion_k": float(current_state["nb_dispersion_k"]),
        "home_total_rate": home_total_rate,
        "away_total_rate": away_total_rate,
        "pair_total_rate": pair_total_rate,
        "direct_total_signal_weight": signal_weight,
        "league_total": league_total,
    }


def nb_binomial_1x2(mu_total: float, dispersion_k: float, home_share: float, max_total: int) -> dict[str, Any]:
    probs = {key: 0.0 for key in OUTCOMES}
    total_mass = 0.0
    total_probs: list[float] = []
    for total in range(max_total + 1):
        pt = negative_binomial_pmf(total, mu_total, dispersion_k)
        total_probs.append(pt)
        total_mass += pt
        for home_goals in range(total + 1):
            away_goals = total - home_goals
            p = pt * binomial_pmf(home_goals, total, home_share)
            if home_goals > away_goals:
                probs["home"] += p
            elif home_goals == away_goals:
                probs["draw"] += p
            else:
                probs["away"] += p
    if total_mass <= 0:
        raise PlatformError("Direct-T finite total mass is zero")
    return {
        "probabilities": {key: value / total_mass for key, value in probs.items()},
        "total_probabilities": [value / total_mass for value in total_probs],
        "finite_total_mass": total_mass,
        "truncation_mass": max(0.0, 1.0 - total_mass),
    }


def target_score(all_matches: list[Any], row: dict[str, Any], cutoff: Any) -> tuple[int, int]:
    home_token = normalize_team_token(str(row["home_team"]))
    away_token = normalize_team_token(str(row["away_team"]))
    season = str(row["season"])
    candidates = [
        m for m in all_matches
        if str(m.season) == season
        and m.date.date() == cutoff.date()
        and normalize_team_token(str(m.home_team)) == home_token
        and normalize_team_token(str(m.away_team)) == away_token
    ]
    if len(candidates) != 1:
        raise PlatformError(f"target settlement match lookup count={len(candidates)}")
    return int(candidates[0].home_goals), int(candidates[0].away_goals)


def poisson_total_pmf(total: int, mu: float) -> float:
    if total < 0:
        return 0.0
    mu = max(1e-12, float(mu))
    return math.exp(-mu + total * math.log(mu) - math.lgamma(total + 1))


def binary_log_loss(p: float, y: int) -> float:
    p = min(1.0 - EPS, max(EPS, float(p)))
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def total_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual_totals = [int(r["actual_total_goals"]) for r in rows]
    s2_mu = [float(r["stage2_mu_total_implied"]) for r in rows]
    s3_mu = [float(r["stage3_direct_t"]["mu_total"]) for r in rows]
    s2_nll = [-math.log(max(EPS, poisson_total_pmf(t, mu))) for t, mu in zip(actual_totals, s2_mu)]
    s3_nll = [-math.log(max(EPS, negative_binomial_pmf(t, r["stage3_direct_t"]["mu_total"], r["stage3_direct_t"]["dispersion_k"]))) for t, r in zip(actual_totals, rows)]
    s2_over25 = [1.0 - sum(poisson_total_pmf(t, mu) for t in range(3)) for mu in s2_mu]
    s3_over25 = [sum(r["stage3_total_probabilities"][3:]) for r in rows]
    labels = [int(t >= 3) for t in actual_totals]
    s2_ou_ll = [binary_log_loss(p, y) for p, y in zip(s2_over25, labels)]
    s3_ou_ll = [binary_log_loss(p, y) for p, y in zip(s3_over25, labels)]
    s2_ou_brier = [(p - y) ** 2 for p, y in zip(s2_over25, labels)]
    s3_ou_brier = [(p - y) ** 2 for p, y in zip(s3_over25, labels)]
    return {
        "n": len(rows),
        "actual_mean_total": statistics.fmean(actual_totals),
        "stage2_mean_implied_total": statistics.fmean(s2_mu),
        "stage3_mean_direct_total": statistics.fmean(s3_mu),
        "stage2_poisson_total_nll": statistics.fmean(s2_nll),
        "stage3_nb_total_nll": statistics.fmean(s3_nll),
        "delta_total_nll_stage3_minus_stage2": statistics.fmean(s3_nll) - statistics.fmean(s2_nll),
        "actual_over25_rate": statistics.fmean(labels),
        "stage2_mean_over25_probability": statistics.fmean(s2_over25),
        "stage3_mean_over25_probability": statistics.fmean(s3_over25),
        "stage2_over25_log_loss": statistics.fmean(s2_ou_ll),
        "stage3_over25_log_loss": statistics.fmean(s3_ou_ll),
        "delta_over25_log_loss_stage3_minus_stage2": statistics.fmean(s3_ou_ll) - statistics.fmean(s2_ou_ll),
        "stage2_over25_brier": statistics.fmean(s2_ou_brier),
        "stage3_over25_brier": statistics.fmean(s3_ou_brier),
        "delta_over25_brier_stage3_minus_stage2": statistics.fmean(s3_ou_brier) - statistics.fmean(s2_ou_brier),
    }


def clustered_bootstrap(rows: list[dict[str, Any]], metric: str, resamples: int, seed: int) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if metric == "1x2_log_loss":
            diff = log_loss(r["stage3_probabilities"], r["actual"]) - log_loss(r["stage2_probabilities"], r["actual"])
        elif metric == "total_nll":
            total = int(r["actual_total_goals"])
            s2 = -math.log(max(EPS, poisson_total_pmf(total, r["stage2_mu_total_implied"])))
            dt = r["stage3_direct_t"]
            s3 = -math.log(max(EPS, negative_binomial_pmf(total, dt["mu_total"], dt["dispersion_k"])))
            diff = s3 - s2
        else:
            raise ValueError(metric)
        by_date[r["date"][:10]].append(diff)
    dates = sorted(by_date)
    observed_values = [v for date in dates for v in by_date[date]]
    observed = statistics.fmean(observed_values)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        vals: list[float] = []
        for _ in dates:
            vals.extend(by_date[rng.choice(dates)])
        samples.append(statistics.fmean(vals))
    samples.sort()
    def q(frac: float) -> float:
        pos = frac * (len(samples) - 1); lo = int(math.floor(pos)); hi = int(math.ceil(pos))
        if lo == hi: return samples[lo]
        w = pos - lo
        return samples[lo] * (1.0 - w) + samples[hi] * w
    return {
        "metric": metric,
        "n": len(rows),
        "date_clusters": len(dates),
        "resamples": resamples,
        "seed": seed,
        "point_estimate_stage3_minus_stage2": observed,
        "ci90": [q(0.05), q(0.95)],
        "ci95": [q(0.025), q(0.975)],
        "interpretation": "negative favors Stage 3 Direct-T",
    }


def grouped(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["competition_id"]].append(row)
    out: dict[str, Any] = {}
    for key, subset in sorted(groups.items()):
        s2 = summarize(subset, "stage2_probabilities")
        s3 = summarize(subset, "stage3_probabilities")
        out[key] = {
            "stage2": s2,
            "stage3": s3,
            "delta_stage3_minus_stage2": paired_metric_delta(s2, s3),
            "totals": total_metrics(subset),
        }
    return out


def fmt(value: Any, digits: int = 6) -> str:
    if value is None: return "NA"
    if isinstance(value, float): return f"{value:.{digits}f}" if math.isfinite(value) else "NA"
    return str(value)


def render_markdown(result: dict[str, Any]) -> str:
    s2 = result["stage2_recomputed"]
    s3 = result["stage3_direct_t"]
    d = result["paired_delta_stage3_minus_stage2"]
    tm = result["total_goal_metrics"]
    b1 = result["bootstrap_1x2_ll"]
    bt = result["bootstrap_total_nll"]
    lines = [
        "# Base Anatomy Stage 3 — Direct-T Only",
        "",
        "**Classification:** RESEARCH_ONLY / formal_weight=0 / VIEWED_STAGE3",
        "",
        "## Isolation contract",
        "",
        "Stage 3 keeps the Stage-2 cross-season equal-weight team state and home-share signal. It replaces only the Poisson total marginal with the formal Direct-T venue-total signal plus Negative-Binomial total. Conditional H|T remains the Poisson-implied Binomial split. Multi-OU, conditional GD/Beta-Binomial, low-score residuals and OOF matrix calibration remain disabled.",
        "",
        f"- Exact paired rows: {result['coverage']['paired_rows']} / {result['coverage']['benchmark_rows']}",
        "",
        "## 1X2 paired metrics",
        "",
        "| metric | Stage 2 | Stage 3 Direct-T | delta S3-S2 |",
        "|---|---:|---:|---:|",
    ]
    for label, key in (("accuracy","accuracy"),("log loss","log_loss"),("Brier","brier"),("RPS","rps"),("draw AUC","draw_auc"),("mean p(draw)","mean_draw_probability"),("draw p std","draw_probability_std"),("draw Top-1 rate","draw_pick_rate"),("draw recall","draw_recall")):
        lines.append(f"| {label} | {fmt(s2.get(key))} | {fmt(s3.get(key))} | {fmt(d.get(key))} |")
    lines.extend([
        "",
        f"- Stage 2 Top-1 H/D/A: {s2.get('top1_counts')}",
        f"- Stage 3 Top-1 H/D/A: {s3.get('top1_counts')}",
        f"- Actual H/D/A: {s3.get('actual_counts')}",
        "",
        "## Total-goal diagnostics",
        "",
        f"- Actual mean total: {fmt(tm['actual_mean_total'])}",
        f"- Stage 2 implied Poisson mean total: {fmt(tm['stage2_mean_implied_total'])}",
        f"- Stage 3 Direct-T mean total: {fmt(tm['stage3_mean_direct_total'])}",
        f"- Total NLL Stage 2 -> Stage 3: {fmt(tm['stage2_poisson_total_nll'])} -> {fmt(tm['stage3_nb_total_nll'])} (delta {fmt(tm['delta_total_nll_stage3_minus_stage2'])})",
        f"- Actual O2.5 rate: {fmt(tm['actual_over25_rate'])}",
        f"- Mean P(O2.5) Stage 2 -> Stage 3: {fmt(tm['stage2_mean_over25_probability'])} -> {fmt(tm['stage3_mean_over25_probability'])}",
        f"- O2.5 log loss Stage 2 -> Stage 3: {fmt(tm['stage2_over25_log_loss'])} -> {fmt(tm['stage3_over25_log_loss'])} (delta {fmt(tm['delta_over25_log_loss_stage3_minus_stage2'])})",
        f"- O2.5 Brier Stage 2 -> Stage 3: {fmt(tm['stage2_over25_brier'])} -> {fmt(tm['stage3_over25_brier'])} (delta {fmt(tm['delta_over25_brier_stage3_minus_stage2'])})",
        "",
        "## Date-block bootstrap",
        "",
        f"- 1X2 LL delta S3-S2: {fmt(b1['point_estimate_stage3_minus_stage2'])}; 90% CI {b1['ci90']}; 95% CI {b1['ci95']}",
        f"- Total NLL delta S3-S2: {fmt(bt['point_estimate_stage3_minus_stage2'])}; 90% CI {bt['ci90']}; 95% CI {bt['ci95']}",
        "",
        "## Draw calibration — Stage 3",
        "",
        "| p(draw) bin | n | mean predicted | actual draw | gap |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in result["stage3_draw_calibration"]:
        lines.append(f"| [{row['low']:.2f}, {row['high']:.2f}) | {row['n']} | {row['mean_predicted_draw']:.4f} | {row['actual_draw_rate']:.4f} | {row['calibration_gap_pred_minus_actual']:+.4f} |")
    lines.extend([
        "",
        "## By competition",
        "",
        "| competition | n | dLL 1X2 | dLL total | draw AUC S2 | draw AUC S3 | D picks S2 | D picks S3 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for comp, block in result["by_competition"].items():
        a=block["stage2"]; c=block["stage3"]; dlt=block["delta_stage3_minus_stage2"]; totals=block["totals"]
        lines.append(f"| {comp} | {a.get('n',0)} | {fmt(dlt.get('log_loss'),4)} | {fmt(totals.get('delta_total_nll_stage3_minus_stage2'),4)} | {fmt(a.get('draw_auc'),4)} | {fmt(c.get('draw_auc'),4)} | {a.get('top1_counts',{}).get('draw',0)} | {c.get('top1_counts',{}).get('draw',0)} |")
    lines.extend([
        "",
        "## Boundary",
        "",
        "This stage tests Direct-T only. It does not authorize multi-OU or conditional-GD conclusions. Stage 4 remains locked to multi-OU only.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--max-goals", type=int, default=25)
    parser.add_argument("--bootstrap-resamples", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260817)
    args = parser.parse_args()

    benchmark = load_json(args.benchmark)
    rows = benchmark.get("rows") or []
    config = load_config()
    match_cache: dict[str, list[Any]] = {}
    paired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()

    for index, row in enumerate(rows):
        competition_id = str(row["competition_id"]); season = str(row["season"])
        try:
            cutoff = parse_iso_datetime(str(row["date"]), "benchmark.row.date")
            if competition_id not in match_cache:
                match_cache[competition_id] = read_processed_matches(competition_id)
            all_matches = match_cache[competition_id]
            _, current_history = current_season_history(all_matches, cutoff, season=season)
            params, parameter_source = point_in_time_parameters(competition_id, season, config)
            current_state = fit_current_season_state(current_history, cutoff, params, config)

            cross = cross_season_score_signals(all_matches, cutoff, str(row["home_team"]), str(row["away_team"]), current_state, params)
            s2_mu_home = float(cross["mu_home"]); s2_mu_away = float(cross["mu_away"])
            s2 = independent_poisson_1x2(s2_mu_home, s2_mu_away, max_goals=args.max_goals)["probabilities"]

            dt = direct_t_parameters(cross, current_state, params)
            matrix = nb_binomial_1x2(dt["mu_total"], dt["dispersion_k"], dt["home_share"], args.max_goals)
            s3 = matrix["probabilities"]
            home_goals, away_goals = target_score(all_matches, row, cutoff)
            actual = str(row["actual"])
            if actual not in OUTCOMES:
                raise PlatformError(f"unexpected actual outcome: {actual!r}")

            paired.append({
                "benchmark_index": index,
                "competition_id": competition_id,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "actual": actual,
                "actual_home_goals": home_goals,
                "actual_away_goals": away_goals,
                "actual_total_goals": home_goals + away_goals,
                "stage2_mu_home": s2_mu_home,
                "stage2_mu_away": s2_mu_away,
                "stage2_mu_total_implied": s2_mu_home + s2_mu_away,
                "stage2_probabilities": s2,
                "stage3_direct_t": dt,
                "stage3_probabilities": s3,
                "stage3_total_probabilities": matrix["total_probabilities"],
                "stage3_truncation_mass": matrix["truncation_mass"],
                "parameter_source": parameter_source,
            })
        except (PlatformError, KeyError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc).split("\n",1)[0]
            skipped_reasons[reason] += 1
            skipped.append({"benchmark_index": index, "competition_id": competition_id, "season": season, "date": str(row.get("date")), "reason": reason})

    if not paired:
        raise PlatformError("Stage-3 produced zero paired rows")
    s2 = summarize(paired, "stage2_probabilities")
    s3 = summarize(paired, "stage3_probabilities")
    result = {
        "schema_version": "base-anatomy-direct-t-r1",
        "status": "RESEARCH_ONLY_VIEWED_STAGE3",
        "classification": "RETROSPECTIVE_RESEARCH_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "stage": 3,
        "stage_name": "direct_t_only",
        "fixed_anatomy_order": ["old_formal_poisson_bottom","cross_season_team_state","direct_t","multi_ou","conditional_goal_difference","unified_score_matrix"],
        "isolation_contract": {
            "cross_season_equal_weight_team_state": True,
            "direct_t_venue_total_signal": True,
            "negative_binomial_total": True,
            "conditional_split": "poisson_implied_binomial",
            "adaptive_hedge_weighting": False,
            "multi_ou": False,
            "conditional_goal_difference_or_beta_binomial": False,
            "low_score_residual": False,
            "oof_matrix_calibration": False,
            "market_as_model_input": False,
            "main_or_current_mutation": False,
        },
        "coverage": {"benchmark_rows": len(rows), "paired_rows": len(paired), "skipped_rows": len(skipped), "paired_coverage_rate": len(paired)/len(rows)},
        "stage2_recomputed": s2,
        "stage3_direct_t": s3,
        "paired_delta_stage3_minus_stage2": paired_metric_delta(s2,s3),
        "total_goal_metrics": total_metrics(paired),
        "bootstrap_1x2_ll": clustered_bootstrap(paired,"1x2_log_loss",args.bootstrap_resamples,args.bootstrap_seed),
        "bootstrap_total_nll": clustered_bootstrap(paired,"total_nll",args.bootstrap_resamples,args.bootstrap_seed+1),
        "stage3_draw_calibration": calibration(paired,"stage3_probabilities"),
        "by_competition": grouped(paired),
        "skipped_reason_counts": dict(skipped_reasons.most_common()),
        "skipped_rows": skipped,
        "paired_predictions": paired,
        "next_stage_locked": "multi_ou_only",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path=args.output_dir/"direct_t_r1.json"; md_path=args.output_dir/"direct_t_r1.md"
    json_path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    markdown=render_markdown(result); md_path.write_text(markdown,encoding="utf-8")
    print(markdown); print(f"\nJSON={json_path.relative_to(REPO_ROOT)}"); print(f"MD={md_path.relative_to(REPO_ROOT)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
