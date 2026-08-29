#!/usr/bin/env python3
from __future__ import annotations

import os, json, math, hashlib, re, unicodedata
from collections import defaultdict, deque, Counter
from pathlib import Path
import numpy as np
import pandas as pd
from rapidfuzz.fuzz import WRatio
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

SEED = 20260829
TRAIN_START = pd.Timestamp('2013-07-01')
LOOKBACK_XI_MATCHES = 8
MIN_PRIOR_LINEUP_MATCHES = 3
MIN_PLAYER_SNAPSHOTS = 9
MIN_TRAIN = 1500
MIN_TEST = 240
BOOTSTRAP_N = 10000
EPS = 1e-15
ROOT = Path(__file__).resolve().parent
EXT = Path(os.environ.get('EURO_FOOTBALL_DATA', 'external_football/data'))
OUT = Path(os.environ.get('R45B_OUT', 'r45b_output'))
OUT.mkdir(parents=True, exist_ok=True)

ARCH = pd.read_csv(ROOT.parent / 'r45a_player_capability_300_retro' / 'sample_market_min.csv')
SAMPLE = ARCH[['match_id','league','home_team','away_team','match_datetime']].copy()
SAMPLE['match_datetime'] = pd.to_datetime(SAMPLE['match_datetime'])
ARCH['match_datetime'] = pd.to_datetime(ARCH['match_datetime'])
CUTOFF = SAMPLE['match_datetime'].min().normalize()
MAX_TARGET_DATE = SAMPLE['match_datetime'].max().normalize()

SOURCE_FILES = {
    'football_match.RData':'97b84ca79115ee6cef1a939e5e08c77ec1f6dd8b',
    'player.RData':'1bc6a94086417ba5eb4d00d8fce66f6cf5168e8c',
    'player_attributes.RData':'30cff8d31ca3e9bbf733eb65991787deae9112af',
    'team.RData':'57201c5a415842e7b2cdadf6d087a7417740f709',
    'team_attributes.RData':'601765b2dccd4eb3e7b0c72b70d804aaa886c9db',
}

def read_csv_source(name: str) -> pd.DataFrame:
    p = EXT / name.replace('.RData', '.csv')
    if not p.exists():
        raise RuntimeError(f'{p}: R-native CSV missing')
    df = pd.read_csv(p, low_memory=False)
    print(name, df.shape, list(df.columns)[:12], flush=True)
    return df

def norm(s):
    if pd.isna(s): return ''
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii','ignore').decode().lower()
    s = s.replace('&','and').replace('saint','st')
    s = re.sub(r'\b(fc|cf|afc|calcio|club|de|la|the)\b',' ',s)
    repl = {'utd':'united','ath':'athletic','munich':'munchen','mgladbach':'monchengladbach','paris sg':'paris st germain','psg':'paris st germain','inter milan':'internazionale','inter':'internazionale'}
    for a,b in repl.items():
        s = re.sub(r'\b'+re.escape(a)+r'\b', b, s)
    return re.sub(r'[^a-z0-9]+','',s)

def safe_num(s): return pd.to_numeric(s, errors='coerce')

def topmean(vals, n=None):
    a = np.array([x for x in vals if pd.notna(x)], float)
    if len(a)==0: return np.nan
    if n is not None: a = np.sort(a)[-min(n,len(a)):]
    return float(a.mean())

fm = read_csv_source('football_match.RData')
team = read_csv_source('team.RData')
pa = read_csv_source('player_attributes.RData')
ta = read_csv_source('team_attributes.RData')
for df in (fm, team, pa, ta):
    df.columns = [str(c) for c in df.columns]
fm['date'] = pd.to_datetime(fm['date']).dt.normalize()
pa['date'] = pd.to_datetime(pa['date']).dt.normalize()
ta['date'] = pd.to_datetime(ta['date']).dt.normalize()
team_name = dict(zip(safe_num(team['team_api_id']).astype('Int64'), team['team_long_name'].astype(str)))
fm['home_ext_name'] = [team_name.get(int(x),'') if pd.notna(x) else '' for x in safe_num(fm['home_team_api_id'])]
fm['away_ext_name'] = [team_name.get(int(x),'') if pd.notna(x) else '' for x in safe_num(fm['away_team_api_id'])]
fm['_rowid'] = np.arange(len(fm))

