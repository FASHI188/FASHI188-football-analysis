#!/usr/bin/env python3
from __future__ import annotations
import csv,itertools,json,math,pathlib,hashlib
from dataclasses import dataclass
from typing import Any,Sequence
import numpy as np
from draw_auto_research_math_r1 import canonical_json_sha256
HERE=pathlib.Path(__file__).resolve().parent; ROOT=HERE.parents[1]
BASE_FEATURES={
 'strength':['elo_signed','elo_abs','elo_closeness'],
 'form':['ppg_gap','ppg_sum','gf_gap','ga_gap','home_net','away_net'],
 'volume':['history_min','history_gap','last5_min','cold_start','stage_unverified'],
 'low_goal':['low_goal_proxy','goal_environment','defence_tightness']}
PROFILE_FEATURES={
 'strength':BASE_FEATURES['strength'],'form':BASE_FEATURES['form'],'volume':BASE_FEATURES['volume'],'low_goal':BASE_FEATURES['low_goal'],
 'strength_form':BASE_FEATURES['strength']+BASE_FEATURES['form'],
 'strength_low_goal':BASE_FEATURES['strength']+BASE_FEATURES['low_goal'],
 'form_low_goal':BASE_FEATURES['form']+BASE_FEATURES['low_goal'],
 'full_core':sum(BASE_FEATURES.values(),[]),
 'full_interactions':sum(BASE_FEATURES.values(),[])+['closeness_x_low_goal','closeness_x_ppg_gap','form_x_low_goal'],
 'robust_full':sum(BASE_FEATURES.values(),[])+['closeness_x_low_goal','closeness_x_ppg_gap','form_x_low_goal','elo_abs_sq','ppg_gap_sq']}
BASIS_VARIANTS=('linear','signed_sqrt','tanh','quadratic')
POSITIVE_CLASS_WEIGHTS=(0.9,1.0,1.1,1.25,1.5)
L2_GRIDS={'strength':[.25,1.,4.],'form':[.25,1.,4.],'volume':[1.,4.,16.],'low_goal':[.25,1.,4.],
 'strength_form':[.5,2.,8.],'strength_low_goal':[.5,2.,8.],'form_low_goal':[.5,2.,8.],
 'full_core':[1.,4.,16.],'full_interactions':[2.,8.,32.],'robust_full':[4.,16.,64.]}
NUMERIC_SOURCE_FIELDS=('home_history_matches','away_history_matches','home_last5_matches','away_last5_matches','home_last5_gf','away_last5_gf','home_last5_ga','away_last5_ga','home_last5_ppg','away_last5_ppg','home_elo_pre_match','away_elo_pre_match','elo_difference_with_home_advantage','cold_start_flag','stage_unverified_flag')
def _number(v):
 try:r=float(v)
 except (TypeError,ValueError):return math.nan
 return r if math.isfinite(r) else math.nan
def _season_key(s):
 d=''.join(c for c in s[:4] if c.isdigit());return (int(d) if len(d)==4 else 9999,s)
@dataclass(frozen=True)
class MatchRow:
 competition:str;season:str;date:str;home_team:str;away_team:str;label:str;values:dict[str,float]
 @property
 def key(self):return f'{self.competition}|{self.season}|{self.date}|{self.home_team}|{self.away_team}'
@dataclass(frozen=True)
class OuterFold:
 fold_id:str;competition:str;target_season:str;prior_seasons:tuple[str,...];inner_train_seasons:tuple[str,...];inner_validation_season:str;train_rows:tuple[MatchRow,...];inner_train_rows:tuple[MatchRow,...];inner_validation_rows:tuple[MatchRow,...];evaluation_rows:tuple[MatchRow,...]
