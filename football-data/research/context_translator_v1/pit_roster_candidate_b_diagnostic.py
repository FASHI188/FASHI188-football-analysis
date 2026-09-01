from __future__ import annotations

import argparse,hashlib,json,pathlib,statistics,sys
from collections import defaultdict
from typing import Any
ROOT=pathlib.Path('.').resolve();CTX=ROOT/'football-data/research/context_translator_v1';sys.path.insert(0,str(CTX))
import source_ingest as si
import candidate_b_diagnostic as cbd
from candidate_b import capability_residual
from player_strength import estimate_player_vectors
N_POOL=272;SAMPLE_MAX=100;FORBIDDEN_KEYS={'home_goals','away_goals','final_score','result','target_result','actual_substitution','actual_red_card','actual_var','actual_stoppage'}

def sha(path:pathlib.Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def canon(x:Any)->str:return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def readjl(path:pathlib.Path)->list[dict[str,Any]]:return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
def dump(path:pathlib.Path,obj:Any)->None:path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
def deep_forbidden(obj:Any)->set[str]:
    bad=set()
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in FORBIDDEN_KEYS:bad.add(k)
            bad|=deep_forbidden(v)
    elif isinstance(obj,list):
        for x in obj:bad|=deep_forbidden(x)
    return bad

def validate_packet(p:dict[str,Any])->None:
    if deep_forbidden(p):raise RuntimeError(f'postmatch/result field reached PIT roster payload: {sorted(deep_forbidden(p))}')
    if not p.get('pit_legal'):return
    src=p.get('source') or {};required=('source_url','source_id','source_observed_at','collected_at','available_at','raw_content_sha256')
    if not all(src.get(k) for k in required):raise RuntimeError('legal packet timestamp/source completeness failed')
    if not si._dt(src['available_at'],'available_at')<si._dt(p['kickoff_utc'],'kickoff_utc'):raise RuntimeError('PIT available_at >= kickoff')
    if src.get('raw_content_scope')!='EXACT_H2_TEAM_NEWS_SECTION_ONLY' or not src.get('full_page_not_persisted'):raise RuntimeError('source payload boundary failed')
    if len(src['raw_content_sha256'])!=64:raise RuntimeError('raw fragment sha invalid')
    if p.get('confirmed_lineups') is not None or p.get('bench') is not None:raise RuntimeError('unproven confirmed lineup/bench reached payload')
    if p.get('probability_contract')!='NO_SOURCE_PLAYER_START_PROBABILITIES_DO_NOT_INVENT':raise RuntimeError('probability contract drift')
    for side in ('home','away'):
        xs=(p.get('predicted_lineups') or {}).get(side) or []
        if len(xs)!=11:raise RuntimeError('legal predicted XI must have exactly 11 source-listed players')
        if any(x.get('starting_probability') is not None or x.get('expected_minutes') is not None for x in xs):raise RuntimeError('invented player probability/minutes reached Candidate B')

def merged_usage(hist_usage,roster_usage):
    out=defaultdict(list)
    for src in (hist_usage,roster_usage):
        for k,vals in src.items():out[str(k)].extend(vals)
    for k in out:out[k].sort(key=lambda x:(str(x.get('known_at','')),str(x.get('match_id',''))))
    return out

def prediction_phase(v2:pathlib.Path,candidate:pathlib.Path,source:pathlib.Path,roster_out:pathlib.Path,out:pathlib.Path)->dict[str,Any]:
    out.mkdir(parents=True,exist_ok=True)
    if (v2/'dataset/evaluation_label_vault.jsonl').exists():raise RuntimeError('evaluation labels physically present during prediction')
    manifest=json.load(open(candidate/'artifact_manifest.json'))
    if manifest['n']!=N_POOL or manifest['formal_weight']!=0 or manifest['formal_promotion_eligible']:raise RuntimeError('Candidate B starting artifact governance mismatch')
    old=readjl(candidate/'candidate_b_predictions.jsonl')
    if len(old)!=N_POOL:raise RuntimeError('Candidate B pool n mismatch')
    mechanical_ids=sorted(str(x['fixture_id']) for x in old)[:SAMPLE_MAX];packets=readjl(roster_out/'pit_roster_packets.jsonl')
    if [str(x['fixture_id']) for x in packets]!=mechanical_ids:raise RuntimeError('roster packet order not exact mechanical first-100')
    for p in packets:validate_packet(p)
    pmap={str(x['fixture_id']):x for x in packets};omap={str(x['fixture_id']):x for x in old};_,mapped=si._map_inventory(v2,source,out);ev=readjl(v2/'dataset/evaluation_features.jsonl');eids={str(x['fixture_id']) for x in ev};gp=[(r,s) for r,s in mapped if str(r['fixture_id']) in eids and str(r['competition_id'])=='GER1' and si._season(str(r['season']))=='2023/24']
    if not gp:raise RuntimeError('no GER1 StatsBomb mapping')
    tm=cbd.team_map(gp);hist=cbd.History(gp);eng=cbd.engine();lock=json.load(open(v2/'locks/v2_lock.json'));roster_usage=defaultdict(list);rows=[];reasons=defaultdict(int)
    ordered_ids=sorted(mechanical_ids,key=lambda fid:(omap[fid]['cutoff'],fid))
    for fid in ordered_ids:
        o=omap[fid];packet=pmap[fid];hist.release_before(o['cutoff']);base=o['baseline'];original=o['candidate_b1_b2'];b1_pred=cbd.pred(base['score_matrix'],eng);stack_pred=cbd.pred(base['score_matrix'],eng);b1_active=b2_active=stack_active=False;reason=None;effect=None;ht=tm.get(str(o['home_team_id']));at=tm.get(str(o['away_team_id']))
        if not packet.get('pit_legal'):reason=packet.get('missing_reason') or 'PIT_ROSTER_PACKET_ILLEGAL'
        elif not packet.get('identity_complete'):reason='PIT_ROSTER_PLAYER_IDENTITY_INCOMPLETE'
        elif not ht or not at:reason='STATSBOMB_TEAM_IDENTITY_FALLBACK'
        else:
            pe=[e for e in hist.events if str(e['team_id']) in {ht,at}];vectors=estimate_player_vectors(pe,hist.segments,as_of=o['cutoff']) if pe else {};hp=[str(x['player_id']) for x in packet['predicted_lineups']['home']];ap=[str(x['player_id']) for x in packet['predicted_lineups']['away']];usage=merged_usage(hist.usage,roster_usage)
            if not vectors:reason='INSUFFICIENT_PIT_PLAYER_CAPABILITY_HISTORY'
            else:
                effect=capability_residual(vectors=vectors,usage=usage,home_team_id=ht,away_team_id=at,home_player_ids=hp,away_player_ids=ap,cutoff=o['cutoff'])
                if effect.active:b1_pred=cbd.effect_prediction(base['score_matrix'],effect,lock,eng);b1_active=True;reason='B2_NO_SOURCE_START_PROBABILITIES_OR_BENCH_EXACT_V2_FALLBACK'
                else:reason=effect.reason
        stack_pred=cbd.pred(base['score_matrix'],eng);stack_active=False;b2_active=False;reasons[reason or 'UNKNOWN']+=1;rows.append({'fixture_id':fid,'cutoff':o['cutoff'],'competition_id':o['competition_id'],'season':o['season'],'home_team_id':o['home_team_id'],'away_team_id':o['away_team_id'],'research_status':'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC','baseline':base,'old_l1_l2':o['old_l1_l2'],'candidate_b_original':original,'candidate_b_pit_b1':b1_pred,'candidate_b_pit':stack_pred,'pit_roster_legal':bool(packet.get('pit_legal')),'pit_identity_complete':bool(packet.get('identity_complete')),'b1_active':b1_active,'b2_active':b2_active,'stack_active':stack_active,'fallback_reason':reason,'b1_effect':None if effect is None else effect.to_dict(),'roster_packet_sha256':packet.get('packet_sha256')})
        if packet.get('pit_legal') and packet.get('identity_complete') and ht and at:
            known=packet['source']['available_at']
            for tid,side in ((ht,'home'),(at,'away')):
                players=[{'player_id':str(x['player_id']),'started':True,'appeared':None,'minutes':None,'role':'UNK','known_at':known,'reference_route':'PREMATCH_PREDICTED_MODAL_XI'} for x in packet['predicted_lineups'][side]];roster_usage[tid].append({'players':players,'known_at':known,'match_id':f'pit_roster:{fid}'})
    rmap={r['fixture_id']:r for r in rows};rows=[rmap[x] for x in mechanical_ids];pp=out/'pit_candidate_b_predictions.jsonl';pp.write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for r in rows));b1n=sum(r['b1_active'] for r in rows);b2n=sum(r['b2_active'] for r in rows);sn=sum(r['stack_active'] for r in rows)
    moves={'old_l1_l2':statistics.fmean(cbd.meanmove(r['old_l1_l2'],r['baseline']) for r in rows),'candidate_b_original':statistics.fmean(cbd.meanmove(r['candidate_b_original'],r['baseline']) for r in rows),'candidate_b_pit_b1':statistics.fmean(cbd.meanmove(r['candidate_b_pit_b1'],r['baseline']) for r in rows),'candidate_b_pit':statistics.fmean(cbd.meanmove(r['candidate_b_pit'],r['baseline']) for r in rows)}
    pre={'schema_version':'football3-pit-roster-candidate-b-pre-score-v1','status':'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC','formal_promotion_eligible':False,'formal_weight':0,'labels_read_in_prediction_phase':False,'inventory_n':len(rows),'mechanical_fixture_ids':mechanical_ids,'mechanical_fixture_set_sha256':canon(mechanical_ids),'pit_legal_roster_packet_n':sum(r['pit_roster_legal'] for r in rows),'identity_complete_packet_n':sum(r['pit_identity_complete'] for r in rows),'candidate_b_pit_b1_active_n':b1n,'candidate_b_pit_b2_active_n':b2n,'candidate_b_pit_stack_active_n':sn,'candidate_b_pit_stack_fallback_n':len(rows)-sn,'activation_threshold_n':30,'feasible_for_post_view_scoring':sn>=30,'fallback_reasons':dict(sorted(reasons.items())),'mean_probability_move_vs_baseline':moves,'direction_diagnostics':{'b1_b2_direction_comparable_n':0,'b1_b2_opposite_direction_n':0,'b1_b2_opposite_direction_rate':None,'post_stack_cancellation_n':0,'post_stack_cancellation_rate':None,'reason':'B2 did not receive source-supported probability mass; no direction comparison is scientifically defined'},'prediction_payload_sha256':sha(pp),'new_fixture_labels_read_n':0,'global_fixture_consumption_registry_extended':False};dump(out/'pit_candidate_b_pre_score.json',pre);gate_status='PIT_ROSTER_DATA_FEASIBLE_POST_VIEW_PENDING_SCORE' if sn>=30 else 'INSUFFICIENT_PIT_ROSTER_DATA';dump(out/'pit_roster_gate.json',{'schema_version':'football3-pit-roster-gate-v1','pipeline_integrity':'PASS','status':gate_status,'inventory_n':len(rows),'pit_legal_roster_packet_n':pre['pit_legal_roster_packet_n'],'candidate_b_pit_stack_active_n':sn,'activation_threshold_n':30,'formal_promotion_eligible':False,'formal_weight':0,'new_fixture_labels_read_n':0,'prediction_payload_sha256':pre['prediction_payload_sha256']});return pre