# Identity matching only: date/team names, never scores.
matched=[]
bydate={d:g for d,g in fm.groupby('date',sort=False)}
for _,s in SAMPLE.iterrows():
    d=s['match_datetime'].normalize(); cand=bydate.get(d,pd.DataFrame()); best=None
    for _,r in cand.iterrows():
        hs=WRatio(norm(s.home_team),norm(r.home_ext_name)); as_=WRatio(norm(s.away_team),norm(r.away_ext_name)); score=(hs+as_)/2
        item=(score,min(hs,as_),int(r['_rowid']),r.home_ext_name,r.away_ext_name,int(r.league_id) if pd.notna(r.league_id) else None)
        if best is None or item[:2]>best[:2]: best=item
    if best is None or best[0]<55 or best[1]<40:
        cands=pd.concat([bydate.get(d+pd.Timedelta(days=x),pd.DataFrame()) for x in (-1,1)],ignore_index=False)
        for _,r in cands.iterrows():
            hs=WRatio(norm(s.home_team),norm(r.home_ext_name)); as_=WRatio(norm(s.away_team),norm(r.away_ext_name)); score=(hs+as_)/2-5
            item=(score,min(hs,as_),int(r['_rowid']),r.home_ext_name,r.away_ext_name,int(r.league_id) if pd.notna(r.league_id) else None)
            if best is None or item[:2]>best[:2]: best=item
    matched.append({'match_id':int(s.match_id),'external_rowid':best[2] if best and best[0]>=55 and best[1]>=40 else np.nan,
                    'identity_similarity':best[0] if best else np.nan,'identity_min_side_similarity':best[1] if best else np.nan,
                    'external_home_name':best[3] if best else '', 'external_away_name':best[4] if best else '',
                    'external_league_id':best[5] if best else np.nan})
matchmap=pd.DataFrame(matched); matchmap.to_csv(OUT/'identity_match.csv',index=False)
validmap=matchmap.dropna(subset=['external_rowid']).copy(); validmap['external_rowid']=validmap.external_rowid.astype(int)
league_ids=sorted(validmap.external_league_id.dropna().astype(int).unique().tolist())
print('identity matched',len(validmap),'league_ids',league_ids,flush=True)
if len(validmap)<270 or len(league_ids)!=5:
    raise RuntimeError(f'IDENTITY_COVERAGE_GATE_FAIL matched={len(validmap)} leagues={league_ids}')

BOOKS=[('B365H','B365D','B365A'),('BWH','BWD','BWA'),('IWH','IWD','IWA'),('LBH','LBD','LBA'),('PSH','PSD','PSA'),('WHH','WHD','WHA'),('SJH','SJD','SJA'),('VCH','VCD','VCA'),('GBH','GBD','GBA'),('BSH','BSD','BSA')]
def market_consensus(row):
    ps=[]
    for h,d,a in BOOKS:
        if not all(c in row.index for c in (h,d,a)): continue
        try: o=np.array([float(row[h]),float(row[d]),float(row[a])])
        except Exception: continue
        if np.all(np.isfinite(o)) and np.all(o>1):
            q=1/o; ps.append(q/q.sum())
    if not ps: return (np.nan,np.nan,np.nan,0)
    p=np.mean(ps,axis=0); p=p/p.sum(); return (*p,len(ps))

# Strictly-prior player snapshot: attribute_date MUST be < match_date.
pa['player_api_id']=safe_num(pa['player_api_id']).astype('Int64')
pa=pa.dropna(subset=['player_api_id','date']).sort_values(['player_api_id','date'])
P_NUM=['overall_rating','crossing','finishing','heading_accuracy','short_passing','volleys','dribbling','curve','free_kick_accuracy','long_passing','ball_control','acceleration','sprint_speed','agility','reactions','balance','shot_power','jumping','stamina','strength','long_shots','interceptions','positioning','vision','penalties','marking','standing_tackle','sliding_tackle','gk_diving','gk_handling','gk_kicking','gk_positioning','gk_reflexes']
for c in P_NUM:
    if c in pa: pa[c]=safe_num(pa[c])
player_lookup={int(pid):(g['date'].values.astype('datetime64[ns]'),g.reset_index(drop=True)) for pid,g in pa.groupby('player_api_id',sort=False)}
def player_snapshot_strict(pid,date):
    if pd.isna(pid): return None
    item=player_lookup.get(int(pid))
    if item is None: return None
    dates,g=item; k=np.searchsorted(dates,np.datetime64(date),side='left')-1
    return None if k<0 else g.iloc[k]

