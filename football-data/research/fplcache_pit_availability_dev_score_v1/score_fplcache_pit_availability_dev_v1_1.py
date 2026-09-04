#!/usr/bin/env python3
import argparse, gzip, hashlib, json, math, pathlib, statistics
from datetime import datetime, timezone

class ScoreError(RuntimeError): pass

def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))

def read_jsonl(path):
    with pathlib.Path(path).open('r',encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)

def read_gz_jsonl(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def write_jsonl(path,rows):
    with pathlib.Path(path).open('w',encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n')

def canon_sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def parse_dt(x):
    return datetime.fromisoformat(str(x).replace('Z','+00:00')).astimezone(timezone.utc)

def minute_key(x):
    return parse_dt(x).replace(second=0,microsecond=0).isoformat()

def outcome_idx(hg,ag):
    return 0 if hg>ag else 1 if hg==ag else 2

def integrate(m):
    h=d=a=0.0
    for i,row in enumerate(m):
        for j,v in enumerate(row):
            v=float(v)
            if i>j: h+=v
            elif i==j: d+=v
            else: a+=v
    s=h+d+a
    if not math.isfinite(s) or abs(s-1.0)>1e-8: raise ScoreError(f'matrix mass {s}')
    return [h/s,d/s,a/s]

def iproject(m,target):
    base=integrate(m)
    if any(x<=0 for x in base): raise ScoreError('zero base region mass')
    scale=[target[i]/base[i] for i in range(3)]
    out=[]
    for i,row in enumerate(m):
        rr=[]
        for j,v in enumerate(row):
            k=0 if i>j else 1 if i==j else 2
            rr.append(float(v)*scale[k])
        out.append(rr)
    s=sum(sum(r) for r in out)
    out=[[v/s for v in r] for r in out]
    return out

def metric(rows,pkey,mkey=None):
    n=len(rows)
    if not n: raise ScoreError('empty metric cohort')
    ll=br=rps=top=score_ll=0.0
    for r in rows:
        p=r[pkey]; y=r['y']
        ll += -math.log(max(float(p[y]),1e-15))
        br += sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in range(3))
        c1=float(p[0]); c2=float(p[0])+float(p[1])
        o1=1.0 if y==0 else 0.0
        o2=1.0 if y in (0,1) else 0.0
        rps += ((c1-o1)**2+(c2-o2)**2)/2.0
        top += int(max(range(3),key=lambda k:p[k])==y)
        if mkey:
            hg,ag=r['home_goals'],r['away_goals']
            if not (0<=hg<15 and 0<=ag<15): raise ScoreError('score outside 0-14 matrix support')
            score_ll += -math.log(max(float(r[mkey][hg][ag]),1e-15))
    out={'n':n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_accuracy':top/n}
    if mkey: out['exact_score_logloss']=score_ll/n
    return out

def paired_required_n(rows):
    ds=[]
    for r in rows:
        y=r['y']
        ds.append(-math.log(max(r['baseline_p'][y],1e-15))+math.log(max(r['candidate_p'][y],1e-15)))
    eff=sum(ds)/len(ds)
    sd=statistics.stdev(ds) if len(ds)>1 else 0.0
    req=None
    if eff>0 and sd>0:
        z=1.959963984540054+0.8416212335729143
        req=math.ceil((z*sd/eff)**2)
    return {'effect':eff,'paired_sd':sd,'required_n':req}

def team_impairment(snapshot,fixture_team_name,snapshot_aliases):
    snapshot_name=snapshot_aliases.get(fixture_team_name,fixture_team_name)
    teams={str(t['name']):int(t['id']) for t in snapshot.get('teams',[])}
    if snapshot_name not in teams: raise ScoreError(f'team absent in snapshot: {fixture_team_name}->{snapshot_name}')
    tid=teams[snapshot_name]
    players=[p for p in snapshot.get('players',[]) if int(p.get('team',-1))==tid]
    if not players: raise ScoreError(f'no players for team {snapshot_name}')
    mins=[]
    for p in players:
        try: x=max(0.0,float(p.get('minutes') or 0.0))
        except Exception: x=0.0
        mins.append(x)
    den=sum(mins)
    if den<=0: return 0.0
    return sum(x for p,x in zip(players,mins) if str(p.get('status') or '')!='a')/den

def eval_cohort(rows):
    b=metric(rows,'baseline_p','baseline_matrix')
    c=metric(rows,'candidate_p','candidate_matrix')
    return {
        'n':len(rows),'baseline':b,'candidate':c,
        'deltas':{
            'one_x_two_logloss_gain':b['logloss']-c['logloss'],
            'one_x_two_brier_delta':c['brier']-b['brier'],
            'one_x_two_rps_delta':c['rps']-b['rps'],
            'top1_delta':c['top1_accuracy']-b['top1_accuracy'],
            'exact_score_logloss_gain':b['exact_score_logloss']-c['exact_score_logloss'],
        },
        'paired_one_x_two':paired_required_n(rows),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--contract',required=True)
    ap.add_argument('--fpl-dir',required=True)
    ap.add_argument('--stress-dir',required=True)
    ap.add_argument('--history-dir',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args(); c=read_json(a.contract)
    if c['status']!='FROZEN_BEFORE_BULK_OUTCOME_SCORING': raise ScoreError('contract not frozen')
    auth=c['authorization']
    if auth['training_allowed'] or auth['tuning_allowed'] or auth['parameter_search_allowed'] or auth['candidate_selection_allowed']: raise ScoreError('forbidden authorization drift')
    if c['single_frozen_candidate']['parameter_grid']!={}: raise ScoreError('parameter grid not empty')
    out=pathlib.Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fpl=pathlib.Path(a.fpl_dir); stress=pathlib.Path(a.stress_dir); hist=pathlib.Path(a.history_dir)
    season_labels=set(c['development_scope']['fplcache_season_labels'])
    under_alias=c['fixture_join']['team_aliases_understat_to_fplcache_fixture']
    snap_alias=c['fixture_join']['fplcache_fixture_to_snapshot_team_aliases']

    cut_by_pair={}; needed=set()
    for r in read_gz_jsonl(fpl/'fixture_cutoff_map.jsonl.gz'):
        if r['season'] not in season_labels: continue
        k=(r['season'],r['home_team'],r['away_team'])
        if k in cut_by_pair: raise ScoreError(f'duplicate fpl pair {k}')
        cut_by_pair[k]=r
        for w in ('T_MINUS_24H','T_MINUS_6H','T_MINUS_90M'):
            x=r['cutoffs'][w]
            if not x['acceptable_staleness']: raise ScoreError(f'unacceptable staleness {k} {w}')
            needed.add(x['snapshot']['path'])
    if len(cut_by_pair)!=int(c['development_scope']['all_pair_unique_fixture_count']): raise ScoreError(f'fpl pair count {len(cut_by_pair)}')

    snapshots={}
    for s in read_gz_jsonl(fpl/'availability_snapshots.jsonl.gz'):
        p=s.get('source',{}).get('path')
        if p in needed: snapshots[p]=s
    if needed-set(snapshots): raise ScoreError('missing selected snapshots')

    fixtures={r['fixture_id']:r for r in read_jsonl(hist/'data/fixtures.jsonl') if r['league']=='EPL' and int(r['season']) in (2024,2025)}
    preds={r['fixture_id']:r for r in read_jsonl(stress/'evidence/predictions_label_free.jsonl') if r['league']=='EPL' and int(r['season']) in (2024,2025)}
    labels={r['fixture_id']:r for r in read_jsonl(hist/'data/label_vault.jsonl') if r['fixture_id'] in fixtures}
    if len(fixtures)!=760 or len(preds)!=760 or len(labels)!=760: raise ScoreError(f'input count {len(fixtures)} {len(preds)} {len(labels)}')

    all_rows=[]; pred_out=[]; pair_unresolved=[]; primary_unresolved=[]; max_matrix_diff=0.0; max_prob_delta=0.0
    for fid in sorted(fixtures,key=lambda z:(parse_dt(fixtures[z]['kickoff']),z)):
        fr=fixtures[fid]; pr=preds[fid]; lab=labels[fid]
        season=f"{int(fr['season'])}-{str(int(fr['season'])+1)[-2:]}"
        home=under_alias.get(fr['home_team_name'],fr['home_team_name']); away=under_alias.get(fr['away_team_name'],fr['away_team_name'])
        pk=(season,home,away); cr=cut_by_pair.get(pk)
        if cr is None:
            pair_unresolved.append({'fixture_id':fid,'key':pk}); continue
        exact=minute_key(fr['kickoff'])==minute_key(cr['kickoff_utc'])
        if not exact: primary_unresolved.append({'fixture_id':fid,'understat_kickoff':fr['kickoff'],'fpl_kickoff':cr['kickoff_utc']})
        imp={}
        for w in ('T_MINUS_24H','T_MINUS_6H','T_MINUS_90M'):
            snap=snapshots[cr['cutoffs'][w]['snapshot']['path']]
            imp[w]=(team_impairment(snap,home,snap_alias),team_impairment(snap,away,snap_alias))
        ih,ia=imp['T_MINUS_90M']; d=ia-ih
        p=[float(x) for x in pr['v3_1_1_1x2']]
        z=p[0]*math.exp(d)+p[1]+p[2]*math.exp(-d)
        q=[p[0]*math.exp(d)/z,p[1]/z,p[2]*math.exp(-d)/z]
        cm=iproject(pr['v3_1_1_matrix'],q); qi=integrate(cm)
        max_matrix_diff=max(max_matrix_diff,max(abs(x-y) for x,y in zip(q,qi)))
        max_prob_delta=max(max_prob_delta,max(abs(x-y) for x,y in zip(p,q)))
        change_d=(imp['T_MINUS_6H'][1]-imp['T_MINUS_24H'][1])-(imp['T_MINUS_6H'][0]-imp['T_MINUS_24H'][0])
        hg=int(lab['home_goals']); ag=int(lab['away_goals'])
        row={'fixture_id':fid,'season':int(fr['season']),'exact_kickoff_identity':exact,'home_goals':hg,'away_goals':ag,'y':outcome_idx(hg,ag),
             'baseline_p':p,'candidate_p':q,'baseline_matrix':pr['v3_1_1_matrix'],'candidate_matrix':cm,'availability_delta':d,'diagnostic_change_delta':change_d}
        all_rows.append(row)
        pred_out.append({'fixture_id':fid,'season':int(fr['season']),'home_team':fr['home_team_name'],'away_team':fr['away_team_name'],
                         'understat_kickoff':fr['kickoff'],'fpl_kickoff':cr['kickoff_utc'],'primary_exact_kickoff_identity':exact,
                         'home_impairment_t24':imp['T_MINUS_24H'][0],'away_impairment_t24':imp['T_MINUS_24H'][1],
                         'home_impairment_t6':imp['T_MINUS_6H'][0],'away_impairment_t6':imp['T_MINUS_6H'][1],
                         'home_impairment_t90':ih,'away_impairment_t90':ia,'availability_delta':d,'diagnostic_change_delta':change_d,
                         'baseline_v3_1_1_1x2':p,'candidate_1x2':q,'candidate_matrix_sha256':canon_sha(cm)})
    if pair_unresolved or len(all_rows)!=760: raise ScoreError(f'pair unresolved {len(pair_unresolved)} rows {len(all_rows)}')
    primary=[r for r in all_rows if r['exact_kickoff_identity']]
    secondary=all_rows
    exp_primary=int(c['development_scope']['primary_exact_kickoff_fixture_count'])
    if len(primary)!=exp_primary: raise ScoreError(f'primary count {len(primary)} expected {exp_primary}')
    bys={2024:sum(r['season']==2024 for r in primary),2025:sum(r['season']==2025 for r in primary)}
    expected_bys=c['development_scope']['primary_by_season']
    if bys!={2024:int(expected_bys['2024-25']),2025:int(expected_bys['2025-26'])}: raise ScoreError(f'primary season counts {bys}')

    pri=eval_cohort(primary); sec=eval_cohort(secondary)
    season_blocks=[]; seasons_ok=True
    for s in (2024,2025):
        e=eval_cohort([r for r in primary if r['season']==s]); ok=e['deltas']['one_x_two_logloss_gain']>=-1e-15; seasons_ok &= ok
        season_blocks.append({'season':s,**e,'logloss_nondegrade':ok})
    active=sum(abs(r['availability_delta'])>1e-15 for r in primary)
    change_active=sum(abs(r['diagnostic_change_delta'])>1e-15 for r in primary)
    g=c['development_gates']
    d=pri['deltas']
    gates={
      'primary_fixture_count_exact':len(primary)==int(g['primary_fixture_count_exact']),
      'primary_unresolved_fixture_count':0<=int(g['primary_unresolved_fixture_count_max']),
      'pair_key_unresolved_count':len(pair_unresolved)==int(c['fixture_join']['secondary_required_unresolved_count']),
      'kickoff_mismatch_count_frozen':len(primary_unresolved)==int(c['identity_preflight']['kickoff_mismatch_count']),
      'signal_active_fixture_min':active>=int(g['signal_active_fixture_min']),
      'pooled_1x2_logloss_gain':d['one_x_two_logloss_gain']>=float(g['pooled_1x2_logloss_gain_min'])-1e-15,
      'pooled_1x2_brier_delta':d['one_x_two_brier_delta']<=float(g['pooled_1x2_brier_delta_max'])+1e-15,
      'pooled_1x2_rps_delta':d['one_x_two_rps_delta']<=float(g['pooled_1x2_rps_delta_max'])+1e-15,
      'pooled_top1_delta':d['top1_delta']>=float(g['pooled_top1_delta_min'])-1e-15,
      'both_season_blocks_1x2_logloss_nondegrade':seasons_ok,
      'exact_score_gain_identity':abs(d['exact_score_logloss_gain']-d['one_x_two_logloss_gain'])<=float(g['exact_score_logloss_gain_must_equal_1x2_logloss_gain_within']),
      'matrix_to_candidate_1x2':max_matrix_diff<=float(g['matrix_to_candidate_1x2_max_abs_diff'])
    }
    status=c['terminal']['pass'] if all(gates.values()) else c['terminal']['fail']
    result={
      'schema_version':'football3-fplcache-pit-availability-dev-score-result-v1.1','status':status,'classification':c['classification'],
      'fresh_confirmation':False,'promotion_allowed':False,'training_performed':False,'tuning_performed':False,'parameter_search_performed':False,'candidate_selection_performed':False,
      'formal_weight_changed':False,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,
      'primary_exact_kickoff':pri,'secondary_pair_unique_sensitivity':sec,'primary_season_blocks':season_blocks,
      'primary_signal_active_fixture_count':active,'primary_diagnostic_t24_t6_change_active_fixture_count':change_active,
      'max_probability_abs_delta_all760':max_prob_delta,'matrix_to_candidate_1x2_max_abs_diff':max_matrix_diff,
      'identity':{'pair_unresolved_count':len(pair_unresolved),'kickoff_mismatch_count':len(primary_unresolved),'kickoff_mismatch_examples':primary_unresolved[:10]},
      'gates':gates,'contract_sha256':hashlib.sha256(pathlib.Path(a.contract).read_bytes()).hexdigest()
    }
    write_json(out/'development_score_result.json',result)
    write_json(out/'primary_season_blocks.json',season_blocks)
    write_jsonl(out/'predictions_label_free.jsonl',pred_out)
    (out/'artifact_slug.txt').write_text(f"{status}__n_{len(primary)}__x1gain_{d['one_x_two_logloss_gain']:.6f}__top1delta_{d['top1_delta']:.6f}__active_{active}\n",encoding='utf-8')
    print(json.dumps({'status':status,'primary_n':len(primary),'secondary_n':len(secondary),'active':active,'x1gain':d['one_x_two_logloss_gain'],'brier_delta':d['one_x_two_brier_delta'],'rps_delta':d['one_x_two_rps_delta'],'top1_delta':d['top1_delta'],'required_n':pri['paired_one_x_two']['required_n'],'secondary_x1gain':sec['deltas']['one_x_two_logloss_gain'],'secondary_top1_delta':sec['deltas']['top1_delta'],'max_prob_delta':max_prob_delta,'gates':gates},sort_keys=True))

if __name__=='__main__': main()
