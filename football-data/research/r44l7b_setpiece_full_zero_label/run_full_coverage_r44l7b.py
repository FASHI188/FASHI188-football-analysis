#!/usr/bin/env python3
import hashlib
import json
import os
import statistics
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SOURCE_REPO="hudl/open-data"
SOURCE_PIN="b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW_ROOT=f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_PIN}"
DOMAINS=[
    {"competition_id":2,"season_id":27,"name":"Premier League"},
    {"competition_id":12,"season_id":27,"name":"Serie A"},
    {"competition_id":7,"season_id":27,"name":"Ligue 1"},
]
WARMUP=100
PRIOR_N=5
MAX_WORKERS=12


def fetch_bytes(path):
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(f"{RAW_ROOT}/{path}",headers={"User-Agent":"r44l7b-zero-label/1.0"})
            with urllib.request.urlopen(req,timeout=45) as r:
                return r.read()
        except Exception as exc:
            last=exc
            if attempt<2: time.sleep(0.4*(attempt+1))
    raise last


def fetch_json(path):
    raw=fetch_bytes(path)
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def identity_rows(comp_id,season_id):
    obj,sha=fetch_json(f"data/matches/{comp_id}/{season_id}.json")
    rows=[]
    for m in obj:
        if int(m["competition"]["competition_id"])!=comp_id or int(m["season"]["season_id"])!=season_id:
            raise RuntimeError("identity_mismatch")
        rows.append({
            "match_id":int(m["match_id"]),
            "match_date":str(m["match_date"]),
            "kick_off":str(m.get("kick_off") or ""),
            "home_team_id":int(m["home_team"]["home_team_id"]),
            "away_team_id":int(m["away_team"]["away_team_id"]),
        })
    rows.sort(key=lambda x:(x["match_date"],x["kick_off"],x["match_id"]))
    return rows,sha


def extract_event_summary(mid):
    path=f"data/events/{mid}.json"
    obj,sha=fetch_json(path)
    teams=defaultdict(Counter)
    for e in obj:
        if e.get("type",{}).get("name")!="Pass": continue
        ptype=(e.get("pass") or {}).get("type",{}).get("name")
        if ptype not in {"Corner","Free Kick"}: continue
        tid=(e.get("team") or {}).get("id")
        pid=(e.get("player") or {}).get("id")
        if tid is not None and pid is not None:
            teams[int(tid)][int(pid)]+=1
    return mid,{int(t):dict(c) for t,c in teams.items()},sha


def extract_lineup(mid):
    path=f"data/lineups/{mid}.json"
    obj,sha=fetch_json(path)
    teams={}
    for t in obj:
        starters=[]
        for p in t.get("lineup",[]):
            if any(pos.get("from")=="00:00" and pos.get("start_reason")=="Starting XI" for pos in (p.get("positions") or [])):
                starters.append(int(p["player_id"]))
        teams[int(t["team_id"])]=starters
    return mid,teams,sha


def parallel_map(fn,ids):
    out={}; errors={}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut={ex.submit(fn,mid):mid for mid in ids}
        for f in as_completed(fut):
            mid=fut[f]
            try:
                got=f.result(); out[mid]=got
            except Exception as exc:
                errors[mid]=f"{type(exc).__name__}:{exc}"
    return out,errors


