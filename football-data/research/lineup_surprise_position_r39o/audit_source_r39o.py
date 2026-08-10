#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,re,statistics,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
R39N=HERE.parent/'lineup_market_value_r39n'
sys.path.insert(0,str(R39N))
import export_valuation_snapshot_r39n as nv


def sha_file(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def parse_date(s):return datetime.strptime(str(s).strip()[:10],'%Y-%m-%d').date()
def norm_type(s):return re.sub(r'[^a-z0-9]+','_',str(s).strip().lower()).strip('_')
def coarse_position(s):
    p=str(s or '').strip().lower()
    if 'goalkeeper' in p:return 'GK'
    if 'defender' in p:return 'DEF'
    if 'midfield' in p:return 'MID'
    if 'attack' in p:return 'ATT'
    return 'OTHER'

def load_full_game_and_lineup_history(games_path:Path,lineups_path:Path):
    games={}
    with games_path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
        rd=csv.DictReader(f);need={'game_id','date','home_club_id','away_club_id'}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('games history schema mismatch')
        for r in rd:
            gid=str(r.get('game_id','')).strip()
            try:d=parse_date(r.get('date',''))
            except:continue
            if not gid:continue
            games[gid]={'date':d,'home':str(r.get('home_club_id','')).strip(),'away':str(r.get('away_club_id','')).strip()}
    starter_pos=defaultdict(dict);raw_pos=Counter();type_counts=Counter();rows=0
    with lineups_path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
        rd=csv.DictReader(f);need={'game_id','player_id','club_id','type','position'}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('lineup history schema mismatch')
        for r in rd:
            gid=str(r.get('game_id','')).strip()
            if gid not in games:continue
            rows+=1;t=norm_type(r.get('type',''));type_counts[t]+=1
            if 'starting' not in t:continue
            club=str(r.get('club_id','')).strip();pid=str(r.get('player_id','')).strip();pos=str(r.get('position','')).strip()
            if not club or not pid:continue
            starter_pos[(gid,club)][pid]=pos;raw_pos[pos]+=1
    history=defaultdict(list);complete_sides=0
    for (gid,club),pm in starter_pos.items():
        if len(pm)!=11:continue
        complete_sides+=1
        history[club].append((games[gid]['date'],gid,dict(pm)))
    for club in history:history[club].sort(key=lambda z:(z[0],z[1]))
    return games,starter_pos,history,{'lineup_rows':rows,'complete_club_sides':complete_sides,'raw_starting_position_counts_all_source':dict(raw_pos),'lineup_type_counts':dict(type_counts)}

def side_valuation(players,target_date,vidx):
    vals=[];maxd=None
    for pid in players:
        v,d=nv.prior_value(vidx,pid,target_date)
        if v is None:continue
        vals.append(float(v));maxd=d if maxd is None or d>maxd else maxd
    return vals,maxd

def prior_lineup_value_counts(history,club,target_date,vidx,window):
    prior=[x for x in history.get(str(club),[]) if x[0] < target_date]
    recent=prior[-int(window):]
    counts=[];totals=[];maxd=None
    for _d,_gid,pm in recent:
        vals,md=side_valuation(pm.keys(),target_date,vidx);counts.append(len(vals));totals.append(sum(vals) if vals else None)
        if md is not None:maxd=md if maxd is None or md>maxd else maxd
    return len(prior),counts,totals,maxd

def qstats(xs):
    if not xs:return None
    ys=sorted(float(x) for x in xs);n=len(ys)
    def q(p):return ys[min(n-1,max(0,int(round((n-1)*p))))]
    return {'min':ys[0],'p10':q(.10),'p25':q(.25),'median':q(.50),'p75':q(.75),'p90':q(.90),'max':ys[-1],'mean':sum(ys)/n}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--r39i-registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--games',type=Path,required=True);ap.add_argument('--lineups',type=Path,required=True);ap.add_argument('--valuations',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());r39i=json.loads(a.r39i_registration.read_text())
    games_target,starters_target,complete,mapped,type_counts_target,lineup_rows_target=nv.build_mapping(a.market_dir,a.games,a.lineups,r39i)
    lane=reg['identity_lane'];pre=[m for m in mapped if m['season']!='2526'];hold=[m for m in mapped if m['season']=='2526']
    if (len(pre),len(hold))!=(lane['complete_preholdout_rows'],lane['complete_2526_rows']):raise RuntimeError(f'identity lane drift {len(pre)} {len(hold)}')
    fixed=sorted(hold,key=lambda r:nv.lf.htxt(f"{lane['fixed100_seed']}|{r['identity']}"))[:lane['fixed100_rows']]
    fixedsha=nv.lf.set_sha([r['identity'] for r in fixed])
    if fixedsha!=lane['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 identity drift {fixedsha}')
    _games_all,starter_pos,history,hmeta=load_full_game_and_lineup_history(a.games,a.lineups)
    vidx,vmeta=nv.load_valuations(a.valuations)
    target_raw_pos=Counter();target_coarse=Counter();target_other=Counter();target_category_complete=0;actual_counts=[];prior_counts_home=[];prior_counts_away=[];strict_viol=0
    windows=[int(x) for x in reg['audit_only_quantities']['prior_lineup_windows_reported']];thresholds=[int(x) for x in reg['audit_only_quantities']['valued_starter_thresholds_reported']]
    combo={f'w{w}_v{t}':{'all':0,'preholdout':0,'holdout_2526':0,'fixed100':0} for w in windows for t in thresholds};fixed_ids={x['identity'] for x in fixed}
    actual_threshold={str(t):{'all':0,'preholdout':0,'holdout_2526':0,'fixed100':0} for t in thresholds}
    for m in mapped:
        hpm=starter_pos.get((m['tm_game_id'],m['home_club_id']),{});apm=starter_pos.get((m['tm_game_id'],m['away_club_id']),{})
        if len(hpm)!=11 or len(apm)!=11:raise RuntimeError(f'target actual XI position history mismatch {m["identity"]}')
        side_cats=[]
        for pm in (hpm,apm):
            cc=Counter()
            for pos in pm.values():
                target_raw_pos[pos]+=1;c=coarse_position(pos);target_coarse[c]+=1;cc[c]+=1
                if c=='OTHER':target_other[pos]+=1
            side_cats.append(cc)
        if all(c['GK']>=1 and c['DEF']>=1 and c['MID']>=1 and c['ATT']>=1 and c['OTHER']==0 for c in side_cats):target_category_complete+=1
        hv,hmd=side_valuation(hpm.keys(),m['target_date'],vidx);av,amd=side_valuation(apm.keys(),m['target_date'],vidx);actual_counts.extend([len(hv),len(av)])
        tord=m['target_date'].toordinal()
        for md in (hmd,amd):
            if md is not None and md>=tord:strict_viol+=1
        for t in thresholds:
            if len(hv)>=t and len(av)>=t:
                actual_threshold[str(t)]['all']+=1
                actual_threshold[str(t)]['holdout_2526' if m['season']=='2526' else 'preholdout']+=1
                if m['identity'] in fixed_ids:actual_threshold[str(t)]['fixed100']+=1
        for w in windows:
            hprior,hcounts,htot,hmax=prior_lineup_value_counts(history,m['home_club_id'],m['target_date'],vidx,w);aprior,acounts,atot,amax=prior_lineup_value_counts(history,m['away_club_id'],m['target_date'],vidx,w)
            if w==5:prior_counts_home.append(hprior);prior_counts_away.append(aprior)
            for md in (hmax,amax):
                if md is not None and md>=tord:strict_viol+=1
            for t in thresholds:
                # Baseline can be the median of at least 3 recent complete lineups. Each contributing lineup must have >= t valued starters at target-date PIT values.
                hg=sum(1 for c in hcounts if c>=t);ag=sum(1 for c in acounts if c>=t)
                ok=len(hv)>=t and len(av)>=t and hg>=3 and ag>=3
                if ok:
                    k=f'w{w}_v{t}';combo[k]['all']+=1;combo[k]['holdout_2526' if m['season']=='2526' else 'preholdout']+=1
                    if m['identity'] in fixed_ids:combo[k]['fixed100']+=1
    passed=(len(mapped)==reg['pass_conditions']['required_actual_starting_xi_rows'] and strict_viol<=reg['pass_conditions']['strict_target_valuation_lag_violations_max'])
    market_hash=sha_text('\n'.join(f'{p.name}:{sha_file(p)}' for p in sorted(a.market_dir.glob('*.csv')))+'\n')
    receipt={'schema_version':reg['schema_version'],'status':'PASS_R39O_ZERO_LABEL_SOURCE_AUDIT' if passed else 'STOP_R39O_ZERO_LABEL_SOURCE_AUDIT','generated_at_utc':datetime.now(timezone.utc).isoformat(),'identity_lane':{'all':len(mapped),'preholdout':len(pre),'holdout_2526':len(hold),'fixed100':len(fixed),'fixed100_identity_sha256':fixedsha},'target_position_audit':{'raw_position_counts':dict(target_raw_pos),'coarse_position_counts':dict(target_coarse),'other_position_counts':dict(target_other),'matches_both_sides_all_four_coarse_groups_and_no_other':target_category_complete},'actual_XI_valued_starter_count_stats':qstats(actual_counts),'actual_XI_match_eligibility_by_valued_threshold':actual_threshold,'strictly_prior_complete_lineup_count_stats':{'home':qstats(prior_counts_home),'away':qstats(prior_counts_away)},'baseline_eligibility_grid':combo,'strict_valuation_lag_violations':strict_viol,'valuation_table':vmeta,'full_lineup_history':hmeta,'target_mapping_lineup_rows':lineup_rows_target,'target_mapping_type_counts':dict(type_counts_target),'source_snapshot_sha256':{'games_identity_csv':sha_file(a.games),'lineups_identity_csv':sha_file(a.lineups),'valuations_prior_csv':sha_file(a.valuations),'market_files_combined':market_hash},'zero_label_contract':reg['no_target_label_contract'],'hard_limits':reg['hard_limits']}
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'source_audit_receipt_r39o.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