def feature_values(row:MatchRow)->dict[str,float]:
 v=row.values;e=v['elo_difference_with_home_advantage'];ea=abs(e) if math.isfinite(e) else math.nan;ec=math.exp(-ea/200) if math.isfinite(ea) else math.nan
 pg=abs(v['home_last5_ppg']-v['away_last5_ppg']);ps=v['home_last5_ppg']+v['away_last5_ppg'];gg=abs(v['home_last5_gf']-v['away_last5_gf']);ag=abs(v['home_last5_ga']-v['away_last5_ga']);hn=v['home_last5_gf']-v['home_last5_ga'];an=v['away_last5_gf']-v['away_last5_ga'];hm=min(v['home_history_matches'],v['away_history_matches']);hg=abs(v['home_history_matches']-v['away_history_matches']);lm=min(v['home_last5_matches'],v['away_last5_matches']);ge=(v['home_last5_gf']+v['away_last5_gf']+v['home_last5_ga']+v['away_last5_ga'])/2;lg=2.5-ge;dt=-(v['home_last5_ga']+v['away_last5_ga'])
 return {'elo_signed':e,'elo_abs':ea,'elo_closeness':ec,'ppg_gap':pg,'ppg_sum':ps,'gf_gap':gg,'ga_gap':ag,'home_net':hn,'away_net':an,'history_min':hm,'history_gap':hg,'last5_min':lm,'cold_start':v['cold_start_flag'],'stage_unverified':v['stage_unverified_flag'],'low_goal_proxy':lg,'goal_environment':ge,'defence_tightness':dt,'closeness_x_low_goal':ec*lg,'closeness_x_ppg_gap':ec*pg,'form_x_low_goal':(hn+an)*lg,'elo_abs_sq':ea*ea,'ppg_gap_sq':pg*pg}
def load_rows(spec:dict[str,Any],root:pathlib.Path=ROOT)->list[MatchRow]:
 out=[]
 for comp in sorted(spec['dataset_sha256']):
  p=root/'football-data'/'training_datasets'/comp/'point_in_time.csv'
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   rd=csv.DictReader(f);req={'competition_id','season','date','home_team','away_team','label_result',*NUMERIC_SOURCE_FIELDS};miss=req-set(rd.fieldnames or [])
   if miss:raise ValueError(f'dataset header missing {comp}: {sorted(miss)}')
   for raw in rd:
    label=str(raw['label_result'])
    if label not in {'H','D','A'}:raise ValueError(f'invalid label {comp}: {label}')
    out.append(MatchRow(comp,str(raw['season']),str(raw['date']),str(raw['home_team']),str(raw['away_team']),label,{k:_number(raw.get(k)) for k in NUMERIC_SOURCE_FIELDS}))
 keys=[r.key for r in out]
 if len(keys)!=len(set(keys)):raise ValueError('duplicate match key')
 return out
def build_outer_folds(rows:Sequence[MatchRow])->list[OuterFold]:
 by={}
 for r in rows:by.setdefault(r.competition,[]).append(r)
 out=[]
 for comp,rs in sorted(by.items()):
  seasons=sorted({r.season for r in rs},key=_season_key)
  if len(seasons)<5:raise ValueError(f'fewer than five complete seasons: {comp}')
  for target in seasons[2:5]:
   i=seasons.index(target);prior=seasons[:i];iv=prior[-1];it=prior[:-1]
   if not it:raise ValueError(f'inner training empty: {comp} {target}')
   out.append(OuterFold(f'{comp}|{target}',comp,target,tuple(prior),tuple(it),iv,tuple(r for r in rs if r.season in prior),tuple(r for r in rs if r.season in it),tuple(r for r in rs if r.season==iv),tuple(r for r in rs if r.season==target)))
 if len(out)!=51:raise ValueError(f'expected 51 outer folds, got {len(out)}')
 return out
