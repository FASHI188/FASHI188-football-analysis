#!/usr/bin/env python3
"""E3g-0D forward PIT collector: default-disabled and research-only."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from e3g0d_common import *
from e3g0d_http import Client
from e3g0d_store import Store
from e3g0d_collect import build_plan,load_plan,due,odds,injuries,lineups

def preflight(args,root,g):
    row={"schema_version":SCHEMA,"deployment_status":STATUS,"mode":"preflight","observed_at_utc":iso(now()),"provider":"API-Football","allowed_api_hosts":[HOST],"allowed_endpoints":sorted(ENDPOINTS),
         "guard":g,"competition_id":args.league,"season_id":args.season,"target_date_utc":args.date,"fixture_limit":args.fixture_limit,"max_requests":args.max_requests,
         "requests_used_today":args.requests_used_today,"timeout_seconds":args.timeout,"retries":args.retries,"backoff_cap_seconds":args.backoff,"upload_artifact":args.upload_artifact,
         "allow_schedule":args.allow_schedule,"artifact_retention_days":args.retention,"artifact_expires_at_utc":args.expires,"candidate_probabilities":0,"model_fits":0,"formal_weight":0}
    xwrite(root/"run_receipts"/f"{now().strftime('%Y%m%dT%H%M%S%fZ')}__preflight.json",packed(row)+b"\n");return row
def run_live(args,root,g):
    if args.league!=39 or args.timezone!="UTC": raise E3Error("pilot is restricted to league 39 and UTC")
    budget=Budget(args.max_requests,args.requests_used_today);client=Client(os.getenv(KEY_ENV,""),args.timeout,args.retries,args.backoff,budget)
    store=Store(root,args.run_head,args.run_id,args.retention,args.expires);obs=now();date=args.date or obs.date().isoformat();snaps=[];plan=None
    if args.mode=="build-plan": plan,s=build_plan(client,store,args.league,args.season,date,args.timezone,args.fixture_limit);snaps.append(s)
    else:
        if not args.plan: raise E3Error(f"--plan is required for {args.mode}")
        plan,fixtures=load_plan(args.plan,args.league,args.season,date,args.fixture_limit)
        if args.mode=="odds":snaps+=odds(client,store,args.league,args.season,date,args.timezone,fixtures,"scheduled_three_hour_odds")
        elif args.mode=="injuries":snaps+=injuries(client,store,fixtures,"scheduled_four_hour_injuries")
        elif args.mode=="lineup-window":
            for label,items in due(fixtures,obs,args.tolerance).items():
                if not items:continue
                snaps+=lineups(client,store,items,label)
                if label in {"T-90m","T-15m"}:snaps+=odds(client,store,args.league,args.season,date,args.timezone,items,"exact_pre_kickoff_odds",[label],label=="T-15m")
                if label=="T-15m":snaps+=injuries(client,store,items,"final_pre_kickoff_injuries",[label],True)
        else:raise E3Error("unsupported live mode")
    row={"schema_version":SCHEMA,"deployment_status":STATUS,"provider":"API-Football","mode":args.mode,"observed_at_utc":iso(obs),"target_date_utc":date,"competition_id":args.league,
         "season_id":args.season,"fixture_limit":args.fixture_limit,"snapshot_count":len(snaps),"snapshots":snaps,"plan_fixture_count":plan.get("fixture_count") if plan else None,
         "request_attempts":budget.attempts,"max_requests":budget.max_requests,"requests_used_today_before_run":budget.used_today,"daily_free_limit":DAILY_LIMIT,"daily_safety_cap":DAILY_CAP,
         "run_head":store.head,"workflow_run_id":store.run_id,"artifact_retention_days":args.retention,"artifact_expires_at_utc":args.expires,"guard":g,"candidate_probabilities":0,"model_fits":0,"formal_weight":0,"append_only":True}
    xwrite(root/"run_receipts"/f"{obs.strftime('%Y%m%dT%H%M%S%fZ')}__{args.mode}.json",packed(row)+b"\n");return row
def self_test(root):
    obs=datetime(2026,8,15,14,30,tzinfo=timezone.utc);store=Store(root,"SELFTEST_HEAD","SELFTEST_RUN",30,iso(obs+timedelta(days=30)));sentinel="SECRET_SHOULD_NEVER_APPEAR"
    fixture={"provider":"API-Football","competition_id":39,"season_id":2026,"fixture_id":1001,"home_team_id":1,"away_team_id":2,"scheduled_kickoff_utc":"2026-08-15T16:00:00Z","provider_updated_at":None}
    payload={"errors":[],"results":1,"response":[{"fixture":{"id":1001}}]};raw=packed(payload)
    a=store.save("fixtures",{"league":39,"season":2026,"date":"2026-08-15"},raw,payload,obs,obs,200,{"authorization":sentinel},"self_test",[fixture])
    b=store.save("fixtures",{"league":39,"season":2026,"date":"2026-08-15"},raw,payload,obs+timedelta(seconds=1),obs+timedelta(seconds=1),200,{},"duplicate",[fixture])
    empty={"errors":[],"results":0,"response":[]};store.save("injuries",{"ids":"1001"},packed(empty),empty,obs+timedelta(minutes=75),obs+timedelta(minutes=75),200,{},"missing",[fixture],["T-15m"],True)
    records=[json.loads(p.read_text()) for p in (root/"records").rglob("*.json")]
    assert a["sha256"]==b["sha256"]==sha(raw);assert len(list((root/"raw").rglob(f"sha256_{sha(raw)}.json")))==1
    assert any(r["data_status"]=="MISSING_UNINTERPRETED" for r in records);assert any(r["is_final_pre_kickoff_candidate"] for r in records);assert all(not r["is_final_pre_kickoff_freeze_version"] for r in records)
    blob=b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file());assert sentinel.encode() not in blob
    q=Budget(3,0);[q.take() for _ in range(3)]
    try:q.take();raise AssertionError
    except E3Error:pass
    try:api_url("fixtures",{"key":"bad"});raise AssertionError
    except E3Error:pass
    result={"self_test":"PASS","deployment_status":STATUS,"secret_redaction":"PASS","append_only":"PASS","raw_deduplication_without_overwrite":"PASS","missing_empty_list_semantics":"PASS","freeze_candidate_marker":"PASS","online_final_freeze_prohibited":"PASS","host_allowlist":"PASS","request_budget":"PASS","no_network":True,"candidate_probabilities":0,"model_fits":0,"formal_weight":0}
    xwrite(root/"self_test_result.json",packed(result)+b"\n");return result
def parser():
    p=argparse.ArgumentParser();p.add_argument("--mode",required=True,choices=["self-test","preflight","build-plan","odds","injuries","lineup-window"]);p.add_argument("--output-dir",required=True);p.add_argument("--plan");p.add_argument("--date");p.add_argument("--league",type=int,default=39);p.add_argument("--season",type=int,default=2026);p.add_argument("--timezone",default="UTC");p.add_argument("--fixture-limit",type=int,default=1);p.add_argument("--max-requests",type=int,default=3);p.add_argument("--requests-used-today",type=int,default=0);p.add_argument("--timeout",type=float,default=15.);p.add_argument("--retries",type=int,default=1);p.add_argument("--backoff",type=float,default=8.);p.add_argument("--tolerance",type=int,default=7);p.add_argument("--dry-run",type=boolv,default=True);p.add_argument("--no-network",type=boolv,default=True);p.add_argument("--upload-artifact",type=boolv,default=False);p.add_argument("--allow-schedule",type=boolv,default=False);p.add_argument("--retention",type=int,default=30);p.add_argument("--expires");p.add_argument("--run-head",default=os.getenv("GITHUB_SHA","LOCAL_UNCOMMITTED"));p.add_argument("--run-id",default=os.getenv("GITHUB_RUN_ID","LOCAL"));p.add_argument("--print-summary",action="store_true");return p
def main():
    args=parser().parse_args();root=Path(args.output_dir);root.mkdir(parents=True,exist_ok=True)
    try:
        if not 1<=args.fixture_limit<=20:raise E3Error("fixture limit must be 1..20")
        args.expires=args.expires or expiry(args.retention);g=guard(args)
        result=self_test(root) if args.mode=="self-test" else preflight(args,root,g) if args.mode=="preflight" or args.dry_run or args.no_network else run_live(args,root,g)
    except E3Error as exc:print(f"E3g-0D collector error: {exc}",file=sys.stderr);return 2
    if args.print_summary:print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
    return 0
if __name__=="__main__":raise SystemExit(main())
