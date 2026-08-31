from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ALLOWED_TIERS={"TIER_1_OFFICIAL","TIER_2_OPEN_STRUCTURED","TIER_3_APPROVED_ARCHIVE"}
ALLOWED_PREDICATES={"schedule","prior_result","prior_event","prior_lineup","prior_minutes","injury","suspension","expected_return","coach_change","expected_lineup","confirmed_lineup","venue","weather","competition_rule","referee","geospatial","tracking","process_event"}
PROHIBITED_KEYS={"home_goals","away_goals","final_score","result","target_result","actual_substitution","actual_red_card","actual_var","actual_stoppage"}
REQUIRED_PROVENANCE={"source_url","raw_sha256","published_at","observed_at","retrieved_at","known_at","source_tier","extraction_confidence","provider_license","immutable_source_ref"}

SB_REPO="hudl/open-data"
SB_COMMIT="b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RELEASE_HOURS=6
TARGETS=(
    {"competition_id":7,"season_id":108,"competition_name":"Ligue 1","season_name":"2021/2022","v2_competition_id":"FRA1","v2_season":"2021/22"},
    {"competition_id":7,"season_id":235,"competition_name":"Ligue 1","season_name":"2022/2023","v2_competition_id":"FRA1","v2_season":"2022/23"},
    {"competition_id":9,"season_id":27,"competition_name":"1. Bundesliga","season_name":"2015/2016","v2_competition_id":"GER1","v2_season":"2015/16"},
    {"competition_id":9,"season_id":281,"competition_name":"1. Bundesliga","season_name":"2023/2024","v2_competition_id":"GER1","v2_season":"2023/24"},
)
LAYERS=("0_v2_team_core","1_player_capability","2_expected_lineup","3_confirmed_lineup","4_bench_substitution","5_coach_regime","6_tactical_matchup","7_fitness_schedule_travel","8_referee_competition_environment","9_pre_match_process_hazard")
NOISE_TOKENS={"fc","sc","sv","vfl","vfb","tsg","rb","ac","as","aj","rc","ogc","hsc","osc","sco","estac","1","04","05","29","63","96","98","1846","1848","1899","de"}
WORD_REWRITE={"munich":"munchen","muenchen":"munchen","lyonnais":"lyon","brestois":"brest","rennais":"rennes"}

class PITViolation(RuntimeError): pass

def _dt(value:str|None,field:str,*,nullable:bool=False)->datetime|None:
    if value is None and nullable:return None
    if not isinstance(value,str) or not value.strip():raise PITViolation(f"{field} must be timezone-aware ISO datetime")
    try:d=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError as exc:raise PITViolation(f"invalid {field}: {value!r}") from exc
    if d.tzinfo is None or d.utcoffset() is None:raise PITViolation(f"{field} missing timezone")
    return d.astimezone(timezone.utc)

def canonical_sha(value:Any)->str:
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class Provenance:
    source_url:str;raw_sha256:str;published_at:str|None;observed_at:str|None;retrieved_at:str;known_at:str;source_tier:str;extraction_confidence:float;provider_license:str;immutable_source_ref:str
    def validate(self,cutoff:str)->None:
        if self.source_tier not in ALLOWED_TIERS:raise PITViolation(f"source tier denied: {self.source_tier}")
        if len(self.raw_sha256)!=64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):raise PITViolation("raw_sha256 must be lowercase 64-hex")
        if not self.source_url or not self.provider_license or not self.immutable_source_ref:raise PITViolation("source URL, license and immutable source ref are required")
        if not isinstance(self.extraction_confidence,(int,float)) or isinstance(self.extraction_confidence,bool) or not 0<=float(self.extraction_confidence)<=1:raise PITViolation("invalid extraction_confidence")
        known,co=_dt(self.known_at,"known_at"),_dt(cutoff,"cutoff");_dt(self.retrieved_at,"retrieved_at");_dt(self.published_at,"published_at",nullable=True);_dt(self.observed_at,"observed_at",nullable=True)
        if known>=co:raise PITViolation(f"known_at must be strictly before cutoff: {known} >= {co}")

@dataclass(frozen=True)
class RawFact:
    predicate:str;entity_type:str;entity_id:str;value:Any;provenance:Provenance
    def validate(self,cutoff:str)->None:
        if self.predicate not in ALLOWED_PREDICATES:raise PITViolation(f"predicate default-denied: {self.predicate}")
        if not self.entity_type or not self.entity_id:raise PITViolation("entity identity missing")
        if isinstance(self.value,dict) and PROHIBITED_KEYS.intersection(self.value):raise PITViolation(f"target/post-match field denied: {sorted(PROHIBITED_KEYS.intersection(self.value))}")
        self.provenance.validate(cutoff)
    def sha256(self)->str:return canonical_sha({"predicate":self.predicate,"entity_type":self.entity_type,"entity_id":self.entity_id,"value":self.value,"provenance":asdict(self.provenance)})

