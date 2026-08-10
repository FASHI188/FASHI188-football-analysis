#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,sys
from datetime import datetime,timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'
sys.path.insert(0,str(HERE));sys.path.insert(0,str(R39L))
import audit_recency_features_r39m as rm
import audit_lineup_attack_features_r39l as lf


def sha_file(p:Path):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def static_rows(market_dir,games_path,lineups_path,appearances_path,r39i,r39l):
    games,starters,complete,mapped,_,_=lf.rebuild_mapping(market_dir,games_path,lineups_path,r39i)
    pidx,cidx,_,bad=lf.load_appearances(appearances_path,set(r39l['feature_contract']['eligible_appearance_competitions']))
    if bad:raise RuntimeError(f'bad static appearance rows {bad}')
    out={}
    for m in mapped:
        z,reason=lf.feature_row(m,starters,pidx,cidx,r39l)
        if z is None:raise RuntimeError(f'static feature drift {m["identity"]}: {reason}')
        if z['strict_lag_violation']:raise RuntimeError(f'static strict-lag violation {m["identity"]}')
        out[m['identity']]={'identity':m['identity'],'season':m['season'],'div':m['div'],'date':str(m['target_date']),'q':[float(v) for v in m['qclose']],'x':[float(v) for v in z['x']]}
    return games,starters,mapped,out

def dynamic_rows(mapped,starters,appearances_path,reg):
    pidx,cidx,_,bad=rm.load_recency_appearances(appearances_path,set(reg['recency_contract']['eligible_appearance_competitions']))
    if bad:raise RuntimeError(f'bad recency appearance rows {bad}')
    allout={}
    for half in reg['recency_contract']['half_life_days_candidates']:
        cache={};out={}
        for m in mapped:
            z,reason=rm.feature_row(m,starters,pidx,cidx,int(half),reg,cache)
            if z is None:raise RuntimeError(f'dynamic feature drift h={half} {m["identity"]}: {reason}')
            if z['strict_lag_violation']:raise RuntimeError(f'dynamic strict-lag violation h={half} {m["identity"]}')
            out[m['identity']]=[float(v) for v in z['x']]
        allout[int(half)]=out
    return allout

def vector_hash(order,source):
    lines=[]
    for i in order:lines.append(i+'|'+','.join(format(float(v),'.17g') for v in source[i]))
    return hashlib.sha256(('\n'.join(lines)+'\n').encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--r39l-source-registration',type=Path,required=True);ap.add_argument('--r39i-registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--appearances',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());r39l=json.loads(a.r39l_source_registration.read_text());r39i=json.loads(a.r39i_registration.read_text())
    games,starters,mapped,static=static_rows(a.market_dir,a.games,a.lineups,a.appearances,r39i,r39l);dyn=dynamic_rows(mapped,starters,a.appearances,reg)
    order=sorted(static,key=lambda i:(static[i]['date'],static[i]['div'],i));pre=[i for i in order if static[i]['season']!='2526'];hold=[i for i in order if static[i]['season']=='2526']
    if (len(order),len(pre),len(hold))!=(9434,8161,1273):raise RuntimeError(f'identity count drift {len(order)} {len(pre)} {len(hold)}')
    fixed=sorted(hold,key=lambda i:lf.htxt(f"{reg['identity_lane']['fixed100_seed']}|{i}"))[:100];fixedsha=lf.set_sha(fixed)
    if fixedsha!=reg['identity_lane']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {fixedsha}')
    for h in dyn:
        if set(dyn[h])!=set(order):raise RuntimeError(f'dynamic identity drift h={h}')
    a.out_dir.mkdir(parents=True,exist_ok=True);csvp=a.out_dir/'feature_snapshot_r39m.csv'
    fields=['identity','season','div','date','qH','qD','qA']+[f's{i}' for i in range(10)]+[f'd{h}_{i}' for h in (90,180,365) for i in range(10)]
    with csvp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for ident in order:
            s=static[ident];row={'identity':ident,'season':s['season'],'div':s['div'],'date':s['date'],'qH':s['q'][0],'qD':s['q'][1],'qA':s['q'][2]}
            row.update({f's{i}':v for i,v in enumerate(s['x'])})
            for h in (90,180,365):row.update({f'd{h}_{i}':v for i,v in enumerate(dyn[h][ident])})
            w.writerow(row)
    market_files=sorted(Path(a.market_dir).glob('*.csv'));receipt={'schema_version':'r39m-frozen-feature-snapshot-v1','status':'PASS_R39M_ZERO_LABEL_FROZEN_FEATURE_SNAPSHOT','generated_at_utc':datetime.now(timezone.utc).isoformat(),'rows':{'all':9434,'preholdout':8161,'holdout_2526':1273,'fixed100':100},'fixed100_identity_sha256':fixedsha,'snapshot_csv_sha256':sha_file(csvp),'static_feature_sha256':vector_hash(order,{i:static[i]['x'] for i in order}),'dynamic_feature_sha256_by_half_life':{str(h):vector_hash(order,dyn[h]) for h in (90,180,365)},'source_snapshot_sha256':{'games_identity_csv':sha_file(a.games),'lineups_identity_csv':sha_file(a.lineups),'appearances_prior_csv':sha_file(a.appearances),'market_files_combined':hashlib.sha256(('\n'.join(f'{p.name}:{sha_file(p)}' for p in market_files)+'\n').encode()).hexdigest()},'zero_label_contract':{'football_data_FTR_accessed':0,'football_data_score_values_accessed':0,'target_result_labels_accessed':0,'model_fit':0,'holdout_labels_accessed':0},'hard_limits':reg['hard_limits']};(a.out_dir/'snapshot_receipt_r39m.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
