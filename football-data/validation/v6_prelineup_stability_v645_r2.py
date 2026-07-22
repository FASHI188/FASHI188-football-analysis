#!/usr/bin/env python3
"""V6.4.5 r2 transport fix: parallel StatsBomb lineup prefetch, unchanged model logic."""
from __future__ import annotations
import json, sys
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_prelineup_stability_v645 as r1
OUT=ROOT/'manifests'/'v6_prelineup_stability_v645_r2_status.json'

def main():
    matches=sorted(r1.get_json(r1.SB_MATCHES),key=lambda m:(m['match_date'],m['match_id']));fd=r1.get_csv(r1.FD);byfd=defaultdict(list)
    for row in fd:
        try:byfd[r1.parse_date(row['Date'])].append(row)
        except Exception:pass
    payloads={};failures={}
    def fetch(m):return int(m['match_id']),r1.get_json(r1.SB_LINEUP.format(match_id=m['match_id']))
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs={pool.submit(fetch,m):int(m['match_id']) for m in matches}
        for fut in as_completed(futs):
            mid=futs[fut]
            try:k,v=fut.result();payloads[k]=v
            except Exception as e:failures[mid]=f'{type(e).__name__}: {e}'
    xi_hist=defaultdict(lambda:deque(maxlen=5));last_mgr={};tenure=defaultdict(int);rows=[];starter_fail=0
    for m in matches:
        date=m['match_date'];hn=m['home_team']['home_team_name'];an=m['away_team']['away_team_name'];candidates=byfd.get(date,[]);raw=next((x for x in candidates if r1.norm(x.get('HomeTeam'))==r1.norm(hn) and r1.norm(x.get('AwayTeam'))==r1.norm(an)),None)
        if raw is None:
            raw=next((x for x in candidates if (r1.norm(hn)[:6] and (r1.norm(hn)[:6] in r1.norm(x.get('HomeTeam')) or r1.norm(x.get('HomeTeam'))[:6] in r1.norm(hn))) and (r1.norm(an)[:6] and (r1.norm(an)[:6] in r1.norm(x.get('AwayTeam')) or r1.norm(x.get('AwayTeam'))[:6] in r1.norm(an)))),None)
        mk=r1.market(raw) if raw else None;hid=int(m['home_team']['home_team_id']);aid=int(m['away_team']['away_team_id']);hmgr=((m['home_team'].get('managers') or [{}])[0].get('id'));amgr=((m['away_team'].get('managers') or [{}])[0].get('id'));hchg=hid in last_mgr and hmgr is not None and last_mgr[hid]!=hmgr;achg=aid in last_mgr and amgr is not None and last_mgr[aid]!=amgr
        if mk and len(xi_hist[hid])>=2 and len(xi_hist[aid])>=2:
            x=r1.row_features(mk,list(xi_hist[hid]),list(xi_hist[aid]),hchg,achg,tenure[hid],tenure[aid]);truth='home' if m['home_score']>m['away_score'] else 'away' if m['home_score']<m['away_score'] else 'draw';rows.append({'date':date,'match_id':m['match_id'],'x':x,'market':mk,'truth':truth})
        lp=payloads.get(int(m['match_id']))
        if lp is not None:
            hxi=r1.starter_ids(lp,hid);axi=r1.starter_ids(lp,aid)
            if len(hxi)>=9:xi_hist[hid].append(hxi)
            else:starter_fail+=1
            if len(axi)>=9:xi_hist[aid].append(axi)
            else:starter_fail+=1
        else:starter_fail+=2
        if hmgr is not None:tenure[hid]=1 if hchg or hid not in last_mgr else tenure[hid]+1;last_mgr[hid]=hmgr
        if amgr is not None:tenure[aid]=1 if achg or aid not in last_mgr else tenure[aid]+1;last_mgr[aid]=amgr
    rows=sorted(rows,key=lambda x:(x['date'],x['match_id']));n=len(rows);a=int(.6*n);b=int(.8*n);tr,va,ho=rows[:a],rows[a:b],rows[b:];bv=r1.score(va);bh=r1.score(ho);cand=[]
    for l2 in r1.L2_GRID:
        decisive=[x for x in tr if x['truth']!='draw'];model=r1.fit([x['x'] for x in decisive],[1 if x['truth']=='home' else 0 for x in decisive],l2);mv=r1.score(va,model);proper=mv['mean_brier']<=bv['mean_brier']+1e-12 and mv['mean_log_loss']<=bv['mean_log_loss']+1e-12;cand.append({'l2':l2,'proper_nonworse':proper,'validation':mv})
    elig=[c for c in cand if c['proper_nonworse']] or cand;elig.sort(key=lambda c:(-c['validation']['accuracy'],c['validation']['mean_log_loss']));sel=elig[0];dec=[x for x in tr+va if x['truth']!='draw'];refit=r1.fit([x['x'] for x in dec],[1 if x['truth']=='home' else 0 for x in dec],sel['l2']);mh=r1.score(ho,refit);guard={'brier_nonworse':mh['mean_brier']<=bh['mean_brier']+1e-12,'log_loss_nonworse':mh['mean_log_loss']<=bh['mean_log_loss']+1e-12}
    out={'schema_version':'V6.4.5-prelineup-stability-pilot-r2-parallel','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','scope':{'competition':'Bundesliga','season':'2023/24','current_match_actual_xi_used_as_feature':False,'features_use_only_prior_lineups':True},'data_audit':{'statsbomb_matches':len(matches),'lineups_downloaded':len(payloads),'lineup_download_failures':len(failures),'starter_parse_failures':starter_fail,'usable_rows':n,'train':len(tr),'validation':len(va),'holdout':len(ho)},'baseline_validation':bv,'selected_candidate':sel,'baseline_holdout':bh,'challenger_holdout':mh,'accuracy_gain_pp':100*(mh['accuracy']-bh['accuracy']) if bh['accuracy'] is not None else None,'proper_score_guard':guard,'pilot_gate_passed':bool(mh['accuracy']>bh['accuracy'] and all(guard.values())),'governance':{'r1_model_logic_unchanged':True,'transport_parallelized_only':True,'pilot_only':True,'single_season_not_promotion_evidence':True,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
