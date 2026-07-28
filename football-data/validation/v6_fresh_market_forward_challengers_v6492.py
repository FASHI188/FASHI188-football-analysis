#!/usr/bin/env python3
"""V6.49.2 fresh-market forward challengers.

Purpose
-------
Fix a forward-acquisition bottleneck without touching the old V6.47.5/V6.47.9 ledgers.
Those ledgers depended on a one-shot base prediction, so fixtures frozen by the base feed
before the challenger epochs could never enter the newer forward tests even when fresh
pre-kickoff market snapshots were later observed.

This sidecar freezes NEW challenger predictions only when:
* the fixture has not kicked off;
* kickoff is 1-72h ahead of the decision freeze;
* a complete prospective market snapshot was observed in the last 2h;
* the snapshot is not after the decision freeze;
* settlement scope is 90m including stoppage.

It runs two unchanged research challengers from the same fresh fixture set:
1) the frozen V6.47.4 hierarchical 1X2 reliability selector;
2) the V6.47.8 nominated direct-total / conditional-margin matrix
   (total=competition-only, margin=full) against comp/comp reference.

No old result is admitted as a prediction. No CURRENT or probability rule is changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import audit_total_margin_capacity_ablation_v6478 as ablation
import v6_total_margin_forward_v6479 as matrix_forward
from platform_core import load_json, normalize_team_token, sha256_json

MARKETS = ROOT / "evidence" / "markets_prospective"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
SELECTOR_SOURCE_FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
MATRIX_SOURCE_STATUS = ROOT / "manifests" / "v6_total_margin_capacity_ablation_v6478_status.json"
UNIFIED_STATUS = ROOT / "manifests" / "v6_unified_forward_pipeline_v6482_status.json"
FREEZE = ROOT / "manifests" / "v6_fresh_market_forward_challengers_v6492_freeze.json"
SELECTOR_LEDGER = ROOT / "forward" / "v6_fresh_selector_events_v6492.json"
MATRIX_LEDGER = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
STATUS = ROOT / "manifests" / "v6_fresh_market_forward_challengers_v6492_status.json"

SCHEMA_FREEZE = "V6.49.2-fresh-market-forward-freeze-r1"
SCHEMA_SELECTOR_LEDGER = "V6.49.2-fresh-selector-ledger-r1"
SCHEMA_MATRIX_LEDGER = "V6.49.2-fresh-matrix-ledger-r1"
SCHEMA_EVENT = "V6.49.2-fresh-forward-event-r1"
MAX_SNAPSHOT_AGE = timedelta(hours=2)
MIN_LEAD = timedelta(hours=1)
MAX_LEAD = timedelta(hours=72)
MIN_RESULT_AGE = timedelta(hours=2)
D = ("home", "draw", "away")
Z90 = 1.6448536269514722


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


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(raw: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(raw.get("competition_id") or ""),
        str(raw.get("kickoff_utc") or raw.get("kickoff_at") or ""),
        normalize_team_token(str(raw.get("home_team") or "")),
        normalize_team_token(str(raw.get("away_team") or "")),
    )


def match_id(key: tuple[str, str, str, str]) -> str:
    return "fresh_" + sha256_json({"competition_id": key[0], "kickoff": key[1], "home": key[2], "away": key[3]})[:24]


def devig(odds: dict[str, Any]) -> dict[str, float]:
    inv = {}
    for d in D:
        x = float(odds[d])
        if not math.isfinite(x) or x <= 1.0:
            raise ValueError(f"invalid odds {d}={x}")
        inv[d] = 1.0 / x
    s = sum(inv.values())
    return {d: inv[d] / s for d in D}


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load_json(FREEZE)
        if x.get("schema_version") != SCHEMA_FREEZE or x.get("status") != "FROZEN":
            raise RuntimeError("invalid V6.49.2 freeze")
        return x
    selector = load_json(SELECTOR_SOURCE_FREEZE)
    matrix = load_json(MATRIX_SOURCE_STATUS)
    if selector.get("status") != "FROZEN":
        raise RuntimeError("selector source freeze not frozen")
    if matrix.get("nominated_forward_configuration") != "total=comp|margin=full":
        raise RuntimeError("matrix nomination drift")
    x = {
        "schema_version": SCHEMA_FREEZE,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "selector_source_freeze_sha256": file_sha(SELECTOR_SOURCE_FREEZE),
        "matrix_source_status_sha256": file_sha(MATRIX_SOURCE_STATUS),
        "fresh_fixture_contract": {
            "fixture_must_be_future_at_decision_freeze": True,
            "minimum_lead_hours": 1,
            "maximum_lead_hours": 72,
            "maximum_market_snapshot_age_hours": 2,
            "choose_latest_snapshot_no_later_than_decision_freeze": True,
            "one_immutable_prediction_per_fixture_per_v6492_ledger": True,
        },
        "historical_result_backfill": False,
        "formal_weight": 0,
        "automatic_promotion": False,
        "current_rule_change": False,
    }
    FREEZE.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return x


def load_ledger(path: Path, schema: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": schema, "events": []}
    x = load_json(path)
    if x.get("schema_version") != schema or not isinstance(x.get("events"), list):
        raise RuntimeError(f"invalid ledger {path}")
    return x


def event_hash(x: dict[str, Any]) -> str:
    return sha256_json(x)


def append_event(ledger: dict[str, Any], typ: str, mid: str, ts: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    e = {
        "schema_version": SCHEMA_EVENT,
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
    prev = "GENESIS"; errors: list[str] = []
    for i, e in enumerate(ledger.get("events") or [], 1):
        if e.get("sequence") != i: errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev: errors.append(f"previous_hash:{i}")
        c = dict(e); recorded = c.pop("event_hash", None)
        if recorded != event_hash(c): errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events") or []), "tip_hash": prev, "errors": errors}


def target_domains() -> set[str]:
    x = load_json(UNIFIED_STATUS) if UNIFIED_STATUS.exists() else {}
    return set(str(v) for v in (x.get("target_domains") or []))


def fresh_market_candidates(now: datetime) -> tuple[dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]], dict[str, int]]:
    candidates: dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]] = {}
    stats = Counter(); domains = target_domains()
    for path in sorted(MARKETS.glob("*.json")) if MARKETS.exists() else []:
        stats["files_seen"] += 1
        try:
            raw = load_json(path)
            cid = str(raw.get("competition_id") or "")
            if domains and cid not in domains:
                stats["outside_target_domains"] += 1; continue
            observed = parse_dt(raw.get("source_observed_at_utc") or raw.get("freeze_utc"))
            kickoff = parse_dt(raw.get("kickoff_utc"))
            if observed is None or kickoff is None:
                stats["bad_time"] += 1; continue
            if observed > now or now - observed > MAX_SNAPSHOT_AGE:
                stats["stale_or_future_snapshot"] += 1; continue
            lead = kickoff - now
            if lead < MIN_LEAD or lead > MAX_LEAD:
                stats["outside_decision_lead_window"] += 1; continue
            if str(raw.get("settlement_scope") or "") not in {"90m_including_stoppage", "90_minutes_including_stoppage"}:
                stats["wrong_scope"] += 1; continue
            if not isinstance(raw.get("one_x_two"), dict):
                stats["missing_1x2"] += 1; continue
            key = identity(raw)
            old = candidates.get(key)
            if old is None or observed > old[0]:
                candidates[key] = (observed, path, raw)
        except Exception:
            stats["rejected"] += 1
    stats["eligible_fixture_count"] = len(candidates)
    return candidates, dict(sorted(stats.items()))


def reliability(q: dict[str, float], cid: str, freeze: dict[str, Any]) -> tuple[str, float, float | None, str, int, bool]:
    pick = max(D, key=lambda d: q[d]); pmax = float(q[pick])
    trained = set(freeze.get("trained_domains") or [])
    threshold = float(freeze.get("selector_threshold") or 0.55)
    if cid not in trained:
        return pick, pmax, None, "UNSEEN_DOMAIN_ABSTAIN", 0, False
    model_payload = freeze.get("model") or {}
    edges = [float(x) for x in (model_payload.get("pmax_edges") or [])]
    if len(edges) < 2:
        raise RuntimeError("missing selector pmax edges")
    b = len(edges) - 2
    for i in range(len(edges) - 1):
        if edges[i] <= pmax < edges[i+1]:
            b = i; break
    rel_model = model_payload.get("reliability_model") or {}
    comp = (rel_model.get("competition") or {}).get(f"{cid}|{pick}|{b}")
    min_n = int(model_payload.get("minimum_competition_cell_n") or 20)
    if isinstance(comp, dict) and int(comp.get("n") or 0) >= min_n:
        score = float(comp["posterior_mean"]); level = "COMPETITION_SHRUNK_CELL"; support = int(comp.get("n") or 0)
    else:
        glob = (rel_model.get("global") or {}).get(f"{pick}|{b}") or {"posterior_mean": 0.5, "n": 0}
        score = float(glob["posterior_mean"]); level = "GLOBAL_FALLBACK_CELL"; support = int(glob.get("n") or 0)
    return pick, pmax, score, level, support, bool(score >= threshold)


def prediction_events(ledger: dict[str, Any], typ: str) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")): e for e in ledger.get("events") or [] if e.get("event_type") == typ}


def settlement_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")): e for e in ledger.get("events") or [] if e.get("event_type") == "RESULT_SETTLED"}


def result_map() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not RESULTS.exists(): return {}
    x = load_json(RESULTS); out = {}
    for r in x.get("results") or []:
        if not isinstance(r, dict): continue
        key = (
            str(r.get("competition_id") or ""),
            str(r.get("kickoff_at") or ""),
            normalize_team_token(str(r.get("home_team") or "")),
            normalize_team_token(str(r.get("away_team") or "")),
        )
        if all(key): out[key] = r
    return out


def freeze_new(now: datetime, candidates: dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]], selector_ledger: dict[str, Any], matrix_ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter()
    selector_freeze = load_json(SELECTOR_SOURCE_FREEZE)
    sel_existing = prediction_events(selector_ledger, "SELECTOR_PREDICTION_FROZEN")
    mat_existing = prediction_events(matrix_ledger, "MATRIX_PREDICTION_FROZEN")
    all_rows, _ = matrix_forward.core.read_rows()
    matrix_model = matrix_forward.model_as_of(now, all_rows)

    for key, (observed, path, raw) in sorted(candidates.items(), key=lambda item: (item[1][0], item[0])):
        mid = match_id(key)
        q = devig(raw["one_x_two"])
        pick, pmax, score, level, support, selected = reliability(q, key[0], selector_freeze)
        fi = {
            "competition_id": key[0], "season": str(raw.get("season") or ""), "kickoff_at": key[1],
            "home_team": str(raw.get("home_team") or ""), "away_team": str(raw.get("away_team") or ""),
            "settlement_scope": str(raw.get("settlement_scope") or ""),
        }
        source = {
            "path": str(path.relative_to(ROOT)), "observed_at_utc": observed.isoformat(),
            "provider_name": raw.get("provider_name"), "provider_group": raw.get("provider_group"),
            "raw_snapshot_sha256": raw.get("raw_snapshot_sha256") or file_sha(path),
        }
        if mid not in sel_existing:
            append_event(selector_ledger, "SELECTOR_PREDICTION_FROZEN", mid, now.isoformat(), {
                "fixture_identity": fi, "decision_freeze_at_utc": now.isoformat(), "market_source": source,
                "prediction": {"probabilities": q, "pick": pick, "pmax": pmax},
                "selector": {"source_model_sha256": selector_freeze.get("model_sha256"), "threshold": selector_freeze.get("selector_threshold"), "reliability_score": score, "reliability_level": level, "reliability_support": support, "selected": selected},
                "governance": {"fixture_future_at_freeze": True, "market_no_later_than_freeze": observed <= now, "historical_result_backfill": False, "probability_mutation": False, "formal_weight": 0},
            })
            stats["new_selector_predictions"] += 1; stats["new_selector_selected" if selected else "new_selector_abstained"] += 1
        else:
            stats["selector_already_frozen"] += 1

        if mid not in mat_existing:
            row = {"competition_id": key[0], "home_team": fi["home_team"], "away_team": fi["away_team"]}
            cand = ablation.mixed_predict(matrix_model, row, "comp", "full")
            ref = ablation.mixed_predict(matrix_model, row, "comp", "comp")
            append_event(matrix_ledger, "MATRIX_PREDICTION_FROZEN", mid, now.isoformat(), {
                "fixture_identity": fi, "decision_freeze_at_utc": now.isoformat(), "market_source": source,
                "history_cutoff_utc": now.isoformat(),
                "candidate": matrix_forward.serialise_prediction(cand),
                "reference": matrix_forward.serialise_prediction(ref),
                "configuration": {"candidate": "total=comp|margin=full", "reference": "total=comp|margin=comp"},
                "governance": {"fixture_future_at_freeze": True, "historical_result_backfill": False, "formal_weight": 0},
            })
            stats["new_matrix_predictions"] += 1
        else:
            stats["matrix_already_frozen"] += 1
    return dict(sorted(stats.items()))


def settle(now: datetime, ledger: dict[str, Any], pred_type: str) -> dict[str, int]:
    stats = Counter(); preds = prediction_events(ledger, pred_type); settled = settlement_events(ledger); receipts = result_map()
    for mid, pe in sorted(preds.items()):
        if mid in settled: continue
        fi = pe.get("payload", {}).get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at"))
        if ko is None or now < ko + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1; continue
        key = (str(fi.get("competition_id") or ""), str(fi.get("kickoff_at") or ""), normalize_team_token(str(fi.get("home_team") or "")), normalize_team_token(str(fi.get("away_team") or "")))
        r = receipts.get(key)
        if not r:
            stats["official_receipt_missing"] += 1; continue
        append_event(ledger, "RESULT_SETTLED", mid, now.isoformat(), {
            "prediction_event_hash": pe.get("event_hash"),
            "result": {"home_goals_90": int(r["home_goals_90"]), "away_goals_90": int(r["away_goals_90"]), "actual_result": str(r["actual_result"])},
            "official_result_receipt_sha256": sha256_json(r), "official_result_source": r.get("source"),
        })
        stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def wilson(hits: int, n: int) -> tuple[float | None, float | None]:
    if n <= 0: return None, None
    p = hits/n; z2=Z90*Z90; den=1+z2/n; center=(p+z2/(2*n))/den
    rad=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n)/den
    return max(0.0, center-rad), min(1.0, center+rad)


def selector_metrics(ledger: dict[str, Any]) -> dict[str, Any]:
    preds = prediction_events(ledger, "SELECTOR_PREDICTION_FROZEN"); settled = settlement_events(ledger)
    rows=[]; selected=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if not pe or se.get("payload",{}).get("prediction_event_hash") != pe.get("event_hash"): continue
        p=pe["payload"]["prediction"]; fi=pe["payload"]["fixture_identity"]; actual=se["payload"]["result"]["actual_result"]
        row={"competition_id":fi["competition_id"],"pick":p["pick"],"p":{d:float(p["probabilities"][d]) for d in D},"actual":actual}
        rows.append(row)
        if pe["payload"]["selector"].get("selected") is True: selected.append(row)
    def calc(rr:list[dict[str,Any]])->dict[str,Any]:
        n=len(rr)
        if not n:return {"count":0}
        hits=sum(r["pick"]==r["actual"] for r in rr); lo,hi=wilson(hits,n); by=Counter(r["competition_id"] for r in rr)
        ll=br=rps=0.0
        for r in rr:
            y={d:1.0 if d==r["actual"] else 0.0 for d in D}; p=r["p"]
            ll-=math.log(max(1e-15,p[r["actual"]])); br+=sum((p[d]-y[d])**2 for d in D)
            rps+=((p["home"]-y["home"])**2+((p["home"]+p["draw"])-(y["home"]+y["draw"]))**2)/2
        return {"count":n,"hits":hits,"accuracy":hits/n,"wilson90":[lo,hi],"log_loss":ll/n,"brier":br/n,"rps":rps/n,"domain_count":len(by),"max_domain_share":max(by.values())/n,"by_competition_n":dict(sorted(by.items()))}
    allm=calc(rows); sm=calc(selected)
    gates={
        "selected_n":sm.get("count",0)>=100,
        "accuracy":sm.get("accuracy") is not None and sm.get("accuracy")>=0.60,
        "wilson90_lower":bool(sm.get("wilson90")) and sm["wilson90"][0] is not None and sm["wilson90"][0]>=0.55,
        "domain_count":sm.get("domain_count",0)>=5,
        "domain_concentration":sm.get("max_domain_share") is not None and sm["max_domain_share"]<=0.40,
    }
    return {"all_settled":allm,"selected_settled":sm,"forward_gate":{"results":gates,"all_pass":all(gates.values())},"decision":"FORWARD_GATE_PASSED_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_FORWARD_SAMPLE_OR_QUALITY"}


def matrix_metrics(ledger: dict[str, Any]) -> dict[str, Any]:
    preds=prediction_events(ledger,"MATRIX_PREDICTION_FROZEN"); settled=settlement_events(ledger); rows=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if not pe or se.get("payload",{}).get("prediction_event_hash") != pe.get("event_hash"): continue
        r=se["payload"]["result"]
        rows.append({"candidate":pe["payload"]["candidate"],"reference":pe["payload"]["reference"],"hg":int(r["home_goals_90"]),"ag":int(r["away_goals_90"])})
    cand=matrix_forward.arm_metric(rows,"candidate"); ref=matrix_forward.arm_metric(rows,"reference")
    gates={
        "minimum_settled":cand.get("count",0)>=100,
        "total_log_nonworse":cand.get("total_log_loss",math.inf)<=ref.get("total_log_loss",-math.inf)+1e-12 if cand.get("count",0) else False,
        "total_rps_nonworse":cand.get("total_rps",math.inf)<=ref.get("total_rps",-math.inf)+1e-12 if cand.get("count",0) else False,
        "score_log_nonworse":cand.get("exact_score_log_loss",math.inf)<=ref.get("exact_score_log_loss",-math.inf)+1e-12 if cand.get("count",0) else False,
        "score_top1_nonworse":cand.get("exact_score_top1_accuracy",-1)>=ref.get("exact_score_top1_accuracy",2)-1e-12 if cand.get("count",0) else False,
    }
    return {"candidate":cand,"reference":ref,"forward_gate":{"results":gates,"all_pass":all(gates.values())},"decision":"FORWARD_GATE_PASSED_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_FORWARD_SAMPLE_OR_QUALITY"}


def main() -> int:
    now=now_utc(); freeze=ensure_freeze(now)
    sl=load_ledger(SELECTOR_LEDGER,SCHEMA_SELECTOR_LEDGER); ml=load_ledger(MATRIX_LEDGER,SCHEMA_MATRIX_LEDGER)
    if audit(sl)["status"]!="PASS" or audit(ml)["status"]!="PASS": raise RuntimeError("pre-run ledger audit failed")
    candidates, market_scan=fresh_market_candidates(now)
    freeze_stats=freeze_new(now,candidates,sl,ml)
    selector_settle=settle(now,sl,"SELECTOR_PREDICTION_FROZEN"); matrix_settle=settle(now,ml,"MATRIX_PREDICTION_FROZEN")
    sa=audit(sl); ma=audit(ml)
    SELECTOR_LEDGER.parent.mkdir(parents=True,exist_ok=True)
    SELECTOR_LEDGER.write_text(json.dumps(sl,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    MATRIX_LEDGER.write_text(json.dumps(ml,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    status={
        "schema_version":"V6.49.2-fresh-market-forward-challengers-status-r1","generated_at_utc":now.isoformat(),"formal_current_version":"V5.0.1","status":"PASS" if sa["status"]=="PASS" and ma["status"]=="PASS" else "FAIL","freeze":freeze,
        "market_scan":market_scan,"freeze_stats":freeze_stats,"selector_ledger_audit":sa,"matrix_ledger_audit":ma,"selector_settlement_scan":selector_settle,"matrix_settlement_scan":matrix_settle,
        "selector_evaluation":selector_metrics(sl),"matrix_evaluation":matrix_metrics(ml),
        "governance":{"future_fixture_only":True,"maximum_snapshot_age_hours":2,"historical_result_backfill":False,"old_v6475_v6479_ledgers_unchanged":True,"probability_mutation":False,"formal_weight":0,"automatic_promotion":False,"current_rule_change":False}
    }
    STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":status["status"],"market_scan":market_scan,"freeze_stats":freeze_stats,"selector":status["selector_evaluation"],"matrix":status["matrix_evaluation"]},ensure_ascii=False,indent=2))
    return 0 if status["status"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
