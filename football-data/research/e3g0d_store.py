"""Append-only PIT evidence store."""
from __future__ import annotations
import json
from pathlib import Path
from e3g0d_common import SCHEMA,STATUS,SAFE_HEADERS,clean_params,iso,parse_utc,packed,sha,slug,kickoff_id,xwrite,raw_write

def response_ids(payload):
    out=set(); rows=payload.get("response")
    if not isinstance(rows,list): return out
    for row in rows:
        if not isinstance(row,dict): continue
        fx=row.get("fixture")
        if isinstance(fx,dict) and fx.get("id") is not None: out.add(int(fx["id"]))
    return out
class Store:
    def __init__(self,root,head,run_id,retention,expires):
        self.root=Path(root);self.head=head or "LOCAL_UNCOMMITTED";self.run_id=run_id or "LOCAL";self.retention=retention;self.expires=expires;self.seq=0
    def event(self,obs,endpoint,h):
        self.seq+=1
        return f"{obs.strftime('%Y%m%dT%H%M%S%fZ')}__{slug(endpoint)}__{self.run_id}__{self.seq:04d}__{h[:16]}"
    def save(self,endpoint,params,raw,payload,requested,observed,status,headers,role,fixtures=(),labels=(),final_candidate=False):
        h=sha(raw); day=observed.strftime("%Y/%m/%d"); ep=slug(endpoint)
        raw_rel=Path("raw")/day/ep/f"sha256_{h}.json"; new=raw_write(self.root/raw_rel,raw); event=self.event(observed,endpoint,h)
        ids=response_ids(payload); records=[]
        for fx in fixtures:
            fixture_id=int(fx["fixture_id"]); kickoff=parse_utc(fx["scheduled_kickoff_utc"]); pre=observed<kickoff
            present=bool(payload.get("response")) if endpoint=="fixtures/lineups" and len(fixtures)==1 else fixture_id in ids
            if present: data_status,missing="PRESENT",None
            elif payload.get("response")==[]: data_status,missing="MISSING_UNINTERPRETED","provider_empty_response_not_equivalent_to_no_injury_or_no_lineup"
            else: data_status,missing="MISSING_UNMAPPED","fixture_not_present_in_provider_response"
            rec={"schema_version":SCHEMA,"provider":"API-Football","competition_id":int(fx["competition_id"]),"season_id":int(fx["season_id"]),"fixture_id":fixture_id,
                 "home_team_id":int(fx["home_team_id"]),"away_team_id":int(fx["away_team_id"]),"scheduled_kickoff_utc":iso(kickoff),"kickoff_version_id":kickoff_id(fx),
                 "provider_updated_at":fx.get("provider_updated_at"),"observed_at_utc":iso(observed),"requested_at_utc":iso(requested),"request_endpoint_type":endpoint,
                 "raw_response_sha256":h,"raw_response_path":raw_rel.as_posix(),"run_head":self.head,"workflow_run_id":self.run_id,"data_status":data_status,"missing_reason":missing,
                 "is_pre_kickoff":pre,"is_final_pre_kickoff_candidate":bool(final_candidate and pre),"is_final_pre_kickoff_freeze_version":False,
                 "final_freeze_rule":"post-kickoff local finalizer selects latest observation strictly before same kickoff version","target_labels":sorted(set(labels)),"append_only":True,"formal_weight":0}
            rel=Path("records")/str(fixture_id)/ep/f"{event}.json";xwrite(self.root/rel,packed(rec)+b"\n");rec["record_path"]=rel.as_posix();records.append(rec)
        manifest={"schema_version":SCHEMA,"deployment_status":STATUS,"provider":"API-Football","request_endpoint_type":endpoint,"role":role,"request":{"params":clean_params(params)},
                  "requested_at_utc":iso(requested),"observed_at_utc":iso(observed),"http_status":status,"safe_response_headers":{k.lower():v for k,v in headers.items() if k.lower() in SAFE_HEADERS},
                  "raw_response_sha256":h,"raw_response_path":raw_rel.as_posix(),"raw_blob_newly_written":new,"provider_results":payload.get("results") if isinstance(payload.get("results"),int) else None,
                  "records":records,"run_head":self.head,"workflow_run_id":self.run_id,"artifact_retention_days":self.retention,"artifact_expires_at_utc":self.expires,"append_only":True,"formal_weight":0}
        man_rel=Path("manifests")/day/f"{event}.manifest.json";xwrite(self.root/man_rel,packed(manifest)+b"\n")
        return {"endpoint":endpoint,"observed_at_utc":iso(observed),"sha256":h,"raw_path":raw_rel.as_posix(),"manifest_path":man_rel.as_posix(),"record_count":len(records)}