PLAYER_NAMES=['overall_mean','overall_top3','pace_top5','dribble_top5','passing_top5','shooting_top3','physical_top5','defense_top5','setpiece_top2','reactions_mean','stamina_mean','gk_max']
def capability_features(player_ids,date):
    snaps=[]
    for pid in player_ids:
        ss=player_snapshot_strict(pid,date)
        if ss is not None: snaps.append(ss)
    def per(ss,cols):
        vals=[float(ss[c]) for c in cols if c in ss.index and pd.notna(ss[c])]
        return np.mean(vals) if vals else np.nan
    dim={
      'overall':[float(s.overall_rating) if pd.notna(s.overall_rating) else np.nan for s in snaps],
      'pace':[per(s,['acceleration','sprint_speed']) for s in snaps],
      'dribble':[per(s,['dribbling','ball_control','agility','balance']) for s in snaps],
      'passing':[per(s,['short_passing','long_passing','vision','crossing']) for s in snaps],
      'shooting':[per(s,['finishing','positioning','shot_power','long_shots','volleys']) for s in snaps],
      'physical':[per(s,['jumping','stamina','strength','heading_accuracy']) for s in snaps],
      'defense':[per(s,['interceptions','marking','standing_tackle','sliding_tackle']) for s in snaps],
      'setpiece':[per(s,['free_kick_accuracy','curve','crossing','penalties']) for s in snaps],
      'reactions':[per(s,['reactions']) for s in snaps],
      'stamina':[per(s,['stamina']) for s in snaps],
      'gk':[per(s,['gk_diving','gk_handling','gk_kicking','gk_positioning','gk_reflexes']) for s in snaps],
    }
    return {'coverage':len(snaps),
      'overall_mean':topmean(dim['overall']),'overall_top3':topmean(dim['overall'],3),
      'pace_top5':topmean(dim['pace'],5),'dribble_top5':topmean(dim['dribble'],5),'passing_top5':topmean(dim['passing'],5),
      'shooting_top3':topmean(dim['shooting'],3),'physical_top5':topmean(dim['physical'],5),'defense_top5':topmean(dim['defense'],5),
      'setpiece_top2':topmean(dim['setpiece'],2),'reactions_mean':topmean(dim['reactions']),'stamina_mean':topmean(dim['stamina']),'gk_max':topmean(dim['gk'],1)}

# Strictly-prior team tactical snapshot: team_attribute_date MUST be < match_date.
ta['team_api_id']=safe_num(ta['team_api_id']).astype('Int64'); ta=ta.dropna(subset=['team_api_id','date']).sort_values(['team_api_id','date'])
STYLE=['buildUpPlaySpeed','buildUpPlayDribbling','buildUpPlayPassing','chanceCreationPassing','chanceCreationCrossing','chanceCreationShooting','defencePressure','defenceTeamWidth']
for c in STYLE:
    if c in ta: ta[c]=safe_num(ta[c])
teamattr_lookup={int(tid):(g['date'].values.astype('datetime64[ns]'),g.reset_index(drop=True)) for tid,g in ta.groupby('team_api_id',sort=False)}
def team_snapshot_strict(tid,date):
    if pd.isna(tid): return None
    item=teamattr_lookup.get(int(tid))
    if item is None:return None
    dates,g=item; k=np.searchsorted(dates,np.datetime64(date),side='left')-1
    return None if k<0 else g.iloc[k]

# State is updated only AFTER all matches on a date are featurized.
sub=fm[fm['league_id'].isin(league_ids)].copy().sort_values(['date','id'])
result_state=defaultdict(lambda:{'hist':deque(maxlen=10),'last_date':None})
lineup_state=defaultdict(lambda:deque(maxlen=LOOKBACK_XI_MATCHES))
records=[]

league_dummy=[f'league_{lid}' for lid in league_ids[1:]]
HIST_NAMES=['ppg_diff','gfpg_diff','gapg_diff','gdpg_diff','drawrate_mean','drawrate_diff','rest_diff','hist_n_min','stage_frac']
STYLE_NAMES=[f'style_{c}_diff' for c in STYLE]
PLAYER_DIFF_NAMES=[f'player_{c}_diff' for c in PLAYER_NAMES]
MARKET=['market_log_h_d','market_log_a_d','market_p_draw']+league_dummy
HIST=MARKET+HIST_NAMES
STYLEF=HIST+STYLE_NAMES
FULL=STYLEF+PLAYER_DIFF_NAMES

