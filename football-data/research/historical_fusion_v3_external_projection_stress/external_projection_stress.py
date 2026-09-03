#!/usr/bin/env python3
import argparse,csv,datetime,importlib.util,json,math,pathlib,sys

EPS=1e-15
OUTCOME={'H':0,'D':1,'A':2}

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def load_json(path): return json.loads(pathlib.Path(path).read_text())
def write_json(path,obj):
    p=pathlib.Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+'\n')
def norm_odds(vals):
    xs=[1.0/float(x) for x in vals]
    s=sum(xs)
    return [x/s for x in xs]
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def metric(rows,key):
    n=len(rows)
    if not n: return {'n':0,'logloss':None,'brier':None,'rps':None,'top1_accuracy':None}
    ll=br=rp=acc=0.0
    for r in rows:
        p=r[key]; y=r['y']; ll-=math.log(max(EPS,p[y]));
        br+=sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
        ycum=[1.0 if y==0 else 0.0,1.0 if y<=1 else 0.0]
        pcum=[p[0],p[0]+p[1]]
        rp+=0.5*sum((pcum[i]-ycum[i])**2 for i in range(2))
        acc+=1.0 if top1(p)==y else 0.0
    return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1_accuracy':acc/n}
def deltas(base,cand):
    return {k:cand[k]-base[k] for k in ('logloss','brier','rps','top1_accuracy')}
def parse_date(s):
    s=s.strip()
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try: return datetime.datetime.strptime(s,fmt).date()
        except ValueError: pass
    raise ValueError(s)