def fact_from_mapping(row:dict[str,Any],cutoff:str)->RawFact:
    allowed={"predicate","entity_type","entity_id","value","provenance"}
    if set(row)!=allowed:raise PITViolation(f"raw fact schema mismatch extra/missing={sorted(set(row)^allowed)}")
    prov=row["provenance"]
    if not isinstance(prov,dict) or set(prov)!=REQUIRED_PROVENANCE:raise PITViolation("provenance schema mismatch")
    fact=RawFact(str(row["predicate"]),str(row["entity_type"]),str(row["entity_id"]),row["value"],Provenance(**prov));fact.validate(cutoff);return fact

def ingest(rows:Iterable[dict[str,Any]],cutoff:str)->list[RawFact]:
    out=[fact_from_mapping(r,cutoff) for r in rows];hs=[x.sha256() for x in out]
    if len(hs)!=len(set(hs)):raise PITViolation("duplicate raw fact payload")
    return out

def _iso(d:datetime)->str:return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def _read_jsonl(p:Path)->list[dict[str,Any]]:return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
def _dump(p:Path,obj:Any)->None:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
def _sha_file(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()
def _url(rel:str)->str:return f"https://raw.githubusercontent.com/{SB_REPO}/{SB_COMMIT}/{rel}"
def _get(rel:str)->bytes:
    last=None
    for i in range(5):
        try:
            req=urllib.request.Request(_url(rel),headers={"User-Agent":"football3-translator-v1-research"})
            with urllib.request.urlopen(req,timeout=90) as r:return r.read()
        except Exception as exc:last=exc;time.sleep(min(2**i,12))
    raise PITViolation(f"download failed {rel}: {last}")
def _canon_team(name:str)->str:
    s=unicodedata.normalize("NFKD",name).encode("ascii","ignore").decode().lower().replace("&"," and ")
    toks=[]
    for t in re.findall(r"[a-z0-9]+",s):
        t=WORD_REWRITE.get(t,t)
        if t not in NOISE_TOKENS and t not in {"olympique","stade","borussia"}:toks.append(t)
    return " ".join(toks)
def _season(x:str)->str:
    x=x.replace("-","/")
    return x if len(x.split("/")[-1])==2 else f"{x.split('/')[0]}/{x.split('/')[1][-2:]}"

# Step 1: exact source inventory only. Match listings are deleted after sanitisation.
def inventory_statsbomb(root:Path)->dict[str,Any]:
    root.mkdir(parents=True,exist_ok=True);retrieved=_iso(datetime.now(timezone.utc));meta={};safe=[];items=[]
    comp_raw=_get("data/competitions.json");(root/"competitions.tmp.json").write_bytes(comp_raw);comps=json.loads(comp_raw)
    for t in TARGETS:
        found=[x for x in comps if int(x.get("competition_id",-1))==t["competition_id"] and int(x.get("season_id",-1))==t["season_id"]]
        if len(found)!=1:raise PITViolation(f"exact competition/season inventory miss {t}")
        rel=f"data/matches/{t['competition_id']}/{t['season_id']}.json";raw=_get(rel);rows=json.loads(raw);ids=sorted(int(x["match_id"]) for x in rows)
        if len(ids)!=len(set(ids)):raise PITViolation(f"duplicate StatsBomb match_id in {rel}")
        listing_sha=hashlib.sha256(raw).hexdigest();id_sha=canonical_sha(ids)
        claim="PARTIAL_OPEN_DATA_SUBSET" if (t["competition_id"],t["season_id"])==(9,281) else "NO_COMPLETENESS_CLAIM"
        items.append({**t,"source_path":rel,"actual_match_count":len(ids),"actual_match_ids":ids,"match_id_set_sha256":id_sha,"raw_match_listing_sha256":listing_sha,"coverage_claim":claim})
        meta[rel]={"url":_url(rel),"sha256":listing_sha,"bytes":len(raw)}
        for m in rows:
            safe.append({"match_id":int(m["match_id"]),"competition_id":t["competition_id"],"season_id":t["season_id"],"competition_name":t["competition_name"],"season_name":t["season_name"],"v2_competition_id":t["v2_competition_id"],"v2_season":t["v2_season"],"match_date":m["match_date"],"home_team_id":int(m["home_team"]["home_team_id"]),"home_team_name":m["home_team"]["home_team_name"],"away_team_id":int(m["away_team"]["away_team_id"]),"away_team_name":m["away_team"]["away_team_name"]})
    (root/"competitions.tmp.json").unlink()
    _dump(root/"safe_match_index.json",sorted(safe,key=lambda x:(x["competition_id"],x["season_id"],x["match_date"],x["match_id"])))
    receipt={"schema_version":"football3-statsbomb-exact-inventory-v2","repository":SB_REPO,"exact_commit":SB_COMMIT,"retrieved_at":retrieved,"source_tier":"TIER_2_OPEN_STRUCTURED","provider_license":"StatsBomb Open Data; attribution required by exact-commit repository notice","selection_rule":"FOUR_USER_PREDECLARED_COMPETITION_SEASONS_ALL_MATCH_IDS_AS_PUBLISHED_AT_EXACT_COMMIT","no_result_or_metric_selection":True,"targets":items,"safe_match_index_sha256":_sha_file(root/"safe_match_index.json"),"raw_match_listings":meta,"raw_match_listings_retained":False}
    receipt["inventory_sha256"]=canonical_sha(receipt);_dump(root/"statsbomb_source_inventory_receipt.json",receipt);return receipt

def _map_inventory(artifact:Path,source:Path,out:Path)->tuple[dict[str,Any],list[tuple[dict[str,Any],dict[str,Any]]]]:
    inv=json.loads((source/"statsbomb_source_inventory_receipt.json").read_text());safe=json.loads((source/"safe_match_index.json").read_text())
    if inv["exact_commit"]!=SB_COMMIT or _sha_file(source/"safe_match_index.json")!=inv["safe_match_index_sha256"]:raise PITViolation("inventory receipt identity mismatch")
    dev=_read_jsonl(artifact/"dataset/development.jsonl");ev=_read_jsonl(artifact/"dataset/evaluation_features.jsonl")
    universe=[]
    for split,rows in (("development",dev),("evaluation",ev)):
        for r in rows:
            universe.append({"split":split,"fixture_id":r["fixture_id"],"competition_id":str(r["competition_id"]),"season":_season(str(r["season"])),"date":str(r.get("date") or r["cutoff"][:10]),"home":str(r["home_team"]),"away":str(r["away_team"]),"row":r})
    idx=defaultdict(list)
    for u in universe:idx[(u["competition_id"],u["season"],u["date"],_canon_team(u["home"]),_canon_team(u["away"]))].append(u)
    mapped=[];detail=[]
    for s in safe:
        key=(s["v2_competition_id"],s["v2_season"],s["match_date"],_canon_team(s["home_team_name"]),_canon_team(s["away_team_name"]))
        cand=idx.get(key,[])
        if len(cand)>1:raise PITViolation(f"ambiguous mechanical identity {key}")
        if cand:
            u=cand[0];mapped.append((u["row"],s));status="MAPPED_EVALUATION" if u["split"]=="evaluation" else "MAPPED_DEVELOPMENT_OVERLAP"
            detail.append({"match_id":s["match_id"],"source_key":list(key),"mapping_status":status,"v2_fixture_id":u["fixture_id"],"v2_split":u["split"]})
        else:
            season_present=any(u["competition_id"]==s["v2_competition_id"] and u["season"]==s["v2_season"] for u in universe)
            detail.append({"match_id":s["match_id"],"source_key":list(key),"mapping_status":"UNMAPPED_IDENTITY" if season_present else "V2_SEASON_ABSENT","v2_fixture_id":None,"v2_split":None})
    by_target=[]
    for t in inv["targets"]:
        ids=set(t["actual_match_ids"]);ds=[x for x in detail if x["match_id"] in ids]
        by_target.append({"competition_id":t["competition_id"],"season_id":t["season_id"],"v2_competition_id":t["v2_competition_id"],"v2_season":t["v2_season"],"actual_source_count":t["actual_match_count"],"mapped_evaluation":sum(x["mapping_status"]=="MAPPED_EVALUATION" for x in ds),"mapped_development_overlap":sum(x["mapping_status"]=="MAPPED_DEVELOPMENT_OVERLAP" for x in ds),"v2_season_absent":sum(x["mapping_status"]=="V2_SEASON_ABSENT" for x in ds),"unmapped_identity":sum(x["mapping_status"]=="UNMAPPED_IDENTITY" for x in ds),"mapped_source_match_ids":[x["match_id"] for x in ds if x["v2_fixture_id"]],"mapping_set_sha256":canonical_sha([x for x in ds if x["v2_fixture_id"]])})
    receipt={"schema_version":"football3-statsbomb-v2-mechanical-mapping-v2","statsbomb_exact_commit":SB_COMMIT,"inventory_sha256":inv["inventory_sha256"],"identity_rule":"competition+season+date+canonical_home+canonical_away+home_away_direction; ASCII fold; frozen token/rewrite table in source_ingest.py","result_fields_used":False,"prediction_error_used":False,"metric_selection_used":False,"targets":by_target,"rows":detail}
    receipt["mapping_sha256"]=canonical_sha(receipt);_dump(out/"statsbomb_v2_mapping_receipt.json",receipt)
    # Any source match in a V2-present season must map mechanically; otherwise fail closed for identity repair.
    bad=[x for x in by_target if x["unmapped_identity"]]
    if bad:raise PITViolation(f"mechanical mapping has identity misses: {bad}")
    return receipt,mapped

def _mins(text:str|None,default:float)->float:
    if not text:return default
    try:a,b=text.split(":",1);return float(a)+float(b)/60
    except Exception:return default

def _history(events:list[dict[str,Any]],lineups:list[dict[str,Any]],known_at:str,home_tid:str,away_tid:str):
    duration=max(90.0,max((float(e.get("minute",0))+float(e.get("second",0))/60 for e in events),default=90.0));vals=defaultdict(lambda:defaultdict(float));xg=defaultdict(float)
    for e in events:
        pid=str((e.get("player") or {}).get("id",""));tid=str((e.get("team") or {}).get("id",""));typ=str((e.get("type") or {}).get("name",""))
        if not pid:continue
        v=vals[pid]
        if typ=="Shot":
            sh=e.get("shot") or {};q=float(sh.get("statsbomb_xg",0) or 0);goal=str((sh.get("outcome") or {}).get("name",""))=="Goal";v["shot_generation"]+=.15+.35*q;v["finishing"]+=.35*(float(goal)-q);v["current_form"]+=.2*q+.2*float(goal);xg[tid]+=q
        elif typ=="Pass":
            pa=e.get("pass") or {};v["passing_progression"]+=.025 if not pa.get("outcome") else -.01;v["chance_creation"]+=.18*bool(pa.get("shot_assist"))+.32*bool(pa.get("goal_assist"))
        elif typ=="Carry":v["carrying_progression"]+=.04
        elif typ=="Pressure":v["pressing"]+=.025;v["off_ball_contribution"]+=.018
        elif typ in {"Interception","Ball Recovery"}:v["tackling_interception"]+=.08;v["off_ball_contribution"]+=.05
        elif typ in {"Block","Clearance"}:v["defensive_position_protection"]+=.06
        elif typ in {"Miscontrol","Dispossessed"}:v["possession_retention_risk"]-=.08
        elif typ=="Goal Keeper":v["goalkeeper_shot_stopping"]+=.05
        if typ in {"Shot","Pass","Carry"}:v["on_ball_contribution"]+=.02
    rows=[];usage=defaultdict(list);starters=defaultdict(list)
    for team in lineups:
        tid=str(team["team_id"])
        for p in team.get("lineup") or []:
            pid=str(p["player_id"]);total=0.;start=False;role="UNK"
            for z in p.get("positions") or []:
                a,b=_mins(z.get("from"),0),_mins(z.get("to"),duration);total+=max(0,min(duration,b)-min(duration,a));start|=z.get("start_reason")=="Starting XI" and a<.01;name=str(z.get("position","")).lower()
                if "goalkeeper" in name:role="GK"
                elif "back" in name or "defensive" in name:role="DEF"
                elif "midfield" in name:role="MID"
                elif "forward" in name or "wing" in name:role="FWD"
            total=min(total,duration);usage[tid].append({"player_id":pid,"started":start,"appeared":total>0,"minutes":total,"role":role,"known_at":known_at})
            if start:starters[tid].append(pid)
            if total>0:rows.append({"player_id":pid,"team_id":tid,"league_id":"statsbomb","role":role,"known_at":known_at,"minutes_exposure":total,"possession_opportunity":1.,"values":dict(vals[pid])})
    return rows,usage,{"known_at":known_at,"minutes":duration,"impact":float(xg[home_tid]-xg[away_tid]),"home_player_ids":starters[home_tid],"away_player_ids":starters[away_tid]}

def _expected(tid:str,usage:dict[str,list[dict[str,Any]]],cutoff:str)->list[dict[str,Any]]:
    matches=usage.get(tid,[])[-8:]
    if not matches:return []
    q=defaultdict(lambda:{"s":0.,"a":0.,"m":0.,"w":0.,"n":0,"role":"UNK","known_at":""});denom=0.
    for age,rec in enumerate(reversed(matches)):
        w=.82**age;denom+=w
        for p in rec["players"]:
            x=q[p["player_id"]];x["s"]+=w*p["started"];x["a"]+=w*p["appeared"];x["m"]+=w*p["minutes"];x["w"]+=w;x["n"]+=1;x["known_at"]=max(x["known_at"],p["known_at"]);x["role"]=p["role"] if p["role"]!="UNK" else x["role"]
    out=[]
    for pid,x in q.items():
        if _dt(x["known_at"],"known_at")>=_dt(cutoff,"cutoff"):raise PITViolation("future expected-lineup row")
        sp=max(0,min(1,x["s"]/denom));ap=max(0,min(1,x["a"]/denom));out.append({"player_id":pid,"starting_probability":sp,"availability_probability":ap,"expected_minutes_distribution":{"mean":x["m"]/max(x["w"],1e-9)},"injury_status":"UNKNOWN","suspension_status":"UNKNOWN","return_status":"UNKNOWN","rotation_probability":1-sp,"role_distribution":{x["role"]:1.},"replacement_quality":0.,"uncertainty":min(1,1/math.sqrt(x["n"])),"known_at":x["known_at"]})
    return sorted(out,key=lambda x:(x["starting_probability"]*x["availability_probability"],x["player_id"]),reverse=True)

def _fit_dev(artifact:Path,repo:Path):
    from match_context import ScheduleTracker,fit_schedule_coefficients
    from test_translator import tune_draw_threshold
    from v2_translator_integration import fit_independent_head
    import importlib
    dev=_read_jsonl(artifact/"dataset/development.jsonl");lock=json.loads((artifact/"locks/v2_lock.json").read_text());p=repo/"football-data/new_engine_v2_joint_score";sys.path.insert(0,str(p));eng=importlib.import_module("engine");sys.path.pop(0);state=eng.EngineState(eng.Parameters(**lock["parameters"]));tr=ScheduleTracker();pending=[];sf=[];hf=[]
    for r in sorted(dev,key=lambda x:(x["cutoff"],x["fixture_id"])):
        co=eng._dt(r["cutoff"]);ready=[x for x in pending if eng._dt(x["available_at"])<=co];pending=[x for x in pending if eng._dt(x["available_at"])>co]
        for x in sorted(ready,key=lambda z:(z["cutoff"],z["fixture_id"])):
            f=eng.Fixture(x["fixture_id"],x["competition_id"],x["season"],eng._dt(x["cutoff"]),x["home_team_id"],x["away_team_id"],x["round_index"]);state.apply_batch([f],{f.fixture_id:(x["home_goals"],x["away_goals"])})
        f=eng.Fixture(r["fixture_id"],r["competition_id"],r["season"],co,r["home_team_id"],r["away_team_id"],r["round_index"]);b=state.predict_features(f);z=tr.features(r["home_team_id"],r["away_team_id"],r["cutoff"],r.get("round_index"));sf.append({"x":z.vector(),"base_mu_home":b["mu_home"],"base_mu_away":b["mu_away"],"home_goals":r["home_goals"],"away_goals":r["away_goals"]});cls=0 if r["home_goals"]>r["away_goals"] else 1 if r["home_goals"]==r["away_goals"] else 2;hf.append({"mu_home":b["mu_home"],"mu_away":b["mu_away"],"context_delta":0.,"uncertainty":b["uncertainty"],"target_class":cls});tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"]);pending.append({**r,"available_at":r["result_available_at"]})
    w=fit_independent_head(hf);return dev,lock,fit_schedule_coefficients(sf),w,tune_draw_threshold(hf,w)
def _pack(meta,layer,i):
    m=i["final_matrix"];p=i["final_1x2"];return {**meta,"layer":layer,"matrix":m,"one_x_two":p,"prediction_sha256":canonical_sha({"matrix":m,"one_x_two":p})}
def _clone(x,layer):
    y={**x,"layer":layer};y["prediction_sha256"]=canonical_sha({"matrix":y["matrix"],"one_x_two":y["one_x_two"]});return y

def predict(artifact:Path,source:Path,repo:Path,out:Path)->dict[str,Any]:
    from football_context_translator import LayerAdjustment,build_plan,team_state
    from lineup_scenarios import LineupScenario,build_lineup_scenarios
    from match_context import ScheduleTracker,schedule_adjustment
    from player_strength import estimate_player_vectors
    from test_translator import matrix_mean
    from v2_translator_integration import integrate_plan
    out.mkdir(parents=True,exist_ok=True);am=json.loads((artifact/"artifact_manifest.json").read_text())
    if am["run_id"]!=33348991436 or am["prediction_sha256"]!="92dc38866e6e46b167ed6bf0bcfc6f6e0e8b85e57e68cb3a571d3c44fc9461a7":raise PITViolation("sealed V2 identity mismatch")
    mapping,mapped=_map_inventory(artifact,source,out);targets=[(r,s) for r,s in mapped if str(r["season"])=="2023/24" and str(r["competition_id"])=="GER1"]
    if not targets:raise PITViolation("no mapped evaluation target rows")
    inv=json.loads((source/"statsbomb_source_inventory_receipt.json").read_text());dev,lock,beta,weights,threshold=_fit_dev(artifact,repo);pred_by={x["fixture_id"]:x for x in _read_jsonl(artifact/"replay/predictions.jsonl")};eval_rows=_read_jsonl(artifact/"dataset/evaluation_features.jsonl")
    # Layer-7 tracker receives the complete V2 schedule, independent of StatsBomb event coverage.
    tr=ScheduleTracker()
    for r in sorted(dev,key=lambda x:(x["cutoff"],x["fixture_id"])):tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"])
    target_ids={r["fixture_id"] for r,_ in targets};sched={}
    for r in sorted(eval_rows,key=lambda x:(x["cutoff"],x["fixture_id"])):
        if r["fixture_id"] in target_ids:
            sf=tr.features(r["home_team_id"],r["away_team_id"],r["cutoff"],r.get("round_index"));sched[r["fixture_id"]]=schedule_adjustment(sf,beta)
        tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"])
    groups=defaultdict(list)
    for pair in targets:groups[pair[0]["cutoff"]].append(pair)
    events=[];segments=[];usage=defaultdict(list);pending=[];ledger=[];preds=[];evidence={};state=hashlib.sha256(b"football3-pit-v2").hexdigest();cov={LAYERS[1]:0,LAYERS[2]:0}
    for cutoff in sorted(groups,key=lambda x:_dt(x,"cutoff")):
        co=_dt(cutoff,"cutoff");ready=[x for x in pending if _dt(x["release_at"],"release_at")<co];pending=[x for x in pending if _dt(x["release_at"],"release_at")>=co]
        for x in sorted(ready,key=lambda z:(z["release_at"],z["match_id"])):
            er=f"data/events/{x['match_id']}.json";lr=f"data/lineups/{x['match_id']}.json";erb,lrb=_get(er),_get(lr);evidence[er]={"sha256":hashlib.sha256(erb).hexdigest(),"bytes":len(erb)};evidence[lr]={"sha256":hashlib.sha256(lrb).hexdigest(),"bytes":len(lrb)};a,u,s=_history(json.loads(erb),json.loads(lrb),x["release_at"],x["home_tid"],x["away_tid"]);events+=a;segments.append(s)
            for tid,players in u.items():usage[tid].append({"players":players,"known_at":x["release_at"],"match_id":x["match_id"]})
            state=hashlib.sha256((state+str(x["match_id"])+evidence[er]["sha256"]+evidence[lr]["sha256"]).encode()).hexdigest();ledger.append({"event":"UPDATE_RELEASE","match_id":x["match_id"],"release_at":x["release_at"],"before_cutoff":cutoff,"state_sha256":state})
        batch=sorted(groups[cutoff],key=lambda x:x[0]["fixture_id"]);rows=[]
        for r,sb in batch:
            pr=pred_by[r["fixture_id"]];raw={"home":pr["v2_joint"]["p_home"],"draw":pr["v2_joint"]["p_draw"],"away":pr["v2_joint"]["p_away"]};meta={"fixture_id":r["fixture_id"],"statsbomb_match_id":sb["match_id"],"cutoff":r["cutoff"],"competition_id":r["competition_id"],"season":r["season"],"cold_start_bucket":pr["shared_cold_start_bucket"],"coverage_grade":"TEAM_ONLY","weak_side":"home" if raw["home"]<raw["away"] else "away"};bh,ba=matrix_mean(pr["v2_joint_off"]["score_matrix"]);blocked=LayerAdjustment("BLOCKED_DATA",0,0,.4,None);neutral=LayerAdjustment("CONTRACT_ONLY",0,0,.15,None);hs=team_state(r["home_team_id"],0,0,float(pr.get("shared_home_prior_appearances",0)),.5);ats=team_state(r["away_team_id"],0,0,float(pr.get("shared_away_prior_appearances",0)),.5);unknown=build_lineup_scenarios(None,None,cutoff=r["cutoff"])
            def run(sc,vectors,context,grade,status):
                plan=build_plan(match_id=r["fixture_id"],cutoff=r["cutoff"],base_mu_home=bh,base_mu_away=ba,home_team_state=hs,away_team_state=ats,scenarios=sc,player_vectors=vectors,coach_tactical=blocked,match_context=context,process_hazard=blocked,provenance_manifest_sha256=inv["inventory_sha256"],player_status=status,coverage_grade=grade);return integrate_plan(plan,lock,repo_root=repo,head_weights=weights)
            rows.append(_pack(meta,LAYERS[0],run(unknown,None,neutral,"TEAM_ONLY","BLOCKED_DATA")));ht,at=str(sb["home_team_id"]),str(sb["away_team_id"]);pe=[e for e in events if e["team_id"] in {ht,at}];vectors=estimate_player_vectors(pe,segments,as_of=r["cutoff"]) if pe else {};he,ae=_expected(ht,usage,r["cutoff"]),_expected(at,usage,r["cutoff"]);hr=[x["player_id"] for x in he[:18]];ar=[x["player_id"] for x in ae[:18]]
            if vectors and hr and ar:
                known=max(x["known_at"] for x in he[:18]+ae[:18]);payload={"home":hr,"away":ar,"known_at":known};sc1=[LineupScenario("cap_"+canonical_sha(payload)[:16],"EXPECTED_LINEUP",1.,hr,ar,known,1.,canonical_sha(payload))];grade,status="FULL_EVENT","IMPLEMENTED";cov[LAYERS[1]]+=1
            else:sc1,grade,status=unknown,"TEAM_ONLY","BLOCKED_DATA"
            rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[1],run(sc1,vectors or None,neutral,grade,status)))
            if vectors and len(he)>=11 and len(ae)>=11:sc2=build_lineup_scenarios(he,ae,cutoff=r["cutoff"]);cov[LAYERS[2]]+=1
            else:sc2=sc1
            rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[2],run(sc2,vectors or None,neutral,grade,status)))
            for layer in LAYERS[3:7]:rows.append(_clone(rows[-1],layer))
            dh,da,sha=sched[r["fixture_id"]];rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[7],run(sc2,vectors or None,LayerAdjustment("IMPLEMENTED",dh,da,.15,sha),grade,status)));rows.append(_clone(rows[-1],LAYERS[8]));rows.append(_clone(rows[-1],LAYERS[9]))
        bsha=canonical_sha([{"fixture_id":x["fixture_id"],"layer":x["layer"],"sha":x["prediction_sha256"]} for x in rows]);ledger.append({"event":"PREDICT_BATCH_FREEZE","cutoff":cutoff,"match_ids":[s["match_id"] for _,s in batch],"state_sha256":state,"prediction_batch_sha256":bsha});preds+=rows
        for r,sb in batch:
            release=_iso(_dt(r["cutoff"],"cutoff")+timedelta(hours=RELEASE_HOURS));pending.append({"match_id":sb["match_id"],"release_at":release,"home_tid":str(sb["home_team_id"]),"away_tid":str(sb["away_team_id"])});ledger.append({"event":"ENQUEUE_AFTER_FREEZE","cutoff":cutoff,"match_id":sb["match_id"],"release_at":release,"prediction_batch_sha256":bsha})
    n=len(targets)
    if len(preds)!=n*len(LAYERS):raise PITViolation("prediction cardinality mismatch")
    pf=out/"statsbomb_round2_predictions.jsonl";lf=out/"statsbomb_pit_ledger.jsonl";pf.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in preds));lf.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in ledger));_dump(out/"statsbomb_released_evidence_receipt.json",{"schema_version":"football3-statsbomb-released-evidence-v1","exact_commit":SB_COMMIT,"files":{k:evidence[k] for k in sorted(evidence)},"evidence_set_sha256":canonical_sha(evidence),"fetch_rule":"ONLY_AFTER_TARGET_BATCH_PREDICTION_SHA_FREEZE_AND_RELEASE_AT_LT_NEXT_CUTOFF"})
    pm={"schema_version":"football3-translator-round2-predict-v2","research_only":True,"scientific_claim":"PARTIAL_OPEN_DATA_SUBSET_ENGINEERING_ONLY","formal_weight":0,"statsbomb_exact_commit":SB_COMMIT,"inventory_sha256":inv["inventory_sha256"],"mapping_sha256":mapping["mapping_sha256"],"n":n,"common_cohort":"GER1 2023/24 MECHANICAL INTERSECTION ONLY","source_v2_run_id":am["run_id"],"source_v2_head":am["head"],"source_prediction_sha256":am["prediction_sha256"],"prediction_sha256":_sha_file(pf),"pit_ledger_sha256":_sha_file(lf),"evaluation_label_vault_access_in_predict_process":"NONE","development_labels_used_only_for_existing_dev_fit":True,"availability_basis":"SIMULATED_POST_MATCH_RELEASE_PLUS_6H","draw_threshold_dev_only":threshold,"coverage_counts":cov,"layer7_schedule_source":"COMPLETE_V2_HISTORY_SCHEDULE_NOT_STATSBOMB_SUBSET"};_dump(out/"statsbomb_round2_predictor_manifest.json",pm);return pm

