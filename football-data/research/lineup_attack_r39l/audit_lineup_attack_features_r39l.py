#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,csv,json,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39I=HERE.parent/'prematch_shock_r39i'
sys.path.insert(0,str(R39I))
from audit_lineup_coverage_r39i import load_transfermarkt,choose,identity,parse_fd_date,valid_odd,htxt,set_sha

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']

def devig3(a,b,c):
    q=np.array([1/float(a),1/float(b),1/float(c)],dtype=float);return q/q.sum()
def entropy(q):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in q)
def parse_date(s):return datetime.strptime(str(s).strip()[:10],'%Y-%m-%d').date()
def finite_num(v):
    try:x=float(str(v).strip())
    except:return None
    return x if math.isfinite(x) else None

def rebuild_mapping(market_dir,games_path,lineups_path,covreg):
    games,by_date,starters,complete,type_counts,lineup_rows=load_transfermarkt(games_path,lineups_path,covreg)
    fd=[]
    for p in sorted(Path(market_dir).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}&hdr:raise RuntimeError('result columns leaked into market input')
            for r in rd:
                if r.get('Season') not in covreg['football_data']['seasons'] or r.get('Div') not in covreg['football_data']['divisions']:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in covreg['football_data']['market_requirement']):continue
                fd.append(r)
    fd.sort(key=lambda r:(parse_fd_date(r['Date']),r['Div'],r['HomeTeam'],r['AwayTeam']))
    used=set();mapped=[]
    for r in fd:
        res=choose(r,by_date,covreg,used)
        if res is None:continue
        g,off,pair=res;used.add(g['game_id'])
        if g['game_id'] not in complete:continue
        q=devig3(r['AvgCH'],r['AvgCD'],r['AvgCA'])
        mapped.append({'identity':identity(r),'season':r['Season'],'div':r['Div'],'target_date':g['date'],'tm_game_id':g['game_id'],'home_club_id':g['home_club_id'],'away_club_id':g['away_club_id'],'qclose':q,'pair_similarity':pair,'day_offset':off})
    return games,starters,complete,mapped,type_counts,lineup_rows

