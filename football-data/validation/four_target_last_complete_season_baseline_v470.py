#!/usr/bin/env python3
"""Four-target descriptive baseline for the last complete season in all formal domains.

Targets:
- 1X2 Top-1
- three-way handicap 1X2 when a real frozen historical integer handicap is available
- exact score Top-1 / Top-3
- exact total-goals Top-1 / Top-2

All model-derived targets come from the same replayed unified score matrix. Historical
handicap results are fail-closed because processed MatchRow does not contain handicap
lines. No line is inferred from a final score or post-match page.

Research / descriptive audit only. No formal probability or weight change.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _actual_result,
    _fold_for_season,
    _one_x_two_brier,
    _one_x_two_rps,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, derive_score_marginals, load_json, read_processed_matches, score_matrix_rows, top_scores

OUT = ROOT / "manifests" / "four_target_last_complete_season_baseline_v470_status.json"
HANDICAP_ROOT = ROOT / "markets" / "historical_three_way_handicap"
WINDOWS = 10


def _wilson_interval(hits: int, n: int, z: float = 1.959963984540054) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    p = hits / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _rolling_summary(values: list[int], window_count: int = WINDOWS) -> dict[str, Any]:
    if not values:
        return {"window_count": 0, "rates": [], "mean": None, "std": None, "min": None, "max": None}
    n = len(values)
    chunks = []
    for i in range(window_count):
        start = round(i * n / window_count)
        end = round((i + 1) * n / window_count)
        if end <= start:
            continue
        chunk = values[start:end]
        chunks.append(sum(chunk) / len(chunk))
    return {
        "window_count": len(chunks),
        "rates": chunks,
        "mean": mean(chunks) if chunks else None,
        "std": pstdev(chunks) if len(chunks) > 1 else 0.0 if chunks else None,
        "min": min(chunks) if chunks else None,
        "max": max(chunks) if chunks else None,
    }


def _total_distribution(matrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for h, a, p in score_matrix_rows(matrix):
        out[h + a] = out.get(h + a, 0.0) + float(p)
    return out


def _load_handicap_rows(competition_id: str, season: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    path = HANDICAP_ROOT / f"{competition_id}.jsonl"
    if not path.exists():
        return {}
    output = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        item = json.loads(raw)
        if str(item.get("season")) != season:
            continue
        required = ("match_date", "home_team", "away_team", "home_handicap", "source", "observed_at", "freeze_at")
        if any(item.get(k) is None for k in required):
            continue
        observed = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00"))
        freeze = datetime.fromisoformat(str(item["freeze_at"]).replace("Z", "+00:00"))
        if observed > freeze:
            continue
        line = float(item["home_handicap"])
        if abs(line - round(line)) > 1e-9:
            continue
        key = (str(item["match_date"])[:10], str(item["home_team"]), str(item["away_team"]))
        output[key] = item
    return output


def _three_way_handicap_probs(matrix, home_handicap: int) -> dict[str, float]:
    out = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for h, a, p in score_matrix_rows(matrix):
        adjusted = h + home_handicap - a
        key = "home" if adjusted > 0 else "draw" if adjusted == 0 else "away"
        out[key] += float(p)
    return out


def _backtest_domain(cid: str) -> dict[str, Any]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    season = _requested_last_complete_season(cid)
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    all_matches = read_processed_matches(cid)
    matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
    if not matches:
        raise PlatformError(f"no matches for {cid} {season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    handicap_rows = _load_handicap_rows(cid, season)

    predicted = skipped = 0
    one_hits = score1_hits = score3_hits = total1_hits = total2_hits = 0
    handicap_n = handicap_hits = 0
    brier_sum = rps_sum = 0.0
    one_seq: list[int] = []
    score_seq: list[int] = []
    total_seq: list[int] = []
    handicap_seq: list[int] = []
    total_pick_counts = Counter()
    total_actual_counts = Counter()
    skip_reasons = Counter()
    max_prob_residual = 0.0

    for match in matches:
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError as exc:
            skipped += 1
            skip_reasons[str(exc)] += 1
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        marginals = derive_score_marginals(matrix)
        max_prob_residual = max(max_prob_residual, abs(float(marginals["probability_sum"]) - 1.0))

        one = marginals["1x2"]
        one_pick = max(("home", "draw", "away"), key=lambda k: float(one[k]))
        actual_result = _actual_result(int(match.home_goals), int(match.away_goals))
        one_hit = int(one_pick == actual_result)
        one_hits += one_hit
        one_seq.append(one_hit)
        brier_sum += _one_x_two_brier(one, actual_result)
        rps_sum += _one_x_two_rps(one, actual_result)

        ranking = top_scores(matrix, 3)
        actual_score = f"{int(match.home_goals)}-{int(match.away_goals)}"
        s1 = int(bool(ranking) and ranking[0]["score"] == actual_score)
        s3 = int(any(item["score"] == actual_score for item in ranking))
        score1_hits += s1
        score3_hits += s3
        score_seq.append(s1)

        total_probs = _total_distribution(matrix)
        ranked_totals = sorted(total_probs.items(), key=lambda kv: (-kv[1], kv[0]))
        actual_total = int(match.home_goals) + int(match.away_goals)
        top_total = ranked_totals[0][0]
        t1 = int(top_total == actual_total)
        t2 = int(actual_total in {item[0] for item in ranked_totals[:2]})
        total1_hits += t1
        total2_hits += t2
        total_seq.append(t1)
        total_pick_counts[str(top_total)] += 1
        total_actual_counts[str(actual_total)] += 1

        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        hrow = handicap_rows.get(key)
        if hrow is not None:
            home_handicap = int(round(float(hrow["home_handicap"])))
            hp = _three_way_handicap_probs(matrix, home_handicap)
            hpick = max(("home", "draw", "away"), key=lambda k: hp[k])
            adjusted = int(match.home_goals) + home_handicap - int(match.away_goals)
            hactual = "home" if adjusted > 0 else "draw" if adjusted == 0 else "away"
            hhit = int(hpick == hactual)
            handicap_n += 1
            handicap_hits += hhit
            handicap_seq.append(hhit)

        predicted += 1

    if predicted <= 0:
        raise PlatformError(f"no eligible predictions for {cid} {season}")

    handicap_status = "AVAILABLE" if handicap_n > 0 else "UNAVAILABLE_NO_FROZEN_HISTORICAL_LINE_DATA"
    return {
        "competition_id": cid,
        "season": season,
        "season_match_count": len(matches),
        "eligible_prediction_count": predicted,
        "coverage_rate": predicted / len(matches),
        "skipped_by_formal_sample_gates": skipped,
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "targets": {
            "one_x_two": {
                "hit_count": one_hits,
                "accuracy": one_hits / predicted,
                "ci95_wilson": _wilson_interval(one_hits, predicted),
                "rolling_10_window": _rolling_summary(one_seq),
                "mean_brier": brier_sum / predicted,
                "mean_rps": rps_sum / predicted,
            },
            "handicap_one_x_two": {
                "status": handicap_status,
                "eligible_with_real_frozen_line": handicap_n,
                "hit_count": handicap_hits if handicap_n else None,
                "accuracy": handicap_hits / handicap_n if handicap_n else None,
                "ci95_wilson": _wilson_interval(handicap_hits, handicap_n),
                "rolling_10_window": _rolling_summary(handicap_seq),
                "hard_gate_reason": None if handicap_n else "No real pre-match frozen integer handicap line dataset is present for this competition-season; final scores cannot be used to infer the line.",
            },
            "exact_score": {
                "top1_hit_count": score1_hits,
                "top1_accuracy": score1_hits / predicted,
                "top1_ci95_wilson": _wilson_interval(score1_hits, predicted),
                "top3_hit_count": score3_hits,
                "top3_accuracy": score3_hits / predicted,
                "rolling_10_window_top1": _rolling_summary(score_seq),
            },
            "total_goals": {
                "top1_hit_count": total1_hits,
                "top1_accuracy": total1_hits / predicted,
                "top1_ci95_wilson": _wilson_interval(total1_hits, predicted),
                "top2_hit_count": total2_hits,
                "top2_accuracy": total2_hits / predicted,
                "rolling_10_window_top1": _rolling_summary(total_seq),
                "predicted_top1_total_counts": dict(total_pick_counts),
                "actual_total_counts": dict(total_actual_counts),
            },
        },
        "audit": {
            "same_unified_matrix_for_one_x_two_score_total": True,
            "handicap_line_inferred_from_result": False,
            "historical_market_coordination_used": False,
            "formal_probability_changed": False,
            "probability_sum_max_residual": max_prob_residual,
            "skip_reason_counts": dict(skip_reasons),
        },
    }


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    reports = {}
    failures = {}
    aggregate = {
        "eligible_predictions": 0,
        "one_x_two_hits": 0,
        "score_top1_hits": 0,
        "score_top3_hits": 0,
        "total_top1_hits": 0,
        "total_top2_hits": 0,
        "handicap_eligible": 0,
        "handicap_hits": 0,
    }
    for cid in competitions:
        try:
            item = _backtest_domain(cid)
            reports[cid] = item
            n = int(item["eligible_prediction_count"])
            aggregate["eligible_predictions"] += n
            aggregate["one_x_two_hits"] += int(item["targets"]["one_x_two"]["hit_count"])
            aggregate["score_top1_hits"] += int(item["targets"]["exact_score"]["top1_hit_count"])
            aggregate["score_top3_hits"] += int(item["targets"]["exact_score"]["top3_hit_count"])
            aggregate["total_top1_hits"] += int(item["targets"]["total_goals"]["top1_hit_count"])
            aggregate["total_top2_hits"] += int(item["targets"]["total_goals"]["top2_hit_count"])
            aggregate["handicap_eligible"] += int(item["targets"]["handicap_one_x_two"]["eligible_with_real_frozen_line"])
            if item["targets"]["handicap_one_x_two"]["hit_count"] is not None:
                aggregate["handicap_hits"] += int(item["targets"]["handicap_one_x_two"]["hit_count"])
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    n = aggregate["eligible_predictions"]
    h_n = aggregate["handicap_eligible"]
    payload = {
        "schema_version": "V4.7.0-four-target-last-complete-season-baseline-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(competitions) and not failures else "PARTIAL",
        "competition_count_requested": len(competitions),
        "competition_count_completed": len(reports),
        "season_policy": {"calendar_year_domains": "2025", "autumn_spring_and_ucl_domains": "2025/26"},
        "aggregate": {
            **aggregate,
            "one_x_two_accuracy": aggregate["one_x_two_hits"] / n if n else None,
            "exact_score_top1_accuracy": aggregate["score_top1_hits"] / n if n else None,
            "exact_score_top3_accuracy": aggregate["score_top3_hits"] / n if n else None,
            "total_goals_top1_accuracy": aggregate["total_top1_hits"] / n if n else None,
            "total_goals_top2_accuracy": aggregate["total_top2_hits"] / n if n else None,
            "handicap_one_x_two_accuracy": aggregate["handicap_hits"] / h_n if h_n else None,
            "handicap_status": "AVAILABLE" if h_n else "UNAVAILABLE_NO_FROZEN_HISTORICAL_LINE_DATA",
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "descriptive_baseline_only": True,
            "formal_weight_change": False,
            "probability_change": False,
            "same_matrix_required_for_one_x_two_score_total": True,
            "handicap_requires_real_frozen_line": True,
            "automatic_promotion": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload["aggregate"], ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
