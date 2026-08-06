#!/usr/bin/env python3
"""Research-only retrospective market-expression audit for draw-risk matches."""
from __future__ import annotations
import argparse, csv, hashlib, json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

class AuditError(RuntimeError): pass

def load(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise AuditError('JSON root must be object')
    return value

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''): h.update(block)
    return h.hexdigest()

def no_vig3(h:float,d:float,a:float)->tuple[float,float,float]:
    x=np.array([h,d,a],float)
    if not np.isfinite(x).all() or np.any(x<=1): raise AuditError('invalid 1X2 odds')
    x=1/x; x/=x.sum(); return tuple(map(float,x))

def half_profit(margin:float,line:float,odds:float)->float:
    x=margin+line
    return odds-1 if x>1e-12 else -1 if x<-1e-12 else 0

def ah_profit(margin:float,line:float,odds:float)->float:
    if odds<=1 or abs(line*4-round(line*4))>1e-8: raise AuditError('invalid AH input')
    lo=math.floor(line*2+1e-12)/2; hi=math.ceil(line*2-1e-12)/2
    return half_profit(margin,line,odds) if abs(lo-hi)<1e-12 else (half_profit(margin,lo,odds)+half_profit(margin,hi,odds))/2

def bootstrap(frame:pd.DataFrame,col:str,cfg:dict[str,Any],offset:int)->dict[str,float]:
    groups={str(k):g[col].to_numpy(float) for k,g in frame.groupby('competition_id')}
    keys=sorted(groups); rng=np.random.default_rng(int(cfg['bootstrap']['seed'])+offset); out=[]
    for _ in range(int(cfg['bootstrap']['samples'])):
        picked=rng.choice(keys,size=len(keys),replace=True)
        out.append(float(np.concatenate([groups[str(k)] for k in picked]).mean()))
    q=np.quantile(out,[0.05,0.5,0.95]); return {'p05':float(q[0]),'median':float(q[1]),'p95':float(q[2])}

def expression(frame:pd.DataFrame,name:str,cfg:dict[str,Any],offset:int)->dict[str,Any]:
    ret,odds={'draw':('return_draw','fd_AvgCD'),'under25':('return_under25','fd_AvgC<2.5'),'underdog_ah':('return_underdog_ah','underdog_ah_odds')}[name]
    x=frame[ret].to_numpy(float)
    return {'matches':len(frame),'leagues':int(frame.competition_id.nunique()),'mean_return':float(x.mean()),'profit_units':float(x.sum()),
            'positive_leagues':int(sum(g[ret].mean()>0 for _,g in frame.groupby('competition_id'))),
            'cluster_bootstrap_90':bootstrap(frame,ret,cfg,offset),'positive_return_rate':float((x>0).mean()),
            'partial_or_push_rate':float(((x>-1)&(x<=0)).mean()),'full_loss_rate':float((x<=-1+1e-12).mean()),
            'average_decimal_odds':float(frame[odds].mean())}

def prepare(raw:pd.DataFrame,cfg:dict[str,Any])->pd.DataFrame:
    req=cfg['input_contract']['required_columns']; missing=[c for c in req if c not in raw]
    if missing or raw[req].isna().any().any(): raise AuditError(f'missing/null input: {missing}')
    exp=cfg['input_contract']['expected_identity']; counts=raw.groupby('season').size().to_dict()
    if len(raw)!=exp['rows'] or raw.match_identity.nunique()!=len(raw) or raw.competition_id.nunique()!=exp['competitions'] or counts!=exp['season_rows']:
        raise AuditError(f'input identity mismatch: rows={len(raw)} comps={raw.competition_id.nunique()} seasons={counts}')
    f=raw.copy(); p=np.array([no_vig3(r.fd_AvgCH,r.fd_AvgCD,r.fd_AvgCA) for r in f.itertuples()])
    f['market_home_probability'],f['market_draw_probability'],f['market_away_probability']=p[:,0],p[:,1],p[:,2]
    io=1/f['fd_AvgC>2.5']; iu=1/f['fd_AvgC<2.5']; f['market_under25_probability']=iu/(io+iu)
    f['market_balance']=1-(f.market_home_probability-f.market_away_probability).abs()
    train=f[f.season==cfg['split_contract']['train_season']]
    for c in ['market_draw_probability','market_under25_probability','market_balance']:
        mu=float(train[c].mean()); sd=float(train[c].std(ddof=0));
        if sd<=0: raise AuditError(f'zero sd: {c}')
        f['z_'+c]=(f[c]-mu)/sd
    f['score_draw_price']=f.market_draw_probability
    f['score_draw_under']=(f.z_market_draw_probability+f.z_market_under25_probability)/2
    f['score_draw_under_balance']=(f.z_market_draw_probability+f.z_market_under25_probability+f.z_market_balance)/3
    hg=f.fd_FTHG.astype(int); ag=f.fd_FTAG.astype(int); total=hg+ag
    f['actual_draw']=(hg==ag).astype(int); f['actual_under25']=(total<=2).astype(int); f['actual_btts_no']=((hg==0)|(ag==0)).astype(int)
    f['actual_00']=((hg==0)&(ag==0)).astype(int); f['actual_11']=((hg==1)&(ag==1)).astype(int); f['actual_22']=((hg==2)&(ag==2)).astype(int)
    f['return_draw']=np.where(f.actual_draw==1,f.fd_AvgCD-1,-1); f['return_under25']=np.where(f.actual_under25==1,f['fd_AvgC<2.5']-1,-1)
    home=f.market_home_probability<f.market_away_probability; f['underdog_side']=np.where(home,'home','away')
    f['underdog_handicap']=np.where(home,f.fd_AHCh,-f.fd_AHCh); f['underdog_ah_odds']=np.where(home,f.fd_AvgCAHH,f.fd_AvgCAHA)
    if (f.underdog_handicap<-1e-12).any(): raise AuditError('underdog is not receiving handicap')
    returns=[]; wins=[]
    for r in f.itertuples():
        margin=(r.fd_FTHG-r.fd_FTAG) if r.underdog_side=='home' else (r.fd_FTAG-r.fd_FTHG)
        wins.append(int(margin>0)); returns.append(ah_profit(float(margin),float(r.underdog_handicap),float(r.underdog_ah_odds)))
    f['actual_underdog_win']=wins; f['return_underdog_ah']=returns; return f