def metric(rows,labels,model):return cbd.metrics([(r,labels[r['fixture_id']]) for r in rows],model)
def score_phase(candidate:pathlib.Path,label_vault:pathlib.Path,out:pathlib.Path)->dict[str,Any]:
    pre=json.load(open(out/'pit_candidate_b_pre_score.json'))
    if not pre['feasible_for_post_view_scoring'] or pre['candidate_b_pit_stack_active_n']<30:raise RuntimeError('score invoked below activation threshold')
    rows=readjl(out/'pit_candidate_b_predictions.jsonl');allowed272={str(x['fixture_id']) for x in readjl(candidate/'candidate_b_predictions.jsonl')}
    if len(allowed272)!=272:raise RuntimeError('allowed POST_VIEW label whitelist not 272')
    labels=cbd.allowed_labels(label_vault,allowed272);sample={r['fixture_id'] for r in rows}
    if not sample<=allowed272:raise RuntimeError('new fixture label attempted')
    labs={k:v for k,v in labels.items() if k in sample};models=('baseline','old_l1_l2','candidate_b_original','candidate_b_pit');overall={m:metric(rows,labs,m) for m in models}
    def sub(fn):
        rr=[r for r in rows if fn(r,labs[r['fixture_id']])];return {'n':len(rr),'metrics':{m:metric(rr,labs,m) for m in models}}
    groups={'actual_draw':sub(lambda r,l:int(l['home_goals'])==int(l['away_goals'])),'weak_team_win':sub(lambda r,l:(int(l['home_goals'])>int(l['away_goals']) and r['baseline']['p_home']<r['baseline']['p_away']) or (int(l['away_goals'])>int(l['home_goals']) and r['baseline']['p_away']<r['baseline']['p_home'])),'b1_active':sub(lambda r,l:r['b1_active']),'stack_active':sub(lambda r,l:r['stack_active'])};result={'schema_version':'football3-pit-roster-candidate-b-post-view-score-v1','status':'POST_VIEW_DIAGNOSTIC','research_only':True,'formal_promotion_eligible':False,'formal_weight':0,'n':len(rows),'already_unsealed_ger1_272_whitelist_n':272,'new_fixture_labels_read_n':0,'models':overall,'subgroups':groups,'trigger':{'b1_active_n':sum(r['b1_active'] for r in rows),'b2_active_n':sum(r['b2_active'] for r in rows),'stack_active_n':sum(r['stack_active'] for r in rows)},'mean_probability_move_vs_baseline':pre['mean_probability_move_vs_baseline'],'direction_diagnostics':pre['direction_diagnostics']};rp=out/'pit_candidate_b_score.json';dump(rp,result);g=json.load(open(out/'pit_roster_gate.json'));g.update({'status':'PIT_ROSTER_DATA_FEASIBLE_POST_VIEW_COMPLETE','score_payload_sha256':sha(rp),'new_fixture_labels_read_n':0});dump(out/'pit_roster_gate.json',g);return result

def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd',required=True);p=sp.add_parser('predict');p.add_argument('--v2',type=pathlib.Path,required=True);p.add_argument('--candidate',type=pathlib.Path,required=True);p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--roster-out',type=pathlib.Path,required=True);p.add_argument('--out',type=pathlib.Path,required=True);s=sp.add_parser('score');s.add_argument('--candidate',type=pathlib.Path,required=True);s.add_argument('--label-vault',type=pathlib.Path,required=True);s.add_argument('--out',type=pathlib.Path,required=True);a=ap.parse_args();print(json.dumps(prediction_phase(a.v2,a.candidate,a.source,a.roster_out,a.out) if a.cmd=='predict' else score_phase(a.candidate,a.label_vault,a.out),indent=2))
if __name__=='__main__':main()
