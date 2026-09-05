from __future__ import annotations

import argparse, collections, hashlib, json, pathlib, sqlite3

LEAGUES=("Bundesliga","EPL","La liga","Ligue 1","Serie A")
MODES=("IGNORE_OWN_GOAL_REFERENCE","OWN_GOAL_CREDITS_EVENT_SIDE","OWN_GOAL_CREDITS_OPPOSITE_SIDE")


def sha256(p:pathlib.Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()


def score(events:list[dict],mode:str):
    h=a=0; bad=0
    for e in events:
        side=str(e['h_a']); result=str(e['result'])
        if result not in ('Goal','OwnGoal'): continue
        if side not in ('h','a'):
            bad+=1; continue
        if result=='Goal': credit=side
        elif mode=='IGNORE_OWN_GOAL_REFERENCE': continue
        elif mode=='OWN_GOAL_CREDITS_EVENT_SIDE': credit=side
        elif mode=='OWN_GOAL_CREDITS_OPPOSITE_SIDE': credit='a' if side=='h' else 'h'
        else: raise RuntimeError(mode)
        if credit=='h': h+=1
        else: a+=1
    return h,a,bad


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_ZERO_MODEL_SOURCE_SEMANTICS_AUDIT'
    assert sha256(a.db)==c['source']['database_sha256']
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in LEAGUES)
    games=[dict(r) for r in con.execute(f'''select id,fid,date,league,season,h_goals,a_goals from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',LEAGUES)]
    ev=collections.defaultdict(list); own_n=0; invalid_goal_side=0
    for r in con.execute(f'''select e.id,e.match_id,e.h_a,e.minute,e.result from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and e.result in ('Goal','OwnGoal') order by e.match_id,e.minute,e.id''',LEAGUES):
        d=dict(r); ev[int(d['match_id'])].append(d); own_n+=int(str(d['result'])=='OwnGoal'); invalid_goal_side+=int(str(d['h_a']) not in ('h','a'))
    con.close()
    if len(games)!=int(c['gates']['expected_game_n']): raise RuntimeError(f"game_n drift {len(games)}")
    if own_n!=int(c['gates']['expected_own_goal_event_n']): raise RuntimeError(f"own_goal_n drift {own_n}")

    results={}; mismatch_samples={}
    for mode in MODES:
        exact=0; bad=0; sample=[]
        byls=collections.Counter()
        for g in games:
            h,a2,b=score(ev[int(g['id'])],mode); bad+=b
            ok=(h,a2)==(int(g['h_goals']),int(g['a_goals']))
            exact+=int(ok)
            if not ok:
                byls[(str(g['league']),int(g['season']))]+=1
                if len(sample)<100: sample.append({'id':int(g['id']),'fid':int(g['fid']),'date':str(g['date']),'league':g['league'],'season':int(g['season']),'official':[int(g['h_goals']),int(g['a_goals'])],'reconstructed':[h,a2]})
        results[mode]={'exact_n':exact,'mismatch_n':len(games)-exact,'rate':exact/len(games),'invalid_goal_side_n':bad,'mismatch_by_league_season':[{'league':k[0],'season':k[1],'n':v} for k,v in sorted(byls.items())]}
        mismatch_samples[mode]=sample

    ranked=sorted(MODES,key=lambda m:(results[m]['rate'],results[m]['exact_n'],m),reverse=True)
    winner,runner=ranked[0],ranked[1]
    sel=c['selection_rule']
    checks={
        'parent_reference_reproduced':abs(results['IGNORE_OWN_GOAL_REFERENCE']['rate']-float(c['parent_failed_audit']['exact_score_reconstruction_rate']))<=float(sel['parent_reference_abs_tolerance']),
        'winner_rate':results[winner]['rate']>=float(sel['minimum_winner_rate']),
        'winner_margin':results[winner]['rate']-results[runner]['rate']>=float(sel['minimum_winner_minus_runner_up']),
        'own_goal_event_n_exact':own_n==int(c['gates']['expected_own_goal_event_n']),
        'invalid_goal_side_rate':invalid_goal_side/max(1,sum(len(v) for v in ev.values()))<=float(c['gates']['maximum_invalid_goal_side_rate']),
        'unique_winner':results[winner]['rate']>results[runner]['rate'],
    }
    passed=all(checks.values())
    out={
        'schema_version':'football3-own-goal-semantics-audit-result-v1',
        'status':c['terminal']['pass'] if passed else c['terminal']['fail'],
        'research_only':True,'source_schema_identification_only':True,'model_fit':0,'candidate_probability':0,'target_feature_generation':False,
        'historical_confirmation_2023_opened':False,'prospective_1335_touched':False,
        'game_n':len(games),'own_goal_event_n':own_n,'invalid_goal_side_n':invalid_goal_side,
        'hypothesis_results':results,'ranking':ranked,'winner':winner if passed else None,'runner_up':runner,'winner_minus_runner_up':results[winner]['rate']-results[runner]['rate'],'checks':checks,
        'next_step':'FREEZE_CORRECTED_SCORE_STATE_RESPONSE_SOURCE_CONTRACT_SEPARATELY' if passed else 'CLOSE_SCORE_STATE_RESPONSE_LANE_NO_RESCUE',
        'mismatch_samples':mismatch_samples,
    }
    (a.out/'own_goal_semantics_audit.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ['status','game_n','own_goal_event_n','hypothesis_results','ranking','winner','winner_minus_runner_up','checks','next_step']},sort_keys=True))

if __name__=='__main__': main()
