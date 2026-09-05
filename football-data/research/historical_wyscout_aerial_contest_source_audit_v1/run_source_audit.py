from __future__ import annotations
import argparse,collections,hashlib,json,math,re,statistics
from datetime import datetime
from pathlib import Path

EXPECTED={"matches_England.json":380,"matches_Spain.json":380,"matches_Italy.json":380,"matches_Germany.json":306,"matches_France.json":380}

def pearson(a,b):
    if len(a)!=len(b) or len(a)<3:return None
    ma,mb=statistics.fmean(a),statistics.fmean(b); va=sum((x-ma)**2 for x in a); vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def read_index(root):
    # Intentionally ignores the score/label column in processed-v2 README.
    t=(root/'processed-v2/README.md').read_text(encoding='utf-8')
    p=re.compile(r'^\|\[(\d+)\]\(files/(\d+)\.json\)\|[^|]*\|([^|]+)\|(matches_[A-Za-z_]+\.json)\|$',re.M)
    out=[]
    for a,b,dtext,src in p.findall(t):
        if src not in EXPECTED: continue
        if a!=b: raise RuntimeError('index id mismatch')
        m=re.search(r'([A-Za-z]+ \d{1,2}, \d{4}) at',dtext)
        if not m: raise RuntimeError(f'date parse {a}')
        out.append({'match_id':int(a),'date':datetime.strptime(m.group(1),'%B %d, %Y').date().isoformat(),'league':src})
    c=collections.Counter(x['league'] for x in out)
    if len(out)!=1826 or dict(c)!=EXPECTED: raise RuntimeError(f'index drift {len(out)} {dict(c)}')
    return out

