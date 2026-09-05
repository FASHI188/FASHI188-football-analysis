from __future__ import annotations

import argparse
import collections
import difflib
import json
import math
import pathlib
import re
import sqlite3
import statistics
import sys
import unicodedata
from datetime import datetime, timezone

EPS = 1e-15
LEAGUE_MAP = {
    "matches_England.json": "EPL",
    "matches_Spain.json": "La liga",
    "matches_Italy.json": "Serie A",
    "matches_Germany.json": "Bundesliga",
    "matches_France.json": "Ligue 1",
}
EXPECTED = {"EPL":380,"La liga":380,"Serie A":380,"Bundesliga":306,"Ligue 1":380}


def norm_name(s: str) -> str:
    x = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    x = re.sub(r"[^a-z0-9]+", "", x)
    aliases = {
        "internazionale":"inter", "internazionalemilano":"inter", "intermilan":"inter",
        "1fckoln":"cologne", "fckoln":"cologne", "fccologne":"cologne",
        "bayernmunchen":"bayernmunich", "rasenballsportleipzig":"rbleipzig",
        "parissaintgermain":"psg", "olympiquemarseille":"marseille",
        "olympiquelyonnais":"lyon", "asmonaco":"monaco", "losclille":"lille",
        "fcgirondinsdebordeaux":"bordeaux", "staderennais":"rennes",
        "enavantguingamp":"guingamp", "estactroyes":"troyes", "smcaen":"caen",
        "fcmetz":"metz", "toulousefc":"toulouse", "scoangers":"angers",
        "brightonhovealbion":"brighton", "huddersfieldtown":"huddersfield",
        "westbromwichalbion":"westbromwich", "afcbournemouth":"bournemouth",
        "athleticclub":"athleticbilbao", "deportivolacoruna":"deportivo",
        "realclubdeportivodelacoruna":"deportivo", "spal2013":"spal", "hellasverona":"verona",
    }
    return aliases.get(x, x)


def name_sim(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) * 0.25 + 0.75
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_wyscout_readme(root: pathlib.Path) -> list[dict]:
    text=(root/'processed-v2/README.md').read_text(encoding='utf-8')
    pat=re.compile(r'^\|\[(\d+)\]\(files/(\d+)\.json\)\|(.*?)\|(.*?)\|(matches_[A-Za-z_]+\.json)\|$', re.M)
    rows=[]
    for a,b,label,date_text,src in pat.findall(text):
        if a != b or src not in LEAGUE_MAP:
            continue
        m=re.match(r'^(.*?) - (.*?),\s*(-?\d+)\s*-\s*(-?\d+)(?:\s+.*)?$', label)
        if not m:
            raise RuntimeError(f'cannot parse match label: {label}')
        home,away=m.group(1).strip(),m.group(2).strip()
        d=datetime.strptime(date_text.split(' at ')[0], '%B %d, %Y').date().isoformat()
        p=root/f'processed-v2/files/{a}.json'
        obj=json.load(open(p,encoding='utf-8'))
        ev=obj.get('events')
        if not isinstance(ev,list) or not ev:
            raise RuntimeError(f'empty events {a}')
        teams=sorted({int(e.get('teamId',0) or 0) for e in ev if int(e.get('teamId',0) or 0)>0})
        if len(teams)!=2:
            raise RuntimeError(f'not exactly two team ids {a}: {teams}')
        rows.append({'wys_match_id':int(a),'source_file':src,'league':LEAGUE_MAP[src],
                     'date':d,'home_name':home,'away_name':away,'team_ids':teams,'events':ev})
    if len(rows)!=1826:
        raise RuntimeError(f'Wyscout Big5 target drift: {len(rows)}')
    return rows