def candidate_catalog()->list[dict[str,Any]]:
 out=[]
 for i,(profile,weight,basis) in enumerate(itertools.product(PROFILE_FEATURES,POSITIVE_CLASS_WEIGHTS,BASIS_VARIANTS),1):
  c={'candidate_id':f'C{i:03d}','profile':profile,'features':PROFILE_FEATURES[profile],'positive_class_weight':weight,'basis_variant':basis,'l2_grid':L2_GRIDS[profile],'generation_index':i};c['candidate_sha256']=canonical_json_sha256(c);out.append(c)
 if len(out)!=200 or len({c['candidate_sha256'] for c in out})!=200:raise ValueError('candidate structural identity failure')
 if any('draw_logit_offset' in c for c in out):raise ValueError('redundant draw_logit_offset prohibited')
 return out
def apply_basis(x:np.ndarray,basis:str)->np.ndarray:
 if basis=='linear':return x
 if basis=='signed_sqrt':return np.sign(x)*np.sqrt(np.abs(x))
 if basis=='tanh':return np.tanh(x)
 if basis=='quadratic':return np.column_stack([x,x*x])
 raise ValueError(f'unknown basis: {basis}')
@dataclass
class Preprocessor:
 original_features:list[str];kept_features:list[str];medians:list[float];means:list[float];scales:list[float];missing_indicator_features:list[str];dropped:dict[str,str];fit_row_keys_sha256:str;basis_variant:str;evaluation_rows_used_for_decisions:int=0
 @classmethod
 def fit(cls,rows:Sequence[MatchRow],features:Sequence[str],basis_variant:str='linear'):
  if not rows:raise ValueError('empty preprocessing training rows')
  vals=[feature_values(r) for r in rows];raw=np.asarray([[v[n] for n in features] for v in vals],float);imp=raw.copy();med=[];mi=[]
  for j,n in enumerate(features):
   col=raw[:,j];fin=col[np.isfinite(col)];m=float(np.median(fin)) if len(fin) else 0.;med.append(m);mi += [n] if np.any(~np.isfinite(col)) else [];imp[:,j]=np.where(np.isfinite(col),col,m)
  means=imp.mean(0);scales=imp.std(0);z=np.zeros_like(imp);drop={};pre=[]
  for j,n in enumerate(features):
   if not math.isfinite(float(scales[j])) or float(scales[j])<1e-12:drop[n]='near_zero_variance_training_only'
   else:z[:,j]=(imp[:,j]-means[j])/scales[j];pre.append(j)
  keep=[]
  for j in pre:
   dup=None
   for k in keep:
    corr=float(np.corrcoef(z[:,k],z[:,j])[0,1])
    if float(np.max(np.abs(z[:,k]-z[:,j])))<=1e-12 or (math.isfinite(corr) and abs(corr)>=.999999):dup=features[k];break
   if dup:drop[features[j]]=f'training_only_duplicate_or_correlation:{dup}'
   else:keep.append(j)
  kh=hashlib.sha256(json.dumps(sorted(r.key for r in rows),separators=(',',':')).encode()).hexdigest()
  return cls(list(features),[features[j] for j in keep],med,[float(x) for x in means],[float(x) for x in scales],mi,drop,kh,basis_variant)
 def transform(self,rows:Sequence[MatchRow])->np.ndarray:
  vals=[feature_values(r) for r in rows];idx={n:i for i,n in enumerate(self.original_features)};cols=[]
  for n in self.kept_features:
   j=idx[n];raw=np.asarray([v[n] for v in vals],float);miss=~np.isfinite(raw);imp=np.where(np.isfinite(raw),raw,self.medians[j]);cols.append((imp-self.means[j])/self.scales[j]);
   if n in self.missing_indicator_features:cols.append(miss.astype(float))
  m=np.column_stack(cols) if cols else np.zeros((len(rows),0));m=apply_basis(m,self.basis_variant)
  if not np.all(np.isfinite(m)):raise ValueError('nonfinite transformed matrix')
  return m
 def receipt(self):return {'original_features':self.original_features,'kept_features':self.kept_features,'missing_indicator_features':self.missing_indicator_features,'dropped':self.dropped,'fit_row_keys_sha256':self.fit_row_keys_sha256,'basis_variant':self.basis_variant,'evaluation_rows_used_for_decisions':self.evaluation_rows_used_for_decisions}
