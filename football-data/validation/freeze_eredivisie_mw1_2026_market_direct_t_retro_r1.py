#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / 'processed'
INPUT = ROOT / 'research/eredivisie_mw1_2026_prematch_market_inputs_r1.json'
CONTRACT = ROOT / 'research/eredivisie_mw1_2026_market_direct_t_retro_contract_r1.json'
OUTDIR = ROOT / 'research/eredivisie_mw1_2026_market_direct_t_retro_r1'
OUTDIR.mkdir(parents=True, exist_ok=True)
TARGET_START = pd.Timestamp('2023-07-01')
CLASSES = list(range(8))
C_GRID = [0.01, 0.1]
EPS = 1e-6


def norm_header(x: Any) -> str:
    import re
    return re.sub(r'[^a-z0-9><]+', '', str(x or '').lower())


def field_name(headers, aliases):
    m = {norm_header(h): h for h in headers}
    for a in aliases:
        if norm_header(a) in m:
            return m[norm_header(a)]
    return None


def fval(row, field):
    if not field:
        return None
    try:
        v = float(str(row.get(field) or '').strip())
        return v if math.isfinite(v) else None
    except Exception:
        return None


def price(v):
    return v is not None and v > 1.0


def parse_date(text):
    text = str(text or '').strip()
    if not text:
        return None
    for dayfirst in (True, False):
        try:
            x = pd.to_datetime(text, dayfirst=dayfirst, errors='raise', utc=True)
            return x.tz_convert(None).normalize()
        except Exception:
            pass
    return None


def devig(vals):
    inv = np.asarray([1.0 / float(x) for x in vals], dtype=float)
    return inv / inv.sum()


def logit(p):
    p = float(np.clip(p, EPS, 1-EPS))
    return math.log(p/(1-p))


def choose_triplet(row, headers):
    prefs = [
        ('PSCH','PSCD','PSCA'), ('AvgCH','AvgCD','AvgCA'), ('MaxCH','MaxCD','MaxCA'), ('B365CH','B365CD','B365CA'),
        ('PSH','PSD','PSA'), ('AvgH','AvgD','AvgA'), ('MaxH','MaxD','MaxA'), ('B365H','B365D','B365A'),
        ('home_odds','draw_odds','away_odds'),
    ]
    for names in prefs:
        fs = [field_name(headers,[n]) for n in names]
        vs = [fval(row,f) for f in fs]
        if all(fs) and all(price(v) for v in vs):
            return devig(vs), '/'.join(fs)
    return None, None


def choose_ou(row, headers):
    prefs = [
        ('PC>2.5','PC<2.5'), ('AvgC>2.5','AvgC<2.5'), ('MaxC>2.5','MaxC<2.5'), ('B365C>2.5','B365C<2.5'),
        ('P>2.5','P<2.5'), ('Avg>2.5','Avg<2.5'), ('over_odds','under_odds'),
    ]
    for overn, undern in prefs:
        of, uf = field_name(headers,[overn]), field_name(headers,[undern])
        ov, uv = fval(row,of), fval(row,uf)
        if of and uf and price(ov) and price(uv):
            return float(devig([ov,uv])[0]), f'{of}/{uf}'
    return None, None


def make_model(C):
    return Pipeline([
        ('scaler', StandardScaler()),
        ('model', LogisticRegression(C=float(C), solver='lbfgs', max_iter=350, tol=1e-4)),
    ])


def align(model, X):
    raw = model.predict_proba(X)
    out = np.zeros((len(X),8), dtype=float)
    for j,c in enumerate(model.named_steps['model'].classes_):
        out[:,int(c)] = raw[:,j]
    out /= out.sum(axis=1,keepdims=True)
    return out


def ll(y,p):
    y=np.asarray(y,int)
    return float((-np.log(np.clip(p[np.arange(len(y)),y],1e-15,1))).mean())


def top2_indices(p):
    return np.argsort(-p, axis=1, kind='stable')[:, :2]


def tail_mass(p, threshold):
    # threshold=4 means P(T>=4), with class 7 representing 7+.
    return p[:, threshold:].sum(axis=1)


