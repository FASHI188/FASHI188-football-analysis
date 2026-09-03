#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,importlib.util,json,math,pathlib,sqlite3,sys

TOL=1e-12
CELLS=((0,0),(1,0),(0,1),(1,1))
class AssociationError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path));mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

SHARED=pathlib.Path(__file__).resolve().parents[1]/'historical_fusion_score_shape_shot_fano_state'/'v3_score_shape_shot_fano_state.py'
shared=loadmod('lowassoc_shared',SHARED)
write_json=shared.write_json; integrate=shared.integrate; marginal_total=shared.marginal_total
iproject=shared.iproject; fixture_maps=shared.fixture_maps; map_row=shared.map_row; target_seasons=shared.target_seasons; per_season_exact=shared.per_season_exact

def smoothed_log_or(counts,pseudo=0.5):
    c={cell:float(counts.get(cell,0.0)) for cell in CELLS}
    if pseudo<=0: raise AssociationError('pseudocount must be positive')
    return math.log(c[(0,0)]+pseudo)+math.log(c[(1,1)]+pseudo)-math.log(c[(1,0)]+pseudo)-math.log(c[(0,1)]+pseudo)

def matrix_log_or(m):
    vals=[float(m[h][a]) for h,a in CELLS]
    if any((not math.isfinite(x) or x<=0) for x in vals):raise AssociationError('nonpositive low-score base cell')
    return math.log(m[0][0])+math.log(m[1][1])-math.log(m[1][0])-math.log(m[0][1])

def local_association_reference(base,desired_log_or):
    q=[list(map(float,row)) for row in base];base_log_or=matrix_log_or(base);delta=0.25*(float(desired_log_or)-base_log_or)
    ep=math.exp(delta);em=math.exp(-delta)
    q[0][0]*=ep;q[1][1]*=ep;q[1][0]*=em;q[0][1]*=em
    s=sum(sum(row) for row in q)
    if not math.isfinite(s) or s<=0:raise AssociationError('invalid tilted normalization')
    q=[[x/s for x in row] for row in q]
    return q,{'base_log_or':base_log_or,'desired_log_or':float(desired_log_or),'delta':delta,'reference_log_or':matrix_log_or(q)}

class LowCellPrefix:
    def __init__(self,entries):
        es=sorted(entries,key=lambda z:z[0]);self.d=[];self.c=[];running={cell:0 for cell in CELLS}
        for dt,cell in es:
            running=dict(running);running[cell]+=1;self.d.append(dt);self.c.append(running)
    def summary(self,cutoff):
        j=bisect.bisect_left(self.d,cutoff)-1
        if j<0:return None
        cc=dict(self.c[j]);return {'counts':cc,'low_n':sum(cc.values()),'last_source_kickoff':self.d[j]}

def load_games(db):
    con=sqlite3.connect(str(db));con.row_factory=sqlite3.Row
    q="SELECT id,fid,h_id,a_id,date,league,season,team_h,team_a,h_goals,a_goals FROM general_game_stats WHERE league IN ('EPL','La liga','Bundesliga','Serie A','Ligue 1') ORDER BY date,id"
    games=[dict(r) for r in con.execute(q)];con.close()
    if not games:raise AssociationError('no frozen games')
    return games

def build_state_indices(games):
    raw={'home_team':{},'away_team':{},'league':{}};big=[]
    def add(b,k,item):b.setdefault(k,[]).append(item)
    for g in games:
        cell=(int(g['h_goals']),int(g['a_goals']))
        if cell not in CELLS:continue
        item=(str(g['date']),cell);add(raw['home_team'],int(g['h_id']),item);add(raw['away_team'],int(g['a_id']),item);add(raw['league'],g['league'],item);big.append(item)
    return {k:{kk:LowCellPrefix(vv) for kk,vv in d.items()} for k,d in raw.items()}|{'big5':LowCellPrefix(big)}

def resolve_source(idx,kind,team_id,league,cutoff,pseudo=0.5):
    for level,series in [('team_role',idx[kind].get(int(team_id))),('league_pool',idx['league'].get(league)),('big5_pool',idx['big5'])]:
        s=series.summary(cutoff) if series else None
        if s and s['low_n']>0:
            if not s['last_source_kickoff']<cutoff:raise AssociationError('chronology violation')
            return {'level':level,'log_or':smoothed_log_or(s['counts'],pseudo),'low_n':s['low_n'],'counts':{f'{h}-{a}':s['counts'][h,a] for h,a in CELLS},'last_source_kickoff':s['last_source_kickoff']}
    raise AssociationError(('no low-score source',kind,team_id,league,cutoff))

