#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,json,math,re
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')


def valid(v:str)->bool:
    s=v.strip().casefold()
    if not s or s in {'nan','na','null','none'}: return False
    try: x=float(s)
    except ValueError: return False
    return math.isfinite(x) and x>1.0


def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')


def mapping(header):
    m={}
    for i,n in enumerate(header[3:],3):
        z=COL_RE.match(n)
        if z:m[(int(z.group(2)),int(z.group(3)),z.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError('odds mapping mismatch')
    return m


def complete(row,m,suffix):
    out=set()
    for b in range(1,33):
        if all(valid(row[m[(b,suffix,o)]]) for o in OUTCOMES):out.add(b)
    return out


def stats(x):
    y=sorted(x);n=len(y)
    def q(p):return y[min(n-1,max(0,int(round(p*(n-1)))))]
    return {'n':n,'min':y[0],'p05':q(.05),'p10':q(.10),'p25':q(.25),'p50':q(.50),'p75':q(.75),'p90':q(.90),'p95':q(.95),'max':y[-1],'mean':sum(y)/n}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    r=json.loads(a.registration.read_text())
    assert r['status']=='PRE_REGISTERED_NO_LABEL_BOOKMAKER_CONTINUITY_AUDIT'
    assert r['hard_limits']['result_labels_allowed'] is False
    start=datetime.fromisoformat(r['population_binding']['holdout_start'])
    counts={'training':[],'holdout':[]};rows=Counter();all_seen=0
    thresholds=[int(x) for x in r['continuity_definition']['reported_thresholds']]
    hits={'training':Counter(),'holdout':Counter()}
    for fn in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        p=a.source_dir/fn.replace('.csv.gz','_no_scores.csv.gz')
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            rd=csv.reader(f);h=next(rd)
            if h[:3]!=['match_id','match_date','match_time']:raise RuntimeError('bad header')
            if {'score_home','score_away'}&set(h):raise RuntimeError('score columns present')
            m=mapping(h)
            for row in rd:
                all_seen+=1
                if len(row)!=len(h) or not row[0].strip():continue
                anchors=[complete(row,m,71-x) for x in (24,6,1)]
                if len(set.intersection(*anchors))<5:continue
                part='training' if parse_dt(row[1],row[2])<start else 'holdout'
                rows[part]+=1
                core=None
                for suffix in range(47,71):
                    b=complete(row,m,suffix)
                    core=b if core is None else core & b
                    if not core: break
                n=len(core or set());counts[part].append(n)
                for t in thresholds:
                    if n>=t:hits[part][f'common24_ge_{t}']+=1
    e=r['population_binding']
    assert rows['training']==e['expected_training_eligible_rows']
    assert rows['holdout']==e['expected_holdout_eligible_rows']
    summary={p:stats(v) for p,v in counts.items()}
    minimum=min(summary['training']['min'],summary['holdout']['min'])
    if minimum>=5: choice='USE_ALL_HOURS_COMMON_BOOKMAKER_SET_MIN5'
    elif minimum>=3: choice='USE_ALL_HOURS_COMMON_BOOKMAKER_SET_MIN3'
    else: choice='USE_DYNAMIC_PER_HOUR_CONSENSUS_WITH_COUNT_AND_DISPERSION_CHANNELS'
    result={
      'schema_version':r['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'status':'PASS_R39E_BOOKMAKER_CONTINUITY_AUDIT_NO_LABELS',
      'population':{'training_eligible_rows':rows['training'],'holdout_eligible_rows':rows['holdout'],'all_rows_seen':all_seen},
      'common_bookmakers_all_T24_T1':summary,
      'threshold_counts':{p:dict(c) for p,c in hits.items()},
      'predeclared_representation_choice':choice,
      'no_label_audit':{'score_values_accessed':0,'result_values_accessed':0,'prediction_metrics_computed':0,'model_fits':0,'identity_locks_created':0,'holdout_individual_identities_output':0,'T0_accessed':0},
      'hard_limits':r['hard_limits']}
    (a.out_dir/'bookmaker_continuity_status_r39e.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
