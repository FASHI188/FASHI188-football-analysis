from __future__ import annotations
import argparse, hashlib, json, math, pathlib, tempfile, urllib.request
from datetime import datetime, timedelta, timezone

LABEL_KEYS={"home_goals","away_goals","result","winner","score","label"}
EVENT_REQUIRED={"team","xg","is_penalty","context","event_time_seconds"}

class InventoryError(RuntimeError): pass

def canon(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_file(p): return sha_bytes(pathlib.Path(p).read_bytes())
def dump(p,obj):
    p=pathlib.Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8"); return p

def load_contract(path):
    c=json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if c["status"]!="FROZEN_BEFORE_TARGET_LABEL_INGESTION": raise InventoryError("contract not frozen")
    return c

def compute_power(c):
    p=c["power_plan"]; sd=float(p["paired_delta_sd"]); effect=float(p["target_effect_absolute_logloss_gain"])
    req=math.ceil(((float(p["z_0_975"])+float(p["z_0_80"]))*sd/effect)**2)
    if req!=int(p["required_n"]): raise InventoryError(f"required_n drift {req}")
    return {**p,"recomputed_required_n":req,"status":"FROZEN_POWER_PLAN_VERIFIED"}

def canonical_fixture(row):
    required=("fixture_id","competition","season","home","away","kickoff")
    if any(not str(row.get(k,"")) for k in required): raise InventoryError("missing canonical identity field")
    return {k:row[k] for k in required}

def validate_event(ev):
    if EVENT_REQUIRED-set(ev): raise InventoryError("missing shot-event field")
    x=float(ev["xg"]); t=float(ev["event_time_seconds"])
    if not math.isfinite(x) or x<0 or not math.isfinite(t) or t<0: raise InventoryError("invalid shot event")
    context=str(ev["context"])
    if context not in {"OPEN_PLAY","SET_PIECE","OTHER_VERIFIED_NONPENALTY","PENALTY"}: raise InventoryError("unfrozen context semantics")
    if bool(ev["is_penalty"]) != (context=="PENALTY"): raise InventoryError("penalty semantic conflict")

def trusted_split(raw_rows, feature_dir, vault_dir, source_receipt):
    """Trusted boundary. It never prints/returns label values; only hashes/counts."""
    feature_dir=pathlib.Path(feature_dir); vault_dir=pathlib.Path(vault_dir)
    if feature_dir.resolve()==vault_dir.resolve(): raise InventoryError("feature/vault path collision")
    feature_dir.mkdir(parents=True,exist_ok=True); vault_dir.mkdir(parents=True,exist_ok=True)
    feats=[]; labs=[]; seen={}
    for row in raw_rows:
        ident=canonical_fixture(row); fid=str(ident["fixture_id"])
        ident_sha=sha_bytes(canon(ident))
        if fid in seen and seen[fid]!=ident_sha: raise InventoryError("fixture identity conflict")
        seen[fid]=ident_sha
        events=list(row.get("events") or [])
        for ev in events: validate_event(ev)
        release_at=str(row.get("release_at") or "")
        if not release_at: raise InventoryError("missing release time")
        feat={**ident,"release_at":release_at,"events":events,"source_sha256":source_receipt["sha256"],"source_semantics_sha256":source_receipt["field_semantics_sha256"]}
        if LABEL_KEYS & set(feat): raise InventoryError("label mixed into feature store")
        label={"fixture_id":fid}
        for k in ("home_goals","away_goals"):
            if k not in row: raise InventoryError("trusted raw missing sealed label")
            label[k]=int(row[k])
        feats.append(feat); labs.append(label)
    feats.sort(key=lambda r:(r["kickoff"],r["fixture_id"])); labs.sort(key=lambda r:r["fixture_id"])
    fp=feature_dir/"feature_pit_store.jsonl"; vp=vault_dir/"sealed_label_vault.jsonl"
    fp.write_text("".join(json.dumps(r,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n" for r in feats),encoding="utf-8")
    vp.write_text("".join(json.dumps(r,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n" for r in labs),encoding="utf-8")
    if any(k in fp.read_bytes() for k in (b'home_goals',b'away_goals',b'result',b'winner')): raise InventoryError("feature label token leak")
    return {"n":len(feats),"feature_store_sha256":sha_file(fp),"sealed_vault_sha256":sha_file(vp),"identity_sha256":sha_bytes(canon([canonical_fixture(r) for r in feats])),"labels_returned":False}

def pit_eligible_history(feature_rows,target_kickoff):
    t=datetime.fromisoformat(str(target_kickoff).replace("Z","+00:00"))
    out=[]
    for r in feature_rows:
        rel=datetime.fromisoformat(str(r["release_at"]).replace("Z","+00:00"))
        if rel<t: out.append(r)
    return out

def batch_freeze_order(target_rows):
    groups={}
    for r in target_rows: groups.setdefault(str(r["kickoff"]),[]).append(str(r["fixture_id"]))
    return [{"kickoff":k,"predict_before_release":sorted(v)} for k,v in sorted(groups.items())]

def source_census(c,out):
    s=c["source_evidence"]["statsbomb_open_data"]
    url=f"https://raw.githubusercontent.com/hudl/open-data/{s['exact_commit']}/data/competitions.json"
    req=urllib.request.Request(url,headers={"User-Agent":"football3-sealed-inventory-zero-label/1.0"})
    with urllib.request.urlopen(req,timeout=60) as r: b=r.read()
    rows=json.loads(b.decode("utf-8"))
    allowed=[]
    for x in rows:
        allowed.append({k:x.get(k) for k in ("competition_id","season_id","country_name","competition_name","competition_gender","competition_international","season_name")})
    rec={"schema_version":"football3-zero-label-source-census-v1","source_url":url,"exact_commit":s["exact_commit"],"downloaded_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"sha256":sha_bytes(b),"bytes":len(b),"competition_season_container_n":len(allowed),"label_fields_accessed":0,"match_files_accessed":0,"target_labels_opened":False,"status":"PASS_ZERO_LABEL_SOURCE_FREEZE"}
    dump(out,rec); return rec

def run_gate(c,outdir):
    outdir=pathlib.Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    power=compute_power(c); dump(outdir/"power_plan.json",power)
    bound=c["mechanical_eligibility_bound"]
    req=int(power["recomputed_required_n"]); upper=int(bound["full_season_big5_capacity_upper_bound"])
    if upper>=req: raise InventoryError("stop bound no longer proves insufficiency; trusted ingestion requires a new decision")
    result={
      "schema_version":"football3-historical-shot-event-sealed-inventory-terminal-v1",
      "status":"NO_ELIGIBLE_SEALED_SHOT_EVENT_COHORT",
      "required_n":req,"eligible_target_identity_upper_bound":upper,"bound_ratio":upper/req,
      "reason":"all Big-5 target identities through 2025/26 are consumed/exposed; pre-2014 is frozen-baseline incompatible; even a full 2026/27 Big-5 season has at most 1752 fixtures, below required_n 6481",
      "target_match_files_downloaded":0,"target_labels_opened":False,"sealed_label_vault_created":False,
      "predictor_run":False,"scorer_run":False,"candidate_modified":False,"formal_v2_modified":False,
      "CURRENT_changed":False,"production_pointer_changed":False,"formal_enablement_changed":False,"pr340_changed":False,"future_queue_created":False
    }
    dump(outdir/"terminal_status.json",result); return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--contract",type=pathlib.Path,required=True); sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("source-census"); p.add_argument("--out",type=pathlib.Path,required=True)
    p=sub.add_parser("gate"); p.add_argument("--outdir",type=pathlib.Path,required=True)
    a=ap.parse_args(); c=load_contract(a.contract)
    if a.cmd=="source-census": r=source_census(c,a.out)
    else: r=run_gate(c,a.outdir)
    print(json.dumps({k:v for k,v in r.items() if k not in LABEL_KEYS},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
