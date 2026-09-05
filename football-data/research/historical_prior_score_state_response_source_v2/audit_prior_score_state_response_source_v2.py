from __future__ import annotations

import argparse, collections, hashlib, json, math, pathlib, sqlite3

LEAGUES=("Bundesliga","EPL","La liga","Ligue 1","Serie A")
STATES=("tied","leading","trailing")


def sha256(p:pathlib.Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()


def qtile(xs,q):
    if not xs: return None
    ys=sorted(map(float,xs)); pos=(len(ys)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    return ys[lo] if lo==hi else ys[lo]*(hi-pos)+ys[hi]*(pos-lo)


def state(hg:int,ag:int,side:str)->str:
    if hg==ag: return 'tied'
    if side=='h': return 'leading' if hg>ag else 'trailing'
    return 'leading' if ag>hg else 'trailing'


def blank():
    return {s:{'minutes':0.0,'xg_for':0.0,'xg_against':0.0,'shot_for':0,'shot_against':0} for s in STATES}


def summarize(events:list[dict],official_h:int,official_a:int):
    hp,ap=blank(),blank(); hg=ag=0; prev=0; invalid=0
    valid_minutes=[]
    for e in events:
        try:
            minute=int(e['minute']); side=str(e['h_a']); xg=float(e['xG']); result=str(e['result']); situation=str(e['situation'])
        except Exception:
            invalid+=1; continue
        if side not in ('h','a') or minute<0 or minute>120 or not math.isfinite(xg) or xg<0:
            invalid+=1; continue
        valid_minutes.append(minute)
        if minute<prev:
            invalid+=1; continue
        dtm=minute-prev
        hp[state(hg,ag,'h')]['minutes']+=dtm
        ap[state(hg,ag,'a')]['minutes']+=dtm
        if situation!='Penalty' and result!='OwnGoal':
            if side=='h':
                hs=state(hg,ag,'h'); aas=state(hg,ag,'a')
                hp[hs]['xg_for']+=xg; hp[hs]['shot_for']+=1
                ap[aas]['xg_against']+=xg; ap[aas]['shot_against']+=1
            else:
                aas=state(hg,ag,'a'); hs=state(hg,ag,'h')
                ap[aas]['xg_for']+=xg; ap[aas]['shot_for']+=1
                hp[hs]['xg_against']+=xg; hp[hs]['shot_against']+=1
        if result=='Goal':
            if side=='h': hg+=1
            else: ag+=1
        elif result=='OwnGoal':
            if side=='h': ag+=1
            else: hg+=1
        prev=minute
    end=min(120,max([90]+valid_minutes)); dtm=max(0,end-prev)
    hp[state(hg,ag,'h')]['minutes']+=dtm; ap[state(hg,ag,'a')]['minutes']+=dtm
    return {'home':hp,'away':ap,'reconstructed':[hg,ag],'score_exact':(hg,ag)==(official_h,official_a),'invalid_event_n':invalid,'end_minute':end}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_ZERO_MODEL_CORRECTED_SOURCE_AUDIT'
    assert sha256(a.db)==c['source']['database_sha256']
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in LEAGUES)
    games=[dict(r) for r in con.execute(f'''select id,fid,date,league,season,h_id,a_id,h_goals,a_goals from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',LEAGUES)]
    ev=collections.defaultdict(list); total_events=own_n=0
    for r in con.execute(f'''select e.id,e.match_id,e.h_a,e.minute,e.xG,e.situation,e.result from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 order by e.match_id,e.minute,e.id''',LEAGUES):
        d=dict(r); ev[int(d['match_id'])].append(d); total_events+=1; own_n+=int(str(d['result'])=='OwnGoal')
    con.close()
    if len(games)!=int(c['gates']['expected_history_game_n']): raise RuntimeError(f"history n drift {len(games)}")
    if own_n!=int(c['gates']['expected_own_goal_event_n']): raise RuntimeError(f"own-goal n drift {own_n}")

    summaries={}; exact=invalid_events=0; mismatch=[]; byls=collections.Counter()
    for g in games:
        s=summarize(ev[int(g['id'])],int(g['h_goals']),int(g['a_goals'])); summaries[int(g['id'])]=s; invalid_events+=s['invalid_event_n']
        if s['score_exact']: exact+=1
        else:
            byls[(str(g['league']),int(g['season']))]+=1
            if len(mismatch)<100: mismatch.append({'id':int(g['id']),'fid':int(g['fid']),'date':str(g['date']),'league':g['league'],'season':int(g['season']),'official':[int(g['h_goals']),int(g['a_goals'])],'reconstructed':s['reconstructed']})

    lookback=int(c['mechanics']['lookback_completed_matches']); min_prior=int(c['mechanics']['minimum_clean_prior_matches'])
    hist=collections.defaultdict(lambda:collections.deque(maxlen=lookback)); bydate=collections.OrderedDict()
    for g in games: bydate.setdefault(str(g['date']),[]).append(g)
    target_n=covered=profile_side_n=all_state_xg_sides=0; exposure={s:[] for s in STATES}
    for _,batch in bydate.items():
        batch=sorted(batch,key=lambda z:int(z['id']))
        for g in batch:
            if int(g['season']) not in (2020,2021,2022): continue
            target_n+=1; hq=hist.get((str(g['league']),int(g['h_id']))); aq=hist.get((str(g['league']),int(g['a_id'])))
            if hq is not None and aq is not None and len(hq)>=min_prior and len(aq)>=min_prior:
                covered+=1
                for q in (hq,aq):
                    profile_side_n+=1; allx=True
                    for st in STATES:
                        mins=sum(float(x[st]['minutes']) for x in q); exposure[st].append(mins)
                        sx=sum(float(x[st]['xg_for'])+float(x[st]['xg_against']) for x in q)
                        allx=allx and sx>0
                    all_state_xg_sides+=int(allx)
        for g in batch:
            s=summaries[int(g['id'])]
            if not s['score_exact'] or s['invalid_event_n']: continue
            hist[(str(g['league']),int(g['h_id']))].append(s['home'])
            hist[(str(g['league']),int(g['a_id']))].append(s['away'])

    recon=exact/len(games); coverage=covered/target_n; invalid_rate=invalid_events/max(1,total_events)
    gates={
        'history_n_exact':len(games)==int(c['gates']['expected_history_game_n']),
        'target_n_exact':target_n==int(c['gates']['expected_target_2020_2022_n']),
        'own_goal_event_n_exact':own_n==int(c['gates']['expected_own_goal_event_n']),
        'exact_final_score_reconstruction_rate':recon>=float(c['gates']['minimum_exact_final_score_reconstruction_rate']),
        'target_profile_coverage':coverage>=float(c['gates']['minimum_target_profile_coverage']),
        'invalid_event_rate':invalid_rate<=float(c['gates']['maximum_invalid_event_rate']),
    }
    passed=all(gates.values())
    out={
        'schema_version':'football3-prior-score-state-response-corrected-source-v2-result-v1',
        'status':c['terminal']['pass'] if passed else c['terminal']['fail'],'research_only':True,'model_fit':0,'candidate_probability':0,
        'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,
        'history_game_n':len(games),'target_n':target_n,'total_event_n':total_events,'own_goal_event_n':own_n,'exact_final_score_n':exact,'mismatch_n':len(games)-exact,'exact_final_score_reconstruction_rate':recon,
        'invalid_event_n':invalid_events,'invalid_event_rate':invalid_rate,'target_profile_covered_n':covered,'target_profile_coverage':coverage,
        'profile_side_n':profile_side_n,'all_three_states_nonpenalty_xg_side_n':all_state_xg_sides,'all_three_states_nonpenalty_xg_side_fraction':all_state_xg_sides/max(1,profile_side_n),
        'state_exposure_minutes_quantiles_over_lookback':{st:{'p10':qtile(v,.1),'p25':qtile(v,.25),'p50':qtile(v,.5),'p75':qtile(v,.75),'p90':qtile(v,.9)} for st,v in exposure.items()},
        'mismatch_by_league_season':[{'league':k[0],'season':k[1],'n':v} for k,v in sorted(byls.items())],'mismatch_sample':mismatch,'gates':gates,
        'next_step':'FREEZE_SEPARATE_LOW_DIMENSIONAL_SCORE_STATE_RESPONSE_CANDIDATE' if passed else 'CLOSE_SCORE_STATE_RESPONSE_LANE_NO_RESCUE'
    }
    (a.out/'prior_score_state_response_corrected_source_v2.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ['status','history_game_n','target_n','own_goal_event_n','exact_final_score_reconstruction_rate','mismatch_n','invalid_event_rate','target_profile_coverage','all_three_states_nonpenalty_xg_side_fraction','state_exposure_minutes_quantiles_over_lookback','gates','next_step']},sort_keys=True))

if __name__=='__main__': main()
