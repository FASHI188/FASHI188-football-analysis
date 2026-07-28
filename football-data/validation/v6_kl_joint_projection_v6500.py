#!/usr/bin/env python3
"""V6.50.0 prospective KL-coherent joint-matrix challenger.

Research-only sidecar. The immutable V6.49.2 F06 candidate score matrix is the prior P.
For each still-future matched fixture, compute Q by iterative proportional fitting (IPF):

    minimize D_KL(Q || P)
    subject to:
      * Q total-goal marginal == frozen F06 direct-total P(T)
      * Q 90m result marginal == frozen F05 1X2 probabilities
      * Q has the same support as P and sums to 1

This creates a coherent research matrix without manually averaging probabilities. It never
changes V6.49.2, CURRENT, the F05 selector, or formal weights. New predictions require >=1h
lead at the actual V6.50.0 event timestamp; no post-kickoff/historical backfill is allowed.
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

from platform_core import sha256_json
import v6_total_margin_forward_v6479 as matrix_forward

SEL = ROOT / "forward" / "v6_fresh_selector_events_v6492.json"
MAT = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
FREEZE = ROOT / "manifests" / "v6_kl_joint_projection_v6500_freeze.json"
LEDGER = ROOT / "forward" / "v6_kl_joint_projection_events_v6500.json"
STATUS = ROOT / "manifests" / "v6_kl_joint_projection_v6500_status.json"

SCHEMA_FREEZE = "V6.50.0-kl-joint-projection-freeze-r1"
SCHEMA_LEDGER = "V6.50.0-kl-joint-projection-ledger-r1"
SCHEMA_EVENT = "V6.50.0-kl-joint-projection-event-r1"
D = ("home", "draw", "away")
TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")
MIN_LEAD = timedelta(hours=1)
MIN_RESULT_AGE = timedelta(hours=2)
IPF_TOL = 1e-12
IPF_MAX_ITER = 10000
AUDIT_TOL = 1e-9
EPS = 1e-15


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
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load(FREEZE)
        if x.get("schema_version") != SCHEMA_FREEZE or x.get("status") != "FROZEN":
            raise RuntimeError("invalid V6.50.0 freeze")
        return x
    x = {
        "schema_version": SCHEMA_FREEZE,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "classification": "PROSPECTIVE_KL_COHERENCE_CHALLENGE_FORMAL_WEIGHT_0",
        "prior": "V6.49.2 F06 candidate score matrix (total=comp|margin=full)",
        "constraints": {
            "total_marginal": "preserve frozen F06 candidate P(T=0..6,7+)",
            "result_marginal": "match frozen V6.49.2 F05 1X2 probabilities",
            "support": "no probability mass outside prior support",
            "probability_sum": 1.0,
        },
        "objective": "minimize D_KL(Q || P_prior)",
        "optimizer": {
            "algorithm": "iterative_proportional_fitting_alternating_total_and_result_marginals",
            "tolerance": IPF_TOL,
            "max_iterations": IPF_MAX_ITER,
            "termination": "max(total_marginal_residual,result_marginal_residual,probability_sum_residual)<=tolerance",
        },
        "prospective_contract": {
            "source_predictions_must_already_be_frozen": True,
            "fixture_must_be_future_at_projection_event": True,
            "minimum_lead_hours_at_projection_event": 1,
            "one_immutable_projection_per_fixture": True,
            "settlement_reuses_matching_V6.49.2_official_result_event": True,
            "historical_backfill": False,
        },
        "review_gate": {
            "minimum_settled": 100,
            "result_brier_nonworse_vs_F06_candidate": True,
            "score_log_nonworse_vs_F06_candidate": True,
            "score_top1_nonworse_vs_F06_candidate": True,
            "total_metrics_identical_to_F06_candidate": True,
            "all_projection_audits_converged": True,
        },
        "governance": {
            "research_only": True,
            "source_probability_mutation": False,
            "source_matrix_mutation": False,
            "selector_mutation": False,
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
        raise RuntimeError("invalid V6.50.0 ledger")
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


def audit_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    prev = "GENESIS"
    errors: list[str] = []
    for i, e in enumerate(ledger.get("events") or [], 1):
        if e.get("sequence") != i:
            errors.append(f"sequence:{i}")
        if e.get("previous_event_hash") != prev:
            errors.append(f"previous_hash:{i}")
        c = dict(e)
        recorded = c.pop("event_hash", None)
        if recorded != event_hash(c):
            errors.append(f"hash:{i}")
        prev = str(recorded or "")
    return {"status": "PASS" if not errors else "FAIL", "event_count": len(ledger.get("events") or []), "tip_hash": prev, "errors": errors}


def pred_events(ledger: dict[str, Any], typ: str) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")): e for e in (ledger.get("events") or []) if e.get("event_type") == typ}


def settled_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e.get("match_id")): e for e in (ledger.get("events") or []) if e.get("event_type") == "RESULT_SETTLED"}


def result_side(h: int, a: int) -> str:
    return "home" if h > a else "away" if a > h else "draw"


def total_bucket(h: int, a: int) -> str:
    t = h + a
    return str(t) if t <= 6 else "7+"


def build_prior_states(surface: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    matrix = surface.get("score_matrix") or {}
    result = {d: float((surface.get("result") or {})[d]) for d in D}
    tail = float(surface.get("tail15plus_probability") or 0.0)
    states: list[dict[str, Any]] = []
    visible_res = {d: 0.0 for d in D}
    for key, raw in matrix.items():
        hs, aas = str(key).split("-", 1)
        h, a = int(hs), int(aas)
        p = float(raw)
        side = result_side(h, a)
        visible_res[side] += p
        states.append({"key": str(key), "visible": True, "total": total_bucket(h, a), "result": side, "p": p})
    tail_by_result = {d: result[d] - visible_res[d] for d in D}
    for d in D:
        if tail_by_result[d] < -AUDIT_TOL:
            raise RuntimeError(f"negative derived tail result mass {d}={tail_by_result[d]}")
        if tail_by_result[d] < 0:
            tail_by_result[d] = 0.0
    derived_tail = sum(tail_by_result.values())
    if abs(derived_tail - tail) > AUDIT_TOL:
        raise RuntimeError(f"tail decomposition mismatch derived={derived_tail} stored={tail}")
    # Tiny floating difference is assigned proportionally to the already-derived tail support.
    if tail > 0 and derived_tail > 0 and abs(derived_tail - tail) > 0:
        scale = tail / derived_tail
        tail_by_result = {d: tail_by_result[d] * scale for d in D}
    for d in D:
        p = tail_by_result[d]
        if p > 0:
            states.append({"key": f"TAIL15+_{d}", "visible": False, "total": "7+", "result": d, "p": p})
    s = sum(float(x["p"]) for x in states)
    if abs(s - 1.0) > AUDIT_TOL:
        raise RuntimeError(f"prior state sum {s}")
    return states, tail_by_result


def marginals(states: list[dict[str, Any]], q: list[float]) -> tuple[dict[str, float], dict[str, float], float]:
    tm = {k: 0.0 for k in TOTAL_KEYS}
    rm = {d: 0.0 for d in D}
    for st, p in zip(states, q):
        tm[st["total"]] += p
        rm[st["result"]] += p
    return tm, rm, sum(q)


def project(surface: dict[str, Any], target_result: dict[str, float]) -> dict[str, Any]:
    states, prior_tail = build_prior_states(surface)
    prior = [float(x["p"]) for x in states]
    target_total = {k: float((surface.get("total") or {})[k]) for k in TOTAL_KEYS}
    target_result = {d: float(target_result[d]) for d in D}
    if abs(sum(target_total.values()) - 1.0) > AUDIT_TOL or abs(sum(target_result.values()) - 1.0) > AUDIT_TOL:
        raise RuntimeError("invalid target marginals")
    q = prior[:]
    converged = False
    residual = math.inf
    iterations = 0
    for iterations in range(1, IPF_MAX_ITER + 1):
        tm, _, _ = marginals(states, q)
        for k in TOTAL_KEYS:
            if target_total[k] > 0 and tm[k] <= 0:
                raise RuntimeError(f"infeasible total support {k}")
            factor = target_total[k] / tm[k] if tm[k] > 0 else 1.0
            for i, st in enumerate(states):
                if st["total"] == k:
                    q[i] *= factor
        _, rm, _ = marginals(states, q)
        for d in D:
            if target_result[d] > 0 and rm[d] <= 0:
                raise RuntimeError(f"infeasible result support {d}")
            factor = target_result[d] / rm[d] if rm[d] > 0 else 1.0
            for i, st in enumerate(states):
                if st["result"] == d:
                    q[i] *= factor
        tm, rm, ps = marginals(states, q)
        residual = max(
            max(abs(tm[k] - target_total[k]) for k in TOTAL_KEYS),
            max(abs(rm[d] - target_result[d]) for d in D),
            abs(ps - 1.0),
        )
        if residual <= IPF_TOL:
            converged = True
            break
    if not converged:
        raise RuntimeError(f"IPF did not converge residual={residual} iterations={iterations}")

    score_matrix: dict[str, float] = {}
    tail_by_result = {d: 0.0 for d in D}
    for st, v in zip(states, q):
        if st["visible"]:
            score_matrix[st["key"]] = v
        else:
            tail_by_result[st["result"]] += v
    top = sorted(score_matrix.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    kl = 0.0
    for qv, pv in zip(q, prior):
        if qv > 0:
            if pv <= 0:
                raise RuntimeError("projection left prior support")
            kl += qv * math.log(qv / pv)
    tm, rm, ps = marginals(states, q)
    return {
        "total": target_total,
        "result": target_result,
        "score_top10": [{"score": k, "probability": v} for k, v in top],
        "score_matrix": score_matrix,
        "tail15plus_probability": sum(tail_by_result.values()),
        "tail15plus_by_result": tail_by_result,
        "audit": {
            "prior_probability_sum": sum(prior),
            "projected_probability_sum": ps,
            "probability_sum_residual": abs(ps - 1.0),
            "max_total_constraint_residual": max(abs(tm[k] - target_total[k]) for k in TOTAL_KEYS),
            "max_result_constraint_residual": max(abs(rm[d] - target_result[d]) for d in D),
            "converged": converged,
            "iterations": iterations,
            "termination_residual": residual,
            "objective_kl_q_to_prior": kl,
            "prior_tail15plus_by_result": prior_tail,
            "support_state_count": len(states),
        },
    }


def freeze_new(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter()
    sel = load(SEL)
    mat = load(MAT)
    sp = pred_events(sel, "SELECTOR_PREDICTION_FROZEN")
    mp = pred_events(mat, "MATRIX_PREDICTION_FROZEN")
    existing = pred_events(ledger, "KL_MATRIX_PREDICTION_FROZEN")
    for mid in sorted(set(sp) & set(mp)):
        if mid in existing:
            stats["already_frozen"] += 1
            continue
        spe = sp[mid]
        mpe = mp[mid]
        s_payload = spe.get("payload") or {}
        m_payload = mpe.get("payload") or {}
        fi = m_payload.get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at"))
        if ko is None:
            stats["bad_kickoff"] += 1
            continue
        lead = ko - now
        if lead < MIN_LEAD:
            stats["not_future_with_minimum_lead"] += 1
            continue
        probs = (s_payload.get("prediction") or {}).get("probabilities") or {}
        if any(d not in probs for d in D):
            stats["missing_f05_probabilities"] += 1
            continue
        prior = m_payload.get("candidate") or {}
        try:
            projected = project(prior, {d: float(probs[d]) for d in D})
        except Exception:
            stats["projection_rejected"] += 1
            continue
        append_event(ledger, "KL_MATRIX_PREDICTION_FROZEN", mid, now.isoformat(), {
            "fixture_identity": fi,
            "projection_freeze_at_utc": now.isoformat(),
            "source_selector_prediction_event_hash": spe.get("event_hash"),
            "source_matrix_prediction_event_hash": mpe.get("event_hash"),
            "source_f05_1x2": {d: float(probs[d]) for d in D},
            "source_f06_candidate": prior,
            "projected": projected,
            "optimizer_record": {
                "prior": "source_f06_candidate",
                "constraints": ["preserve_source_f06_total", "match_source_f05_1x2_result"],
                "objective": "D_KL(Q||P_prior)",
                "algorithm": "IPF",
                "converged": projected["audit"]["converged"],
                "iterations": projected["audit"]["iterations"],
                "termination_residual": projected["audit"]["termination_residual"],
                "objective_value": projected["audit"]["objective_kl_q_to_prior"],
            },
            "governance": {
                "fixture_future_with_minimum_lead_at_projection": True,
                "historical_backfill": False,
                "source_probability_mutation": False,
                "source_matrix_mutation": False,
                "formal_weight": 0,
            },
        })
        stats["new_predictions"] += 1
    return dict(sorted(stats.items()))


def settle_from_source(now: datetime, ledger: dict[str, Any]) -> dict[str, int]:
    stats = Counter()
    mat = load(MAT)
    source_preds = pred_events(mat, "MATRIX_PREDICTION_FROZEN")
    source_settled = settled_events(mat)
    preds = pred_events(ledger, "KL_MATRIX_PREDICTION_FROZEN")
    settled = settled_events(ledger)
    for mid, pe in sorted(preds.items()):
        if mid in settled:
            continue
        fi = (pe.get("payload") or {}).get("fixture_identity") or {}
        ko = parse_dt(fi.get("kickoff_at"))
        if ko is None or now < ko + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1
            continue
        se = source_settled.get(mid)
        src_pe = source_preds.get(mid)
        if not se or not src_pe:
            stats["source_v6492_settlement_missing"] += 1
            continue
        if (se.get("payload") or {}).get("prediction_event_hash") != src_pe.get("event_hash"):
            stats["source_settlement_hash_mismatch"] += 1
            continue
        append_event(ledger, "RESULT_SETTLED", mid, now.isoformat(), {
            "prediction_event_hash": pe.get("event_hash"),
            "source_matrix_prediction_event_hash": src_pe.get("event_hash"),
            "source_matrix_settlement_event_hash": se.get("event_hash"),
            "result": (se.get("payload") or {}).get("result"),
            "official_result_receipt_sha256": (se.get("payload") or {}).get("official_result_receipt_sha256"),
            "official_result_source": (se.get("payload") or {}).get("official_result_source"),
        })
        stats["new_settlements"] += 1
    return dict(sorted(stats.items()))


def evaluate(ledger: dict[str, Any]) -> dict[str, Any]:
    preds = pred_events(ledger, "KL_MATRIX_PREDICTION_FROZEN")
    settled = settled_events(ledger)
    rows: list[dict[str, Any]] = []
    all_converged = True
    for mid, se in settled.items():
        pe = preds.get(mid)
        if not pe or (se.get("payload") or {}).get("prediction_event_hash") != pe.get("event_hash"):
            continue
        p = pe.get("payload") or {}
        r = (se.get("payload") or {}).get("result") or {}
        rows.append({
            "projected": p["projected"],
            "candidate": p["source_f06_candidate"],
            "hg": int(r["home_goals_90"]),
            "ag": int(r["away_goals_90"]),
        })
        all_converged = all_converged and bool((p.get("projected") or {}).get("audit", {}).get("converged"))
    proj = matrix_forward.arm_metric(rows, "projected")
    cand = matrix_forward.arm_metric(rows, "candidate")
    n = int(proj.get("count") or 0)
    total_identical = bool(n) and abs(float(proj.get("total_log_loss")) - float(cand.get("total_log_loss"))) <= 1e-12 and abs(float(proj.get("total_rps")) - float(cand.get("total_rps"))) <= 1e-12
    gates = {
        "minimum_settled": n >= 100,
        "result_brier_nonworse_vs_candidate": bool(n) and float(proj.get("result_brier")) <= float(cand.get("result_brier")) + 1e-12,
        "score_log_nonworse_vs_candidate": bool(n) and float(proj.get("exact_score_log_loss")) <= float(cand.get("exact_score_log_loss")) + 1e-12,
        "score_top1_nonworse_vs_candidate": bool(n) and float(proj.get("exact_score_top1_accuracy")) >= float(cand.get("exact_score_top1_accuracy")) - 1e-12,
        "total_metrics_identical_to_candidate": total_identical,
        "all_projection_audits_converged": all_converged,
    }
    return {
        "projected": proj,
        "source_candidate": cand,
        "forward_gate": {"results": gates, "all_pass": all(gates.values())},
        "decision": "KL_COHERENT_CHALLENGE_REVIEW_REQUIRED" if all(gates.values()) else "PENDING_KL_COHERENT_FORWARD_SAMPLE_OR_QUALITY",
    }


def projection_diagnostics(ledger: dict[str, Any]) -> dict[str, Any]:
    preds = list(pred_events(ledger, "KL_MATRIX_PREDICTION_FROZEN").values())
    if not preds:
        return {"count": 0}
    audits = [((e.get("payload") or {}).get("projected") or {}).get("audit") or {} for e in preds]
    top_changed = 0
    for e in preds:
        p = e.get("payload") or {}
        prior_top = ((p.get("source_f06_candidate") or {}).get("score_top10") or [{}])[0].get("score")
        proj_top = ((p.get("projected") or {}).get("score_top10") or [{}])[0].get("score")
        top_changed += int(prior_top != proj_top)
    return {
        "count": len(preds),
        "all_converged": all(bool(a.get("converged")) for a in audits),
        "max_iterations": max(int(a.get("iterations") or 0) for a in audits),
        "max_termination_residual": max(float(a.get("termination_residual") or 0.0) for a in audits),
        "max_total_constraint_residual": max(float(a.get("max_total_constraint_residual") or 0.0) for a in audits),
        "max_result_constraint_residual": max(float(a.get("max_result_constraint_residual") or 0.0) for a in audits),
        "max_probability_sum_residual": max(float(a.get("probability_sum_residual") or 0.0) for a in audits),
        "mean_kl_objective": sum(float(a.get("objective_kl_q_to_prior") or 0.0) for a in audits) / len(audits),
        "max_kl_objective": max(float(a.get("objective_kl_q_to_prior") or 0.0) for a in audits),
        "score_top1_changed_count": top_changed,
        "score_top1_changed_rate": top_changed / len(preds),
    }


def main() -> int:
    now = now_utc()
    freeze = ensure_freeze(now)
    ledger = load_ledger()
    before = audit_ledger(ledger)
    if before["status"] != "PASS":
        raise RuntimeError("pre-run KL ledger audit failed")
    scan = freeze_new(now, ledger)
    settlement = settle_from_source(now, ledger)
    after = audit_ledger(ledger)
    if after["status"] != "PASS":
        raise RuntimeError("post-run KL ledger audit failed")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnostics = projection_diagnostics(ledger)
    evaluation = evaluate(ledger)
    status = {
        "schema_version": "V6.50.0-kl-joint-projection-status-r1",
        "generated_at_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if after["status"] == "PASS" and diagnostics.get("all_converged", True) else "FAIL",
        "freeze": freeze,
        "prediction_scan": scan,
        "settlement_scan": settlement,
        "ledger_audit": after,
        "projection_diagnostics": diagnostics,
        "evaluation": evaluation,
        "governance": {
            "research_only": True,
            "source_probability_mutation": False,
            "source_matrix_mutation": False,
            "selector_mutation": False,
            "historical_backfill": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status["status"],
        "prediction_scan": scan,
        "settlement_scan": settlement,
        "ledger_audit": after,
        "projection_diagnostics": diagnostics,
        "evaluation": evaluation,
    }, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
