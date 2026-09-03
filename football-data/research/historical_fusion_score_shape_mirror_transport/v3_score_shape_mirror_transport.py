#!/usr/bin/env python3
from __future__ import annotations
import argparse, importlib.util, json, math, pathlib, sys

EPS=1e-300
TOL=1e-12

class ScoreShapeError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def integrate(m):
    h=d=a=0.0
    for i,row in enumerate(m):
        for j,x in enumerate(row):
            if i>j: h+=float(x)
            elif i==j: d+=float(x)
            else: a+=float(x)
    return [h,d,a]

def sigmoid(z):
    if z>=0:
        e=math.exp(-z); return 1.0/(1.0+e)
    e=math.exp(z); return e/(1.0+e)

def pair_home_mass(base,lam):
    out=0.0
    n=len(base)
    for h in range(n):
        for a in range(h):
            x=float(base[h][a]); y=float(base[a][h]); s=x+y
            if s<=0: continue
            if x<=0: qh=0.0
            elif y<=0: qh=s
            else: qh=s*sigmoid(math.log(x/y)+lam)
            out+=qh
    return out

def mirror_transport(base,target_hda,draw_tol=5e-12,solver_tol=1e-14,max_iter=256):
    b=[[float(x) for x in row] for row in base]
    n=len(b)
    if not n or any(len(row)!=n for row in b): raise ScoreShapeError('matrix must be square')
    if any((not math.isfinite(x)) or x<0 for row in b for x in row): raise ScoreShapeError('invalid baseline matrix')
    bp=integrate(b); t=list(map(float,target_hda))
    if abs(sum(bp)-1.0)>1e-10 or abs(sum(t)-1.0)>1e-10: raise ScoreShapeError('probability normalization drift')
    if abs(t[1]-bp[1])>draw_tol: raise ScoreShapeError('target draw differs from frozen baseline draw')
    if max(abs(t[i]-bp[i]) for i in range(3))<=solver_tol:
        return [row[:] for row in b], {'lambda':0.0,'exact_identity':True,'iterations':0}
    asym=1.0-bp[1]
    if t[0] < -solver_tol or t[0] > asym+solver_tol: raise ScoreShapeError('target home mass infeasible under fixed diagonal')
    lo,hi=-64.0,64.0
    flo=pair_home_mass(b,lo); fhi=pair_home_mass(b,hi)
    if t[0] < flo-solver_tol or t[0] > fhi+solver_tol:
        raise ScoreShapeError('target outside deterministic lambda bracket')
    it=0
    for it in range(1,max_iter+1):
        mid=(lo+hi)/2.0; fm=pair_home_mass(b,mid)
        if abs(fm-t[0])<=solver_tol:
            lo=hi=mid; break
        if fm<t[0]: lo=mid
        else: hi=mid
    lam=(lo+hi)/2.0
    q=[[0.0]*n for _ in range(n)]
    for h in range(n):
        q[h][h]=b[h][h]
        for a in range(h):
            x=b[h][a]; y=b[a][h]; s=x+y
            if s<=0: qh=qa=0.0
            elif x<=0: qh=0.0; qa=s
            elif y<=0: qh=s; qa=0.0
            else:
                qh=s*sigmoid(math.log(x/y)+lam); qa=s-qh
            q[h][a]=qh; q[a][h]=qa
    ip=integrate(q)
    if max(abs(ip[i]-t[i]) for i in range(3))>2e-12:
        raise ScoreShapeError(('target HDA not reached',ip,t,lam))
    if abs(sum(sum(row) for row in q)-1.0)>2e-12: raise ScoreShapeError('transport normalization drift')
    return q, {'lambda':lam,'exact_identity':False,'iterations':it}

def marginal_total(m):
    n=len(m); out=[0.0]*(2*n-1)
    for h in range(n):
        for a in range(n): out[h+a]+=float(m[h][a])
    return out

def marginal_absdiff(m):
    n=len(m); out=[0.0]*n
    for h in range(n):
        for a in range(n): out[abs(h-a)]+=float(m[h][a])
    return out

def structural_diagnostics(rows,mm,bm,target_pmap,dg,lambdas):
    diag=pair=tot=ad=norm=hda=drawid=nonswitch=0.0
    for r in rows:
        fid=r['fixture_id']; q=mm[fid]; b=bm[fid]; n=len(b)
        ip=integrate(q); bp=integrate(b); tp=target_pmap[fid]
        hda=max(hda,max(abs(ip[i]-float(tp[i])) for i in range(3)))
        drawid=max(drawid,abs(float(tp[1])-bp[1]))
        norm=max(norm,abs(sum(sum(row) for row in q)-1.0))
        for h in range(n):
            diag=max(diag,abs(q[h][h]-b[h][h]))
            for a in range(h):
                pair=max(pair,abs((q[h][a]+q[a][h])-(b[h][a]+b[a][h])))
        tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))))
        ad=max(ad,max(abs(x-y) for x,y in zip(marginal_absdiff(q),marginal_absdiff(b))))
        if not dg[fid].get('v324_executed_switch',False):
            nonswitch=max(nonswitch,max(abs(q[h][a]-b[h][a]) for h in range(n) for a in range(n)))
    ls=list(lambdas.values())
    return {
      'draw_diagonal_max_abs_delta':diag,
      'mirror_pair_sum_max_abs_delta':pair,
      'total_goals_marginal_max_abs_delta':tot,
      'absolute_goal_difference_marginal_max_abs_delta':ad,
      'matrix_sum_max_abs_error':norm,
      'matrix_to_target_1x2_max_abs_error':hda,
      'target_draw_identity_max_abs_error':drawid,
      'non_switch_exact_identity_max_abs_error':nonswitch,
      'lambda_min':min(ls) if ls else 0.0,
      'lambda_mean':sum(ls)/len(ls) if ls else 0.0,
      'lambda_max':max(ls) if ls else 0.0,
    }

