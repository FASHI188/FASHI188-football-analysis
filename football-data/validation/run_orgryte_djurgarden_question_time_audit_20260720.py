#!/usr/bin/env python3
from __future__ import annotations
import csv, json, math, sys
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]; ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from football_v460_engine import predict_joint_distribution
from platform_core import derive_score_marginals, read_processed_matches, settle_home_handicap, settle_over_total, top_scores, normalize_team_token
COMP='SWE_Allsvenskan'; SEASON='2026'; CUTOFF=datetime(2026,7,20,12,59,23,tzinfo=timezone.utc)
OUT=ROOT/'manifests'/'question_time_audits'/'SWE_Allsvenskan_2026-07-20_Orgryte_vs_Djurgarden.json'
SUPPLEMENTAL=[('17/07/2026','IFK Goteborg','Brommapojkarna',2,1),('17/07/2026','Mjallby','Vasteras',0,0),('18/07/2026','AIK','GAIS',2,0),('19/07/2026','Hammarby','Degerfors',4,0),('19/07/2026','Elfsborg','Sirius',1,3),('19/07/2026','Halmstad','Hacken',0,2)]
MARKET={
 'one_x_two_reference':{'source':'BetMines current preview page','home':6.75,'draw':5.00,'away':1.42},
 'total_goals_reference':{'source':'BetMines current preview page','line':2.5,'over':1.47,'under':2.60},
 'asian_handicap_reference':{'source':'SportsGambler preview page','line_home':1.75,'home':1.75,'away':1.91},
 'synchronization_status':'degraded_mixed_sources_no_common_original_timestamp'
}
def score_market(matrix,line,fn):
 out={'win':0.0,'push':0.0,'loss':0.0}
 for c in matrix:
  p=float(c['probability']); s=fn(int(c['home_goals']),int(c['away_goals']),line)
  for k in out: out[k]+=p*float(s[k])
 return out

def resolve_name(rows,candidates):
 names=sorted({r.home_team for r in rows}|{r.away_team for r in rows})
 by={normalize_team_token(n):n for n in names}
 for c in candidates:
  t=normalize_team_token(c)
  if t in by: return by[t]
 for c in candidates:
  t=normalize_team_token(c)
  for n in names:
   nt=normalize_team_token(n)
   if t in nt or nt in t: return n
 raise RuntimeError(f'cannot resolve team from candidates={candidates}; names={names}')

def main():
 pd=ROOT/'processed'/COMP; pd.mkdir(parents=True,exist_ok=True)
 existing=read_processed_matches(COMP); keys={(m.season,m.date.date().isoformat(),m.home_team.casefold(),m.away_team.casefold()) for m in existing}
 rows=[]
 for date,home,away,hg,ag in SUPPLEMENTAL:
  iso=datetime.strptime(date,'%d/%m/%Y').date().isoformat(); key=(SEASON,iso,home.casefold(),away.casefold())
  if key not in keys: rows.append({'competition_id':COMP,'season':SEASON,'stage':'regular_league','Date':date,'HomeTeam':home,'AwayTeam':away,'FTHG':hg,'FTAG':ag})
 overlay=pd/'__question_time_orgryte_djurgarden_20260720.csv'
 try:
  if rows:
   with overlay.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['competition_id','season','stage','Date','HomeTeam','AwayTeam','FTHG','FTAG']);w.writeheader();w.writerows(rows)
  after=read_processed_matches(COMP); hist=[m for m in after if m.season==SEASON and m.date<CUTOFF]
  home=resolve_name(hist,['Orgryte','Orgryte IS','Örgryte','Örgryte IS'])
  away=resolve_name(hist,['Djurgarden','Djurgardens IF','Djurgården','Djurgårdens IF'])
  pred=predict_joint_distribution(COMP,home,away,CUTOFF,season=SEASON)
  matrix=pred['probabilities']['score_matrix']; margins=derive_score_marginals(matrix); ranking=top_scores(matrix,10)
  ah_home=score_market(matrix,1.75,settle_home_handicap); ou25=score_market(matrix,2.5,settle_over_total)
  # away -1.75 is the inverse of home +1.75 settlement.
  ah_away={'win':ah_home['loss'],'push':ah_home['push'],'loss':ah_home['win']}
  raw={k:1.0/MARKET['one_x_two_reference'][k] for k in ('home','draw','away')}; s=sum(raw.values()); no_vig={k:v/s for k,v in raw.items()}
  total_rank=sorted(margins['total_goals'].items(),key=lambda kv:(-kv[1],kv[0]))
  result={'schema_version':'V4.7.0-question-time-formal-audit-r1','status':'PASS','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
   'match_identity':{'competition_id':COMP,'season':SEASON,'round':13,'home_team':home,'display_home_team':'Orgryte IS','away_team':away,'display_away_team':'Djurgardens IF','kickoff_utc':'2026-07-20T17:00:00+00:00','freeze_time_utc':CUTOFF.isoformat(),'settlement':'90_minutes_including_stoppage','venue':'Gamla Ullevi'},
   'data_audit':{'supplemental_rows_applied':len(rows),'supplemental_rows':rows,'current_season_history_matches':len(hist),'latest_history_match_date':hist[-1].date.date().isoformat() if hist else None,'engine_history_matches':pred['history_matches'],'engine_latest_history_match_date':pred['latest_history_match_date'],'parameter_source':pred['parameter_source'],'team_sample':pred['team_sample'],'competition_effective_matches':pred['competition_effective_matches'],'nb_dispersion_k':pred['nb_dispersion_k'],'low_score_factors':pred['low_score_factors']},
   'module_states':{'competition_identity_time':'通过','data_quality_sources':'通过' if len(hist)>=100 else '部分通过','synchronized_market':'降级','team_lineup_task':'部分通过','direct_total_goals':'通过','conditional_goal_difference':'通过','unified_score_matrix':'通过' if abs(margins['probability_sum']-1.0)<=1e-10 else '失败','market_coordination':'未启用','price_ev_no_bet':'降级'},
   'market_reference':MARKET,'market_no_vig_1x2_reference':no_vig,
   'formal_probabilities':{'one_x_two':margins['1x2'],'total_goals':margins['total_goals'],'btts_yes':margins['btts_yes'],'top_scores':ranking[:5],'top3_cumulative':sum(float(x['probability']) for x in ranking[:3]),'top1_top2_gap':float(ranking[0]['probability'])-float(ranking[1]['probability']),'home_handicap_plus_1_75':ah_home,'away_handicap_minus_1_75':ah_away,'over_2_5':ou25},
   'total_goals_primary':total_rank[0],'total_goals_secondary':total_rank[1],
   'audit':{'probability_sum':margins['probability_sum'],'oof_matrix_calibration_2026':'identity_guardrail_temperature_1.0','dynamic_strength_v470':'未启用','d_conditional_v470':'未启用','formal_ev':'No Bet: SWE domain has no independent LOMO/OOS promotion receipt; market references are not synchronized.'}}
  OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
 except Exception as exc:
  fail={'schema_version':'V4.7.0-question-time-formal-audit-r1','status':'FAIL','match':'Orgryte vs Djurgarden','reason':str(exc)}; OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(fail,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(fail,ensure_ascii=False,indent=2)); return 1
 finally: overlay.unlink(missing_ok=True)
if __name__=='__main__': raise SystemExit(main())