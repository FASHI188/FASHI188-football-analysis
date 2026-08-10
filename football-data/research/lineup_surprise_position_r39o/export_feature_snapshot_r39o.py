#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,statistics,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39N=HERE.parent/'lineup_market_value_r39n'
sys.path.insert(0,str(R39N))
import export_valuation_snapshot_r39n as nv
import audit_source_r39o as audit

SOURCE_HASHES={
    'games_identity_csv':'de8a9c8c66b6788ab8bb45918d3c32d7808ddf8cb4738d8455e68676ae81c735',
    'lineups_identity_csv':'d28132a4dca1acee26ba92645484292e6b6f0a7ea2b4967b281f3e594e16016c',
    'valuations_prior_csv':'5085ffc0532b2033d53c7b140d121b237ab38da4ea92aadfa3eef2ba249fb39d',
    'market_files_combined':'0987e1e1b1ad5e79c332f430deb208cfe34c3b26a18f6a0c78aeafef93f6333d',
}
POSITION_MAP={
    'Goalkeeper':'GK',
    'Centre-Back':'DEF','Left-Back':'DEF','Right-Back':'DEF','Sweeper':'DEF','Defender':'DEF',
    'Defensive Midfield':'MID','Central Midfield':'MID','Attacking Midfield':'MID','Left Midfield':'MID','Right Midfield':'MID','Midfield':'MID','midfield':'MID',
    'Centre-Forward':'ATT','Left Winger':'ATT','Right Winger':'ATT','Second Striker':'ATT','Attack':'ATT',
}


