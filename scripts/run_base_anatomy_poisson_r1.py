#!/usr/bin/env python3
"""Stage-1 base anatomy: reconstruct the old independent-Poisson bottom.

Research-only ablation on the frozen fixed1000 benchmark.  This stage deliberately
uses only the current-season PIT attack/defence score signals exposed by the
formal V4.6.x engine, then converts those two means with independent Poisson
marginals.  It explicitly excludes every later layer:

- cross-season team state
- Direct-T / direct Negative-Binomial total
- multi-OU
- conditional goal-difference / Beta-Binomial allocation
- low-score residual correction
- OOF matrix calibration
- market probabilities as model inputs

The benchmark's closing 1X2 probabilities are retained only as an orientation
comparator; they never enter model construction.  No main/CURRENT/formal file is
modified by this runner.
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
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
)
from platform_core import PlatformError, load_json, parse_iso_datetime, read_processed_matches  # noqa: E402

BENCHMARK_DEFAULT = FOOTBALL_DATA / "benchmarks" / "v6_1x2_fixed1000_v6130.json"
MODEL_ROOT = FOOTBALL_DATA / "models" / "formal_core_v460"
OUTPUT_DEFAULT = FOOTBALL_DATA / "research" / "base_anatomy_20260817"
OUTCOMES = ("home", "draw", "away")
EPS = 1e-15
DRAW_BINS = (0.0, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 1.0000001)


def poisson_vector(mu: float, max_goals: int) -> list[float]:
    mu = max(1e-12, float(mu))
    values = [math.exp(-mu)]
    for goals in range(1, max_goals + 1):
        values.append(values[-1] * mu / goals)
    return values


def independent_poisson_1x2(mu_home: float, mu_away: float, max_goals: int = 25) -> dict[str, Any]:
    home = poisson_vector(mu_home, max_goals)
    away = poisson_vector(mu_away, max_goals)
    probs = {key: 0.0 for key in OUTCOMES}
    finite_mass = 0.0
    for hg, ph in enumerate(home):
        for ag, pa in enumerate(away):
            p = ph * pa
            finite_mass += p
            if hg > ag:
                probs["home"] += p
            elif hg == ag:
                probs["draw"] += p
            else:
                probs["away"] += p
    if finite_mass <= 0:
        raise PlatformError("independent Poisson finite-grid mass is zero")
    probs = {key: value / finite_mass for key, value in probs.items()}
    return {
        "probabilities": probs,
        "finite_grid_mass": finite_mass,
        "truncation_mass": max(0.0, 1.0 - finite_mass),
    }


def point_in_time_parameters(competition_id: str, season: str, config: dict[str, Any]) -> tuple[dict[str, float], str]:
    model_path = MODEL_ROOT / competition_id / "model.json"
    selected: dict[str, Any] | None = None
    source = "config_default"
    if model_path.exists():
        model = load_json(model_path)
        pit = model.get("point_in_time_parameters") or {}
        if isinstance(pit, dict) and isinstance(pit.get(str(season)), dict):
            selected = pit[str(season)]
            source = "point_in_time_parameters"
        elif isinstance(model.get("selected_parameters"), dict):
            selected = model["selected_parameters"]
            source = "selected_parameters_fallback"
    return _merge_parameters(config, selected), source


def argmax_label(probabilities: dict[str, float]) -> str:
    return max(OUTCOMES, key=lambda key: (probabilities[key], -OUTCOMES.index(key)))


def log_loss(probabilities: dict[str, float], actual: str) -> float:
    return -math.log(max(EPS, float(probabilities[actual])))


def multiclass_brier(probabilities: dict[str, float], actual: str) -> float:
    return sum((probabilities[key] - (1.0 if key == actual else 0.0)) ** 2 for key in OUTCOMES)


def rps(probabilities: dict[str, float], actual: str) -> float:
    # Natural ordered 1X2 axis: H, D, A. Two cumulative cut points.
    actual_idx = OUTCOMES.index(actual)
    cumulative_p = 0.0
    score = 0.0
    for idx in range(len(OUTCOMES) - 1):
        cumulative_p += probabilities[OUTCOMES[idx]]
        cumulative_o = 1.0 if actual_idx <= idx else 0.0
        score += (cumulative_p - cumulative_o) ** 2
    return score / 2.0


def binary_auc(scores: list[float], labels: list[int]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(zip(scores, labels), key=lambda pair: pair[0])
    rank_sum_pos = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        rank_sum_pos += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum_pos - positives * (positives + 1) / 2.0) / (positives * negatives)


def summarize(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    picks = Counter()
    actuals = Counter()
    correct = 0
    losses: list[float] = []
    briers: list[float] = []
    rps_values: list[float] = []
    draw_scores: list[float] = []
    draw_labels: list[int] = []
    draw_pick_correct = 0
    actual_draws = 0
    for row in rows:
        p = row[probability_key]
        actual = row["actual"]
        pick = argmax_label(p)
        picks[pick] += 1
        actuals[actual] += 1
        correct += int(pick == actual)
        losses.append(log_loss(p, actual))
        briers.append(multiclass_brier(p, actual))
        rps_values.append(rps(p, actual))
        draw_scores.append(float(p["draw"]))
        is_draw = int(actual == "draw")
        draw_labels.append(is_draw)
        actual_draws += is_draw
        if pick == "draw" and actual == "draw":
            draw_pick_correct += 1
    draw_picks = picks["draw"]
    n = len(rows)
    return {
        "n": n,
        "accuracy": correct / n,
        "log_loss": statistics.fmean(losses),
        "brier": statistics.fmean(briers),
        "rps": statistics.fmean(rps_values),
        "top1_counts": {key: picks[key] for key in OUTCOMES},
        "actual_counts": {key: actuals[key] for key in OUTCOMES},
        "actual_draw_rate": actual_draws / n,
        "mean_draw_probability": statistics.fmean(draw_scores),
        "draw_probability_std": statistics.pstdev(draw_scores) if len(draw_scores) > 1 else 0.0,
        "draw_auc": binary_auc(draw_scores, draw_labels),
        "draw_pick_rate": draw_picks / n,
        "draw_pick_precision": (draw_pick_correct / draw_picks) if draw_picks else None,
        "draw_recall": (draw_pick_correct / actual_draws) if actual_draws else None,
    }


def calibration(rows: list[dict[str, Any]], probability_key: str) -> list[dict[str, Any]]:
    output = []
    for low, high in zip(DRAW_BINS[:-1], DRAW_BINS[1:]):
        subset = [row for row in rows if low <= row[probability_key]["draw"] < high]
        if not subset:
            continue
        mean_p = statistics.fmean(row[probability_key]["draw"] for row in subset)
        actual_rate = statistics.fmean(1.0 if row["actual"] == "draw" else 0.0 for row in subset)
        output.append({
            "low": low,
            "high": min(high, 1.0),
            "n": len(subset),
            "mean_predicted_draw": mean_p,
            "actual_draw_rate": actual_rate,
            "calibration_gap_pred_minus_actual": mean_p - actual_rate,
        })
    return output


def grouped_summary(rows: list[dict[str, Any]], field: str, probability_key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    return {key: summarize(value, probability_key) for key, value in sorted(groups.items())}


def date_block_bootstrap(rows: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    """Bootstrap mean LL(Poisson)-LL(closing) by calendar-date clusters."""
    if not rows:
        return {"n": 0, "status": "unavailable"}
    by_date: dict[str, list[float]] = defaultdict(list)
    all_diffs: list[float] = []
    for row in rows:
        diff = log_loss(row["poisson_probabilities"], row["actual"]) - log_loss(row["market_probabilities"], row["actual"])
        by_date[row["date"][:10]].append(diff)
        all_diffs.append(diff)
    dates = sorted(by_date)
    observed = statistics.fmean(all_diffs)
    if len(dates) < 2 or resamples <= 0:
        return {
            "n": len(rows),
            "date_clusters": len(dates),
            "point_estimate": observed,
            "status": "insufficient_clusters",
        }
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        chosen = [rng.choice(dates) for _ in dates]
        values: list[float] = []
        for date in chosen:
            values.extend(by_date[date])
        samples.append(statistics.fmean(values))
    samples.sort()

    def quantile(q: float) -> float:
        if not samples:
            return math.nan
        position = q * (len(samples) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return samples[lower]
        weight = position - lower
        return samples[lower] * (1.0 - weight) + samples[upper] * weight

    return {
        "n": len(rows),
        "date_clusters": len(dates),
        "resamples": resamples,
        "seed": seed,
        "metric": "mean_log_loss_poisson_minus_closing_market",
        "point_estimate": observed,
        "ci90": [quantile(0.05), quantile(0.95)],
        "ci95": [quantile(0.025), quantile(0.975)],
        "interpretation": "negative favors reconstructed Poisson; closing market is orientation-only, not a fair live-input baseline",
    }


def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "NA"
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(result: dict[str, Any]) -> str:
    p = result["poisson_base"]
    m = result["closing_market_orientation"]
    b = result["bootstrap_poisson_minus_market"]
    cov = result["coverage"]
    lines = [
        "# Base Anatomy Stage 1 — Old Formal Poisson Bottom",
        "",
        "**Classification:** RESEARCH_ONLY / formal_weight=0 / VIEWED_STAGE1",
        "",
        "## Isolation contract",
        "",
        "This stage reconstructs only the current-season PIT attack/defence score signals and applies an independent-Poisson score model. It does **not** use cross-season state, Direct-T/NB total, multi-OU, conditional GD/Beta-Binomial allocation, low-score residuals, OOF matrix calibration, or market prices as model inputs.",
        "",
        "## Coverage",
        "",
        f"- Frozen benchmark rows: {cov['benchmark_rows']}",
        f"- Poisson-evaluable rows: {cov['evaluated_rows']}",
        f"- Coverage: {fmt(cov['coverage_rate'])}",
        f"- Skipped rows: {cov['skipped_rows']}",
        "",
        "## Stage-1 Poisson metrics",
        "",
        f"- Accuracy: {fmt(p.get('accuracy'))}",
        f"- Log loss: {fmt(p.get('log_loss'))}",
        f"- Brier: {fmt(p.get('brier'))}",
        f"- RPS: {fmt(p.get('rps'))}",
        f"- Top-1 H/D/A: {p.get('top1_counts')}",
        f"- Actual H/D/A: {p.get('actual_counts')}",
        f"- Mean draw probability: {fmt(p.get('mean_draw_probability'))}",
        f"- Draw probability std: {fmt(p.get('draw_probability_std'))}",
        f"- Draw AUC: {fmt(p.get('draw_auc'))}",
        f"- Draw pick rate: {fmt(p.get('draw_pick_rate'))}",
        f"- Draw recall: {fmt(p.get('draw_recall'))}",
        "",
        "## Closing-market orientation on the identical evaluable subset",
        "",
        "The closing market is shown only to orient scale; it is not used by the Poisson model and is not treated as a fair question-time baseline.",
        "",
        f"- Market accuracy: {fmt(m.get('accuracy'))}",
        f"- Market log loss: {fmt(m.get('log_loss'))}",
        f"- Market Brier: {fmt(m.get('brier'))}",
        f"- Market RPS: {fmt(m.get('rps'))}",
        f"- Market draw AUC: {fmt(m.get('draw_auc'))}",
        "",
        "## Date-block bootstrap: Poisson LL minus closing-market LL",
        "",
        f"- Point estimate: {fmt(b.get('point_estimate'))}",
        f"- 90% CI: {b.get('ci90')}",
        f"- 95% CI: {b.get('ci95')}",
        f"- Date clusters: {b.get('date_clusters')}",
        "",
        "## Draw calibration",
        "",
        "| p(draw) bin | n | mean predicted | actual draw | gap |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in result["draw_calibration"]:
        lines.append(
            f"| [{row['low']:.2f}, {row['high']:.2f}) | {row['n']} | {row['mean_predicted_draw']:.4f} | {row['actual_draw_rate']:.4f} | {row['calibration_gap_pred_minus_actual']:+.4f} |"
        )
    lines.extend([
        "",
        "## By competition",
        "",
        "| competition | n | acc | LL | draw AUC | mean pD | actual D |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for competition, summary in result["by_competition"].items():
        lines.append(
            f"| {competition} | {summary.get('n', 0)} | {fmt(summary.get('accuracy'),4)} | {fmt(summary.get('log_loss'),4)} | {fmt(summary.get('draw_auc'),4)} | {fmt(summary.get('mean_draw_probability'),4)} | {fmt(summary.get('actual_draw_rate'),4)} |"
        )
    lines.extend([
        "",
        "## Boundary",
        "",
        "This is an ablation/reconstruction of the old independent-Poisson bottom from the formal engine's pre-Direct-T score signals. It is **not** a claim that the current V4.6.x formal engine itself is Poisson. Stage 2 remains locked to cross-season team state only.",
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
    if not isinstance(rows, list) or not rows:
        raise PlatformError("frozen benchmark rows are missing")
    config = load_config()
    match_cache: dict[str, list[Any]] = {}
    evaluated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()
    parameter_sources: Counter[str] = Counter()
    truncation_masses: list[float] = []

    for index, row in enumerate(rows):
        competition_id = str(row["competition_id"])
        season = str(row["season"])
        try:
            cutoff = parse_iso_datetime(str(row["date"]), "benchmark.row.date")
            if competition_id not in match_cache:
                match_cache[competition_id] = read_processed_matches(competition_id)
            _, history = current_season_history(match_cache[competition_id], cutoff, season=season)
            params, parameter_source = point_in_time_parameters(competition_id, season, config)
            state = fit_current_season_state(history, cutoff, params, config)
            signals = expected_goals(state, str(row["home_team"]), str(row["away_team"]), params, config)

            # Stage-1 isolation point: use the attack/defence score signals BEFORE
            # direct-total construction and every later allocation/calibration layer.
            mu_home = float(signals["home_score_signal"])
            mu_away = float(signals["away_score_signal"])
            poisson = independent_poisson_1x2(mu_home, mu_away, max_goals=args.max_goals)
            market = {key: float(row["probabilities"][key]) for key in OUTCOMES}
            actual = str(row["actual"])
            if actual not in OUTCOMES:
                raise PlatformError(f"unexpected actual outcome: {actual!r}")
            parameter_sources[parameter_source] += 1
            truncation_masses.append(float(poisson["truncation_mass"]))
            evaluated.append({
                "benchmark_index": index,
                "competition_id": competition_id,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "actual": actual,
                "mu_home_poisson": mu_home,
                "mu_away_poisson": mu_away,
                "poisson_probabilities": poisson["probabilities"],
                "market_probabilities": market,
                "parameter_source": parameter_source,
                "history_matches": len(history),
                "home_venue_raw_matches": int(signals["home_raw_matches"]),
                "away_venue_raw_matches": int(signals["away_raw_matches"]),
                "finite_grid_mass": poisson["finite_grid_mass"],
                "truncation_mass": poisson["truncation_mass"],
            })
        except (PlatformError, KeyError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc).split("\n", 1)[0]
            skipped_reasons[reason] += 1
            skipped.append({
                "benchmark_index": index,
                "competition_id": competition_id,
                "season": season,
                "date": str(row.get("date")),
                "home_team": str(row.get("home_team")),
                "away_team": str(row.get("away_team")),
                "reason": reason,
            })

    if not evaluated:
        raise PlatformError("Stage-1 produced zero evaluable rows")

    result = {
        "schema_version": "base-anatomy-poisson-r1",
        "status": "RESEARCH_ONLY_VIEWED_STAGE1",
        "classification": "RETROSPECTIVE_RESEARCH_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "stage": 1,
        "stage_name": "old_formal_independent_poisson_bottom",
        "fixed_anatomy_order": [
            "old_formal_poisson_bottom",
            "cross_season_team_state",
            "direct_t",
            "multi_ou",
            "conditional_goal_difference",
            "unified_score_matrix",
        ],
        "isolation_contract": {
            "current_season_pit_attack_defence": True,
            "independent_poisson_only": True,
            "cross_season_team_state": False,
            "direct_t_or_negative_binomial_total": False,
            "multi_ou": False,
            "conditional_goal_difference_or_beta_binomial": False,
            "low_score_residual": False,
            "oof_matrix_calibration": False,
            "market_as_model_input": False,
            "main_or_current_mutation": False,
        },
        "benchmark": {
            "path": str(args.benchmark.relative_to(REPO_ROOT)),
            "schema_version": benchmark.get("schema_version"),
            "status": benchmark.get("status"),
            "seed": benchmark.get("seed"),
            "target_n": benchmark.get("target_n"),
            "sampling_policy": benchmark.get("sampling_policy"),
        },
        "coverage": {
            "benchmark_rows": len(rows),
            "evaluated_rows": len(evaluated),
            "skipped_rows": len(skipped),
            "coverage_rate": len(evaluated) / len(rows),
            "parameter_sources": dict(parameter_sources),
            "max_poisson_truncation_mass": max(truncation_masses) if truncation_masses else None,
        },
        "poisson_base": summarize(evaluated, "poisson_probabilities"),
        "closing_market_orientation": summarize(evaluated, "market_probabilities"),
        "bootstrap_poisson_minus_market": date_block_bootstrap(evaluated, args.bootstrap_resamples, args.bootstrap_seed),
        "draw_calibration": calibration(evaluated, "poisson_probabilities"),
        "by_competition": grouped_summary(evaluated, "competition_id", "poisson_probabilities"),
        "by_season": grouped_summary(evaluated, "season", "poisson_probabilities"),
        "skipped_reason_counts": dict(skipped_reasons.most_common()),
        "skipped_rows": skipped,
        "predictions": evaluated,
        "next_stage_locked": "cross_season_team_state_only",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "poisson_base_r1.json"
    md_path = args.output_dir / "poisson_base_r1.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    markdown = render_markdown(result)
    md_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"\nJSON={json_path.relative_to(REPO_ROOT)}")
    print(f"MD={md_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
