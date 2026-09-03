from __future__ import annotations
import argparse, hashlib, heapq, importlib.util, json, math, pathlib, random, sqlite3, sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from statistics import stdev

TOL=1e-12

class StressError(RuntimeError): pass

def loadmod(name,p):
    spec=importlib.util.spec_from_file_location(name,str(p))
    if spec is None or spec.loader is None: raise StressError(f"cannot import {p}")
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def sha_file(p):
    h=hashlib.sha256()
    with pathlib.Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def canon(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def canon_sha(o): return hashlib.sha256(canon(o)).hexdigest()
def write_json(p,o):
    p=pathlib.Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
def write_jsonl(p,rows):
    with pathlib.Path(p).open('w',encoding='utf-8') as f:
        for r in rows: f.write(canon(r).decode()+'\n')
def parse_iso(s):
    d=datetime.fromisoformat(str(s))
    if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)

def read_jsonl(p):
    with pathlib.Path(p).open('r',encoding='utf-8') as f:
        return [json.loads(x) for x in f if x.strip()]

def load_data_manifest(data_dir,contract):
    d=json.loads((data_dir/'data_manifest.json').read_text())
    if d.get('status')!='HISTORICAL_REPLAY_DATA_READY': raise StressError('data manifest status')
    if d.get('post_view_historical_stress_test') is not True or d.get('fresh_confirmation') is not False: raise StressError('data classification')
    if d.get('seasons')!=[2024,2025]: raise StressError('data seasons')
    if int(d.get('fixture_n',0))<3400: raise StressError('data fixture count unexpectedly small')
    for n,key in [('fixtures.jsonl','fixture_store_sha256'),('state_updates.jsonl','state_update_store_sha256'),('label_vault.jsonl','label_vault_sha256')]:
        if sha_file(data_dir/n)!=d[key]: raise StressError(f'data SHA drift {n}')
    return d