def sha_file(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def source_hashes(games,lineups,valuations,market_dir):
    return {
        'games_identity_csv':sha_file(games),
        'lineups_identity_csv':sha_file(lineups),
        'valuations_prior_csv':sha_file(valuations),
        'market_files_combined':sha_text('\n'.join(f'{p.name}:{sha_file(p)}' for p in sorted(Path(market_dir).glob('*.csv')))+'\n'),
    }
def feature_set_sha(rows):
    lines=[]
    for r in sorted(rows,key=lambda z:z['identity']):
        lines.append(r['identity']+'|'+','.join(format(float(v),'.17g') for v in r['x']))
    return sha_text('\n'.join(lines)+'\n')
def identity_sha(rows):return nv.lf.set_sha([r['identity'] for r in rows])

def mapped_position(raw):return POSITION_MAP.get(str(raw).strip())

def lineup_metrics(player_pos,target_date,vidx,min_valued=9):
    vals=[];deflogs=[];offlogs=[];maxd=None
    for pid,pos in player_pos.items():
        cat=mapped_position(pos)
        if cat is None:return None,{'reason':'unknown_position','raw_position':str(pos)}
        v,d=nv.prior_value(vidx,pid,target_date)
        if v is None:continue
        lv=math.log1p(float(v));vals.append(float(v))
        if cat in {'GK','DEF'}:deflogs.append(lv)
        elif cat in {'MID','ATT'}:offlogs.append(lv)
        maxd=d if maxd is None or d>maxd else maxd
    if len(vals)<min_valued:return None,{'reason':'valued_starters_below_threshold','valued':len(vals)}
    if not deflogs or not offlogs:return None,{'reason':'unit_empty','def_n':len(deflogs),'off_n':len(offlogs)}
    t=target_date.toordinal()
    if maxd is None or maxd>=t:return None,{'reason':'strict_valuation_lag_violation','max_date':maxd,'target':t}
    dmean=float(np.mean(deflogs));omean=float(np.mean(offlogs))
    return {
        'total_log':math.log1p(sum(vals)),
        'def_mean_log':dmean,
        'off_mean_log':omean,
        'balance':dmean-omean,
        'valued':len(vals),
        'max_date':maxd,
    },None

def recent_baseline(history,club,target_date,vidx,window=5,min_valued=9,min_valid=3):
    prior=[x for x in history.get(str(club),[]) if x[0] < target_date]
    recent=prior[-window:]
    valid=[];rejected=Counter()
    for _d,_gid,pm in recent:
        m,err=lineup_metrics(pm,target_date,vidx,min_valued)
        if m is None:
            rejected[err['reason']]+=1
            continue
        valid.append(m)
    if len(valid)<min_valid:return None,{'reason':'fewer_than_three_valid_prior_lineups','recent_count':len(recent),'valid_count':len(valid),'rejected':dict(rejected)}
    keys=('total_log','def_mean_log','off_mean_log','balance')
    base={k:float(statistics.median([z[k] for z in valid])) for k in keys}
    return base,{'recent_count':len(recent),'valid_count':len(valid),'rejected':dict(rejected)}

def surprise(actual,baseline):
    return {k:float(actual[k]-baseline[k]) for k in ('total_log','def_mean_log','off_mean_log','balance')}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--r39i-registration',type=Path,required=True)
    ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--valuations',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();pre=json.loads(a.prereg.read_text());r39i=json.loads(a.r39i_registration.read_text())
    observed=source_hashes(a.games,a.lineups,a.valuations,a.market_dir)
    if observed!=SOURCE_HASHES:raise RuntimeError(f'R39O audited source snapshot drift: {observed}')
    _games_target,_starters_target,_complete,mapped,_type_counts,_lineup_rows=nv.build_mapping(a.market_dir,a.games,a.lineups,r39i)
    pre_lane=[m for m in mapped if m['season']!='2526'];hold_lane=[m for m in mapped if m['season']=='2526']
    if (len(pre_lane),len(hold_lane))!=(8161,1273):raise RuntimeError(f'identity lane drift {len(pre_lane)} {len(hold_lane)}')
    fixed_all=sorted(hold_lane,key=lambda r:nv.lf.htxt(f"51145|{r['identity']}"))[:100];fixedsha=nv.lf.set_sha([r['identity'] for r in fixed_all])
    if fixedsha!=pre['blind_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {fixedsha}')
    _games_all,starter_pos,history,_hmeta=audit.load_full_game_and_lineup_history(a.games,a.lineups)
    vidx,_vmeta=nv.load_valuations(a.valuations)
    rows=[];reasons=Counter();prior_rejections=Counter();unknown_target=Counter();strict_viol=0
    for m in mapped:
        hpm=starter_pos.get((m['tm_game_id'],m['home_club_id']),{});apm=starter_pos.get((m['tm_game_id'],m['away_club_id']),{})
        if len(hpm)!=11 or len(apm)!=11:reasons['target_xi_not_11']+=1;continue
        for pos in list(hpm.values())+list(apm.values()):
            if mapped_position(pos) is None:unknown_target[str(pos)]+=1
        if any(mapped_position(pos) is None for pos in list(hpm.values())+list(apm.values())):
            reasons['unknown_target_position']+=1;continue
        hm,he=lineup_metrics(hpm,m['target_date'],vidx,9);am,ae=lineup_metrics(apm,m['target_date'],vidx,9)
        if hm is None or am is None:
            reasons[(he or ae)['reason']]+=1
            if (he or ae)['reason']=='strict_valuation_lag_violation':strict_viol+=1
            continue
        hb,ha=recent_baseline(history,m['home_club_id'],m['target_date'],vidx,5,9,3);ab,aa=recent_baseline(history,m['away_club_id'],m['target_date'],vidx,5,9,3)
        for info in (ha,aa):
            if info:
                for k,v in info.get('rejected',{}).items():prior_rejections[k]+=int(v)
        if hb is None or ab is None:
            reasons['prior5_baseline_ineligible']+=1;continue
        hs=surprise(hm,hb);as_=surprise(am,ab);q=m['qclose']
        x=[
            abs(float(q[0]-q[2])),nv.lf.entropy(q),
            hs['total_log'],as_['total_log'],abs(hs['total_log']-as_['total_log']),
            hs['def_mean_log'],as_['def_mean_log'],abs(hs['def_mean_log']-as_['def_mean_log']),
            hs['off_mean_log'],as_['off_mean_log'],abs(hs['off_mean_log']-as_['off_mean_log']),
            hs['balance'],as_['balance'],abs(hs['balance']-as_['balance'])]
        if len(x)!=14 or any(not math.isfinite(float(v)) for v in x):reasons['nonfinite_feature']+=1;continue
        rows.append({'identity':m['identity'],'season':m['season'],'div':m['div'],'date':str(m['target_date']),'q':[float(v) for v in q],'x':[float(v) for v in x]})
    rows.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    elig={r['identity']:r for r in rows};pre_rows=[r for r in rows if r['season']!='2526'];hold_rows=[r for r in rows if r['season']=='2526'];fixed_ids={r['identity'] for r in fixed_all};fixed_rows=[elig[i] for i in fixed_ids if i in elig]
    expected=pre['zero_label_snapshot_gate']['expected_feature_eligible_from_source_audit'];actual={'all':len(rows),'preholdout':len(pre_rows),'holdout_2526':len(hold_rows),'fixed100':len(fixed_rows)}
    passed=(actual==expected and fixedsha==pre['zero_label_snapshot_gate']['fixed100_identity_sha256_required'] and not unknown_target and strict_viol==0 and reasons.get('nonfinite_feature',0)==0)
    a.out_dir.mkdir(parents=True,exist_ok=True);csvp=a.out_dir/'feature_snapshot_r39o.csv';fields=['identity','season','div','date','qH','qD','qA']+[f'x{i}' for i in range(14)]
    with csvp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            z={'identity':r['identity'],'season':r['season'],'div':r['div'],'date':r['date'],'qH':r['q'][0],'qD':r['q'][1],'qA':r['q'][2]};z.update({f'x{i}':v for i,v in enumerate(r['x'])});w.writerow(z)
    receipt={
        'schema_version':'r39o-zero-label-feature-snapshot-v1','status':'PASS_R39O_ZERO_LABEL_FEATURE_SNAPSHOT' if passed else pre['zero_label_snapshot_gate']['if_fail'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'rows':actual,'fixed100_identity_sha256':fixedsha,'eligible_identity_sha256':{'all':identity_sha(rows),'preholdout':identity_sha(pre_rows),'holdout_2526':identity_sha(hold_rows)},
        'feature_vector_sha256':feature_set_sha(rows),'snapshot_csv_sha256':sha_file(csvp),'source_snapshot_sha256':observed,'source_audit_artifact_id':pre['zero_label_source_audit']['artifact_id'],'source_audit_artifact_sha256':pre['zero_label_source_audit']['artifact_sha256'],
        'final_position_mapping':pre['zero_label_schema_amendment']['final_position_mapping'],'unknown_target_position_counts':dict(unknown_target),'match_ineligible_reasons':dict(reasons),'prior_lineup_rejections':dict(prior_rejections),'strict_valuation_lag_violations':strict_viol,
        'zero_label_contract':{'football_data_FTR_accessed':0,'football_data_score_values_accessed':0,'target_result_labels_accessed':0,'target_match_performance_used':0,'future_valuation_used':0,'same_day_valuation_used':0,'model_fit':0,'metric_against_target_outcome':0,'holdout_labels_accessed':0},'hard_limits':pre['hard_limits']}
    (a.out_dir/'snapshot_receipt_r39o.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
