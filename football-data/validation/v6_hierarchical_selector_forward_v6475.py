#!/usr/bin/env python3
"""V6.47.5 prospective forward sidecar for the V6.47.4 hierarchical 1X2 selector.

The historical model is built once from matches strictly before the frozen recent-two-
season benchmark window, then persisted inside a no-backfill epoch freeze. The selector
is subsequently applied to new V6.5.1 Kambi market freezes without changing their
probabilities. Old settled matches are never imported as prospective predictions.

Research only, formal_weight=0. A forward pass cannot auto-promote CURRENT.
"""
from __future__ import annotations

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

import evaluate_hierarchical_market_selector_v6474 as hist
from platform_core import PlatformError, atomic_write_json, load_json, normalize_team_token, parse_iso_datetime, sha256_json

BASE_LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
HIST_STATUS = ROOT / "manifests" / "v6_hierarchical_market_selector_v6474_status.json"
FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
LEDGER = ROOT / "forward" / "v6_hierarchical_selector_events_v6475.json"
STATUS = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_status.json"

FREEZE_SCHEMA = "V6.47.5-hierarchical-selector-forward-freeze-r1"
LEDGER_SCHEMA = "V6.47.5-hierarchical-selector-forward-ledger-r1"
EVENT_SCHEMA = "V6.47.5-hierarchical-selector-forward-event-r1"
MIN_RESULT_AGE = timedelta(hours=2)
D = ("home", "draw", "away")
MIN_SELECTED_N = 100
MIN_ACCURACY = 0.60
MIN_WILSON90_LOWER = 0.55
MIN_DOMAIN_COUNT = 5
MAX_DOMAIN_SHARE = 0.40
Z90 = 1.6448536269514722


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def wilson(hits: int, n: int, z: float = Z90) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    rad = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / den
    return max(0.0, center - rad), min(1.0, center + rad)


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load_json(FREEZE)
        if x.get("schema_version") != FREEZE_SCHEMA or x.get("status") != "FROZEN":
            raise PlatformError("invalid V6.47.5 freeze")
        return x

    hs = load_json(HIST_STATUS)
    if hs.get("decision") != "CHALLENGE_FORWARD_REQUIRED":
        raise PlatformError("V6.47.4 did not pass the fixed1000 challenge gate")
    threshold = hs.get("development_gate", {}).get("chosen_threshold")
    if threshold is None:
        raise PlatformError("missing frozen development threshold")

    benchmark = load_json(hist.BENCHMARK)
    all_rows = hist.read_all_market_rows()
    dev, cutoffs = hist.split_development(all_rows, benchmark)
    model = hist.build_reliability(dev)
    model_payload = {
        "model_schema": "V6.47.4-hierarchical-reliability-model-r1",
        "development_row_count": len(dev),
        "competition_cutoff_dates": cutoffs,
        "pmax_edges": list(hist.PMAX_EDGES),
        "prior_strength": hist.PRIOR_STRENGTH,
        "minimum_competition_cell_n": hist.MIN_COMP_CELL_N,
        "reliability_model": model,
    }
    x = {
        "schema_version": FREEZE_SCHEMA,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "historical_challenge_status_sha256": sha256_json(hs),
        "selector_threshold": float(threshold),
        "model": model_payload,
        "model_sha256": sha256_json(model_payload),
        "trained_domain_count": len(cutoffs),
        "trained_domains": sorted(cutoffs),
        "unseen_domain_policy": "ABSTAIN",
        "selection_rule": "select iff trained domain and frozen hierarchical reliability score >= frozen threshold",
        "historical_backfill": False,
        "governance": {
            "formal_current_version": "V5.0.1",
            "research_only": True,
            "formal_weight": 0,
            "automatic_promotion": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(FREEZE, x)
    return x


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    x = load_json(LEDGER)
    if x.get("schema_version") != LEDGER_SCHEMA or not isinstance(x.get("events"), list):
        raise PlatformError("invalid V6.47.5 ledger")
    return x


def event_hash(x: dict[str, Any]) -> str:
    return sha256_json(x)


def append(ledger: dict[str, Any], typ: str, mid: str, ts: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    e = {
        "schema_version": EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": typ,
        "event_timestamp_utc": ts,
        "match_id": mid,
        "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS",
        "payload": payload,
    }
    e["event_hash"] = event_hash(e)
    events.append(e)
    return e


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"; errors = []
    for i, e in enumerate(ledger.get("events", []), 1):
        if e.get("sequence") != i: errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev: errors.append(f"previous_hash:{i}")
        c = dict(e); recorded = c.pop("event_hash", None); expected = event_hash(c)
        if recorded != expected: errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events", [])), "tip_hash": prev, "errors": errors}


def prediction_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "SELECTOR_PREDICTION_FROZEN"}


def settlement_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "RESULT_SETTLED"}


def base_predictions() -> list[dict[str, Any]]:
    if not BASE_LEDGER.exists(): return []
    x = load_json(BASE_LEDGER)
    return [e for e in x.get("events", []) if isinstance(e, dict) and e.get("event_type") == "MARKET_PREDICTION_FROZEN"]


def score_for(fi: dict[str, Any], pred: dict[str, Any], freeze: dict[str, Any]) -> tuple[float | None, str, int]:
    cid = str(fi.get("competition_id") or "")
    trained = set(freeze.get("trained_domains") or [])
    if cid not in trained:
        return None, "UNSEEN_DOMAIN_ABSTAIN", 0
    probs = pred.get("probabilities") or {}
    pick = str(pred.get("pick") or "")
    if pick not in D or any(d not in probs for d in D):
        return None, "INVALID_BASE_PROBABILITIES", 0
    pmax = max(float(probs[d]) for d in D)
    row = {"competition_id": cid, "pick": pick, "pmax": pmax}
    model = freeze["model"]["reliability_model"]
    return hist.reliability_for(row, model)


def scan(now: datetime, freeze: dict[str, Any], ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter(); epoch = parse_iso_datetime(freeze["freeze_timestamp_utc"], "freeze")
    existing = prediction_events(ledger)
    for e in base_predictions():
        mid = str(e.get("match_id") or "")
        if not mid or mid in existing:
            if mid in existing: stats["already_frozen"] += 1
            continue
        try:
            event_ts = parse_iso_datetime(str(e.get("event_timestamp_utc") or ""), "base event timestamp")
            if event_ts < epoch:
                stats["before_epoch"] += 1; continue
            payload = e.get("payload") or {}; fi = payload.get("fixture_identity") or {}; pred = payload.get("prediction") or {}
            ko = parse_iso_datetime(str(fi.get("kickoff_at") or ""), "kickoff")
            if event_ts >= ko or now >= ko:
                stats["not_prospectively_freezable"] += 1; continue
            score, level, support = score_for(fi, pred, freeze)
            threshold = float(freeze["selector_threshold"])
            selected = score is not None and score >= threshold
            out = {
                "base_prediction_event_hash": e.get("event_hash"),
                "fixture_identity": fi,
                "market_source": payload.get("market_source"),
                "prediction": {
                    "probabilities": pred.get("probabilities"),
                    "pick": pred.get("pick"),
                    "pmax": max(float(v) for v in (pred.get("probabilities") or {}).values()) if pred.get("probabilities") else None,
                },
                "selector": {
                    "model_sha256": freeze["model_sha256"],
                    "threshold": threshold,
                    "reliability_score": score,
                    "reliability_level": level,
                    "reliability_support": support,
                    "selected": bool(selected),
                },
            }
            append(ledger, "SELECTOR_PREDICTION_FROZEN", mid, event_ts.isoformat(), out)
            existing[mid] = ledger["events"][-1]
            stats["new_predictions"] += 1
            stats["new_selected" if selected else "new_abstained"] += 1
            if level == "UNSEEN_DOMAIN_ABSTAIN": stats["unseen_domain_abstain"] += 1
        except Exception:
            stats["base_event_rejected"] += 1
    return dict(sorted(stats.items()))


def result_map() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not RESULTS.exists(): return {}
    x = load_json(RESULTS); out = {}
    for r in x.get("results", []):
        if not isinstance(r, dict): continue
        key = (
            str(r.get("competition_id") or ""), str(r.get("kickoff_at") or ""),
            normalize_team_token(str(r.get("home_team") or "")), normalize_team_token(str(r.get("away_team") or "")),
        )
        if all(key): out[key] = r
    return out


def settle(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter(); preds = prediction_events(ledger); settled = settlement_events(ledger); receipts = result_map()
    for mid, pe in sorted(preds.items()):
        if mid in settled: continue
        fi = pe["payload"]["fixture_identity"]
        ko = parse_iso_datetime(str(fi["kickoff_at"]), "kickoff")
        if now < ko + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1; continue
        key = (
            str(fi["competition_id"]), str(fi["kickoff_at"]),
            normalize_team_token(str(fi["home_team"])), normalize_team_token(str(fi["away_team"])),
        )
        rec = receipts.get(key)
        if not rec:
            stats["official_receipt_not_yet_available"] += 1; continue
        actual = str(rec.get("actual_result") or "")
        if actual not in D:
            stats["invalid_receipt"] += 1; continue
        append(ledger, "RESULT_SETTLED", mid, now.isoformat(), {
            "prediction_event_hash": pe["event_hash"],
            "result": {"home_goals_90": int(rec["home_goals_90"]), "away_goals_90": int(rec["away_goals_90"]), "actual_result": actual},
            "official_result_receipt_sha256": sha256_json(rec),
            "official_result_source": rec.get("source"),
        })
        stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n: return {"count": 0}
    hits = sum(r["pick"] == r["actual"] for r in rows)
    lo, hi = wilson(hits, n)
    ll = br = rps = 0.0
    by = Counter(r["competition_id"] for r in rows)
    for r in rows:
        p = r["p"]; y = {d: 1.0 if r["actual"] == d else 0.0 for d in D}
        ll -= math.log(max(1e-15, p[r["actual"]]))
        br += sum((p[d] - y[d]) ** 2 for d in D)
        rps += ((p["home"] - y["home"]) ** 2 + ((p["home"] + p["draw"]) - (y["home"] + y["draw"])) ** 2) / 2.0
    return {
        "count": n, "hits": hits, "accuracy": hits / n, "wilson90": [lo, hi],
        "log_loss": ll / n, "brier": br / n, "rps": rps / n,
        "domain_count": len(by), "max_domain_share": max(by.values()) / n if n else None,
        "by_competition_n": dict(sorted(by.items())),
    }


def evaluate(ledger: dict[str, Any]) -> dict[str, Any]:
    preds = prediction_events(ledger); settled = settlement_events(ledger); all_rows = []; selected_rows = []
    for mid, se in settled.items():
        pe = preds.get(mid)
        if not pe or se["payload"].get("prediction_event_hash") != pe.get("event_hash"): continue
        p = pe["payload"]["prediction"]; fi = pe["payload"]["fixture_identity"]; actual = se["payload"]["result"]["actual_result"]
        row = {"competition_id": fi["competition_id"], "pick": p["pick"], "p": {d: float(p["probabilities"][d]) for d in D}, "actual": actual}
        all_rows.append(row)
        if pe["payload"]["selector"].get("selected") is True: selected_rows.append(row)
    sm = metrics(selected_rows); allm = metrics(all_rows)
    gates = {
        "selected_n": sm.get("count", 0) >= MIN_SELECTED_N,
        "accuracy": sm.get("accuracy") is not None and sm.get("accuracy") >= MIN_ACCURACY,
        "wilson90_lower": bool(sm.get("wilson90")) and sm["wilson90"][0] is not None and sm["wilson90"][0] >= MIN_WILSON90_LOWER,
        "domain_count": sm.get("domain_count", 0) >= MIN_DOMAIN_COUNT,
        "domain_concentration": sm.get("max_domain_share") is not None and sm["max_domain_share"] <= MAX_DOMAIN_SHARE,
    }
    return {
        "all_settled": allm,
        "selected_settled": sm,
        "selection_coverage_among_settled": sm.get("count", 0) / allm.get("count", 1) if allm.get("count", 0) else 0.0,
        "forward_gate": {
            "minimum_selected_n": MIN_SELECTED_N,
            "minimum_accuracy": MIN_ACCURACY,
            "minimum_wilson90_lower": MIN_WILSON90_LOWER,
            "minimum_domain_count": MIN_DOMAIN_COUNT,
            "maximum_domain_share": MAX_DOMAIN_SHARE,
            "results": gates,
            "minimum_sample_met": all(gates.values()),
        },
        "decision": "FORWARD_GATE_PASSED_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_FORWARD_SAMPLE_OR_QUALITY",
        "automatic_promotion": False,
    }


def main() -> int:
    now = utc_now(); freeze = ensure_freeze(now); ledger = load_ledger()
    before = audit(ledger)
    if before["status"] != "PASS": raise PlatformError(str(before))
    scan_stats = scan(now, freeze, ledger); settle_stats = settle(now, ledger); after = audit(ledger)
    if after["status"] != "PASS": raise PlatformError(str(after))
    atomic_write_json(LEDGER, ledger)
    ev = evaluate(ledger)
    payload = {
        "schema_version": "V6.47.5-hierarchical-selector-forward-status-r1",
        "generated_at_utc": now.isoformat(), "formal_current_version": "V5.0.1", "status": "PASS",
        "freeze": freeze, "ledger_audit": after, "prediction_scan": scan_stats, "settlement_scan": settle_stats,
        "evaluation": ev,
        "governance": {
            "new_postfreeze_epoch": True, "historical_backfill": False, "official_result_receipts_only": True,
            "probability_mutation": False, "formal_weight": 0, "automatic_promotion": False,
            "runtime_probability_change": False, "current_rule_change": False,
        },
    }
    atomic_write_json(STATUS, payload); print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