# Reproduce the exact historical-training materialization from the fabul0us B01-B04 route.
rows=[]
post_target_score_dereference=0
for path in sorted(PROCESSED.rglob('*.csv')):
    rel=str(path.relative_to(ROOT)).replace('\\','/')
    try:
        with path.open('r',encoding='utf-8-sig',newline='') as f:
            r=csv.DictReader(f); headers=list(r.fieldnames or [])
            datef=field_name(headers,['Date','date','kickoff_at','kickoff_utc'])
            homef=field_name(headers,['HomeTeam','home_team','home'])
            awayf=field_name(headers,['AwayTeam','away_team','away'])
            compf=field_name(headers,['competition_id','competition','league_id'])
            hgf=field_name(headers,['FTHG','home_goals_90','home_score'])
            agf=field_name(headers,['FTAG','away_goals_90','away_score'])
            if not all([datef,homef,awayf,hgf,agf]):
                continue
            for rn,row in enumerate(r,start=2):
                d=parse_date(row.get(datef))
                if d is None or d >= TARGET_START:
                    continue
                try:
                    hg=int(float(str(row.get(hgf) or '').strip())); ag=int(float(str(row.get(agf) or '').strip()))
                except Exception:
                    continue
                if not (0 <= hg <= 30 and 0 <= ag <= 30):
                    continue
                hda,hsrc=choose_triplet(row,headers); pou,ousrc=choose_ou(row,headers)
                if hda is None or pou is None:
                    continue
                home=str(row.get(homef) or '').strip(); away=str(row.get(awayf) or '').strip()
                if not home or not away:
                    continue
                comp=str(row.get(compf) or path.parent.name).strip()
                ident=f'{comp}|{d.date().isoformat()}|{home}|{away}'
                rows.append({
                    'identity':ident,'date':d,'source_file':rel,'row_number':rn,
                    'total_class':min(hg+ag,7),
                    'logit_pH':logit(hda[0]),'logit_pD':logit(hda[1]),'logit_pA':logit(hda[2]),
                    'ou25_logit_over':logit(pou),'hda_source':hsrc,'ou_source':ousrc,
                })
    except Exception:
        continue

hist=pd.DataFrame(rows)
if hist.empty:
    raise SystemExit('no eligible historical market+score rows')
hist=hist.sort_values(['date','identity','source_file','row_number']).drop_duplicates('identity',keep='first').reset_index(drop=True)
assert (hist.date < TARGET_START).all()
assert post_target_score_dereference == 0
if len(hist) < 1000:
    raise SystemExit(f'historical cohort too small: {len(hist)}')

cut_idx=max(1,int(math.floor(len(hist)*0.80)))
policy_start=hist.iloc[cut_idx].date
train=hist[hist.date < policy_start].copy()
policy=hist[hist.date >= policy_start].copy()
if min(len(train),len(policy)) < 100:
    raise SystemExit(f'insufficient split train={len(train)} policy={len(policy)}')
base_features=['logit_pH','logit_pD','logit_pA']
ch_features=base_features+['ou25_logit_over']
receipts=[]
for C in C_GRID:
    m=make_model(C); m.fit(train[base_features],train.total_class)
    receipts.append({'C':C,'policy_logloss':ll(policy.total_class.to_numpy(int),align(m,policy[base_features]))})
selected=min(receipts,key=lambda x:(x['policy_logloss'],x['C']))['C']
base=make_model(selected); cand=make_model(selected)
base.fit(hist[base_features],hist.total_class); cand.fit(hist[ch_features],hist.total_class)

# Target packet has market inputs only and deliberately contains no score/result fields.
contract=json.loads(CONTRACT.read_text(encoding='utf-8'))
packet=json.loads(INPUT.read_text(encoding='utf-8'))
assert contract['status']=='METHOD_LOCKED_BEFORE_TARGET_RESULT_SETTLEMENT'
assert packet['status']=='INPUTS_FROZEN_TARGET_RESULTS_NOT_USED'
assert packet['target_score_fields_present'] is False
assert packet['target_results_used_for_input_selection'] is False
assert len(packet['rows'])==9
for r in packet['rows']:
    forbidden={'score','result','home_goals','away_goals','actual_total','winner'}
    assert not (forbidden & {str(k).lower() for k in r})
    assert all(price(float(r[k])) for k in ('home_odds','draw_odds','away_odds','over25_odds','under25_odds'))

