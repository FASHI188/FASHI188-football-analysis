#!/usr/bin/env python3
from __future__ import annotations

import csv, hashlib, json, math
from collections import Counter
from pathlib import Path

FILES=sorted(Path('.').glob('football-data/training_datasets/*/point_in_time.csv'))
if not FILES: raise SystemExit('NO_POINT_IN_TIME_FILES')

IDENTITY={'competition_id','season','date','home_team','away_team'}
CONTROL={'split'}
R39U_RAW={
 'home_history_matches','away_history_matches','home_history_gf','away_history_gf','home_history_ga','away_history_ga',
 'home_history_ppg','away_history_ppg','home_last5_ppg','away_last5_ppg','home_last5_gf','away_last5_gf',
 'home_last5_ga','away_last5_ga','elo_difference_with_home_advantage'
}

def fnum(v):
    try: x=float(str(v).strip())
    except Exception: return None
    return x if math.isfinite(x) else None

def corr(xs,ys):
    if len(xs)<2:return None
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    sx=sum((x-mx)**2 for x in xs); sy=sum((y-my)**2 for y in ys)
    if sx<=0 or sy<=0:return None
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(sx*sy)

headers=[]
for p in FILES:
    with p.open('r',encoding='utf-8-sig',newline='') as f:
        headers.append(list(csv.DictReader(f).fieldnames or []))
canonical=headers[0]
if not all(h==canonical for h in headers): raise RuntimeError('SCHEMA_DRIFT')
label_fields=[x for x in canonical if x.startswith('label_')]
unused=[x for x in canonical if x not in IDENTITY|CONTROL|R39U_RAW and not x.startswith('label_')]

stats={k:{'finite':0,'sum':0.0,'min':None,'max':None,'unique':set()} for k in unused}
pairs={
 'home_stage2_vs_last5':([],[]), 'away_stage2_vs_last5':([],[]),
 'elo_reconstructed_vs_existing':([],[])
}
flags={k:Counter() for k in ('cold_start_flag','stage2_form_ready_flag') if k in canonical}
rows=0
for p in FILES:
    with p.open('r',encoding='utf-8-sig',newline='') as f:
        for raw in csv.DictReader(f):
            rows+=1
            # No label_* value is referenced anywhere in this loop.
            for k in unused:
                x=fnum(raw.get(k,''))
                if x is None: continue
                s=stats[k]; s['finite']+=1; s['sum']+=x
                s['min']=x if s['min'] is None else min(s['min'],x)
                s['max']=x if s['max'] is None else max(s['max'],x)
                if len(s['unique'])<10000:s['unique'].add(x)
            if 'home_stage2_form_ppg' in canonical and 'home_last5_ppg' in canonical:
                a=fnum(raw.get('home_stage2_form_ppg','')); b=fnum(raw.get('home_last5_ppg',''))
                if a is not None and b is not None: pairs['home_stage2_vs_last5'][0].append(a); pairs['home_stage2_vs_last5'][1].append(b)
            if 'away_stage2_form_ppg' in canonical and 'away_last5_ppg' in canonical:
                a=fnum(raw.get('away_stage2_form_ppg','')); b=fnum(raw.get('away_last5_ppg',''))
                if a is not None and b is not None: pairs['away_stage2_vs_last5'][0].append(a); pairs['away_stage2_vs_last5'][1].append(b)
            if all(k in canonical for k in ('home_elo_pre','away_elo_pre','elo_home_advantage','elo_difference_with_home_advantage')):
                h=fnum(raw.get('home_elo_pre','')); a=fnum(raw.get('away_elo_pre','')); adv=fnum(raw.get('elo_home_advantage','')); e=fnum(raw.get('elo_difference_with_home_advantage',''))
                if None not in (h,a,adv,e): pairs['elo_reconstructed_vs_existing'][0].append(h-a+adv); pairs['elo_reconstructed_vs_existing'][1].append(e)
            for k in flags: flags[k][str(raw.get(k,'')).strip()]+=1

stat_out={}
for k,s in stats.items():
    stat_out[k]={
      'finite_count':s['finite'],'coverage':s['finite']/rows if rows else 0,
      'mean':s['sum']/s['finite'] if s['finite'] else None,
      'min':s['min'],'max':s['max'],'unique_count_capped_10000':len(s['unique'])
    }
pair_out={}
for k,(xs,ys) in pairs.items():
    diffs=[abs(x-y) for x,y in zip(xs,ys)]
    pair_out[k]={
      'n':len(xs),'pearson':corr(xs,ys),
      'exact_equal_rate':sum(x==y for x,y in zip(xs,ys))/len(xs) if xs else None,
      'mean_absolute_difference':sum(diffs)/len(diffs) if diffs else None,
      'max_absolute_difference':max(diffs) if diffs else None
    }
out={
 'schema_version':'R40H-FEATURE-REDUNDANCY-AUDIT-1.0','status':'PASS_R40H_ZERO_LABEL_FEATURE_AUDIT',
 'file_count':len(FILES),'row_count':rows,'field_count':len(canonical),'canonical_fields':canonical,
 'r39u_raw_fields_present':sorted(R39U_RAW & set(canonical)),
 'r39u_raw_fields_missing':sorted(R39U_RAW - set(canonical)),
 'unused_pre_match_fields':unused,'unused_numeric_stats':stat_out,'pairwise_redundancy':pair_out,
 'flag_distributions':{k:dict(v) for k,v in flags.items()},'label_fields_present_but_values_not_referenced':label_fields,
 'hard_boundaries':{'target_label_values_referenced':0,'model_fits':0,'candidate_probabilities':0,'external_network_requests':0,'football_api_requests':0,'new_data_collection':False,'formal_weight':0,'formal_model_mutation':False,'formal_data_mutation':False,'current_rule_mutation':False,'main_mutation':False}
}
Path('/tmp/r40h-output').mkdir(parents=True,exist_ok=True)
raw=json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
Path('/tmp/r40h-output/r40h_audit.json').write_text(raw,encoding='utf-8')
Path('/tmp/r40h-output/r40h_audit.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest()+'\n',encoding='ascii')
print(json.dumps({k:out[k] for k in ('status','file_count','row_count','field_count','r39u_raw_fields_present','r39u_raw_fields_missing','unused_pre_match_fields','unused_numeric_stats','pairwise_redundancy','flag_distributions','hard_boundaries')},ensure_ascii=False,indent=2))