def recover_wyscout_team_ids(matches: list[dict]) -> dict[tuple[str,str],int]:
    counts=collections.defaultdict(collections.Counter)
    appearances=collections.Counter()
    for m in matches:
        for side in ('home_name','away_name'):
            k=(m['source_file'], norm_name(m[side])); appearances[k]+=1
            for tid in m['team_ids']:
                counts[k][tid]+=1
    mapping={}
    for k,c in counts.items():
        best=c.most_common()
        if not best or best[0][1] != appearances[k]:
            raise RuntimeError(f'cannot recover Wyscout team id for {k}: {best[:4]} appearances={appearances[k]}')
        if len(best)>1 and best[1][1] == best[0][1]:
            raise RuntimeError(f'ambiguous Wyscout team id for {k}: {best[:4]}')
        mapping[k]=int(best[0][0])
    for m in matches:
        h=mapping[(m['source_file'],norm_name(m['home_name']))]
        a=mapping[(m['source_file'],norm_name(m['away_name']))]
        if h==a or sorted([h,a])!=m['team_ids']:
            raise RuntimeError(f'Wyscout home-away id recovery mismatch: {m["wys_match_id"]}')
        m['home_wys_id']=h; m['away_wys_id']=a
    return mapping


def load_understat(db: pathlib.Path) -> tuple[list[dict],list[dict]]:
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    leagues=tuple(EXPECTED)
    qs=','.join('?' for _ in leagues)
    hist=[dict(r) for r in con.execute(f'''select id,fid,date,league,season,h_id,a_id,h_goals,a_goals,team_h,team_a
      from general_game_stats where league in ({qs}) and season between 2014 and 2017 order by date,id''', leagues)]
    target=[r for r in hist if int(r['season'])==2017]
    con.close()
    got=collections.Counter(r['league'] for r in target)
    if dict(got)!=EXPECTED:
        raise RuntimeError(f'Understat 2017 identity drift: {got}')
    if len(target)!=1826:
        raise RuntimeError(f'Understat target drift: {len(target)}')
    return hist,target


def map_identity(wys: list[dict], under_target: list[dict]) -> tuple[dict[int,dict],dict]:
    groups=collections.defaultdict(list)
    for u in under_target:
        groups[(u['league'],str(u['date'])[:10])].append(u)
    used=set(); out={}; evidence=[]; low=[]
    for w in sorted(wys,key=lambda z:(z['date'],z['source_file'],z['wys_match_id'])):
        cand=[u for u in groups[(w['league'],w['date'])] if int(u['id']) not in used]
        if not cand:
            raise RuntimeError(f'no Understat identity candidate {w["wys_match_id"]} {w["date"]} {w["home_name"]}-{w["away_name"]}')
        scored=[]
        for u in cand:
            hs=name_sim(w['home_name'],u['team_h']); as_=name_sim(w['away_name'],u['team_a'])
            rev=name_sim(w['home_name'],u['team_a'])+name_sim(w['away_name'],u['team_h'])
            direct=hs+as_
            scored.append((direct, direct-rev, hs, as_, u))
        scored.sort(key=lambda x:(x[0],x[1]),reverse=True)
        best=scored[0]; second=scored[1][0] if len(scored)>1 else -1.0
        if best[0] < 1.15 or best[0]-second < 0.10 or best[1] <= 0.05:
            low.append({'w':w['wys_match_id'],'date':w['date'],'home':w['home_name'],'away':w['away_name'],
                        'best_score':best[0],'margin':best[0]-second,'orientation_margin':best[1],
                        'under_home':best[4]['team_h'],'under_away':best[4]['team_a']})
            continue
        u=best[4]; used.add(int(u['id'])); out[w['wys_match_id']]=u
        evidence.append({'wys_match_id':w['wys_match_id'],'understat_id':int(u['id']),'understat_fid':int(u['fid']),
                         'league':w['league'],'date':w['date'],'wys_home':w['home_name'],'wys_away':w['away_name'],
                         'under_home':u['team_h'],'under_away':u['team_a'],'name_score':best[0],'runner_up_margin':best[0]-second})
    if low:
        raise RuntimeError('identity mapping low-confidence examples: '+json.dumps(low[:20],ensure_ascii=False))
    if len(out)!=1826 or len(used)!=1826:
        raise RuntimeError(f'identity mapping incomplete: map={len(out)} used={len(used)}')
    return out, {'mapped_n':len(out),'min_name_score':min(x['name_score'] for x in evidence),
                 'min_runner_up_margin':min(x['runner_up_margin'] for x in evidence),'rows':evidence}