def target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324):
    params=c4['direction_generator']['frozen_t2_params']; epsilon=c4['projection']['epsilon']
    allr=[]; pm={}; dg={}; stats=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]
        te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322)
        q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322)
        p,pd,st=v324.project_direction_map(te,bmap,q,d,v322,epsilon)
        stats.append({'season':s,'fit':fit,**st}); allr.extend(te); pm.update(p); dg.update(pd)
    return allr,pm,dg,stats

def build_matrices(rows,bmats,pmap,draw_tol):
    mm={}; lambdas={}
    for r in rows:
        fid=r['fixture_id']
        q,rec=mirror_transport(bmats[fid],pmap[fid],draw_tol=draw_tol)
        mm[fid]=q; lambdas[fid]=rec['lambda']
    return mm,lambdas

def per_season_exact(rows,mm,bm,v32dev):
    out=[]
    for s in sorted({r['season'] for r in rows}):
        rs=[r for r in rows if r['season']==s]
        out.append({'season':s,'n':len(rs),'exact_score_logloss_delta':v32dev.exact_ll(rs,mm)-v32dev.exact_ll(rs,bm)})
    return out

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,contract,v311,v322,v323,v324,v32dev):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324)
    bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr}
    mm,lams=build_matrices(rr,bm,pm,contract['hard_gates']['structural']['target_draw_identity_max_abs_error'])
    se=v32dev.score_eval(rr,mm,bm,core,contract,pm)
    sd=structural_diagnostics(rr,mm,bm,pm,dg,lams)
    sg=contract['hard_gates']['structural']
    checks={
      'draw_diagonal_gate':sd['draw_diagonal_max_abs_delta']<=sg['draw_diagonal_max_abs_delta']+TOL,
      'mirror_pair_sum_gate':sd['mirror_pair_sum_max_abs_delta']<=sg['mirror_pair_sum_max_abs_delta']+TOL,
      'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_delta']<=sg['total_goals_marginal_max_abs_delta']+TOL,
      'absolute_goal_difference_marginal_gate':sd['absolute_goal_difference_marginal_max_abs_delta']<=sg['absolute_goal_difference_marginal_max_abs_delta']+TOL,
      'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,
      'target_draw_identity_gate':sd['target_draw_identity_max_abs_error']<=sg['target_draw_identity_max_abs_error']+TOL,
      'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=contract['hard_gates']['score']['matrix_to_1x2_max_abs_error']+TOL,
      'non_switch_exact_identity_gate':sd['non_switch_exact_identity_max_abs_error']<=TOL,
    }
    checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {
      'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,
      'executed_switch_n':sum(x['executed_switch_n'] for x in stats),
      'score':se,'structural':sd,'structural_checks':checks,
      'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),
      'all_pass':checks['all_pass']
    }

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); c4=json.loads(a.v324_contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_SCORE_SHAPE_TARGET_SCORING': raise ScoreShapeError('contract drift')
    if c['frozen_target_hda']['params']!=c4['direction_generator']['frozen_t2_params'] or float(c['frozen_target_hda']['epsilon'])!=float(c4['projection']['epsilon']):
        raise ScoreShapeError('V3.2.4 target source drift')
    v324=loadmod('shape_v324',a.v324); v323=loadmod('shape_v323',a.v323); v322=loadmod('shape_v322',a.v322)
    a.v32dev_module=loadmod('shape_v32dev_stage',a.v32dev); v32dev=a.v32dev_module
    core=loadmod('shape_core',a.core); v311=loadmod('shape_v311',a.v311); v31=loadmod('shape_v31',a.v31); usr=loadmod('shape_usr',a.usr1); v2=loadmod('shape_v2',a.v2); xg=loadmod('shape_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec)
    write_json(a.out/'pre2023_process_receipt.json',procrec)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v311,v322,v323,v324,v32dev)
    write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-final-v1','status':c['terminal']['failure'],'reason':'score_shape_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v311,v322,v323,v324,v32dev)
    write_json(a.out/'evaluation_2021_2022.json',ev)
    status=c['terminal']['success'] if ev['all_pass'] else c['terminal']['failure']
    final={'schema_version':'football3-v3-score-shape-final-v1','status':status,'reason':'all_frozen_score_shape_gates_pass' if ev['all_pass'] else 'score_shape_failed_2021_2022_frozen_gates','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'sanity_2019_2020_all_pass':sanity['all_pass'],'evaluation_2021_2022_all_pass':ev['all_pass'],'evaluation_2021_2022':ev,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,'macro_stage_7_started':False,'component_ready_for_macro_integration':bool(ev['all_pass'])}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
