#!/usr/bin/env python3
import argparse, importlib.util, itertools, json, math, pathlib, sys

EPS=1e-15
TOL=1e-12

class Stage4KLError(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def top1(p):
    return max(range(3),key=lambda i:(float(p[i]),-i))

def reverse_kl(base,p):
    return sum(float(base[i])*math.log(float(base[i])/float(p[i])) for i in range(3))

def minimum_reverse_kl_boundary_projection(base,target,weak,epsilon=1e-9):
    b=list(map(float,base)); bt=top1(b); target=int(target); weak=int(weak); epsilon=float(epsilon)
    if target==bt:
        return b,{'executed':False,'reason':'same_argmax','target':target,'baseline_top1':bt,'total_variation':0.0,'reverse_kl':0.0,'weak_floor_binding':False}
    competitors=[i for i in range(3) if i!=target]
    candidates=[]
    for k in (1,2):
        for bind in itertools.combinations(competitors,k):
            if bt not in bind:
                continue
            group=(target,)+tuple(bind)
            free=[i for i in range(3) if i not in group]
            if not free:
                c=(1.0-epsilon)/(k+1)
                p=[None]*3; p[target]=c+epsilon
                for j in bind: p[j]=c
            else:
                bbind=sum(b[j] for j in bind); bfree=sum(b[j] for j in free)
                lo=1e-14; hi=(1.0-epsilon)/(k+1)-1e-14
                def deriv(c):
                    residual=1.0-(k+1)*c-epsilon
                    return -b[target]/(c+epsilon)-bbind/c+bfree*(k+1)/residual
                a,z=lo,hi
                for _ in range(160):
                    m=(a+z)/2.0
                    if deriv(m)>0: z=m
                    else: a=m
                c=(a+z)/2.0; residual=1.0-(k+1)*c-epsilon
                p=[None]*3; p[target]=c+epsilon
                for j in bind: p[j]=c
                for j in free: p[j]=residual*b[j]/bfree
            candidates.append((False,bind,p))
            if weak!=target:
                if weak not in group and len(free)==1 and free[0]==weak:
                    c=(1.0-b[weak]-epsilon)/(k+1)
                    p=[None]*3; p[weak]=b[weak]; p[target]=c+epsilon
                    for j in bind: p[j]=c
                    candidates.append((True,bind,p))
                elif weak in bind:
                    c=b[weak]
                    p=[None]*3; p[target]=c+epsilon
                    for j in bind: p[j]=c
                    rem=[i for i in range(3) if i not in group]
                    if rem:
                        p[rem[0]]=1.0-sum(x for x in p if x is not None)
                        candidates.append((True,bind,p))
    best=None
    for weak_binding,bind,p in candidates:
        if any(x is None or (not math.isfinite(x)) or x<=EPS for x in p): continue
        if abs(sum(p)-1.0)>1e-10: continue
        if any(p[target] < p[j]+epsilon-5e-13 for j in competitors): continue
        if p[weak] < b[weak]-TOL: continue
        kl=reverse_kl(b,p); tv=0.5*sum(abs(p[i]-b[i]) for i in range(3))
        rec=(kl,tv,p,bind,weak_binding)
        if best is None or rec[:2] < best[:2]: best=rec
    if best is None:
        return b,{'executed':False,'reason':'weak_floor_or_projection_infeasible','target':target,'baseline_top1':bt,'total_variation':0.0,'reverse_kl':0.0,'weak_floor_binding':False}
    kl,tv,p,bind,weak_binding=best
    if top1(p)!=target: raise Stage4KLError('projection did not create target Top1')
    return p,{'executed':True,'reason':'projected','target':target,'baseline_top1':bt,'binding_competitors':list(bind),'weak_floor_binding':bool(weak_binding),'total_variation':tv,'reverse_kl':kl}

def summarize_projection(rows,pm,bmap,dg):
    executed=[r['fixture_id'] for r in rows if dg[r['fixture_id']].get('v324_executed_switch',False)]
    kls=[]; weak_binding=0
    for fid in executed:
        kl=reverse_kl(bmap[fid],pm[fid]); kls.append(kl)
        if dg[fid].get('stage4_reverse_kl_weak_floor_binding',False): weak_binding+=1
    return {
        'weak_floor_binding_n':weak_binding,
        'mean_reverse_kl_on_executed':sum(kls)/len(kls) if kls else 0.0,
        'max_reverse_kl_on_executed':max(kls) if kls else 0.0
    }

def scaffold_config(c4):
    cfg=json.loads(json.dumps(c4))
    cfg['direction_generator']['frozen_t2_params']=dict(c4['direction_generator']['params'])
    return cfg

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_scaffold','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c4=json.loads(a.contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c4['status']!='FROZEN_BEFORE_STAGE4_TARGET_SCORING' or c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING':
        raise Stage4KLError('contract drift')
    if c4['projection']['objective']!='minimize KL(base || p) = sum_i base_i * log(base_i / p_i)':
        raise Stage4KLError('KL orientation drift')
    if c4['direction_generator']['params']!={'cap':0.02,'draw_lambda':16.0,'draw_scale':0.25,'half_life':1.0,'side_lambda':4.0,'side_scale':0.25,'support_tau':1.5}:
        raise Stage4KLError('frozen T2 params drift')
    scaffold_c4=scaffold_config(c4)
    if scaffold_c4['direction_generator']['frozen_t2_params']!=c4['direction_generator']['params']:
        raise Stage4KLError('scaffold compatibility alias drift')
    v324=loadmod('stage4_kl_v324_scaffold',a.v324_scaffold)
    v324.minimum_boundary_projection=minimum_reverse_kl_boundary_projection
    original_project=v324.project_direction_map
    def project_with_diag(rows,bmap,qmap,dg,v322,epsilon):
        pm,outdg,stats=original_project(rows,bmap,qmap,dg,v322,epsilon)
        for r in rows:
            fid=r['fixture_id']; d=outdg[fid]
            if d.get('v324_executed_switch',False):
                _,weak=v322.fav_weak(bmap[fid])
                _,meta=minimum_reverse_kl_boundary_projection(bmap[fid],d['v324_target'],weak,epsilon)
                d['stage4_reverse_kl']=meta['reverse_kl']; d['stage4_reverse_kl_weak_floor_binding']=meta['weak_floor_binding']
            else:
                d['stage4_reverse_kl']=0.0; d['stage4_reverse_kl_weak_floor_binding']=False
        stats.update(summarize_projection(rows,pm,bmap,outdg))
        return pm,outdg,stats
    v324.project_direction_map=project_with_diag
    v323=loadmod('stage4_kl_v323',a.v323); v322=loadmod('stage4_kl_v322',a.v322)
    a.v32dev_module=loadmod('stage4_kl_v32dev',a.v32dev); core=loadmod('stage4_kl_core',a.core)
    v311=loadmod('stage4_kl_v311',a.v311); v31=loadmod('stage4_kl_v31',a.v31)
    usr=loadmod('stage4_kl_usr',a.usr1); v2=loadmod('stage4_kl_v2',a.v2); xg=loadmod('stage4_kl_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec)
    write_json(a.out/'pre2023_process_receipt.json',procrec)
    write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec})
    write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,spm,sdg,_=v324.predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,scaffold_c4,v311,v322,v323,None,'sanity_gates_2019_2020')
    if 'v324_projection' in sanity['pooled']:
        sanity['pooled']['reverse_kl_projection']=sanity['pooled'].pop('v324_projection')
    sanity_rows=[r for r in rows if r['season'] in (2019,2020)]
    sanity['pooled']['reverse_kl_projection'].update(summarize_projection(sanity_rows,spm,bmap,sdg))
    write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-stage4-reverse-kl-final-v1','status':c4['terminal']['development_failure'],'reason':'fixed_reverse_kl_projection_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,rpm,rdg,_=v324.predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,scaffold_c4,v311,v322,v323,fold_map,'hard_gates_2021_2022')
    if 'v324_projection' in rolling['pooled']:
        rolling['pooled']['reverse_kl_projection']=rolling['pooled'].pop('v324_projection')
    rolling_rows=[r for r in rows if r['season'] in (2021,2022)]
    rolling['pooled']['reverse_kl_projection'].update(summarize_projection(rolling_rows,rpm,bmap,rdg))
    write_json(a.out/'rolling_2021_2022.json',rolling)
    status=c4['terminal']['development_success'] if rolling['all_pass'] else c4['terminal']['development_failure']
    final={'schema_version':'football3-stage4-reverse-kl-final-v1','status':status,'reason':('all_frozen_stage4_gates_passed' if rolling['all_pass'] else 'fixed_reverse_kl_projection_failed_2021_2022_hard_gates'),'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
