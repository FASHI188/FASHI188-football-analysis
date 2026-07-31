"""Fixture identity, plan, and collection operations."""
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path
from e3g0d_common import E3Error,PLAN_SCHEMA,TARGETS,iso,now,parse_utc,packed,sha,xwrite

def updated(row):
    for value in (row.get("update"),row.get("updated_at"),(row.get("fixture") or {}).get("updated_at")):
        if value:
            try:return iso(parse_utc(value))
            except E3Error:return str(value)
    return None
def fixture_rows(payload,league,season,date):
    rows=payload.get("response")
    if not isinstance(rows,list): raise E3Error("fixtures response is not a list")
    out=[]
    for row in rows:
        if not isinstance(row,dict): raise E3Error("invalid fixture row")
        fx=row.get("fixture") or {};lg=row.get("league") or {};teams=row.get("teams") or {};home=teams.get("home") or {};away=teams.get("away") or {}
        req=[fx.get("id"),fx.get("date"),lg.get("id"),lg.get("season"),home.get("id"),away.get("id")]
        if any(v is None for v in req): raise E3Error("fixture identity mapping failed")
        if int(lg["id"])!=league or int(lg["season"])!=season: raise E3Error("competition or season mismatch")
        ko=parse_utc(fx["date"])
        if ko.date().isoformat()!=date or ko<now()-timedelta(hours=2) or ko>now()+timedelta(days=400): raise E3Error("abnormal kickoff time")
        out.append({"provider":"API-Football","competition_id":league,"season_id":season,"fixture_id":int(fx["id"]),"home_team_id":int(home["id"]),"away_team_id":int(away["id"]),
                    "home_team_name":home.get("name"),"away_team_name":away.get("name"),"scheduled_kickoff_utc":iso(ko),"provider_updated_at":updated(row),"status_short":(fx.get("status") or {}).get("short")})
    return sorted(out,key=lambda x:(x["scheduled_kickoff_utc"],x["fixture_id"]))
def api_save(client,store,endpoint,params,role,fixtures=(),labels=(),final=False):
    raw,payload,req,obs,status,headers=client.get(endpoint,params)
    return store.save(endpoint,params,raw,payload,req,obs,status,headers,role,fixtures,labels,final),payload
def build_plan(client,store,league,season,date,tz,limit):
    params={"league":league,"season":season,"date":date,"timezone":tz};raw,payload,req,obs,status,headers=client.get("fixtures",params)
    fixtures=fixture_rows(payload,league,season,date)[:limit]
    snap=store.save("fixtures",params,raw,payload,req,obs,status,headers,"daily_fixture_plan_source",fixtures)
    plan={"schema_version":PLAN_SCHEMA,"deployment_status":"IMPLEMENTED_NOT_LIVE","provider":"API-Football","competition_id":league,"season_id":season,"date_utc":date,"timezone":tz,
          "observed_at_utc":snap["observed_at_utc"],"fixtures":fixtures,"fixture_count":len(fixtures),"source_raw_response_sha256":snap["sha256"],"source_manifest_path":snap["manifest_path"],
          "run_head":store.head,"workflow_run_id":store.run_id,"append_only":True,"formal_weight":0}
    h=sha(packed(plan));xwrite(store.root/"plans"/f"{date}__league_{league}__season_{season}__sha256_{h}.json",packed(plan)+b"\n");return plan,snap
def load_plan(path,league,season,date,limit):
    try: plan=json.loads(Path(path).read_text())
    except Exception as exc: raise E3Error("unable to load fixture plan") from exc
    if plan.get("schema_version")!=PLAN_SCHEMA or int(plan.get("competition_id",-1))!=league or int(plan.get("season_id",-1))!=season or plan.get("date_utc")!=date: raise E3Error("fixture plan identity mismatch")
    fixtures=plan.get("fixtures")
    if not isinstance(fixtures,list): raise E3Error("invalid fixture plan")
    selected=fixtures[:limit]
    need={"competition_id","season_id","fixture_id","home_team_id","away_team_id","scheduled_kickoff_utc"}
    if any(not isinstance(f,dict) or not need.issubset(f) for f in selected): raise E3Error("plan mapping failed")
    return plan,[dict(f) for f in selected]
def due(fixtures,observed,tolerance):
    if not 0<=tolerance<=15: raise E3Error("invalid due-window tolerance")
    out={k:[] for k in TARGETS}
    for fx in fixtures:
        mins=(parse_utc(fx["scheduled_kickoff_utc"])-observed).total_seconds()/60
        for label,target in TARGETS.items():
            if target-tolerance<=mins<=target+tolerance: out[label].append(dict(fx))
    return out
def odds(client,store,league,season,date,tz,fixtures,role,labels=(),final=False):
    snap,_=api_save(client,store,"odds",{"league":league,"season":season,"date":date,"timezone":tz,"page":1},role,fixtures,labels,final);return [snap]
def injuries(client,store,fixtures,role,labels=(),final=False):
    ids=[int(f["fixture_id"]) for f in fixtures]
    if not ids:return []
    if len(ids)>20: raise E3Error("injury batch exceeds 20 fixtures")
    snap,_=api_save(client,store,"injuries",{"ids":"-".join(map(str,ids)),"timezone":"UTC"},role,fixtures,labels,final);return [snap]
def lineups(client,store,fixtures,label):
    out=[]
    for fx in fixtures:
        snap,_=api_save(client,store,"fixtures/lineups",{"fixture":int(fx["fixture_id"])},"pre_kickoff_lineup_poll",[fx],[label],label=="T-15m");out.append(snap)
    return out
