#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,csv,hashlib,json,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'
sys.path.insert(0,str(R39L))
import audit_lineup_attack_features_r39l as lf


def sha_file(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def parse_date(s):return datetime.strptime(str(s).strip()[:10],'%Y-%m-%d').date()
def finite_positive(v):
    try:x=float(str(v).strip())
    except:return None
    return x if math.isfinite(x) and x>0 else None

def load_valuations(path):
    raw=defaultdict(list);total=kept=bad=0
    need={'player_id','date','market_value_in_eur'}
    with Path(path).open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
        rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
        if not need<=hdr:raise RuntimeError(f'valuations missing {sorted(need-hdr)}')
        for r in rd:
            total+=1;pid=str(r.get('player_id','')).strip();v=finite_positive(r.get('market_value_in_eur'))
            try:d=parse_date(r.get('date',''))
            except:bad+=1;continue
            if not pid or v is None:bad+=1;continue
            raw[pid].append((d.toordinal(),float(v)));kept+=1
    out={}
    for pid,vals in raw.items():
        vals.sort(key=lambda z:z[0]);out[pid]=(np.asarray([z[0] for z in vals],dtype=np.int32),np.asarray([z[1] for z in vals],dtype=float))
    return out,{'total_rows':total,'kept_rows':kept,'bad_rows':bad,'players_with_history':len(out)}

def prior_value(index,pid,target_date):
    x=index.get(str(pid))
    if x is None:return None,None
    dates,vals=x;t=target_date.toordinal();i=int(np.searchsorted(dates,t,side='left'))
    if i<=0:return None,None
    d=int(dates[i-1]);v=float(vals[i-1])
    if not d<t:raise RuntimeError('strict valuation lag violation')
    return v,d

def team_features(players,target_date,index,min_valued):
    vals=[];maxd=None
    for pid in players:
        v,d=prior_value(index,pid,target_date)
        if v is None:continue
        vals.append(v);maxd=d if maxd is None or d>maxd else maxd
    n=len(vals)
    if n<min_valued:return None,{'valued':n,'coverage':n/11.0,'max_date':maxd}
    vals=np.asarray(sorted(vals,reverse=True),dtype=float);logs=np.log1p(vals)
    top=vals[:4];bottom=vals[-4:];toplogs=np.log1p(top);bottomlogs=np.log1p(bottom);total=float(vals.sum())
    out={'mean_log':float(logs.mean()),'top4_mean_log':float(toplogs.mean()),'bottom4_mean_log':float(bottomlogs.mean()),'top4_share':float(top.sum()/total),'coverage':float(n/11.0),'valued':n,'max_date':maxd}
    return out,out

def build_mapping(market_dir,games_path,lineups_path,r39i):
    games,starters,complete,mapped,type_counts,lineup_rows=lf.rebuild_mapping(market_dir,games_path,lineups_path,r39i)
    return games,starters,complete,mapped,type_counts,lineup_rows

def snapshot_hash(rows):
    lines=[]
    for r in sorted(rows,key=lambda z:z['identity']):lines.append(r['identity']+'|'+','.join(format(float(v),'.17g') for v in r['x']))
    return sha_text('\n'.join(lines)+'\n')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--r39i-registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--valuations',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());r39i=json.loads(a.r39i_registration.read_text());games,starters,complete,mapped,type_counts,lineup_rows=build_mapping(a.market_dir,a.games,a.lineups,r39i);lane=reg['identity_lane']
    pre_all=[m for m in mapped if m['season']!='2526'];hold_all=[m for m in mapped if m['season']=='2526']
    if (len(pre_all),len(hold_all))!=(lane['complete_preholdout_rows'],lane['complete_2526_rows']):raise RuntimeError(f'identity lane drift {len(pre_all)} {len(hold_all)}')
    fixed_all=sorted(hold_all,key=lambda r:lf.htxt(f"{lane['fixed100_seed']}|{r['identity']}"))[:lane['fixed100_rows']];fixedsha=lf.set_sha([r['identity'] for r in fixed_all])
    if fixedsha!=lane['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 identity drift {fixedsha}')
    vidx,vmeta=load_valuations(a.valuations);minv=int(reg['valuation_table']['minimum_valued_starters_per_team']);rows=[];reasons=Counter();strict_viol=0;coverage=[]
    for m in mapped:
        hxi=starters.get((m['tm_game_id'],m['home_club_id']),set());axi=starters.get((m['tm_game_id'],m['away_club_id']),set())
        if len(hxi)!=11 or len(axi)!=11:reasons['bad_current_xi']+=1;continue
        hf,hd=team_features(hxi,m['target_date'],vidx,minv);af,ad=team_features(axi,m['target_date'],vidx,minv)
        coverage.append((hd['coverage'],ad['coverage']))
        if hf is None or af is None:reasons['insufficient_prior_valuations']+=1;continue
        t=m['target_date'].toordinal();mx=max(x for x in (hf['max_date'],af['max_date']) if x is not None)
        if not mx<t:strict_viol+=1;continue
        q=m['qclose'];x=[abs(float(q[0]-q[2])),lf.entropy(q),hf['mean_log'],af['mean_log'],abs(hf['mean_log']-af['mean_log']),hf['top4_mean_log'],af['top4_mean_log'],abs(hf['top4_mean_log']-af['top4_mean_log']),hf['bottom4_mean_log'],af['bottom4_mean_log'],abs(hf['bottom4_mean_log']-af['bottom4_mean_log']),hf['top4_share'],af['top4_share'],min(hf['coverage'],af['coverage'])]
        if len(x)!=14 or any(not math.isfinite(float(v)) for v in x):reasons['nonfinite_feature']+=1;continue
        rows.append({'identity':m['identity'],'season':m['season'],'div':m['div'],'date':str(m['target_date']),'q':[float(v) for v in q],'x':[float(v) for v in x],'home_valued':hf['valued'],'away_valued':af['valued'],'max_valuation_ordinal':mx})
    eligible={r['identity']:r for r in rows};pre=[r for r in rows if r['season']!='2526'];hold=[r for r in rows if r['season']=='2526'];fixed_ids={m['identity'] for m in fixed_all};fixed=[eligible[i] for i in fixed_ids if i in eligible]
    gate=reg['snapshot_gate'];passed=(len(pre)>=int(gate['minimum_feature_eligible_preholdout_rows']) and len(fixed)==int(gate['fixed100_feature_eligible_rows_required']) and strict_viol<=int(gate['strict_lag_violations_max']) and not any(k=='nonfinite_feature' and v>0 for k,v in reasons.items()))
    rows.sort(key=lambda r:(r['date'],r['div'],r['identity']));a.out_dir.mkdir(parents=True,exist_ok=True);csvp=a.out_dir/'feature_snapshot_r39n.csv';fields=['identity','season','div','date','qH','qD','qA','home_valued','away_valued']+[f'x{i}' for i in range(14)]
    with csvp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            z={'identity':r['identity'],'season':r['season'],'div':r['div'],'date':r['date'],'qH':r['q'][0],'qD':r['q'][1],'qA':r['q'][2],'home_valued':r['home_valued'],'away_valued':r['away_valued']};z.update({f'x{i}':v for i,v in enumerate(r['x'])});w.writerow(z)
    by_season=Counter(r['season'] for r in rows);by_div=Counter(r['div'] for r in rows);cov=np.asarray(coverage,dtype=float) if coverage else np.empty((0,2));receipt={'schema_version':'r39n-zero-label-valuation-snapshot-v1','status':'PASS_R39N_ZERO_LABEL_VALUATION_SNAPSHOT' if passed else reg['zero_label_snapshot_gate']['if_fail'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'identity_lane_before_valuation_filter':{'preholdout':len(pre_all),'holdout_2526':len(hold_all),'fixed100':len(fixed_all),'fixed100_identity_sha256':fixedsha},'feature_eligible':{'all':len(rows),'preholdout':len(pre),'holdout_2526':len(hold),'fixed100':len(fixed)},'eligible_by_season':dict(by_season),'eligible_by_division':dict(by_div),'ineligible_reasons':dict(reasons),'valuation_table':vmeta,'valuation_coverage':{'home_mean':float(cov[:,0].mean()) if len(cov) else None,'away_mean':float(cov[:,1].mean()) if len(cov) else None,'minimum_observed':float(cov.min()) if len(cov) else None},'strict_valuation_lag_violations':strict_viol,'feature_vector_sha256':snapshot_hash(rows),'snapshot_csv_sha256':sha_file(csvp),'source_snapshot_sha256':{'games_identity_csv':sha_file(a.games),'lineups_identity_csv':sha_file(a.lineups),'valuations_prior_csv':sha_file(a.valuations),'market_files_combined':sha_text('\n'.join(f'{p.name}:{sha_file(p)}' for p in sorted(Path(a.market_dir).glob('*.csv')))+'\n')},'zero_label_contract':reg['no_target_label_contract'],'hard_limits':reg['hard_limits']};(a.out_dir/'snapshot_receipt_r39n.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