def lineup_ids(row, side):
    out=[]
    for i in range(1,12):
        c=f'{side}_player_{i}'
        if c in row.index and pd.notna(row[c]):
            try: out.append(int(row[c]))
            except Exception: pass
    return out

def result_feat(team_id, d):
    x=result_state[team_id]; hist=list(x['hist']); n=len(hist)
    if n:
        ppg=np.mean([q[2] for q in hist]); gf=np.mean([q[0] for q in hist]); ga=np.mean([q[1] for q in hist]); dr=np.mean([q[3] for q in hist]); gd=gf-ga
    else: ppg=gf=ga=dr=gd=np.nan
    rest=(d-x['last_date']).days if x['last_date'] is not None else np.nan
    return {'n':n,'ppg':ppg,'gfpg':gf,'gapg':ga,'gdpg':gd,'drawrate':dr,'rest':rest}

def expected_xi(team_id):
    hist=list(lineup_state[team_id])
    usable=[x for x in hist if len(x)>=9]
    if len(usable)<MIN_PRIOR_LINEUP_MATCHES:
        return [], len(usable)
    counts=Counter(); last={}
    for j,players in enumerate(usable):
        for pid in players:
            counts[pid]+=1; last[pid]=j
    ranked=sorted(counts, key=lambda pid:(-counts[pid],-last[pid],pid))
    return ranked[:11], len(usable)

def make_row_features(r):
    ph,pd_,pa_,nb=market_consensus(r); eps=1e-6; f={}
    f['market_log_h_d']=np.log((ph+eps)/(pd_+eps)) if np.isfinite(ph) and np.isfinite(pd_) else np.nan
    f['market_log_a_d']=np.log((pa_+eps)/(pd_+eps)) if np.isfinite(pa_) and np.isfinite(pd_) else np.nan
    f['market_p_draw']=pd_
    h=int(r.home_team_api_id); a=int(r.away_team_api_id); d=r.date
    hr=result_feat(h,d); ar=result_feat(a,d)
    f.update({'ppg_diff':hr['ppg']-ar['ppg'],'gfpg_diff':hr['gfpg']-ar['gfpg'],'gapg_diff':hr['gapg']-ar['gapg'],'gdpg_diff':hr['gdpg']-ar['gdpg'],
              'drawrate_mean':np.nanmean([hr['drawrate'],ar['drawrate']]) if not (pd.isna(hr['drawrate']) and pd.isna(ar['drawrate'])) else np.nan,
              'drawrate_diff':hr['drawrate']-ar['drawrate'],'rest_diff':hr['rest']-ar['rest'],'hist_n_min':min(hr['n'],ar['n']),
              'stage_frac':float(r.stage)/38.0 if pd.notna(r.stage) else np.nan})
    hs=team_snapshot_strict(r.home_team_api_id,d); as_=team_snapshot_strict(r.away_team_api_id,d)
    style_complete=hs is not None and as_ is not None
    for c in STYLE:
        f[f'style_{c}_diff']=(float(hs[c])-float(as_[c])) if style_complete and c in hs.index and pd.notna(hs[c]) and pd.notna(as_[c]) else np.nan
    hxi,h_hist=expected_xi(h); axi,a_hist=expected_xi(a)
    hp=capability_features(hxi,d); ap=capability_features(axi,d)
    f['expected_xi_history_home']=h_hist; f['expected_xi_history_away']=a_hist
    f['expected_xi_size_home']=len(hxi); f['expected_xi_size_away']=len(axi)
    f['player_coverage_home']=hp['coverage']; f['player_coverage_away']=ap['coverage']
    for c in PLAYER_NAMES:
        f[f'player_{c}_diff']=hp[c]-ap[c] if pd.notna(hp[c]) and pd.notna(ap[c]) else np.nan
    for lid in league_ids[1:]: f[f'league_{lid}']=1.0 if int(r.league_id)==lid else 0.0
    f['style_complete']=1 if style_complete else 0; f['market_books_train']=nb
    return f

