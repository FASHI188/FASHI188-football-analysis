#!/usr/bin/env python3
"""V6.12.8 prospective sidecar for the V6.12.7 adaptive selective 1X2 rule.

Research only. This module does NOT create, rewrite, or settle market predictions. It
reads the immutable V6.5.1 prospective market ledger, freezes the V6.12.7 threshold-
selection procedure once, and scores only V6.5.1 predictions observed at/after the
V6.12.8 freeze timestamp. Result settlement remains exclusively owned by the existing
V6.5.1 official-result chain.

The threshold-selection algorithm is frozen from V6.12.7:
- candidate home/away top-1 probability thresholds: 0.58..0.72 by 0.02;
- use only pre-freeze historical market rows;
- use the most recent 25% of that history as selector validation;
- maximize the validation Wilson-90 lower bound subject to >=80% retention and
  direction sample floors;
- no draw selections.

Historical rows used to select the threshold are retrospective closing-price evidence,
so V6.12.8 has formal_weight=0. Only the post-freeze V6.5.1 market predictions are
prospective evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_1x2_crossseason_phase_v6123 as phase
import validate_1x2_nested_walkforward_v6127 as nested

SOURCE_SCRIPT = VALIDATION / "validate_1x2_nested_walkforward_v6127.py"
SOURCE_AUDIT = ROOT / "manifests" / "v6_1x2_nested_walkforward_v6127_status.json"
LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
FREEZE = ROOT / "manifests" / "v6_1x2_adaptive_forward_freeze_v6128.json"
OUT = ROOT / "manifests" / "v6_1x2_adaptive_forward_v6128_status.json"
FREEZE_SCHEMA = "V6.12.8-adaptive-1x2-forward-freeze-r1"
STATUS_SCHEMA = "V6.12.8-adaptive-1x2-forward-status-r1"
LEDGER_SCHEMA = "V6.5.1-market-first-forward-ledger-r1"
Z90 = 1.6448536269514722


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_dt(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def wilson_lower(hits: int, n: int, z: float = Z90) -> float | None:
    if n <= 0:
        return None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - radius) / den


def ensure_freeze(now: datetime) -> dict[str, Any]:
    source_script_sha = file_sha(SOURCE_SCRIPT)
    source_audit = load_json(SOURCE_AUDIT)
    if source_audit.get("status") != "PASS":
        raise RuntimeError("V6.12.7 source audit is not PASS")
    source_audit_sha = file_sha(SOURCE_AUDIT)

    if FREEZE.exists():
        frozen = load_json(FREEZE)
        if frozen.get("schema_version") != FREEZE_SCHEMA or frozen.get("status") != "FROZEN":
            raise RuntimeError("invalid V6.12.8 freeze manifest")
        if frozen.get("source_script_sha256") != source_script_sha:
            raise RuntimeError("V6.12.7 selector source drift after V6.12.8 freeze")
        return frozen

    rows, providers = phase._read_rows()
    history = [r for r in rows if parse_dt(str(r["date"])) < now]
    history.sort(key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    if len(history) < 2000:
        raise RuntimeError(f"insufficient pre-freeze selector history: {len(history)}")
    tail_n = max(300, int(len(history) * nested.VALIDATION_TAIL_FRACTION))
    validation = history[-tail_n:]
    chosen, candidates = nested._choose(validation)
    ht = float(chosen["home_threshold"])
    at = float(chosen["away_threshold"])

    frozen = {
        "schema_version": FREEZE_SCHEMA,
        "status": "FROZEN",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_PROSPECTIVE_SIDECAR_FORMAL_WEIGHT_0",
        "freeze_timestamp_utc": now.isoformat(),
        "source_script_path": str(SOURCE_SCRIPT.relative_to(ROOT)),
        "source_script_sha256": source_script_sha,
        "source_audit_path": str(SOURCE_AUDIT.relative_to(ROOT)),
        "source_audit_sha256_at_freeze": source_audit_sha,
        "source_audit_aggregate": {
            "adaptive_accuracy": source_audit.get("aggregate_adaptive", {}).get("accuracy"),
            "adaptive_count": source_audit.get("aggregate_adaptive", {}).get("count"),
            "fixed_accuracy": source_audit.get("aggregate_fixed_064_060", {}).get("accuracy"),
            "baseline_accuracy": source_audit.get("aggregate_baseline_062_062", {}).get("accuracy"),
        },
        "selector_history": {
            "pre_freeze_rows": len(history),
            "validation_tail_rows": len(validation),
            "history_last_date": history[-1]["date"],
            "provider_counts_full_available_history": providers,
        },
        "frozen_rule": {
            "probability_source": "V6.5.1 multiplicatively de-vigged prospective 1X2",
            "pick": "top1",
            "draws_selected": False,
            "home_threshold": ht,
            "away_threshold": at,
            "threshold_grid": list(nested.GRID),
            "validation_tail_fraction": nested.VALIDATION_TAIL_FRACTION,
            "selection_objective": "maximize validation Wilson90 lower bound subject to >=80% retention and direction sample floors",
            "selected_validation": chosen,
            "admissible_candidate_count": sum(1 for c in candidates if c.get("admissible")),
        },
        "forward_checkpoints": {
            "diagnostic_selected_minimum": 100,
            "promotion_review_selected_minimum": 500,
            "minimum_competitions_at_promotion_review": 8,
            "raw_accuracy_target": 0.70,
            "wilson90_lower_target": 0.65,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "post_freeze_predictions_only": True,
            "v651_prediction_rewrite": False,
            "v651_result_rewrite": False,
            "official_result_chain_only": True,
            "automatic_promotion": False,
            "manual_review_required": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    write_json(FREEZE, frozen)
    return frozen


def selected_by_rule(prediction: dict[str, Any], frozen_rule: dict[str, Any]) -> bool:
    pick = str(prediction.get("pick") or "")
    probs = prediction.get("probabilities") or {}
    if pick == "home":
        return float(probs.get("home", -1.0)) >= float(frozen_rule["home_threshold"])
    if pick == "away":
        return float(probs.get("away", -1.0)) >= float(frozen_rule["away_threshold"])
    return False


def main() -> int:
    now = utc_now()
    frozen = ensure_freeze(now)
    if not LEDGER.exists():
        raise RuntimeError("V6.5.1 prospective ledger missing")
    ledger = load_json(LEDGER)
    if ledger.get("schema_version") != LEDGER_SCHEMA or not isinstance(ledger.get("events"), list):
        raise RuntimeError("invalid V6.5.1 prospective ledger")

    freeze_dt = parse_dt(str(frozen["freeze_timestamp_utc"]))
    rule = frozen["frozen_rule"]
    predictions: dict[str, dict[str, Any]] = {}
    settlements: dict[str, dict[str, Any]] = {}
    for event in ledger["events"]:
        etype = event.get("event_type")
        mid = str(event.get("match_id") or "")
        if etype == "MARKET_PREDICTION_FROZEN":
            event_dt = parse_dt(str(event.get("event_timestamp_utc") or ""))
            if event_dt >= freeze_dt:
                predictions[mid] = event
        elif etype == "RESULT_SETTLED":
            settlements[mid] = event

    selected = []
    settled_selected = []
    for mid, event in sorted(predictions.items(), key=lambda item: item[1].get("event_timestamp_utc", "")):
        payload = event.get("payload") or {}
        prediction = payload.get("prediction") or {}
        is_selected = selected_by_rule(prediction, rule)
        if not is_selected:
            continue
        identity = payload.get("fixture_identity") or {}
        row = {
            "match_id": mid,
            "prediction_event_hash": event.get("event_hash"),
            "prediction_timestamp_utc": event.get("event_timestamp_utc"),
            "competition_id": identity.get("competition_id"),
            "kickoff_at": identity.get("kickoff_at"),
            "home_team": identity.get("home_team"),
            "away_team": identity.get("away_team"),
            "pick": prediction.get("pick"),
            "top1_probability": (prediction.get("probabilities") or {}).get(str(prediction.get("pick") or "")),
        }
        selected.append(row)
        settlement = settlements.get(mid)
        if settlement is not None:
            result = (settlement.get("payload") or {}).get("result") or {}
            actual = result.get("actual_result")
            row = dict(row)
            row.update({
                "actual_result": actual,
                "hit": bool(actual == row["pick"]),
                "result_event_hash": settlement.get("event_hash"),
                "result_source": (settlement.get("payload") or {}).get("result_source"),
            })
            settled_selected.append(row)

    hits = sum(1 for r in settled_selected if r["hit"])
    n = len(settled_selected)
    by_comp: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "hits": 0})
    for r in settled_selected:
        cid = str(r.get("competition_id") or "UNKNOWN")
        by_comp[cid]["count"] += 1
        by_comp[cid]["hits"] += int(bool(r["hit"]))
    by_comp_out = {
        cid: {
            "count": s["count"],
            "hits": s["hits"],
            "accuracy": s["hits"] / s["count"] if s["count"] else None,
        }
        for cid, s in sorted(by_comp.items())
    }

    checkpoints = frozen["forward_checkpoints"]
    diagnostic_ready = n >= int(checkpoints["diagnostic_selected_minimum"])
    promotion_sample_ready = (
        n >= int(checkpoints["promotion_review_selected_minimum"])
        and len(by_comp_out) >= int(checkpoints["minimum_competitions_at_promotion_review"])
    )
    raw_accuracy = hits / n if n else None
    w90 = wilson_lower(hits, n)
    performance_gate = bool(
        promotion_sample_ready
        and raw_accuracy is not None
        and raw_accuracy >= float(checkpoints["raw_accuracy_target"])
        and w90 is not None
        and w90 >= float(checkpoints["wilson90_lower_target"])
    )

    if not predictions:
        evaluation_status = "WAITING_FOR_POST_FREEZE_PREDICTIONS"
    elif not diagnostic_ready:
        evaluation_status = "PENDING_100_SELECTED_DIAGNOSTIC"
    elif not promotion_sample_ready:
        evaluation_status = "DIAGNOSTIC_READY_PENDING_500_SELECTED_REVIEW"
    elif performance_gate:
        evaluation_status = "PROMOTION_REVIEW_PERFORMANCE_GATE_MET_MANUAL_REVIEW_REQUIRED"
    else:
        evaluation_status = "PROMOTION_REVIEW_SAMPLE_READY_PERFORMANCE_GATE_NOT_MET"

    payload = {
        "schema_version": STATUS_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "evaluation_status": evaluation_status,
        "formal_current_version": "V5.0.1",
        "freeze_timestamp_utc": frozen["freeze_timestamp_utc"],
        "source_v651_ledger_path": str(LEDGER.relative_to(ROOT)),
        "source_v651_ledger_sha256": file_sha(LEDGER),
        "frozen_rule": {
            "home_threshold": rule["home_threshold"],
            "away_threshold": rule["away_threshold"],
            "draws_selected": False,
        },
        "post_freeze_prediction_count": len(predictions),
        "post_freeze_selected_count": len(selected),
        "settled_selected_count": n,
        "open_selected_count": len(selected) - n,
        "hits": hits,
        "accuracy": raw_accuracy,
        "wilson90_lower": w90,
        "competitions_represented_settled": len(by_comp_out),
        "by_competition": by_comp_out,
        "diagnostic_100_selected_ready": diagnostic_ready,
        "promotion_review_sample_ready": promotion_sample_ready,
        "promotion_review_performance_gate_met": performance_gate,
        "selected_predictions": selected,
        "settled_selected_predictions": settled_selected,
        "governance": frozen["governance"],
    }
    write_json(OUT, payload)
    print(json.dumps({
        "evaluation_status": evaluation_status,
        "frozen_rule": payload["frozen_rule"],
        "post_freeze_prediction_count": len(predictions),
        "post_freeze_selected_count": len(selected),
        "settled_selected_count": n,
        "hits": hits,
        "accuracy": raw_accuracy,
        "wilson90_lower": w90,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
