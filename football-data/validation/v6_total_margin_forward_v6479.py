#!/usr/bin/env python3
"""V6.47.9 no-backfill forward challenge for the V6.47.8 nominated matrix.

Candidate: direct total at competition-only capacity + conditional D|T at full context
capacity. Reference: competition-only total + competition-only D|T. Both are frozen for
the same future fixture from result history strictly BEFORE the market event freeze.

The V6.47.8 nomination was post-hoc on fixed1000, so only this new prospective epoch can
supply promotion-quality evidence. No historical event is backfilled.
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

import evaluate_direct_total_margin_matrix_v6477 as core
import audit_total_margin_capacity_ablation_v6478 as ablation
from platform_core import PlatformError, atomic_write_json, load_json, normalize_team_token, parse_iso_datetime, sha256_json

BASE_LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
ABLATION_STATUS = ROOT / "manifests" / "v6_total_margin_capacity_ablation_v6478_status.json"
FREEZE = ROOT / "manifests" / "v6_total_margin_forward_v6479_freeze.json"
LEDGER = ROOT / "forward" / "v6_total_margin_events_v6479.json"
STATUS = ROOT / "manifests" / "v6_total_margin_forward_v6479_status.json"

FREEZE_SCHEMA = "V6.47.9-total-margin-forward-freeze-r1"
LEDGER_SCHEMA = "V6.47.9-total-margin-forward-ledger-r1"
EVENT_SCHEMA = "V6.47.9-total-margin-forward-event-r1"
MIN_RESULT_AGE = timedelta(hours=2)
MIN_SETTLED = 100
D = core.DIRECTIONS
EPS = 1e-15


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def event_hash(x: dict[str, Any]) -> str:
    return sha256_json(x)


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load_json(FREEZE)
        if x.get("schema_version") != FREEZE_SCHEMA or x.get("status") != "FROZEN":
            raise PlatformError("invalid V6.47.9 freeze")
        return x
    status = load_json(ABLATION_STATUS)
    nominated = status.get("nominated_forward_configuration")
    if nominated != "total=comp|margin=full":
        raise PlatformError(f"unexpected V6.47.8 nomination: {nominated}")
    x = {
        "schema_version": FREEZE_SCHEMA,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "ablation_status_sha256": sha256_json(status),
        "candidate": {"total_level": "comp", "margin_level": "full"},
        "reference": {"total_level": "comp", "margin_level": "comp"},
        "history_cutoff_rule": "only processed 90m results with match timestamp strictly before prediction freeze timestamp",
        "same_day_policy": "same-calendar-day historical results are excluded to avoid unknown intraday ordering",
        "historical_backfill": False,
        "formal_weight": 0,
        "governance": {"automatic_promotion": False, "runtime_probability_change": False, "current_rule_change": False},
    }
    atomic_write_json(FREEZE, x)
    return x


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": LEDGER_SCHEMA, "events": []}
    x = load_json(LEDGER)
    if x.get("schema_version") != LEDGER_SCHEMA or not isinstance(x.get("events"), list):
        raise PlatformError("invalid V6.47.9 ledger")
    return x


def append(ledger: dict[str, Any], typ: str, mid: str, ts: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    e = {"schema_version": EVENT_SCHEMA, "sequence": len(events)+1, "event_type": typ, "event_timestamp_utc": ts, "match_id": mid, "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS", "payload": payload}
    e["event_hash"] = event_hash(e); events.append(e); return e


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"; errors = []
    for i, e in enumerate(ledger.get("events", []), 1):
        if e.get("sequence") != i: errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev: errors.append(f"previous_hash:{i}")
        c = dict(e); recorded = c.pop("event_hash", None)
        if recorded != event_hash(c): errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events", [])), "tip_hash": prev, "errors": errors}


def pred_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "MATRIX_PREDICTION_FROZEN"}


def settled_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["match_id"]): e for e in ledger["events"] if e.get("event_type") == "RESULT_SETTLED"}


def base_predictions() -> list[dict[str, Any]]:
    if not BASE_LEDGER.exists(): return []
    x = load_json(BASE_LEDGER)
    return [e for e in x.get("events", []) if isinstance(e, dict) and e.get("event_type") == "MARKET_PREDICTION_FROZEN"]


def model_as_of(cutoff: datetime, all_rows: list[dict[str, Any]]) -> core.OnlineModel:
    model = core.OnlineModel()
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cutoff_day = cutoff.date().isoformat()
    for r in all_rows:
        day = str(r["date"])[:10]
        if day >= cutoff_day:
            continue
        days[day].append(r)
    for day in sorted(days):
        batch_rows = days[day]
        frozen = [(r, model.features(r)) for r in batch_rows]
        model.update_batch(frozen)
    return model


def serialise_prediction(p: dict[str, Any]) -> dict[str, Any]:
    score_probs = {f"{h}-{a}": v for (h, a), v in p["matrix"].items()}
    score_top = sorted(score_probs.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "total": p["total"],
        "result": p["result"],
        "score_top10": [{"score": s, "probability": q} for s, q in score_top],
        "score_matrix": score_probs,
        "tail15plus_probability": p["tail15plus"],
        "audit": p["audit"],
    }


def scan(now: datetime, freeze: dict[str, Any], ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter(); epoch = parse_iso_datetime(freeze["freeze_timestamp_utc"], "epoch")
    existing = pred_events(ledger); all_rows, _ = core.read_rows(); model_cache: dict[str, core.OnlineModel] = {}
    for e in base_predictions():
        mid = str(e.get("match_id") or "")
        if not mid or mid in existing:
            if mid in existing: stats["already_frozen"] += 1
            continue
        try:
            ts = parse_iso_datetime(str(e.get("event_timestamp_utc") or ""), "event timestamp")
            if ts < epoch: stats["before_epoch"] += 1; continue
            payload = e.get("payload") or {}; fi = payload.get("fixture_identity") or {}
            ko = parse_iso_datetime(str(fi.get("kickoff_at") or ""), "kickoff")
            if ts >= ko or now >= ko: stats["not_prospectively_freezable"] += 1; continue
            cache_key = ts.date().isoformat()
            if cache_key not in model_cache:
                model_cache[cache_key] = model_as_of(ts, all_rows)
            model = model_cache[cache_key]
            row = {"competition_id": str(fi.get("competition_id") or ""), "home_team": str(fi.get("home_team") or ""), "away_team": str(fi.get("away_team") or "")}
            cand = ablation.mixed_predict(model, row, "comp", "full")
            ref = ablation.mixed_predict(model, row, "comp", "comp")
            out = {
                "base_market_prediction_event_hash": e.get("event_hash"),
                "fixture_identity": fi,
                "history_cutoff_utc": ts.isoformat(),
                "candidate": serialise_prediction(cand),
                "reference": serialise_prediction(ref),
                "configuration": {"candidate": "total=comp|margin=full", "reference": "total=comp|margin=comp"},
            }
            append(ledger, "MATRIX_PREDICTION_FROZEN", mid, ts.isoformat(), out); existing[mid] = ledger["events"][-1]
            stats["new_predictions"] += 1
        except Exception:
            stats["event_rejected"] += 1
    return dict(sorted(stats.items()))


def result_map() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not RESULTS.exists(): return {}
    x = load_json(RESULTS); out = {}
    for r in x.get("results", []):
        if not isinstance(r, dict): continue
        key = (str(r.get("competition_id") or ""), str(r.get("kickoff_at") or ""), normalize_team_token(str(r.get("home_team") or "")), normalize_team_token(str(r.get("away_team") or "")))
        if all(key): out[key] = r
    return out


def settle(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter(); preds = pred_events(ledger); settled = settled_events(ledger); receipts = result_map()
    for mid, pe in sorted(preds.items()):
        if mid in settled: continue
        fi = pe["payload"]["fixture_identity"]; ko = parse_iso_datetime(str(fi["kickoff_at"]), "kickoff")
        if now < ko + MIN_RESULT_AGE: stats["not_old_enough"] += 1; continue
        key = (str(fi["competition_id"]), str(fi["kickoff_at"]), normalize_team_token(str(fi["home_team"])), normalize_team_token(str(fi["away_team"])))
        rec = receipts.get(key)
        if not rec: stats["official_receipt_not_yet_available"] += 1; continue
        append(ledger, "RESULT_SETTLED", mid, now.isoformat(), {"prediction_event_hash": pe["event_hash"], "result": {"home_goals_90": int(rec["home_goals_90"]), "away_goals_90": int(rec["away_goals_90"]), "actual_result": str(rec["actual_result"])}, "official_result_receipt_sha256": sha256_json(rec), "official_result_source": rec.get("source")})
        stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def arm_metric(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    n = len(rows)
    if not n: return {"count": 0}
    total1 = total2 = score1 = score3 = result1 = 0; tll = rps = sll = rb = 0.0
    for r in rows:
        p = r[arm]; hg = r["hg"]; ag = r["ag"]; tc = core.total_cat(hg, ag); actual_res = core.result_direction(hg, ag)
        tr = p["total"]; tk = core.topk(tr, 2); total1 += int(tk[0] == tc); total2 += int(tc in tk); tll -= math.log(max(EPS, tr[tc])); rps += core.rps_total(tr, tc)
        scores = {tuple(map(int, k.split("-"))): float(v) for k, v in p["score_matrix"].items()}; sk = core.topk(scores, 3); score1 += int(sk[0] == (hg, ag)); score3 += int((hg, ag) in sk); sll -= math.log(max(EPS, scores.get((hg, ag), 0.0)))
        rp = p["result"]; result1 += int(max(D, key=lambda d: rp[d]) == actual_res); y = {d: 1.0 if d == actual_res else 0.0 for d in D}; rb += sum((rp[d]-y[d])**2 for d in D)
    return {"count": n, "total_top1_accuracy": total1/n, "total_top2_accuracy": total2/n, "total_log_loss": tll/n, "total_rps": rps/n, "exact_score_top1_accuracy": score1/n, "exact_score_top3_accuracy": score3/n, "exact_score_log_loss": sll/n, "result_top1_accuracy": result1/n, "result_brier": rb/n}


def evaluate(ledger: dict[str, Any]) -> dict[str, Any]:
    preds = pred_events(ledger); settled = settled_events(ledger); rows = []
    for mid, se in settled.items():
        pe = preds.get(mid)
        if not pe or se["payload"].get("prediction_event_hash") != pe.get("event_hash"): continue
        res = se["payload"]["result"]; rows.append({"candidate": pe["payload"]["candidate"], "reference": pe["payload"]["reference"], "hg": int(res["home_goals_90"]), "ag": int(res["away_goals_90"])})
    cand = arm_metric(rows, "candidate"); ref = arm_metric(rows, "reference")
    gates = {
        "minimum_settled": cand.get("count", 0) >= MIN_SETTLED,
        "total_log_nonworse": cand.get("total_log_loss", math.inf) <= ref.get("total_log_loss", -math.inf) + 1e-12 if cand.get("count",0) else False,
        "total_rps_nonworse": cand.get("total_rps", math.inf) <= ref.get("total_rps", -math.inf) + 1e-12 if cand.get("count",0) else False,
        "score_log_nonworse": cand.get("exact_score_log_loss", math.inf) <= ref.get("exact_score_log_loss", -math.inf) + 1e-12 if cand.get("count",0) else False,
        "score_top1_nonworse": cand.get("exact_score_top1_accuracy", -1) >= ref.get("exact_score_top1_accuracy", 2) - 1e-12 if cand.get("count",0) else False,
    }
    return {"candidate": cand, "reference": ref, "forward_gate": {"minimum_settled": MIN_SETTLED, "results": gates, "all_pass": all(gates.values())}, "decision": "FORWARD_GATE_PASSED_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_FORWARD_SAMPLE_OR_QUALITY", "automatic_promotion": False}


def main() -> int:
    now = utc_now(); freeze = ensure_freeze(now); ledger = load_ledger(); before = audit(ledger)
    if before["status"] != "PASS": raise PlatformError(str(before))
    scan_stats = scan(now, freeze, ledger); settle_stats = settle(now, ledger); after = audit(ledger)
    if after["status"] != "PASS": raise PlatformError(str(after))
    atomic_write_json(LEDGER, ledger); ev = evaluate(ledger)
    payload = {"schema_version": "V6.47.9-total-margin-forward-status-r1", "generated_at_utc": now.isoformat(), "formal_current_version": "V5.0.1", "status": "PASS", "freeze": freeze, "ledger_audit": after, "prediction_scan": scan_stats, "settlement_scan": settle_stats, "evaluation": ev, "governance": {"new_postfreeze_epoch": True, "historical_backfill": False, "official_result_receipts_only": True, "formal_weight": 0, "automatic_promotion": False, "runtime_probability_change": False, "current_rule_change": False}}
    atomic_write_json(STATUS, payload); print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