for d,g in sub.groupby('date',sort=True):
    # phase 1: all predictions/features on date use state ending strictly before date
    for _,r in g.iterrows():
        if d < TRAIN_START or d > MAX_TARGET_DATE: continue
        f=make_row_features(r)
        outcome='H' if r.home_team_goal>r.away_team_goal else 'A' if r.home_team_goal<r.away_team_goal else 'D'
        f.update({'external_rowid':int(r['_rowid']),'date':d,'league_id':int(r.league_id),'outcome':outcome})
        records.append(f)
    # phase 2: update histories only after all matches on date have been featurized
    for _,r in g.iterrows():
        h=int(r.home_team_api_id); a=int(r.away_team_api_id)
        hg=float(r.home_team_goal); ag=float(r.away_team_goal)
        hp=3 if hg>ag else 1 if hg==ag else 0; ap=3 if ag>hg else 1 if hg==ag else 0; dr=1 if hg==ag else 0
        result_state[h]['hist'].append((hg,ag,hp,dr)); result_state[a]['hist'].append((ag,hg,ap,dr)); result_state[h]['last_date']=d; result_state[a]['last_date']=d
        lineup_state[h].append(lineup_ids(r,'home')); lineup_state[a].append(lineup_ids(r,'away'))

feat=pd.DataFrame(records).set_index('external_rowid',drop=False)

# Fixed test identity map; archive market overrides external bookmaker market only for target rows.
test_rows=[]; arch_by={int(r.match_id):r for _,r in ARCH.iterrows()}
for _,m in validmap.iterrows():
    er=int(m.external_rowid)
    if er not in feat.index: continue
    rr=feat.loc[er].to_dict(); a=arch_by[int(m.match_id)]
    ph,pd_,pa_=float(a.last_p_home),float(a.last_p_draw),float(a.last_p_away); eps=1e-6
    rr['market_log_h_d']=np.log((ph+eps)/(pd_+eps)); rr['market_log_a_d']=np.log((pa_+eps)/(pd_+eps)); rr['market_p_draw']=pd_
    rr.update({'match_id':int(m.match_id),'archive_outcome':a.outcome,'archive_p_home':ph,'archive_p_draw':pd_,'archive_p_away':pa_,
               'identity_similarity':float(m.identity_similarity)})
    test_rows.append(rr)
test=pd.DataFrame(test_rows)
train=feat[(feat.date<CUTOFF)&(feat.date>=TRAIN_START)].copy()

# Common strict-chronology subset for M3-vs-M4 fairness.
def eligible(df):
    return (df.market_p_draw.notna() & (df.player_coverage_home>=MIN_PLAYER_SNAPSHOTS) & (df.player_coverage_away>=MIN_PLAYER_SNAPSHOTS) &
            (df.expected_xi_history_home>=MIN_PRIOR_LINEUP_MATCHES) & (df.expected_xi_history_away>=MIN_PRIOR_LINEUP_MATCHES) &
            (df.style_complete==1) & (df.hist_n_min>=5))
tr=train[eligible(train)].copy(); te=test[eligible(test)].copy()
print('strict train eligible',len(tr),'strict test eligible',len(te),'of',len(test),flush=True)
coverage_pre={'train_eligible':int(len(tr)),'test_eligible':int(len(te)),'identity_matched':int(len(validmap)),
              'test_expected_xi_ge9_both':int(((test.player_coverage_home>=MIN_PLAYER_SNAPSHOTS)&(test.player_coverage_away>=MIN_PLAYER_SNAPSHOTS)).sum()),
              'test_prior_lineups_ge3_both':int(((test.expected_xi_history_home>=MIN_PRIOR_LINEUP_MATCHES)&(test.expected_xi_history_away>=MIN_PRIOR_LINEUP_MATCHES)).sum()),
              'test_style_complete':int((test.style_complete==1).sum())}
test.to_csv(OUT/'test_feature_coverage.csv',index=False)
if len(tr)<MIN_TRAIN or len(te)<MIN_TEST:
    stop={'study_id':'R45B_PLAYER_CAPABILITY_STRICT_CHRONOLOGY','status':'STOP_COVERAGE','classification':'POSTVIEW_STRICT_CHRONOLOGY_REPAIR_FORMAL_WEIGHT_ZERO','formal_weight':0,
          'coverage':coverage_pre,'thresholds':{'min_train':MIN_TRAIN,'min_test':MIN_TEST},'promotion_allowed':False}
    (OUT/'result.json').write_text(json.dumps(stop,indent=2),encoding='utf-8')
    print(json.dumps(stop,indent=2)); raise SystemExit(0)

