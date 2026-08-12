#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, os, urllib.request
from collections import Counter
from pathlib import Path

SOURCE_COMMIT='b0bc9f22dd77c206ddedc1d742893b3bbe64baec'
RAW=f'https://raw.githubusercontent.com/hudl/open-data/{SOURCE_COMMIT}/data'
COMPETITION_ID=7
SEASONS={'2015/2016':27,'2021/2022':108,'2022/2023':235}
ALLOWED={'match_id','match_date','kick_off','competition','season','home_team','away_team'}
OUT=Path(os.environ.get('R44L5_OUT','r44l5_zero_label_output')); OUT.mkdir(parents=True,exist_ok=True)

def fetch(url:str)->bytes:
    req=urllib.request.Request(url,headers={'User-Agent':'r44l5-zero-label/1.0'})
    with urllib.request.urlopen(req,timeout=180) as r:return r.read()

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

def zero(v):
    try:return all(abs(float(x))<1e-12 for x in str(v or '').split(':'))
    except:return False

def restrict_matches(data:bytes,season_id:int):
    src=json.loads(data.decode('utf-8')); rows=[]; errors=[]
    for obj in src:
        r={k:obj.get(k) for k in ALLOWED}
        cid=int((r.get('competition') or {}).get('competition_id',-1)); sid=int((r.get('season') or {}).get('season_id',-1))
        if cid!=COMPETITION_ID or sid!=season_id: errors.append([r.get('match_id'),cid,sid])
        rows.append({'match_id':int(r['match_id']),'match_date':str(r.get('match_date') or ''),'kick_off':str(r.get('kick_off') or ''),'home_team_id':int((r.get('home_team') or {}).get('home_team_id',-1)),'away_team_id':int((r.get('away_team') or {}).get('away_team_id',-1))})
    return rows,errors

def lineup_audit(mid:int):
    try:
        b=fetch(f'{RAW}/lineups/{mid}.json'); teams=json.loads(b.decode('utf-8'))
    except Exception as e:return {'match_id':mid,'ok':False,'error':type(e).__name__}
    counts=[]
    for team in teams:
        n=0
        for p in team.get('lineup',[]):
            if any(zero(x.get('from')) for x in (p.get('positions') or [])): n+=1
        counts.append(n)
    return {'match_id':mid,'ok':True,'sha256':sha(b),'team_count':len(teams),'starter_counts':counts}

def event_audit(mid:int):
    try:
        b=fetch(f'{RAW}/events/{mid}.json'); ev=json.loads(b.decode('utf-8'))
    except Exception as e:return {'match_id':mid,'ok':False,'error':type(e).__name__}
    shots={str(x.get('id')) for x in ev if str((x.get('type') or {}).get('name'))=='Shot' and isinstance((x.get('shot') or {}).get('statsbomb_xg'),(int,float))}
    links=0
    for x in ev:
        if str((x.get('type') or {}).get('name'))!='Pass': continue
        sid=str((x.get('pass') or {}).get('assisted_shot_id') or '')
        if sid and sid in shots: links+=1
    return {'match_id':mid,'ok':True,'sha256':sha(b),'xg_shots':len(shots),'linked_shot_assists':links}

def sample(ids,n=15):return sorted(ids,key=lambda m:hashlib.sha256(f'R44L5_SCHEMA_20260813|{m}'.encode()).hexdigest())[:n]

def main():
    rows_by={}; ids=[]; identity_errors=[]; ledger=[]
    for name,sid in SEASONS.items():
        url=f'{RAW}/matches/{COMPETITION_ID}/{sid}.json'; b=fetch(url); rows,errs=restrict_matches(b,sid)
        rows_by[name]=rows; ids.extend(x['match_id'] for x in rows); identity_errors.extend(errs)
        ledger.append({'season':name,'season_id':sid,'url':url,'sha256':sha(b),'bytes':len(b),'identity_rows':len(rows)})
    dup=[m for m,c in Counter(ids).items() if c!=1]
    lineups=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for r in ex.map(lineup_audit,ids): lineups.append(r)
    ok=[r for r in lineups if r.get('ok')]; xi=[r for r in ok if r.get('team_count')==2 and sorted(r.get('starter_counts',[]))==[11,11]]
    schema={}; schema_rows=[]
    for name,rows in rows_by.items():
        mids=sample([x['match_id'] for x in rows])
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex: rr=list(ex.map(event_audit,mids))
        schema_rows.extend([{'season':name,**x} for x in rr]); good=[x for x in rr if x.get('ok')]
        schema[name]={'sample_n':len(rr),'download_ok':len(good),'with_xg':sum(int(x.get('xg_shots',0)>0) for x in good),'with_linked_assist':sum(int(x.get('linked_shot_assists',0)>0) for x in good),'xg_shots':sum(int(x.get('xg_shots',0)) for x in good),'linked_assists':sum(int(x.get('linked_shot_assists',0)) for x in good)}
    counts={k:len(v) for k,v in rows_by.items()}
    gates={'total_identity_ge_1000':len(ids)>=1000,'each_season_identity_ge_300':all(v>=300 for v in counts.values()),'global_match_id_unique':not dup,'identity_exact':not identity_errors,'lineup_coverage_ge_98pct':len(ok)/max(len(ids),1)>=.98,'xi_11v11_coverage_ge_95pct':len(xi)/max(len(ids),1)>=.95,'event_schema_three_of_three':all(v['download_ok']==15 and v['with_xg']>=14 and v['with_linked_assist']>=12 for v in schema.values())}
    result={'study_id':'r44l5_ligue1_attack_balance_external_domain','phase':'ZERO_LABEL_COVERAGE','source_commit':SOURCE_COMMIT,'competition_id':COMPETITION_ID,'season_ids':SEASONS,'label_fields_accessed':0,'model_fits':0,'thresholds_selected':0,'formal_weight':0,'season_identity_counts':counts,'total_identity_count':len(ids),'duplicate_match_ids':dup,'identity_error_count':len(identity_errors),'lineup_download_ok':len(ok),'lineup_coverage':len(ok)/max(len(ids),1),'xi_11v11_ok':len(xi),'xi_11v11_coverage':len(xi)/max(len(ids),1),'schema_checks':schema,'gates':gates,'status':'PASS_R44L5_ZERO_LABEL_COVERAGE' if all(gates.values()) else 'STOP_DATA_COVERAGE_R44L5'}
    (OUT/'zero_label_result_r44l5.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'source_ledger_r44l5.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'lineup_coverage_r44l5.json').write_text(json.dumps(lineups,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'event_schema_sample_r44l5.json').write_text(json.dumps(schema_rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'total':len(ids),'season_counts':counts,'lineup_coverage':result['lineup_coverage'],'xi_coverage':result['xi_11v11_coverage'],'gates':gates},ensure_ascii=False))
    return 0 if all(gates.values()) else 3
if __name__=='__main__': raise SystemExit(main())
