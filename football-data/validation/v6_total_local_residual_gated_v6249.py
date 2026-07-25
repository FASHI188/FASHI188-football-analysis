#!/usr/bin/env python3
"""V6.24.9 nested-OOS gated feature-conditioned total residual head.

Research only; formal_weight=0.

V6.24.8 showed that feature-conditioned local residuals diversify the exact
total mode but can over-correct. V6.24.9 keeps the same pre-match feature and
nearest-neighbor construction, but chooses a log-factor shrinkage alpha per
competition using only nested prior-season OOS folds.

Candidate alpha grid is fixed ex ante:
    0.00, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00
For each validation season strictly earlier than the target season, the local
residual learner is trained only on seasons earlier than that validation season.
The alpha minimizing pooled total-goal RPS is selected. Ties prefer smaller alpha.
Alpha=0 exactly recovers the baseline total marginal.

No target-season outcome is used for alpha selection or local fitting.
"""
from __future__ import annotations

import json
import math
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

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import _merge_parameters, load_config  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _calibrate_matrix, _score, _top1_counts  # noqa: E402
from v6_total_local_residual_knn_v6248 import (  # noqa: E402
    _distance,
    _local_factors,
    _season_rows,
    _standardizer,
    _z,
)

OUT = ROOT / "manifests" / "v6_total_local_residual_gated_v6249_status.json"
SEED = 20260725
SAMPLE_N = 100
ALPHAS = (0.0, 0.10, 0.20, 0.35, 0.50, 0.75, 1.0)


def _attenuate(factors: dict[str, float], alpha: float) -> dict[str, float]:
    a = max(0.0, min(1.0, float(alpha)))
    return {key: math.exp(a * math.log(max(1e-12, float(value)))) for key, value in factors.items()}


