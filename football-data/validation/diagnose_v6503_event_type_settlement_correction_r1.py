#!/usr/bin/env python3
"""Correct V6.50.3 settlement accounting without opening any new outcomes.

The append-only V6503 and V6505 ledgers contain both frozen prediction events and
RESULT_SETTLED receipt events. The original settlement evaluator iterated every V6503
event and built the V6505 match map from every event. This diagnostic measures the
impact and recomputes the already-available settlement using prediction-event filters.

No network/provider call, new outcome source, sample search, model fit, or parameter
search is performed. The four already-present settlement receipts remain the only
strict prospective outcome cohort. formal_weight=0.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

import evaluate_v6503_forward_total_settlement_r1 as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6503_event_type_settlement_correction_r1.json"
ROWS_OUT = ROOT / "manifests" / "v6503_event_type_settlement_correction_r1_rows.json"


def sha_ids(values: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def settle_prediction_events(
    prediction_events: list[dict[str, Any]],
    joint_by_match: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    primary_results, primary_duplicates = base.load_primary_results()
    historical_results, historical_ambiguous = base.load_historical_results()
    rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for event in prediction_events:
        payload = event.get("payload") or {}
        fixture = payload.get("fixture_identity") or {}
        match_id = str(event.get("match_id") or "")
        comp = fixture.get("competition_id")
        kickoff_text = fixture.get("kickoff_at")
        home = fixture.get("home_team")
        away = fixture.get("away_team")
        exact_key = base.fixture_key(comp, kickoff_text, home, away)
        date_key_ = base.date_team_key(comp, kickoff_text, home, away)
        result = primary_results.get(exact_key)
        settlement_tier = "market_first_exact_fixture"
        result_observed = None
        result_source = None
        if result is not None:
            hg = int(result["home_goals_90"])
            ag = int(result["away_goals_90"])
            result_observed = ((result.get("source") or {}).get("observed_at"))
            result_source = ((result.get("source") or {}).get("name"))
        else:
            hist = historical_results.get(date_key_)
            if hist is None:
                unmatched.append({
                    "match_id": match_id,
                    "competition_id": comp,
                    "kickoff_at": kickoff_text,
                    "home_team": home,
                    "away_team": away,
                })
                continue
            settlement_tier = "historical_exact_date_team_fallback"
            hg = int(hist["home_goals_90"])
            ag = int(hist["away_goals_90"])
            result_source = str(hist.get("source_file") or "")

        kickoff = base.parse_dt(kickoff_text)
        freeze = base.parse_dt(payload.get("projection_freeze_at_utc"))
        market_obs = base.parse_dt(payload.get("market_observed_at_utc"))
        result_obs = base.parse_dt(result_observed)
        if kickoff is None or freeze is None or market_obs is None:
            raise base.SettlementError(f"missing frozen timestamps for prediction event {match_id}")
        prediction_temporal_pass = bool(market_obs <= freeze < kickoff)
        result_temporal_pass = bool(result_obs is not None and kickoff < result_obs)
        strict_temporal_pass = bool(
            settlement_tier == "market_first_exact_fixture"
            and prediction_temporal_pass
            and result_temporal_pass
        )
        source_p = base.probability_vector(payload.get("source_prior_total") or {})
        candidate_p = base.probability_vector(payload.get("candidate_total") or {})
        actual_total = hg + ag
        rows.append({
            "match_id": match_id,
            "competition_id": str(comp or ""),
            "kickoff_at": kickoff.isoformat(),
            "home_team": str(home or ""),
            "away_team": str(away or ""),
            "home_goals_90": hg,
            "away_goals_90": ag,
            "actual_total": actual_total,
            "actual_total_class": min(actual_total, 7),
            "settlement_tier": settlement_tier,
            "result_source": result_source,
            "result_observed_at_utc": result_obs.isoformat() if result_obs else None,
            "prediction_temporal_pass": prediction_temporal_pass,
            "result_temporal_pass": result_temporal_pass,
            "strict_temporal_pass": strict_temporal_pass,
            "projection_freeze_at_utc": freeze.isoformat(),
            "market_observed_at_utc": market_obs.isoformat(),
            "prediction_lead_hours": (kickoff - freeze).total_seconds() / 3600.0,
            "market_lead_hours": (kickoff - market_obs).total_seconds() / 3600.0,
            "ou_line": float((payload.get("over_under_raw") or {})["line"]),
            "market_p_over": float((payload.get("over_under_devig") or {})["over"]),
            "source_p": source_p,
            "candidate_p": candidate_p,
            "source_top1": int(np.argmax(source_p)),
            "candidate_top1": int(np.argmax(candidate_p)),
            "joint_prediction_available": match_id in joint_by_match,
        })

    audit = {
        "primary_result_duplicate_keys_excluded": primary_duplicates,
        "historical_ambiguous_identity_keys_excluded": int(historical_ambiguous),
    }
    return rows, unmatched, audit


def metric_view(rows: list[dict[str, Any]], joint_by_match: dict[str, dict[str, Any]]) -> dict[str, Any]:
    strict = [r for r in rows if r["strict_temporal_pass"]]
    all_settled = list(rows)
    if not strict:
        raise base.SettlementError("zero strict timestamped prediction settlements")
    return {
        "strict_n": len(strict),
        "all_settled_n": len(all_settled),
        "strict_competition_counts": dict(sorted(Counter(r["competition_id"] for r in strict).items())),
        "strict_actual_draws": int(sum(r["home_goals_90"] == r["away_goals_90"] for r in strict)),
        "strict_direct_total": base.eval_total(strict, 6505601),
        "strict_binary_at_observed_ou_line": base.binary_components(strict),
        "strict_exact_score_projection": base.exact_score_eval(strict, joint_by_match),
        "supplementary_direct_total": base.eval_total(all_settled, 6505611),
        "supplementary_exact_score_projection": base.exact_score_eval(all_settled, joint_by_match),
        "strict_lead_time": base.lead_stats(strict),
    }


def main() -> None:
    t_root = base.load_json(base.EVENTS_T)
    joint_root = base.load_json(base.EVENTS_JOINT)
    t_events = list(t_root.get("events", []))
    joint_events = list(joint_root.get("events", []))

    t_type_counts = Counter(str(e.get("event_type") or "") for e in t_events)
    joint_type_counts = Counter(str(e.get("event_type") or "") for e in joint_events)
    t_predictions = [e for e in t_events if e.get("event_type") == "TOTAL_PREDICTION_FROZEN"]
    t_receipts = [e for e in t_events if e.get("event_type") == "RESULT_SETTLED"]
    joint_predictions = [e for e in joint_events if e.get("event_type") == "JOINT_MATRIX_PREDICTION_FROZEN"]
    joint_receipts = [e for e in joint_events if e.get("event_type") == "RESULT_SETTLED"]

    prediction_ids = [str(e.get("match_id") or "") for e in t_predictions]
    receipt_ids = [str(e.get("match_id") or "") for e in t_receipts]
    joint_prediction_ids = [str(e.get("match_id") or "") for e in joint_predictions]
    joint_receipt_ids = [str(e.get("match_id") or "") for e in joint_receipts]
    joint_by_match = {str(e.get("match_id") or ""): e for e in joint_predictions}

    if len(prediction_ids) != len(set(prediction_ids)):
        raise base.SettlementError("duplicate V6503 frozen prediction match_id")
    if len(joint_prediction_ids) != len(set(joint_prediction_ids)):
        raise base.SettlementError("duplicate V6505 frozen prediction match_id")

    rows, unmatched, settlement_audit = settle_prediction_events(t_predictions, joint_by_match)
    metrics = metric_view(rows, joint_by_match)
    tier_counts = Counter(r["settlement_tier"] for r in rows)

    result = {
        "schema_version": "V6503_EVENT_TYPE_SETTLEMENT_CORRECTION_R1",
        "classification": "POST_HOC_CODE_PATH_CORRECTION_SAME_EXISTING_OUTCOMES_ONLY",
        "ledger_audit": {
            "v6503_all_events": len(t_events),
            "v6503_event_type_counts": dict(sorted(t_type_counts.items())),
            "v6503_prediction_events": len(t_predictions),
            "v6503_result_receipts": len(t_receipts),
            "v6503_prediction_match_ids_sha256": sha_ids(prediction_ids),
            "v6503_receipt_match_ids_sha256": sha_ids(receipt_ids),
            "v6505_all_events": len(joint_events),
            "v6505_event_type_counts": dict(sorted(joint_type_counts.items())),
            "v6505_prediction_events": len(joint_predictions),
            "v6505_result_receipts": len(joint_receipts),
            "v6505_prediction_match_ids_sha256": sha_ids(joint_prediction_ids),
            "prediction_id_set_matches_v6505": set(prediction_ids) == set(joint_prediction_ids),
            "v6503_receipt_ids_subset_predictions": set(receipt_ids).issubset(set(prediction_ids)),
            "v6505_receipt_ids_subset_predictions": set(joint_receipt_ids).issubset(set(joint_prediction_ids)),
        },
        "original_code_path_bug": {
            "v6503_iteration_included_non_prediction_events": True,
            "false_unmatched_receipt_events": len([x for x in t_receipts if str(x.get("match_id") or "") in set(prediction_ids)]),
            "v6505_unfiltered_match_map_overwrite_risk": True,
            "joint_prediction_rows_overwritten_by_result_receipts": len(set(joint_prediction_ids) & set(joint_receipt_ids)),
            "impact": "Original R1 unmatched_n was inflated by RESULT_SETTLED receipts, and its unfiltered V6505 match map replaced frozen joint matrices with receipt payloads for the already-settled match_ids. Direct-T strict metrics remain based on the same four frozen predictions/outcomes; exact-score settlement must use the filtered frozen joint events.",
        },
        "corrected_settlement": {
            "prediction_event_n": len(t_predictions),
            "settled_total": len(rows),
            "strict_timestamped_n": metrics["strict_n"],
            "unmatched_prediction_n": len(unmatched),
            "settlement_tier_counts": dict(sorted(tier_counts.items())),
            "unmatched_by_competition": dict(sorted(Counter(str(r.get("competition_id") or "") for r in unmatched).items())),
            "unmatched_by_date": dict(sorted(Counter(base.date_key(r.get("kickoff_at")) for r in unmatched).items())),
            "unmatched_examples": unmatched[:20],
            **settlement_audit,
        },
        "corrected_metrics": metrics,
        "interpretation": {
            "strict_direct_total_scientific_sample_changed": False,
            "strict_direct_total_outcomes_changed": False,
            "strict_direct_total_probabilities_changed": False,
            "unmatched_accounting_corrected": True,
            "exact_score_code_path_corrected": True,
            "new_scientific_claim_allowed": False,
            "reason": "Correction uses only prediction-event filtering and already-present outcomes; n=4 remains too small for inference.",
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "new_outcome_source_opened": False,
            "latest_confirmation_block_opened": False,
            "model_fit_performed": False,
            "parameter_search_performed": False,
            "formal_asset_changes": 0,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    serial_rows = []
    for r in rows:
        x = dict(r)
        x["source_p"] = [float(v) for v in r["source_p"]]
        x["candidate_p"] = [float(v) for v in r["candidate_p"]]
        serial_rows.append(x)
    ROWS_OUT.write_text(json.dumps(serial_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "classification": result["classification"],
        "ledger_audit": result["ledger_audit"],
        "original_code_path_bug": result["original_code_path_bug"],
        "corrected_settlement": result["corrected_settlement"],
        "corrected_metrics": result["corrected_metrics"],
        "interpretation": result["interpretation"],
        "governance": result["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