def load_appearances(path,allowed_competitions):
    player=defaultdict(list);comp=defaultdict(list);rows=0;bad=0
    need={'appearance_id','game_id','player_id','player_club_id','date','competition_id','goals','assists','minutes_played'}
    with Path(path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
        if not need<=hdr:raise RuntimeError(f'appearances missing {sorted(need-hdr)}')
        for r in rd:
            if r['competition_id'] not in allowed_competitions:continue
            try:d=parse_date(r['date'])
            except:bad+=1;continue
            goals=finite_num(r['goals']);assists=finite_num(r['assists']);mins=finite_num(r['minutes_played'])
            if goals is None or assists is None or mins is None or mins<0:bad+=1;continue
            ga=max(0.0,goals)+max(0.0,assists);mins=max(0.0,mins);pid=str(r['player_id']).strip();c=str(r['competition_id']).strip()
            if not pid:bad+=1;continue
            player[pid].append((d,ga,mins));comp[c].append((d,ga,mins));rows+=1
    return make_prefix(player),make_prefix(comp),rows,bad

def make_prefix(raw):
    out={}
    for key,vals in raw.items():
        vals.sort(key=lambda z:z[0]);dates=[];ga=[0.0];mins=[0.0]
        for d,g,m in vals:dates.append(d);ga.append(ga[-1]+g);mins.append(mins[-1]+m)
        out[key]=(dates,ga,mins)
    return out

def before(index,key,d):
    x=index.get(key)
    if x is None:return 0.0,0.0,None
    dates,ga,mins=x;i=bisect.bisect_left(dates,d)
    return ga[i],mins[i],dates[i-1] if i else None

def player_rate(pid,d,target_comp,player_index,comp_index,pseudo):
    cg,cm,cd=before(comp_index,target_comp,d)
    if cm<=0:return None
    pg,pm,pd=before(player_index,pid,d)
    prior=cg/cm;rate=90.0*(pg+prior*pseudo)/(pm+pseudo)
    maxd=max([x for x in (cd,pd) if x is not None],default=None)
    if maxd is not None and not maxd<d:raise RuntimeError('appearance strict-lag violation')
    return float(rate),maxd

def score_set(players,d,target_comp,player_index,comp_index,pseudo):
    vals=[];mx=None
    for pid in players:
        z=player_rate(pid,d,target_comp,player_index,comp_index,pseudo)
        if z is None:return None
        v,md=z;vals.append(v)
        if md is not None and (mx is None or md>mx):mx=md
    return vals,mx

def feature_row(m,starters,player_index,comp_index,reg):
    comp=reg['source']['competition_ids'][m['div']];d=m['target_date'];pseudo=float(reg['feature_contract']['eb_pseudo_minutes'])
    hxi=starters.get((m['tm_game_id'],m['home_club_id']),set());axi=starters.get((m['tm_game_id'],m['away_club_id']),set())
    if len(hxi)!=11 or len(axi)!=11:return None,'bad_current_xi'
    hs=score_set(hxi,d,comp,player_index,comp_index,pseudo);as_=score_set(axi,d,comp,player_index,comp_index,pseudo)
    if hs is None or as_ is None:return None,'missing_comp_prior'
    hv,hmx=hs;av,amx=as_;hmean=float(np.mean(hv));amean=float(np.mean(av));htop=float(sum(sorted(hv,reverse=True)[:4]));atop=float(sum(sorted(av,reverse=True)[:4]))
    q=m['qclose'];x=[abs(float(q[0]-q[2])),entropy(q),hmean,amean,abs(hmean-amean),hmean+amean,htop,atop,abs(htop-atop),htop+atop]
    maxd=max([z for z in (hmx,amx) if z is not None],default=None);violation=bool(maxd is not None and not maxd<d)
    if len(x)!=int(reg['feature_contract']['feature_dimension']):raise RuntimeError(f'feature dimension {len(x)}')
    if any(not math.isfinite(float(v)) for v in x):return None,'nonfinite_feature'
    return {'identity':m['identity'],'season':m['season'],'div':m['div'],'target_date':str(d),'x':x,'max_source_date':str(maxd) if maxd else None,'strict_lag_violation':violation},None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--r39i-coverage-registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--appearances',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());cov=json.loads(a.r39i_coverage_registration.read_text())
    games,starters,complete,mapped,type_counts,lineup_rows=rebuild_mapping(a.market_dir,a.games,a.lineups,cov)
    hold=reg['r39i_identity_lane'];pre=[m for m in mapped if m['season']!='2526'];hp=[m for m in mapped if m['season']=='2526']
    if len(pre)!=hold['complete_preholdout_rows'] or len(hp)!=hold['complete_2526_rows']:raise RuntimeError(f'R39I lane drift pre={len(pre)} hold={len(hp)}')
    fixed=sorted(hp,key=lambda r:htxt(f"{hold['fixed100_seed']}|{r['identity']}"))[:hold['fixed100_rows']];sha=set_sha([r['identity'] for r in fixed])
    if sha!=hold['fixed100_identity_sha256']:raise RuntimeError(f'R39I fixed100 drift {sha}')
    pidx,cidx,app_rows,app_bad=load_appearances(a.appearances,set(reg['feature_contract']['eligible_appearance_competitions']))
    features={};reasons=Counter();violations=0
    for m in mapped:
        z,reason=feature_row(m,starters,pidx,cidx,reg)
        if z is None:reasons[reason]+=1;continue
        features[z['identity']]=z;violations+=int(z['strict_lag_violation'])
    fpre=[features[m['identity']] for m in pre if m['identity'] in features];fixed_ids={x['identity'] for x in fixed};ffixed=[features[i] for i in fixed_ids if i in features]
    arr=np.asarray([z['x'] for z in features.values()],dtype=float) if features else np.empty((0,int(reg['feature_contract']['feature_dimension'])))
    summary=[]
    if len(arr):
        for j in range(arr.shape[1]):summary.append({'index':j,'min':float(arr[:,j].min()),'mean':float(arr[:,j].mean()),'max':float(arr[:,j].max()),'std':float(arr[:,j].std())})
    gate=reg['coverage_gate'];passed=(len(fpre)>=int(gate['minimum_feature_eligible_preholdout_rows']) and len(ffixed)==int(gate['fixed100_feature_eligible_rows_required']) and violations<=int(gate['strict_lag_violations_max']) and np.isfinite(arr).all())
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'PASS_R39L_ZERO_TARGET_LABEL_FEATURE_COVERAGE' if passed else 'STOP_R39L_FEATURE_COVERAGE_OR_LAG_GATE','r39i_identity_lane':{'preholdout':len(pre),'holdout_2526':len(hp),'fixed100_rows':len(fixed),'fixed100_identity_sha256':sha},'transfermarkt':{'top5_games':len(games),'complete_games':len(complete),'lineup_rows':lineup_rows,'lineup_type_counts':dict(type_counts),'eligible_appearance_rows':app_rows,'bad_appearance_rows':app_bad},'feature_eligible':{'all':len(features),'preholdout':len(fpre),'holdout_2526':sum(m['identity'] in features for m in hp),'fixed100':len(ffixed)},'ineligible_reasons':dict(reasons),'strict_lag_violations':violations,'feature_summary':summary,'audit_access':reg['no_target_label_contract'],'hard_limits':reg['hard_limits']}
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'feature_coverage_r39l.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:out[k] for k in ('status','r39i_identity_lane','transfermarkt','feature_eligible','ineligible_reasons','strict_lag_violations','audit_access')},indent=2))
if __name__=='__main__':main()