def build_source_features(wys: list[dict], identity: dict[int,dict], lookback: int, min_prior: int) -> tuple[dict[int,dict],dict]:
    profiles={}
    for m in wys:
        z={m['home_wys_id']:{'events':0,'counter':0},m['away_wys_id']:{'events':0,'counter':0}}
        for e in m['events']:
            tid=int(e.get('teamId',0) or 0)
            if tid not in z: continue
            z[tid]['events']+=1
            tags={int(t.get('id')) for t in (e.get('tags') or []) if isinstance(t,dict) and t.get('id') is not None}
            if 1901 in tags: z[tid]['counter']+=1
        profiles[m['wys_match_id']]=z
    states=collections.defaultdict(lambda:collections.deque(maxlen=lookback))
    bydate=collections.OrderedDict()
    for m in sorted(wys,key=lambda z:(z['date'],z['source_file'],z['wys_match_id'])):
        bydate.setdefault(m['date'],[]).append(m)
    fmap={}; covered=0
    for _,batch in bydate.items():
        for m in batch:
            vals=[]
            for tid in (m['home_wys_id'],m['away_wys_id']):
                q=states[(m['source_file'],tid)]
                if len(q)<min_prior:
                    vals.append(None); continue
                den=sum(x['events'] for x in q); num=sum(x['counter'] for x in q)
                vals.append(num/den if den>0 else None)
            rec={'home_rate':vals[0],'away_rate':vals[1]}
            if vals[0] is not None and vals[1] is not None:
                rec['direction']=float(vals[0]-vals[1]); rec['openness']=float(0.5*(vals[0]+vals[1])); covered+=1
            uid=int(identity[m['wys_match_id']]['id']); fmap[uid]=rec
        for m in batch:
            p=profiles[m['wys_match_id']]
            states[(m['source_file'],m['home_wys_id'])].append(p[m['home_wys_id']])
            states[(m['source_file'],m['away_wys_id'])].append(p[m['away_wys_id']])
    if len(fmap)!=1826: raise RuntimeError(f'feature map identity drift {len(fmap)}')
    return fmap, {'target_n':1826,'bilateral_feature_n':covered,'bilateral_feature_coverage':covered/1826}


def utc(s: str) -> datetime:
    return datetime.fromisoformat(str(s)).replace(tzinfo=timezone.utc)


def build_baseline(hist: list[dict], pure_engine_path: pathlib.Path, selected: dict) -> dict[int,dict]:
    sys.path.insert(0,str(pure_engine_path.parent)); import pure_engine as pe
    params=pe.Parameters(**selected); eng=pe.EngineState(params=params)
    bytime=collections.OrderedDict()
    for r in hist: bytime.setdefault(str(r['date']),[]).append(r)
    out={}
    for _,batch in bytime.items():
        batch=sorted(batch,key=lambda z:int(z['id'])); fs=[]; labels={}
        for r in batch:
            f=pe.Fixture(f"understat:{int(r['fid'])}",str(r['league']),str(r['season']),utc(r['date']),str(r['h_id']),str(r['a_id']))
            fs.append(f); labels[f.fixture_id]=(int(r['h_goals']),int(r['a_goals']))
            if int(r['season'])==2017:
                p=eng.predict(f)
                out[int(r['id'])]={'base_prob':[float(p['p_home']),float(p['p_draw']),float(p['p_away'])],
                                  'base_matrix':p['score_matrix'],'fixture_id':f.fixture_id}
        eng.apply_batch(fs,labels)
    if len(out)!=1826: raise RuntimeError(f'baseline target drift {len(out)}')
    return out


