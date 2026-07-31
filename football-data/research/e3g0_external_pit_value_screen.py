#!/usr/bin/env python3
"""E3g-0: screen one external PIT source before any H/D/A model fit."""
from __future__ import annotations
import argparse,hashlib,json,re,subprocess,sys,unicodedata,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent;FD=HERE.parent;ROOT=FD.parent
for p in (FD/'engine',FD/'validation',HERE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import e3f0_pit_feature_coverage_entry as e3f0_entry
OUT=ROOT/'artifacts/research/e3g0_external_pit_value_screen';EXPECTED=6251
RAW='https://raw.githubusercontent.com/hudl/open-data/master'
URLS={'competitions':f'{RAW}/data/competitions.json','matches':f'{RAW}/data/matches/9/281.json','readme':f'{RAW}/README.md','license':f'{RAW}/LICENSE.pdf'}
ALIASES={'bayerleverkusen':'leverkusen','leverkusen':'leverkusen','vfbstuttgart':'stuttgart','stuttgart':'stuttgart','borussiadortmund':'dortmund','dortmund':'dortmund','fckoln':'koln','koln':'koln','cologne':'koln','vflbochum':'bochum','bochum':'bochum','tsghoffenheim':'hoffenheim','hoffenheim':'hoffenheim','eintrachtfrankfurt':'frankfurt','einfrankfurt':'frankfurt','frankfurt':'frankfurt','rbleipzig':'leipzig','leipzig':'leipzig','bayernmunich':'bayernmunich','bayernmunchen':'bayernmunich','svdarmstadt98':'darmstadt','darmstadt':'darmstadt','fcaugsburg':'augsburg','augsburg':'augsburg','scfreiburg':'freiburg','freiburg':'freiburg','fcheidenheim':'heidenheim','heidenheim':'heidenheim','fsvmainz05':'mainz','mainz05':'mainz','mainz':'mainz','borussiamonchengladbach':'monchengladbach','mgladbach':'monchengladbach','monchengladbach':'monchengladbach','vflwolfsburg':'wolfsburg','wolfsburg':'wolfsburg','unionberlin':'unionberlin','werderbremen':'werderbremen'}
def head():
    try:return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    except Exception:return None
def digest(b):return hashlib.sha256(b).hexdigest()
def fetch(url):
    q=urllib.request.Request(url,headers={'User-Agent':'FASHI188-E3g0/1.0'})
    with urllib.request.urlopen(q,timeout=90) as r:return r.read(),{k.lower():v for k,v in r.headers.items()}
def dt(v):
    if not v:return None
    try:x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except ValueError:return None
    return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
def canon(v):
    s=unicodedata.normalize('NFKD',v or '');s=''.join(c for c in s if not unicodedata.combining(c));s=re.sub('[^a-z0-9]','',s.lower().replace('ß','ss'));return ALIASES.get(s,s)
def rdate(r):
    v=r['date'];return v.date().isoformat() if hasattr(v,'date') else str(v)[:10]
def fixed_id(r):return rdate(r),canon(str(r['home_team'])),canon(str(r['away_team']))
def source_id(m):return str(m['match_date']),canon(m['home_team']['home_team_name']),canon(m['away_team']['away_team_name'])
def kickoff(m):return datetime.fromisoformat(f"{m['match_date']}T{str(m.get('kick_off') or '00:00:00').split('.')[0]}").replace(tzinfo=timezone.utc)
def md(r):
    c=r['coverage'];p=r['pit_audit']
    return '\n'.join(['# E3g-0 External PIT Increment Screen','',f"- Status: `{r['status']}`",f"- Source: {r['source']['provider']}",f"- Fixed sample: {r['fixed_sample_count']}",f"- Fixed Bundesliga 2023/24: {c['fixed_league_season_count']}",f"- Source/matched: {c['source_match_count']}/{c['matched_intersection_count']}",f"- Intersection coverage: {c['intersection_rate_of_fixed_league_season']:.4%}",f"- All source matches involve Leverkusen: {c['all_source_matches_involve_focal_club']}",'','## PIT gate','',f"- match_available: `{p['competition_match_available']}`",f"- latest target kickoff: `{p['latest_target_kickoff']}`",f"- available before target: {p['available_before_target_count']}",f"- per-match first available_at: {p['per_match_first_available_at_present']}",f"- identity/timestamp/coverage: {p['identity_gate_pass']}/{p['timestamp_gate_pass']}/{p['coverage_gate_pass']}",'','- Candidate model fits: 0','- Threshold tuning: 0','- formal_weight: 0','- Score/totals/BTTS/handicap: NOT_APPLICABLE','',p['reason'],''])
def main():
    a=argparse.ArgumentParser();a.add_argument('--output-dir',default=str(OUT));a.add_argument('--print-summary',action='store_true');z=a.parse_args();out=Path(z.output_dir);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat()
    try:
        blobs={};headers={}
        for k,u in URLS.items():blobs[k],headers[k]=fetch(u)
        comps=json.loads(blobs['competitions']);matches=json.loads(blobs['matches'])
        comp=next(x for x in comps if int(x['competition_id'])==9 and int(x['season_id'])==281)
        if comp['season_name']!='2023/2024' or not matches:raise RuntimeError('source identity changed')
        probe=int(sorted(matches,key=lambda x:(x['match_date'],x['match_id']))[0]['match_id']);event_url=f'{RAW}/data/events/{probe}.json';event_blob,event_headers=fetch(event_url);events=json.loads(event_blob);shots=[x for x in events if x.get('type',{}).get('name')=='Shot'];xg=[x for x in shots if x.get('shot',{}).get('statsbomb_xg') is not None]
        rows,lineage=e3f0_entry.audit.reconstruct_fixed_sample()
        if len(rows)!=EXPECTED:raise RuntimeError(f'fixed sample={len(rows)}')
        season=[r for r in rows if r['competition_id']=='GER_Bundesliga' and '2023' in str(r.get('season',''))]
        if not season:season=[r for r in rows if r['competition_id']=='GER_Bundesliga' and '2023-08-01'<=rdate(r)<='2024-06-30']
        fi={fixed_id(r):r for r in season};si={source_id(m):m for m in matches};matched=set(fi)&set(si);unmatched=set(si)-set(fi)
        focal='leverkusen';all_focal=all(canon(m['home_team']['home_team_name'])==focal or canon(m['away_team']['away_team_name'])==focal for m in matches);clubs={canon(m['home_team']['home_team_name']) for m in matches}|{canon(m['away_team']['away_team_name']) for m in matches}
        av=dt(comp.get('match_available'));kos=[kickoff(m) for m in matches];available=sum(av is not None and av<=k for k in kos);per_first=all('available_at' in m for m in matches);identity=len(matched)==len(matches) and not unmatched;rate=len(matched)/max(1,len(season));coverage=len(matched)>=100 and rate>=.5 and not all_focal;timestamp=av is not None and available==len(matches) and per_first
        status='PIT_IDENTITY_OR_TIMESTAMP_FAILED' if not identity or not timestamp else 'PIT_COVERAGE_INSUFFICIENT' if not coverage else 'RESEARCH_EXECUTION_FAILED'
        manifest={'provider':'StatsBomb Open Data','repository':'hudl/open-data','fetched_at':now,'urls':{**URLS,'event_probe':event_url},'sha256':{**{k:digest(v) for k,v in blobs.items()},'event_probe':digest(event_blob)},'response_headers':{**headers,'event_probe':event_headers},'license_note':'README permits public research use and requests StatsBomb attribution/logo when publishing or sharing.','mutable_master_revision_risk':True,'raw_payloads_committed':False}
        mj=json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)
        report={'schema_version':'1.0','research_id':'E3g-0','status':status,'secondary_statuses':['PIT_COVERAGE_INSUFFICIENT'] if not coverage else [],'repository_head':head(),'scope':'pure_90_minute_HDA_external_PIT_screen','formal_weight':0,'fixed_sample_count':len(rows),'b100_frozen':True,'baseline_predictions_frozen':True,'external_source_count':1,'candidate_model_fit_count':0,'threshold_tuning_count':0,'class_weight':None,'posthoc_threshold':None,'external_records_used_for_training':0,'source':{'provider':'StatsBomb Open Data','repository':'hudl/open-data','competition_id':9,'season_id':281,'competition':comp['competition_name'],'season':comp['season_name'],'selection_reason':'first-priority real event/xG source; free official JSON; Bundesliga season overlaps frozen sample','pit_status':'PIT_UNVERIFIED_RESEARCH_ONLY','event_probe':{'match_id':probe,'event_count':len(events),'shot_count':len(shots),'shots_with_statsbomb_xg':len(xg)}},'coverage':{'fixed_league_season_count':len(season),'source_match_count':len(matches),'matched_intersection_count':len(matched),'intersection_rate_of_fixed_league_season':rate,'source_unique_club_count':len(clubs),'all_source_matches_involve_focal_club':all_focal,'focal_club':focal,'actual_draws_in_source':sum(int(m['home_score'])==int(m['away_score']) for m in matches),'unmatched_source_count':len(unmatched),'unmatched_fixed_count':len(set(fi)-set(si)),'missingness_assessment':'SEVERE_NON_RANDOM_STRONG_TEAM_SELECTION' if all_focal else 'REQUIRES_FURTHER_AUDIT'},'pit_audit':{'observed_at':now,'competition_match_available':comp.get('match_available'),'competition_match_updated':comp.get('match_updated'),'earliest_target_kickoff':min(kos).isoformat(),'latest_target_kickoff':max(kos).isoformat(),'available_before_target_count':available,'per_match_first_available_at_present':per_first,'historical_revision_risk':'HIGH_MUTABLE_REPOSITORY_AND_LAST_UPDATED_FIELDS','identity_gate_pass':identity,'timestamp_gate_pass':timestamp,'coverage_gate_pass':coverage,'same_match_event_use_forbidden':True,'prior_match_event_use_authorized':False,'reason':'StatsBomb open season availability is after all target matches; per-match first available_at is absent; the subset contains only 34 Bayer Leverkusen matches.'},'model_stage':{'market_offset_model_built':False,'logistic_residual_built':False,'tree_or_gam_built':False,'market_increment_tested':False,'hda_metrics_produced':False,'stop_before_training':True},'decision':{'external_pit_increment_signal_found':False,'worth_expanding_to_full_6251':False,'automatic_e3g1':False,'next_action':'STOP_FOR_USER_AND_CODEX_REVIEW'},'asset_protection':{'formal_model_changed':0,'formal_joint_score_matrix_changed':0,'formal_score_module_changed':0,'formal_total_goals_module_changed':0,'formal_btts_module_changed':0,'formal_data_changed':0,'formal_config_changed':0,'current_changed':0,'formal_weight_changed':0,'actions_permissions_changed':0,'auto_commit_push_enabled':False},'lineage':lineage,'source_manifest_sha256':digest(mj.encode())}
        (out/'e3g0_source_snapshot_manifest.json').write_text(mj,encoding='utf-8');(out/'e3g0_external_pit_value_screen.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');(out/'e3g0_external_pit_value_screen.md').write_text(md(report),encoding='utf-8')
        if z.print_summary:print(json.dumps({'status':status,'head':report['repository_head'],'fixed':len(rows),'league_season':len(season),'source':len(matches),'matched':len(matched),'timestamp':timestamp,'coverage':coverage,'fits':0},sort_keys=True))
        return 0
    except Exception as e:
        fail={'schema_version':'1.0','research_id':'E3g-0','status':'RESEARCH_EXECUTION_FAILED','repository_head':head(),'formal_weight':0,'candidate_model_fit_count':0,'error':f'{type(e).__name__}: {e}','asset_protection':{'formal_model_changed':0,'formal_data_changed':0,'formal_config_changed':0,'current_changed':0,'formal_weight_changed':0}}
        (out/'e3g0_external_pit_value_screen.json').write_text(json.dumps(fail,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(fail,ensure_ascii=False));return 1
if __name__=='__main__':raise SystemExit(main())