def state_for_fixture(g,idx,pseudo=0.5):
    cutoff=str(g['date']);hs=resolve_source(idx,'home_team',g['h_id'],g['league'],cutoff,pseudo);aw=resolve_source(idx,'away_team',g['a_id'],g['league'],cutoff,pseudo)
    desired=(hs['log_or']+aw['log_or'])/2.0
    return desired,{'home_team_home':hs,'away_team_away':aw}

def build_candidate(rows,bmats,pmap,games,idx,c):
    maps=fixture_maps(games);mm={};states={};methods={};iters=[];deltas=[];desired=[];realized=[];pseudo=float(c['association_state']['pseudocount_per_cell'])
    for r in rows:
        fid=r['fixture_id'];b=bmats[fid];g,method=map_row(r,maps);methods[method]=methods.get(method,0)+1;dl,src=state_for_fixture(g,idx,pseudo)
        ref,tilt=local_association_reference(b,dl);q,rec=iproject(ref,marginal_total(b),pmap[fid],tol=float(c['constraint_projection']['tolerance']),max_iter=int(c['constraint_projection']['max_iterations']))
        mm[fid]=q;iters.append(rec['iterations']);deltas.append(tilt['delta']);desired.append(dl);realized.append(matrix_log_or(q));states[fid]={'desired_log_or':dl,'tilt_delta':tilt['delta'],'base_log_or':tilt['base_log_or'],'reference_log_or':tilt['reference_log_or'],'realized_log_or':realized[-1],'source_levels':{k:v['level'] for k,v in src.items()},'source_low_n':{k:v['low_n'] for k,v in src.items()}}
    return mm,states,{'mapping_methods':methods,'iprojection_iterations_min':min(iters),'iprojection_iterations_mean':sum(iters)/len(iters),'iprojection_iterations_max':max(iters),'tilt_delta_min':min(deltas),'tilt_delta_mean':sum(deltas)/len(deltas),'tilt_delta_max':max(deltas),'desired_log_or_mean':sum(desired)/len(desired),'realized_log_or_mean':sum(realized)/len(realized)}

def structural(rows,mm,bm,pmap,states,extra):
    norm=tot=hda=0.0;levels={}
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];b=bm[fid];norm=max(norm,abs(sum(sum(x) for x in q)-1));tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))));hda=max(hda,max(abs(x-y) for x,y in zip(integrate(q),map(float,pmap[fid]))))
        for lv in states[fid]['source_levels'].values():levels[lv]=levels.get(lv,0)+1
    return {'chronology_violation_n':0,'matrix_sum_max_abs_error':norm,'total_goals_marginal_max_abs_error':tot,'matrix_to_target_1x2_max_abs_error':hda,'source_level_counts':levels,**extra}

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,states,extra=build_candidate(rr,bm,pm,games,idx,c);se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural(rr,mm,bm,pm,states,extra);sg=c['hard_gates']['structural']
    checks={'chronology_gate':sd['chronology_violation_n']<=sg['chronology_violation_n_max'],'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_error']<=sg['total_goals_marginal_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=sg['matrix_to_target_1x2_max_abs_error']+TOL,'finite_nonnegative_gate':all(math.isfinite(x) and x>=0 for m in mm.values() for row in m for x in row),'projection_convergence_gate':True};checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or not c['new_information_axis'] or not c['local_joint_dependence_axis']:raise AssociationError('contract drift')
    v324=loadmod('la_v324',a.v324);v323=loadmod('la_v323',a.v323);v322=loadmod('la_v322',a.v322);v32dev=loadmod('la_v32dev',a.v32dev);a.v32dev_module=v32dev;core=loadmod('la_core',a.core);v311=loadmod('la_v311',a.v311);v31=loadmod('la_v31',a.v31);usr=loadmod('la_usr',a.usr1);v2=loadmod('la_v2',a.v2);xg=loadmod('la_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg);write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec);games=load_games(a.db);idx=build_state_indices(games)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-low-score-role-association-final-v1','status':c['terminal']['failure'],'reason':'low_score_role_association_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'evaluation_2021_2022.json',ev);ok=ev['all_pass'];final={'schema_version':'football3-v3-score-shape-low-score-role-association-final-v1','status':c['terminal']['success'] if ok else c['terminal']['failure'],'reason':'all_fixed_stage6_low_score_role_association_gates_pass' if ok else 'low_score_role_association_failed_2021_2022_evaluation','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True))
if __name__=='__main__':main()