def standardize(train: list[dict], key: str) -> tuple[float,float]:
    vals=[float(r[key]) for r in train if r.get(key) is not None]
    if not vals: raise RuntimeError(f'no train feature {key}')
    mu=statistics.fmean(vals); sd=statistics.pstdev(vals)
    if not math.isfinite(sd) or sd<=0: raise RuntimeError(f'invalid sd {key} {sd}')
    return mu,sd


def softmax_scores(base: list[float], z1: float, z2: float, b1: float, b2: float) -> list[float]:
    s=[math.log(max(EPS,base[0]))+b1*z1, math.log(max(EPS,base[1]))+b2*z2, math.log(max(EPS,base[2]))-b1*z1]
    m=max(s); e=[math.exp(x-m) for x in s]; den=sum(e); return [x/den for x in e]


def fit_newton(train: list[dict], ridge: float, max_iter: int, tol: float) -> dict:
    mu1,sd1=standardize(train,'direction'); mu2,sd2=standardize(train,'openness'); b1=b2=0.0
    for _ in range(max_iter):
        g1=2*ridge*b1; g2=2*ridge*b2; h11=2*ridge; h22=2*ridge; h12=0.0
        for r in train:
            if r.get('direction') is None: z1=z2=0.0
            else: z1=(float(r['direction'])-mu1)/sd1; z2=(float(r['openness'])-mu2)/sd2
            q=softmax_scores(r['base_prob'],z1,z2,b1,b2); y=int(r['y'])
            ih=1.0 if y==0 else 0.0; idr=1.0 if y==1 else 0.0; ia=1.0 if y==2 else 0.0
            g1 += z1*((q[0]-ih)-(q[2]-ia)); g2 += z2*(q[1]-idr)
            h11 += z1*z1*((q[0]+q[2])-(q[0]-q[2])**2)
            h22 += z2*z2*q[1]*(1-q[1]); h12 += -z1*z2*q[1]*(q[0]-q[2])
        det=h11*h22-h12*h12
        if det<=1e-18 or not math.isfinite(det): raise RuntimeError(f'Newton Hessian singular: {det}')
        d1=(h22*g1-h12*g2)/det; d2=(-h12*g1+h11*g2)/det; b1-=d1; b2-=d2
        if max(abs(d1),abs(d2))<tol: break
    return {'direction_mean':mu1,'direction_sd':sd1,'openness_mean':mu2,'openness_sd':sd2,'beta_direction':b1,'beta_openness':b2}


def predict_candidate(r: dict, fit: dict) -> list[float]:
    if r.get('direction') is None: z1=z2=0.0
    else:
        z1=(float(r['direction'])-fit['direction_mean'])/fit['direction_sd']; z2=(float(r['openness'])-fit['openness_mean'])/fit['openness_sd']
    return softmax_scores(r['base_prob'],z1,z2,fit['beta_direction'],fit['beta_openness'])


def rescale_matrix(cells: list[dict], base: list[float], cand: list[float]) -> list[dict]:
    ratios=[cand[i]/max(EPS,base[i]) for i in range(3)]; out=[]
    for c in cells:
        h=int(c['home_goals']); a=int(c['away_goals']); cls=0 if h>a else 1 if h==a else 2
        out.append({'home_goals':h,'away_goals':a,'probability':float(c['probability'])*ratios[cls]})
    tot=sum(x['probability'] for x in out)
    for x in out: x['probability']/=tot
    return out


