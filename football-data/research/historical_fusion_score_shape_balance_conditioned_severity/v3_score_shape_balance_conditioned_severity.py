#!/usr/bin/env python3
from __future__ import annotations
import argparse,importlib.util,json,math,pathlib,sqlite3,sys

TOL=1e-12
class BalanceSeverityError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path));mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

SHARED=pathlib.Path(__file__).resolve().parents[1]/'historical_fusion_score_shape_shot_fano_state'/'v3_score_shape_shot_fano_state.py'
shared=loadmod('balance_severity_shared',SHARED)
write_json=shared.write_json;integrate=shared.integrate;marginal_total=shared.marginal_total
iproject=shared.iproject;fixture_maps=shared.fixture_maps;map_row=shared.map_row;target_seasons=shared.target_seasons;per_season_exact=shared.per_season_exact

def outcome(h,a): return 'H' if h>a else ('A' if h<a else 'D')
def partkey(h,a): return (h+a,outcome(h,a))

def partition_cells(n):
    d={}
    for h in range(n):
        for a in range(n): d.setdefault(partkey(h,a),[]).append((h,a))
    return d

def normalized_severity(h,a):
    o=outcome(h,a);t=h+a
    if o=='D' or t<=0: return 0.0
    m=abs(h-a);mmin=1 if t%2 else 2
    den=t-mmin
    if den<=0: return 0.0
    z=(m-mmin)/den
    return min(1.0,max(0.0,float(z)))

def strength_signal(matrix,o,floor=1e-15):
    h,d,a=map(float,integrate(matrix));h=max(floor,h);a=max(floor,a)
    if o=='H': return math.log(h/a)
    if o=='A': return math.log(a/h)
    return 0.0

def logsumexp(vals):
    m=max(vals);return m+math.log(sum(math.exp(v-m) for v in vals))

def conditional_stats(base_matrix,cells,x,gamma):
    vals=[];sevs=[]
    for h,a in cells:
        p=float(base_matrix[h][a]);s=normalized_severity(h,a)
        if p>0 and math.isfinite(p): vals.append(math.log(p)+gamma*x*s);sevs.append(s)
    if not vals: raise BalanceSeverityError('empty conditional partition')
    lz=logsumexp(vals);ws=[math.exp(v-lz) for v in vals]
    mean=sum(w*s for w,s in zip(ws,sevs));var=sum(w*(s-mean)**2 for w,s in zip(ws,sevs))
    return lz,mean,var

def load_games(db):
    con=sqlite3.connect(str(db));con.row_factory=sqlite3.Row
    q="SELECT id,fid,h_id,a_id,date,league,season,team_h,team_a,h_goals,a_goals FROM general_game_stats WHERE league IN ('EPL','La liga','Bundesliga','Serie A','Ligue 1') ORDER BY date,id"
    games=[dict(r) for r in con.execute(q)];con.close()
    if not games: raise BalanceSeverityError('no frozen games')
    return games

def season_int(v):
    s=str(v).strip()
    try:return int(float(s))
    except Exception:
        for token in s.replace('/','-').split('-'):
            if token.isdigit() and len(token)==4:return int(token)
    raise BalanceSeverityError(('unparseable season',v))

def development_observations(rows,bmats,games,seasons):
    maps=fixture_maps(games);parts_cache={};obs=[];methods={};seen_by_season={}
    allowed=set(map(int,seasons))
    for r in rows:
        fid=r['fixture_id']
        if fid not in bmats: continue
        try:g,method=map_row(r,maps)
        except Exception:continue
        sy=season_int(g['season'])
        if sy not in allowed:continue
        h=int(g['h_goals']);a=int(g['a_goals']);o=outcome(h,a)
        if o=='D':continue
        b=bmats[fid];n=len(b)
        if h<0 or a<0 or h>=n or a>=n:continue
        parts=parts_cache.setdefault(n,partition_cells(n));cells=parts.get(partkey(h,a),[])
        if len(cells)<2:continue
        mass=sum(float(b[x][y]) for x,y in cells)
        if mass<=0 or float(b[h][a])<=0:continue
        x=strength_signal(b,o);sobs=normalized_severity(h,a)
        obs.append({'x':x,'sobs':sobs,'cells':cells,'matrix':b,'observed':(h,a),'fixture_id':fid,'season':sy})
        methods[method]=methods.get(method,0)+1;seen_by_season[sy]=seen_by_season.get(sy,0)+1
    if not obs:raise BalanceSeverityError('no development observations')
    return obs,{'n':len(obs),'mapping_methods':methods,'n_by_season':seen_by_season}

