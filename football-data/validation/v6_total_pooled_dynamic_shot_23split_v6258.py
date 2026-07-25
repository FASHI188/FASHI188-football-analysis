#!/usr/bin/env python3
"""V6.25.8 pooled cross-domain dynamic-shot 2-vs-3 split challenger.

Research only; formal_weight=0.

Per-domain shot residual heads in V6.25.6/7 were unstable because each league
had only hundreds of strict-PIT training rows. V6.25.8 pools the already
league-relative dynamic shot features across all shot-covered domains while
keeping each match's frozen baseline distribution as its probability offset.

One global ridge logistic residual models P(T=2 | T in {2,3}). Nested validation
is still chronological inside every domain:
- validation fold index i uses that domain's prior season i;
- training uses only earlier prior seasons 0..i-1 from all domains;
- no target-season row enters fitting or alpha selection.

Alpha is selected globally from a fixed grid. It must have pooled nested-OOS
Total RPS no worse than alpha=0; among eligible alphas, exact-total log loss is
minimized. Alpha=0 exactly recovers the baseline.

Only P2/P3 split changes; P2+P3 and every other total bucket stay fixed, and
P(score|T) remains unchanged in the joint matrix.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_shot_dynamic_23split_v6257 as dynamic  # noqa: E402,F401
import v6_total_shot_23split_v6256 as base  # noqa: E402
from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import _merge_parameters, load_config  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402
from v6_total_shot_feature_offset_v6253 import MIN_SHOT_COVERAGE, _read_stat_rows  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_pooled_dynamic_shot_23split_v6258_status.json"


def _domain_context(cid: str) -> dict[str, Any] | None:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    stats, coverage = _read_stat_rows(cid)
    if float(coverage["coverage"]) < MIN_SHOT_COVERAGE:
        return None
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    selected = target_fold.get("selected_parameters")
    if not isinstance(selected, dict):
        return None
    return {
        "cid": cid,
        "config": config,
        "report": report,
        "stats": stats,
        "coverage": coverage,
        "target_season": target_season,
        "target_fold": target_fold,
        "target_params": _merge_parameters(config, selected),
        "prior": [str(s) for s in (target_fold.get("prior_seasons") or [])],
    }


def _load(ctx: dict[str, Any], season: str) -> list[dict[str, Any]]:
    try:
        fold = _fold_for_season(ctx["report"], season)
    except Exception:
        return []
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        return []
    return dynamic._build_rows_dynamic(
        ctx["cid"],
        season,
        _merge_parameters(ctx["config"], selected),
        ctx["config"],
        ctx["stats"],
    )


def _select_global_alpha(contexts: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    scores = {
        alpha: {"rps_sum": 0.0, "log_sum": 0.0, "count": 0, "folds": []}
        for alpha in base.ALPHAS
    }
    max_prior = max((len(ctx["prior"]) for ctx in contexts), default=0)
    for idx in range(1, max_prior):
        training: list[dict[str, Any]] = []
        validation: list[dict[str, Any]] = []
        fold_domains: list[str] = []
        for ctx in contexts:
            prior = ctx["prior"]
            if idx >= len(prior):
                continue
            for season in prior[:idx]:
                training.extend(_load(ctx, season))
            rows = _load(ctx, prior[idx])
            if rows:
                validation.extend(rows)
                fold_domains.append(ctx["cid"])
        if not validation:
            continue
        try:
            model = base._fit_model(training)
        except Exception:
            continue
        for alpha in base.ALPHAS:
            rows = base._candidate_rows(validation, model, alpha)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            logloss = base._total_log_loss(rows, "candidate_matrix")
            scores[alpha]["rps_sum"] += rps * n
            scores[alpha]["log_sum"] += logloss * n
            scores[alpha]["count"] += n
            scores[alpha]["folds"].append({
                "fold_index": idx,
                "domains": list(fold_domains),
                "training_count": len(training),
                "validation_count": n,
                "mean_total_rps": rps,
                "mean_total_log_loss": logloss,
            })
    for alpha in base.ALPHAS:
        n = int(scores[alpha]["count"])
        scores[alpha]["mean_rps"] = scores[alpha]["rps_sum"] / n if n else None
        scores[alpha]["mean_log_loss"] = scores[alpha]["log_sum"] / n if n else None
    baseline_rps = scores[0.0]["mean_rps"]
    if baseline_rps is None:
        return 0.0, {"fallback": "insufficient_pooled_nested_folds", "alpha_scores": scores}
    eligible: list[tuple[float, float]] = []
    for alpha in base.ALPHAS:
        rps = scores[alpha]["mean_rps"]
        logloss = scores[alpha]["mean_log_loss"]
        if rps is not None and logloss is not None and float(rps) <= float(baseline_rps) + base.RPS_TOLERANCE:
            eligible.append((float(logloss), float(alpha)))
    if not eligible:
        return 0.0, {"fallback": "no_pooled_rps_nonworse_alpha", "alpha_scores": scores}
    best_log, best_alpha = min(eligible, key=lambda item: (item[0], item[1]))
    return best_alpha, {
        "selected_alpha": best_alpha,
        "selected_mean_log_loss": best_log,
        "baseline_mean_rps": baseline_rps,
        "selection_rule": "pooled nested-OOS RPS nonworse, then minimum exact-total log loss; tie -> smaller alpha",
        "alpha_scores": scores,
    }


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    contexts: list[dict[str, Any]] = []
    unavailable: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in competitions:
        try:
            ctx = _domain_context(cid)
            if ctx is None:
                unavailable[cid] = "shot_coverage_or_target_fold_unavailable"
            else:
                contexts.append(ctx)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not contexts:
        raise PlatformError("no shot-covered pooled domains")

    alpha, selection = _select_global_alpha(contexts)
    training: list[dict[str, Any]] = []
    target_pool: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}
    for ctx in contexts:
        domain_training: list[dict[str, Any]] = []
        training_seasons: list[str] = []
        for season in ctx["prior"]:
            rows = _load(ctx, season)
            if rows:
                domain_training.extend(rows)
                training_seasons.append(season)
        training.extend(domain_training)
        target = dynamic._build_rows_dynamic(
            ctx["cid"], ctx["target_season"], ctx["target_params"], ctx["config"], ctx["stats"]
        )
        reports[ctx["cid"]] = {
            "coverage": ctx["coverage"],
            "target_season": ctx["target_season"],
            "training_seasons": training_seasons,
            "training_prediction_count": len(domain_training),
            "target_prediction_count": len(target),
        }
        target_pool.extend(target)

    model = base._fit_model(training) if alpha > 0.0 else None
    candidate_pool = base._candidate_rows(target_pool, model, alpha)
    full_base = _score(candidate_pool, "baseline_matrix")
    full_candidate = _score(candidate_pool, "candidate_matrix")
    sample_n = min(base.SAMPLE_N, len(candidate_pool))
    sampled = random.Random(base.SEED).sample(candidate_pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.8-pooled-dynamic-shot-2v3-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_POOLED_STRICT_PIT_DYNAMIC_SHOT_2V3_FORMAL_WEIGHT_0",
        "applied_domains": [ctx["cid"] for ctx in contexts],
        "applied_domain_count": len(contexts),
        "eligible_target_pool_count": len(candidate_pool),
        "training_prediction_count": len(training),
        "selected_alpha": alpha,
        "selection": selection,
        "full_pool": {
            "baseline": full_base,
            "candidate": full_candidate,
            "baseline_total_log_loss": base._total_log_loss(candidate_pool, "baseline_matrix"),
            "candidate_total_log_loss": base._total_log_loss(candidate_pool, "candidate_matrix"),
            "delta": base._delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(candidate_pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(candidate_pool, "candidate_matrix"),
        },
        "random100": {
            "seed": base.SEED,
            "count": sample_n,
            "baseline": sample_base,
            "candidate": sample_candidate,
            "baseline_total_log_loss": base._total_log_loss(sampled, "baseline_matrix"),
            "candidate_total_log_loss": base._total_log_loss(sampled, "candidate_matrix"),
            "delta": base._delta(sample_base, sample_candidate),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "unavailable_domains": unavailable,
        "failures": failures,
        "governance": {
            "pooled_training_across_shot_domains": True,
            "domain_baseline_distribution_remains_probability_offset": True,
            "nested_validation_strictly_prior_inside_each_domain": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_zero_exact_baseline_fallback": True,
            "alpha_selection_requires_nested_rps_nonworse": True,
            "only_2_and_3_bucket_split_changed": True,
            "combined_p2_p3_mass_preserved": True,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "historical_market_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "applied_domain_count", "eligible_target_pool_count", "training_prediction_count",
        "selected_alpha", "selection", "full_pool", "random100", "failures"
    )}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
