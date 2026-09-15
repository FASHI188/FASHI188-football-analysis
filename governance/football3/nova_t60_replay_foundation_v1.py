#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

class FoundationError(RuntimeError): pass

def require(ok: bool, msg: str) -> None:
    if not ok: raise FoundationError(msg)

def canon(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def sha(v: Any) -> str: return hashlib.sha256(canon(v)).hexdigest()

def dt(v: str) -> datetime:
    t=str(v).strip().replace("Z", "+00:00")
    x=datetime.fromisoformat(t)
    if x.tzinfo is None: x=x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)

def iso(x: datetime) -> str: return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def load_config(path: Path) -> dict[str, Any]:
    c=json.loads(path.read_text(encoding="utf-8"))
    require(c["status"]=="DESIGN_LOCKED_FOUNDATION_NOT_ENABLED", "CONFIG_STATUS")
    require(c["activation"]["enabled"] is False and c["activation"]["replay_coverage_start_at"] is None, "FOUNDATION_ENABLEMENT_FORBIDDEN")
    require(c["formal_boundaries"]=={"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0}, "FORMAL_BOUNDARY")
    return c

def register_fixture(c, reg, *, fixture_id, competition, season, kickoff, observed_at):
    require(competition in c["coverage"]["initial_competitions"], f"UNSUPPORTED_COMPETITION:{competition}")
    require(fixture_id not in reg, f"FIXTURE_ALREADY_REGISTERED:{fixture_id}")
    k,o=dt(kickoff),dt(observed_at)
    reg[fixture_id]={"fixture_id":fixture_id,"competition":competition,"season":season,"registered_at":iso(o),"kickoff_revisions":[{"revision_no":1,"kickoff":iso(k),"observed_at":iso(o),"late_revision_risk":o>k-timedelta(minutes=c["timing"]["seal_offset_minutes"])}]}
    return reg[fixture_id]

def revise_kickoff(c, reg, *, fixture_id, kickoff, observed_at):
    require(fixture_id in reg, f"UNKNOWN_FIXTURE:{fixture_id}")
    rs=reg[fixture_id]["kickoff_revisions"]; o,k=dt(observed_at),dt(kickoff)
    require(o>=dt(rs[-1]["observed_at"]), "REVISION_TIME_NOT_MONOTONIC")
    r={"revision_no":len(rs)+1,"kickoff":iso(k),"observed_at":iso(o),"late_revision_risk":o>k-timedelta(minutes=c["timing"]["seal_offset_minutes"])}
    rs.append(r); return r

def latest_fixture(c, reg, fixture_id):
    require(fixture_id in reg, f"UNKNOWN_FIXTURE:{fixture_id}")
    x=reg[fixture_id]; r=x["kickoff_revisions"][-1]; k=dt(r["kickoff"])
    return {"fixture_id":fixture_id,"competition":x["competition"],"season":x["season"],"kickoff":iso(k),"cutoff":iso(k-timedelta(minutes=c["timing"]["seal_offset_minutes"])),"kickoff_revision_no":r["revision_no"],"late_revision_risk":bool(r["late_revision_risk"])}

def build_state_or_gap(c, fixture, observations, *, required_fields, model_head, current_sha, capture_observed_at):
    cutoff=dt(fixture["cutoff"]); eligible={}; forbidden={x.lower() for x in c["state_contract"]["forbidden_payload_keys"]}
    for raw in observations:
        field=str(raw["field"]); require(field.lower() not in forbidden, f"RESULT_FIELD_FORBIDDEN:{field}")
        a,o=dt(raw["available_at"]),dt(raw["observed_at"]); require(a>=o, f"AVAILABLE_BEFORE_OBSERVED:{field}")
        if a>cutoff: continue
        row={"field":field,"value":raw["value"],"source":str(raw["source"]),"observed_at":iso(o),"available_at":iso(a)}
        prev=eligible.get(field)
        if prev is None or (row["available_at"],row["observed_at"],row["source"])>(prev["available_at"],prev["observed_at"],prev["source"]): eligible[field]=row
    missing=[f for f in required_fields if f not in eligible]
    if missing or fixture["late_revision_risk"]:
        g={"schema_version":"football3-nova-t60-gap-receipt-v1","status":"GAP_RECEIPT","fixture_id":fixture["fixture_id"],"competition":fixture["competition"],"season":fixture["season"],"kickoff":fixture["kickoff"],"cutoff":fixture["cutoff"],"capture_observed_at":iso(dt(capture_observed_at)),"missing_required_fields":missing,"late_kickoff_revision_risk":fixture["late_revision_risk"],"retroactive_fabrication":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
        g["gap_receipt_id"]="gap:"+sha(g)[:24]; return "gap",g
    selected=[eligible[k] for k in sorted(required_fields)]; input_sha=sha(selected)
    core={"schema_version":"football3-nova-t60-sealed-state-v1","fixture_id":fixture["fixture_id"],"competition":fixture["competition"],"season":fixture["season"],"kickoff":fixture["kickoff"],"cutoff":fixture["cutoff"],"observed_at":iso(dt(capture_observed_at)),"source":sorted({r["source"] for r in selected}),"input_sha256":input_sha,"model_head":model_head,"current_sha":current_sha,"kickoff_revision_no":fixture["kickoff_revision_no"],"payload":{r["field"]:r["value"] for r in selected},"payload_available_at":{r["field"]:r["available_at"] for r in selected},"result_fields_read":0,"retroactive_fabrication":False}
    state=dict(core); state["state_sha256"]=sha(core)
    receipt={"schema_version":"football3-nova-t60-capture-receipt-v1","status":"SEALED_STATE_CAPTURED","fixture_id":state["fixture_id"],"competition":state["competition"],"season":state["season"],"kickoff":state["kickoff"],"cutoff":state["cutoff"],"observed_at":state["observed_at"],"source":state["source"],"input_sha256":input_sha,"state_sha256":state["state_sha256"],"model_head":model_head,"current_sha":current_sha,"result_fields_read":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False}
    receipt["capture_receipt_id"]="capture:"+sha(receipt)[:24]; state["capture_receipt_id"]=receipt["capture_receipt_id"]
    return "state",{"state":state,"receipt":receipt}

def dispatch_identity(state): return {k:state[k] for k in ("fixture_id","cutoff","model_head","state_sha256","input_sha256")}

def reserve_dispatch(ledger, state, reserved_at):
    ident=dispatch_identity(state); key=sha(ident); old=ledger.get(key)
    if old: return {"status":"DUPLICATE_SUPPRESSED","ledger_key":key,"existing_status":old["status"],"dispatch_identity":ident}
    x={"status":"RESERVED","ledger_key":key,"reserved_at":iso(dt(reserved_at)),"dispatch_identity":ident}; ledger[key]=x; return x

def complete_dispatch(ledger, state, *, output_sha256, completed_at):
    ident=dispatch_identity(state); key=sha(ident); require(key in ledger and ledger[key]["status"]=="RESERVED", "DISPATCH_NOT_RESERVED")
    x={"status":"COMPLETED","ledger_key":key,"reserved_at":ledger[key]["reserved_at"],"completed_at":iso(dt(completed_at)),"dispatch_identity":ident,"output_sha256":output_sha256}; ledger[key]=x; return x

def simulate(cfg_path: Path, out_dir: Path):
    c=load_config(cfg_path); out_dir.mkdir(parents=True, exist_ok=True); reg={}
    register_fixture(c,reg,fixture_id="sim:001",competition="EPL",season="2026",kickoff="2026-10-01T19:00:00Z",observed_at="2026-09-20T10:00:00Z")
    revise_kickoff(c,reg,fixture_id="sim:001",kickoff="2026-10-01T19:30:00Z",observed_at="2026-09-30T12:00:00Z")
    fx=latest_fixture(c,reg,"sim:001")
    obs=[{"field":"home_ppda","value":9.2,"source":"sim-provider","observed_at":"2026-10-01T18:00:00Z","available_at":"2026-10-01T18:10:00Z"},{"field":"away_ppda","value":11.5,"source":"sim-provider","observed_at":"2026-10-01T18:00:00Z","available_at":"2026-10-01T18:20:00Z"},{"field":"home_ppda","value":8.8,"source":"sim-provider","observed_at":"2026-10-01T18:31:00Z","available_at":"2026-10-01T18:31:30Z"}]
    k,a=build_state_or_gap(c,fx,obs,required_fields=["home_ppda","away_ppda"],model_head="model:sim-v2",current_sha="current:sim",capture_observed_at="2026-10-01T18:30:00Z")
    _,b=build_state_or_gap(c,fx,list(reversed(obs)),required_fields=["away_ppda","home_ppda"],model_head="model:sim-v2",current_sha="current:sim",capture_observed_at="2026-10-01T18:30:00Z")
    require(k=="state" and a["state"]["state_sha256"]==b["state"]["state_sha256"] and a["state"]["payload"]["home_ppda"]==9.2, "DETERMINISM_OR_CUTOFF")
    ledger={}; r=reserve_dispatch(ledger,a["state"],"2026-10-01T18:31:00Z"); d1=reserve_dispatch(ledger,a["state"],"2026-10-01T18:31:01Z"); done=complete_dispatch(ledger,a["state"],output_sha256=hashlib.sha256(b"simulation-output").hexdigest(),completed_at="2026-10-01T18:32:00Z"); d2=reserve_dispatch(ledger,a["state"],"2026-10-01T18:32:01Z")
    register_fixture(c,reg,fixture_id="sim:gap",competition="J1",season="2026",kickoff="2026-10-02T10:00:00Z",observed_at="2026-09-20T10:00:00Z")
    _,g=build_state_or_gap(c,latest_fixture(c,reg,"sim:gap"),[{"field":"home_ppda","value":8.1,"source":"sim-provider","observed_at":"2026-10-02T08:50:00Z","available_at":"2026-10-02T08:55:00Z"}],required_fields=["home_ppda","away_ppda"],model_head="model:sim-v2",current_sha="current:sim",capture_observed_at="2026-10-02T09:00:00Z")
    require(r["status"]=="RESERVED" and d1["existing_status"]=="RESERVED" and done["status"]=="COMPLETED" and d2["existing_status"]=="COMPLETED" and g["status"]=="GAP_RECEIPT", "LEDGER_OR_GAP")
    for name,obj in (("fixture_registry.json",reg),("sealed_state.json",a["state"]),("capture_receipt.json",a["receipt"]),("gap_receipt.json",g),("dispatch_ledger.json",ledger)):
        (out_dir/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    p={"schema_version":"football3-nova-t60-foundation-simulation-receipt-v1","status":"T60_REPLAY_FOUNDATION_SIMULATION_PASS","activation_enabled":False,"replay_coverage_start_at":None,"coverage_guarantee_active":False,"competition_count":len(c["coverage"]["initial_competitions"]),"deterministic_state_replay":True,"available_at_cutoff_enforced":True,"post_cutoff_value_excluded":True,"gap_receipt_generated":True,"reservation_duplicate_suppressed":True,"completed_duplicate_suppressed":True,"result_fields_read":0,"retroactive_fabrication":False,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,"sealed_state_sha256":a["state"]["state_sha256"],"sealed_input_sha256":a["state"]["input_sha256"],"gap_receipt_id":g["gap_receipt_id"]}
    (out_dir/"simulation_receipt.json").write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return p

def main():
    ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest="cmd",required=True); s=sp.add_parser("simulate"); s.add_argument("--config",type=Path,required=True); s.add_argument("--out-dir",type=Path,required=True); a=ap.parse_args(); print(json.dumps(simulate(a.config,a.out_dir),ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()
