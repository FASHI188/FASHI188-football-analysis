#!/usr/bin/env python3
"""V6.5.1 immutable market-first pristine forward epoch.

Research-only. Creates an independent hash-chain ledger from prospective market evidence.
No historical backfill: only evidence observed at/after the epoch freeze can create a market
prediction. Each match is frozen once at the first eligible observed snapshot; later movement
is descriptive only and cannot rewrite the prediction.

Primary historical rule from V6.5.0:
- de-vigged 1X2 top-1;
- exclude draw selections;
- confidence = top1 probability - runner-up probability;
- arm A selects confidence >= 0.35.

The sidecar also freezes synchronized AH/OU surfaces for future diagnostic/context research,
but they do not alter V6.5.1 1X2 probabilities. Settlement uses processed 90-minute results
strictly after kickoff. No CURRENT/formal/runtime mutation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import (
    PlatformError,
    atomic_write_json,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    sha256_json,
)

RULE = ROOT / "manifests" / "v6_market_first_selector_v650_status.json"
FREEZE = ROOT / "manifests" / "v6_market_first_forward_freeze_v651.json"
LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
OUT = ROOT / "manifests" / "v6_market_first_forward_evaluation_v651_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "markets_prospective"
LEDGER_SCHEMA = "V6.5.1-market-first-forward-ledger-r1"
EVENT_SCHEMA = "V6.5.1-market-first-forward-event-r1"
FREEZE_SCHEMA = "V6.5.1-market-first-forward-freeze-r1"
EVAL_SCHEMA = "V6.5.1-market-first-forward-evaluation-r1"
THRESHOLD = 0.35
MIN_LEAD = timedelta(hours=1)
MAX_LEAD = timedelta(hours=72)
MIN_RESULT_AGE = timedelta(hours=2)
Z90 = 1.6448536269514722


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z2 = Z90 * Z90
    den = 1.0 + z2 / count
    ctr = p + z2 / (2.0 * count)
    spr = Z90 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count)
    return (ctr - spr) / den


def devig(odds: dict[str, Any]) -> dict[str, float]:
    vals = {}
    for key in ("home", "draw", "away"):
        value = float(odds[key])
        if not math.isfinite(value) or value <= 1.0:
            raise PlatformError(f"invalid 1X2 odds {key}={value}")
        vals[key] = 1.0 / value
    total = sum(vals.values())
    return {k: vals[k] / total for k in vals}


def top_pick(q: dict[str, float]) -> tuple[str, float]:
    ranked = sorted(((float(q[k]), k) for k in ("home", "draw", "away")), reverse=True)
    return ranked[0][1], ranked[0][0] - ranked[1][0]


def ensure_freeze(now: datetime) -> dict[str, Any]:
    rule = load_json(RULE)
    if rule.get("status") != "PASS":
        raise PlatformError("V6.5.0 rule must be PASS")
    if FREEZE.exists():
        frozen = load_json(FREEZE)
        if frozen.get("schema_version") != FREEZE_SCHEMA or frozen.get("status") != "FROZEN":
            raise PlatformError("invalid V6.5.1 freeze")
        if frozen.get("source_rule_sha256") != file_sha(RULE):
            raise PlatformError("V6.5.0 source rule drift after V6.5.1 freeze")
        return frozen
    frozen = {
        "schema_version": FREEZE_SCHEMA,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "source_rule_path": str(RULE.relative_to(ROOT)),
        "source_rule_sha256": file_sha(RULE),
        "rule": {
            "probability_source": "multiplicatively_de_vigged_prospective_1x2",
            "pick": "top1",
            "draws_excluded_from_selective_arm": True,
            "confidence_definition": "top1_probability_minus_runner_up_probability",
            "selective_threshold": THRESHOLD,
            "ah_ou_role": "frozen_diagnostic_only_no_probability_rewrite",
            "historical_backfill": False,
        },
        "forward_gates": {
            "minimum_valid_settled": 500,
            "minimum_selected": 120,
            "minimum_competitions": 8,
            "raw_accuracy_minimum": 0.65,
            "wilson90_lower_minimum": 0.65,
        },
        "governance": {
            "automatic_promotion": False,
            "manual_review_required": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(FREEZE, frozen)
    return frozen


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    data = load_json(LEDGER)
    if data.get("schema_version") != LEDGER_SCHEMA or not isinstance(data.get("events"), list):
        raise PlatformError("invalid V6.5.1 ledger")
    return data


def event_hash(event_without_hash: dict[str, Any]) -> str:
    return sha256_json(event_without_hash)


def append_event(ledger: dict[str, Any], event_type: str, match_id: str, timestamp: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    prev = events[-1]["event_hash"] if events else "GENESIS"
    event = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": event_type,
        "event_timestamp_utc": timestamp,
        "match_id": match_id,
        "previous_event_hash": prev,
        "payload": payload,
    }
    event["event_hash"] = event_hash(event)
    events.append(event)
    return event


def audit_chain(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"
    errors = []
    for i, event in enumerate(ledger.get("events", []), start=1):
        if int(event.get("sequence", -1)) != i:
            errors.append(f"sequence:{i}")
        if event.get("previous_event_hash") != prev:
            errors.append(f"previous_hash:{i}")
        copied = dict(event)
        recorded = copied.pop("event_hash", None)
        expected = event_hash(copied)
        if recorded != expected:
            errors.append(f"event_hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events", [])), "tip_hash": prev, "errors": errors}


def identity_key(raw: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(raw.get("competition_id") or ""),
        str(raw.get("kickoff_utc") or ""),
        normalize_team_token(str(raw.get("home_team") or "")),
        normalize_team_token(str(raw.get("away_team") or "")),
    )


def match_id_from_identity(key: tuple[str, str, str, str]) -> str:
    return "market_" + sha256_json({"competition_id": key[0], "kickoff_utc": key[1], "home": key[2], "away": key[3]})[:24]


def prediction_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "MARKET_PREDICTION_FROZEN"}


def settlement_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "RESULT_SETTLED"}


def scan_new_predictions(now: datetime, freeze: dict[str, Any], ledger: dict[str, Any]) -> dict[str, int]:
    stats: Counter = Counter()
    frozen_at = parse_iso_datetime(freeze["freeze_timestamp_utc"], "freeze_timestamp_utc")
    existing = prediction_events(ledger)
    candidates: dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]] = {}
    for path in sorted(EVIDENCE_ROOT.glob("*.json")) if EVIDENCE_ROOT.exists() else []:
        stats["files_seen"] += 1
        try:
            raw = load_json(path)
            observed = parse_iso_datetime(str(raw.get("source_observed_at_utc") or raw.get("freeze_utc") or ""), "observed")
            kickoff = parse_iso_datetime(str(raw.get("kickoff_utc") or ""), "kickoff")
            if observed < frozen_at:
                stats["before_epoch_freeze"] += 1
                continue
            if observed >= kickoff or observed > now:
                stats["invalid_timing"] += 1
                continue
            lead = kickoff - observed
            if lead < MIN_LEAD or lead > MAX_LEAD:
                stats["outside_lead_window"] += 1
                continue
            if str(raw.get("settlement_scope") or "") not in {"90m_including_stoppage", "90_minutes_including_stoppage"}:
                stats["wrong_scope"] += 1
                continue
            if not isinstance(raw.get("one_x_two"), dict):
                stats["missing_1x2"] += 1
                continue
            key = identity_key(raw)
            mid = match_id_from_identity(key)
            if mid in existing:
                stats["already_frozen"] += 1
                continue
            # Earliest eligible snapshot is chosen for pristine no-lookahead semantics.
            previous = candidates.get(key)
            if previous is None or observed < previous[0]:
                candidates[key] = (observed, path, raw)
        except Exception:
            stats["files_rejected"] += 1
    for key, (observed, path, raw) in sorted(candidates.items(), key=lambda item: (item[1][0], item[0])):
        q = devig(raw["one_x_two"])
        pick, confidence = top_pick(q)
        mid = match_id_from_identity(key)
        payload = {
            "fixture_identity": {
                "competition_id": key[0],
                "season": str(raw.get("season") or ""),
                "kickoff_at": key[1],
                "home_team": str(raw.get("home_team") or ""),
                "away_team": str(raw.get("away_team") or ""),
                "settlement_scope": str(raw.get("settlement_scope") or ""),
            },
            "market_source": {
                "provider_name": raw.get("provider_name"),
                "provider_group": raw.get("provider_group"),
                "source_url": raw.get("source_url"),
                "source_observed_at_utc": observed.isoformat(),
                "evidence_path": str(path.relative_to(ROOT)),
                "evidence_sha256": sha256_json(raw),
                "raw_snapshot_sha256": raw.get("raw_snapshot_sha256"),
            },
            "frozen_surfaces": {
                "one_x_two_odds": raw.get("one_x_two"),
                "asian_handicap": raw.get("asian_handicap"),
                "over_under": raw.get("over_under"),
                "surface_observed_at_utc": raw.get("surface_observed_at_utc"),
            },
            "prediction": {
                "probabilities": q,
                "pick": pick,
                "confidence": confidence,
                "selected_arm_a": bool(pick != "draw" and confidence >= THRESHOLD),
            },
        }
        append_event(ledger, "MARKET_PREDICTION_FROZEN", mid, observed.isoformat(), payload)
        existing[mid] = ledger["events"][-1]
        stats["new_predictions_frozen"] += 1
    return dict(sorted(stats.items()))


def settle_open(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats: Counter = Counter()
    preds = prediction_events(ledger)
    settled = settlement_events(ledger)
    cache: dict[str, list[Any]] = {}
    for mid, event in sorted(preds.items()):
        if mid in settled:
            continue
        identity = event["payload"]["fixture_identity"]
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        if now < kickoff + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1
            continue
        cid = identity["competition_id"]
        if cid not in cache:
            try:
                cache[cid] = read_processed_matches(cid)
            except Exception:
                cache[cid] = []
        rows = [
            m for m in cache[cid]
            if m.date.date() == kickoff.date()
            and normalize_team_token(m.home_team) == normalize_team_token(identity["home_team"])
            and normalize_team_token(m.away_team) == normalize_team_token(identity["away_team"])
        ]
        if len(rows) != 1:
            stats["result_not_unique"] += 1
            continue
        m = rows[0]
        hg, ag = int(m.home_goals), int(m.away_goals)
        actual = "home" if hg > ag else "away" if hg < ag else "draw"
        append_event(ledger, "RESULT_SETTLED", mid, now.isoformat(), {
            "prediction_event_hash": event["event_hash"],
            "result": {
                "home_goals_90": hg,
                "away_goals_90": ag,
                "actual_result": actual,
                "source_record_id": f"{m.source_path}|{m.date.date().isoformat()}|{m.home_team}|{m.away_team}",
            },
        })
        stats["new_results_settled"] += 1
    return dict(sorted(stats.items()))


def evaluate(freeze: dict[str, Any], ledger: dict[str, Any], now: datetime, prediction_scan: dict[str, int], settlement_scan: dict[str, int]) -> dict[str, Any]:
    chain = audit_chain(ledger)
    preds = prediction_events(ledger)
    settlements = settlement_events(ledger)
    rows = []
    errors = []
    for mid, settle in settlements.items():
        pred = preds.get(mid)
        if pred is None:
            errors.append(f"settlement_without_prediction:{mid}")
            continue
        if settle["payload"].get("prediction_event_hash") != pred.get("event_hash"):
            errors.append(f"prediction_reference_mismatch:{mid}")
            continue
        p = pred["payload"]["prediction"]
        result = settle["payload"]["result"]
        rows.append({
            "match_id": mid,
            "competition_id": pred["payload"]["fixture_identity"]["competition_id"],
            "pick": p["pick"],
            "confidence": float(p["confidence"]),
            "selected": bool(p["selected_arm_a"]),
            "truth": result["actual_result"],
            "hit": int(p["pick"] == result["actual_result"]),
        })
    selected = [r for r in rows if r["selected"]]
    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        hits = sum(int(r["hit"]) for r in items)
        comps = Counter(str(r["competition_id"]) for r in items)
        return {
            "count": len(items),
            "hits": hits,
            "accuracy": hits / len(items) if items else None,
            "wilson90_lower": wilson(hits, len(items)),
            "competitions_represented": len(comps),
            "by_competition": {cid: count for cid, count in sorted(comps.items())},
        }
    all_summary = summary(rows)
    sel_summary = summary(selected)
    gates = freeze["forward_gates"]
    minimums = (
        len(rows) >= int(gates["minimum_valid_settled"])
        and len(selected) >= int(gates["minimum_selected"])
        and int(sel_summary["competitions_represented"]) >= int(gates["minimum_competitions"])
    )
    gate_pass = bool(
        minimums
        and sel_summary["accuracy"] is not None
        and float(sel_summary["accuracy"]) >= float(gates["raw_accuracy_minimum"])
        and sel_summary["wilson90_lower"] is not None
        and float(sel_summary["wilson90_lower"]) >= float(gates["wilson90_lower_minimum"])
        and chain["status"] == "PASS"
        and not errors
    )
    if not rows:
        status = "PENDING_NO_SETTLED_FORWARD_MATCHES"
    elif not minimums:
        status = "PENDING_MINIMUM_SAMPLE"
    elif gate_pass:
        status = "FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else:
        status = "FORWARD_GATE_FAIL"
    return {
        "schema_version": EVAL_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": "PASS" if chain["status"] == "PASS" and not errors else "FAIL_INTEGRITY",
        "evaluation_status": status,
        "freeze_timestamp_utc": freeze["freeze_timestamp_utc"],
        "prediction_scan": prediction_scan,
        "settlement_scan": settlement_scan,
        "ledger_chain_audit": chain,
        "semantic_errors": errors,
        "prediction_count": len(preds),
        "settled_count": len(rows),
        "open_prediction_count": len(preds) - len(settlements),
        "all_market_predictions": all_summary,
        "selective_arm_a": sel_summary,
        "minimum_sample_gate_met": minimums,
        "promotion_gate_passed": gate_pass,
        "governance": {
            "prospective_market_only": True,
            "historical_backfill": False,
            "single_freeze_per_match": True,
            "later_market_movement_cannot_rewrite_prediction": True,
            "ah_ou_frozen_but_not_used_in_prediction": True,
            "automatic_promotion": False,
            "manual_review_required": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }


def main() -> int:
    now = utc_now()
    freeze = ensure_freeze(now)
    ledger = load_ledger()
    before = audit_chain(ledger)
    if before["status"] != "PASS":
        raise PlatformError(f"pre-existing V6.5.1 ledger invalid: {before['errors']}")
    prediction_scan = scan_new_predictions(now, freeze, ledger)
    settlement_scan = settle_open(now, ledger)
    after = audit_chain(ledger)
    if after["status"] != "PASS":
        raise PlatformError(f"V6.5.1 ledger invalid after append: {after['errors']}")
    atomic_write_json(LEDGER, ledger)
    result = evaluate(freeze, ledger, now, prediction_scan, settlement_scan)
    atomic_write_json(OUT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
