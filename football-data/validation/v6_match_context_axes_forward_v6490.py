#!/usr/bin/env python3
"""V6.49.0 immutable manager/material-roster-delta sidecar.

Consumes only manually/live verified pre-kickoff context evidence observed at/after the frozen
V6.49.0 epoch. A fixture is eligible only after it also exists in the clean V6.48.6
context+market decision ledger. Manager identity and roster deltas are audited as separate axes;
pending transfers are preserved but never counted as completed roster deltas.

Research only: no probability, CURRENT, model weight or historical event is mutated.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "match_context_live" / "manual_web"
BASE_LEDGER = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
FREEZE = ROOT / "manifests" / "v6_match_context_axes_v6490_freeze.json"
LEDGER = ROOT / "forward" / "v6_match_context_axes_events_v6490.json"
STATUS = ROOT / "manifests" / "v6_match_context_axes_v6490_status.json"
LEDGER_SCHEMA = "V6.49.0-match-context-axes-ledger-r1"
EVENT_SCHEMA = "V6.49.0-match-context-axes-event-r1"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def parse_dt(value: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def sha_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def fixture_key(fi: dict[str, Any]) -> str:
    return "|".join(str(fi.get(k) or "") for k in ("competition_id", "kickoff_at", "home_team", "away_team"))


def base_context_events() -> dict[str, dict[str, Any]]:
    data = load(BASE_LEDGER)
    out: dict[str, dict[str, Any]] = {}
    for e in data.get("events") or []:
        if isinstance(e, dict) and e.get("event_type") == "CONTEXT_DECISION_FROZEN":
            out[str(e.get("fixture_key") or "")] = e
    return out


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    data = load(LEDGER)
    if data.get("schema_version") != LEDGER_SCHEMA or not isinstance(data.get("events"), list):
        raise RuntimeError("invalid V6.49.0 axes ledger")
    return data


def append_event(ledger: dict[str, Any], key: str, observed: datetime, payload: dict[str, Any]) -> None:
    events = ledger["events"]
    event = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": "MATCH_CONTEXT_AXES_FROZEN",
        "event_timestamp_utc": observed.isoformat(),
        "fixture_key": key,
        "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS",
        "payload": payload,
    }
    event["event_hash"] = sha_json(event)
    events.append(event)


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"; errors: list[str] = []
    for i, e in enumerate(ledger.get("events") or [], 1):
        if e.get("sequence") != i: errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev: errors.append(f"previous_hash:{i}")
        copy = dict(e); recorded = copy.pop("event_hash", None)
        if recorded != sha_json(copy): errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events") or []), "tip_hash": prev, "errors": errors}


def main() -> int:
    freeze = load(FREEZE)
    epoch = parse_dt(freeze.get("freeze_timestamp_utc"))
    if freeze.get("status") != "FROZEN" or epoch is None or freeze.get("historical_backfill") is not False:
        raise RuntimeError("invalid V6.49.0 freeze")
    base = base_context_events()
    ledger = load_ledger()
    before = audit(ledger)
    if before["status"] != "PASS": raise RuntimeError(str(before))
    existing = {str(e.get("fixture_key") or "") for e in ledger["events"]}
    scan = {"files_seen": 0, "before_epoch": 0, "no_base_context_freeze": 0, "no_new_axes": 0, "already_frozen": 0, "new_events": 0}

    for path in sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []:
        scan["files_seen"] += 1
        doc = load(path); observed = parse_dt(doc.get("observed_at_utc")); fi = doc.get("fixture_identity") or {}
        kickoff = parse_dt(fi.get("kickoff_at")); key = fixture_key(fi)
        if observed is None or kickoff is None or observed >= kickoff:
            continue
        if observed < epoch:
            scan["before_epoch"] += 1; continue
        if key in existing:
            scan["already_frozen"] += 1; continue
        base_event = base.get(key)
        if base_event is None:
            scan["no_base_context_freeze"] += 1; continue
        context = doc.get("context") or {}; manager = context.get("manager") or {}; deltas = context.get("material_roster_delta") or []
        home_mgr = manager.get("home") if isinstance(manager.get("home"), dict) else {}
        away_mgr = manager.get("away") if isinstance(manager.get("away"), dict) else {}
        manager_home_verified = bool(home_mgr.get("verified") is True and str(home_mgr.get("name") or "").strip())
        manager_away_verified = bool(away_mgr.get("verified") is True and str(away_mgr.get("name") or "").strip())
        clean_deltas = [x for x in deltas if isinstance(x, dict) and str(x.get("delta_type") or "").strip()]
        if not (manager_home_verified or manager_away_verified or clean_deltas):
            scan["no_new_axes"] += 1; continue
        confirmed = [x for x in clean_deltas if str(x.get("delta_type") or "") in {"confirmed_departure", "confirmed_arrival"}]
        append_event(ledger, key, observed, {
            "fixture_identity": fi,
            "context_decision_event_hash": base_event.get("event_hash"),
            "context_decision_freeze_at_utc": base_event.get("event_timestamp_utc"),
            "evidence_path": str(path.relative_to(ROOT)),
            "source": doc.get("source"),
            "manager": {"home": home_mgr, "away": away_mgr},
            "manager_features": {"home_verified": manager_home_verified, "away_verified": manager_away_verified, "both_verified": manager_home_verified and manager_away_verified},
            "material_roster_delta": clean_deltas,
            "confirmed_material_roster_delta_count": len(confirmed),
            "pending_delta_count": sum(1 for x in clean_deltas if str(x.get("delta_type") or "").startswith("pending_")),
            "governance": {"pre_kickoff": observed < kickoff, "base_context_freeze_required": True, "historical_backfill": False, "probability_mutation": False, "formal_weight": 0},
        })
        existing.add(key); scan["new_events"] += 1

    after = audit(ledger)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mgr_home = mgr_away = mgr_both = confirmed_deltas = pending_deltas = 0
    by_comp: dict[str, int] = {}
    for e in ledger["events"]:
        p = e.get("payload") or {}; mf = p.get("manager_features") or {}; fi = p.get("fixture_identity") or {}
        mgr_home += int(mf.get("home_verified") is True); mgr_away += int(mf.get("away_verified") is True); mgr_both += int(mf.get("both_verified") is True)
        confirmed_deltas += int(p.get("confirmed_material_roster_delta_count") or 0); pending_deltas += int(p.get("pending_delta_count") or 0)
        cid = str(fi.get("competition_id") or ""); by_comp[cid] = by_comp.get(cid, 0) + 1 if cid else by_comp.get(cid, 0)
    status = {
        "schema_version": "V6.49.0-match-context-axes-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if after["status"] == "PASS" else "FAIL",
        "freeze": freeze,
        "ledger_audit": after,
        "scan": scan,
        "coverage": {"manager_home_verified": mgr_home, "manager_away_verified": mgr_away, "manager_both_verified": mgr_both, "confirmed_material_roster_deltas": confirmed_deltas, "pending_deltas_not_applied": pending_deltas},
        "by_competition": dict(sorted((k, v) for k, v in by_comp.items() if k)),
        "governance": {"historical_backfill": False, "pending_transfer_not_counted_as_completed_delta": True, "probability_mutation": False, "formal_weight": 0, "current_rule_change": False},
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status["status"], "scan": scan, "coverage": status["coverage"]}, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