def metric(prob: list[float], y: int) -> dict:
    pick=max(range(3),key=lambda i:prob[i]); ll=-math.log(max(EPS,prob[y]))
    b=sum((prob[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
    rps=((prob[0]-(1.0 if y==0 else 0.0))**2+((prob[0]+prob[1])-(1.0 if y<=1 else 0.0))**2)/2
    return {'hit':int(pick==y),'logloss':ll,'brier':b,'rps':rps}


def score_ll(cells: list[dict], hg: int, ag: int) -> float:
    p=next((float(c['probability']) for c in cells if int(c['home_goals'])==hg and int(c['away_goals'])==ag),EPS)
    return -math.log(max(EPS,p))


def total_ll(cells: list[dict], t: int) -> float:
    p=sum(float(c['probability']) for c in cells if int(c['home_goals'])+int(c['away_goals'])==t)
    return -math.log(max(EPS,p))


def agg(scored: list[dict]) -> dict:
    n=len(scored); out={'n':n}
    for k in ('hit','logloss','brier','rps','exact_score_ll','total_goals_ll'):
        out['top1_accuracy' if k=='hit' else k]=sum(float(x[k]) for x in scored)/n
    out['hits']=sum(int(x['hit']) for x in scored); return out


def date_cut(rows: list[dict], frac: float) -> int:
    n=len(rows); i=max(1,min(n-1,int(n*frac))); d=rows[i-1]['date']
    while i<n and rows[i]['date']==d: i+=1
    return i


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True)
    ap.add_argument('--wys-root',type=pathlib.Path,required=True); ap.add_argument('--pure-engine',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_BEFORE_OLD_SOURCE_SCREENING_EVALUATION'; assert c['feature']['lookback_completed_league_matches']==8; assert c['feature']['minimum_prior_matches']==3
    assert c['model']['fixed_ridge']==0.01 and c['model']['no_hyperparameter_grid'] and c['model']['no_rescue_if_fail']
    wys=parse_wyscout_readme(a.wys_root); recover_wyscout_team_ids(wys); hist,target=load_understat(a.db)
    ident,irec=map_identity(wys,target); fmap,faudit=build_source_features(wys,ident,8,3); base=build_baseline(hist,a.pure_engine,c['baseline']['selected_parameters'])
    rows=[]
    for u in target:
        uid=int(u['id']); rec={'understat_id':uid,'fixture_id':base[uid]['fixture_id'],'date':str(u['date'])[:10],'kickoff':str(u['date']),
              'league':u['league'],'home':u['team_h'],'away':u['team_a'],'home_goals':int(u['h_goals']),'away_goals':int(u['a_goals']),
              'y':0 if int(u['h_goals'])>int(u['a_goals']) else 1 if int(u['h_goals'])==int(u['a_goals']) else 2,
              'base_prob':base[uid]['base_prob'],'base_matrix':base[uid]['base_matrix']}
        rec.update(fmap[uid]); rows.append(rec)
    rows.sort(key=lambda r:(r['kickoff'],r['understat_id'])); cov=faudit['bilateral_feature_coverage']
    assert cov>=c['gates']['minimum_bilateral_feature_coverage'],(cov,c['gates'])
    c40,c60,c80=date_cut(rows,.40),date_cut(rows,.60),date_cut(rows,.80); folds=[(0,c40,c60),(0,c60,c80),(0,c80,len(rows))]
    pooled_base=[]; pooled_cand=[]; fold_results=[]; max_delta=0.0
    for fi,(s,tr_end,te_end) in enumerate(folds,1):
        train=rows[s:tr_end]; test=rows[tr_end:te_end]
        fit=fit_newton(train,float(c['model']['fixed_ridge']),int(c['model']['max_iterations']),float(c['model']['convergence_abs_step']))
        sb=[]; sc=[]
        for r in test:
            cp=predict_candidate(r,fit); max_delta=max(max_delta,max(abs(cp[i]-r['base_prob'][i]) for i in range(3))); cm=rescale_matrix(r['base_matrix'],r['base_prob'],cp)
            mb=metric(r['base_prob'],r['y']); mc=metric(cp,r['y'])
            mb['exact_score_ll']=score_ll(r['base_matrix'],r['home_goals'],r['away_goals']); mb['total_goals_ll']=total_ll(r['base_matrix'],r['home_goals']+r['away_goals'])
            mc['exact_score_ll']=score_ll(cm,r['home_goals'],r['away_goals']); mc['total_goals_ll']=total_ll(cm,r['home_goals']+r['away_goals'])
            sb.append(mb); sc.append(mc); pooled_base.append(mb); pooled_cand.append(mc)
        ab,ac=agg(sb),agg(sc); deltas={'hits':ac['hits']-ab['hits'],'top1_pp':100*(ac['top1_accuracy']-ab['top1_accuracy'])}
        for k in ('logloss','brier','rps','exact_score_ll','total_goals_ll'): deltas[k]=ac[k]-ab[k]
        fold_results.append({'fold':fi,'train_n':len(train),'test_n':len(test),'train_end_date':train[-1]['date'],'test_first_date':test[0]['date'],'test_last_date':test[-1]['date'],'fit':fit,'baseline':ab,'candidate':ac,'deltas':deltas})
    pb,pc=agg(pooled_base),agg(pooled_cand); pd={'hits':pc['hits']-pb['hits'],'top1_pp':100*(pc['top1_accuracy']-pb['top1_accuracy'])}
    for k in ('logloss','brier','rps','exact_score_ll','total_goals_ll'): pd[k]=pc[k]-pb[k]
    fold_nonworse=sum(fr['deltas']['logloss']<=0 and fr['deltas']['rps']<=0 for fr in fold_results)
    checks={'feature_coverage':cov>=c['gates']['minimum_bilateral_feature_coverage'],'max_probability_delta':max_delta<=c['gates']['max_outcome_probability_abs_delta'],
      'top1':pd['hits']>=c['gates']['pooled_top1_hit_delta_min'],'logloss':pd['logloss']<=c['gates']['pooled_logloss_delta_max'],
      'brier':pd['brier']<=c['gates']['pooled_brier_delta_max'],'rps':pd['rps']<=c['gates']['pooled_rps_delta_max'],
      'exact_score_ll':pd['exact_score_ll']<=c['gates']['pooled_exact_score_logloss_delta_max'],'total_goals_ll':pd['total_goals_ll']<=c['gates']['pooled_total_goals_logloss_delta_max'],
      'fold_consistency':fold_nonworse>=c['gates']['minimum_folds_with_logloss_and_rps_nonworse']}
    passed=all(checks.values()); status='COUNTERATTACK_REGIME_OLD_SOURCE_SCREEN_PASS_REQUIRES_NEWER_SOURCE_CONFIRMATION' if passed else 'COUNTERATTACK_REGIME_OLD_SOURCE_SCREEN_FAIL_CLOSE_EXACT_FORMULATION_NO_RETUNE'
    result={'schema_version':'football3-wyscout-counterattack-regime-old-source-screen-result-v1','status':status,'screening_pass':passed,'research_class':c['research_class'],
            'target_n':len(rows),'rolling_oos_n':len(pooled_base),'feature_audit':faudit,'identity_audit':{k:v for k,v in irec.items() if k!='rows'},
            'fold_results':fold_results,'pooled':{'baseline':pb,'candidate':pc,'deltas':pd},'max_probability_abs_delta':max_delta,
            'folds_nonworse_logloss_and_rps':fold_nonworse,'checks':checks,'formal_weight':0,'newer_source_confirmation_required_if_pass':True,
            'historical_confirmation_2023_opened':False,'prospective_1335_touched':False,'CURRENT_changed':False,'formal_model_changed':False,'retune_allowed':False}
    (a.out/'COUNTERATTACK_REGIME_SCREEN_RESULT.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    (a.out/'IDENTITY_MAPPING.jsonl').write_text(''.join(json.dumps(x,sort_keys=True,ensure_ascii=False)+'\n' for x in irec['rows']))
    print(json.dumps({'status':status,'coverage':cov,'rolling_oos_n':len(pooled_base),'pooled_deltas':pd,'checks':checks,'max_probability_abs_delta':max_delta},sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
