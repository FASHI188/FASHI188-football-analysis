#!/usr/bin/env python3
"""World Model V2 frozen retrospective evaluator. Research-only; formal_weight=0."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

HELPER = Path(__file__).resolve().parents[1] / "research" / "world_model_v2"
sys.path.insert(0, str(HELPER))

from wmv2_source import *  # noqa: F403
from wmv2_model import *  # noqa: F403


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    matches, matches_sha, source_counts = load_matches()
    if len(matches) < MIN_USABLE_MATCHES:
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": f"only {len(matches)} match metadata rows",
            "coverage": {"metadata_matches": len(matches), "source_counts": source_counts},
        }

    # Coverage gate uses HEAD only. No event target payload is opened before this passes.
    event_ok, event_missing = event_head_coverage(matches)
    event_rate = event_ok / len(matches)
    if event_rate < MIN_EVENT_SUCCESS_RATE:
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": "event HEAD coverage below frozen threshold before event target access",
            "coverage": {
                "metadata_matches": len(matches),
                "event_head_success_rate": event_rate,
                "event_missing": event_missing,
                "source_counts": source_counts,
            },
        }

    by_date: dict[str, list[MatchMeta]] = defaultdict(list)
    for match in matches:
        by_date[match.match_date].append(match)
    dates = sorted(by_date)

    # Freeze a 45% chronological burn-in using whole calendar-date groups.
    target_burn = math.ceil(len(matches) * 0.45)
    cumulative = 0
    test_start_idx: int | None = None
    for i, date in enumerate(dates):
        if cumulative >= target_burn:
            test_start_idx = i
            break
        cumulative += len(by_date[date])

    if test_start_idx is None or test_start_idx >= len(dates):
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": "could not create frozen 45/55 chronological split",
        }

    test_dates = dates[test_start_idx:]
    test_count = sum(len(by_date[d]) for d in test_dates)
    if test_count < MIN_TEST_MATCHES:
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": f"test matches {test_count} below {MIN_TEST_MATCHES}",
        }

    counts = {d: len(by_date[d]) for d in test_dates}
    fold_map = make_folds(test_dates, counts)
    fold_counts = {
        f: sum(counts[d] for d in test_dates if fold_map[d] == f) for f in range(3)
    }
    if min(fold_counts.values()) < MIN_FOLD_MATCHES:
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": f"fold counts below frozen minimum: {fold_counts}",
        }

    xg_history: list[MatchSummary] = []
    # Each row freezes the marginal means actually used before the realized score:
    # (mu_home, mu_away, home_goals, away_goals)
    theta_history: list[tuple[float, float, int, int]] = []
    results: list[dict[str, Any]] = []
    event_shas: dict[int, str] = {}
    event_failures: list[dict[str, Any]] = []
    theta_path: list[dict[str, Any]] = []

    for date in dates:
        day = sorted(by_date[date], key=lambda m: (m.kick_off, m.match_id))

        # All same-date marginal intensities and theta are frozen from strictly prior dates.
        lambdas = {m.match_id: estimate_lambdas(m, xg_history) for m in day}
        theta = fit_theta(theta_history)
        theta_path.append(
            {
                "match_date": date,
                "prior_score_matches": len(theta_history),
                "theta": float(theta),
                "is_test_date": date in test_dates,
            }
        )

        pending: dict[int, dict[str, Any]] = {}
        if date in test_dates:
            if len(theta_history) < MIN_PRIOR_MATCHES:
                return {
                    "status": "STOP_DATA_COVERAGE",
                    "reason": (
                        f"prior score matches {len(theta_history)} below "
                        f"{MIN_PRIOR_MATCHES} at first/early test date {date}"
                    ),
                }
            for meta in day:
                lh, la = lambdas[meta.match_id]
                pending[meta.match_id] = {
                    "lh": lh,
                    "la": la,
                    "theta": theta,
                    "baseline": baseline_outputs(lh, la),
                    "candidate": candidate_outputs(lh, la, theta),
                }

        # Only after every same-date prediction is frozen do event labels become accessible.
        summaries: dict[int, MatchSummary] = {}
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(day)))) as pool:
            futures = {pool.submit(summarize_events, meta): meta for meta in day}
            for future in as_completed(futures):
                meta = futures[future]
                try:
                    summary = future.result()
                    summaries[meta.match_id] = summary
                    event_shas[meta.match_id] = summary.event_sha256
                except Exception as exc:
                    event_failures.append(
                        {
                            "match_id": meta.match_id,
                            "date": date,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        if len(summaries) != len(day):
            return {
                "status": "STOP_DATA_COVERAGE",
                "reason": (
                    "event download/parse failure after frozen HEAD coverage; "
                    "no partial scientific verdict"
                ),
                "event_failures": event_failures,
            }

        if date in test_dates:
            for meta in day:
                summary = summaries[meta.match_id]
                pred = pending[meta.match_id]
                hg, ag = summary.home_goals, summary.away_goals
                hb, ab = min(hg, 7), min(ag, 7)
                yi = outcome_index(hg, ag)
                total_goals = hg + ag
                is_draw = hg == ag
                b = pred["baseline"]
                c = pred["candidate"]

                b_score = -math.log(max(float(b["matrix"][hb, ab]), PROB_FLOOR))
                c_score = -math.log(max(float(c["matrix"][hb, ab]), PROB_FLOOR))
                b_hda_ll = -math.log(max(float(b["hda"][yi]), PROB_FLOOR))
                c_hda_ll = -math.log(max(float(c["hda"][yi]), PROB_FLOOR))

                results.append(
                    {
                        "match_id": meta.match_id,
                        "match_date": date,
                        "fold": int(fold_map[date]),
                        "home": meta.home,
                        "away": meta.away,
                        "home_goals": hg,
                        "away_goals": ag,
                        "lambda_home": float(pred["lh"]),
                        "lambda_away": float(pred["la"]),
                        "theta": float(pred["theta"]),
                        "score_baseline": b_score,
                        "score_candidate": c_score,
                        "score_delta": c_score - b_score,
                        "hda_ll_baseline": b_hda_ll,
                        "hda_ll_candidate": c_hda_ll,
                        "hda_brier_baseline": multiclass_brier(b["hda"], yi),
                        "hda_brier_candidate": multiclass_brier(c["hda"], yi),
                        "total_rps_baseline": total_rps(b["total"], total_goals),
                        "total_rps_candidate": total_rps(c["total"], total_goals),
                        "draw_ll_baseline": draw_logloss(float(b["hda"][1]), is_draw),
                        "draw_ll_candidate": draw_logloss(float(c["hda"][1]), is_draw),
                        "draw_p_baseline": float(b["hda"][1]),
                        "draw_p_candidate": float(c["hda"][1]),
                        "is_draw": is_draw,
                        "top1_baseline": int(np.argmax(b["hda"])),
                        "top1_candidate": int(np.argmax(c["hda"])),
                        "p00_baseline": float(b["matrix"][0, 0]),
                        "p00_candidate": float(c["matrix"][0, 0]),
                        "p11_baseline": float(b["matrix"][1, 1]),
                        "p11_candidate": float(c["matrix"][1, 1]),
                        "p_total_ge5_baseline": float(b["total"][5:].sum()),
                        "p_total_ge5_candidate": float(c["total"][5:].sum()),
                        "p_home7_baseline": float(b["matrix"][7, :].sum()),
                        "p_home7_candidate": float(c["matrix"][7, :].sum()),
                        "p_away7_baseline": float(b["matrix"][:, 7].sum()),
                        "p_away7_candidate": float(c["matrix"][:, 7].sum()),
                        "tail_residual_baseline": float(b["tail_residual"]),
                        "tail_residual_candidate": float(c["tail_residual"]),
                        "kmax_baseline": int(b["kmax"]),
                        "kmax_candidate": int(c["kmax"]),
                    }
                )

        # Update all histories only after all same-date target payloads and predictions are complete.
        for meta in day:
            summary = summaries[meta.match_id]
            lh, la = lambdas[meta.match_id]
            theta_history.append((lh, la, summary.home_goals, summary.away_goals))
            xg_history.append(summary)

    if len(results) < MIN_TEST_MATCHES:
        return {
            "status": "STOP_DATA_COVERAGE",
            "reason": f"only {len(results)} evaluated test matches",
        }

    def mean_key(key: str) -> float:
        return float(np.mean([r[key] for r in results]))

    metrics = {
        "baseline": {
            "exact_score_logscore": mean_key("score_baseline"),
            "hda_logloss": mean_key("hda_ll_baseline"),
            "hda_brier": mean_key("hda_brier_baseline"),
            "total_rps": mean_key("total_rps_baseline"),
            "draw_binary_logloss": mean_key("draw_ll_baseline"),
        },
        "candidate": {
            "exact_score_logscore": mean_key("score_candidate"),
            "hda_logloss": mean_key("hda_ll_candidate"),
            "hda_brier": mean_key("hda_brier_candidate"),
            "total_rps": mean_key("total_rps_candidate"),
            "draw_binary_logloss": mean_key("draw_ll_candidate"),
        },
    }
    deltas = {
        key: metrics["candidate"][key] - metrics["baseline"][key]
        for key in metrics["baseline"]
    }

    bootstrap = bootstrap_primary(results)
    fold_metrics: dict[str, Any] = {}
    fold_wins = 0
    for fold in range(3):
        rr = [r for r in results if r["fold"] == fold]
        delta = float(np.mean([r["score_delta"] for r in rr]))
        fold_metrics[str(fold)] = {
            "n": len(rr),
            "exact_score_logscore_delta": delta,
        }
        if delta < 0.0:
            fold_wins += 1

    checks = {
        "primary_mean_delta_le_neg_0_005": deltas["exact_score_logscore"] <= -0.005,
        "bootstrap_p95_lt_zero": bootstrap["p95"] < 0.0,
        "fold_primary_wins_at_least_2": fold_wins >= 2,
        "hda_logloss_not_materially_worse": deltas["hda_logloss"] <= 0.002,
        "total_rps_not_materially_worse": deltas["total_rps"] <= 0.002,
    }
    passed = all(checks.values())
    status = (
        "RESEARCH_SIGNAL_PASS_RETROSPECTIVE_ONLY"
        if passed
        else "FAIL_RESEARCH_ONLY"
    )

    base_draw_calls = sum(1 for r in results if r["top1_baseline"] == 1)
    cand_draw_calls = sum(1 for r in results if r["top1_candidate"] == 1)
    base_draw_hits = sum(
        1 for r in results if r["top1_baseline"] == 1 and r["is_draw"]
    )
    cand_draw_hits = sum(
        1 for r in results if r["top1_candidate"] == 1 and r["is_draw"]
    )

    def binary_diag(prob_key: str, actual_fn) -> dict[str, float]:
        probs = np.asarray([float(r[prob_key]) for r in results], dtype=float)
        actual = np.asarray([1.0 if actual_fn(r) else 0.0 for r in results], dtype=float)
        return {
            "mean_pred": float(probs.mean()),
            "actual_rate": float(actual.mean()),
            "mean_error": float(probs.mean() - actual.mean()),
        }

    tail_diag = {
        "total_ge5_baseline": binary_diag(
            "p_total_ge5_baseline", lambda r: r["home_goals"] + r["away_goals"] >= 5
        ),
        "total_ge5_candidate": binary_diag(
            "p_total_ge5_candidate", lambda r: r["home_goals"] + r["away_goals"] >= 5
        ),
        "score_0_0_baseline": binary_diag(
            "p00_baseline", lambda r: r["home_goals"] == 0 and r["away_goals"] == 0
        ),
        "score_0_0_candidate": binary_diag(
            "p00_candidate", lambda r: r["home_goals"] == 0 and r["away_goals"] == 0
        ),
        "score_1_1_baseline": binary_diag(
            "p11_baseline", lambda r: r["home_goals"] == 1 and r["away_goals"] == 1
        ),
        "score_1_1_candidate": binary_diag(
            "p11_candidate", lambda r: r["home_goals"] == 1 and r["away_goals"] == 1
        ),
        "home_7plus_baseline": binary_diag(
            "p_home7_baseline", lambda r: r["home_goals"] >= 7
        ),
        "home_7plus_candidate": binary_diag(
            "p_home7_candidate", lambda r: r["home_goals"] >= 7
        ),
        "away_7plus_baseline": binary_diag(
            "p_away7_baseline", lambda r: r["away_goals"] >= 7
        ),
        "away_7plus_candidate": binary_diag(
            "p_away7_candidate", lambda r: r["away_goals"] >= 7
        ),
        "max_numeric_tail_residual": float(
            max(
                max(r["tail_residual_baseline"], r["tail_residual_candidate"])
                for r in results
            )
        ),
    }

    sample_sha = hashlib.sha256(
        "\n".join(str(r["match_id"]) for r in results).encode("utf-8")
    ).hexdigest()
    event_digest = hashlib.sha256(
        "\n".join(f"{k}:{event_shas[k]}" for k in sorted(event_shas)).encode("utf-8")
    ).hexdigest()

    return {
        "status": status,
        "scientific_component_pass": False,
        "research_metric_gate_pass": passed,
        "boundary": {
            "formal_weight": 0,
            "b05_opened": False,
            "new_protected_labels_opened": 0,
            "reserved_confirmation_panel_opened": False,
            "market_data_used": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
            "automatic_confirmation": False,
            "automatic_promotion": False,
            "pit_status": "RETROSPECTIVE_OPEN_DATA_NOT_PROVEN_HISTORICAL_PIT",
        },
        "coverage": {
            "metadata_matches": len(matches),
            "source_counts": source_counts,
            "event_head_success_rate": event_rate,
            "event_failures": event_failures,
        },
        "split": {
            "burn_in_matches": cumulative,
            "test_matches": len(results),
            "test_start_date": test_dates[0],
            "test_sample_sha256": sample_sha,
            "fold_counts": {str(k): int(v) for k, v in fold_counts.items()},
            "same_day_update_forbidden": True,
        },
        "metrics": {
            **metrics,
            "deltas_candidate_minus_baseline": deltas,
        },
        "bootstrap": bootstrap,
        "fold_metrics": fold_metrics,
        "development_gate": {"checks": checks, "passed": passed},
        "draw_diagnostic": {
            "baseline_top1_draw_calls": base_draw_calls,
            "baseline_top1_draw_hits": base_draw_hits,
            "candidate_top1_draw_calls": cand_draw_calls,
            "candidate_top1_draw_hits": cand_draw_hits,
        },
        "draw_calibration": {
            "baseline": calibration_bins(results, "draw_p_baseline"),
            "candidate": calibration_bins(results, "draw_p_candidate"),
            "baseline_logistic": logistic_calibration(results, "draw_p_baseline"),
            "candidate_logistic": logistic_calibration(results, "draw_p_candidate"),
        },
        "tail_diagnostic": tail_diag,
        "theta": {
            "final_theta": float(theta_path[-1]["theta"]),
            "test_theta_min": float(
                min(r["theta"] for r in theta_path if r["is_test_date"])
            ),
            "test_theta_max": float(
                max(r["theta"] for r in theta_path if r["is_test_date"])
            ),
            "test_theta_mean": float(
                np.mean([r["theta"] for r in theta_path if r["is_test_date"]])
            ),
            "path": theta_path,
        },
        "source_hashes": {
            "matches_ledger_sha256": matches_sha,
            "event_ledger_sha256": event_digest,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "research_metric_gate_pass": result.get("research_metric_gate_pass"),
                "coverage": result.get("coverage"),
                "split": result.get("split"),
                "deltas": (
                    (result.get("metrics") or {}).get("deltas_candidate_minus_baseline")
                ),
                "bootstrap": result.get("bootstrap"),
                "development_gate": result.get("development_gate"),
                "theta": result.get("theta"),
                "draw_diagnostic": result.get("draw_diagnostic"),
                "tail_diagnostic": result.get("tail_diagnostic"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
