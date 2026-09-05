from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import pathlib
import sqlite3

LEAGUES=("Bundesliga","EPL","La liga","Ligue 1","Serie A")
STATES=("tied","leading","trailing")


def sha256(p:pathlib.Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()


def qtile(xs:list[float],q:float):
    if not xs: return None
    ys=sorted(float(x) for x in xs)
    pos=(len(ys)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi: return ys[lo]
    w=pos-lo
    return ys[lo]*(1-w)+ys[hi]*w


def perspective_state(hg:int,ag:int,side:str)->str:
    if hg==ag: return 'tied'
    if side=='h': return 'leading' if hg>ag else 'trailing'
    return 'leading' if ag>hg else 'trailing'


def summarize_match(events:list[dict],official_h:int,official_a:int):
    hg=ag=0; prev=0
    last=max([90]+[min(120,max(0,int(e['minute']))) for e in events])
    out={s:{z:0.0 for z in ('minutes','xg_for','xg_against')} for s in STATES}
    invalid=0
    for e in events:
        try:
            minute=int(e['minute'])
            side=str(e['h_a'])
            xg=float(e['xG'])
        except Exception:
            invalid+=1; continue
        if side not in ('h','a') or minute<0 or minute>120 or not math.isfinite(xg) or xg<0:
            invalid+=1; continue
        m=min(120,max(0,minute))
        if m<prev:
            invalid+=1; continue
        dt=m-prev
        hs=perspective_state(hg,ag,'h'); aas=perspective_state(hg,ag,'a')
        out[hs]['minutes']+=dt; out[aas]['minutes']+=dt
        if str(e['situation'])!='Penalty':
            ss=perspective_state(hg,ag,side)
            oside='a' if side=='h' else 'h'
            os=perspective_state(hg,ag,oside)
            out[ss]['xg_for']+=xg; out[os]['xg_against']+=xg
        if str(e['result'])=='Goal':
            if side=='h': hg+=1
            else: ag+=1
        prev=m
    dt=max(0,last-prev)
    out[perspective_state(hg,ag,'h')]['minutes']+=dt
    out[perspective_state(hg,ag,'a')]['minutes']+=dt
    return {
        'reconstructed_h':hg,'reconstructed_a':ag,
        'score_exact':hg==official_h and ag==official_a,
        'invalid_event_n':invalid,'profile':out,'end_minute':last,
    }


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_ZERO_MODEL_SOURCE_AUDIT'
    assert sha256(a.db)==c['source']['database_sha256']
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in LEAGUES)
    games=[dict(r) for r in con.execute(f'''select id,fid,date,league,season,h_id,a_id,h_goals,a_goals from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',LEAGUES)]
    ev_by=collections.defaultdict(list); result_cat=collections.Counter(); total_events=0
    for r in con.execute(f'''select e.id,e.match_id,e.h_a,e.minute,e.xG,e.situation,e.result from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 order by e.match_id,e.minute,e.id''',LEAGUES):
        d=dict(r); ev_by[int(d['match_id'])].append(d); result_cat[str(d['result'])]+=1; total_events+=1
    con.close()
    if len(games)!=int(c['gates']['expected_history_game_n']): raise RuntimeError(f"history n drift {len(games)}")
    targets=[g for g in games if int(g['season']) in (2020,2021,2022)]
    if len(targets)!=int(c['gates']['expected_target_2020_2022_n']): raise RuntimeError(f"target n drift {len(targets)}")

    summaries={}; mismatches=[]; invalid_events=0; exact=0
    mismatch_ls=collections.Counter()
    for g in games:
        s=summarize_match(ev_by[int(g['id'])],int(g['h_goals']),int(g['a_goals']))
        summaries[int(g['id'])]=s; invalid_events+=s['invalid_event_n']
        if s['score_exact']: exact+=1
        else:
            mismatch_ls[(str(g['league']),int(g['season']))]+=1
            if len(mismatches)<200: mismatches.append({'id':int(g['id']),'fid':int(g['fid']),'date':str(g['date']),'league':g['league'],'season':int(g['season']),'official':[int(g['h_goals']),int(g['a_goals'])],'reconstructed':[s['reconstructed_h'],s['reconstructed_a']]})

    states=collections.defaultdict(lambda:collections.deque(maxlen=int(c['mechanics']['lookback_completed_matches'])))
    bydate=collections.OrderedDict()
    for g in games: bydate.setdefault(str(g['date']),[]).append(g)
    target_n=covered=0; snapshot_exposure={s:[] for s in STATES}; all_state_xg=0; profile_sides=0
    min_prior=int(c['mechanics']['minimum_clean_prior_matches'])
    for _,batch in bydate.items():
        batch=sorted(batch,key=lambda z:int(z['id']))
        for g in batch:
            if int(g['season']) not in (2020,2021,2022): continue
            target_n+=1
            hq=states.get((str(g['league']),int(g['h_id']))); aq=states.get((str(g['league']),int(g['a_id'])))
            if hq is not None and aq is not None and len(hq)>=min_prior and len(aq)>=min_prior:
                covered+=1
                for q in (hq,aq):
                    profile_sides+=1; every=True
                    for st in STATES:
                        mins=sum(float(x[st]['minutes']) for x in q); snapshot_exposure[st].append(mins)
                        xg=sum(float(x[st]['xg_for'])+float(x[st]['xg_against']) for x in q)
                        every=every and xg>0
                    all_state_xg+=int(every)
        # exact-date isolation: update only after all snapshots above are frozen
        for g in batch:
            s=summaries[int(g['id'])]
            if not s['score_exact'] or s['invalid_event_n']:
                continue
            hp={st:{'minutes':s['profile'][st]['minutes']/2.0,'xg_for':s['profile'][st]['xg_for'],'xg_against':s['profile'][st]['xg_against']} for st in STATES}
            ap={st:{'minutes':s['profile'][st]['minutes']/2.0,'xg_for':s['profile'][st]['xg_against'],'xg_against':s['profile'][st]['xg_for']} for st in STATES}
            states[(str(g['league']),int(g['h_id']))].append(hp)
            states[(str(g['league']),int(g['a_id']))].append(ap)

    recon=exact/len(games); coverage=covered/target_n; invalid_rate=invalid_events/max(1,total_events)
    gates={
        'history_n_exact':len(games)==int(c['gates']['expected_history_game_n']),
        'target_n_exact':target_n==int(c['gates']['expected_target_2020_2022_n']),
        'exact_final_score_reconstruction_rate':recon>=float(c['gates']['minimum_exact_final_score_reconstruction_rate']),
        'target_profile_coverage':coverage>=float(c['gates']['minimum_target_profile_coverage']),
        'invalid_event_rate':invalid_rate<=float(c['gates']['maximum_unordered_or_invalid_event_rate']),
    }
    passed=all(gates.values())
    byls=[{'league':k[0],'season':k[1],'mismatch_n':v} for k,v in sorted(mismatch_ls.items())]
    exposure={st:{'p10':qtile(v,.10),'p25':qtile(v,.25),'p50':qtile(v,.50),'p75':qtile(v,.75),'p90':qtile(v,.90)} for st,v in snapshot_exposure.items()}
    out={
        'schema_version':'football3-prior-score-state-response-source-audit-v1',
        'status':c['terminal']['pass'] if passed else c['terminal']['fail'],
        'research_only':True,'model_fit':0,'candidate_probability':0,'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,
        'history_game_n':len(games),'target_n':target_n,'total_event_n':total_events,'event_result_categories':dict(sorted(result_cat.items())),
        'exact_final_score_n':exact,'exact_final_score_reconstruction_rate':recon,'mismatch_n':len(games)-exact,'mismatch_by_league_season':byls,
        'invalid_event_n':invalid_events,'invalid_event_rate':invalid_rate,'target_profile_covered_n':covered,'target_profile_coverage':coverage,
        'profile_side_n':profile_sides,'all_three_states_nonpenalty_xg_side_n':all_state_xg,'all_three_states_nonpenalty_xg_side_fraction':all_state_xg/max(1,profile_sides),
        'state_exposure_minutes_quantiles_over_lookback':exposure,'gates':gates,
        'next_step':'FREEZE_SEPARATE_LOW_DIMENSIONAL_RESPONSE_CANDIDATE_CONTRACT' if passed else 'CLOSE_SCORE_STATE_RESPONSE_LANE_NO_RESCUE',
        'mismatch_sample':mismatches,
    }
    (a.out/'prior_score_state_response_source_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ['status','history_game_n','target_n','event_result_categories','exact_final_score_reconstruction_rate','mismatch_n','invalid_event_rate','target_profile_coverage','all_three_states_nonpenalty_xg_side_fraction','state_exposure_minutes_quantiles_over_lookback','gates','next_step']},sort_keys=True))

if __name__=='__main__': main()