def gamma_score(obs,gamma):
    d1=d2=ll=0.0
    for z in obs:
        x=float(z['x']);b=z['matrix'];cells=z['cells'];h,a=z['observed'];sobs=float(z['sobs'])
        lz,mean,var=conditional_stats(b,cells,x,gamma)
        ll += math.log(float(b[h][a])) + gamma*x*sobs - lz
        d1 += x*(sobs-mean);d2 -= x*x*var
    return ll,d1,d2

def fit_gamma(obs,tol=1e-12):
    ll0,g0,_=gamma_score(obs,0.0)
    if not all(map(math.isfinite,(ll0,g0))):raise BalanceSeverityError('nonfinite gamma score at zero')
    if abs(g0)<=tol:return {'gamma':0.0,'score_at_solution':g0,'loglik_at_gamma0':ll0,'loglik_at_fit':ll0,'bracket':[0.0,0.0],'iterations':0}
    if g0>0:
        lo=0.0;hi=1.0
        while gamma_score(obs,hi)[1]>0 and hi<128.0:hi*=2.0
        if gamma_score(obs,hi)[1]>0:raise BalanceSeverityError('gamma root not bracketed on positive side')
    else:
        hi=0.0;lo=-1.0
        while gamma_score(obs,lo)[1]<0 and abs(lo)<128.0:lo*=2.0
        if gamma_score(obs,lo)[1]<0:raise BalanceSeverityError('gamma root not bracketed on negative side')
    it=0
    for it in range(1,129):
        mid=(lo+hi)/2.0;gm=gamma_score(obs,mid)[1]
        if abs(gm)<=tol:lo=hi=mid;break
        if gm>0:lo=mid
        else:hi=mid
    gamma=(lo+hi)/2.0;ll,g,_=gamma_score(obs,gamma)
    if not all(map(math.isfinite,(gamma,ll,g))):raise BalanceSeverityError('nonfinite gamma fit')
    return {'gamma':gamma,'score_at_solution':g,'loglik_at_gamma0':ll0,'loglik_at_fit':ll,'loglik_gain':ll-ll0,'bracket':[lo,hi],'iterations':it}

def apply_severity(seed,baseline,gamma):
    n=len(seed);parts=partition_cells(n);q=[row[:] for row in seed];changed=0;eligible=0;signals=[]
    for (t,o),cells in parts.items():
        if o=='D' or len(cells)<2:continue
        eligible+=1;x=strength_signal(baseline,o);signals.append(x);mass=sum(float(seed[h][a]) for h,a in cells)
        logs=[]
        for h,a in cells:
            p=float(seed[h][a]);logs.append((-math.inf if p<=0 else math.log(p)+gamma*x*normalized_severity(h,a)))
        finite=[v for v in logs if math.isfinite(v)]
        if mass<=0 or not finite:continue
        mx=max(finite);weights=[0.0 if not math.isfinite(v) else math.exp(v-mx) for v in logs];sw=sum(weights)
        if sw<=0 or not math.isfinite(sw):raise BalanceSeverityError('invalid severity partition weights')
        ch=False
        for (h,a),w in zip(cells,weights):
            nv=mass*w/sw
            if abs(nv-seed[h][a])>1e-15:ch=True
            q[h][a]=nv
        if ch:changed+=1
    return q,{'eligible_partition_n':eligible,'changed_partition_n':changed,'signal_min':min(signals) if signals else 0.0,'signal_mean':sum(signals)/len(signals) if signals else 0.0,'signal_max':max(signals) if signals else 0.0}

def partition_errors(q,seed):
    parts=partition_cells(len(q));pe=se=de=0.0
    for (t,o),cells in parts.items():
        a=sum(q[h][a] for h,a in cells);b=sum(seed[h][a] for h,a in cells);pe=max(pe,abs(a-b))
        if len(cells)==1:
            h,a0=cells[0];se=max(se,abs(q[h][a0]-seed[h][a0]))
        if o=='D':
            for h,a0 in cells:de=max(de,abs(q[h][a0]-seed[h][a0]))
    return pe,se,de

