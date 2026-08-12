#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path('.')
PREFLIGHT_OUT = Path('/tmp/r45-preflight')
OUT = Path('/tmp/r45-development')
SAMPLE_RECEIPT = ROOT / 'football-data/research/existing_pit_1000_r45/sample_freeze_receipt_r45.json'
PREREG = ROOT / 'football-data/research/existing_pit_1000_r45/development_prereg_r45.json'
DATA_FILES = sorted(ROOT.glob('football-data/training_datasets/*/point_in_time.csv'))
ID_FIELDS = ('competition_id', 'season', 'date', 'home_team', 'away_team')
CLASSES = ('H', 'D', 'A')
CLASS_TO_I = {c:i for i,c in enumerate(CLASSES)}
EPS = 1e-15


def canonical_id(row: dict[str, str]) -> str:
    return '|'.join(str(row.get(k, '')).strip() for k in ID_FIELDS)


def sha_ids(ids: list[str]) -> str:
    raw = ('\n'.join(ids) + ('\n' if ids else '')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def load_identity_csv(path: Path) -> list[str]:
    with path.open('r', encoding='utf-8', newline='') as f:
        return [r['identity'] for r in csv.DictReader(f)]


def softmax(logits: list[float]) -> list[float]:
    m = max(logits)
    exps = [math.exp(z-m) for z in logits]
    s = sum(exps)
    return [e/s for e in exps]


def standardize_fit(X: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(X); p = len(X[0])
    means = [sum(row[j] for row in X)/n for j in range(p)]
    scales = []
    for j in range(p):
        v = sum((row[j]-means[j])**2 for row in X)/n
        sd = math.sqrt(v)
        scales.append(sd if sd > 0 else 1.0)
    return means, scales


def standardize_apply(X: list[list[float]], means: list[float], scales: list[float]) -> list[list[float]]:
    return [[(x-means[j])/scales[j] for j,x in enumerate(row)] for row in X]


def predict_probs(X: list[list[float]], W: list[list[float]], b: list[float]) -> list[list[float]]:
    out=[]
    for x in X:
        logits=[b[k] + sum(W[k][j]*x[j] for j in range(len(x))) for k in range(3)]
        out.append(softmax(logits))
    return out


def objective_and_grad(X, y, W, b, l2):
    n=len(X); p=len(X[0])
    gW=[[0.0]*p for _ in range(3)]
    gb=[0.0]*3
    ce=0.0
    for x,yi in zip(X,y):
        logits=[b[k] + sum(W[k][j]*x[j] for j in range(p)) for k in range(3)]
        probs=softmax(logits)
        ce -= math.log(max(EPS, probs[yi]))
        for k in range(3):
            err=probs[k]-(1.0 if yi==k else 0.0)
            gb[k]+=err
            gwk=gW[k]
            for j in range(p):
                gwk[j]+=err*x[j]
    penalty=0.0
    for k in range(3):
        for j in range(p):
            penalty += W[k][j]*W[k][j]
            gW[k][j]=(gW[k][j] + l2*W[k][j])/n
        gb[k]/=n
    obj=ce/n + (l2*penalty)/(2*n)
    return obj,gW,gb


def fit_softmax(X, y, *, l2: float, lr: float, iterations: int):
    p=len(X[0])
    W=[[0.0]*p for _ in range(3)]
    b=[0.0]*3
    trace=[]
    last_grad_norm=None
    for it in range(1,iterations+1):
        obj,gW,gb=objective_and_grad(X,y,W,b,l2)
        sq=0.0
        for k in range(3):
            b[k]-=lr*gb[k]; sq += gb[k]*gb[k]
            for j in range(p):
                W[k][j]-=lr*gW[k][j]; sq += gW[k][j]*gW[k][j]
        last_grad_norm=math.sqrt(sq)
        if it in {1,10,50,100,200,400,800,iterations}:
            trace.append({'iteration':it,'objective':obj,'gradient_l2':last_grad_norm})
    final_obj,_,_=objective_and_grad(X,y,W,b,l2)
    return W,b,{'iterations':iterations,'final_objective':final_obj,'final_gradient_l2':last_grad_norm,'trace':trace}


def multiclass_metrics(y: list[int], probs: list[list[float]]) -> dict:
    n=len(y)
    ll=0.0; brier=0.0; rps=0.0
    correct=0; pred_counts=Counter(); max_resid=0.0
    draw_scores=[]; draw_true=[]
    conf_bins=[{'n':0,'conf':0.0,'acc':0.0} for _ in range(10)]
    tp=fp=fn=0
    for yi,p in zip(y,probs):
        ll -= math.log(max(EPS,p[yi]))
        brier += sum((p[k]-(1.0 if yi==k else 0.0))**2 for k in range(3))
        c1=p[0]; c2=p[0]+p[1]
        y1=1.0 if yi==0 else 0.0
        y2=1.0 if yi in (0,1) else 0.0
        rps += ((c1-y1)**2 + (c2-y2)**2)/2.0
        pred=max(range(3),key=lambda k:p[k]); pred_counts[CLASSES[pred]]+=1
        if pred==yi: correct+=1
        if pred==1 and yi==1: tp+=1
        elif pred==1 and yi!=1: fp+=1
        elif pred!=1 and yi==1: fn+=1
        draw_scores.append(p[1]); draw_true.append(1 if yi==1 else 0)
        conf=max(p); idx=min(9,int(conf*10)); conf_bins[idx]['n']+=1; conf_bins[idx]['conf']+=conf; conf_bins[idx]['acc']+=(1.0 if pred==yi else 0.0)
        max_resid=max(max_resid,abs(sum(p)-1.0))
    positives=sum(draw_true)
    ranked=sorted(zip(draw_scores,draw_true), key=lambda t:t[0], reverse=True)
    hits=0; ap_sum=0.0
    for rank,(_,truth) in enumerate(ranked,1):
        if truth:
            hits+=1; ap_sum += hits/rank
    ap=ap_sum/positives if positives else None
    draw_brier=sum((s-t)**2 for s,t in zip(draw_scores,draw_true))/n
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=(2*precision*recall/(precision+recall)) if precision+recall else 0.0
    ece=0.0; bins=[]
    for i,bn in enumerate(conf_bins):
        if bn['n']:
            mc=bn['conf']/bn['n']; ma=bn['acc']/bn['n']; ece += (bn['n']/n)*abs(mc-ma)
            bins.append({'bin':i,'count':bn['n'],'mean_confidence':mc,'accuracy':ma})
    return {
        'count':n,
        'multiclass_log_loss':ll/n,
        'multiclass_brier':brier/n,
        'rps_H_D_A':rps/n,
        'top_confidence_ece_10bin':ece,
        'top_confidence_bins':bins,
        'accuracy_secondary_only':correct/n,
        'prediction_counts':dict(pred_counts),
        'draw_average_precision':ap,
        'draw_brier':draw_brier,
        'draw_mean_probability':sum(draw_scores)/n,
        'draw_prevalence':positives/n,
        'draw_precision_argmax':precision,
        'draw_recall_argmax':recall,
        'draw_f1_argmax':f1,
        'draw_argmax_tp':tp,
        'draw_argmax_fp':fp,
        'draw_argmax_fn':fn,
        'probability_sum_max_abs_residual':max_resid,
    }


def prior_probs(y_train: list[int], n: int) -> tuple[list[list[float]], dict]:
    c=Counter(y_train); total=len(y_train)
    prior=[c[k]/total for k in range(3)]
    return [prior[:] for _ in range(n)], {CLASSES[k]:prior[k] for k in range(3)}


sample=json.loads(SAMPLE_RECEIPT.read_text(encoding='utf-8'))
prereg=json.loads(PREREG.read_text(encoding='utf-8'))
features=prereg['features']
train_ids=load_identity_csv(PREFLIGHT_OUT/'r45_train650_identity.csv')
val_ids=load_identity_csv(PREFLIGHT_OUT/'r45_validation150_identity.csv')
oos_ids=load_identity_csv(PREFLIGHT_OUT/'r45_locked_oos200_identity.csv')
assert len(train_ids)==650 and sha_ids(train_ids)==sample['train650']['identity_sha256']
assert len(val_ids)==150 and sha_ids(val_ids)==sample['validation150']['identity_sha256']
assert len(oos_ids)==200 and sha_ids(oos_ids)==sample['locked_oos200']['identity_sha256']
train_set=set(train_ids); val_set=set(val_ids); oos_set=set(oos_ids)
assert not (train_set & val_set or train_set & oos_set or val_set & oos_set)

train_rows={}; val_rows={}; oos_identity_seen=set(); oos_label_references=0
for path in DATA_FILES:
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        fields=set(reader.fieldnames or [])
        missing=[x for x in features if x not in fields]
        if missing: raise RuntimeError(f'MISSING_FEATURES:{path}:{missing}')
        if 'label_result' not in fields: raise RuntimeError(f'MISSING_TARGET:{path}')
        for row in reader:
            ident=canonical_id(row)
            if ident in train_set or ident in val_set:
                x=[]
                for name in features:
                    v=str(row.get(name,'')).strip()
                    if v=='': raise RuntimeError(f'MISSING_VALUE:{ident}:{name}')
                    if v.lower()=='true': x.append(1.0)
                    elif v.lower()=='false': x.append(0.0)
                    else: x.append(float(v))
                label=str(row['label_result']).strip()
                if label not in CLASS_TO_I: raise RuntimeError(f'BAD_LABEL:{ident}:{label}')
                target=(x,CLASS_TO_I[label])
                (train_rows if ident in train_set else val_rows)[ident]=target
            elif ident in oos_set:
                # Deliberately record identity presence only. label_result is never accessed on locked OOS rows.
                oos_identity_seen.add(ident)

assert len(train_rows)==650 and len(val_rows)==150 and len(oos_identity_seen)==200
assert oos_label_references==0
X_train=[train_rows[i][0] for i in train_ids]; y_train=[train_rows[i][1] for i in train_ids]
X_val=[val_rows[i][0] for i in val_ids]; y_val=[val_rows[i][1] for i in val_ids]
means,scales=standardize_fit(X_train)
Xs_train=standardize_apply(X_train,means,scales)
Xs_val=standardize_apply(X_val,means,scales)
model_cfg=prereg['model']
W,b,opt=fit_softmax(Xs_train,y_train,l2=float(model_cfg['l2_lambda']),lr=float(model_cfg['learning_rate']),iterations=int(model_cfg['iterations']))
train_probs=predict_probs(Xs_train,W,b)
val_probs=predict_probs(Xs_val,W,b)
base_probs,priors=prior_probs(y_train,len(y_val))
train_metrics=multiclass_metrics(y_train,train_probs)
val_metrics=multiclass_metrics(y_val,val_probs)
baseline_metrics=multiclass_metrics(y_val,base_probs)

model_payload={
    'schema_version':'R45-DEVELOPMENT-MODEL-1.0',
    'status':'DEVELOPMENT_ONLY_NOT_PROMOTION_ELIGIBLE',
    'classes':list(CLASSES),
    'features':features,
    'train_identity_sha256':sample['train650']['identity_sha256'],
    'validation_identity_sha256':sample['validation150']['identity_sha256'],
    'locked_oos_identity_sha256':sample['locked_oos200']['identity_sha256'],
    'means':means,'scales':scales,'weights':W,'intercepts':b,
    'optimizer':opt,'model_config':model_cfg,
    'locked_oos_labels_read':False,
}
model_raw=json.dumps(model_payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
model_sha=hashlib.sha256(model_raw.encode('utf-8')).hexdigest()

result={
    'schema_version':'R45-DEVELOPMENT-VALIDATION-1.0',
    'status':'COMPLETE_DEVELOPMENT_VALIDATION_OOS_STILL_LOCKED',
    'formal_weight':0,
    'research_family_status':prereg['research_family_status'],
    'sample':{
        'train650':sample['train650'],
        'validation150':sample['validation150'],
        'locked_oos200':sample['locked_oos200'],
    },
    'label_access':{
        'train_labels_read':len(y_train),
        'validation_labels_read':len(y_val),
        'locked_oos_identities_verified_present':len(oos_identity_seen),
        'locked_oos_labels_referenced':oos_label_references,
    },
    'class_counts':{
        'train':{CLASSES[k]:Counter(y_train)[k] for k in range(3)},
        'validation':{CLASSES[k]:Counter(y_val)[k] for k in range(3)},
    },
    'baseline_train_prior':priors,
    'baseline_validation':baseline_metrics,
    'model_train':train_metrics,
    'model_validation':val_metrics,
    'validation_delta_model_minus_prior':{
        'multiclass_log_loss':val_metrics['multiclass_log_loss']-baseline_metrics['multiclass_log_loss'],
        'multiclass_brier':val_metrics['multiclass_brier']-baseline_metrics['multiclass_brier'],
        'rps_H_D_A':val_metrics['rps_H_D_A']-baseline_metrics['rps_H_D_A'],
        'accuracy_secondary_only':val_metrics['accuracy_secondary_only']-baseline_metrics['accuracy_secondary_only'],
        'draw_average_precision':val_metrics['draw_average_precision']-baseline_metrics['draw_average_precision'],
    },
    'model_sha256':model_sha,
    'optimizer':opt,
    'oos_gate':{
        'released':False,
        'reason':'V5.2 same-information research family remains sealed; validation is diagnostic only and cannot automatically consume locked_oos200.'
    },
    'boundaries':{
        'hyperparameter_search':False,
        'candidate_models_fit':1,
        'locked_oos_labels_referenced':0,
        'new_match_downloads':0,
        'external_network_requests':0,
        'formal_weight':0,
        'formal_model_mutation':False,
        'formal_data_mutation':False,
        'current_rule_mutation':False,
        'main_mutation':False,
    }
}
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'r45_development_model.json').write_text(model_raw,encoding='utf-8')
result_raw=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
(OUT/'r45_development_validation.json').write_text(result_raw,encoding='utf-8')
(OUT/'r45_development_validation.sha256').write_text(hashlib.sha256(result_raw.encode('utf-8')).hexdigest()+'\n',encoding='ascii')
print(json.dumps(result,ensure_ascii=False,indent=2))
