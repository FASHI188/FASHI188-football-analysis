#!/usr/bin/env python3
import argparse, collections, datetime as dt, hashlib, json, re
from pathlib import Path

C = Path(__file__).with_name("v3_schedule_importance_feature_construction_contract_v1.json")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REG = re.compile(r"^Matchday (\d+)$")
CHAMP = re.compile(r"^Championship, Matchday (\d+)$")
RELEG = re.compile(r"^Relegation, Matchday (\d+)$")

class Stop(RuntimeError): pass

def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def parse_round(s):
    for rx,stage in ((REG,"REGULAR"),(CHAMP,"CHAMPIONSHIP_SPLIT"),(RELEG,"RELEGATION_SPLIT")):
        m=rx.fullmatch(s)
        if m: return stage,int(m.group(1))
    fixed={
      "Semifinals":("PLAYOFF_SEMIFINAL",None),
      "Final":("PLAYOFF_FINAL",None),
      "Europa League Finals, Semifinals":("EUROPA_QUALIFICATION_PLAYOFF_SEMIFINAL",None),
      "Europa League Finals, Final":("EUROPA_QUALIFICATION_PLAYOFF_FINAL",None),
    }
    if s in fixed:return fixed[s]
    raise Stop("STOP_UNKNOWN_ROUND_STAGE")

def load_rows(path,c):
    if sha256_file(path)!=c['parent']['sanitized_sha256']: raise Stop("STOP_PARENT_SANITIZED_SHA_MISMATCH")
    allowed=set(c['input_contract']['allowed_input_keys']); forbidden=set(c['input_contract']['forbidden_input_or_output_keys'])
    rows=[]
    for i,line in enumerate(open(path,encoding='utf-8')):
        try:r=json.loads(line)
        except Exception as e: raise Stop("STOP_SANITIZED_JSON_PARSE") from e
        if set(r)!=allowed: raise Stop("STOP_SANITIZED_KEYSET")
        if forbidden & set(r): raise Stop("STOP_FORBIDDEN_INPUT_KEY")
        if not isinstance(r['source_path'],str) or not isinstance(r['row_index'],int) or r['row_index']<0: raise Stop("STOP_IDENTITY_SCHEMA")
        if not isinstance(r['round'],str) or not isinstance(r['team1'],str) or not isinstance(r['team2'],str): raise Stop("STOP_SAFE_SCHEMA")
        if not r['team1'].strip() or not r['team2'].strip() or r['team1']==r['team2']: raise Stop("STOP_TEAM_IDENTITY")
        if not isinstance(r['date'],str) or not DATE.fullmatch(r['date']): raise Stop("STOP_DATE_SCHEMA")
        try:r['_date']=dt.date.fromisoformat(r['date'])
        except ValueError as e: raise Stop("STOP_DATE_VALUE") from e
        parse_round(r['round'])
        rows.append(r)
    if len(rows)!=c['parent']['sanitized_rows']: raise Stop("STOP_PARENT_ROW_COUNT")
    keys=[(r['source_path'],r['row_index']) for r in rows]
    if len(keys)!=len(set(keys)): raise Stop("STOP_DUPLICATE_SOURCE_ROW")
    fixture=[(r['source_path'],r['date'],r['team1'],r['team2']) for r in rows]
    if len(fixture)!=len(set(fixture)): raise Stop("STOP_DUPLICATE_FIXTURE")
    seen=collections.Counter()
    for r in rows:
        seen[(r['source_path'],r['date'],r['team1'])]+=1; seen[(r['source_path'],r['date'],r['team2'])]+=1
    if any(v>1 for v in seen.values()): raise Stop("STOP_TEAM_MULTIPLE_MATCHES_SAME_DATE")
    return rows

def features_for(history,target_date):
    prior=[x for x in history if x[0] < target_date]
    if not prior:return (None,0,0,0,0)
    latest=max(x[0] for x in prior)
    rest=(target_date-latest).days
    counts=[sum(1 for d,_ in prior if 0 < (target_date-d).days <= w) for w in (7,14,21)]
    streak=0
    for _,away in sorted(prior,key=lambda x:x[0],reverse=True):
        if away:streak+=1
        else:break
    return (rest,*counts,streak)

