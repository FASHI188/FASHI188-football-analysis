from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, pathlib, sys
from collections import defaultdict
from datetime import datetime, timezone

V1_ARTIFACT_SHA='5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb'
V1_HEAD='22f639304d2e32fc952dbec2255153ee45dcd41a'
V1_ENGINE_SHA='cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318'
DEV_SHA='b8713ceed6d57ead7b2aadbb24d3154bf5cb5df0d45eef0e762b5c395d6d4fab'
EVAL_FEATURE_SHA='10620687f988ff942d1e56d372f6b7e2721aa65654f9665e982ae27232b472a9'
EVAL_LABEL_SHA='e82b612d6e6b974c5f4695f29551830beaec87804429e586cd821aef7e7dff2a'
DEV_N=1826; POST_N=5256; SCORES=((0,0),(1,1),(2,2))

def sha(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def dt(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None: raise RuntimeError('naive datetime')
    return d.astimezone(timezone.utc)

def readjl(p): return [json.loads(x) for x in pathlib.Path(p).read_text().splitlines() if x.strip()]
def writej(p,obj):
    p=pathlib.Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')
def writejl(p,rows):
    p=pathlib.Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w') as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')

def import_v1(path):
    if sha(path)!=V1_ENGINE_SHA: raise RuntimeError('V1 engine SHA mismatch')
    spec=importlib.util.spec_from_file_location('football3_v1_error_atlas_frozen',path)
    if spec is None or spec.loader is None: raise RuntimeError('cannot import frozen V1')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m

def params_from_result(path):
    d=json.loads(pathlib.Path(path).read_text())
    if d.get('scientific_status')!='MODEL_CANDIDATE_PASSED': raise RuntimeError('V1 status mismatch')
    return d['selected_parameters']

def batches(rows):
    out=[]; cur=[]; key=None
    for r in sorted(rows,key=lambda z:(dt(z['cutoff']),str(z['competition_id']),str(z['fixture_id']))):
        k=dt(r['cutoff'])
        if key is None or k==key: cur.append(r); key=k
        else: out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out

def fixture(v1,r): return v1.Fixture(str(r['fixture_id']),str(r['competition_id']),str(r['season']),dt(r['cutoff']),str(r['home_team_id']),str(r['away_team_id']))
def matrix_cell(matrix,h,a):
    for c in matrix:
        if int(c['home_goals'])==h and int(c['away_goals'])==a: return float(c['probability'])
    raise RuntimeError('score cell absent')

def pred_row(r,p,lab):
    hg,ag=int(lab['home_goals']),int(lab['away_goals']); probs=[float(p['p_home']),float(p['p_draw']),float(p['p_away'])]
    names=['home','draw','away']; actual='home' if hg>ag else 'draw' if hg==ag else 'away'; top=names[max(range(3),key=lambda i:probs[i])]
    return {'fixture_id':str(r['fixture_id']),'competition_id':str(r['competition_id']),'season':str(r['season']),'cutoff':str(r['cutoff']),'home_team_id':str(r['home_team_id']),'away_team_id':str(r['away_team_id']),'home_goals':hg,'away_goals':ag,'mu_home':float(p['mu_home']),'mu_away':float(p['mu_away']),'p_home':probs[0],'p_draw':probs[1],'p_away':probs[2],'p_0_0':matrix_cell(p['score_matrix'],0,0),'p_1_1':matrix_cell(p['score_matrix'],1,1),'p_2_2':matrix_cell(p['score_matrix'],2,2),'cold_start_bucket':str(p.get('cold_start_bucket','unknown')),'actual_class':actual,'top1':top,'pred_total':float(p['mu_home'])+float(p['mu_away']),'strength_gap':abs(math.log(max(1e-15,float(p['mu_home']))/max(1e-15,float(p['mu_away'])))),'favorite_p':max(probs),'prediction_hash':str(p['prediction_hash'])}

def replay(v1,params,dev,features,labels):
    state=v1.EngineState(params=v1.Parameters(**params)); pending=[]; devout=[]
    for batch in batches(dev):
        now=dt(batch[0]['cutoff'])
        while pending and pending[0][0]<=now:
            _,old=pending.pop(0); state.apply_batch([fixture(v1,x) for x in old],{str(x['fixture_id']):(int(x['home_goals']),int(x['away_goals'])) for x in old})
        for r in batch: devout.append(pred_row(r,state.predict(fixture(v1,r)),r))
        pending.append((max(dt(r['result_available_at']) for r in batch),batch)); pending.sort(key=lambda x:x[0])
    if pending:
        for _,old in sorted(pending,key=lambda x:x[0]): state.apply_batch([fixture(v1,x) for x in old],{str(x['fixture_id']):(int(x['home_goals']),int(x['away_goals'])) for x in old})
    pending=[]; lm={str(r['fixture_id']):r for r in labels}; post=[]
    for batch in batches(features):
        now=dt(batch[0]['cutoff'])
        while pending and pending[0][0]<=now:
            _,old=pending.pop(0); state.apply_batch([fixture(v1,x) for x in old],{str(x['fixture_id']):(int(lm[str(x['fixture_id'])]['home_goals']),int(lm[str(x['fixture_id'])]['away_goals'])) for x in old})
        for r in batch:
            lab=lm.get(str(r['fixture_id']))
            if lab is None: raise RuntimeError('missing POST_VIEW label')
            post.append(pred_row(r,state.predict(fixture(v1,r)),lab))
        pending.append((max(dt(lm[str(r['fixture_id'])]['result_available_at']) for r in batch),batch)); pending.sort(key=lambda x:x[0])
    return devout,post

def outcome_vec(r): return [1.0 if r['actual_class']==x else 0.0 for x in ('home','draw','away')]
def ll(r): return -math.log(max(1e-15,r['p_'+r['actual_class']]))
def brier(r):
    y=outcome_vec(r); p=[r['p_home'],r['p_draw'],r['p_away']]; return sum((a-b)**2 for a,b in zip(p,y))
def rps(r):
    y=outcome_vec(r); p=[r['p_home'],r['p_draw'],r['p_away']]; return ((p[0]-y[0])**2+(p[0]+p[1]-y[0]-y[1])**2)/2

def calibration(rows,cls):
    key='p_'+cls; bins=[]; e=0.0; n=len(rows)
    for i in range(10):
        lo=i/10; hi=(i+1)/10; z=[r for r in rows if lo<=r[key]<(hi if i<9 else 1.0000001)]
        if not z: continue
        mp=sum(r[key] for r in z)/len(z); ar=sum(r['actual_class']==cls for r in z)/len(z); e+=len(z)/n*abs(mp-ar)
        bins.append({'lo':lo,'hi':hi,'n':len(z),'mean_p':mp,'actual_rate':ar,'gap_pred_minus_actual':mp-ar})
    mp=sum(r[key] for r in rows)/n; ar=sum(r['actual_class']==cls for r in rows)/n
    return {'ece':e,'mean_p':mp,'actual_rate':ar,'bias_pred_minus_actual':mp-ar,'bins':bins}

def base_metrics(rows):
    n=len(rows); ls=[ll(r) for r in rows]
    return {'n':n,'logloss':sum(ls)/n,'brier':sum(brier(r) for r in rows)/n,'rps':sum(rps(r) for r in rows)/n,'top1':sum(r['top1']==r['actual_class'] for r in rows)/n,'mean_pred_goals':{'home':sum(r['mu_home'] for r in rows)/n,'away':sum(r['mu_away'] for r in rows)/n},'mean_actual_goals':{'home':sum(r['home_goals'] for r in rows)/n,'away':sum(r['away_goals'] for r in rows)/n}}

def group_report(rows,keyfn,min_n=1):
    overall=base_metrics(rows); tot=sum(ll(r) for r in rows); d=defaultdict(list); out={}
    for r in rows: d[str(keyfn(r))].append(r)
    for k,z in sorted(d.items()):
        if len(z)<min_n: continue
        m=base_metrics(z); m['share_total_logloss']=sum(ll(r) for r in z)/tot; m['excess_logloss_contribution']=max(0.0,m['logloss']-overall['logloss'])*len(z)/len(rows); out[k]=m
    return out

def entropy_gain(rows,keyfn):
    groups=defaultdict(list)
    for r in rows: groups[str(keyfn(r))].append(r)
    cur=sum(ll(r) for r in rows); oracle=0.0
    for z in groups.values():
        freq={c:sum(r['actual_class']==c for r in z)/len(z) for c in ('home','draw','away')}
        oracle+=sum(-math.log(max(1e-15,freq[r['actual_class']])) for r in z)
    return max(0.0,(cur-oracle)/len(rows))

def total_bin(r):
    x=r['pred_total']; return '<2.0' if x<2 else '2.0-2.5' if x<2.5 else '2.5-3.0' if x<3 else '3.0-3.5' if x<3.5 else '>=3.5'
def strength_bin(r):
    x=r['strength_gap']; return '<0.10' if x<.10 else '0.10-0.25' if x<.25 else '0.25-0.50' if x<.50 else '>=0.50'
def favorite_bin(r):
    x=r['favorite_p']; return '<0.45' if x<.45 else '0.45-0.55' if x<.55 else '0.55-0.65' if x<.65 else '>=0.65'

def draw_total_bin_gain(rows):
    groups=defaultdict(list)
    for r in rows: groups[total_bin(r)].append(r)
    old=sum(ll(r) for r in rows); new=0.0
    for z in groups.values():
        target=sum(r['actual_class']=='draw' for r in z)/len(z)
        for r in z:
            pd=r['p_draw']; scale=(1-target)/max(1e-15,1-pd); pp={'draw':target,'home':r['p_home']*scale,'away':r['p_away']*scale}; new+=-math.log(max(1e-15,pp[r['actual_class']]))
    return max(0.0,(old-new)/len(rows))

def atlas(rows):
    overall=base_metrics(rows); cal={c:calibration(rows,c) for c in ('home','draw','away')}; mean_ece=sum(x['ece'] for x in cal.values())/3; tot=sum(ll(r) for r in rows); per_class={}
    for c in ('home','draw','away'):
        z=[r for r in rows if r['actual_class']==c]; m=base_metrics(z); m['share_total_logloss']=sum(ll(r) for r in z)/tot; m['class_probability_calibration']=cal[c]; per_class[c]=m
    score={}
    for h,a in SCORES:
        key=f'{h}-{a}'; pk=f'p_{h}_{a}'; actual=sum(r['home_goals']==h and r['away_goals']==a for r in rows)/len(rows); pred=sum(r[pk] for r in rows)/len(rows); score[key]={'mean_predicted_probability':pred,'actual_frequency':actual,'gap_actual_minus_predicted':actual-pred}
    low_pred=sum(sum(r[f'p_{h}_{a}'] for h,a in SCORES) for r in rows)/len(rows); low_act=sum((r['home_goals'],r['away_goals']) in SCORES for r in rows)/len(rows)
    conf={a:{b:0 for b in ('home','draw','away')} for a in ('home','draw','away')}
    for r in rows: conf[r['actual_class']][r['top1']]+=1
    opp={'multiclass_calibration_ll_proxy':entropy_gain(rows,lambda r:f"{r['top1']}|{favorite_bin(r)}"),'draw_total_structure_ll_proxy':draw_total_bin_gain(rows),'team_strength_ll_proxy':entropy_gain(rows,lambda r:f"{strength_bin(r)}|{r['top1']}"),'competition_season_ll_proxy':entropy_gain(rows,lambda r:f"{r['competition_id']}|{r['season']}")}
    return {'overall':overall,'per_actual_class':per_class,'calibration':{'mean_class_ece':mean_ece,'classes':cal},'draw':{'mean_predicted':cal['draw']['mean_p'],'actual_rate':cal['draw']['actual_rate'],'gap_actual_minus_predicted':cal['draw']['actual_rate']-cal['draw']['mean_p'],'top1_draw_pick_rate':sum(r['top1']=='draw' for r in rows)/len(rows)},'exact_draw_scores':score,'low_score_draw_cells':{'cells':['0-0','1-1','2-2'],'mean_predicted_probability':low_pred,'actual_frequency':low_act,'gap_actual_minus_predicted':low_act-low_pred},'groups':{'predicted_total_goals':group_report(rows,total_bin),'strength_gap':group_report(rows,strength_bin),'favorite_confidence':group_report(rows,favorite_bin),'cold_start':group_report(rows,lambda r:r['cold_start_bucket']),'competition_season_n_ge_100':group_report(rows,lambda r:f"{r['competition_id']}|{r['season']}",100)},'top1_confusion_actual_rows_pred_cols':conf,'diagnostic_ll_opportunity_proxies_nonadditive':opp}

def decision(dev,post):
    same=lambda a,b:(a==0 or b==0 or (a>0)==(b>0)); dd=dev['draw']['gap_actual_minus_predicted']; pd=post['draw']['gap_actual_minus_predicted']; dl=dev['low_score_draw_cells']['gap_actual_minus_predicted']; pl=post['low_score_draw_cells']['gap_actual_minus_predicted']
    joint=abs(dd)>=.01 and abs(pd)>=.01 and same(dd,pd) and abs(dl)>=.01 and abs(pl)>=.01 and same(dl,pl) and dev['diagnostic_ll_opportunity_proxies_nonadditive']['draw_total_structure_ll_proxy']>=.001 and post['diagnostic_ll_opportunity_proxies_nonadditive']['draw_total_structure_ll_proxy']>=.001
    biases=[]
    for c in ('home','draw','away'):
        a=dev['calibration']['classes'][c]['bias_pred_minus_actual']; b=post['calibration']['classes'][c]['bias_pred_minus_actual']; biases.append(abs(a)>=.01 and abs(b)>=.01 and same(a,b))
    cal=(not joint and dev['calibration']['mean_class_ece']>=.02 and post['calibration']['mean_class_ece']>=.02 and sum(biases)>=2 and dev['diagnostic_ll_opportunity_proxies_nonadditive']['multiclass_calibration_ll_proxy']>=.001 and post['diagnostic_ll_opportunity_proxies_nonadditive']['multiclass_calibration_ll_proxy']>=.001)
    rec='V1_JOINT_SCORE_CHALLENGER' if joint else 'V1_2_MULTICLASS_CALIBRATION_LAYER' if cal else 'STOP_NO_STABLE_ERROR_TARGET'; keys=['multiclass_calibration_ll_proxy','draw_total_structure_ll_proxy','team_strength_ll_proxy','competition_season_ll_proxy']
    ranking=sorted([{'source':k,'development':dev['diagnostic_ll_opportunity_proxies_nonadditive'][k],'postview':post['diagnostic_ll_opportunity_proxies_nonadditive'][k],'stable_min':min(dev['diagnostic_ll_opportunity_proxies_nonadditive'][k],post['diagnostic_ll_opportunity_proxies_nonadditive'][k])} for k in keys],key=lambda x:(-x['stable_min'],x['source']))
    return {'recommendation':rec,'joint_score_rule_pass':joint,'calibration_rule_pass':cal,'stable_error_source_ranking':ranking,'decision_contract':{'joint_requires_draw_gap_abs_each':.01,'joint_requires_low_score_gap_abs_each':.01,'proxy_gain_each':.001,'calibration_mean_ece_each':.02,'calibration_same_sign_classes_min':2}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--v1-engine',type=pathlib.Path,required=True); ap.add_argument('--v1-result',type=pathlib.Path,required=True); ap.add_argument('--development',type=pathlib.Path,required=True); ap.add_argument('--features',type=pathlib.Path,required=True); ap.add_argument('--labels',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True); x=ap.parse_args()
    for p,s,n in [(x.development,DEV_SHA,DEV_N),(x.features,EVAL_FEATURE_SHA,POST_N),(x.labels,EVAL_LABEL_SHA,POST_N)]:
        if sha(p)!=s or sum(1 for _ in p.open())!=n: raise RuntimeError('dataset identity mismatch '+str(p))
    v1=import_v1(x.v1_engine); params=params_from_result(x.v1_result); dev=readjl(x.development); feat=readjl(x.features); labs=readjl(x.labels); dp,pp=replay(v1,params,dev,feat,labs)
    if len(dp)!=DEV_N or len(pp)!=POST_N: raise RuntimeError('prediction n mismatch')
    x.out.mkdir(parents=True,exist_ok=True); writejl(x.out/'development_v1_predictions.jsonl',dp); writejl(x.out/'postview_v1_predictions.jsonl',pp); da,pa=atlas(dp),atlas(pp); dec=decision(da,pa)
    result={'schema_version':'football3-v1-error-atlas-v1','status':'COMPLETE_READ_ONLY_DIAGNOSTIC','v1_head':V1_HEAD,'v1_artifact_id':9732754224,'v1_artifact_digest':'sha256:'+V1_ARTIFACT_SHA,'v1_engine_sha256':V1_ENGINE_SHA,'v1_parameters':params,'development_n':DEV_N,'postview_n':POST_N,'postview_label_status':'ALREADY_UNSEALED_POST_VIEW_DIAGNOSTIC','not_blind':True,'formal_weight':0,'formal_enablement':False,'development':da,'postview':pa,'decision':dec}
    writej(x.out/'error_atlas.json',result); writej(x.out/'decision.json',dec); print(json.dumps({'status':result['status'],'recommendation':dec['recommendation']}))
if __name__=='__main__': main()
