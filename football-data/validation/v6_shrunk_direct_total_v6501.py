#!/usr/bin/env python3
"""V6.50.1 time-ordered shrunk direct-total challenger.

Goal: break the competition-only P(T) collapse without accepting the historical proper-score
regression of the unshrunk strength track.

Development calibration (historical, time-ordered):
- for each formal domain, only matches strictly before that domain's recent-two-season fixed1000
  window are eligible to choose the global shrink weight;
- same-day rows are all predicted before updates;
- grid is frozen a priori;
- choose weight minimizing development total-goal log loss, then RPS, then lower weight.

Candidate P(T) = (1-w)*P_comp(T) + w*P_strength(T), on the internal 0..14,15+ states.
The fixed1000 is reported only as a retrospective diagnostic and cannot promote the model.
A new prospective ledger freezes candidate totals only for still-future V6.49.2 fixtures with
>=1h lead, using historical results strictly before the original source matrix freeze.
Formal weight remains zero; no source prediction is changed.
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

import evaluate_direct_total_margin_matrix_v6477 as core
import v6_total_margin_forward_v6479 as matrix_forward
from platform_core import sha256_json

BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
SOURCE_MATRIX = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
FREEZE = ROOT / "manifests" / "v6_shrunk_direct_total_v6501_freeze.json"
LEDGER = ROOT / "forward" / "v6_shrunk_direct_total_events_v6501.json"
STATUS = ROOT / "manifests" / "v6_shrunk_direct_total_v6501_status.json"

WEIGHT_GRID = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0)
MIN_DEV_N = 3000
MIN_LEAD = timedelta(hours=1)
MIN_RESULT_AGE = timedelta(hours=2)
MIN_SETTLED = 100
EPS = 1e-15
SCHEMA_FREEZE = "V6.50.1-shrunk-direct-total-freeze-r1"
SCHEMA_LEDGER = "V6.50.1-shrunk-direct-total-ledger-r1"
SCHEMA_EVENT = "V6.50.1-shrunk-direct-total-event-r1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(v: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(v or "").replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def load(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def mix_internal(comp: dict[Any, float], strength: dict[Any, float], w: float) -> dict[Any, float]:
    out = {s: (1.0-w)*float(comp[s]) + w*float(strength[s]) for s in core.TOTAL_STATES}
    z = sum(out.values())
    if not math.isfinite(z) or z <= 0:
        raise RuntimeError("invalid total mixture")
    return {s: out[s]/z for s in core.TOTAL_STATES}


def actual_state(r: dict[str, Any]) -> Any:
    return core.total_state(int(r["hg"]), int(r["ag"]))


def metric_rows(rows: list[tuple[dict[Any, float], Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    ll = rps = 0.0
    top1 = top2 = 0
    mode_counts: Counter[str] = Counter()
    for p, actual in rows:
        report = core.report_total_probs(p)
        ac = str(actual) if actual != core.TOTAL_TAIL and int(actual) <= 6 else "7+"
        ll -= math.log(max(EPS, float(report[ac])))
        rps += core.rps_total(report, ac)
        tk = core.topk(report, 2)
        top1 += int(tk[0] == ac)
        top2 += int(ac in tk)
        mode_counts[str(tk[0])] += 1
    return {
        "count": n,
        "total_log_loss": ll/n,
        "total_rps": rps/n,
        "total_top1_accuracy": top1/n,
        "total_top2_accuracy": top2/n,
        "mode_counts": dict(sorted(mode_counts.items())),
        "unique_mode_count": len(mode_counts),
    }


def calibration() -> dict[str, Any]:
    benchmark = load(BENCHMARK)
    rows, meta = core.read_rows()
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_comp[str(r["competition_id"])].append(r)
    selected_seasons = benchmark.get("source_meta", {}).get("selected_seasons", {})
    benchmark_keys = {core.identity_key(r) for r in benchmark.get("rows", [])}

    dev_by_w: dict[float, list[tuple[dict[Any, float], Any]]] = {w: [] for w in WEIGHT_GRID}
    test_by_w: dict[float, list[tuple[dict[Any, float], Any]]] = {w: [] for w in WEIGHT_GRID}
    cutoffs: dict[str, str] = {}
    model = core.OnlineModel()

    for cid in core.base.TARGET_COMPETITIONS:
        cr = sorted(by_comp.get(cid, []), key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        seasons = set(str(x) for x in selected_seasons.get(cid, []))
        dates = [str(r["date"]) for r in cr if str(r["season"]) in seasons]
        cutoff = min(dates) if dates else None
        if cutoff:
            cutoffs[cid] = cutoff
        days: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in cr:
            days[str(r["date"])[:10]].append(r)
        for day in sorted(days):
            frozen = [(r, model.features(r)) for r in days[day]]
            for r, feat in frozen:
                comp = model.total_probs(cid, feat, "comp")
                strength = model.total_probs(cid, feat, "strength")
                actual = actual_state(r)
                is_dev = bool(cutoff and str(r["date"]) < cutoff)
                is_test = core.identity_key(r) in benchmark_keys
                for w in WEIGHT_GRID:
                    p = mix_internal(comp, strength, w)
                    if is_dev:
                        dev_by_w[w].append((p, actual))
                    if is_test:
                        test_by_w[w].append((p, actual))
            model.update_batch(frozen)

    dev_curve = {str(w): metric_rows(dev_by_w[w]) for w in WEIGHT_GRID}
    eligible = [(w, m) for w, m in ((w, dev_curve[str(w)]) for w in WEIGHT_GRID) if int(m.get("count") or 0) >= MIN_DEV_N]
    if not eligible:
        raise RuntimeError("no shrink weight has enough development rows")
    chosen_w, chosen_dev = min(eligible, key=lambda wm: (float(wm[1]["total_log_loss"]), float(wm[1]["total_rps"]), wm[0]))
    fixed_metrics = metric_rows(test_by_w[chosen_w])
    comp_fixed = metric_rows(test_by_w[0.0])
    return {
        "development_design": {
            "strictly_before_recent_two_season_fixed1000_window_by_domain": True,
            "same_day_predict_before_update": True,
            "weight_grid_predeclared": list(WEIGHT_GRID),
            "minimum_development_n": MIN_DEV_N,
            "competition_cutoffs": cutoffs,
            "source_meta": meta,
        },
        "development_curve": dev_curve,
        "chosen_weight": chosen_w,
        "chosen_development_metrics": chosen_dev,
        "selection_rule": "minimum development total log loss, then RPS, then lower weight",
        "fixed1000_diagnostic": {
            "classification": "RETROSPECTIVE_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE",
            "chosen_candidate": fixed_metrics,
            "comp_reference": comp_fixed,
            "candidate_minus_reference": {
                "total_log_loss": float(fixed_metrics["total_log_loss"]) - float(comp_fixed["total_log_loss"]),
                "total_rps": float(fixed_metrics["total_rps"]) - float(comp_fixed["total_rps"]),
                "total_top1_accuracy": float(fixed_metrics["total_top1_accuracy"]) - float(comp_fixed["total_top1_accuracy"]),
                "total_top2_accuracy": float(fixed_metrics["total_top2_accuracy"]) - float(comp_fixed["total_top2_accuracy"]),
            },
        },
    }


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load(FREEZE)
        if x.get("schema_version") != SCHEMA_FREEZE or x.get("status") != "FROZEN":
            raise RuntimeError("invalid V6.50.1 freeze")
        return x
    cal = calibration()
    x = {
        "schema_version": SCHEMA_FREEZE,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "classification": "TIME_ORDERED_SHRUNK_DIRECT_TOTAL_CHALLENGE_FORMAL_WEIGHT_0",
        "formula": "P_candidate(T)=(1-w)*P_comp(T)+w*P_strength(T)",
        "calibration": cal,
        "prospective_contract": {
            "source_fixture": "V6.49.2 immutable matrix prediction",
            "history_cutoff": "reuse each source matrix history_cutoff_utc; never use later results",
            "fixture_future_at_candidate_freeze": True,
            "minimum_lead_hours": 1,
            "one_immutable_prediction_per_fixture": True,
            "historical_backfill": False,
            "settlement_reuses_matching_V6.49.2 official 90m settlement": True,
        },
        "forward_gate": {
            "minimum_settled": MIN_SETTLED,
            "candidate_total_log_nonworse": True,
            "candidate_total_rps_nonworse": True,
            "candidate_total_top1_nonworse": True,
            "candidate_total_top2_nonworse": True,
        },
        "governance": {
            "research_only": True,
            "source_probability_mutation": False,
            "source_matrix_mutation": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    FREEZE.parent.mkdir(parents=True, exist_ok=True)
    FREEZE.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return x


def load_ledger() -> dict[str, Any]:
    if not LEDGER.exists():
        return {"schema_version": SCHEMA_LEDGER, "events": []}
    x = load(LEDGER)
    if x.get("schema_version") != SCHEMA_LEDGER or not isinstance(x.get("events"), list):
        raise RuntimeError("invalid V6.50.1 ledger")
    return x


def event_hash(x: dict[str, Any]) -> str:
    return sha256_json(x)


def append(ledger: dict[str, Any], typ: str, mid: str, ts: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = ledger["events"]
    e = {"schema_version": SCHEMA_EVENT, "sequence": len(events)+1, "event_type": typ, "event_timestamp_utc": ts, "match_id": mid, "previous_event_hash": events[-1]["event_hash"] if events else "GENESIS", "payload": payload}
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
    return {"status":"PASS" if not errors else "FAIL","event_count":len(ledger.get("events") or []),"tip_hash":prev,"errors":errors}


def pred_events(ledger: dict[str, Any], typ: str) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")):e for e in (ledger.get("events") or []) if e.get("event_type")==typ}


def settled_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")):e for e in (ledger.get("events") or []) if e.get("event_type")=="RESULT_SETTLED"}


def freeze_new(now: datetime, freeze: dict[str, Any], ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter()
    src = load(SOURCE_MATRIX)
    src_preds = pred_events(src, "MATRIX_PREDICTION_FROZEN")
    existing = pred_events(ledger, "TOTAL_PREDICTION_FROZEN")
    all_rows, _ = core.read_rows()
    model_cache: dict[str, core.OnlineModel] = {}
    w = float((freeze.get("calibration") or {}).get("chosen_weight"))
    for mid, pe in sorted(src_preds.items()):
        if mid in existing:
            stats["already_frozen"] += 1
            continue
        pp = pe.get("payload") or {}; fi = pp.get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at")); cutoff = parse_dt(pp.get("history_cutoff_utc"))
        if ko is None or cutoff is None:
            stats["bad_time"] += 1; continue
        if ko - now < MIN_LEAD:
            stats["not_future_with_minimum_lead"] += 1; continue
        ck = cutoff.date().isoformat()
        if ck not in model_cache:
            model_cache[ck] = matrix_forward.model_as_of(cutoff, all_rows)
        model = model_cache[ck]
        row = {"competition_id":str(fi.get("competition_id") or ""),"home_team":str(fi.get("home_team") or ""),"away_team":str(fi.get("away_team") or "")}
        feat = model.features(row)
        comp = model.total_probs(str(row["competition_id"]), feat, "comp")
        strength = model.total_probs(str(row["competition_id"]), feat, "strength")
        cand = mix_internal(comp, strength, w)
        report = core.report_total_probs(cand)
        source_total = {k:float(((pp.get("candidate") or {}).get("total") or {})[k]) for k in core.REPORT_TOTAL_STATES}
        append(ledger, "TOTAL_PREDICTION_FROZEN", mid, now.isoformat(), {
            "fixture_identity": fi,
            "projection_freeze_at_utc": now.isoformat(),
            "source_matrix_prediction_event_hash": pe.get("event_hash"),
            "source_history_cutoff_utc": cutoff.isoformat(),
            "features": {"strength_bin":feat[0],"home_recent_total_bin":feat[1],"away_recent_total_bin":feat[2]},
            "chosen_weight": w,
            "candidate_internal_total": {str(k):float(v) for k,v in cand.items()},
            "candidate_total": report,
            "source_comp_total": source_total,
            "candidate_mode": core.topk(report,1)[0],
            "source_mode": core.topk(source_total,1)[0],
            "governance": {"historical_backfill":False,"source_probability_mutation":False,"formal_weight":0},
        })
        stats["new_predictions"] += 1
    return dict(sorted(stats.items()))


def settle_from_source(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter(); src = load(SOURCE_MATRIX)
    src_preds = pred_events(src,"MATRIX_PREDICTION_FROZEN"); src_set = settled_events(src)
    preds=pred_events(ledger,"TOTAL_PREDICTION_FROZEN"); settled=settled_events(ledger)
    for mid, pe in sorted(preds.items()):
        if mid in settled: continue
        fi=(pe.get("payload") or {}).get("fixture_identity") or {}; ko=parse_dt(fi.get("kickoff_at"))
        if ko is None or now < ko + MIN_RESULT_AGE: stats["not_old_enough"] += 1; continue
        se=src_set.get(mid); src_pe=src_preds.get(mid)
        if not se or not src_pe: stats["source_settlement_missing"] += 1; continue
        if (se.get("payload") or {}).get("prediction_event_hash") != src_pe.get("event_hash"): stats["source_hash_mismatch"] += 1; continue
        append(ledger,"RESULT_SETTLED",mid,now.isoformat(),{
            "prediction_event_hash":pe.get("event_hash"),
            "source_matrix_prediction_event_hash":src_pe.get("event_hash"),
            "source_matrix_settlement_event_hash":se.get("event_hash"),
            "result":(se.get("payload") or {}).get("result"),
            "official_result_receipt_sha256":(se.get("payload") or {}).get("official_result_receipt_sha256"),
            "official_result_source":(se.get("payload") or {}).get("official_result_source"),
        }); stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def evaluate(ledger: dict[str, Any]) -> dict[str, Any]:
    preds=pred_events(ledger,"TOTAL_PREDICTION_FROZEN"); settled=settled_events(ledger)
    candidate_rows=[]; ref_rows=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if not pe or (se.get("payload") or {}).get("prediction_event_hash") != pe.get("event_hash"): continue
        pp=pe.get("payload") or {}; r=(se.get("payload") or {}).get("result") or {}
        hg,ag=int(r["home_goals_90"]),int(r["away_goals_90"]); ac=core.total_cat(hg,ag)
        cp={k:float((pp.get("candidate_total") or {})[k]) for k in core.REPORT_TOTAL_STATES}
        rp={k:float((pp.get("source_comp_total") or {})[k]) for k in core.REPORT_TOTAL_STATES}
        candidate_rows.append((cp,ac)); ref_rows.append((rp,ac))
    def calc(rows:list[tuple[dict[str,float],str]])->dict[str,Any]:
        n=len(rows)
        if not n:return {"count":0}
        ll=rps=0.0; top1=top2=0
        for p,ac in rows:
            ll-=math.log(max(EPS,p[ac])); rps+=core.rps_total(p,ac); tk=core.topk(p,2); top1+=int(tk[0]==ac); top2+=int(ac in tk)
        return {"count":n,"total_log_loss":ll/n,"total_rps":rps/n,"total_top1_accuracy":top1/n,"total_top2_accuracy":top2/n}
    c=calc(candidate_rows); r=calc(ref_rows); n=int(c.get("count") or 0)
    gates={
        "minimum_settled":n>=MIN_SETTLED,
        "total_log_nonworse":bool(n) and c["total_log_loss"]<=r["total_log_loss"]+1e-12,
        "total_rps_nonworse":bool(n) and c["total_rps"]<=r["total_rps"]+1e-12,
        "total_top1_nonworse":bool(n) and c["total_top1_accuracy"]>=r["total_top1_accuracy"]-1e-12,
        "total_top2_nonworse":bool(n) and c["total_top2_accuracy"]>=r["total_top2_accuracy"]-1e-12,
    }
    return {"candidate":c,"reference":r,"forward_gate":{"results":gates,"all_pass":all(gates.values())},"decision":"SHRUNK_TOTAL_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_SHRUNK_TOTAL_FORWARD_SAMPLE_OR_QUALITY"}


def diagnostics(ledger: dict[str, Any]) -> dict[str, Any]:
    preds=list(pred_events(ledger,"TOTAL_PREDICTION_FROZEN").values())
    if not preds:return {"count":0}
    cand_modes=Counter(str((e.get("payload") or {}).get("candidate_mode")) for e in preds)
    src_modes=Counter(str((e.get("payload") or {}).get("source_mode")) for e in preds)
    changed=sum(int((e.get("payload") or {}).get("candidate_mode") != (e.get("payload") or {}).get("source_mode")) for e in preds)
    l1=[]
    for e in preds:
        p=e.get("payload") or {}; c=p.get("candidate_total") or {}; r=p.get("source_comp_total") or {}
        l1.append(sum(abs(float(c[k])-float(r[k])) for k in core.REPORT_TOTAL_STATES))
    return {"count":len(preds),"candidate_mode_counts":dict(sorted(cand_modes.items())),"source_mode_counts":dict(sorted(src_modes.items())),"mode_changed_count":changed,"mode_changed_rate":changed/len(preds),"mean_l1_from_comp":sum(l1)/len(l1),"max_l1_from_comp":max(l1)}


def main()->int:
    now=now_utc(); freeze=ensure_freeze(now); ledger=load_ledger(); before=audit(ledger)
    if before["status"]!="PASS": raise RuntimeError("pre-run ledger audit failed")
    scan=freeze_new(now,freeze,ledger); settle=settle_from_source(now,ledger); after=audit(ledger)
    if after["status"]!="PASS": raise RuntimeError("post-run ledger audit failed")
    LEDGER.parent.mkdir(parents=True,exist_ok=True); LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    diag=diagnostics(ledger); ev=evaluate(ledger)
    status={
        "schema_version":"V6.50.1-shrunk-direct-total-status-r1","generated_at_utc":now.isoformat(),"formal_current_version":"V5.0.1","status":"PASS",
        "freeze":freeze,"prediction_scan":scan,"settlement_scan":settle,"ledger_audit":after,"prospective_diagnostics":diag,"evaluation":ev,
        "governance":{"research_only":True,"source_probability_mutation":False,"source_matrix_mutation":False,"historical_backfill":False,"formal_weight":0,"automatic_promotion":False,"current_rule_change":False}
    }
    STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":status["status"],"chosen_weight":(freeze.get("calibration") or {}).get("chosen_weight"),"development":(freeze.get("calibration") or {}).get("chosen_development_metrics"),"fixed1000":(freeze.get("calibration") or {}).get("fixed1000_diagnostic"),"prediction_scan":scan,"prospective_diagnostics":diag,"evaluation":ev},ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