def write_csv(path:Path,rows:list[dict[str,Any]])->None:
    if not rows: raise AuditError(f'empty output {path.name}')
    with path.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def run(input_path:Path,cfg:dict[str,Any],out:Path)->dict[str,Any]:
    digest=sha(input_path)
    if digest!=cfg['input_contract']['expected_input_sha256']: raise AuditError(f'input SHA mismatch: {digest}')
    f=prepare(pd.read_csv(input_path),cfg); split=cfg['split_contract']; policy=f[f.season==split['policy_season']]; test=f[f.season==split['test_season']]
    selectors=cfg['selector_contract']['selectors']; coverages=cfg['selector_contract']['coverage_grid']; names=cfg['expression_contract']['expressions']
    status={'schema_version':'MARKET-EXPRESSION-R1-STATUS','classification':cfg['classification'],
            'input':{'path':str(input_path),'sha256':digest,'rows':len(f),'competitions':int(f.competition_id.nunique()),'season_rows':{str(k):int(v) for k,v in f.groupby('season').size().items()},'all_required_prices_complete':True,'original_row_level_quote_timestamps':False},
            'design':{'selectors':selectors,'coverage_grid':coverages,'expressions':names,'cutoff_rule':'policy upper quantile; labels not used','selection_rule':cfg['policy_selection_gate'],'test_is_new_blind_holdout':False},
            'baselines':{},'grid':{},'hard_limits':cfg['hard_limits']}
    offset=0
    for key,season in [('train',split['train_season']),('policy',split['policy_season']),('test',split['test_season'])]:
        sub=f[f.season==season]; status['baselines'][key]={'rows':len(sub),'draw_rate':float(sub.actual_draw.mean()),'under25_rate':float(sub.actual_under25.mean()),'expressions':{}}
        for name in names: offset+=1; status['baselines'][key]['expressions'][name]=expression(sub,name,cfg,offset)
    grid=[]; selected_rows=[]
    for selector in selectors:
        status['grid'][selector]={}
        for coverage in coverages:
            cutoff=float(policy[selector].quantile(1-coverage,interpolation='higher')); ck=f'{round(coverage*100)}pct'; status['grid'][selector][ck]={'cutoff':cutoff}
            for split_name,base in [('policy',policy),('test',test)]:
                s=base[base[selector]>=cutoff].copy(); outcome={'matches':len(s),'leagues':int(s.competition_id.nunique()),'actual_draw_rate':float(s.actual_draw.mean()),
                    'actual_under25_rate':float(s.actual_under25.mean()),'actual_btts_no_rate':float(s.actual_btts_no.mean()),'actual_00_rate':float(s.actual_00.mean()),
                    'actual_11_rate':float(s.actual_11.mean()),'actual_22_rate':float(s.actual_22.mean()),'draw_or_underdog_win_rate':float(((s.actual_draw==1)|(s.actual_underdog_win==1)).mean()),
                    'mean_market_draw_probability':float(s.market_draw_probability.mean()),'mean_market_under25_probability':float(s.market_under25_probability.mean()),'mean_market_balance':float(s.market_balance.mean())}
                summaries={}
                for name in names: offset+=1; summaries[name]=expression(s,name,cfg,offset)
                losses={'draw_and_under_both_lose':float(((s.return_draw<0)&(s.return_under25<0)).mean()),'draw_and_ah_both_lose':float(((s.return_draw<0)&(s.return_underdog_ah<0)).mean()),
                        'under_and_ah_both_lose':float(((s.return_under25<0)&(s.return_underdog_ah<0)).mean()),'all_three_lose':float(((s.return_draw<0)&(s.return_under25<0)&(s.return_underdog_ah<0)).mean())}
                status['grid'][selector][ck][split_name]={'outcomes':outcome,'expressions':summaries,'return_correlation':s[['return_draw','return_under25','return_underdog_ah']].corr().fillna(0).to_dict(),'joint_losses':losses}
                for name,result in summaries.items():
                    b=result['cluster_bootstrap_90']; grid.append({'selector':selector,'coverage':coverage,'cutoff':cutoff,'split':split_name,'expression':name,**outcome,
                        'mean_return':result['mean_return'],'profit_units':result['profit_units'],'positive_leagues':result['positive_leagues'],'expression_leagues':result['leagues'],
                        'bootstrap_p05':b['p05'],'bootstrap_median':b['median'],'bootstrap_p95':b['p95'],'full_loss_rate':result['full_loss_rate'],'average_decimal_odds':result['average_decimal_odds'],**losses})
                for r in s.itertuples(): selected_rows.append({'selector':selector,'coverage':coverage,'split':split_name,'cutoff':cutoff,'gold_sample_id':r.gold_sample_id,'match_identity':r.match_identity,
                    'competition_id':r.competition_id,'season':r.season,'selector_score':getattr(r,selector),'market_draw_probability':r.market_draw_probability,'market_under25_probability':r.market_under25_probability,
                    'market_balance':r.market_balance,'home_goals':r.fd_FTHG,'away_goals':r.fd_FTAG,'return_draw':r.return_draw,'return_under25':r.return_under25,'underdog_side':r.underdog_side,
                    'underdog_handicap':r.underdog_handicap,'underdog_ah_odds':r.underdog_ah_odds,'return_underdog_ah':r.return_underdog_ah})
    gate=cfg['policy_selection_gate']; eligible=[r for r in grid if r['split']=='policy' and r['matches']>=gate['minimum_matches'] and r['expression_leagues']>=gate['minimum_leagues'] and r['positive_leagues']>=gate['minimum_positive_leagues'] and r['mean_return']>gate['minimum_mean_return'] and r['bootstrap_p05']>gate['minimum_cluster_bootstrap_lower_bound']]
    eligible.sort(key=lambda r:(-r['mean_return'],r['coverage'],selectors.index(r['selector']),names.index(r['expression']))); status['policy_eligible_candidates']=eligible; status['selected_policy_candidate']=eligible[0] if eligible else None
    if eligible:
        p=eligible[0]; row=next(r for r in grid if r['split']=='test' and r['selector']==p['selector'] and r['coverage']==p['coverage'] and r['expression']==p['expression']); g=cfg['test_gate']
        checks={'minimum_matches':row['matches']>=g['minimum_matches'],'minimum_leagues':row['expression_leagues']>=g['minimum_leagues'],'minimum_positive_leagues':row['positive_leagues']>=g['minimum_positive_leagues'],'positive_mean_return':row['mean_return']>g['minimum_mean_return'],'positive_cluster_bootstrap_lower_bound':row['bootstrap_p05']>g['minimum_cluster_bootstrap_lower_bound']}
        status['test_result']={'row':row,'checks':checks,'pass':all(checks.values())}
    else: status['test_result']={'row':None,'checks':{},'pass':False,'reason':'no_policy_candidate_passed'}
    status['status']='PASS_RETROSPECTIVE_EXECUTION_LAYER_RESCUE_R1' if status['test_result']['pass'] else 'FAIL_RETROSPECTIVE_EXECUTION_LAYER_RESCUE_R1'
    status['ruling']={'execution_layer_rescued_draw_risk':status['test_result']['pass'],'formal_promotion_allowed':False,'current_match_use_allowed':False,'ev_claim_allowed':False,'formal_weight':0,
        'double_chance_price_available':False,'btts_price_available':False,'unified_score_matrix_used':False,'interpretation':'Retrospective execution-layer rescue passed; strict-PIT confirmation still required.' if status['test_result']['pass'] else 'Changing the settlement expression did not produce a stable out-of-policy rescue.'}
    out.mkdir(parents=True,exist_ok=True); write_csv(out/'market_expression_r1_grid.csv',grid); write_csv(out/'market_expression_r1_selected_rows.csv',selected_rows)
    (out/'market_expression_r1_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest={'schema_version':'MARKET-EXPRESSION-R1-ARTIFACT-MANIFEST','files':{}}
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name!='manifest.json': manifest['files'][p.name]={'sha256':sha(p),'bytes':p.stat().st_size}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); return status

def self_test()->None:
    assert ah_profit(0,.25,2)==.5 and ah_profit(0,-.25,2)==-.5 and ah_profit(-1,.75,2)==-.5 and ah_profit(1,-.75,2)==.5 and ah_profit(1,-1,2)==0

def main()->None:
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--prereg',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--self-test',action='store_true'); args=ap.parse_args()
    if args.self_test: self_test(); print(json.dumps({'status':'PASS','self_test':True})); return
    result=run(args.input,load(args.prereg),args.out); print(json.dumps({'status':result['status'],'selected_policy_candidate':result['selected_policy_candidate'],'test_result':result['test_result']},ensure_ascii=False))
if __name__=='__main__': main()