def _ledger_gate(rows:list[dict[str,Any]],n:int)->dict[str,Any]:
    frozen=set();enqueued=set();batches=0
    for r in rows:
        if r["event"]=="PREDICT_BATCH_FREEZE":
            s=set(map(int,r["match_ids"]));
            if s&frozen:raise PITViolation("duplicate freeze")
            frozen|=s;batches+=1
        elif r["event"]=="ENQUEUE_AFTER_FREEZE":
            mid=int(r["match_id"])
            if mid not in frozen or _dt(r["release_at"],"release_at")<=_dt(r["cutoff"],"cutoff"):raise PITViolation("enqueue-before-freeze")
            enqueued.add(mid)
        elif r["event"]=="UPDATE_RELEASE":
            mid=int(r["match_id"])
            if mid not in enqueued or _dt(r["release_at"],"release_at")>=_dt(r["before_cutoff"],"cutoff"):raise PITViolation("release PIT violation")
        else:raise PITViolation("unknown ledger event")
    if len(frozen)!=n or len(enqueued)!=n:raise PITViolation("ledger cardinality mismatch")
    return {"passed":True,"frozen_matches":len(frozen),"freeze_batches":batches}

def score(artifact:Path,out:Path)->dict[str,Any]:
    pm=json.loads((out/"statsbomb_round2_predictor_manifest.json").read_text());pf=out/"statsbomb_round2_predictions.jsonl";lf=out/"statsbomb_pit_ledger.jsonl"
    if _sha_file(pf)!=pm["prediction_sha256"] or _sha_file(lf)!=pm["pit_ledger_sha256"] or pm["evaluation_label_vault_access_in_predict_process"]!="NONE":raise PITViolation("predictor/scorer separation SHA gate")
    pit=_ledger_gate(_read_jsonl(lf),pm["n"]);rows=_read_jsonl(pf);by={k:[] for k in LAYERS}
    for r in rows:by[r["layer"]].append(r)
    ids=[x["fixture_id"] for x in sorted(by[LAYERS[0]],key=lambda x:(x["cutoff"],x["fixture_id"]))]
    for k in LAYERS:
        by[k].sort(key=lambda x:(x["cutoff"],x["fixture_id"]))
        if [x["fixture_id"] for x in by[k]]!=ids:raise PITViolation("layer pairing mismatch")
    # Scorer opens labels only to verify target identities are scoreable; no promotion metric is computed for this partial subset.
    labels={x["fixture_id"] for x in _read_jsonl(artifact/"dataset/evaluation_label_vault.jsonl")}
    if not set(ids)<=labels:raise PITViolation("scorer label identity mismatch")
    eng={LAYERS[0]:"ENGINEERING/PIT_PASS",LAYERS[1]:"ENGINEERING/PIT_PASS" if pm["coverage_counts"][LAYERS[1]] else "INSUFFICIENT_SAMPLE",LAYERS[2]:"ENGINEERING/PIT_PASS" if pm["coverage_counts"][LAYERS[2]] else "INSUFFICIENT_SAMPLE",LAYERS[3]:"INSUFFICIENT_SAMPLE",LAYERS[4]:"INSUFFICIENT_SAMPLE",LAYERS[5]:"INSUFFICIENT_SAMPLE",LAYERS[6]:"INSUFFICIENT_SAMPLE",LAYERS[7]:"REJECTED_ABLATION",LAYERS[8]:"INSUFFICIENT_SAMPLE",LAYERS[9]:"INSUFFICIENT_SAMPLE"}
    report={"schema_version":"football3-translator-round2-validation-v2","research_only":True,"formal_promotion_eligible":False,"formal_weight":0,"scientific_claim":"PARTIAL_OPEN_DATA_SUBSET_ENGINEERING_ONLY","model_improvement_claim":False,"statsbomb_exact_commit":SB_COMMIT,"inventory_sha256":pm["inventory_sha256"],"mapping_sha256":pm["mapping_sha256"],"n":pm["n"],"common_cohort":pm["common_cohort"],"prediction_sha256":pm["prediction_sha256"],"pit_ledger_sha256":pm["pit_ledger_sha256"],"pit_ledger_gate":pit,"predictor_scorer_separation":{"passed":True,"evaluation_label_vault_access_in_predict_process":"NONE","scorer_after_prediction_sha_freeze":True},"engineering_layer_status":eng,"coverage_counts":pm["coverage_counts"],"sample_interpretation":"INSUFFICIENT_SAMPLE_FOR_SCIENTIFIC_ABLATION_OR_PROMOTION","layer7_lock":{"status":"REJECTED_ABLATION","formal_revival_allowed":False,"rerun_for_promotion":False,"schedule_tracker":"COMPLETE_V2_HISTORY"},"blocked_or_insufficient":{"3_confirmed_lineup":"no reliable exact pre-match target-XI publication timestamp","4_bench_substitution":"no preregistered PIT effect estimate","5_coach_regime":"insufficient nonselected mapped sample","6_tactical_matchup":"insufficient nonselected mapped sample","8_referee_competition_environment":"target publication timestamp not admitted","9_pre_match_process_hazard":"insufficient preregistered PIT effect evidence"},"protected_v2_core_modified":False,"frozen_contracts_modified":False,"current_modified":False,"main_modified":False,"source_raw_in_artifact":False};_dump(out/"statsbomb_round2_validation_report.json",report);return report

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--statsbomb-inventory");ap.add_argument("--statsbomb-predict",action="store_true");ap.add_argument("--statsbomb-score",action="store_true");ap.add_argument("--artifact-dir");ap.add_argument("--source-dir");ap.add_argument("--repo-root",default=".");ap.add_argument("--out-dir",default="translator_out");a=ap.parse_args();modes=sum(bool(x) for x in (a.statsbomb_inventory,a.statsbomb_predict,a.statsbomb_score))
    if modes!=1:raise SystemExit("choose exactly one mode")
    if a.statsbomb_inventory:
        r=inventory_statsbomb(Path(a.statsbomb_inventory));print(json.dumps({"commit":r["exact_commit"],"inventory_sha256":r["inventory_sha256"],"counts":[{"competition_id":x["competition_id"],"season_id":x["season_id"],"count":x["actual_match_count"]} for x in r["targets"]]}));return 0
    if not a.artifact_dir:raise SystemExit("--artifact-dir required")
    if a.statsbomb_predict:
        if not a.source_dir:raise SystemExit("--source-dir required")
        r=predict(Path(a.artifact_dir),Path(a.source_dir),Path(a.repo_root).resolve(),Path(a.out_dir));print(json.dumps({"n":r["n"],"prediction_sha256":r["prediction_sha256"],"coverage":r["coverage_counts"]}));return 0
    r=score(Path(a.artifact_dir),Path(a.out_dir));print(json.dumps({"n":r["n"],"engineering_layer_status":r["engineering_layer_status"],"pit":r["pit_ledger_gate"]}));return 0

if __name__=="__main__":raise SystemExit(main())
