#!/usr/bin/env python3
from __future__ import annotations
import argparse, importlib.util, json, math, pathlib, sys

TOL=1e-12
class ScoreShapeError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def integrate(m):
    h=d=a=0.0
    for i,row in enumerate(m):
        for j,x in enumerate(row):
            if i>j:h+=float(x)
            elif i==j:d+=float(x)
            else:a+=float(x)
    return [h,d,a]

def marginal_total(m):
    n=len(m); out=[0.0]*(2*n-1)
    for h in range(n):
        for a in range(n):out[h+a]+=float(m[h][a])
    return out

def _slice(base,t):
    n=len(base); asym=[]; diag=[]
    for h in range(n):
        a=t-h
        if 0<=a<n:
            x=float(base[h][a])
            if h==a:diag.append((h,a,x))
            else:asym.append((h,a,x))
    return asym,diag

def _tilted_asym(base,t,lam):
    cells,_=_slice(base,t); mass=sum(x for _,_,x in cells)
    if mass<=0:return [],0.0
    logs=[]
    for h,a,x in cells:
        if x>0:logs.append((h,a,x,math.log(x)+float(lam)*(h-a)/2.0))
    if not logs:return [],mass
    zmax=max(z for *_,z in logs)
    ws=[(h,a,x,math.exp(z-zmax)) for h,a,x,z in logs]; den=sum(w for *_,w in ws)
    if den<=0 or not math.isfinite(den):raise ScoreShapeError('invalid tilt denominator')
    vals=[(h,a,mass*w/den) for h,a,x,w in ws]
    return vals,mass

def tilted_home_mass(base,lam):
    total=0.0
    for t in range(2*len(base)-1):
        vals,_=_tilted_asym(base,t,lam)
        total+=sum(q for h,a,q in vals if h>a)
    return total

def goaldiff_tilt(base,target_hda,draw_tol=5e-12,solver_tol=1e-14,max_iter=256):
    b=[[float(x) for x in row] for row in base]; n=len(b)
    if not n or any(len(r)!=n for r in b):raise ScoreShapeError('matrix must be square')
    if any((not math.isfinite(x)) or x<0 for r in b for x in r):raise ScoreShapeError('invalid baseline matrix')
    bp=integrate(b); t=list(map(float,target_hda))
    if abs(sum(bp)-1.0)>1e-10 or abs(sum(t)-1.0)>1e-10:raise ScoreShapeError('normalization drift')
    if abs(t[1]-bp[1])>draw_tol:raise ScoreShapeError('target draw differs from baseline draw')
    if max(abs(t[i]-bp[i]) for i in range(3))<=solver_tol:return [r[:] for r in b],{'lambda':0.0,'exact_identity':True,'iterations':0}
    lo,hi=-64.0,64.0; flo=tilted_home_mass(b,lo); fhi=tilted_home_mass(b,hi)
    if t[0]<flo-solver_tol or t[0]>fhi+solver_tol:raise ScoreShapeError(('target outside feasible bracket',t[0],flo,fhi))
    it=0
    for it in range(1,max_iter+1):
        mid=(lo+hi)/2.0; fm=tilted_home_mass(b,mid)
        if abs(fm-t[0])<=solver_tol:lo=hi=mid; break
        if fm<t[0]:lo=mid
        else:hi=mid
    lam=(lo+hi)/2.0; q=[[0.0]*n for _ in range(n)]
    for tg in range(2*n-1):
        vals,_=_tilted_asym(b,tg,lam); _,diag=_slice(b,tg)
        for h,a,x in diag:q[h][a]=x
        for h,a,x in vals:q[h][a]=x
    ip=integrate(q)
    if max(abs(ip[i]-t[i]) for i in range(3))>2e-12:raise ScoreShapeError(('target HDA not reached',ip,t,lam))
    if abs(sum(sum(r) for r in q)-1.0)>2e-12:raise ScoreShapeError('transport normalization drift')
    return q,{'lambda':lam,'exact_identity':False,'iterations':it}

def tilt_identity_error(q,b,lam):
    worst=0.0; n=len(b)
    for t in range(2*n-1):
        vals=[]
        for h in range(n):
            a=t-h
            if 0<=a<n and h!=a and b[h][a]>0 and q[h][a]>0:
                vals.append(math.log(q[h][a]/b[h][a])-float(lam)*(h-a)/2.0)
        if vals:worst=max(worst,max(vals)-min(vals))
    return worst

