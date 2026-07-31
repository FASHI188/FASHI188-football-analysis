#!/usr/bin/env python3
"""Manual read-only Artifact archive, final-freeze, and schedule-status helper."""
from __future__ import annotations
import argparse,io,json,os,sys,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from e3g0d_common import E3Error,iso,now,parse_utc,sha,xwrite
from e3g0d_archive_core import GitHubReader,WORKFLOW,archive_one,archived_ids,rows,verify_zip,append_manifest

def list_unarchived(reader,root):
    done=archived_ids(root);items=[]
    for a in reader.artifacts():
        if int(a["id"]) not in done:items.append({k:a.get(k) for k in ("id","name","created_at","expires_at","expired","size_in_bytes","digest")})
    return {"operation":"list-unarchived","read_only":True,"maximum_archive_interval_days":30,"unarchived_count":len(items),"artifacts":items}
def freeze_manifest(root):return Path(root)/"final_freeze_manifest.jsonl"
def freeze_ids(root):
    p=freeze_manifest(root)
    return {json.loads(x)["marker_id"] for x in p.read_text().splitlines() if x.strip()} if p.exists() else set()
def append_freeze(root,row):
    if row["marker_id"] in freeze_ids(root):return
    p=freeze_manifest(root);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("ab") as f:f.write(json.dumps(row,sort_keys=True,separators=(",",":")).encode()+b"\n");f.flush();os.fsync(f.fileno())
def finalize(root,as_of):
    groups={}
    for ar in rows(root):
        path=Path(root)/ar["local_path"]
        if not path.exists():raise E3Error("archive manifest references missing ZIP")
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if "/records/" not in f"/{name}" or not name.endswith(".json"):continue
                rec=json.loads(z.read(name));need={"fixture_id","request_endpoint_type","kickoff_version_id","scheduled_kickoff_utc","observed_at_utc","raw_response_sha256","is_pre_kickoff"}
                if not need.issubset(rec):raise E3Error("PIT record missing freeze fields")
                ko=parse_utc(rec["scheduled_kickoff_utc"]);obs=parse_utc(rec["observed_at_utc"])
                if ko>as_of or not rec["is_pre_kickoff"] or obs>=ko:continue
                rec=dict(rec,source_artifact_id=int(ar["artifact_id"]),source_archive_path=ar["local_path"],source_member_path=name)
                groups.setdefault((int(rec["fixture_id"]),rec["request_endpoint_type"],rec["kickoff_version_id"]),[]).append(rec)
    out=[]
    for (fixture,endpoint,kickoff),items in sorted(groups.items()):
        selected=max(items,key=lambda r:parse_utc(r["observed_at_utc"]));basis={"fixture_id":fixture,"endpoint":endpoint,"kickoff":kickoff,"observed":selected["observed_at_utc"],"raw":selected["raw_response_sha256"]};mid=sha(json.dumps(basis,sort_keys=True).encode())
        row={"schema_version":"E3G0D-FINAL-FREEZE-1.0","marker_id":mid,"provider":selected.get("provider"),"competition_id":selected.get("competition_id"),"season_id":selected.get("season_id"),"fixture_id":fixture,"home_team_id":selected.get("home_team_id"),"away_team_id":selected.get("away_team_id"),"scheduled_kickoff_utc":selected["scheduled_kickoff_utc"],"kickoff_version_id":kickoff,"request_endpoint_type":endpoint,"selected_observed_at_utc":selected["observed_at_utc"],"selected_raw_response_sha256":selected["raw_response_sha256"],"selected_source_artifact_id":selected["source_artifact_id"],"selected_source_archive_path":selected["source_archive_path"],"selected_source_member_path":selected["source_member_path"],"is_final_pre_kickoff_freeze_version":True,"selection_rule":"latest observed_at_utc strictly before same kickoff version after kickoff","finalized_at_utc":iso(now()),"as_of_utc":iso(as_of),"append_only":True,"github_artifact_modified":False,"repository_modified":False,"formal_weight":0}
        rel=Path("final_freeze_markers")/str(fixture)/endpoint.replace("/","__")/f"{kickoff}__{mid}.json"
        if not (Path(root)/rel).exists():xwrite(Path(root)/rel,json.dumps(row,sort_keys=True).encode()+b"\n")
        append_freeze(root,dict(row,marker_path=rel.as_posix()));out.append(row)
    return {"operation":"finalize-freeze","read_only_inputs":True,"as_of_utc":iso(as_of),"candidate_groups":len(groups),"final_markers":out,"repository_modified":False}
def expected(now_):
    cur=(now_-timedelta(hours=48)).replace(second=0,microsecond=0);out=[]
    while cur<=now_:
        h,m=cur.hour,cur.minute
        if (h==0 and m==5) or (m==10 and h%3==0) or (m==20 and h%4==0) or (10<=h<=22 and m%15==0):out.append(cur)
        cur+=timedelta(minutes=5)
    return sorted(set(out))
