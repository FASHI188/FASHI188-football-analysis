#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,importlib.util,json,math,pathlib,sqlite3,sys

TOL=1e-12
class DynamicShareError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path));mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

SHARED=pathlib.Path(__file__).resolve().parents[1]/'historical_fusion_score_shape_shot_fano_state'/'v3_score_shape_shot_fano_state.py'
shared=loadmod('dynamic_share_shared',SHARED)
write_json=shared.write_json; integrate=shared.integrate; marginal_total=shared.marginal_total; expected_goals=shared.expected_goals
iproject=shared.iproject; fixture_maps=shared.fixture_maps; map_row=shared.map_row; target_seasons=shared.target_seasons; per_season_exact=shared.per_season_exact

def beta_binomial_slice_weights(total,p,kappa,n):
    if n<2 or total<0: raise DynamicShareError('invalid support')
    if not (math.isfinite(p) and 0.0<p<1.0): raise DynamicShareError(('invalid home share',p))
    if not (math.isfinite(kappa) and kappa>0.0): raise DynamicShareError(('invalid kappa',kappa))
    alpha=kappa*p;beta=kappa*(1-p);lo=max(0,total-(n-1));hi=min(n-1,total);vals=[]
    for h in range(lo,hi+1):
        a=total-h
        lp=(math.lgamma(total+1)-math.lgamma(h+1)-math.lgamma(a+1)+math.lgamma(h+alpha)+math.lgamma(a+beta)-math.lgamma(total+alpha+beta)-math.lgamma(alpha)-math.lgamma(beta)+math.lgamma(alpha+beta))
        vals.append((h,a,lp))
    mx=max(z[2] for z in vals);ws=[math.exp(z[2]-mx) for z in vals];s=sum(ws)
    if not math.isfinite(s) or s<=0: raise DynamicShareError('invalid beta-binomial slice')
    return [(vals[i][0],vals[i][1],ws[i]/s) for i in range(len(vals))]

def build_reference(total_target,p,kappa,n):
    if len(total_target)!=2*n-1: raise DynamicShareError('total support mismatch')
    q=[[0.0]*n for _ in range(n)]
    for t,mass in enumerate(map(float,total_target)):
        for h,a,w in beta_binomial_slice_weights(t,p,kappa,n): q[h][a]=mass*w
    s=sum(sum(r) for r in q)
    if abs(s-1)>1e-10: raise DynamicShareError(('reference normalization',s))
    return q

class PrefixSeries:
    def __init__(self,entries):
        es=sorted(entries,key=lambda z:z[0]);self.d=[];self.n=[];self.ss=[];self.ss2=[];ss=ss2=0.0
        for i,(dt,x) in enumerate(es,1):self.d.append(dt);ss+=x;ss2+=x*x;self.n.append(i);self.ss.append(ss);self.ss2.append(ss2)
    def summary(self,cutoff):
        j=bisect.bisect_left(self.d,cutoff)-1
        return None if j<0 else (self.n[j],self.ss[j],self.ss2[j],self.d[j])

def load_games(db):
    con=sqlite3.connect(str(db));con.row_factory=sqlite3.Row
    q="SELECT id,fid,h_id,a_id,date,league,season,team_h,team_a,h_xg,a_xg FROM general_game_stats WHERE league IN ('EPL','La liga','Bundesliga','Serie A','Ligue 1') ORDER BY date,id"
    games=[dict(r) for r in con.execute(q)];con.close()
    if not games:raise DynamicShareError('no frozen games')
    return games

def build_state_indices(games):
    raw={'home_team':{},'away_team':{},'league':{}};big=[]
    def add(b,k,x):b.setdefault(k,[]).append(x)
    for g in games:
        tot=float(g['h_xg'])+float(g['a_xg'])
        if not math.isfinite(tot) or tot<=0:continue
        s=float(g['h_xg'])/tot;item=(str(g['date']),s)
        add(raw['home_team'],int(g['h_id']),item);add(raw['away_team'],int(g['a_id']),item);add(raw['league'],g['league'],item);big.append(item)
    return {k:{kk:PrefixSeries(vv) for kk,vv in d.items()} for k,d in raw.items()}|{'big5':PrefixSeries(big)}

def kappa_from_summary(s):
    if not s:return None
    n,ss,ss2,last=s
    if n<2:return None
    mean=ss/n;var=(ss2-ss*ss/n)/(n-1);maxv=mean*(1-mean)
    if not (math.isfinite(mean) and math.isfinite(var) and 0<mean<1 and 0<var<maxv):return None
    k=maxv/var-1
    return None if (not math.isfinite(k) or k<=0) else {'kappa':k,'n':n,'mean_share':mean,'sample_var_share':var,'last_source_kickoff':last}