def structural_diagnostics(rows,mm,bm,target_pmap,dg,lambdas):
    diag=tot=tilterr=norm=hda=drawid=nonswitch=0.0
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];b=bm[fid];n=len(b);tp=target_pmap[fid];ip=integrate(q);bp=integrate(b)
        hda=max(hda,max(abs(ip[i]-float(tp[i])) for i in range(3)));drawid=max(drawid,abs(float(tp[1])-bp[1]))
        norm=max(norm,abs(sum(sum(x) for x in q)-1.0));diag=max(diag,max(abs(q[i][i]-b[i][i]) for i in range(n)))
        tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))))
        tilterr=max(tilterr,tilt_identity_error(q,b,lambdas[fid]))
        if not dg[fid].get('v324_executed_switch',False):nonswitch=max(nonswitch,max(abs(q[h][a]-b[h][a]) for h in range(n) for a in range(n)))
    ls=list(lambdas.values())
    return {'draw_diagonal_max_abs_delta':diag,'total_goals_marginal_max_abs_delta':tot,'conditional_tilt_identity_max_abs_error':tilterr,'matrix_sum_max_abs_error':norm,'matrix_to_target_1x2_max_abs_error':hda,'target_draw_identity_max_abs_error':drawid,'non_switch_exact_identity_max_abs_error':nonswitch,'lambda_min':min(ls) if ls else 0.0,'lambda_mean':sum(ls)/len(ls) if ls else 0.0,'lambda_max':max(ls) if ls else 0.0}

def target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324):
    params=c4['direction_generator']['frozen_t2_params'];epsilon=c4['projection']['epsilon'];allr=[];pm={};dg={};stats=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap];te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322);q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322);p,pd,st=v324.project_direction_map(te,bmap,q,d,v322,epsilon)
        stats.append({'season':s,'fit':fit,**st});allr.extend(te);pm.update(p);dg.update(pd)
    return allr,pm,dg,stats

def build_matrices(rows,bmats,pmap,draw_tol):
    mm={};lams={}
    for r in rows:
        fid=r['fixture_id'];q,rec=goaldiff_tilt(bmats[fid],pmap[fid],draw_tol=draw_tol);mm[fid]=q;lams[fid]=rec['lambda']
    return mm,lams

def per_season_exact(rows,mm,bm,v32dev):
    return [{'season':s,'n':len(rs),'exact_score_logloss_delta':v32dev.exact_ll(rs,mm)-v32dev.exact_ll(rs,bm)} for s in sorted({r['season'] for r in rows}) for rs in [[r for r in rows if r['season']==s]]]

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,lams=build_matrices(rr,bm,pm,c['hard_gates']['structural']['target_draw_identity_max_abs_error'])
    se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural_diagnostics(rr,mm,bm,pm,dg,lams);sg=c['hard_gates']['structural']
    checks={'draw_diagonal_gate':sd['draw_diagonal_max_abs_delta']<=sg['draw_diagonal_max_abs_delta']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_delta']<=sg['total_goals_marginal_max_abs_delta']+TOL,'conditional_tilt_identity_gate':sd['conditional_tilt_identity_max_abs_error']<=sg['conditional_tilt_identity_max_abs_error']+TOL,'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'target_draw_identity_gate':sd['target_draw_identity_max_abs_error']<=sg['target_draw_identity_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=c['hard_gates']['score']['matrix_to_1x2_max_abs_error']+TOL,'non_switch_exact_identity_gate':sd['non_switch_exact_identity_max_abs_error']<=TOL}
    checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_SCORE_SHAPE_TARGET_SCORING':raise ScoreShapeError('contract drift')
    if c['frozen_target_hda']['params']!=c4['direction_generator']['frozen_t2_params'] or float(c['frozen_target_hda']['epsilon'])!=float(c4['projection']['epsilon']):raise ScoreShapeError('V3.2.4 target source drift')
    v324=loadmod('gd_v324',a.v324);v323=loadmod('gd_v323',a.v323);v322=loadmod('gd_v322',a.v322);v32dev=loadmod('gd_v32dev',a.v32dev);a.v32dev_module=v32dev
    core=loadmod('gd_core',a.core);v311=loadmod('gd_v311',a.v311);v31=loadmod('gd_v31',a.v31);usr=loadmod('gd_usr',a.usr1);v2=loadmod('gd_v2',a.v2);xg=loadmod('gd_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-goaldiff-final-v1','status':c['terminal']['failure'],'reason':'score_shape_failed_2019_2020_sanity_stage6_transport_program_blocked','stage6_transport_program_blocked':True,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return 0
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev);write_json(a.out/'evaluation_2021_2022.json',ev);ok=ev['all_pass'];status=c['terminal']['success'] if ok else c['terminal']['failure']
    final={'schema_version':'football3-v3-score-shape-goaldiff-final-v1','status':status,'reason':'all_frozen_score_shape_gates_pass' if ok else 'score_shape_failed_2021_2022_stage6_transport_program_blocked','stage6_transport_program_blocked':not ok,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'sanity_2019_2020_all_pass':True,'evaluation_2021_2022_all_pass':ok,'evaluation_2021_2022':ev,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'component_ready_for_macro_integration':ok,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
