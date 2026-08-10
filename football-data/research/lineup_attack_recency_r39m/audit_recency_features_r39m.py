#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'
sys.path.insert(0,str(R39L))
import audit_lineup_attack_features_r39l as lfeat


def load_recency_appearances(path,allowed):
    player=defaultdict(list);comp=defaultdict(list);rows=0;bad=0
    need={'appearance_id','game_id','player_id','player_club_id','date','competition_id','goals','assists','minutes_played'}
    with Path(path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
        if not need<=hdr:raise RuntimeError(f'appearances missing {sorted(need-hdr)}')
        for r in rd:
            c=str(r['competition_id']).strip()
            if c not in allowed:continue
            try:d=lfeat.parse_date(r['date']);g=lfeat.finite_num(r['goals']);a=lfeat.finite_num(r['assists']);m=lfeat.finite_num(r['minutes_played'])
            except:bad+=1;continue
            if g is None or a is None or m is None or m<0:bad+=1;continue
            pid=str(r['player_id']).strip()
            if not pid:bad+=1;continue
            z=(d.toordinal(),max(0.0,g)+max(0.0,a),max(0.0,m));player[pid].append(z);comp[c].append(z);rows+=1
    return pack(player),pack(comp),rows,bad

def pack(raw):
    out={}
    for k,vals in raw.items():
        vals.sort(key=lambda z:z[0]);out[k]=(np.asarray([z[0] for z in vals],dtype=np.int32),np.asarray([z[1] for z in vals],dtype=float),np.asarray([z[2] for z in vals],dtype=float))
    return out

def weighted_before(index,key,target_date,half):
    x=index.get(key)
    if x is None:return 0.0,0.0,None
    dates,ga,mins=x;t=target_date.toordinal();i=int(np.searchsorted(dates,t,side='left'))
    if i==0:return 0.0,0.0,None
    ages=t-dates[:i];w=np.exp(-math.log(2.0)*ages/float(half));return float(np.sum(w*ga[:i])),float(np.sum(w*mins[:i])),int(dates[i-1])

def rate(pid,d,comp,pidx,cidx,half,pseudo,cache):
    ck=(comp,d.toordinal(),int(half))
    if ck not in cache:
        cg,cm,cd=weighted_before(cidx,comp,d,half);cache[ck]=(cg/cm if cm>0 else None,cd)
    prior,cd=cache[ck]
    if prior is None:return None
    pg,pm,pd=weighted_before(pidx,pid,d,half);v=90.0*(pg+prior*pseudo)/(pm+pseudo);mx=max([x for x in (cd,pd) if x is not None],default=None)
    if mx is not None and not mx<d.toordinal():raise RuntimeError('strict-lag violation')
    return float(v),mx

def feature_row(m,starters,pidx,cidx,half,reg,cache):
    d=m['target_date'];comp=reg['source']['competition_ids'][m['div']];pseudo=float(reg['recency_contract']['eb_pseudo_minutes']);hxi=starters.get((m['tm_game_id'],m['home_club_id']),set());axi=starters.get((m['tm_game_id'],m['away_club_id']),set())
    if len(hxi)!=11 or len(axi)!=11:return None,'bad_current_xi'
    def score(ps):
        vals=[];mx=None
        for pid in ps:
            z=rate(pid,d,comp,pidx,cidx,half,pseudo,cache)
            if z is None:return None
            v,md=z;vals.append(v);mx=max([x for x in (mx,md) if x is not None],default=None)
        return vals,mx
    hs=score(hxi);as_=score(axi)
    if hs is None or as_ is None:return None,'missing_comp_prior'
    hv,hm=hs;av,am=as_;hmean=float(np.mean(hv));amean=float(np.mean(av));htop=float(sum(sorted(hv,reverse=True)[:4]));atop=float(sum(sorted(av,reverse=True)[:4]));q=m['qclose'];x=[abs(float(q[0]-q[2])),lfeat.entropy(q),hmean,amean,abs(hmean-amean),hmean+amean,htop,atop,abs(htop-atop),htop+atop]
    mx=max([z for z in (hm,am) if z is not None],default=None);viol=bool(mx is not None and not mx<d.toordinal())
    if len(x)!=10 or any(not math.isfinite(v) for v in x):return None,'nonfinite_feature'
    return {'identity':m['identity'],'season':m['season'],'div':m['div'],'date':str(d),'x':x,'q':[float(v) for v in q],'max_source_ordinal':mx,'strict_lag_violation':viol},None

def feature_hash(features):
    lines=[]
    for i in sorted(features):
        z=features[i];lines.append(i+'|'+','.join(format(float(v),'.17g') for v in z['x']))
    return hashlib.sha256(('\n'.join(lines)+'\n').encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--r39l-source-registration',type=Path,required=True);ap.add_argument('--r39i-registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--appearances',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());r39l=json.loads(a.r39l_source_registration.read_text());r39i=json.loads(a.r39i_registration.read_text())
    games,starters,complete,mapped,type_counts,lineup_rows=lfeat.rebuild_mapping(a.market_dir,a.games,a.lineups,r39i);pre=[m for m in mapped if m['season']!='2526'];hold=[m for m in mapped if m['season']=='2526'];lane=reg['identity_lane']
    if (len(pre),len(hold))!=(lane['preholdout_rows'],lane['holdout_2526_rows']):raise RuntimeError(f'identity lane drift {len(pre)} {len(hold)}')
    fixed=sorted(hold,key=lambda r:lfeat.htxt(f"{lane['fixed100_seed']}|{r['identity']}"))[:lane['fixed100_rows']];sha=lfeat.set_sha([r['identity'] for r in fixed])
    if sha!=lane['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {sha}')
    pidx,cidx,app_rows,bad=load_recency_appearances(a.appearances,set(reg['recency_contract']['eligible_appearance_competitions']))
    if bad:raise RuntimeError(f'bad appearance rows {bad}')
    results={};total_viol=0
    for half in reg['recency_contract']['half_life_days_candidates']:
        features={};reasons=Counter();cache={};viol=0
        for m in mapped:
            z,reason=feature_row(m,starters,pidx,cidx,int(half),reg,cache)
            if z is None:reasons[reason]+=1;continue
            features[z['identity']]=z;viol+=int(z['strict_lag_violation'])
        fixed_ids={r['identity'] for r in fixed};counts={'all':len(features),'preholdout':sum(r['identity'] in features for r in pre),'holdout_2526':sum(r['identity'] in features for r in hold),'fixed100':sum(i in features for i in fixed_ids)};total_viol+=viol;results[str(half)]={'counts':counts,'ineligible_reasons':dict(reasons),'strict_lag_violations':viol,'feature_sha256':feature_hash(features)}
    gate=reg['coverage_gate'];passed=all(v['counts']['preholdout']==gate['preholdout_rows_required'] and v['counts']['holdout_2526']==gate['holdout_2526_rows_required'] and v['counts']['fixed100']==gate['fixed100_rows_required'] and v['strict_lag_violations']<=gate['strict_lag_violations_max'] and not v['ineligible_reasons'] for v in results.values())
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'PASS_R39M_ZERO_TARGET_LABEL_RECENCY_FEATURE_COVERAGE' if passed else 'STOP_R39M_RECENCY_FEATURE_COVERAGE_OR_LAG_GATE','identity_lane':{'preholdout':len(pre),'holdout_2526':len(hold),'fixed100':len(fixed),'fixed100_identity_sha256':sha},'transfermarkt':{'games':len(games),'complete_games':len(complete),'lineup_rows':lineup_rows,'lineup_type_counts':dict(type_counts),'eligible_appearance_rows':app_rows,'bad_appearance_rows':bad},'half_life_results':results,'total_strict_lag_violations':total_viol,'audit_access':reg['no_target_label_contract'],'hard_limits':reg['hard_limits']};a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'feature_coverage_r39m.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:out[k] for k in ('status','identity_lane','half_life_results','total_strict_lag_violations','audit_access')},indent=2))
if __name__=='__main__':main()