def usable_odds(r,cols):
    try:
        vals=[float(r[c]) for c in cols]
        return all(math.isfinite(x) and x>1.0 for x in vals)
    except Exception: return False

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',required=True,type=pathlib.Path); ap.add_argument('--data-dir',required=True,type=pathlib.Path); ap.add_argument('--v324-runner',required=True,type=pathlib.Path); ap.add_argument('--out',required=True,type=pathlib.Path)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=load_json(a.contract)
    if c['status']!='FROZEN_BEFORE_EXTERNAL_DATA_DOWNLOAD_OR_LABEL_SCORING': raise RuntimeError('contract drift')
    v324=loadmod('external_stress_v324',a.v324_runner)
    rows=[]; inventory=[]; missing_value_rows=0
    req=c['data']['required_columns']
    for season in c['data']['seasons']:
        sp=c['data']['season_paths'][season]
        for lg in c['data']['leagues']:
            code=lg['code']; path=a.data_dir/f'{sp}_{code}.csv'
            with path.open('r',encoding='utf-8-sig',newline='') as f:
                rd=csv.DictReader(f); header=rd.fieldnames or []
                miss=[x for x in req if x not in header]
                if miss: raise RuntimeError(f'{path}: missing columns {miss}')
                raw=list(rd)
            n_done=n_use=0
            for r in raw:
                if r.get('Div','').strip()!=code: raise RuntimeError(f'{path}: division mismatch')
                if r.get('FTR','').strip() not in OUTCOME: continue
                n_done+=1
                if not usable_odds(r,['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA']):
                    missing_value_rows+=1; continue
                op=norm_odds([r['AvgH'],r['AvgD'],r['AvgA']]); cp=norm_odds([r['AvgCH'],r['AvgCD'],r['AvgCA']])
                weak=0 if op[0] < op[2] else 2
                target=top1(cp)
                proposed=top1(op)!=target
                if proposed:
                    pp,pr=v324.minimum_boundary_projection(op,target,weak,float(c['mechanism']['epsilon']))
                else:
                    pp=list(op); pr={'executed':False,'reason':'same_argmax','total_variation':0.0,'l2_sq':0.0}
                rows.append({'season':season,'season_path':sp,'league':code,'date':parse_date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUTCOME[r['FTR'].strip()],'open':op,'close':cp,'candidate':pp,'proposed':proposed,'executed':bool(pr['executed']),'projection_reason':pr['reason'],'weak_floor_fallback':bool(proposed and not pr['executed']),'projection_tv':float(pr.get('total_variation',0.0))})
                n_use+=1
            inventory.append({'season':season,'season_path':sp,'league':code,'completed_match_count':n_done,'usable_pre_match_odds_count':n_use,'file':path.name})
    rows.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away'],r['season']))
    if not rows: raise RuntimeError('no usable rows')
    base=metric(rows,'open'); cand=metric(rows,'candidate'); d=deltas(base,cand)
    nfold=int(c['evaluation']['chronological_folds']); folds=[]; nondeg=0
    for k in range(nfold):
        lo=(len(rows)*k)//nfold; hi=(len(rows)*(k+1))//nfold; rr=rows[lo:hi]
        b=metric(rr,'open'); q=metric(rr,'candidate'); dd=deltas(b,q); ok=dd['logloss']<=0.0; nondeg+=int(ok)
        folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'baseline':b,'candidate':q,'deltas':dd,'ll_nondegrade':ok})
    groups=[]; gnon=0
    for season in c['data']['seasons']:
        for lg in c['data']['leagues']:
            rr=[r for r in rows if r['season']==season and r['league']==lg['code']]
            b=metric(rr,'open'); q=metric(rr,'candidate'); dd=deltas(b,q); ok=dd['logloss']<=0.0; gnon+=int(ok)
            groups.append({'season':season,'league':lg['code'],'n':len(rr),'baseline':b,'candidate':q,'deltas':dd,'ll_nondegrade':ok})
    global_ok=(d['logloss']<=0 and d['brier']<=0 and d['rps']<=0 and d['top1_accuracy']>=0)
    if d['logloss']>0 or nondeg<=6 or gnon<=6: cls='GEOMETRY_EXTERNALLY_IMPLICATED'
    elif global_ok and nondeg>=10 and gnon>=10: cls='GEOMETRY_EXTERNALLY_STABLE'
    else: cls='GEOMETRY_EXTERNALLY_MIXED'
    props=sum(r['proposed'] for r in rows); exe=sum(r['executed'] for r in rows); fall=sum(r['weak_floor_fallback'] for r in rows)
    tv=[r['projection_tv'] for r in rows if r['executed']]
    out={'schema_version':'football3-v3-external-projection-stress-result-v1','classification':cls,'scientific_role':c['scientific_role'],'row_count':len(rows),'missing_or_invalid_odds_row_count':missing_value_rows,'inventory':inventory,'projection':{'proposal_n':props,'executed_n':exe,'weak_floor_fallback_n':fall,'mean_tv':sum(tv)/len(tv) if tv else 0.0,'max_tv':max(tv) if tv else 0.0},'global':{'baseline':base,'candidate':cand,'deltas':d},'chronological_fold_ll_nondegrade_n':nondeg,'chronological_folds':folds,'league_season_ll_nondegrade_n':gnon,'league_season_groups':groups,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,'interpretation':('Projection geometry is externally stable under an independent pre-match direction signal; remaining Stage7 fold-LL instability is more consistent with direction-generation/context risk than with the minimum-boundary projection operator itself.' if cls=='GEOMETRY_EXTERNALLY_STABLE' else 'External stress does not cleanly exonerate projection geometry.' if cls=='GEOMETRY_EXTERNALLY_MIXED' else 'External stress implicates projection geometry as a plausible contributor to fold-LL instability.')}
    write_json(a.out/'external_projection_stress_result.json',out)
    safe={'classification':cls,'row_count':len(rows),'proposal_n':props,'executed_n':exe,'weak_floor_fallback_n':fall,'global_deltas':d,'chronological_fold_ll_nondegrade_n':nondeg,'league_season_ll_nondegrade_n':gnon}
    print(json.dumps(safe,sort_keys=True))
if __name__=='__main__': main()
