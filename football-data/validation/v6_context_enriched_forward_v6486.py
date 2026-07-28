#!/usr/bin/env python3
"""V6.48.6 immutable decision-freeze sidecar for live match context.

Each fixture is frozen once, only after a pre-kickoff FotMob context observation exists.
The decision freeze is the context observation timestamp. The sidecar binds the newest
complete prospective market snapshot observed no later than that decision freeze.
This avoids attaching context evidence retroactively to an older market-first event.

No probability is changed in this version. The ledger creates the clean prospective
sample required to test future orthogonal availability / predicted-XI features.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "match_context_live" / "fotmob"
MARKETS = ROOT / "evidence" / "markets_prospective"
FREEZE = ROOT / "manifests" / "v6_context_enriched_forward_v6486_freeze.json"
LEDGER = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
STATUS = ROOT / "manifests" / "v6_context_enriched_forward_v6486_status.json"
SCHEMA = "V6.48.6-context-enriched-forward-ledger-r1"
EVENT_SCHEMA = "V6.48.6-context-enriched-forward-event-r1"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(value: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def sha_json(obj: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def fixture_key(fi: dict[str, Any]) -> str:
    return "|".join(str(fi.get(k) or "") for k in ("competition_id", "kickoff_at", "home_team", "away_team"))


def ensure_freeze(ts: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        return load(FREEZE)
    payload = {
        "schema_version": "V6.48.6-context-enriched-forward-freeze-r1",
        "status": "FROZEN",
        "freeze_timestamp_utc": ts.isoformat(),
        "formal_current_version": "V5.0.1",
        "historical_backfill": False,
        "decision_freeze_rule": "first eligible pre-kickoff context observation after epoch; bind newest complete market snapshot observed <= context observation",
        "context_minimum": "at least one assigned availability candidate OR one explicit predicted XI",
        "formal_weight": 0,
        "automatic_promotion": False,
        "current_rule_change": False,
    }
    FREEZE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def market_index() -> dict[str, list[tuple[datetime, Path, dict[str, Any]]]]:
    out: dict[str, list[tuple[datetime, Path, dict[str, Any]]]] = {}
    if not MARKETS.exists():
        return out
    for path in MARKETS.glob("*.json"):
        x = load(path); observed = parse_dt(x.get("source_observed_at_utc") or x.get("freeze_utc")); kickoff = parse_dt(x.get("kickoff_utc"))
        if observed is None or kickoff is None or observed >= kickoff:
            continue
        if not all(isinstance(x.get(k), dict) for k in ("one_x_two", "asian_handicap", "over_under")):
            continue
        fi = {"competition_id": x.get("competition_id"), "kickoff_at": kickoff.isoformat(), "home_team": x.get("home_team"), "away_team": x.get("away_team")}
        key = fixture_key(fi); out.setdefault(key, []).append((observed, path, x))
    for rows in out.values():
        rows.sort(key=lambda z: z[0])
    return out


def context_docs() -> list[tuple[datetime, Path, dict[str, Any]]]:
    rows = []
    if not EVIDENCE.exists():
        return rows
    for path in EVIDENCE.glob("*.json"):
        x = load(path); observed = parse_dt(x.get("observed_at_utc")); fi = x.get("fixture_identity") or {}; kickoff = parse_dt(fi.get("kickoff_at"))
        if observed is None or kickoff is None or observed >= kickoff:
            continue
        elig = x.get("eligibility") or {}
        if not (elig.get("availability_candidate_present") is True or elig.get("predicted_xi_home_eligible") is True or elig.get("predicted_xi_away_eligible") is True):
            continue
        rows.append((observed, path, x))
    rows.sort(key=lambda z: (z[0], str(z[1])))
    return rows


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": SCHEMA, "events": []}
    x = load(LEDGER)
    if x.get("schema_version") != SCHEMA or not isinstance(x.get("events"), list):
        raise RuntimeError("invalid V6.48.6 ledger")
    return x


def append(ledger: dict[str, Any], fi: dict[str, Any], observed: datetime, payload: dict[str, Any]) -> None:
    events = ledger["events"]
    event = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": "CONTEXT_DECISION_FROZEN",
        "event_timestamp_utc": observed.isoformat(),
        "fixture_key": fixture_key(fi),
        "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS",
        "payload": payload,
    }
    event["event_hash"] = sha_json(event); events.append(event)


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"; errors = []
    for i, event in enumerate(ledger.get("events", []), 1):
        if event.get("sequence") != i: errors.append(f"sequence:{i}")
        if event.get("previous_event_hash") != prev: errors.append(f"previous_hash:{i}")
        copy = dict(event); recorded = copy.pop("event_hash", None)
        if recorded != sha_json(copy): errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events", [])), "tip_hash": prev, "errors": errors}


def main() -> int:
    current = now(); freeze = ensure_freeze(current); epoch = parse_dt(freeze.get("freeze_timestamp_utc"))
    if epoch is None: raise RuntimeError("invalid epoch")
    ledger = load_ledger(); before = audit(ledger)
    if before["status"] != "PASS": raise RuntimeError(str(before))
    existing = {str(e.get("fixture_key")) for e in ledger["events"] if e.get("event_type") == "CONTEXT_DECISION_FROZEN"}
    markets = market_index(); counts = {"context_docs_considered": 0, "before_epoch": 0, "already_frozen": 0, "market_snapshot_missing": 0, "new_events": 0}

    for observed, path, doc in context_docs():
        counts["context_docs_considered"] += 1
        fi = doc.get("fixture_identity") or {}; key = fixture_key(fi)
        if observed < epoch:
            counts["before_epoch"] += 1; continue
        if key in existing:
            counts["already_frozen"] += 1; continue
        eligible_markets = [row for row in markets.get(key, []) if row[0] <= observed]
        if not eligible_markets:
            counts["market_snapshot_missing"] += 1; continue
        market_observed, market_path, market = eligible_markets[-1]
        context = doc.get("context") or {}; availability = context.get("availability") or {}; predicted = context.get("predicted_xi") or {}
        payload = {
            "fixture_identity": fi,
            "decision_freeze_at_utc": observed.isoformat(),
            "market": {
                "observed_at_utc": market_observed.isoformat(), "path": str(market_path.relative_to(ROOT)),
                "one_x_two": market.get("one_x_two"), "asian_handicap": market.get("asian_handicap"), "over_under": market.get("over_under"),
                "provider_name": market.get("provider_name"), "provider_group": market.get("provider_group"),
            },
            "context_evidence": {
                "observed_at_utc": observed.isoformat(), "path": str(path.relative_to(ROOT)), "source": doc.get("source"),
                "availability": availability, "predicted_xi": predicted,
                "predicted_xi_verified_by_explicit_marker": context.get("predicted_xi_verified_by_explicit_marker"),
            },
            "context_features_available": {
                "home_availability": bool(availability.get("home")), "away_availability": bool(availability.get("away")),
                "home_predicted_xi": len(predicted.get("home") or []) == 11, "away_predicted_xi": len(predicted.get("away") or []) == 11,
            },
            "governance": {"market_observed_no_later_than_decision_freeze": market_observed <= observed, "context_observed_at_decision_freeze": True, "pre_kickoff": observed < (parse_dt(fi.get("kickoff_at")) or observed), "probability_mutation": False, "formal_weight": 0},
        }
        append(ledger, fi, observed, payload); existing.add(key); counts["new_events"] += 1

    after = audit(ledger); LEDGER.parent.mkdir(parents=True, exist_ok=True); LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    feature_counts = {"home_availability": 0, "away_availability": 0, "home_predicted_xi": 0, "away_predicted_xi": 0, "full_predicted_xi": 0}
    by_comp: dict[str, int] = {}
    for e in ledger["events"]:
        p = e.get("payload") or {}; f = p.get("context_features_available") or {}; cid = str((p.get("fixture_identity") or {}).get("competition_id") or "")
        if cid: by_comp[cid] = by_comp.get(cid, 0) + 1
        for k in ("home_availability", "away_availability", "home_predicted_xi", "away_predicted_xi"):
            feature_counts[k] += int(f.get(k) is True)
        feature_counts["full_predicted_xi"] += int(f.get("home_predicted_xi") is True and f.get("away_predicted_xi") is True)
    status = {
        "schema_version": "V6.48.6-context-enriched-forward-status-r1", "generated_at_utc": current.isoformat(), "formal_current_version": "V5.0.1", "status": "PASS" if after["status"] == "PASS" else "FAIL",
        "freeze": freeze, "ledger_audit": after, "scan": counts, "feature_counts": feature_counts, "by_competition": dict(sorted(by_comp.items())),
        "interpretation": "Clean prospective context+market decision freezes. No outcome model or formal weight is attached yet.",
        "governance": {"historical_backfill": False, "probability_mutation": False, "formal_weight": 0, "automatic_promotion": False, "current_rule_change": False},
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status["status"], "scan": counts, "feature_counts": feature_counts, "by_competition": status["by_competition"]}, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