def build(c,rows):
    out=[]; history=collections.defaultdict(list)
    grouped=collections.defaultdict(list)
    for r in rows: grouped[(r['source_path'],r['_date'])].append(r)
    for source_path,date in sorted(grouped):
        todays=sorted(grouped[(source_path,date)],key=lambda r:r['row_index'])
        for r in todays:
            stage,num=parse_round(r['round'])
            hf=features_for(history[(source_path,r['team1'])],date)
            af=features_for(history[(source_path,r['team2'])],date)
            z={
              'source_path':r['source_path'],'row_index':r['row_index'],'date':r['date'],'round':r['round'],
              'round_stage':stage,'round_number':num,'team1':r['team1'],'team2':r['team2'],
              'home_league_rest_days':hf[0],'away_league_rest_days':af[0],
              'home_league_congestion_7d':hf[1],'away_league_congestion_7d':af[1],
              'home_league_congestion_14d':hf[2],'away_league_congestion_14d':af[2],
              'home_league_congestion_21d':hf[3],'away_league_congestion_21d':af[3],
              'home_consecutive_away_before':hf[4],'away_consecutive_away_before':af[4],
            }
            if list(z)!=c['output_contract']['keys']: raise Stop("STOP_OUTPUT_KEYSET")
            out.append(z)
        for r in todays:
            history[(source_path,r['team1'])].append((date,False))
            history[(source_path,r['team2'])].append((date,True))
    out.sort(key=lambda r:(r['source_path'],r['row_index']))
    if len(out)!=c['output_contract']['row_count']:raise Stop("STOP_OUTPUT_ROW_COUNT")
    forbidden=set(c['input_contract']['forbidden_input_or_output_keys'])
    if any(forbidden & set(r) for r in out):raise Stop("STOP_FORBIDDEN_OUTPUT_KEY")
    return out

def audit(inp,outp,receiptp):
    c=json.loads(C.read_text()); base={
      'schema_version':'football3-v3-schedule-importance-feature-construction-receipt-v1',
      'phase':'SCHEDULE_FEATURE_CONSTRUCTION_PREREG_AUDIT','target_population':'COMPLETED_MATCHES_ONLY',
      'parent_head_sha':c['parent']['head_sha'],'parent_run_id':c['parent']['run_id'],'parent_artifact_id':c['parent']['artifact_id'],
      'parent_sanitized_sha256':c['parent']['sanitized_sha256'],'coverage_scope':c['feature_contract']['coverage_scope'],
      'input_rows':0,'output_rows':0,'score_result_goal_values_read':0,'future_matches_allowed':False,'stage6_touched':False,
      'standings_pressure_used':False,'travel_pressure_used':False,'rotation_pressure_used':False,'hour_level_rest_used':False,
      'training':False,'tuning':False,'formal_weight':0,'matrix_delta':0,'data_ready':False
    }
    try:
        if c.get('status')!='DESIGN_LOCKED':raise Stop('STOP_CONTRACT_NOT_LOCKED')
        rows=load_rows(inp,c); base['input_rows']=len(rows)
        out=build(c,rows); base['output_rows']=len(out)
        text=''.join(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n' for r in out)
        Path(outp).write_text(text)
        base['feature_sha256']=hashlib.sha256(text.encode()).hexdigest()
        base['round_stage_counts']=dict(sorted(collections.Counter(r['round_stage'] for r in out).items()))
        base['home_rest_missing']=sum(r['home_league_rest_days'] is None for r in out)
        base['away_rest_missing']=sum(r['away_league_rest_days'] is None for r in out)
        base['max_home_congestion_7d']=max(r['home_league_congestion_7d'] for r in out)
        base['max_away_congestion_7d']=max(r['away_league_congestion_7d'] for r in out)
        base['data_ready']=True
        base['decision']='PASS_SCHEDULE_FEATURE_CONSTRUCTION_PREREG_NEXT_SCIENTIFIC_EVALUATION_PREREG'
    except Stop as e: base['decision']=str(e)
    Path(receiptp).write_text(json.dumps(base,indent=2,sort_keys=True)+'\n')
    return base

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--receipt',required=True);a=p.parse_args()
    r=audit(a.input,a.output,a.receipt);print(json.dumps({'decision':r['decision'],'output_rows':r['output_rows']},sort_keys=True))
    if not r['decision'].startswith('PASS_'):raise SystemExit(2)
if __name__=='__main__':main()