def build_candidate(rows,bmats,pmap,gamma):
    mm={};seeds={};iters=[];eligible=changed=0;signals=[]
    for r in rows:
        fid=r['fixture_id'];b=bmats[fid];seed,rec=iproject(b,marginal_total(b),pmap[fid],tol=1e-13,max_iter=10000);q,d=apply_severity(seed,b,gamma)
        mm[fid]=q;seeds[fid]=seed;iters.append(rec['iterations']);eligible+=d['eligible_partition_n'];changed+=d['changed_partition_n'];signals.extend([d['signal_min'],d['signal_mean'],d['signal_max']])
    return mm,seeds,{'canonical_iprojection_iterations_min':min(iters),'canonical_iprojection_iterations_mean':sum(iters)/len(iters),'canonical_iprojection_iterations_max':max(iters),'eligible_partition_total':eligible,'changed_partition_total':changed,'signal_summary_min':min(signals),'signal_summary_mean':sum(signals)/len(signals),'signal_summary_max':max(signals)}

def structural(rows,mm,seeds,bm,pmap,extra):
    norm=tot=hda=part=single=draw=0.0
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];seed=seeds[fid];b=bm[fid]
        norm=max(norm,abs(sum(sum(x) for x in q)-1));tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))));hda=max(hda,max(abs(x-y) for x,y in zip(integrate(q),map(float,pmap[fid]))));p,s,d=partition_errors(q,seed);part=max(part,p);single=max(single,s);draw=max(draw,d)
    return {'matrix_sum_max_abs_error':norm,'total_goals_marginal_max_abs_error':tot,'matrix_to_target_1x2_max_abs_error':hda,'partition_mass_max_abs_error':part,'singleton_cell_max_abs_error':single,'draw_partition_max_abs_error':draw,**extra}

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,gamma):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,seeds,extra=build_candidate(rr,bm,pm,gamma);se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural(rr,mm,seeds,bm,pm,extra);sg=c['hard_gates']['structural']
    checks={'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_error']<=sg['total_goals_marginal_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=sg['matrix_to_target_1x2_max_abs_error']+TOL,'partition_mass_gate':sd['partition_mass_max_abs_error']<=sg['partition_mass_max_abs_error']+TOL,'singleton_identity_gate':sd['singleton_cell_max_abs_error']<=sg['singleton_cell_max_abs_error']+TOL,'draw_identity_gate':sd['draw_partition_max_abs_error']<=sg['draw_partition_max_abs_error']+TOL,'finite_nonnegative_gate':all(math.isfinite(x) and x>=0 for m in mm.values() for row in m for x in row),'canonical_projection_convergence_gate':True};checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or not c['final_stage6_independent_axis']:raise BalanceSeverityError('contract drift')
    v324=loadmod('bs_v324',a.v324);v323=loadmod('bs_v323',a.v323);v322=loadmod('bs_v322',a.v322);v32dev=loadmod('bs_v32dev',a.v32dev);a.v32dev_module=v32dev;core=loadmod('bs_core',a.core);v311=loadmod('bs_v311',a.v311);v31=loadmod('bs_v31',a.v31);usr=loadmod('bs_usr',a.usr1);v2=loadmod('bs_v2',a.v2);xg=loadmod('bs_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg);write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec)
    games=load_games(a.db);obs,devdiag=development_observations(rows,bmats,games,c['development_only_gamma_fit']['development_seasons']);fit=fit_gamma(obs);fit.update(devdiag);fit['development_seasons']=c['development_only_gamma_fit']['development_seasons'];write_json(a.out/'development_gamma_fit.json',fit);gamma=float(fit['gamma'])
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,gamma);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-balance-conditioned-severity-final-v1','status':c['terminal']['failure'],'reason':'balance_conditioned_severity_failed_2019_2020_sanity_stage6_program_blocked','gamma':gamma,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'stage6_program_blocked':True,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,gamma);write_json(a.out/'evaluation_2021_2022.json',ev);ok=ev['all_pass'];final={'schema_version':'football3-v3-score-shape-balance-conditioned-severity-final-v1','status':c['terminal']['success'] if ok else c['terminal']['failure'],'reason':'all_fixed_stage6_balance_conditioned_severity_gates_pass' if ok else 'balance_conditioned_severity_failed_2021_2022_evaluation_stage6_program_blocked','gamma':gamma,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'stage6_program_blocked':not ok,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True))
if __name__=='__main__':main()
