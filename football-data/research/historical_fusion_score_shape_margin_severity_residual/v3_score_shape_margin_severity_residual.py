#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,importlib.util,json,math,pathlib,sqlite3,sys

TOL=1e-12
MAX_SCORE=14
class SeverityError(RuntimeError):pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path));mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

SHARED=pathlib.Path(__file__).resolve().parents[1]/'historical_fusion_score_shape_shot_fano_state'/'v3_score_shape_shot_fano_state.py'
shared=loadmod('severity_shared',SHARED)
write_json=shared.write_json;integrate=shared.integrate;marginal_total=shared.marginal_total
iproject=shared.iproject;fixture_maps=shared.fixture_maps;map_row=shared.map_row;target_seasons=shared.target_seasons;per_season_exact=shared.per_season_exact

def outcome(h,a):return 'H' if h>a else ('A' if h<a else 'D')
def partkey(h,a):return (h+a,outcome(h,a))

def partition_cells(n):
    d={}
    for h in range(n):
        for a in range(n):d.setdefault(partkey(h,a),[]).append((h,a))
    return d

class ScorePrefix:
    def __init__(self,entries):
        es=sorted(entries,key=lambda z:z[0]);self.d=[];self.c=[];running={}
        for dt,cell in es:
            running=dict(running);running[cell]=running.get(cell,0)+1;self.d.append(dt);self.c.append(running)
    def summary(self,cutoff):
        j=bisect.bisect_left(self.d,cutoff)-1
        if j<0:return None
        cc=dict(self.c[j]);return {'counts':cc,'n':sum(cc.values()),'last_source_kickoff':self.d[j]}

def load_games(db):
    con=sqlite3.connect(str(db));con.row_factory=sqlite3.Row
    q="SELECT id,fid,h_id,a_id,date,league,season,team_h,team_a,h_goals,a_goals FROM general_game_stats WHERE league IN ('EPL','La liga','Bundesliga','Serie A','Ligue 1') ORDER BY date,id"
    games=[dict(r) for r in con.execute(q)];con.close()
    if not games:raise SeverityError('no frozen games')
    return games

def build_indices(games):
    raw={'home_team':{},'away_team':{},'league':{}};big={}
    def add(bucket,key,item):bucket.setdefault(key,[]).append(item)
    for g in games:
        h=int(g['h_goals']);a=int(g['a_goals'])
        if h<0 or a<0 or h>MAX_SCORE or a>MAX_SCORE:continue
        p=partkey(h,a);item=(str(g['date']),(h,a))
        add(raw['home_team'],(int(g['h_id']),p),item);add(raw['away_team'],(int(g['a_id']),p),item);add(raw['league'],(g['league'],p),item);add(big,p,item)
    return {k:{kk:ScorePrefix(vv) for kk,vv in b.items()} for k,b in raw.items()}|{'big5':{p:ScorePrefix(v) for p,v in big.items()}}

def posterior(series,cutoff,cells,pseudo):
    s=series.summary(cutoff) if series else None
    if not s or s['n']<=0:return None
    if not s['last_source_kickoff']<cutoff:raise SeverityError('chronology violation')
    den=s['n']+pseudo*len(cells);return {'p':{cell:(s['counts'].get(cell,0)+pseudo)/den for cell in cells},'n':s['n'],'last':s['last_source_kickoff']}

def reference_posterior(idx,league,p,cutoff,cells,pseudo):
    z=posterior(idx['league'].get((league,p)),cutoff,cells,pseudo)
    if z is not None:z['level']='league_pool';return z
    z=posterior(idx['big5'].get(p),cutoff,cells,pseudo)
    if z is not None:z['level']='big5_pool';return z
    return None

def source_residual(idx,kind,team_id,league,p,cutoff,cells,pseudo):
    team=posterior(idx[kind].get((int(team_id),p)),cutoff,cells,pseudo)
    if team is None:return {cell:1.0 for cell in cells},{'level':'neutral_no_team_partition','team_n':0,'reference_level':'none'}
    ref=reference_posterior(idx,league,p,cutoff,cells,pseudo)
    if ref is None:return {cell:1.0 for cell in cells},{'level':'neutral_no_reference_partition','team_n':team['n'],'reference_level':'none'}
    return {cell:team['p'][cell]/ref['p'][cell] for cell in cells},{'level':'team_role_residual','team_n':team['n'],'reference_n':ref['n'],'reference_level':ref['level']}

def apply_residual(seed,g,idx,c):
    n=len(seed);parts=partition_cells(n);q=[row[:] for row in seed];cutoff=str(g['date']);pseudo=float(c['severity_state']['pseudocount_per_cell']);diag={'eligible_partition_n':0,'changed_partition_n':0,'neutral_source_n':0,'source_reference_levels':{}}
    for p,cells in parts.items():
        if len(cells)<2:continue
        diag['eligible_partition_n']+=1
        rh,dh=source_residual(idx,'home_team',g['h_id'],g['league'],p,cutoff,cells,pseudo);ra,da=source_residual(idx,'away_team',g['a_id'],g['league'],p,cutoff,cells,pseudo)
        for d in (dh,da):
            diag['source_reference_levels'][d['reference_level']]=diag['source_reference_levels'].get(d['reference_level'],0)+1
            if d['level'].startswith('neutral_'):diag['neutral_source_n']+=1
        mass=sum(seed[h][a] for h,a in cells)
        weights=[]
        for h,a in cells:weights.append(seed[h][a]*math.sqrt(max(1e-300,rh[h,a])*max(1e-300,ra[h,a])))
        sw=sum(weights)
        if mass<=0 or sw<=0 or not math.isfinite(sw):continue
        changed=False
        for (h,a),w in zip(cells,weights):
            nv=mass*w/sw
            if abs(nv-seed[h][a])>1e-15:changed=True
            q[h][a]=nv
        if changed:diag['changed_partition_n']+=1
    return q,diag