def old_process_state(usr,db):
    leagues=('Bundesliga','EPL','La liga','Ligue 1','Serie A'); qs=','.join('?' for _ in leagues)
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    games=[dict(r) for r in con.execute(f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({qs}) and season between 2014 and 2023 order by date,id",leagues)]
    ev={}
    sql=f"""select e.match_id,e.h_a,
      sum(case when e.situation!='Penalty' then e.xG else 0 end) npxg,
      sum(case when e.situation!='Penalty' then 1 else 0 end) npshots,
      sum(case when e.situation='OpenPlay' then 1 else 0 end) open_n,
      sum(case when e.situation in ('SetPiece','FromCorner','DirectFreekick') then 1 else 0 end) set_n
      from game_events e join general_game_stats g on g.id=e.match_id
      where g.league in ({qs}) and g.season between 2014 and 2023
      group by e.match_id,e.h_a"""
    for r in con.execute(sql,leagues): ev[(str(r['match_id']),r['h_a'])]=dict(r)
    con.close()
    def side_stats(g,side):
        me=ev.get((str(g['id']),side)); opp=ev.get((str(g['id']),'a' if side=='h' else 'h'))
        if not me or not opp or float(me['npshots'] or 0)<=0 or float(opp['npshots'] or 0)<=0: return None
        return {'npxg_for':float(me['npxg'] or 0.0),'npxg_against':float(opp['npxg'] or 0.0),
                'npshots_for':float(me['npshots']),'npshots_against':float(opp['npshots']),
                'npxg_per_shot':float(me['npxg'] or 0.0)/float(me['npshots']),
                'open_share':float(me['open_n'] or 0.0)/float(me['npshots']),
                'set_share':float(me['set_n'] or 0.0)/float(me['npshots'])}
    pools=defaultdict(lambda:defaultdict(list))
    for g in games:
        if int(g['season'])<=2017:
            for side in ('h','a'):
                s=side_stats(g,side)
                if s:
                    for k,v in s.items(): pools[g['league']][k].append(v)
    priors={l:{k:sum(v)/len(v) for k,v in d.items()} for l,d in pools.items()}
    if set(priors)!=set(leagues): raise StressError('process priors incomplete')
    states=defaultdict(usr.TeamState); queue=[]; seq=0
    for g in games:
        ko=datetime.strptime(g['date'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        while queue and queue[0][0]<=ko:
            _,_,updates=heapq.heappop(queue)
            for team,stats,t in updates: states[str(team)].add(t,stats)
        rel=ko+timedelta(hours=3); updates=[]; hs=side_stats(g,'h'); aa=side_stats(g,'a')
        if hs: updates.append((str(g['h_id']),hs,rel))
        if aa: updates.append((str(g['a_id']),aa,rel))
        if updates: seq+=1; heapq.heappush(queue,(rel,seq,updates))
    return states,priors,queue,seq,{'old_game_n':len(games),'pending_release_groups':len(queue)}

def release_process(queue,states,now):
    while queue and queue[0][0]<=now:
        _,_,updates=heapq.heappop(queue)
        for team,stats,t in updates: states[str(team)].add(t,stats)

def team_num(prefixed):
    s=str(prefixed)
    if not s.startswith('understat-team:'): raise StressError('team id namespace')
    q=s.split(':',1)[1]
    if not q.isdigit(): raise StressError('team id numeric')
    return q

def process_profile(usr,states,priors,fixture):
    league=fixture['league']; lp=priors.get(league)
    if not lp: return {'valid':False,'home':None,'away':None,'home_weight':0.0,'away_weight':0.0}
    ko=parse_iso(fixture['kickoff'])
    def prof(team):
        w,s=states[team_num(team)].snapshot(ko)
        if w<usr.MIN_PROFILE_WEIGHT: return None,w
        return {k:(s.get(k,0.0)+usr.PRIOR_MATCHES*lp[k])/(w+usr.PRIOR_MATCHES) for k in lp},w
    h,hw=prof(fixture['home_team_id']); a,aw=prof(fixture['away_team_id'])
    return {'valid':bool(h and a),'home':h,'away':a,'home_weight':hw,'away_weight':aw}

def process_updates_from_row(fix,u):
    if str(u['fixture_id'])!=str(fix['fixture_id']): raise StressError('process update identity')
    if parse_iso(u['release_at'])!=parse_iso(fix['kickoff'])+timedelta(hours=3): raise StressError('process release')
    if not bool(u['process_update_eligible']): return []
    need=['home_npxg','away_npxg','home_nonpenalty_shots','away_nonpenalty_shots','home_npxg_per_shot','away_npxg_per_shot',
          'home_open_play_share','away_open_play_share','home_set_piece_share','away_set_piece_share']
    if any(u.get(k) is None for k in need): raise StressError('eligible process row missing field')
    rel=parse_iso(u['release_at'])
    hs={'npxg_for':float(u['home_npxg']),'npxg_against':float(u['away_npxg']),
        'npshots_for':float(u['home_nonpenalty_shots']),'npshots_against':float(u['away_nonpenalty_shots']),
        'npxg_per_shot':float(u['home_npxg_per_shot']),'open_share':float(u['home_open_play_share']),'set_share':float(u['home_set_piece_share'])}
    aa={'npxg_for':float(u['away_npxg']),'npxg_against':float(u['home_npxg']),
        'npshots_for':float(u['away_nonpenalty_shots']),'npshots_against':float(u['home_nonpenalty_shots']),
        'npxg_per_shot':float(u['away_npxg_per_shot']),'open_share':float(u['away_open_play_share']),'set_share':float(u['away_set_piece_share'])}
    return [(team_num(fix['home_team_id']),hs,rel),(team_num(fix['away_team_id']),aa,rel)]

def batches(fixtures):
    out=[]; cur=[]; key=None
    for r in fixtures:
        k=parse_iso(r['kickoff'])
        if key is None or k==key: cur.append(r); key=k
        else: out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out

def bootstrap_pair(d1,dscore,reps,seed):
    n=len(d1); rng=random.Random(seed); a=[]; b=[]
    for _ in range(reps):
        s1=ss=0.0
        for _j in range(n):
            i=rng.randrange(n); s1+=d1[i]; ss+=dscore[i]
        a.append(s1/n); b.append(ss/n)
    def pct(x,q):
        x=sorted(x); pos=(len(x)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
        if lo==hi:return x[lo]
        w=pos-lo; return x[lo]*(1-w)+x[hi]*w
    return {'reps':reps,'seed':seed,
            'one_x_two_logloss_gain':{'mean':sum(a)/len(a),'ci95':[pct(a,.025),pct(a,.975)]},
            'exact_score_logloss_gain':{'mean':sum(b)/len(b),'ci95':[pct(b,.025),pct(b,.975)]}}

def main():
    ap=argparse.ArgumentParser()
    for n in ['stress-contract','joint-contract','joint-engine','v31-contract','v31-engine','v31-final','usr1-engine','v2-engine','xg-engine','v1-engine','v1-result','db','xg-identity','data-dir','out']:
        ap.add_argument('--'+n,type=pathlib.Path,required=True)
    a=ap.parse_args(); c=json.loads(a.stress_contract.read_text()); jc=json.loads(a.joint_contract.read_text()); v31c=json.loads(a.v31_contract.read_text())
    out=a.out; out.mkdir(parents=True,exist_ok=True); data_dir=a.data_dir
    if c['status']!='FROZEN_BEFORE_2024_2026_HISTORICAL_STRESS_SCORING' or c['fresh_confirmation_claim'] is not False: raise StressError('stress contract')
    if jc['status']!='FROZEN_BEFORE_AUTHORITATIVE_JOINT_SCORE_RESEARCH': raise StressError('joint contract')
    if jc['frozen_v3_1_a']['head']!=c['candidate']['v3_1_parent_head'] or jc['formal_v2']['head']!=c['formal_v2']['head']: raise StressError('contract lineage')
    if c['candidate']['id']!='V3.1.1-A' or float(c['candidate']['residual_scale'])!=0.25: raise StressError('candidate drift')
    data_manifest=load_data_manifest(data_dir,c)
    xg=loadmod('stress_xg',a.xg_engine); v2=loadmod('stress_v2',a.v2_engine); usr=loadmod('stress_usr',a.usr1_engine); v31=loadmod('stress_v31',a.v31_engine); joint=loadmod('stress_joint',a.joint_engine)

    rows,fold_map,fold_rows,rowrec=v31.build_rows_to_2023(xg,v2,a.v1_engine,a.v1_result,a.db,a.xg_identity)
    proc_old,procrec=v31.process_features_ext(usr,a.db,rows,2023)
    train=[r for r in rows if 2018<=r['season']<=2022]; hold=[r for r in rows if r['season']==2023]
    model,fitmeta=usr.fit(train,proc_old)
    frozen_v31=json.loads(a.v31_final.read_text())
    if fitmeta!=frozen_v31['final_fit_meta_2018_2022']: raise StressError('V3.1 fit meta drift')
    pm23={r['fixture_id']:v31.predict_variant(usr,model,r,proc_old,'V3.1-A',{'residual_scale':0.25}) for r in hold}
    e23=v31.evaluate(usr,hold,pm23,v31c,fold_map=None,season_gate=False,require_fold=False)
    if canon(e23)!=canon(frozen_v31['candidate_fixed_2023_holdout']): raise StressError('V3.1 frozen 2023 reproduction drift')
    write_json(out/'frozen_candidate_reproduction.json',{'status':'EXACT_2023_REPRODUCTION','fit_meta':fitmeta,'holdout_sha256':canon_sha(e23)})

    fixtures=read_jsonl(data_dir/'fixtures.jsonl')
    if len(fixtures)!=int(data_manifest['fixture_n']): raise StressError('fixture count')
    if any(int(r['season']) not in (2024,2025) for r in fixtures): raise StressError('unexpected season')
    ordered=sorted(fixtures,key=lambda r:(parse_iso(r['kickoff']),r['fixture_id']))
    if canon(ordered)!=canon(fixtures): raise StressError('fixture chronology drift')
    if canon_sha([r['fixture_id'] for r in fixtures])!=data_manifest['fixture_identity_sha256']: raise StressError('fixture identity SHA')

    v1=xg.import_v1(a.v1_engine); v1params=xg.load_v1_params(a.v1_result)
    old_fixtures,old_labels,old_meta=xg.load_universe(a.db,a.xg_identity)
    xstate=xg.ChallengerState(v1,v1params,v2.fixed_xg_params(xg))
    v2.warm_state_through_old(xg,xstate,old_fixtures,old_labels)
    old_x_pending=xstate._fusion_old_pending; new_x_pending=deque()
    pstates,priors,pqueue,pseq,procstate_meta=old_process_state(usr,a.db)

    upd_f=(data_dir/'state_updates.jsonl').open('r',encoding='utf-8'); lab_f=(data_dir/'label_vault.jsonl').open('r',encoding='utf-8')
    upd_iter=iter(upd_f); lab_iter=iter(lab_f)
    score_rows=[]; cand_map={}; base_map={}; prediction_rows=[]
    max_cand_diff=max_base_diff=max_weak_diff=0.0; fallback_exact=True; process_active=0; process_fallback=0
    target_labels_read=0; target_updates_read=0
    for batch_rows in batches(fixtures):
        now=parse_iso(batch_rows[0]['kickoff'])
        xg.release_ready(xstate,old_x_pending,old_labels,now,update_base=True)
        while new_x_pending and new_x_pending[0][0]<=now:
            _,fb,labs=new_x_pending.popleft(); xstate.apply_released_batch(fb,labs,as_of=now,update_base=True)
        release_process(pqueue,pstates,now)
        fb=[xg.FixtureRow(r['fixture_id'],r['competition_id'],str(r['season']),parse_iso(r['kickoff']),r['home_team_id'],r['away_team_id'],r['home_team_name'],r['away_team_name']) for r in batch_rows]
        xp,bp=xstate.predict_batch(fb,include_matrix=False)
        batch_pred=[]
        for fr,f,x,b in zip(batch_rows,fb,xp,bp):
            rec=v2.prediction_record(xg,b,x,0.75)
            row={'fixture_id':f.fixture_id,'league':fr['league'],'season':int(fr['season']),'kickoff':f.kickoff.isoformat(),
                 'home_team_id':f.home_team_id,'away_team_id':f.away_team_id,
                 'v1_mu_home':float(b['mu_home']),'v1_mu_away':float(b['mu_away']),
                 'xg_mu_home':float(x['mu_home']),'xg_mu_away':float(x['mu_away']),
                 'v1':rec['v1'],'xg':rec['xg'],'fusion':rec['fusion'],'fallback_exact_v1':bool(rec['fallback_exact_v1']),'cold_start_bucket':rec['cold_start_bucket']}
            proc={f.fixture_id:process_profile(usr,pstates,priors,fr)}
            target=v31.predict_variant(usr,model,row,proc,'V3.1-A',{'residual_scale':0.25})
            bm=joint.base_matrix(row); cm=joint.candidate_matrix('V3.1.1-A',{},row,target)
            if not joint.matrix_valid(bm) or not joint.matrix_valid(cm): raise StressError('matrix invalid')
            bp1=joint.integrate(bm); cp=joint.integrate(cm); formal=[float(row['fusion']['p_home']),float(row['fusion']['p_draw']),float(row['fusion']['p_away'])]
            max_base_diff=max(max_base_diff,max(abs(q-z) for q,z in zip(bp1,formal)))
            max_cand_diff=max(max_cand_diff,max(abs(q-z) for q,z in zip(cp,target)))
            if abs(formal[0]-formal[2])>1e-15:
                weak=0 if formal[0]<formal[2] else 2; max_weak_diff=max(max_weak_diff,abs(cp[weak]-formal[weak]))
            if row['fallback_exact_v1'] and joint.canon(cm)!=joint.canon(bm): fallback_exact=False
            process_active+=int(proc[f.fixture_id]['valid'] and not row['fallback_exact_v1']); process_fallback+=int(not(proc[f.fixture_id]['valid'] and not row['fallback_exact_v1']))
            batch_pred.append((fr,f,row,bm,cm,cp))
        batch_labs={}
        for fr,f,row,bm,cm,cp in batch_pred:
            try: u=json.loads(next(upd_iter)); lab=json.loads(next(lab_iter))
            except StopIteration as exc: raise StressError('history vault ended early') from exc
            target_updates_read+=1; target_labels_read+=1
            if str(u['fixture_id'])!=f.fixture_id or str(lab['fixture_id'])!=f.fixture_id: raise StressError('history vault identity/order mismatch')
            rel=parse_iso(u['release_at'])
            if rel!=f.kickoff+timedelta(hours=3): raise StressError('history release mismatch')
            hg=int(lab['home_goals']); ag=int(lab['away_goals'])
            batch_labs[f.fixture_id]=xg.ReleasedLabel(hg,ag,float(u['home_xg']),float(u['away_xg']),rel)
            pu=process_updates_from_row(fr,u)
            if pu: pseq+=1; heapq.heappush(pqueue,(rel,pseq,pu))
            srow=dict(row); srow['home_goals']=hg; srow['away_goals']=ag; score_rows.append(srow)
            base_map[f.fixture_id]=bm; cand_map[f.fixture_id]=cm
            prediction_rows.append({'fixture_id':f.fixture_id,'league':fr['league'],'season':int(fr['season']),'kickoff':f.kickoff.isoformat(),
                                    'formal_v2_1x2':joint.integrate(bm),'v3_1_1_1x2':cp,
                                    'formal_matrix':bm,'v3_1_1_matrix':cm,'formal_matrix_sha256':canon_sha(bm),'v3_1_1_matrix_sha256':canon_sha(cm),
                                    'label_read_after_prediction_freeze':True})
        new_x_pending.append((max(v.release_at for v in batch_labs.values()),fb,batch_labs))
    try:
        extra=next(upd_iter)
        if extra.strip(): raise StressError('extra state update rows')
    except StopIteration: pass
    try:
        extra=next(lab_iter)
        if extra.strip(): raise StressError('extra label rows')
    except StopIteration: pass
    upd_f.close(); lab_f.close()
    n=len(score_rows)
    if n!=len(fixtures) or target_labels_read!=n or target_updates_read!=n: raise StressError('score count mismatch')
    if max_base_diff>TOL or max_cand_diff>TOL or max_weak_diff>TOL or not fallback_exact: raise StressError('matrix/fallback invariant')

    ev=joint.all_eval(v31,usr,score_rows,cand_map,base_map,jc,fold_map=None,season_gate=False,require_fold=False)
    season_detail=[]; seasons_pass=True
    for s in (2024,2025):
        rs=[r for r in score_rows if r['season']==s]
        bm={r['fixture_id']:base_map[r['fixture_id']] for r in rs}; cm={r['fixture_id']:cand_map[r['fixture_id']] for r in rs}
        pmap=joint.pmap_from_mmap(cm)
        b1=usr.metrics(rs,lambda r:usr.pvec(r)); c1=usr.metrics(rs,lambda r:pmap[r['fixture_id']])
        bs=joint.score_diagnostics(rs,bm); cs=joint.score_diagnostics(rs,cm)
        g1=b1['logloss']-c1['logloss']; gs=bs['exact_score_logloss']-cs['exact_score_logloss']
        ok1=g1>=-TOL; oks=gs>=-TOL; seasons_pass &= ok1 and oks
        season_detail.append({'season':s,'n':len(rs),'one_x_two_logloss_gain':g1,'one_x_two_nondegrade':ok1,
                              'exact_score_logloss_gain':gs,'exact_score_nondegrade':oks,
                              'formal_1x2':b1,'candidate_1x2':c1,'formal_score':bs,'candidate_score':cs})
    league_seasons=[]; worst=-1e9
    for key in sorted({(r['league'],r['season']) for r in score_rows}):
        rs=[r for r in score_rows if (r['league'],r['season'])==key]
        pmap={r['fixture_id']:joint.integrate(cand_map[r['fixture_id']]) for r in rs}
        b=usr.metrics(rs,lambda r:usr.pvec(r)); cc=usr.metrics(rs,lambda r:pmap[r['fixture_id']]); deg=cc['logloss']-b['logloss']; worst=max(worst,deg)
        league_seasons.append({'league':key[0],'season':key[1],'n':len(rs),'logloss_degradation':deg,'formal':b,'candidate':cc})
    group_pass=worst<=float(c['stress_gates']['league_season_worst_1x2_logloss_degradation_max'])+TOL

    d1=[]; ds=[]
    for r in score_rows:
        y=usr.result_idx(r); bp=usr.pvec(r); cp=joint.integrate(cand_map[r['fixture_id']])
        d1.append(-math.log(max(bp[y],1e-15)) + math.log(max(cp[y],1e-15)))
        hg,ag=int(r['home_goals']),int(r['away_goals'])
        qb=max(joint.score_prob(base_map[r['fixture_id']],hg,ag),1e-15); qc=max(joint.score_prob(cand_map[r['fixture_id']],hg,ag),1e-15)
        ds.append(-math.log(qb)+math.log(qc))
    effect1=sum(d1)/n; sd1=stdev(d1); req1=joint.power_required_n(effect1,sd1,.05,.8)
    effects=sum(ds)/n; sds=stdev(ds); reqs=joint.power_required_n(effects,sds,.05,.8)
    boot=bootstrap_pair(d1,ds,int(c['report']['paired_bootstrap_reps']),int(c['report']['paired_bootstrap_seed']))

    all_pass=bool(ev['all_pass'] and seasons_pass and group_pass and fallback_exact and max_base_diff<=TOL and max_cand_diff<=TOL and max_weak_diff<=TOL)
    status=c['terminal']['pass'] if all_pass else c['terminal']['fail']
    result={'schema_version':'football3-v3-1-1-historical-stress-result-v1','status':status,'classification':'POST_VIEW_HISTORICAL_STRESS_TEST',
            'fresh_confirmation':False,'promotion_allowed':False,'n':n,'seasons':[2024,2025],
            'pooled':ev,'season_detail':season_detail,'league_season':league_seasons,'worst_league_season_1x2_logloss_degradation':worst,
            'paired':{'one_x_two':{'effect':effect1,'paired_sd':sd1,'required_n':req1},'exact_score':{'effect':effects,'paired_sd':sds,'required_n':reqs},'bootstrap':boot},
            'matrix_audit':{'max_formal_matrix_to_formal_1x2_abs_diff':max_base_diff,'max_candidate_matrix_to_v31_target_abs_diff':max_cand_diff,
                            'max_weak_side_probability_abs_delta':max_weak_diff,'fallback_matrix_exact':fallback_exact,'matrix_shape':[15,15],'cell_count':225},
            'process_coverage':{'candidate_active_n':process_active,'candidate_fallback_n':process_fallback,'candidate_active_rate':process_active/max(1,n),
                                'old_process_state':procstate_meta},
            'pit_audit':{'same_kickoff_predictions_frozen_before_batch_label_read':True,'target_labels_read':target_labels_read,'target_state_updates_read':target_updates_read,
                         'labels_used_for_training_or_selection':False},
            'data_manifest_sha256':sha_file(data_dir/'data_manifest.json'),'fixture_identity_sha256':data_manifest['fixture_identity_sha256'],
            'formal_v2_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(out/'historical_stress_result.json',result)
    write_json(out/'season_detail.json',season_detail); write_json(out/'league_season_detail.json',league_seasons)
    write_json(out/'matrix_audit.json',result['matrix_audit']); write_json(out/'paired_bootstrap.json',boot)
    write_jsonl(out/'predictions_label_free.jsonl',prediction_rows)
    (out/'artifact_slug.txt').write_text(f"{status}__n_{n}__x1gain_{effect1:.6f}__scoregain_{effects:.6f}__sd_{sd1:.6f}\n",encoding='utf-8')
    print(json.dumps({'status':status,'n':n,'one_x_two_gain':effect1,'one_x_two_sd':sd1,'one_x_two_required_n':req1,'exact_score_gain':effects,'exact_score_sd':sds,'exact_score_required_n':reqs,'seasons_pass':seasons_pass,'worst_league_season_degradation':worst},sort_keys=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
