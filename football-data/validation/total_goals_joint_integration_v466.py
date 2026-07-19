#!/usr/bin/env python3
"""Unified-matrix promotion gate for direct-total challengers.

Research-only.  This module takes the direct P(T) winners from V4.6.4 round 1
and V4.6.5 round 2, rebuilds the score matrix by replacing only the 0..7+
total marginal while freezing the current formal core conditional score
allocation P(H,A|T), and evaluates the resulting joint distribution on the
same strictly out-of-sample rolling windows.

No passing result changes formal weights automatically.  A CURRENT-compliant
promotion decision is still required after this gate.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

VALIDATION_DIR = Path(__file__).resolve().parent
ENGINE_DIR = VALIDATION_DIR.parents[0] / "engine"
for item in (str(VALIDATION_DIR), str(ENGINE_DIR)):
    if item not in sys.path:
        sys.path.insert(0, item)

import total_goals_dynamic_challenger_v464 as tg1  # noqa: E402
import total_goals_round2_v465 as tg2  # noqa: E402
from football_v460_engine import predict_from_history  # noqa: E402
from platform_core import ROOT, PlatformError, atomic_write_json, load_json, read_processed_matches, sha256_file, utc_now  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve()
REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_joint_integration_v466"
MANIFEST_PATH = ROOT / "manifests" / "total_goals_joint_integration_v466_status.json"
ROUND1_MANIFEST = ROOT / "manifests" / "total_goals_dynamic_v464_status.json"
ROUND2_MANIFEST = ROOT / "manifests" / "total_goals_round2_v465_status.json"
FORMAL_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
ROUND1_REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_dynamic_v464"
ROUND2_REPORT_ROOT = ROOT / "validation" / "reports" / "total_goals_round2_v465"
POLICY_PATH = ROOT / "validation" / "promotion_policy.json"
CURRENT_REPLAY_MANIFEST = ROOT / "manifests" / "final_chain_replay_v463_status.json"
TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
BOOTSTRAP_RESAMPLES = 2000
SEED = 466


def _winners() -> dict[str, str]:
    r1 = load_json(ROUND1_MANIFEST)
    r2 = load_json(ROUND2_MANIFEST)
    output: dict[str, str] = {}
    for cid, item in (r1.get("reports") or {}).items():
        if item.get("status") == "TOTAL_GOALS_CHALLENGER_PASS":
            output[cid] = "round1"
    for cid, item in (r2.get("reports") or {}).items():
        if item.get("status") == "TOTAL_GOALS_ROUND2_PASS":
            output[cid] = "round2"
    return output


def _source_report(cid: str, source: str) -> dict[str, Any]:
    root = ROUND1_REPORT_ROOT if source == "round1" else ROUND2_REPORT_ROOT
    return load_json(root / f"{cid}.json")


def _formal_parameters_by_season(cid: str) -> dict[str, dict[str, Any]]:
    report = load_json(FORMAL_REPORT_ROOT / f"{cid}.json")
    result = {}
    for fold in report.get("folds") or []:
        if fold.get("outer_season") and isinstance(fold.get("selected_parameters"), dict):
            result[str(fold["outer_season"])] = dict(fold["selected_parameters"])
    return result


def _aggregate_total_bins(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in TOTAL_KEYS}
    for cell in matrix:
        total = int(cell["home_goals"]) + int(cell["away_goals"])
        key = str(total) if total <= 6 else "7+"
        out[key] += float(cell["probability"])
    return out


def _one_x_two(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for cell in matrix:
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        out["home" if h > a else "away" if h < a else "draw"] += p
    return out


def _replace_total_marginal(base_matrix: list[dict[str, Any]], target: dict[str, float]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in base_matrix:
        grouped[int(cell["home_goals"]) + int(cell["away_goals"])].append(cell)
    base_total = {total: sum(float(c["probability"]) for c in cells) for total, cells in grouped.items()}
    tail_totals = sorted(total for total in grouped if total >= 7)
    tail_base = sum(base_total[t] for t in tail_totals)
    if tail_base <= 0:
        raise PlatformError("base matrix has no 7+ tail mass")

    exact_mass: dict[int, float] = {}
    for total in sorted(grouped):
        if total <= 6:
            exact_mass[total] = float(target[str(total)])
        else:
            exact_mass[total] = float(target["7+"]) * base_total[total] / tail_base

    output = []
    for total, cells in sorted(grouped.items()):
        denominator = base_total[total]
        allocated_mass = exact_mass[total]
        if denominator <= 0:
            if allocated_mass <= 1e-15:
                continue
            raise PlatformError(f"positive target mass has zero conditional support at total={total}")
        for cell in cells:
            output.append({
                "home_goals": int(cell["home_goals"]),
                "away_goals": int(cell["away_goals"]),
                "probability": exact_mass[total] * float(cell["probability"]) / denominator,
            })
    norm = sum(float(c["probability"]) for c in output)
    if norm <= 0 or not math.isfinite(norm):
        raise PlatformError("integrated matrix normalization failed")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / norm
    return output


def _score_probability(matrix: list[dict[str, Any]], home: int, away: int) -> float:
    for cell in matrix:
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            return float(cell["probability"])
    return 1e-15


def _brier_1x2(p: dict[str, float], actual: str) -> float:
    return sum((float(p[key]) - (1.0 if key == actual else 0.0)) ** 2 for key in ("home", "draw", "away"))


def _rps_1x2(p: dict[str, float], actual: str) -> float:
    order = ("home", "draw", "away")
    actual_index = order.index(actual)
    cp = 0.0
    co = 0.0
    score = 0.0
    for index in range(2):
        cp += float(p[order[index]])
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / 2.0


def _rps_total(p: dict[str, float], actual_total: int) -> float:
    actual_index = min(int(actual_total), 7)
    cp = 0.0
    co = 0.0
    score = 0.0
    for index, key in enumerate(TOTAL_KEYS[:-1]):
        cp += float(p[key])
        co += 1.0 if actual_index == index else 0.0
        score += (cp - co) ** 2
    return score / 7.0


def _score_set_hit(matrix: list[dict[str, Any]], target: float, home: int, away: int) -> bool:
    ranking = sorted(
        matrix,
        key=lambda cell: (
            -float(cell["probability"]),
            int(cell["home_goals"]),
            int(cell["away_goals"]),
        ),
    )
    cumulative = 0.0
    hit = False
    for cell in ranking:
        cumulative += float(cell["probability"])
        if int(cell["home_goals"]) == home and int(cell["away_goals"]) == away:
            hit = True
        if cumulative + 1e-12 >= target:
            return hit
    return hit


def _record_metrics(matrix: list[dict[str, Any]], home: int, away: int) -> dict[str, float]:
    one = _one_x_two(matrix)
    totals = _aggregate_total_bins(matrix)
    actual = "home" if home > away else "away" if home < away else "draw"
    total = home + away
    return {
        "joint_log_score": -math.log(max(1e-15, _score_probability(matrix, home, away))),
        "one_x_two_brier": _brier_1x2(one, actual),
        "one_x_two_rps": _rps_1x2(one, actual),
        "total_goals_rps": _rps_total(totals, total),
        "tail4_pred": sum(float(totals[k]) for k in ("4", "5", "6", "7+")),
        "tail4_actual": 1.0 if total >= 4 else 0.0,
        "tail5_pred": sum(float(totals[k]) for k in ("5", "6", "7+")),
        "tail5_actual": 1.0 if total >= 5 else 0.0,
        "score80_hit": 1.0 if _score_set_hit(matrix, 0.80, home, away) else 0.0,
        "score90_hit": 1.0 if _score_set_hit(matrix, 0.90, home, away) else 0.0,
    }


def _bootstrap(records: list[dict[str, Any]], metric: str, seed: int) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        blocks[str(record["block_id"])].append(record)
    values = list(blocks.values())
    observed = mean(float(r["integrated"][metric]) - float(r["current"][metric]) for r in records)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [rng.choice(values) for _ in values]
        flat = [r for block in sample for r in block]
        samples.append(mean(float(r["integrated"][metric]) - float(r["current"][metric]) for r in flat))
    samples.sort()
    return {
        "mean_difference": observed,
        "ci95_lower": samples[max(0, int(0.025 * len(samples)) - 1)],
        "ci95_upper": samples[min(len(samples) - 1, int(0.975 * len(samples)))],
        "blocks": len(values),
    }


def _subprocess_replay(matrix: list[dict[str, Any]], target: dict[str, float]) -> float:
    payload = json.dumps({"matrix": matrix, "target": target}, separators=(",", ":"))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--replay-transform"],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )
    replay = json.loads(proc.stdout)
    rebuilt = _replace_total_marginal(matrix, target)
    left = {(int(c["home_goals"]), int(c["away_goals"])): float(c["probability"]) for c in rebuilt}
    right = {(int(c["home_goals"]), int(c["away_goals"])): float(c["probability"]) for c in replay}
    return max(abs(left[key] - right.get(key, 0.0)) for key in left)


def _build_challenger_records(cid: str, source_report: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], int]:
    matches = read_processed_matches(cid)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[str(match.season)].append(match)
    output: dict[str, dict[str, Any]] = {}
    for fold in source_report.get("folds") or []:
        season = str(fold["outer_season"])
        candidate = dict(fold["selected_candidate"])
        start = str(fold["test_start_date"])
        end = str(fold["test_end_date"])
        rows = tg1._evaluate_season(sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)), candidate, tg1.load_config())
        for row in rows:
            if start <= str(row["date"]) <= end:
                if row["match_key"] in output:
                    raise PlatformError(f"overlapping challenger integration window: {row['match_key']}")
                output[row["match_key"]] = row
    return output, len(source_report.get("folds") or [])


def validate_competition(cid: str, source: str, *, write: bool = True) -> dict[str, Any]:
    source_report = _source_report(cid, source)
    challenger, fold_count = _build_challenger_records(cid, source_report)
    formal_params = _formal_parameters_by_season(cid)
    matches = read_processed_matches(cid)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_season[str(match.season)].append(match)

    records = []
    max_probability_residual = 0.0
    max_total_residual = 0.0
    max_replay_difference = 0.0
    replay_samples = 0
    skipped = 0
    for season, season_matches in sorted(by_season.items()):
        params = formal_params.get(season)
        if params is None:
            continue
        by_date: dict[Any, list[Any]] = defaultdict(list)
        for match in season_matches:
            by_date[match.date].append(match)
        history = []
        sequence = 0
        for date in sorted(by_date):
            for match in sorted(by_date[date], key=lambda x: (x.home_team, x.away_team)):
                key = f"{season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}"
                challenge = challenger.get(key)
                if challenge is None:
                    continue
                try:
                    current = predict_from_history(
                        list(history), cid, season, match.home_team, match.away_team, match.date,
                        selected_parameters=params,
                    )
                except Exception:
                    skipped += 1
                    continue
                base_matrix = current["probabilities"]["score_matrix"]
                target = {key_: float(challenge["probabilities"][key_]) for key_ in TOTAL_KEYS}
                integrated = _replace_total_marginal(base_matrix, target)
                prob_resid = abs(sum(float(c["probability"]) for c in integrated) - 1.0)
                total_bins = _aggregate_total_bins(integrated)
                total_resid = max(abs(float(total_bins[k]) - float(target[k])) for k in TOTAL_KEYS)
                max_probability_residual = max(max_probability_residual, prob_resid)
                max_total_residual = max(max_total_residual, total_resid)
                if replay_samples < 12:
                    max_replay_difference = max(max_replay_difference, _subprocess_replay(base_matrix, target))
                    replay_samples += 1
                records.append({
                    "match_key": key,
                    "block_id": str(challenge.get("block_id") or f"{season}:{sequence // 20}"),
                    "integrated": _record_metrics(integrated, int(match.home_goals), int(match.away_goals)),
                    "current": _record_metrics(base_matrix, int(match.home_goals), int(match.away_goals)),
                })
                sequence += 1
            history.extend(by_date[date])
            history.sort(key=lambda x: (x.date, x.home_team, x.away_team))

    if not records:
        raise PlatformError(f"no paired unified-matrix integration predictions: {cid}")
    policy = load_json(POLICY_PATH)["a_grade_thresholds"]
    ci = {
        metric: _bootstrap(records, metric, SEED + index)
        for index, metric in enumerate(("joint_log_score", "one_x_two_brier", "one_x_two_rps", "total_goals_rps"))
    }
    tail4_error = abs(mean(r["integrated"]["tail4_pred"] for r in records) - mean(r["integrated"]["tail4_actual"] for r in records))
    tail5_error = abs(mean(r["integrated"]["tail5_pred"] for r in records) - mean(r["integrated"]["tail5_actual"] for r in records))
    coverage80 = mean(r["integrated"]["score80_hit"] for r in records)
    coverage90 = mean(r["integrated"]["score90_hit"] for r in records)
    current_replay = load_json(CURRENT_REPLAY_MANIFEST)
    current_replay_pass = ((current_replay.get("reports") or {}).get(cid) or {}).get("status") == "通过"
    checks = {
        "minimum_outer_predictions": len(records) >= int(policy["minimum_outer_predictions"]),
        "minimum_outer_time_folds": fold_count >= int(policy["minimum_outer_time_folds"]),
        "joint_log_score_improves_current_core": float(ci["joint_log_score"]["ci95_upper"]) < 0.0,
        "one_x_two_brier_noninferior": float(ci["one_x_two_brier"]["ci95_upper"]) <= 0.002,
        "one_x_two_rps_noninferior": float(ci["one_x_two_rps"]["ci95_upper"]) <= 0.002,
        "total_goals_rps_improves": float(ci["total_goals_rps"]["ci95_upper"]) <= 0.0,
        "tail4_error": tail4_error <= 0.04,
        "tail5_error": tail5_error <= 0.04,
        "score_set_80_coverage": 0.76 <= coverage80 <= 0.84,
        "score_set_90_coverage": 0.86 <= coverage90 <= 0.94,
        "probability_conservation": max_probability_residual <= 1e-10,
        "total_marginal_preservation": max_total_residual <= 1e-10,
        "current_core_full_final_chain_replay": current_replay_pass,
        "integration_transform_independent_replay": max_replay_difference <= 1e-12 and replay_samples >= 1,
    }
    report = {
        "schema_version": "V4.6.6-joint-integration",
        "generated_at_utc": utc_now(),
        "competition_id": cid,
        "challenger_source": source,
        "formal_weight": 0,
        "paired_predictions": len(records),
        "skipped_unpaired_predictions": skipped,
        "outer_folds": fold_count,
        "paired_bootstrap_differences_vs_current_formal_core": ci,
        "tail_calibration": {"tail4_absolute_error": tail4_error, "tail5_absolute_error": tail5_error},
        "score_set_coverage": {"80": coverage80, "90": coverage90},
        "audit": {
            "max_probability_residual": max_probability_residual,
            "max_0_7plus_total_marginal_residual": max_total_residual,
            "integration_replay_samples": replay_samples,
            "max_integration_replay_difference": max_replay_difference,
            "current_core_final_chain_replay_pass": current_replay_pass,
        },
        "checks": checks,
        "status": "READY_FOR_CURRENT_COMPLIANT_PROMOTION_REVIEW" if all(checks.values()) else "JOINT_INTEGRATION_NOT_PROMOTED",
        "promotion_note": "No automatic formal weight change. Passing this gate only permits a separate CURRENT-compliant promotion review.",
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{cid}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    winners = _winners()
    reports = {}
    failures = []
    for cid, source in sorted(winners.items()):
        try:
            report = validate_competition(cid, source, write=write)
            reports[cid] = {
                "status": report["status"],
                "challenger_source": source,
                "paired_predictions": report["paired_predictions"],
                "outer_folds": report["outer_folds"],
                "checks": report["checks"],
                "joint_log_score_ci95_upper": report["paired_bootstrap_differences_vs_current_formal_core"]["joint_log_score"]["ci95_upper"],
                "one_x_two_brier_ci95_upper": report["paired_bootstrap_differences_vs_current_formal_core"]["one_x_two_brier"]["ci95_upper"],
                "one_x_two_rps_ci95_upper": report["paired_bootstrap_differences_vs_current_formal_core"]["one_x_two_rps"]["ci95_upper"],
                "total_goals_rps_ci95_upper": report["paired_bootstrap_differences_vs_current_formal_core"]["total_goals_rps"]["ci95_upper"],
            }
        except Exception as exc:
            failures.append({"competition_id": cid, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.6-joint-integration",
        "generated_at_utc": utc_now(),
        "competition_count_requested": len(winners),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "ready_for_promotion_review_count": sum(r["status"] == "READY_FOR_CURRENT_COMPLIANT_PROMOTION_REVIEW" for r in reports.values()),
        "reports": reports,
        "failures": failures,
        "formal_weight": 0,
        "implementation_sha256": sha256_file(SCRIPT_PATH),
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"joint integration failed for {len(failures)} domains: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--replay-transform", action="store_true")
    args = parser.parse_args()
    if args.replay_transform:
        payload = json.load(sys.stdin)
        print(json.dumps(_replace_total_marginal(payload["matrix"], payload["target"]), separators=(",", ":")))
        return 0
    try:
        result = run_all(write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
