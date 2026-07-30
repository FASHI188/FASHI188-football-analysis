#!/usr/bin/env python3
"""Research-only D0 diagnostic for full 1X2 behavior of the V4.6.x formal core.

This script does not train or mutate the formal model, config, weights, or CURRENT.
It reuses the existing nested time-ordered validation machinery to recover the
per-match OOS 1X2 probabilities that are not persisted in the formal-core reports,
then writes research diagnostics to an ephemeral output directory.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

HERE = Path(__file__).resolve().parent
FOOTBALL_DIR = HERE.parent
ENGINE_DIR = FOOTBALL_DIR / "engine"
VALIDATION_DIR = FOOTBALL_DIR / "validation"
for path in (str(ENGINE_DIR), str(VALIDATION_DIR), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from football_v460_engine import load_config  # noqa: E402
from nested_backtest_v460 import (  # noqa: E402
    _multiclass_ece,
    _objective,
    _paired_records,
    evaluate_season,
)
from platform_core import MatchRow, ROOT, load_registry, read_processed_matches  # noqa: E402

CLASSES = ("home", "draw", "away")
CLASS_INDEX = {name: index for index, name in enumerate(CLASSES)}
MARKET_REFERENCE_PATH = ROOT / "manifests" / "v6_draw_problem_resolution_v6512_status.json"
DEFAULT_OUTPUT_DIR = ROOT.parent / "artifacts" / "research" / "full_1x2_d0"
EXCLUDED_COMPETITIONS = {
    "USA_MLS": "INACTIVE_STALE_BOUND_ARTIFACT",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _predict_class(record: dict[str, Any]) -> str:
    probs = {name: float(record[f"p_{name}"]) for name in CLASSES}
    return max(CLASSES, key=lambda name: (probs[name], -CLASS_INDEX[name]))


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = min(1.0, max(0.0, q)) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    if not records:
        return {"count": 0}

    confusion = {
        actual: {predicted: 0 for predicted in CLASSES}
        for actual in CLASSES
    }
    predicted_counts = Counter()
    actual_counts = Counter()
    draw_ranks = Counter()
    draw_margins: list[float] = []
    actual_draw_p: list[float] = []
    non_draw_p: list[float] = []

    for record in records:
        actual = str(record["actual_outcome"])
        predicted = _predict_class(record)
        confusion[actual][predicted] += 1
        predicted_counts[predicted] += 1
        actual_counts[actual] += 1

        probs = {name: float(record[f"p_{name}"]) for name in CLASSES}
        ranking = sorted(CLASSES, key=lambda name: (-probs[name], CLASS_INDEX[name]))
        draw_ranks[str(ranking.index("draw") + 1)] += 1
        draw_margins.append(probs["draw"] - max(probs["home"], probs["away"]))
        if actual == "draw":
            actual_draw_p.append(probs["draw"])
        else:
            non_draw_p.append(probs["draw"])

    per_class = {}
    for label in CLASSES:
        tp = confusion[label][label]
        pred_n = predicted_counts[label]
        actual_n = actual_counts[label]
        precision = _safe_div(tp, pred_n)
        recall = _safe_div(tp, actual_n)
        f1 = _safe_div(2.0 * precision * recall, precision + recall)
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "predicted": pred_n,
            "actual": actual_n,
            "true_positive": tp,
        }

    hits = sum(confusion[label][label] for label in CLASSES)
    recalls = [per_class[label]["recall"] for label in CLASSES]
    f1s = [per_class[label]["f1"] for label in CLASSES]
    margins = {
        "mean": mean(draw_margins),
        "median": median(draw_margins),
        "p10": _quantile(draw_margins, 0.10),
        "p25": _quantile(draw_margins, 0.25),
        "p50": _quantile(draw_margins, 0.50),
        "p75": _quantile(draw_margins, 0.75),
        "p90": _quantile(draw_margins, 0.90),
    }

    return {
        "count": count,
        "one_x_two_accuracy": hits / count,
        "balanced_accuracy": mean(recalls),
        "macro_f1": mean(f1s),
        "mean_one_x_two_brier": mean(float(r["one_x_two_brier"]) for r in records),
        "mean_one_x_two_rps": mean(float(r["one_x_two_rps"]) for r in records),
        "one_x_two_ece": _multiclass_ece(records),
        "predicted_counts": {label: predicted_counts[label] for label in CLASSES},
        "actual_counts": {label: actual_counts[label] for label in CLASSES},
        "predicted_shares": {label: predicted_counts[label] / count for label in CLASSES},
        "actual_shares": {label: actual_counts[label] / count for label in CLASSES},
        "confusion_matrix_actual_rows_predicted_columns": confusion,
        "per_class": per_class,
        "draw_probability_rank_counts": {
            "rank1": draw_ranks["1"],
            "rank2": draw_ranks["2"],
            "rank3": draw_ranks["3"],
        },
        "draw_probability_rank_shares": {
            "rank1": draw_ranks["1"] / count,
            "rank2": draw_ranks["2"] / count,
            "rank3": draw_ranks["3"] / count,
        },
        "draw_margin_p_draw_minus_best_home_away": margins,
        "mean_p_draw_when_actual_draw": mean(actual_draw_p) if actual_draw_p else None,
        "mean_p_draw_when_actual_non_draw": mean(non_draw_p) if non_draw_p else None,
    }


def _competition_records(competition_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    config = load_config()
    matches = read_processed_matches(competition_id)
    season_matches: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        season_matches[match.season].append(match)

    seasons = sorted(
        season_matches,
        key=lambda key: min(item.date for item in season_matches[key]),
    )
    candidates = config["candidate_parameters"]
    cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    baseline_cache: dict[str, list[dict[str, Any]]] = {}

    for season in seasons:
        ordered = sorted(
            season_matches[season],
            key=lambda item: (item.date, item.home_team, item.away_team),
        )
        baseline_cache[season] = evaluate_season(
            competition_id,
            ordered,
            config["default_parameters"],
            use_team_effects=False,
        )
        for index, candidate in enumerate(candidates):
            cache[index][season] = evaluate_season(
                competition_id,
                ordered,
                candidate,
                use_team_effects=True,
            )

    model_outer: list[dict[str, Any]] = []
    baseline_outer: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []

    for outer_index in range(1, len(seasons)):
        outer_season = seasons[outer_index]
        prior_seasons = seasons[:outer_index]
        candidate_scores = []
        for index, candidate in enumerate(candidates):
            prior_records = [
                record
                for season in prior_seasons
                for record in cache[index][season]
            ]
            candidate_scores.append(
                (_objective(prior_records), index, candidate, len(prior_records))
            )
        candidate_scores.sort(key=lambda item: (item[0], item[1]))
        _, selected_index, selected_candidate, selection_count = candidate_scores[0]

        model_records = cache[selected_index][outer_season]
        baseline_records = baseline_cache[outer_season]
        pairs = _paired_records(model_records, baseline_records)
        if not pairs:
            continue

        model_part = [pair[0] for pair in pairs]
        baseline_part = [pair[1] for pair in pairs]
        model_outer.extend(model_part)
        baseline_outer.extend(baseline_part)
        folds.append({
            "outer_season": outer_season,
            "prior_seasons": prior_seasons,
            "selected_candidate_index": selected_index,
            "selected_parameters": selected_candidate,
            "selection_prediction_count": selection_count,
            "paired_outer_predictions": len(pairs),
            "model_one_x_two": _diagnostics(model_part),
            "strong_non_market_baseline_one_x_two": _diagnostics(baseline_part),
        })

    return model_outer, baseline_outer, folds


def _market_selector_reference() -> dict[str, Any]:
    if not MARKET_REFERENCE_PATH.is_file():
        return {
            "available": False,
            "path": str(MARKET_REFERENCE_PATH.relative_to(ROOT.parent)),
        }
    payload = _json(MARKET_REFERENCE_PATH)
    return {
        "available": True,
        "path": str(MARKET_REFERENCE_PATH.relative_to(ROOT.parent)),
        "classification": payload.get("classification"),
        "evaluation_set_status": payload.get("evaluation_set_status"),
        "market_full_1x2_reference": payload.get("FULL_1X2"),
        "selector_base_reference": payload.get("SELECTIVE_1X2_BASE"),
        "selector_risk_veto_reference": payload.get("SELECTIVE_1X2_HA_RISK_VETO"),
        "comparability_to_formal_core_oos": "NOT_SAME_EVALUATION_POPULATION",
        "warning": (
            "This retrospective 5110-market reference is not treated as a paired baseline "
            "for the formal-core OOS records. Accuracy differences across these populations "
            "must not be interpreted as model lift."
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    model = aggregate["formal_core_model_one_x_two"]
    baseline = aggregate["strong_non_market_baseline_one_x_two"]
    excluded = report["scope"]["excluded_competitions"]

    lines = [
        "# D0 Full 1X2 Diagnostic",
        "",
        "Research-only. No CURRENT, formal model, formal config, or model-weight mutation.",
        "",
        f"- Repository HEAD: `{report.get('repository_head')}`",
        f"- Outcome scope: {report['scope']['outcome_scope']}",
        f"- Included competitions: {len(report['competitions'])}",
        f"- Excluded competitions: {json.dumps(excluded, ensure_ascii=False)}",
        f"- Paired formal-core OOS predictions: {model['count']}",
        "",
        "## Aggregate formal-core OOS",
        "",
        f"- Full 1X2 accuracy: {model['one_x_two_accuracy']:.6f}",
        f"- Balanced accuracy: {model['balanced_accuracy']:.6f}",
        f"- Macro-F1: {model['macro_f1']:.6f}",
        f"- 1X2 Brier: {model['mean_one_x_two_brier']:.6f}",
        f"- 1X2 RPS: {model['mean_one_x_two_rps']:.6f}",
        f"- Predicted H/D/A: {model['predicted_counts']}",
        f"- Actual H/D/A: {model['actual_counts']}",
        f"- Draw rank shares: {model['draw_probability_rank_shares']}",
        "",
        "## Same-match strong non-market baseline",
        "",
        f"- Full 1X2 accuracy: {baseline['one_x_two_accuracy']:.6f}",
        f"- Balanced accuracy: {baseline['balanced_accuracy']:.6f}",
        f"- Macro-F1: {baseline['macro_f1']:.6f}",
        f"- 1X2 Brier: {baseline['mean_one_x_two_brier']:.6f}",
        f"- 1X2 RPS: {baseline['mean_one_x_two_rps']:.6f}",
        "",
        "## Market / selector reference",
        "",
        "The 5110-match market/selector artifact is retained only as retrospective reference.",
        "It is not the same evaluation population as the formal-core OOS sample, so cross-population accuracy deltas are not model lift.",
        "",
        "## Governance",
        "",
        "- Candidate/research status only.",
        "- Formal weight remains 0 for this diagnostic.",
        "- No automatic promotion.",
        "- Promotion requires Codex acceptance and user approval.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    registry = load_registry()
    registered = [item["competition_id"] for item in registry["competitions"]]
    if args.competition:
        if args.competition not in registered:
            raise SystemExit(f"unknown competition: {args.competition}")
        if args.competition in EXCLUDED_COMPETITIONS:
            raise SystemExit(
                f"competition excluded from research: {args.competition} "
                f"({EXCLUDED_COMPETITIONS[args.competition]})"
            )
        competition_ids = [args.competition]
    else:
        competition_ids = [
            competition_id
            for competition_id in registered
            if competition_id not in EXCLUDED_COMPETITIONS
        ]

    competitions: dict[str, Any] = {}
    all_model: list[dict[str, Any]] = []
    all_baseline: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for competition_id in competition_ids:
        try:
            model_records, baseline_records, folds = _competition_records(competition_id)
            competitions[competition_id] = {
                "paired_outer_predictions": len(model_records),
                "formal_core_model_one_x_two": _diagnostics(model_records),
                "strong_non_market_baseline_one_x_two": _diagnostics(baseline_records),
                "folds": folds,
            }
            for record in model_records:
                all_model.append({**record, "competition_id": competition_id})
            for record in baseline_records:
                all_baseline.append({**record, "competition_id": competition_id})
        except Exception as exc:
            failures.append({
                "competition_id": competition_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    report = {
        "schema_version": "D0-full-1x2-diagnostic-v1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository_head": _git_head(),
        "status": "PASS" if not failures else "PARTIAL",
        "scope": {
            "research_only": True,
            "outcome_scope": "90_minutes_including_stoppage",
            "candidate_or_formal_model_mutation": False,
            "formal_weight_change": False,
            "current_rule_change": False,
            "formal_config_change": False,
            "training_new_model": False,
            "included_competitions": competition_ids,
            "excluded_competitions": EXCLUDED_COMPETITIONS,
        },
        "method": {
            "source": "existing nested_backtest_v460 time-ordered OOS machinery",
            "same_day_leakage_policy": "same-day outcomes withheld until all same-day predictions finish",
            "hyperparameter_selection": "earlier seasons only for each outer season",
            "full_1x2_decision_rule": "argmax(p_home,p_draw,p_away); no abstention",
            "selector_is_not_used_for_formal_core_accuracy": True,
        },
        "aggregate": {
            "formal_core_model_one_x_two": _diagnostics(all_model),
            "strong_non_market_baseline_one_x_two": _diagnostics(all_baseline),
        },
        "competitions": competitions,
        "market_selector_reference": _market_selector_reference(),
        "failures": failures,
        "governance": {
            "candidate_status": "RESEARCH_DIAGNOSTIC_ONLY",
            "formal_model_promotion": False,
            "codex_acceptance_required_for_any_promotion": True,
            "user_approval_required_for_any_promotion": True,
        },
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "full_1x2_diagnostic_v700.json"
    md_path = output_dir / "full_1x2_diagnostic_v700.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")

    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "repository_head": report["repository_head"],
            "included_competitions": len(competitions),
            "excluded_competitions": EXCLUDED_COMPETITIONS,
            "failures": failures,
            "formal_core_model_one_x_two": report["aggregate"]["formal_core_model_one_x_two"],
            "strong_non_market_baseline_one_x_two": report["aggregate"]["strong_non_market_baseline_one_x_two"],
            "market_selector_comparability": report["market_selector_reference"].get("comparability_to_formal_core_oos"),
            "json_report": str(json_path),
            "markdown_report": str(md_path),
        }, ensure_ascii=False, indent=2))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