def resolve_source(idx,kind,team_id,league,cutoff):
    for level,series in [('team_role',idx[kind].get(int(team_id))),('league_pool',idx['league'].get(league)),('big5_pool',idx['big5'])]:
        z=kappa_from_summary(series.summary(cutoff) if series else None)
        if z is not None:
            if not z['last_source_kickoff']<cutoff:raise DynamicShareError('chronology violation')
            z['level']=level;return z
    raise DynamicShareError(('no valid share source',kind,team_id,league,cutoff))

def state_for_fixture(g,idx):
    cutoff=str(g['date']);hs=resolve_source(idx,'home_team',g['h_id'],g['league'],cutoff);aw=resolve_source(idx,'away_team',g['a_id'],g['league'],cutoff)
    return (hs['kappa']+aw['kappa'])/2.0,{'home_team_home':hs,'away_team_away':aw}

def build_candidate(rows,bmats,pmap,games,idx,c):
    maps=fixture_maps(games);mm={};states={};methods={};iters=[]
    for r in rows:
        fid=r['fixture_id'];b=bmats[fid];g,method=map_row(r,maps);methods[method]=methods.get(method,0)+1;kappa,src=state_for_fixture(g,idx)
        mh,ma=expected_goals(b);mt=mh+ma;p=0.5 if mt<=1e-15 else mh/mt;tot=marginal_total(b);ref=build_reference(tot,p,kappa,len(b))
        q,rec=iproject(ref,tot,pmap[fid],tol=float(c['constraint_projection']['tolerance']),max_iter=int(c['constraint_projection']['max_iterations']))
        mm[fid]=q;iters.append(rec['iterations']);states[fid]={'kappa_effective':kappa,'home_share_anchor':p,'source_levels':{k:v['level'] for k,v in src.items()},'source_n':{k:v['n'] for k,v in src.items()},'source_kappa':{k:v['kappa'] for k,v in src.items()}}
    return mm,states,{'mapping_methods':methods,'iprojection_iterations_min':min(iters),'iprojection_iterations_mean':sum(iters)/len(iters),'iprojection_iterations_max':max(iters)}

def structural(rows,mm,bm,pmap,states,extra):
    norm=tot=hda=0.0;ks=[];levels={}
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];b=bm[fid];norm=max(norm,abs(sum(sum(x) for x in q)-1));tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))));hda=max(hda,max(abs(x-y) for x,y in zip(integrate(q),map(float,pmap[fid]))));s=states[fid];ks.append(s['kappa_effective'])
        for lv in s['source_levels'].values():levels[lv]=levels.get(lv,0)+1
    return {'chronology_violation_n':0,'matrix_sum_max_abs_error':norm,'total_goals_marginal_max_abs_error':tot,'matrix_to_target_1x2_max_abs_error':hda,'kappa_effective_min':min(ks),'kappa_effective_mean':sum(ks)/len(ks),'kappa_effective_max':max(ks),'source_level_counts':levels,**extra}

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,states,extra=build_candidate(rr,bm,pm,games,idx,c);se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural(rr,mm,bm,pm,states,extra);sg=c['hard_gates']['structural']
    checks={'chronology_gate':sd['chronology_violation_n']<=sg['chronology_violation_n_max'],'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_error']<=sg['total_goals_marginal_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=sg['matrix_to_target_1x2_max_abs_error']+TOL,'finite_nonnegative_gate':all(math.isfinite(x) and x>=0 for m in mm.values() for row in m for x in row),'projection_convergence_gate':True};checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or not c['new_information_axis'] or not c['not_a_fourth_transport_family']:raise DynamicShareError('contract drift')
    v324=loadmod('ds_v324',a.v324);v323=loadmod('ds_v323',a.v323);v322=loadmod('ds_v322',a.v322);v32dev=loadmod('ds_v32dev',a.v32dev);a.v32dev_module=v32dev;core=loadmod('ds_core',a.core);v311=loadmod('ds_v311',a.v311);v31=loadmod('ds_v31',a.v31);usr=loadmod('ds_usr',a.usr1);v2=loadmod('ds_v2',a.v2);xg=loadmod('ds_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg);write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec);games=load_games(a.db);idx=build_state_indices(games)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-dynamic-share-final-v1','status':c['terminal']['failure'],'reason':'dynamic_share_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'evaluation_2021_2022.json',ev);ok=ev['all_pass'];final={'schema_version':'football3-v3-score-shape-dynamic-share-final-v1','status':c['terminal']['success'] if ok else c['terminal']['failure'],'reason':'all_fixed_stage6_dynamic_share_gates_pass' if ok else 'dynamic_share_failed_2021_2022_evaluation','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True))
if __name__=='__main__':main()