def build_raw(root,index,min_duels):
    rows=[]; valid_fixtures=0; total_duels=air_duels=0; league_vals=collections.defaultdict(list)
    for meta in index:
        p=root/f"processed-v2/files/{meta['match_id']}.json"; data=json.loads(p.read_text(encoding='utf-8')); ev=data.get('events') or []
        teams=sorted({int(e.get('teamId',0) or 0) for e in ev if int(e.get('teamId',0) or 0)>0})
        if len(teams)!=2: raise RuntimeError(f"team inventory {meta['match_id']} {teams}")
        z={tid:{'events':0,'duels':0,'air':0,'fouls':0,'counter':0,'pass':0,'acc_xy':0,'forward':0.0} for tid in teams}
        for e in ev:
            tid=int(e.get('teamId',0) or 0)
            if tid not in z: continue
            r=z[tid]; r['events']+=1
            tags={int(q.get('id')) for q in (e.get('tags') or []) if isinstance(q,dict) and q.get('id') is not None}
            if 1901 in tags:r['counter']+=1
            if e.get('eventName')=='Foul':r['fouls']+=1
            if e.get('eventName')=='Duel':
                r['duels']+=1; total_duels+=1
                if e.get('subEventName')=='Air duel':r['air']+=1; air_duels+=1
            if e.get('eventName')=='Pass':
                r['pass']+=1
                if 1801 in tags:
                    pos=e.get('positions') or []
                    good=len(pos)>=2 and all(isinstance(q,dict) for q in pos[:2]) and all(isinstance(pos[i].get('x'),(int,float)) and isinstance(pos[i].get('y'),(int,float)) for i in (0,1))
                    if good:
                        x0,x1=float(pos[0]['x']),float(pos[1]['x'])
                        if not(0<=x0<=100 and 0<=x1<=100): raise RuntimeError('coord domain')
                        r['acc_xy']+=1; r['forward']+=max(x1-x0,0.0)
        ok=True
        for tid in teams:
            r=z[tid]
            if r['duels']<min_duels: ok=False
            feat=r['air']/r['duels'] if r['duels'] else None
            row={'match_id':meta['match_id'],'date':meta['date'],'league':meta['league'],'team_id':tid,'duel_n':r['duels'],'air_duel_n':r['air'],'air_duel_share':feat,
                 'duel_event_rate_proxy':r['duels']/r['events'] if r['events'] else None,'foul_event_rate_proxy':r['fouls']/r['events'] if r['events'] else None,
                 'counterattack_event_rate_proxy':r['counter']/r['events'] if r['events'] else None,
                 'pass_progression_proxy':r['forward']/r['acc_xy'] if r['acc_xy'] else None,'source_file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            rows.append(row)
            if feat is not None: league_vals[meta['league']].append(feat)
        if ok:valid_fixtures+=1
    if len(rows)!=3652: raise RuntimeError(f'row drift {len(rows)}')
    variances={k:statistics.pvariance(v) for k,v in league_vals.items()}
    return rows,{'duel_event_n':total_duels,'air_duel_event_n':air_duels,'air_duel_share_global':air_duels/total_duels if total_duels else 0,
                 'raw_bilateral_valid_fixture_n':valid_fixtures,'raw_bilateral_valid_fixture_rate':valid_fixtures/1826,'league_raw_feature_variance':variances}

def build_pit(rows,lookback,min_prior):
    dates=sorted({r['date'] for r in rows}); bydate=collections.defaultdict(list)
    for r in rows:bydate[r['date']].append(r)
    hist=collections.defaultdict(list); out=[]; bilateral=0; matchvals=[]
    for d in dates:
        bymatch=collections.defaultdict(list)
        for r in bydate[d]:bymatch[(r['league'],r['match_id'])].append(r)
        pending=[]
        for key,rr in sorted(bymatch.items()):
            avail=[]; vals=[]
            for r in sorted(rr,key=lambda x:x['team_id']):
                h=hist[(r['league'],r['team_id'])]; f=statistics.fmean(h[-lookback:]) if len(h)>=min_prior else None
                out.append({'match_id':r['match_id'],'date':d,'league':r['league'],'team_id':r['team_id'],'prior_n':len(h),'strict_pit_air_duel_share':f}); avail.append(f is not None); vals.append(f)
                if r['air_duel_share'] is not None:pending.append((r['league'],r['team_id'],float(r['air_duel_share'])))
            if all(avail):bilateral+=1; matchvals.append(statistics.fmean(float(x) for x in vals if x is not None))
        for lg,tid,v in pending:hist[(lg,tid)].append(v)
    return out,{'strict_pit_bilateral_feature_fixture_n':bilateral,'strict_pit_bilateral_feature_coverage':bilateral/1826,'strict_pit_match_feature_variance':statistics.pvariance(matchvals) if len(matchvals)>1 else 0,'same_calendar_date_isolation':True,'lookback':lookback,'min_prior':min_prior}

def correlations(rows):
    v=[r for r in rows if r['air_duel_share'] is not None and r['pass_progression_proxy'] is not None]
    y=[float(r['air_duel_share']) for r in v]
    fields=['duel_event_rate_proxy','foul_event_rate_proxy','counterattack_event_rate_proxy','pass_progression_proxy']
    c={f:pearson(y,[float(r[f]) for r in v]) for f in fields}; finite=[abs(x) for x in c.values() if x is not None]
    return {'pearson':c,'max_abs':max(finite) if finite else None,'n':len(v)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',required=True); ap.add_argument('--wys-root',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    c=json.loads(Path(a.contract).read_text()); root=Path(a.wys_root); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    if c['status']!='FROZEN_ZERO_LABEL_SOURCE_AUDIT' or not c['boundaries']['zero_label_only'] or c['boundaries']['model_fit_allowed']:raise RuntimeError('contract drift')
    idx=read_index(root); rows,raw=build_raw(root,idx,int(c['audit_gates']['minimum_duel_events_per_team_match'])); pit,pa=build_pit(rows,8,3); corr=correlations(rows); g=c['audit_gates']
    checks={'fixture_n':len(idx)==g['expected_fixture_n'],'team_match_n':len(rows)==g['expected_team_match_n'],'raw_coverage':raw['raw_bilateral_valid_fixture_rate']>=g['raw_bilateral_valid_fixture_rate_min'],
            'pit_coverage':pa['strict_pit_bilateral_feature_coverage']>=g['strict_pit_bilateral_feature_coverage_min'],'five_league_variance':len(raw['league_raw_feature_variance'])==5 and all(v>0 for v in raw['league_raw_feature_variance'].values()),
            'pit_variance':pa['strict_pit_match_feature_variance']>0,'proxy_redundancy':corr['max_abs'] is not None and corr['max_abs']<g['max_abs_same_source_proxy_correlation']}
    passed=all(checks.values()); status=c['terminal']['pass'] if passed else c['terminal']['fail']
    res={'schema_version':'football3-wyscout-aerial-contest-source-audit-result-v1','status':status,'source_gate_pass':passed,'fixture_n':len(idx),'team_match_n':len(rows),'raw_audit':raw,'strict_pit_audit':pa,'proxy_redundancy':corr,'checks':checks,
         'candidate_fit_performed':False,'outcome_fields_used':False,'index_match_label_parsed':False,'duel_outcome_tags_used':False,'historical_confirmation_2023_opened':False,'prospective_1335_touched':False,'formal_model_changed':False,'CURRENT_changed':False,'formal_weight':0}
    (out/'SOURCE_AUDIT_RESULT.json').write_text(json.dumps(res,sort_keys=True,indent=2)+'\n'); (out/'TEAM_MATCH_FEATURES.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in rows)); (out/'STRICT_PIT_FEATURES.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in pit)); print(json.dumps(res,sort_keys=True)); return 0
if __name__=='__main__':raise SystemExit(main())
