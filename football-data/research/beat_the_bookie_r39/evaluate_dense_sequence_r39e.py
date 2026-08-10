#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,importlib.util,json,math,random,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')


def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def canonical_sha(o)->str:return sha_text(json.dumps(o,sort_keys=True,separators=(',',':')))

def load_parent(path:Path):
    spec=importlib.util.spec_from_file_location('r39c_parent',path)
    if spec is None or spec.loader is None:raise RuntimeError('cannot import R39C parent')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def parse_mapping(header):
    m={}
    for i,name in enumerate(header[3:],3):
        z=COL_RE.match(name)
        if z:m[(int(z.group(2)),int(z.group(3)),z.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError(f'odds mapping mismatch {len(m)}')
    return m

def dct6(x):
    x=np.asarray(x,dtype=float);N=len(x);out=[]
    n=np.arange(N,dtype=float)
    for k in range(6):
        basis=np.cos((math.pi/N)*(n+0.5)*k)
        scale=math.sqrt(1.0/N) if k==0 else math.sqrt(2.0/N)
        out.append(float(scale*np.dot(x,basis)))
    return out

def sequence_features(row,mapping,parent):
    core=None
    for suffix in range(47,71):
        books=set()
        for b in range(1,33):
            vals=[row[mapping[(b,suffix,o)]] for o in OUTCOMES]
            if all(parent.valid(v) for v in vals):books.add(b)
        core=books if core is None else core & books
    if core is None or len(core)<3:raise RuntimeError(f'core bookmaker count below 3: {len(core or set())}')
    draw_logit=[];ha_log_ratio=[];entropy=[];draw_disp=[]
    for suffix in range(47,71):
        per=[]
        for b in sorted(core):
            per.append(parent.devig(*(row[mapping[(b,suffix,o)]] for o in OUTCOMES)))
        arr=np.vstack(per);q=arr.mean(axis=0)
        draw_logit.append(parent.logit(float(q[1])))
        ha_log_ratio.append(math.log(max(float(q[0]),1e-8)/max(float(q[2]),1e-8)))
        entropy.append(parent.entropy(q))
        draw_disp.append(float(arr[:,1].std(ddof=0)))
    features=[]
    for channel in (draw_logit,ha_log_ratio,entropy,draw_disp):features.extend(dct6(channel))
    features.append(math.log1p(len(core)))
    assert len(features)==25
    return features,len(core)

def load_sequence_features(source_dir:Path,eligible_ids:set[str],parent):
    seq={};core_counts=[]
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        p=source_dir/source.replace('.csv.gz','_no_scores.csv.gz')
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            rd=csv.reader(f);header=next(rd)
            assert header[:3]==['match_id','match_date','match_time']
            assert 'score_home' not in header and 'score_away' not in header
            mapping=parse_mapping(header)
            for row in rd:
                if len(row)!=len(header) or not row[0].strip():continue
                identity=f'{source}|{row[0].strip()}'
                if identity not in eligible_ids:continue
                feat,n=sequence_features(row,mapping,parent);seq[identity]=feat;core_counts.append(n)
    if set(seq)!=eligible_ids:raise RuntimeError(f'sequence identity mismatch {len(seq)} vs {len(eligible_ids)}')
    return seq,{'min':min(core_counts),'max':max(core_counts),'mean':sum(core_counts)/len(core_counts)}

def outcome(hg,ag):return 0 if hg>ag else 1 if hg==ag else 2

def cluster_bootstrap(dates,base_dec,cand_dec,actual,samples,seed):
    groups=defaultdict(list)
    for i,d in enumerate(dates):groups[d].append(i)
    keys=sorted(groups);rng=random.Random(seed);diff=[]
    for _ in range(samples):
        chosen=[keys[rng.randrange(len(keys))] for _j in range(len(keys))]
        idx=[i for k in chosen for i in groups[k]]
        b=sum(int(base_dec[i]==actual[i]) for i in idx)/len(idx)
        c=sum(int(cand_dec[i]==actual[i]) for i in idx)/len(idx)
        diff.append(c-b)
    diff.sort()
    def q(frac):return diff[min(len(diff)-1,max(0,int(frac*(len(diff)-1))))]
    return {'clusters':len(keys),'samples':len(diff),'seed':seed,'mean':sum(diff)/len(diff),'p05':q(.05),'p50':q(.50),'p95':q(.95)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--prereg',type=Path,required=True)
    ap.add_argument('--model-registration',type=Path,required=True)
    ap.add_argument('--sanitized-dir',type=Path,required=True)
    ap.add_argument('--original-dir',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    pre=json.loads(a.prereg.read_text());model_reg=json.loads(a.model_registration.read_text())
    assert pre['status']=='PRE_REGISTERED_BEFORE_R39E_THIRD100_LABEL_ACCESS'
    assert model_reg['status']=='MODEL_REPRESENTATION_FROZEN_BEFORE_THIRD100_IDENTITY_LOCK'
    assert pre['models']['r39e']['threshold_selection']=='forbidden'
    parent=load_parent(a.parent_code)

    rows,_=parent.load_feature_rows(a.sanitized_dir,{})
    start=datetime.fromisoformat(pre['identity_binding']['holdout_start'])
    training=[x for x in rows.values() if x['dt']<start]
    holdout=[x for x in rows.values() if x['dt']>=start]
    assert len(training)==pre['identity_binding']['training_eligible_rows']
    assert len(holdout)==pre['identity_binding']['holdout_eligible_rows']
    ordered=sorted(holdout,key=lambda x:sha_text(f'51139|{x["identity"]}'))
    first,second,third=ordered[:100],ordered[100:200],ordered[200:300]
    def setsha(xs):return sha_text('\n'.join(sorted(x['identity'] for x in xs))+'\n')
    s1,s2,s3=setsha(first),setsha(second),setsha(third)
    ib=pre['identity_binding']
    assert s1==ib['first100_identity_sha256'];assert s2==ib['second100_identity_sha256'];assert s3==ib['third100_identity_sha256']
    assert not ({x['identity'] for x in first+second}&{x['identity'] for x in third})

    eligible_ids=set(rows)
    seq,core_diag=load_sequence_features(a.sanitized_dir,eligible_ids,parent)
    assert core_diag['min']>=3

    training_ids={x['identity'] for x in training}
    labels,training_access,holdout_before=parent.read_training_labels(a.original_dir,training_ids,start)
    assert holdout_before==0 and training_access==len(training)==len(labels)
    y=np.asarray([1 if labels[x['identity']][0]==labels[x['identity']][1] else 0 for x in training],dtype=float)
    Xc=np.asarray([x['features'] for x in training],dtype=float)
    Xe=np.asarray([x['features']+seq[x['identity']] for x in training],dtype=float)
    assert Xc.shape==(49511,15) and Xe.shape==(49511,40)
    Xcs,mc,sc=parent.standardize(Xc);Xes,me,se=parent.standardize(Xe)
    bc,dc=parent.fit_logistic(Xcs,y,l2=1.0,max_iter=50,tol=1e-8)
    be,de=parent.fit_logistic(Xes,y,l2=1.0,max_iter=50,tol=1e-8)
    freeze={
      'schema':'r39e-dense-sequence-model-freeze','identity_set_sha256':s3,
      'first100_identity_sha256':s1,'second100_identity_sha256':s2,
      'training_rows':len(training),'training_draws':int(y.sum()),'third100_labels_accessed_before_freeze':0,'first200_holdout_labels_accessed':0,
      'r39c_feature_count':15,'r39e_feature_count':40,'sequence_feature_count':25,'sequence_core_bookmaker_diagnostics_all_eligible':core_diag,
      'r39c_mean':mc.tolist(),'r39c_std':sc.tolist(),'r39c_beta':bc.tolist(),'r39c_diagnostics':dc,
      'r39e_mean':me.tolist(),'r39e_std':se.tolist(),'r39e_beta':be.tolist(),'r39e_diagnostics':de,'numpy_version':np.__version__}
    freeze['model_parameter_sha256']=canonical_sha(freeze)
    fp=a.out_dir/'model_parameter_freeze_r39e.json';fp.write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    assert fp.exists() and fp.stat().st_size>0

    third_ids={x['identity'] for x in third}
    test_labels,test_access=parent.read_test_labels_after_freeze(a.original_dir,third_ids)
    assert test_access==100 and len(test_labels)==100
    ts=sorted(third,key=lambda z:(z['dt'],z['identity']))
    Xct=np.asarray([x['features'] for x in ts],dtype=float);Xet=np.asarray([x['features']+seq[x['identity']] for x in ts],dtype=float)
    pc=parent.predict_logistic((Xct-mc)/sc,bc);pe=parent.predict_logistic((Xet-me)/se,be)
    market=[];r39c=[];r39e=[];actual=[]
    for i,x in enumerate(ts):
        q=np.asarray(x['q1'],dtype=float);market.append(q);r39c.append(parent.threeway(q,pc[i]));r39e.append(parent.threeway(q,pe[i]))
        actual.append(outcome(*test_labels[x['identity']]))
    mm=parent.metrics(market,actual);mcx=parent.metrics(r39c,actual);mex=parent.metrics(r39e,actual)
    aucm=parent.auc_draw(market,actual);aucc=parent.auc_draw(r39c,actual);auce=parent.auc_draw(r39e,actual)
    md=[int(np.argmax(p)) for p in market];cd=[int(np.argmax(p)) for p in r39c];ed=[int(np.argmax(p)) for p in r39e]
    dates=[x['dt'].date().isoformat() for x in ts]
    boot_m=cluster_bootstrap(dates,md,ed,actual,5000,511392)
    boot_c=cluster_bootstrap(dates,cd,ed,actual,5000,511393)
    prob_gate={
      'r39e_log_loss_strictly_better_than_r39c':mex['log_loss']<mcx['log_loss'],
      'r39e_brier_strictly_better_than_r39c':mex['brier']<mcx['brier'],
      'r39e_rps_strictly_better_than_r39c':mex['rps']<mcx['rps'],
      'r39e_draw_auc_strictly_better_than_r39c':auce is not None and aucc is not None and auce>aucc}
    decision_gate={
      'r39e_accuracy_strictly_better_than_market':mex['accuracy']>mm['accuracy'],
      'r39e_accuracy_strictly_better_than_r39c':mex['accuracy']>mcx['accuracy'],
      'r39e_predicted_draw_count_at_least_one':mex['predicted_draw_count']>=1,
      'r39e_draw_f1_strictly_better_than_r39c':mex['draw_f1']>mcx['draw_f1']}
    uncertainty={'cluster_bootstrap_R39E_minus_market_accuracy_p05_positive':boot_m['p05']>0}
    prob_pass=all(prob_gate.values());decision_pass=all(decision_gate.values());uncertainty_pass=all(uncertainty.values())
    overall=prob_pass and decision_pass and uncertainty_pass
    status='PASS_R39E_DENSE_SEQUENCE_CHALLENGER_EXPLORATION_ONLY' if overall else 'NO_R39E_DENSE_SEQUENCE_INCREMENT_THIRD100_EXPLORATION_ONLY'
    discord={
      'r39e_correct_market_wrong':sum(int(e==y and m!=y) for e,m,y in zip(ed,md,actual)),
      'market_correct_r39e_wrong':sum(int(m==y and e!=y) for e,m,y in zip(ed,md,actual)),
      'r39e_correct_r39c_wrong':sum(int(e==y and c!=y) for e,c,y in zip(ed,cd,actual)),
      'r39c_correct_r39e_wrong':sum(int(c==y and e!=y) for e,c,y in zip(ed,cd,actual))}
    per=[]
    for x,y,pm,pcp,pep,dm,dcx,dex in zip(ts,actual,market,r39c,r39e,md,cd,ed):
        per.append({'identity':x['identity'],'match_datetime':x['dt'].isoformat(),'actual':['H','D','A'][y],
                    'market_probability':[float(v) for v in pm],'r39c_probability':[float(v) for v in pcp],'r39e_probability':[float(v) for v in pep],
                    'market_top1':['H','D','A'][dm],'r39c_top1':['H','D','A'][dcx],'r39e_top1':['H','D','A'][dex]})
    (a.out_dir/'per_match_r39e.json').write_text(json.dumps(per,ensure_ascii=False,indent=2),encoding='utf-8')
    result={
      'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
      'identity_set_sha256':s3,'first200_third100_overlap':0,
      'access_audit':{'training_labels_accessed':training_access,'third100_labels_accessed_before_parameter_freeze':0,'first200_holdout_labels_accessed':0,'third100_labels_accessed_after_parameter_freeze':test_access},
      'actual_counts':{'H':sum(y==0 for y in actual),'D':sum(y==1 for y in actual),'A':sum(y==2 for y in actual)},
      'metrics':{'market':{**mm,'draw_auc':aucm},'r39c':{**mcx,'draw_auc':aucc},'r39e':{**mex,'draw_auc':auce}},
      'paired_discordance':discord,'bootstrap':{'r39e_minus_market_accuracy':boot_m,'r39e_minus_r39c_accuracy':boot_c},
      'gates':{'probability_signal':{**prob_gate,'passed':prob_pass},'decision_increment':{**decision_gate,'passed':decision_pass},'strong_uncertainty':{**uncertainty,'passed':uncertainty_pass},'overall_passed':overall},
      'model_parameter_sha256':freeze['model_parameter_sha256'],'hard_limits':pre['hard_limits']}
    (a.out_dir/'result_r39e.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
