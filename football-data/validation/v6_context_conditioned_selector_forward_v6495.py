#!/usr/bin/env python3
"""V6.49.5 context-conditioned 90m 1X2 forward audit.

Purpose
-------
Create a clean prospective bridge between the frozen V6.49.2 selector and exact-match
pre-kickoff context. This script DOES NOT change probabilities, picks, selector thresholds,
or formal weights. It only freezes a new selector prediction at a context decision freeze
that occurs after the V6.49.5 epoch and while the fixture is still in the future, then
attaches auditable context descriptors for later forward failure-regime analysis.

No context veto exists at freeze. Any future veto candidate requires >=100 selected settled
context-conditioned events and separate review; no automatic promotion is possible.
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
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_fresh_market_forward_challengers_v6492 as base
from platform_core import load_json, normalize_team_token, sha256_json

FREEZE = ROOT / "manifests" / "v6_context_conditioned_selector_v6495_freeze.json"
CONTEXT_LEDGER = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
AXES_LEDGER = ROOT / "forward" / "v6_match_context_axes_events_v6490.json"
SELECTOR_FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
LEDGER = ROOT / "forward" / "v6_context_conditioned_selector_events_v6495.json"
STATUS = ROOT / "manifests" / "v6_context_conditioned_selector_v6495_status.json"

LEDGER_SCHEMA = "V6.49.5-context-conditioned-selector-ledger-r1"
EVENT_SCHEMA = "V6.49.5-context-conditioned-selector-event-r1"
MIN_RESULT_AGE = timedelta(hours=2)
D = ("home", "draw", "away")


def now_utc() -> datetime:
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


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"
    errors: list[str] = []
    events = ledger.get("events") or []
    for i, e in enumerate(events, 1):
        if e.get("sequence") != i:
            errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev:
            errors.append(f"previous_hash:{i}")
        c = dict(e)
        recorded = c.pop("event_hash", None)
        if recorded != sha256_json(c):
            errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(events), "tip_hash": prev, "errors": errors}


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    x = load(LEDGER)
    if x.get("schema_version") != LEDGER_SCHEMA or not isinstance(x.get("events"), list):
        raise RuntimeError("invalid V6.49.5 ledger")
    return x


def append_event(ledger: dict[str, Any], typ: str, fixture_key: str, ts: datetime, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    e = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": typ,
        "event_timestamp_utc": ts.isoformat(),
        "fixture_key": fixture_key,
        "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS",
        "payload": payload,
    }
    e["event_hash"] = sha256_json(e)
    events.append(e)
    return e


def fixture_key(fi: dict[str, Any]) -> str:
    return "|".join(str(fi.get(k) or "") for k in ("competition_id", "kickoff_at", "home_team", "away_team"))


def result_key(fi: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(fi.get("competition_id") or ""),
        str(fi.get("kickoff_at") or ""),
        normalize_team_token(str(fi.get("home_team") or "")),
        normalize_team_token(str(fi.get("away_team") or "")),
    )


def result_map() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    x = load(RESULTS)
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in x.get("results") or []:
        if not isinstance(r, dict):
            continue
        key = (
            str(r.get("competition_id") or ""),
            str(r.get("kickoff_at") or ""),
            normalize_team_token(str(r.get("home_team") or "")),
            normalize_team_token(str(r.get("away_team") or "")),
        )
        if all(key):
            out[key] = r
    return out


def axes_by_fixture() -> dict[str, dict[str, Any]]:
    x = load(AXES_LEDGER)
    out: dict[str, dict[str, Any]] = {}
    for e in x.get("events") or []:
        if not isinstance(e, dict) or e.get("event_type") != "MATCH_CONTEXT_AXES_FROZEN":
            continue
        key = str(e.get("fixture_key") or "")
        if key:
            out[key] = e
    return out


def context_doc_flags(context_event: dict[str, Any]) -> dict[str, Any]:
    p = context_event.get("payload") or {}
    ce = p.get("context_evidence") or {}
    rel = str(ce.get("path") or "")
    doc = load(ROOT / rel) if rel else {}
    ctx = doc.get("context") or {}
    gov = doc.get("governance") or {}
    return {
        "source_conflict": bool(ctx.get("source_conflict_note") or gov.get("source_conflict_preserved") is True),
        "source_tier": (doc.get("source") or {}).get("source_tier"),
        "evidence_path": rel or None,
    }


def context_features(context_event: dict[str, Any], axes_event: dict[str, Any] | None) -> dict[str, Any]:
    p = context_event.get("payload") or {}
    ce = p.get("context_evidence") or {}
    availability = ce.get("availability") or {}
    predicted = ce.get("predicted_xi") or {}
    home_abs = list(availability.get("home") or [])
    away_abs = list(availability.get("away") or [])
    home_xi = list(predicted.get("home") or [])
    away_xi = list(predicted.get("away") or [])
    f = p.get("context_features_available") or {}
    flags = context_doc_flags(context_event)

    manager_both = False
    confirmed_delta_count = 0
    pending_delta_count = 0
    axes_hash = None
    if isinstance(axes_event, dict):
        axes_hash = axes_event.get("event_hash")
        ap = axes_event.get("payload") or {}
        mf = ap.get("manager_features") or {}
        manager_both = mf.get("both_verified") is True
        confirmed_delta_count = int(ap.get("confirmed_material_roster_delta_count") or 0)
        pending_delta_count = int(ap.get("pending_delta_count") or 0)

    return {
        "home_availability_evidence": bool(home_abs),
        "away_availability_evidence": bool(away_abs),
        "both_availability_evidence": bool(home_abs) and bool(away_abs),
        "home_absence_count": len(home_abs),
        "away_absence_count": len(away_abs),
        "absence_count_difference_home_minus_away": len(home_abs) - len(away_abs),
        "home_predicted_xi_complete": len(home_xi) == 11 and f.get("home_predicted_xi") is True,
        "away_predicted_xi_complete": len(away_xi) == 11 and f.get("away_predicted_xi") is True,
        "full_predicted_xi": len(home_xi) == 11 and len(away_xi) == 11 and f.get("home_predicted_xi") is True and f.get("away_predicted_xi") is True,
        "manager_both_verified": manager_both,
        "confirmed_material_roster_delta_count": confirmed_delta_count,
        "pending_delta_count_not_applied": pending_delta_count,
        "source_conflict": flags["source_conflict"],
        "source_tier": flags["source_tier"],
        "context_evidence_path": flags["evidence_path"],
        "axes_event_hash": axes_hash,
    }


def picked_side_absence_relation(pick: str, cf: dict[str, Any]) -> str:
    if not cf.get("both_availability_evidence"):
        return "AVAILABILITY_NOT_BOTH_SIDES"
    h = int(cf.get("home_absence_count") or 0)
    a = int(cf.get("away_absence_count") or 0)
    if pick == "draw":
        return "DRAW_PICK"
    picked = h if pick == "home" else a
    opp = a if pick == "home" else h
    if picked > opp:
        return "PICKED_SIDE_MORE_REPORTED_ABSENCES"
    if picked < opp:
        return "PICKED_SIDE_FEWER_REPORTED_ABSENCES"
    return "REPORTED_ABSENCE_COUNTS_EQUAL"


def freeze_predictions(now: datetime, ledger: dict[str, Any], freeze: dict[str, Any]) -> dict[str, int]:
    epoch = parse_dt(freeze.get("freeze_timestamp_utc"))
    if epoch is None:
        raise RuntimeError("invalid V6.49.5 epoch")
    selector_freeze = load(SELECTOR_FREEZE)
    if selector_freeze.get("status") != "FROZEN":
        raise RuntimeError("selector source freeze is not frozen")
    axes = axes_by_fixture()
    existing = {
        str(e.get("fixture_key") or "")
        for e in ledger.get("events") or []
        if e.get("event_type") == "CONTEXT_SELECTOR_PREDICTION_FROZEN"
    }
    stats = Counter()
    context = load(CONTEXT_LEDGER)
    for ce in context.get("events") or []:
        if not isinstance(ce, dict) or ce.get("event_type") != "CONTEXT_DECISION_FROZEN":
            continue
        stats["context_events_seen"] += 1
        observed = parse_dt(ce.get("event_timestamp_utc"))
        p = ce.get("payload") or {}
        fi = p.get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at"))
        key = fixture_key(fi)
        if observed is None or ko is None or not key:
            stats["bad_identity_or_time"] += 1
            continue
        if observed < epoch:
            stats["before_epoch"] += 1
            continue
        if now >= ko:
            stats["fixture_not_future_at_processing"] += 1
            continue
        if key in existing:
            stats["already_frozen"] += 1
            continue
        market = p.get("market") or {}
        odds = market.get("one_x_two") or {}
        if not all(k in odds for k in D):
            stats["missing_1x2"] += 1
            continue
        try:
            q = base.devig(odds)
        except Exception:
            stats["bad_1x2"] += 1
            continue
        cid = str(fi.get("competition_id") or "")
        pick, pmax, score, level, support, selected = base.reliability(q, cid, selector_freeze)
        cf = context_features(ce, axes.get(key))
        relation = picked_side_absence_relation(pick, cf)
        append_event(ledger, "CONTEXT_SELECTOR_PREDICTION_FROZEN", key, observed, {
            "fixture_identity": fi,
            "context_decision_event_hash": ce.get("event_hash"),
            "decision_freeze_at_utc": observed.isoformat(),
            "market": {
                "observed_at_utc": market.get("observed_at_utc"),
                "path": market.get("path"),
                "one_x_two": odds,
                "provider_name": market.get("provider_name"),
                "provider_group": market.get("provider_group"),
            },
            "prediction": {"probabilities": q, "pick": pick, "pmax": pmax},
            "selector": {
                "source_model_sha256": selector_freeze.get("model_sha256"),
                "threshold": selector_freeze.get("selector_threshold"),
                "reliability_score": score,
                "reliability_level": level,
                "reliability_support": support,
                "selected": selected,
            },
            "context_features": cf,
            "picked_side_absence_relation": relation,
            "governance": {
                "source_context_event_after_epoch": observed >= epoch,
                "fixture_future_when_prediction_created": now < ko,
                "prediction_rule_change": False,
                "context_probability_adjustment": False,
                "context_veto_applied": False,
                "historical_result_backfill": False,
                "formal_weight": 0,
            },
        })
        existing.add(key)
        stats["new_predictions"] += 1
        stats["new_selected" if selected else "new_abstained"] += 1
    return dict(sorted(stats.items()))


def settle(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    preds = {
        str(e.get("fixture_key") or ""): e
        for e in ledger.get("events") or []
        if e.get("event_type") == "CONTEXT_SELECTOR_PREDICTION_FROZEN"
    }
    settled = {
        str(e.get("fixture_key") or ""): e
        for e in ledger.get("events") or []
        if e.get("event_type") == "RESULT_SETTLED"
    }
    results = result_map()
    stats = Counter()
    for key, pe in preds.items():
        if key in settled:
            stats["already_settled"] += 1
            continue
        p = pe.get("payload") or {}
        fi = p.get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at"))
        if ko is None or now < ko + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1
            continue
        r = results.get(result_key(fi))
        if not r:
            stats["official_receipt_missing"] += 1
            continue
        append_event(ledger, "RESULT_SETTLED", key, now, {
            "prediction_event_hash": pe.get("event_hash"),
            "result": {
                "home_goals_90": int(r["home_goals_90"]),
                "away_goals_90": int(r["away_goals_90"]),
                "actual_result": str(r["actual_result"]),
            },
            "official_result_receipt_sha256": sha256_json(r),
            "official_result_source": r.get("source"),
        })
        stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def row_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = sum(r["pick"] == r["actual"] for r in rows)
    ll = br = rps = 0.0
    by_comp = Counter(r["competition_id"] for r in rows)
    for r in rows:
        p = r["probabilities"]
        y = {d: 1.0 if d == r["actual"] else 0.0 for d in D}
        ll -= math.log(max(1e-15, p[r["actual"]]))
        br += sum((p[d] - y[d]) ** 2 for d in D)
        rps += ((p["home"] - y["home"]) ** 2 + ((p["home"] + p["draw"]) - (y["home"] + y["draw"])) ** 2) / 2
    lo, hi = base.wilson(hits, n)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "wilson90": [lo, hi],
        "log_loss": ll / n,
        "brier": br / n,
        "rps": rps / n,
        "domain_count": len(by_comp),
        "by_competition_n": dict(sorted(by_comp.items())),
    }


def evaluation(ledger: dict[str, Any], freeze: dict[str, Any]) -> dict[str, Any]:
    preds = {
        str(e.get("fixture_key") or ""): e
        for e in ledger.get("events") or []
        if e.get("event_type") == "CONTEXT_SELECTOR_PREDICTION_FROZEN"
    }
    settlements = {
        str(e.get("fixture_key") or ""): e
        for e in ledger.get("events") or []
        if e.get("event_type") == "RESULT_SETTLED"
    }
    rows: list[dict[str, Any]] = []
    for key, se in settlements.items():
        pe = preds.get(key)
        if not pe:
            continue
        if (se.get("payload") or {}).get("prediction_event_hash") != pe.get("event_hash"):
            continue
        pp = pe.get("payload") or {}
        pred = pp.get("prediction") or {}
        actual = str(((se.get("payload") or {}).get("result") or {}).get("actual_result") or "")
        if actual not in D:
            continue
        rows.append({
            "competition_id": str((pp.get("fixture_identity") or {}).get("competition_id") or ""),
            "pick": str(pred.get("pick") or ""),
            "probabilities": {d: float((pred.get("probabilities") or {})[d]) for d in D},
            "actual": actual,
            "selected": (pp.get("selector") or {}).get("selected") is True,
            "context_features": pp.get("context_features") or {},
            "picked_side_absence_relation": pp.get("picked_side_absence_relation"),
        })
    selected = [r for r in rows if r["selected"]]

    strata: dict[str, Any] = {}
    strata["all_selected"] = row_metrics(selected)
    for label in (
        "PICKED_SIDE_MORE_REPORTED_ABSENCES",
        "PICKED_SIDE_FEWER_REPORTED_ABSENCES",
        "REPORTED_ABSENCE_COUNTS_EQUAL",
        "AVAILABILITY_NOT_BOTH_SIDES",
        "DRAW_PICK",
    ):
        strata[f"absence_relation::{label}"] = row_metrics([r for r in selected if r["picked_side_absence_relation"] == label])
    strata["full_predicted_xi"] = row_metrics([r for r in selected if (r["context_features"] or {}).get("full_predicted_xi") is True])
    strata["manager_both_verified"] = row_metrics([r for r in selected if (r["context_features"] or {}).get("manager_both_verified") is True])
    strata["source_conflict"] = row_metrics([r for r in selected if (r["context_features"] or {}).get("source_conflict") is True])
    strata["no_source_conflict"] = row_metrics([r for r in selected if (r["context_features"] or {}).get("source_conflict") is not True])

    minimum = int(freeze.get("minimum_selected_settled_before_any_context_veto_candidate_review") or 100)
    eligible_for_context_rule_review = len(selected) >= minimum
    return {
        "all_settled": row_metrics(rows),
        "selected_settled": row_metrics(selected),
        "selected_context_strata": strata,
        "context_rule_review_gate": {
            "minimum_selected_settled": minimum,
            "current_selected_settled": len(selected),
            "sample_gate_met": eligible_for_context_rule_review,
            "candidate_context_veto_rule": "NONE" if not eligible_for_context_rule_review else "REVIEW_STRATA_ONLY_NO_AUTO_RULE",
        },
        "decision": "PENDING_CONTEXT_EFFECT_SAMPLE" if not eligible_for_context_rule_review else "CONTEXT_EFFECT_REVIEW_ALLOWED_NO_AUTO_PROMOTION",
    }


def main() -> int:
    now = now_utc()
    freeze = load(FREEZE)
    if freeze.get("status") != "FROZEN" or freeze.get("historical_backfill") is not False:
        raise RuntimeError("invalid V6.49.5 freeze")
    ledger = load_ledger()
    before = audit(ledger)
    if before["status"] != "PASS":
        raise RuntimeError(str(before))
    freeze_scan = freeze_predictions(now, ledger, freeze)
    settlement_scan = settle(now, ledger)
    after = audit(ledger)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ev = evaluation(ledger, freeze)
    status = {
        "schema_version": "V6.49.5-context-conditioned-selector-status-r1",
        "generated_at_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if after["status"] == "PASS" else "FAIL",
        "freeze": freeze,
        "ledger_audit": after,
        "freeze_scan": freeze_scan,
        "settlement_scan": settlement_scan,
        "evaluation": ev,
        "governance": {
            "prediction_rule_change": False,
            "context_probability_adjustment": False,
            "context_veto_rule_active": False,
            "historical_backfill": False,
            "fixture_must_still_be_future_when_prediction_event_is_created": True,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status["status"],
        "freeze_scan": freeze_scan,
        "settlement_scan": settlement_scan,
        "selected_settled": (ev.get("selected_settled") or {}).get("count"),
        "decision": ev.get("decision"),
    }, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