def status(reader):
    n=now();error=None
    try:w=reader.json(f"/repos/{reader.repo}/actions/workflows/{WORKFLOW}");wid=w.get("id");state=w.get("state")
    except E3Error as exc:wid=None;state="NOT_ON_DEFAULT_BRANCH_OR_UNAVAILABLE";error=str(exc)
    runs=[]
    if wid:
        value=reader.json(f"/repos/{reader.repo}/actions/workflows/{wid}/runs?per_page=100").get("workflow_runs");runs=[dict(x) for x in value or [] if isinstance(x,dict)]
    schedules=sorted([r for r in runs if r.get("event")=="schedule"],key=lambda r:r.get("created_at",''),reverse=True);last=parse_utc(schedules[0]["created_at"]) if schedules else None;due=expected(n)[-1]
    near=[]
    for a in reader.artifacts():
        if a.get("expires_at") and not a.get("expired"):
            exp=parse_utc(a["expires_at"])
            if exp<=n+timedelta(days=7):near.append({"artifact_id":a["id"],"name":a["name"],"expires_at":a["expires_at"]})
    return {"operation":"status","read_only":True,"workflow_file":WORKFLOW,"workflow_id":wid,"workflow_state":state,"workflow_error":error,"latest_expected_schedule_utc":iso(due),"latest_actual_schedule_run_utc":iso(last) if last else None,"latest_actual_run_utc":max((r.get("created_at",'') for r in runs),default=None),"possible_missed_schedule":last is None or last<due-timedelta(minutes=20),"artifacts_near_expiry":near,"meaningless_keepalive_commit_permitted":False}
def self_test(root):
    raw=b'{"errors":[],"results":0,"response":[]}';h=sha(raw);manifest={"raw_response_path":"raw/test.json","raw_response_sha256":h};record={"provider":"API-Football","competition_id":39,"season_id":2026,"fixture_id":1,"home_team_id":1,"away_team_id":2,"scheduled_kickoff_utc":"2026-08-15T16:00:00Z","kickoff_version_id":"k1","observed_at_utc":"2026-08-15T15:45:00Z","request_endpoint_type":"injuries","raw_response_sha256":h,"is_pre_kickoff":True}
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w") as z:z.writestr("bundle/raw/test.json",raw);z.writestr("bundle/manifests/test.manifest.json",json.dumps(manifest));z.writestr("bundle/records/1/injuries/test.json",json.dumps(record))
    data=b.getvalue();check=verify_zip(data);rel=Path("artifacts/self-test.zip");xwrite(Path(root)/rel,data);append_manifest(root,{"schema_version":"TEST","artifact_id":123,"local_path":rel.as_posix()});f=finalize(root,datetime(2026,8,16,tzinfo=timezone.utc));assert len(f["final_markers"])==1
    result={"self_test":"PASS","zip_verification":check,"append_only_local_manifest":"PASS","post_kickoff_final_freeze_selection":"PASS","network_used":False,"github_artifact_deleted":False,"repository_modified":False,"automatic_execution":False};xwrite(Path(root)/"archive_helper_self_test.json",json.dumps(result,sort_keys=True).encode()+b"\n");return result
def main():
    p=argparse.ArgumentParser();p.add_argument("command",choices=["self-test","list-unarchived","download","finalize-freeze","status"]);p.add_argument("--repository",default="FASHI188/FASHI188-football-analysis");p.add_argument("--archive-root",default="e3g0d-local-archive");p.add_argument("--artifact-id",type=int);p.add_argument("--as-of-utc");p.add_argument("--print-summary",action="store_true");a=p.parse_args();root=Path(a.archive_root)
    try:
        if a.command=="self-test":result=self_test(root)
        elif a.command=="finalize-freeze":result=finalize(root,parse_utc(a.as_of_utc) if a.as_of_utc else now())
        else:
            r=GitHubReader(a.repository,os.getenv("GH_TOKEN",""));result=list_unarchived(r,root) if a.command=="list-unarchived" else archive_one(r,root,a.artifact_id) if a.command=="download" and a.artifact_id else status(r) if a.command=="status" else (_ for _ in ()).throw(E3Error("artifact id required"))
        if a.command!="self-test":xwrite(root/f"{a.command.replace('-','_')}_{now().strftime('%Y%m%dT%H%M%S%fZ')}.json",json.dumps(result,sort_keys=True).encode()+b"\n")
    except E3Error as exc:print(f"E3g-0D archive helper error: {exc}",file=sys.stderr);return 2
    if a.print_summary:print(json.dumps(result,indent=2,sort_keys=True));return 0
    return 0
if __name__=="__main__":raise SystemExit(main())
