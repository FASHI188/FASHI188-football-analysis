#!/usr/bin/env python3
"""V6.49.5 context-resolution priority queue.

Prioritises scarce live/manual context verification by CURRENT forward decision value only:
P0: V6.49.2 fresh 1X2 selector selected=True and clean context decision missing;
P1: selected=True with context present but manager/material-delta axes missing;
P2: V6.49.2 fresh matrix prediction exists and clean context decision missing;
P3: matrix/context present but manager/material-delta axes missing;
P4: selector abstained / lower-value future fixture / context complete.

Hard scope rule
---------------
V6.47.5/V6.47.9 are historical evidence with execution authority 0. This queue MUST NOT
fall back to them. If either current V6.49.2 ledger is absent or invalid, fail closed.

This script does not change probabilities or selection. It only orders work.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FRESH_SELECTOR = ROOT / "forward" / "v6_fresh_selector_events_v6492.json"
FRESH_MATRIX = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
CONTEXT_LEDGER = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
AXES_LEDGER = ROOT / "forward" / "v6_match_context_axes_events_v6490.json"
OUT = ROOT / "manifests" / "v6_context_priority_queue_v6493_status.json"
EXPECTED_SELECTOR_SCHEMA = "V6.49.2-fresh-selector-ledger-r1"
EXPECTED_MATRIX_SCHEMA = "V6.49.2-fresh-matrix-ledger-r1"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        x=json.loads(path.read_text(encoding="utf-8")); return x if isinstance(x,dict) else {}
    except Exception:
        return {}


def require_current_ledger(path: Path, expected_schema: str, label: str) -> dict[str, Any]:
    x=load(path)
    if not x:
        raise RuntimeError(f"current {label} ledger missing: {path}")
    if x.get("schema_version") != expected_schema or not isinstance(x.get("events"),list):
        raise RuntimeError(f"current {label} ledger invalid or wrong schema: {path}")
    return x


def dt(value: object):
    try:
        x=datetime.fromisoformat(str(value or "").replace("Z","+00:00"))
        if x.tzinfo is None:return None
        return x.astimezone(timezone.utc)
    except Exception:return None


def norm(value: object)->str:
    return " ".join(str(value or "").strip().casefold().split())


def fkey(fi: dict[str,Any])->str:
    return "|".join([str(fi.get("competition_id") or ""),str(fi.get("kickoff_at") or ""),norm(fi.get("home_team")),norm(fi.get("away_team"))])


def pred_map(ledger: dict[str,Any], event_type: str)->dict[str,dict[str,Any]]:
    out={}
    for e in ledger.get("events") or []:
        if isinstance(e,dict) and e.get("event_type")==event_type:
            fi=(e.get("payload") or {}).get("fixture_identity") or {}
            out[fkey(fi)]=e
    return out


def context_keys()->set[str]:
    x=load(CONTEXT_LEDGER); out=set()
    for e in x.get("events") or []:
        if isinstance(e,dict) and e.get("event_type")=="CONTEXT_DECISION_FROZEN":
            fi=(e.get("payload") or {}).get("fixture_identity") or {}
            out.add(fkey(fi))
    return out


def axes_keys()->set[str]:
    x=load(AXES_LEDGER); out=set()
    for e in x.get("events") or []:
        if isinstance(e,dict) and e.get("event_type")=="MATCH_CONTEXT_AXES_FROZEN":
            fi=(e.get("payload") or {}).get("fixture_identity") or {}
            out.add(fkey(fi))
    return out


def main()->int:
    now=datetime.now(timezone.utc).replace(microsecond=0)
    selector_ledger=require_current_ledger(FRESH_SELECTOR,EXPECTED_SELECTOR_SCHEMA,"V6.49.2 selector")
    matrix_ledger=require_current_ledger(FRESH_MATRIX,EXPECTED_MATRIX_SCHEMA,"V6.49.2 matrix")
    selectors=pred_map(selector_ledger,"SELECTOR_PREDICTION_FROZEN")
    matrices=pred_map(matrix_ledger,"MATRIX_PREDICTION_FROZEN")
    contexts=context_keys(); axes=axes_keys()
    keys=set(selectors)|set(matrices); rows=[]
    for key in sorted(keys):
        se=selectors.get(key); me=matrices.get(key); pe=se or me
        p=(pe or {}).get("payload") or {}; fi=p.get("fixture_identity") or {}; ko=dt(fi.get("kickoff_at"))
        if ko is None or ko<=now:continue
        selected=bool(se and ((se.get("payload") or {}).get("selector") or {}).get("selected") is True)
        has_context=key in contexts; has_axes=key in axes; has_matrix=me is not None
        if selected and not has_context:priority="P0_SELECTED_CONTEXT_MISSING"
        elif selected and has_context and not has_axes:priority="P1_SELECTED_AXES_MISSING"
        elif has_matrix and not has_context:priority="P2_MATRIX_CONTEXT_MISSING"
        elif has_matrix and has_context and not has_axes:priority="P3_MATRIX_AXES_MISSING"
        else:priority="P4_ABSTAIN_OR_CONTEXT_COMPLETE"
        rows.append({
            "priority":priority,"competition_id":fi.get("competition_id"),"kickoff_at":fi.get("kickoff_at"),"home_team":fi.get("home_team"),"away_team":fi.get("away_team"),
            "lead_hours":round((ko-now).total_seconds()/3600,3),"selector_selected":selected,"matrix_prediction":has_matrix,"context_decision_frozen":has_context,"manager_delta_axes_frozen":has_axes,
            "selector_pick":((se.get("payload") or {}).get("prediction") or {}).get("pick") if se else None,
            "selector_pmax":((se.get("payload") or {}).get("prediction") or {}).get("pmax") if se else None,
            "selector_reliability":((se.get("payload") or {}).get("selector") or {}).get("reliability_score") if se else None,
        })
    order={"P0_SELECTED_CONTEXT_MISSING":0,"P1_SELECTED_AXES_MISSING":1,"P2_MATRIX_CONTEXT_MISSING":2,"P3_MATRIX_AXES_MISSING":3,"P4_ABSTAIN_OR_CONTEXT_COMPLETE":4}
    rows.sort(key=lambda r:(order[r["priority"]],r["lead_hours"],str(r["competition_id"]),str(r["home_team"])))
    counts={k:sum(r["priority"]==k for r in rows) for k in order}
    out={
        "schema_version":"V6.49.5-context-priority-queue-r2","generated_at_utc":now.isoformat(),"formal_current_version":"V5.0.1","status":"PASS",
        "selector_source":str(FRESH_SELECTOR.relative_to(ROOT)),"matrix_source":str(FRESH_MATRIX.relative_to(ROOT)),"future_fixture_count":len(rows),"priority_counts":counts,"queue":rows,
        "governance":{
            "queue_only":True,"probability_mutation":False,"selection_mutation":False,"formal_weight":0,"current_rule_change":False,
            "legacy_v6475_v6479_fallback":False,"fail_closed_if_current_v6492_ledgers_missing":True
        }
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"future_fixture_count":len(rows),"priority_counts":counts,"top":rows[:10]},ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