def partition_mass_errors(q,seed):
    parts=partition_cells(len(q));pe=se=0.0
    for p,cells in parts.items():
        a=sum(q[h][a] for h,a in cells);b=sum(seed[h][a] for h,a in cells);pe=max(pe,abs(a-b))
        if len(cells)==1:
            h,a0=cells[0];se=max(se,abs(q[h][a0]-seed[h][a0]))
    return pe,se

def build_candidate(rows,bmats,pmap,games,idx,c):
    maps=fixture_maps(games);mm={};seeds={};methods={};iters=[];part_changed=part_eligible=neutral=0;refs={}
    for r in rows:
        fid=r['fixture_id'];b=bmats[fid];g,method=map_row(r,maps);methods[method]=methods.get(method,0)+1
        seed,rec=iproject(b,marginal_total(b),pmap[fid],tol=float(c['canonical_target_matrix']['tolerance']),max_iter=int(c['canonical_target_matrix']['max_iterations']));iters.append(rec['iterations']);q,d=apply_residual(seed,g,idx,c);mm[fid]=q;seeds[fid]=seed
        part_changed+=d['changed_partition_n'];part_eligible+=d['eligible_partition_n'];neutral+=d['neutral_source_n']
        for k,v in d['source_reference_levels'].items():refs[k]=refs.get(k,0)+v
    return mm,seeds,{'mapping_methods':methods,'canonical_iprojection_iterations_min':min(iters),'canonical_iprojection_iterations_mean':sum(iters)/len(iters),'canonical_iprojection_iterations_max':max(iters),'eligible_partition_total':part_eligible,'changed_partition_total':part_changed,'neutral_source_total':neutral,'source_reference_levels':refs}

def structural(rows,mm,seeds,bm,pmap,extra):
    norm=tot=hda=part=single=0.0
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];seed=seeds[fid];b=bm[fid];norm=max(norm,abs(sum(sum(x) for x in q)-1));tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))));hda=max(hda,max(abs(x-y) for x,y in zip(integrate(q),map(float,pmap[fid]))));p,s=partition_mass_errors(q,seed);part=max(part,p);single=max(single,s)
    return {'chronology_violation_n':0,'matrix_sum_max_abs_error':norm,'total_goals_marginal_max_abs_error':tot,'matrix_to_target_1x2_max_abs_error':hda,'partition_mass_max_abs_error':part,'singleton_cell_max_abs_error':single,**extra}

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,seeds,extra=build_candidate(rr,bm,pm,games,idx,c);se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural(rr,mm,seeds,bm,pm,extra);sg=c['hard_gates']['structural']
    checks={'chronology_gate':sd['chronology_violation_n']<=sg['chronology_violation_n_max'],'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_error']<=sg['total_goals_marginal_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=sg['matrix_to_target_1x2_max_abs_error']+TOL,'partition_mass_gate':sd['partition_mass_max_abs_error']<=sg['partition_mass_max_abs_error']+TOL,'singleton_identity_gate':sd['singleton_cell_max_abs_error']<=sg['singleton_cell_max_abs_error']+TOL,'finite_nonnegative_gate':all(math.isfinite(x) and x>=0 for m in mm.values() for row in m for x in row),'canonical_projection_convergence_gate':True};checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or not c['final_stage6_independent_axis']:raise SeverityError('contract drift')
    v324=loadmod('sv_v324',a.v324);v323=loadmod('sv_v323',a.v323);v322=loadmod('sv_v322',a.v322);v32dev=loadmod('sv_v32dev',a.v32dev);a.v32dev_module=v32dev;core=loadmod('sv_core',a.core);v311=loadmod('sv_v311',a.v311);v31=loadmod('sv_v31',a.v31);usr=loadmod('sv_usr',a.usr1);v2=loadmod('sv_v2',a.v2);xg=loadmod('sv_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg);write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec);games=load_games(a.db);idx=build_indices(games)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-margin-severity-final-v1','status':c['terminal']['failure'],'reason':'margin_severity_failed_2019_2020_sanity_stage6_program_blocked','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'stage6_program_blocked':True,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'evaluation_2021_2022.json',ev);ok=ev['all_pass'];final={'schema_version':'football3-v3-score-shape-margin-severity-final-v1','status':c['terminal']['success'] if ok else c['terminal']['failure'],'reason':'all_fixed_stage6_margin_severity_gates_pass' if ok else 'margin_severity_failed_2021_2022_evaluation_stage6_program_blocked','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'stage6_program_blocked':not ok,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True))
if __name__=='__main__':main()