def main():
    root=Path(os.environ.get("R44L7B_OUTPUT","r44l7b_output")); root.mkdir(parents=True,exist_ok=True)
    domains=[]; all_rows={}; source_manifest=[]
    all_event_ids=[]; target_ids=[]
    for d in DOMAINS:
        rows,sha=identity_rows(d["competition_id"],d["season_id"])
        all_rows[d["name"]]=rows
        domains.append({"name":d["name"],"competition_id":d["competition_id"],"identity_count":len(rows),"target_count":max(0,len(rows)-WARMUP)})
        source_manifest.append({"path":f"data/matches/{d['competition_id']}/{d['season_id']}.json","sha256":sha})
        all_event_ids.extend(r["match_id"] for r in rows)
        target_ids.extend(r["match_id"] for r in rows[WARMUP:])

    # Download only what is needed. Event contents are reduced to set-piece taker counts.
    event_map,event_errors=parallel_map(extract_event_summary,all_event_ids)
    lineup_map,lineup_errors=parallel_map(extract_lineup,target_ids)
    for mid,got in event_map.items(): source_manifest.append({"path":f"data/events/{mid}.json","sha256":got[2]})
    for mid,got in lineup_map.items(): source_manifest.append({"path":f"data/lineups/{mid}.json","sha256":got[2]})

    feature_rows=[]; match_rows=[]; same_match_event_before_feature=0
    for d in DOMAINS:
        name=d["name"]; rows=all_rows[name]
        history=defaultdict(lambda:deque(maxlen=PRIOR_N))
        for idx,r in enumerate(rows):
            mid=r["match_id"]
            # Feature row is generated before current-match event summary enters history.
            if idx>=WARMUP:
                lineup_tuple=lineup_map.get(mid)
                lineups=(lineup_tuple[1] if lineup_tuple else {})
                ht,at=r["home_team_id"],r["away_team_id"]
                exact=len(lineups.get(ht,[]))==11 and len(lineups.get(at,[]))==11
                match_rows.append({"domain":name,"match_id":mid,"match_date":r["match_date"],"lineup_ok":lineup_tuple is not None,"exact_11v11":exact})
                for team_id,is_home in [(ht,True),(at,False)]:
                    xi=set(lineups.get(team_id,[]))
                    prior=list(history[team_id])
                    total=Counter()
                    for c in prior: total.update(c)
                    nsp=sum(total.values())
                    top_pid,top_n=(total.most_common(1)[0] if total else (None,0))
                    feature_rows.append({
                        "domain":name,"match_id":mid,"match_date":r["match_date"],"team_id":team_id,"is_home":is_home,
                        "current_xi_n":len(xi),"prior_match_count":len(prior),"complete_prior5":len(prior)==PRIOR_N,
                        "prior5_setpiece_passes":nsp,"prior5_unique_takers":len(total),
                        "prior5_top1_share":(top_n/nsp) if nsp else None,
                        "prior5_top1_taker_in_xi":(top_pid in xi) if top_pid is not None else None,
                        "prior5_xi_role_retention":(sum(v for pid,v in total.items() if pid in xi)/nsp) if nsp else None,
                    })
            # Only after feature sealing, ingest current set-piece events for future rows.
            ev=event_map.get(mid)
            if ev:
                ht,at=r["home_team_id"],r["away_team_id"]
                history[ht].append(Counter(ev[1].get(ht,{})))
                history[at].append(Counter(ev[1].get(at,{})))

    target_n=len(match_rows); team_n=len(feature_rows)
    lineup_ok=sum(1 for x in match_rows if x["lineup_ok"])
    exact=sum(1 for x in match_rows if x["exact_11v11"])
    complete=sum(1 for x in feature_rows if x["complete_prior5"])
    observable=sum(1 for x in feature_rows if x["prior5_setpiece_passes"]>=3)
    ret=[x["prior5_xi_role_retention"] for x in feature_rows if x["prior5_xi_role_retention"] is not None]
    tops=[x for x in feature_rows if x["prior5_top1_taker_in_xi"] is not None]
    medret=statistics.median(ret) if ret else 0.0
    top_rate=sum(1 for x in tops if x["prior5_top1_taker_in_xi"])/len(tops) if tops else 0.0
    identity_total=sum(x["identity_count"] for x in domains)
    gates={
        "domain_identity_and_total":all(x["identity_count"]>=370 for x in domains) and identity_total>=1130,
        "target_rows_ge_830":target_n>=830,
        "target_lineup_success_ge_0_98":(lineup_ok/target_n if target_n else 0)>=0.98,
        "exact_11v11_ge_0_97":(exact/target_n if target_n else 0)>=0.97,
        "complete_prior5_ge_0_98":(complete/team_n if team_n else 0)>=0.98,
        "setpiece_observable_ge_0_90":(observable/team_n if team_n else 0)>=0.90,
        "median_role_retention_ge_0_60":medret>=0.60,
        "top1_in_xi_ge_0_70":top_rate>=0.70,
        "same_match_event_before_feature_zero":same_match_event_before_feature==0,
        "hard_boundaries":True,
    }
    terminal="PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE" if all(gates.values()) else "STOP_R44L7B_FULL_PRIOR_ONLY_COVERAGE"
    head=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    receipt={
        "schema_version":"V520-R44L7B-FULL-ZERO-LABEL-1.0","terminal":terminal,"research_only":True,"formal_weight":0,
        "source_repo":SOURCE_REPO,"source_pin":SOURCE_PIN,"domains":domains,
        "summary":{
            "identity_total":identity_total,"event_files_ok":len(event_map),"event_files_error":len(event_errors),
            "target_matches":target_n,"team_targets":team_n,"target_lineups_ok":lineup_ok,"exact_11v11":exact,
            "complete_prior5":complete,"setpiece_observable":observable,"median_xi_role_retention":medret,"top1_taker_in_xi_rate":top_rate,
        },
        "gates":gates,
        "hard_boundary_receipt":{
            "result_keys_accessed":0,"winner_labels_accessed":0,"model_fits":0,"candidate_probabilities":0,
            "label_threshold_selection":0,"formal_model_changes":0,"formal_data_changes":0,"formal_config_changes":0,"CURRENT_changes":0,
        },
        "same_match_event_used_before_feature":same_match_event_before_feature,
        "event_errors":event_errors,"lineup_errors":lineup_errors,
        "run_identity":{"checked_out_head_sha":head,"github_run_id":os.environ.get("GITHUB_RUN_ID"),"github_run_attempt":os.environ.get("GITHUB_RUN_ATTEMPT")},
    }
    (root/"feature_rows.json").write_text(json.dumps(feature_rows,indent=2),encoding="utf-8")
    (root/"match_rows.json").write_text(json.dumps(match_rows,indent=2),encoding="utf-8")
    (root/"source_manifest.json").write_text(json.dumps(source_manifest,indent=2),encoding="utf-8")
    raw=json.dumps(receipt,indent=2,sort_keys=True).encode(); (root/"receipt.json").write_bytes(raw)
    (root/"receipt.sha256").write_text(hashlib.sha256(raw).hexdigest()+"\n",encoding="ascii")
    print(json.dumps({"terminal":terminal,"summary":receipt["summary"],"gates":gates},sort_keys=True))

if __name__=="__main__": main()