xrows=[]
for r in packet['rows']:
    hda=devig([r['home_odds'],r['draw_odds'],r['away_odds']])
    pou=float(devig([r['over25_odds'],r['under25_odds']])[0])
    xrows.append({
        'fixture_no':int(r['fixture_no']),'kickoff_utc':r['kickoff_utc'],'home':r['home'],'away':r['away'],
        'logit_pH':logit(hda[0]),'logit_pD':logit(hda[1]),'logit_pA':logit(hda[2]),
        'ou25_logit_over':logit(pou),
        'p_home_devig':float(hda[0]),'p_draw_devig':float(hda[1]),'p_away_devig':float(hda[2]),
        'p_over25_devig':pou,
        'source':r['source'],'source_url':r['source_url'],
    })
X=pd.DataFrame(xrows).sort_values('fixture_no').reset_index(drop=True)
pb=align(base,X[base_features]); pc=align(cand,X[ch_features])

pred=X[['fixture_no','kickoff_utc','home','away','p_home_devig','p_draw_devig','p_away_devig','p_over25_devig','source','source_url']].copy()
for j in CLASSES:
    pred[f'baseline_pT{j}']=pb[:,j]
    pred[f'candidate_pT{j}']=pc[:,j]
pred['baseline_top1_T']=np.argmax(pb,axis=1)
pred['candidate_top1_T']=np.argmax(pc,axis=1)
pred['baseline_top2_T']=[';'.join(str(int(v)) for v in row) for row in top2_indices(pb)]
pred['candidate_top2_T']=[';'.join(str(int(v)) for v in row) for row in top2_indices(pc)]
for threshold in (4,5,6):
    pred[f'baseline_pT_ge_{threshold}']=tail_mass(pb,threshold)
    pred[f'candidate_pT_ge_{threshold}']=tail_mass(pc,threshold)

pred_path=OUTDIR/'frozen_predictions.csv'
pred.to_csv(pred_path,index=False,float_format='%.15g')
pred_sha=hashlib.sha256(pred_path.read_bytes()).hexdigest()
training_identity=hashlib.sha256(('\n'.join(hist.identity.astype(str))+'\n').encode()).hexdigest()
lock={
  'schema_version':'EREDIVISIE-MW1-2026-MARKET-DIRECT-T-RETRO-PREDICTION-LOCK-R1',
  'status':'PREDICTIONS_FROZEN_BEFORE_TARGET_RESULT_SETTLEMENT',
  'classification':'RETROSPECTIVE_PREMATCH_REFERENCE_EXPERIMENT_ONLY',
  'target':{
    'competition_id':'NED_Eredivisie','season':'2026/27','matchweek':1,'n':9,
    'market_input_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
    'method_contract_sha256':hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
    'strict_T60_PIT_claim':False,
    'timestamp_tier':'RETROSPECTIVE_PREMATCH_REFERENCE_NO_EXACT_T60_QUOTE_TIMESTAMP',
    'target_labels_accessed_by_prediction_script':0,
  },
  'historical_training':{
    'target_period_start_exclusive':'2023-07-01','eligible_rows':int(len(hist)),
    'train_rows':int(len(train)),'policy_rows':int(len(policy)),
    'policy_start_date':policy_start.date().isoformat(),'identity_sha256':training_identity,
    'post_target_outcome_values_dereferenced':0,
  },
  'model':{
    'source_contract':'fabul0us OU2.5 B01-B04 Direct-T exact architecture',
    'baseline_features':base_features,'candidate_features':ch_features,
    'candidate_increment_exact':'logit(de-vigged P(Over2.5))',
    'pipeline':['StandardScaler','LogisticRegression'],'solver':'lbfgs','max_iter':350,'tol':0.0001,
    'C_grid':C_GRID,'C_selection':'baseline-only chronological policy logloss; same C forced for baseline and candidate',
    'selected_C':selected,'policy_receipts':receipts,
  },
  'frozen_predictions':{'path':str(pred_path.relative_to(ROOT)).replace('\\','/'),'sha256':pred_sha,'rows':9},
  'interpretation_guard':{
    'nine_matches_can_confirm_model':False,'can_authorize_promotion':False,
    'availability_or_XI_probability_adjustment_used':False,'post_result_parameter_search_allowed':False,
  },
  'governance':{'formal_weight':0,'CURRENT_mutation':False,'main_mutation':False,'formal_model_mutation':False}
}
lock_path=OUTDIR/'prediction_lock.json'
lock_path.write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({**lock,'prediction_lock_sha256':hashlib.sha256(lock_path.read_bytes()).hexdigest()},ensure_ascii=False,indent=2))
