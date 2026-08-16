#!/usr/bin/env python3
"""Zero-label engineering acceptance for Live Prematch Runtime R1.2."""
from __future__ import annotations
import copy, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from live_prematch_runtime_r12 import run_live_prematch
from platform_core import PlatformError
FIXTURE=ROOT/'research'/'live_prematch_runtime_r1_arsenal_mancity_20260816.json'
OUT=ROOT/'research'/'artifacts'/'live_prematch_runtime_r1'/'validation_status.json'
TOL=2e-10

def mass(x): return sum(float(c['probability']) for c in x['probabilities']['score_matrix'])
def mmap(x): return {(int(c['home_goals']),int(c['away_goals'])):float(c['probability']) for c in x['probabilities']['score_matrix']}

def main():
    base=json.loads(FIXTURE.read_text(encoding='utf-8')); checks={}; diag={}
    c=run_live_prematch(base)
    checks['community_output']=c['status']=='PASS'
    checks['community_identity_separate']=c['event_identity']['event_competition_id']=='ENG_CommunityShield' and c['event_identity']['strength_reference_competition_id']=='ENG_PremierLeague'
    checks['community_crossseason']=c['route']['selected']=='CROSS_SEASON_COLD_START_BRIDGE' and c['route']['same_season_history_matches']==0
    checks['community_mass']=abs(mass(c)-1)<=TOL
    checks['community_formal_weight_zero']=c['formal_weight']==0
    diag['community']={'route':c['route'],'one_x_two':c['probabilities']['one_x_two'],'total_goals':c['probabilities']['total_goals'],'top_scores':c['conclusions']['top_scores']}

    s=copy.deepcopy(base); s['home_team'],s['away_team']=base['away_team'],base['home_team']; s['strength_home_team'],s['strength_away_team']=base['strength_away_team'],base['strength_home_team']
    cs=run_live_prematch(s); p,q=c['probabilities']['one_x_two'],cs['probabilities']['one_x_two']
    one=max(abs(p['home']-q['away']),abs(p['draw']-q['draw']),abs(p['away']-q['home']))
    m1,m2=mmap(c),mmap(cs); mr=max(abs(v-m2.get((a,h),0)) for (h,a),v in m1.items())
    checks['neutral_swap_1x2']=one<=TOL; checks['neutral_swap_matrix']=mr<=TOL; diag['neutral']={'one_residual':one,'matrix_residual':mr}

    promoted_cases=[('Arsenal','Arsenal','Coventry City','Coventry City','2026-08-21T19:00:00Z'),('Hull City','Hull City','Manchester United','Man United','2026-08-22T11:30:00Z')]
    promo=[]
    for h,sh,a,sa,kickoff in promoted_cases:
        x=run_live_prematch({'event_competition_id':'ENG_PremierLeague','strength_reference_competition_id':'ENG_PremierLeague','season':'2026/27','home_team':h,'away_team':a,'strength_home_team':sh,'strength_away_team':sa,'kickoff_utc':kickoff,'freeze_time_utc':'2026-08-16T13:34:00Z','neutral_venue':False,'evidence':[]})
        promo.append(x); checks[f'promotion_{h}_{a}']=x['route']['selected']=='PROMOTION_OFFLINE_COLD_START_FALLBACK' and x['route']['external_runtime_network_required'] is False and abs(mass(x)-1)<=TOL
    diag['promotion']=[{'match':f"{x['event_identity']['home_team']} v {x['event_identity']['away_team']}",'route':x['route'],'one_x_two':x['probabilities']['one_x_two'],'top_scores':x['conclusions']['top_scores'],'audit':x['audit']['promotion_coldstart']} for x in promo]

    mid=run_live_prematch({'event_competition_id':'ENG_PremierLeague','strength_reference_competition_id':'ENG_PremierLeague','season':'2025/26','home_team':'Arsenal','away_team':'Manchester City','strength_home_team':'Arsenal','strength_away_team':'Man City','kickoff_utc':'2026-03-02T20:00:00Z','freeze_time_utc':'2026-03-01T20:00:00Z','neutral_venue':False,'evidence':[]})
    checks['normal_inseason']=mid['route']['selected']=='SAME_SEASON_NORMAL_SHADOW' and mid['route']['same_season_history_matches']>=30

    bad=copy.deepcopy(base); bad['evidence']=list(base['evidence'])+[{'kind':'forbidden','source_name':'test','source_url':'https://example.invalid','observed_at_utc':'2026-08-16T13:55:01Z'}]
    rejected=False
    try: run_live_prematch(bad)
    except PlatformError as exc: rejected='post-freeze evidence rejected' in str(exc)
    checks['post_freeze_rejected']=rejected
    checks['formal_unchanged']=all(x['formal_weight']==0 and x['audit']['formal_current_mutated'] is False for x in [c,cs,mid,*promo])

    ok=all(checks.values()); report={'schema_version':'live-prematch-runtime-r1.2-validation','status':'PASS' if ok else 'FAIL','classification':'ENGINEERING_ACCEPTANCE_ZERO_TARGET_LABEL','checks':checks,'diagnostics':diag,'target_match_result_read':False,'formal_current_changed':False,'formal_weight_changed':False}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0 if ok else 2
if __name__=='__main__': raise SystemExit(main())
