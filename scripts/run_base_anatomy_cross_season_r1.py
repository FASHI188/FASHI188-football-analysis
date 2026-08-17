#!/usr/bin/env python3
"""Stage-2 base anatomy: add cross-season team state to the Stage-1 Poisson bottom.

Controlled ablation contract
----------------------------
Stage 1 = current-season strict-PIT attack/defence score signals -> independent Poisson.
Stage 2 = identical league baseline, identical shrinkage priors, identical independent-Poisson
probability head, but target-team venue GF/GA state is sourced from the research-only
cross-season multi-timescale state utility.

Deliberately excluded from both sides of the paired comparison:
- adaptive Hedge weighting (Stage-2 primary uses equal expert weights / ledger=None)
- Direct-T / Negative-Binomial total
- multi-OU
- conditional goal difference / Beta-Binomial allocation
- low-score residuals
- OOF matrix calibration
- market probabilities as model inputs

Team names are normalized with the same normalizer used by the formal engine before they are
passed into the cross-season state utility. This prevents raw-name aliases from masquerading as
missing historical team state.
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
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FOOTBALL_DATA = REPO_ROOT / "football-data"
ENGINE_ROOT = FOOTBALL_DATA / "engine"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (ENGINE_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cross_season_hedge_state_v6262 import EXPERT_HALF_LIVES, team_state  # noqa: E402
from football_v460_engine import (  # noqa: E402
    _shrunk_rate,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
)
from platform_core import PlatformError, load_json, normalize_team_token, parse_iso_datetime, read_processed_matches  # noqa: E402
from run_base_anatomy_poisson_r1 import (  # noqa: E402
    BENCHMARK_DEFAULT,
    MODEL_ROOT,
    OUTCOMES,
    argmax_label,
    calibration,
    independent_poisson_1x2,
    log_loss,
    multiclass_brier,
    point_in_time_parameters,
    rps,
    summarize,
)

OUTPUT_DEFAULT = FOOTBALL_DATA / "research" / "base_anatomy_20260817"


def normalized_history_before(matches: list[Any], cutoff: Any) -> list[Any]:
    """All competition history strictly before the cutoff calendar date, with normalized names."""
    output: list[Any] = []
    for match in matches:
        if match.date.date() >= cutoff.date():
            continue
        output.append(SimpleNamespace(
            date=match.date,
            home_team=normalize_team_token(str(match.home_team)),
            away_team=normalize_team_token(str(match.away_team)),
            home_goals=int(match.home_goals),
            away_goals=int(match.away_goals),
        ))
    return output


def cross_season_score_signals(
    all_matches: list[Any],
    cutoff: Any,
    home_team: str,
    away_team: str,
    current_state: dict[str, Any],
    params: dict[str, float],
) -> dict[str, Any]:
    """Replace only target-team venue state; keep Stage-1 league baseline and shrinkage."""
    history = normalized_history_before(all_matches, cutoff)
    home_token = normalize_team_token(home_team)
    away_token = normalize_team_token(away_team)
    home_state = team_state(history, home_token, cutoff, ledger=None)
    away_state = team_state(history, away_token, cutoff, ledger=None)
    home = home_state["mixed"]
    away = away_state["mixed"]

    # Paired Stage-2 rows already satisfy the Stage-1 current-season sample gate. Still require
    # observable all-history state so a missing/renamed team cannot silently collapse to the prior.
    if float(home.get("raw_matches", 0.0)) <= 0 or float(away.get("raw_matches", 0.0)) <= 0:
        raise PlatformError("cross-season normalized team history missing")

    league_home = float(current_state["league_home_goals"])
    league_away = float(current_state["league_away_goals"])
    prior = float(params["team_prior_matches"])

    home_attack = _shrunk_rate(float(home["home_gf"]), float(home["home_matches"]), league_home, prior) / league_home
    home_defence = _shrunk_rate(float(home["home_ga"]), float(home["home_matches"]), league_away, prior) / league_away
    away_attack = _shrunk_rate(float(away["away_gf"]), float(away["away_matches"]), league_away, prior) / league_away
    away_defence = _shrunk_rate(float(away["away_ga"]), float(away["away_matches"]), league_home, prior) / league_home

    minimum_mu = float(params["minimum_goal_mean"])
    maximum_mu = float(params["maximum_goal_mean"])
    mu_home = min(maximum_mu, max(minimum_mu, league_home * home_attack * away_defence))
    mu_away = min(maximum_mu, max(minimum_mu, league_away * away_attack * home_defence))
    return {
        "mu_home": mu_home,
        "mu_away": mu_away,
        "home_team_token": home_token,
        "away_team_token": away_token,
        "home_state": home_state,
        "away_state": away_state,
        "history_rows": len(history),
    }


def paired_metric_delta(stage1: dict[str, Any], stage2: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "accuracy", "log_loss", "brier", "rps", "mean_draw_probability", "draw_probability_std",
        "draw_auc", "draw_pick_rate", "draw_pick_precision", "draw_recall",
    )
    output: dict[str, Any] = {}
    for key in keys:
        left = stage1.get(key)
        right = stage2.get(key)
        output[key] = None if left is None or right is None else float(right) - float(left)
    return output


def grouped_paired(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    output: dict[str, Any] = {}
    for key, subset in sorted(groups.items()):
        s1 = summarize(subset, "stage1_probabilities")
        s2 = summarize(subset, "stage2_probabilities")
        output[key] = {"stage1": s1, "stage2": s2, "delta_stage2_minus_stage1": paired_metric_delta(s1, s2)}
    return output


def date_block_bootstrap_paired(rows: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    """Bootstrap clustered per-row LL(Stage2)-LL(Stage1); negative favors cross-season state."""
    by_date: dict[str, list[float]] = defaultdict(list)
    all_diffs: list[float] = []
    for row in rows:
        diff = log_loss(row["stage2_probabilities"], row["actual"]) - log_loss(row["stage1_probabilities"], row["actual"])
        by_date[row["date"][:10]].append(diff)
        all_diffs.append(diff)
    dates = sorted(by_date)
    observed = statistics.fmean(all_diffs)
    if len(dates) < 2 or resamples <= 0:
        return {"n": len(rows), "date_clusters": len(dates), "point_estimate": observed, "status": "insufficient_clusters"}
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
        pos = q * (len(samples) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return samples[lo]
        w = pos - lo
        return samples[lo] * (1.0 - w) + samples[hi] * w

    return {
        "n": len(rows),
        "date_clusters": len(dates),
        "resamples": resamples,
        "seed": seed,
        "metric": "mean_log_loss_stage2_cross_season_minus_stage1_current_season",
        "point_estimate": observed,
        "ci90": [quantile(0.05), quantile(0.95)],
        "ci95": [quantile(0.025), quantile(0.975)],
        "interpretation": "negative favors cross-season team state",
    }


def state_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    home_raw = [float(row["cross_season_support"]["home_raw_matches"]) for row in rows]
    away_raw = [float(row["cross_season_support"]["away_raw_matches"]) for row in rows]
    home_venue_eff = [float(row["cross_season_support"]["home_venue_effective_matches"]) for row in rows]
    away_venue_eff = [float(row["cross_season_support"]["away_venue_effective_matches"]) for row in rows]
    mu_home_shift = [float(row["stage2_mu_home"]) - float(row["stage1_mu_home"]) for row in rows]
    mu_away_shift = [float(row["stage2_mu_away"]) - float(row["stage1_mu_away"]) for row in rows]

    def basic(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        def q(frac: float) -> float:
            pos = frac * (len(ordered) - 1)
            lo = int(math.floor(pos)); hi = int(math.ceil(pos))
            if lo == hi:
                return ordered[lo]
            w = pos - lo
            return ordered[lo] * (1.0 - w) + ordered[hi] * w
        return {
            "min": min(values), "p10": q(0.10), "median": q(0.50), "p90": q(0.90), "max": max(values),
            "mean": statistics.fmean(values),
        }

    expert_weights = rows[0]["cross_season_support"]["expert_weights"] if rows else []
    return {
        "expert_half_lives_days": list(EXPERT_HALF_LIVES),
        "primary_weighting": "equal_weights_ledger_none",
        "expert_weights": expert_weights,
        "home_raw_matches": basic(home_raw),
        "away_raw_matches": basic(away_raw),
        "home_venue_effective_matches": basic(home_venue_eff),
        "away_venue_effective_matches": basic(away_venue_eff),
        "mu_home_stage2_minus_stage1": basic(mu_home_shift),
        "mu_away_stage2_minus_stage1": basic(mu_away_shift),
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
    s1 = result["stage1_recomputed"]
    s2 = result["stage2_cross_season"]
    d = result["paired_delta_stage2_minus_stage1"]
    b = result["paired_date_block_bootstrap"]
    cov = result["coverage"]
    diag = result["state_diagnostics"]
    lines = [
        "# Base Anatomy Stage 2 — Cross-Season Team State Only",
        "",
        "**Classification:** RESEARCH_ONLY / formal_weight=0 / VIEWED_STAGE2",
        "",
        "## Isolation contract",
        "",
        "Stage 2 changes exactly one component: target-team venue GF/GA state is sourced from the cross-season 45/90/180/360-day state utility with equal expert weights. The Stage-1 current-season league baseline, shrinkage priors and independent-Poisson probability head are unchanged. Direct-T, multi-OU, conditional GD/Beta-Binomial, low-score residuals and OOF matrix calibration remain disabled.",
        "",
        "## Coverage",
        "",
        f"- Frozen benchmark rows: {cov['benchmark_rows']}",
        f"- Exact paired rows: {cov['paired_rows']}",
        f"- Paired coverage: {fmt(cov['paired_coverage_rate'])}",
        f"- Skipped rows: {cov['skipped_rows']}",
        "",
        "## Exact paired metrics",
        "",
        "| metric | Stage 1 current-season Poisson | Stage 2 cross-season Poisson | delta S2-S1 |",
        "|---|---:|---:|---:|",
    ]
    metric_rows = (
        ("accuracy", "accuracy"), ("log loss", "log_loss"), ("Brier", "brier"), ("RPS", "rps"),
        ("draw AUC", "draw_auc"), ("mean p(draw)", "mean_draw_probability"),
        ("draw p std", "draw_probability_std"), ("draw Top-1 rate", "draw_pick_rate"),
        ("draw recall", "draw_recall"),
    )
    for label, key in metric_rows:
        lines.append(f"| {label} | {fmt(s1.get(key))} | {fmt(s2.get(key))} | {fmt(d.get(key))} |")
    lines.extend([
        "",
        f"- Stage 1 Top-1 H/D/A: {s1.get('top1_counts')}",
        f"- Stage 2 Top-1 H/D/A: {s2.get('top1_counts')}",
        f"- Actual H/D/A: {s2.get('actual_counts')}",
        "",
        "## Paired date-block bootstrap",
        "",
        "Primary contrast is per-row LL(Stage 2) - LL(Stage 1); **negative favors cross-season state**.",
        "",
        f"- Point estimate: {fmt(b.get('point_estimate'))}",
        f"- 90% CI: {b.get('ci90')}",
        f"- 95% CI: {b.get('ci95')}",
        f"- Date clusters: {b.get('date_clusters')}",
        "",
        "## Cross-season support diagnostics",
        "",
        f"- Expert half-lives: {diag.get('expert_half_lives_days')}",
        f"- Primary expert weights: {diag.get('expert_weights')}",
        f"- Home raw historical matches: {diag.get('home_raw_matches')}",
        f"- Away raw historical matches: {diag.get('away_raw_matches')}",
        f"- Home venue effective matches: {diag.get('home_venue_effective_matches')}",
        f"- Away venue effective matches: {diag.get('away_venue_effective_matches')}",
        f"- mu_home shift S2-S1: {diag.get('mu_home_stage2_minus_stage1')}",
        f"- mu_away shift S2-S1: {diag.get('mu_away_stage2_minus_stage1')}",
        "",
        "## Draw calibration — Stage 2",
        "",
        "| p(draw) bin | n | mean predicted | actual draw | gap |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in result["stage2_draw_calibration"]:
        lines.append(
            f"| [{row['low']:.2f}, {row['high']:.2f}) | {row['n']} | {row['mean_predicted_draw']:.4f} | {row['actual_draw_rate']:.4f} | {row['calibration_gap_pred_minus_actual']:+.4f} |"
        )
    lines.extend([
        "",
        "## By competition",
        "",
        "| competition | n | LL S1 | LL S2 | dLL | draw AUC S1 | draw AUC S2 | D picks S1 | D picks S2 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for competition, block in result["by_competition"].items():
        a = block["stage1"]; c = block["stage2"]; delta = block["delta_stage2_minus_stage1"]
        lines.append(
            f"| {competition} | {a.get('n',0)} | {fmt(a.get('log_loss'),4)} | {fmt(c.get('log_loss'),4)} | {fmt(delta.get('log_loss'),4)} | {fmt(a.get('draw_auc'),4)} | {fmt(c.get('draw_auc'),4)} | {a.get('top1_counts',{}).get('draw',0)} | {c.get('top1_counts',{}).get('draw',0)} |"
        )
    lines.extend([
        "",
        "## Boundary",
        "",
        "This is a controlled Stage-2 state-source ablation, not a promotion claim. Equal expert weights are intentional so adaptive Hedge learning is not smuggled in as a second intervention. Stage 3 remains locked to Direct-T only.",
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
    paired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()
    parameter_sources: Counter[str] = Counter()

    for index, row in enumerate(rows):
        competition_id = str(row["competition_id"])
        season = str(row["season"])
        try:
            cutoff = parse_iso_datetime(str(row["date"]), "benchmark.row.date")
            if competition_id not in match_cache:
                match_cache[competition_id] = read_processed_matches(competition_id)
            all_matches = match_cache[competition_id]
            _, current_history = current_season_history(all_matches, cutoff, season=season)
            params, parameter_source = point_in_time_parameters(competition_id, season, config)
            current_state = fit_current_season_state(current_history, cutoff, params, config)

            # Stage 1 is recomputed in-process to guarantee exact pairing.
            s1_signals = expected_goals(current_state, str(row["home_team"]), str(row["away_team"]), params, config)
            s1_mu_home = float(s1_signals["home_score_signal"])
            s1_mu_away = float(s1_signals["away_score_signal"])
            s1 = independent_poisson_1x2(s1_mu_home, s1_mu_away, max_goals=args.max_goals)["probabilities"]

            # Stage 2 changes the state source only.
            s2_signals = cross_season_score_signals(
                all_matches, cutoff, str(row["home_team"]), str(row["away_team"]), current_state, params
            )
            s2_mu_home = float(s2_signals["mu_home"])
            s2_mu_away = float(s2_signals["mu_away"])
            s2 = independent_poisson_1x2(s2_mu_home, s2_mu_away, max_goals=args.max_goals)["probabilities"]
            actual = str(row["actual"])
            if actual not in OUTCOMES:
                raise PlatformError(f"unexpected actual outcome: {actual!r}")

            home_state = s2_signals["home_state"]
            away_state = s2_signals["away_state"]
            parameter_sources[parameter_source] += 1
            paired.append({
                "benchmark_index": index,
                "competition_id": competition_id,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "actual": actual,
                "stage1_mu_home": s1_mu_home,
                "stage1_mu_away": s1_mu_away,
                "stage2_mu_home": s2_mu_home,
                "stage2_mu_away": s2_mu_away,
                "stage1_probabilities": s1,
                "stage2_probabilities": s2,
                "row_delta": {
                    "log_loss_stage2_minus_stage1": log_loss(s2, actual) - log_loss(s1, actual),
                    "brier_stage2_minus_stage1": multiclass_brier(s2, actual) - multiclass_brier(s1, actual),
                    "rps_stage2_minus_stage1": rps(s2, actual) - rps(s1, actual),
                    "stage1_pick": argmax_label(s1),
                    "stage2_pick": argmax_label(s2),
                },
                "cross_season_support": {
                    "expert_half_lives_days": list(home_state["expert_half_lives_days"]),
                    "expert_weights": list(home_state["expert_weights"]),
                    "home_raw_matches": float(home_state["mixed"]["raw_matches"]),
                    "away_raw_matches": float(away_state["mixed"]["raw_matches"]),
                    "home_venue_effective_matches": float(home_state["mixed"]["home_matches"]),
                    "away_venue_effective_matches": float(away_state["mixed"]["away_matches"]),
                    "home_history_rows": int(s2_signals["history_rows"]),
                    "normalized_home_token": s2_signals["home_team_token"],
                    "normalized_away_token": s2_signals["away_team_token"],
                },
                "parameter_source": parameter_source,
                "current_season_history_matches": len(current_history),
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

    if not paired:
        raise PlatformError("Stage-2 produced zero exact paired rows")

    stage1 = summarize(paired, "stage1_probabilities")
    stage2 = summarize(paired, "stage2_probabilities")
    result = {
        "schema_version": "base-anatomy-cross-season-r1",
        "status": "RESEARCH_ONLY_VIEWED_STAGE2",
        "classification": "RETROSPECTIVE_RESEARCH_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "stage": 2,
        "stage_name": "cross_season_team_state_only",
        "fixed_anatomy_order": [
            "old_formal_poisson_bottom",
            "cross_season_team_state",
            "direct_t",
            "multi_ou",
            "conditional_goal_difference",
            "unified_score_matrix",
        ],
        "isolation_contract": {
            "stage1_current_season_pit_state": True,
            "stage2_cross_season_team_state": True,
            "stage2_equal_expert_weights_ledger_none": True,
            "same_current_season_league_baseline": True,
            "same_formal_team_prior_shrinkage": True,
            "same_independent_poisson_probability_head": True,
            "team_name_normalization": "formal_normalize_team_token",
            "adaptive_hedge_weighting": False,
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
            "paired_rows": len(paired),
            "skipped_rows": len(skipped),
            "paired_coverage_rate": len(paired) / len(rows),
            "parameter_sources": dict(parameter_sources),
        },
        "stage1_recomputed": stage1,
        "stage2_cross_season": stage2,
        "paired_delta_stage2_minus_stage1": paired_metric_delta(stage1, stage2),
        "paired_date_block_bootstrap": date_block_bootstrap_paired(paired, args.bootstrap_resamples, args.bootstrap_seed),
        "stage2_draw_calibration": calibration(paired, "stage2_probabilities"),
        "state_diagnostics": state_diagnostics(paired),
        "by_competition": grouped_paired(paired, "competition_id"),
        "by_season": grouped_paired(paired, "season"),
        "skipped_reason_counts": dict(skipped_reasons.most_common()),
        "skipped_rows": skipped,
        "paired_predictions": paired,
        "next_stage_locked": "direct_t_only",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "cross_season_state_r1.json"
    md_path = args.output_dir / "cross_season_state_r1.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    markdown = render_markdown(result)
    md_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"\nJSON={json_path.relative_to(REPO_ROOT)}")
    print(f"MD={md_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