def _prepare_local_factors(
    training_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Compute nearest neighbors once; alpha candidates reuse the raw factors."""
    if not training_rows:
        raise PlatformError("no local residual training rows")
    means, sds = _standardizer(training_rows)
    train: list[dict[str, Any]] = []
    for row in training_rows:
        item = dict(row)
        item["z"] = _z(item["feature"], means, sds)
        train.append(item)
    k = max(1, int(round(math.sqrt(len(train)))))
    prepared: list[dict[str, Any]] = []
    for row in validation_rows:
        z = _z(row["feature"], means, sds)
        nearest = sorted(train, key=lambda tr: _distance(z, tr["z"]))[:k]
        prepared.append({"row": row, "raw_factors": _local_factors(nearest)})
    return prepared, k


def _rows_from_prepared(
    prepared: list[dict[str, Any]],
    k: int,
    alpha: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in prepared:
        row = item["row"]
        factors = _attenuate(item["raw_factors"], alpha)
        output.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": row["matrix"] if float(alpha) == 0.0 else _calibrate_matrix(row["matrix"], factors),
            "local_k": k,
            "alpha": float(alpha),
        })
    return output


def _build_local_rows(
    training_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    alpha: float,
) -> list[dict[str, Any]]:
    prepared, k = _prepare_local_factors(training_rows, validation_rows)
    return _rows_from_prepared(prepared, k, alpha)


def _load_season_rows(cid: str, season: str, report: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    params = _merge_parameters(config, selected)
    rows, _ = _season_rows(cid, season, params, config)
    return rows


def _select_alpha(cid: str, target_fold: dict[str, Any], report: dict[str, Any], config: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    prior = [str(s) for s in (target_fold.get("prior_seasons") or [])]
    scores: dict[float, dict[str, Any]] = {a: {"rps_sum": 0.0, "count": 0, "folds": []} for a in ALPHAS}

    for idx in range(1, len(prior)):
        validation_season = prior[idx]
        training_seasons = prior[:idx]
        training_rows: list[dict[str, Any]] = []
        for season in training_seasons:
            try:
                training_rows.extend(_load_season_rows(cid, season, report, config))
            except Exception:
                continue
        try:
            validation_rows = _load_season_rows(cid, validation_season, report, config)
        except Exception:
            continue
        if not training_rows or not validation_rows:
            continue
        prepared, k = _prepare_local_factors(training_rows, validation_rows)
        for alpha in ALPHAS:
            rows = _rows_from_prepared(prepared, k, alpha)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            scores[alpha]["rps_sum"] += rps * n
            scores[alpha]["count"] += n
            scores[alpha]["folds"].append({
                "validation_season": validation_season,
                "training_seasons": list(training_seasons),
                "count": n,
                "local_k": k,
                "mean_total_rps": rps,
            })

    eligible = []
    for alpha in ALPHAS:
        count = int(scores[alpha]["count"])
        mean_rps = scores[alpha]["rps_sum"] / count if count else None
        scores[alpha]["mean_rps"] = mean_rps
        if mean_rps is not None:
            eligible.append((float(mean_rps), float(alpha)))
    if not eligible:
        return 0.0, {"fallback": "no_nested_oos_folds", "alpha_scores": scores}
    best_rps, best_alpha = min(eligible, key=lambda item: (item[0], item[1]))
    return best_alpha, {
        "selected_alpha": best_alpha,
        "selected_mean_rps": best_rps,
        "selection_rule": "minimum pooled nested-OOS total RPS; tie -> smaller alpha",
        "alpha_scores": scores,
        "neighbor_search_reused_across_alpha_grid": True,
    }


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError(f"invalid target parameters for {cid} {target_season}")
    target_params = _merge_parameters(config, target_selected)

    alpha, selection = _select_alpha(cid, target_fold, report, config)
    training_rows: list[dict[str, Any]] = []
    training_seasons: list[str] = []
    for season in target_fold.get("prior_seasons") or []:
        try:
            rows = _load_season_rows(cid, str(season), report, config)
        except Exception:
            continue
        if rows:
            training_rows.extend(rows)
            training_seasons.append(str(season))
    target_raw, skips = _season_rows(cid, target_season, target_params, config)
    if alpha <= 0.0:
        target_rows = [{
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": row["matrix"],
            "local_k": 0,
            "alpha": 0.0,
        } for row in target_raw]
    else:
        target_rows = _build_local_rows(training_rows, target_raw, alpha)

    base = _score(target_rows, "baseline_matrix")
    cand = _score(target_rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "target_season": target_season,
        "training_seasons": training_seasons,
        "training_prediction_count": len(training_rows),
        "selected_alpha": alpha,
        "selection": selection,
        "baseline": base,
        "candidate": cand,
        "delta": _delta(base, cand),
        "baseline_top1_bucket_counts": _top1_counts(target_rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(target_rows, "candidate_matrix"),
        "target_skips": dict(skips),
    }, target_rows


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    alpha_counts = Counter()
    for cid in competitions:
        try:
            result, rows = _domain(cid)
            reports[cid] = result
            pool.extend(rows)
            alpha_counts[str(result["selected_alpha"])] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError("insufficient pooled target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.24.9-total-local-residual-nested-oos-gated-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_NESTED_OOS_GATED_FORMAL_WEIGHT_0",
        "eligible_target_pool_count": len(pool),
        "alpha_grid": list(ALPHAS),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "full_pool": {
            "baseline": full_base,
            "candidate": full_cand,
            "delta": _delta(full_base, full_cand),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "baseline": sample_base,
            "candidate": sample_cand,
            "delta": _delta(sample_base, sample_cand),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "target_season_results_used_for_training_or_alpha_selection": False,
            "alpha_selected_nested_oos_prior_seasons_only": True,
            "alpha_zero_exact_baseline_fallback": True,
            "training_predictions_strict_pit": True,
            "feature_values_pre_match_only": True,
            "neighbor_search_reused_across_alpha_grid": True,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "historical_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "eligible_target_pool_count": len(pool),
        "selected_alpha_domain_counts": payload["selected_alpha_domain_counts"],
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