mis=(te['archive_outcome']!=te['outcome']).sum()
if mis: raise RuntimeError(f'OUTCOME_IDENTITY_CONFLICT n={mis}')

def fit_predict(cols):
    pipe=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('sc',StandardScaler()),('lr',LogisticRegression(C=1.0,solver='lbfgs',max_iter=2000,random_state=SEED))])
    pipe.fit(tr[cols],tr.outcome)
    p=pipe.predict_proba(te[cols]); cls=list(pipe.named_steps['lr'].classes_); order=[cls.index(x) for x in ['H','D','A']]
    return p[:,order]

pred_m3=fit_predict(STYLEF); pred_m4=fit_predict(FULL)
preds={'M3_strict_market_history_style':pred_m3,'M4_strict_expected_xi_player':pred_m4,'M0_archive_market_native':te[['archive_p_home','archive_p_draw','archive_p_away']].to_numpy(float)}

def rps(y,p):
    po=p[:,[2,1,0]]; yv=np.array([[1,0,0] if z=='A' else [0,1,0] if z=='D' else [0,0,1] for z in y])
    return float(np.mean(np.sum((np.cumsum(po,1)[:,:-1]-np.cumsum(yv,1)[:,:-1])**2,axis=1)/2))

def metrics(y,p):
    yarr=np.array(y); Y=np.array([[1,0,0] if z=='H' else [0,1,0] if z=='D' else [0,0,1] for z in yarr]); pred=np.array(['H','D','A'])[p.argmax(1)]
    return {'n':len(yarr),'logloss':float(-np.mean(np.sum(Y*np.log(np.clip(p,EPS,1)),axis=1))),
            'brier':float(np.mean(np.sum((p-Y)**2,axis=1))),'rps':rps(yarr,p),'accuracy':float(np.mean(pred==yarr)),
            'hits':int(np.sum(pred==yarr)),'draw_actual':int(np.sum(yarr=='D')),'draw_top1':int(np.sum(pred=='D')),'draw_hits':int(np.sum((pred=='D')&(yarr=='D'))),
            'draw_brier':float(np.mean((p[:,1]-(yarr=='D').astype(float))**2)),
            'draw_logloss':float(-np.mean((yarr=='D')*np.log(np.clip(p[:,1],EPS,1))+(yarr!='D')*np.log(np.clip(1-p[:,1],EPS,1)))),
            'draw_auc':float(roc_auc_score((yarr=='D').astype(int),p[:,1])) if len(np.unique(yarr=='D'))>1 else np.nan}

m={k:metrics(te.outcome.values,v) for k,v in preds.items()}
delta={'hits':m['M4_strict_expected_xi_player']['hits']-m['M3_strict_market_history_style']['hits'],
       'accuracy_pp':100*(m['M4_strict_expected_xi_player']['accuracy']-m['M3_strict_market_history_style']['accuracy']),
       'logloss':m['M4_strict_expected_xi_player']['logloss']-m['M3_strict_market_history_style']['logloss'],
       'brier':m['M4_strict_expected_xi_player']['brier']-m['M3_strict_market_history_style']['brier'],
       'rps':m['M4_strict_expected_xi_player']['rps']-m['M3_strict_market_history_style']['rps'],
       'draw_logloss':m['M4_strict_expected_xi_player']['draw_logloss']-m['M3_strict_market_history_style']['draw_logloss'],
       'draw_brier':m['M4_strict_expected_xi_player']['draw_brier']-m['M3_strict_market_history_style']['draw_brier']}

# Paired bootstrap LL delta M4-M3.
y=te.outcome.values; Y=np.array([[1,0,0] if z=='H' else [0,1,0] if z=='D' else [0,0,1] for z in y])
loss3=-np.sum(Y*np.log(np.clip(pred_m3,EPS,1)),axis=1); loss4=-np.sum(Y*np.log(np.clip(pred_m4,EPS,1)),axis=1); dd=loss4-loss3
rng=np.random.default_rng(SEED); vals=np.empty(BOOTSTRAP_N); n=len(dd)
for i in range(BOOTSTRAP_N): vals[i]=dd[rng.integers(0,n,n)].mean()
boot={'delta_logloss':float(dd.mean()),'ci90':[float(np.quantile(vals,.05)),float(np.quantile(vals,.95))],'bootstrap_n':BOOTSTRAP_N,'seed':SEED}

# League robustness on common strict subset.
per_league={}; league_nonworse=0
for lid,g in te.groupby('league_id'):
    pos=np.array([te.index.get_loc(x) for x in g.index])
    a=metrics(g.outcome.values,pred_m3[pos]); b=metrics(g.outcome.values,pred_m4[pos]); dll=b['logloss']-a['logloss']
    per_league[str(int(lid))]={'n':len(g),'m3':a,'m4':b,'m4_minus_m3_logloss':dll,'m4_minus_m3_hits':b['hits']-a['hits']}
    league_nonworse += int(dll<=1e-12)

gate={'top1_nonnegative':delta['hits']>=0,'logloss_improves':delta['logloss']<0,'brier_improves':delta['brier']<0,'rps_improves':delta['rps']<0,
      'draw_logloss_nonworse':delta['draw_logloss']<=0,'draw_brier_nonworse':delta['draw_brier']<=0,'at_least_4_of_5_leagues_logloss_nonworse':league_nonworse>=4,
      'bootstrap90_upper_lt_zero':boot['ci90'][1]<0}
gate['passed']=all(gate.values())

pout=te[['match_id','date','league_id','outcome','archive_outcome','identity_similarity','expected_xi_history_home','expected_xi_history_away','expected_xi_size_home','expected_xi_size_away','player_coverage_home','player_coverage_away']].reset_index(drop=True)
for name,p in preds.items():
    pout[f'{name}_pH']=p[:,0]; pout[f'{name}_pD']=p[:,1]; pout[f'{name}_pA']=p[:,2]; pout[f'{name}_pred']=np.array(['H','D','A'])[p.argmax(1)]
pout.to_csv(OUT/'predictions.csv',index=False)

receipt={'study_id':'R45B_PLAYER_CAPABILITY_STRICT_CHRONOLOGY','status':'COMPLETE','classification':'POSTVIEW_STRICT_CHRONOLOGY_REPAIR_FORMAL_WEIGHT_ZERO','formal_weight':0,
         'governance':{'r45a_labels_already_consumed':True,'promotion_allowed':False,'target_match_xi_used':False,'same_date_lineup_updates_before_prediction':False,
                       'player_snapshot_rule':'attribute_date < match_date','team_style_snapshot_rule':'attribute_date < match_date','odds_used_for_training_baseline':True,
                       'draw_override':False,'draw_threshold':False,'class_weight':False,'feature_search':False,'hyperparameter_search':False},
         'design':{'expected_xi_rule':{'lookback_completed_matches':LOOKBACK_XI_MATCHES,'min_prior_lineup_matches':MIN_PRIOR_LINEUP_MATCHES,'ranking':'prior starts desc, recency desc, player_id asc','target_xi_invisible':True},
                   'model':'SimpleImputer(median,+indicator)+StandardScaler+multinomial LogisticRegression(C=1.0,lbfgs,max_iter=2000)','player_features':PLAYER_NAMES,
                   'baseline':'M3 strict market+history+team-style','candidate':'M3 + strict-prior expected-XI player capability diffs'},
         'sample':{'fixed_r45a_sample_n':300,'cutoff':str(CUTOFF.date()),'max_target_date':str(MAX_TARGET_DATE.date()),'league_ids':league_ids},
         'coverage':coverage_pre,'metrics':m,'candidate_minus_baseline':delta,'bootstrap_m4_minus_m3':boot,'per_league':per_league,'league_logloss_nonworse_count':league_nonworse,
         'gate':gate,'action':'ARCHITECTURE_WORTH_INDEPENDENT_FOLLOWUP_ONLY' if gate['passed'] else 'DO_NOT_PROMOTE_OR_RETUNE_R45B_ON_CONSUMED_SAMPLE'}
(OUT/'result.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'coverage':coverage_pre,'candidate_minus_baseline':delta,'bootstrap':boot,'league_nonworse':league_nonworse,'gate':gate,'action':receipt['action']},ensure_ascii=False,indent=2),flush=True)
