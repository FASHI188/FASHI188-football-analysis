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
    xs=[1.0/float(x) for x in vals]; s=sum(xs); return [x/s for x in xs]
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def parse_date(s):
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try: return datetime.datetime.strptime(s.strip(),fmt).date()
        except ValueError: pass
    raise ValueError(s)
def usable_odds(r,cols):
    try:
        vals=[float(r[c]) for c in cols]
        return all(math.isfinite(x) and x>1.0 for x in vals)
    except Exception: return False

def metric(rows,key):
    n=len(rows)
    if not n: return {'n':0,'logloss':None,'brier':None,'rps':None,'top1_accuracy':None}
    ll=br=rp=acc=0.0
    for r in rows:
        p=r[key]; y=r['y']; ll-=math.log(max(EPS,p[y]))
        br+=sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
        yc=[1.0 if y==0 else 0.0,1.0 if y<=1 else 0.0]; pc=[p[0],p[0]+p[1]]
        rp+=0.5*sum((pc[i]-yc[i])**2 for i in range(2)); acc+=1.0 if top1(p)==y else 0.0
    return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1_accuracy':acc/n}
def deltas(a,b): return {k:b[k]-a[k] for k in ('logloss','brier','rps','top1_accuracy')}
def evidence_dominates(op,cp,old,target):
    return math.log(cp[target]/cp[old]) >= math.log(op[old]/op[target])

def projected(v324,op,target,eps):
    weak=0 if op[0] < op[2] else 2
    return v324.minimum_boundary_projection(op,target,weak,eps)

def eval_slice(rows,key):
    b=metric(rows,'open'); q=metric(rows,key); return {'baseline':b,'candidate':q,'deltas':deltas(b,q),'ll_nondegrade':q['logloss']<=b['logloss']}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',required=True,type=pathlib.Path); ap.add_argument('--data-dir',required=True,type=pathlib.Path); ap.add_argument('--v324-runner',required=True,type=pathlib.Path); ap.add_argument('--out',required=True,type=pathlib.Path)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=load_json(a.contract)
    if c['status']!='FROZEN_BEFORE_SECOND_EXTERNAL_COHORT_DOWNLOAD_OR_SCORING': raise RuntimeError('contract drift')
    v324=loadmod('ed_v324',a.v324_runner); eps=float(c['mechanism']['epsilon']); req=c['data']['required_columns']
    first_codes={'N1','P1','B1','SC0','T1','G1'}; codes={x['code'] for x in c['data']['leagues']}
    if codes & first_codes: raise RuntimeError('cohort overlap with first external stress')
    rows=[]; inventory=[]; invalid=0
    for season in c['data']['seasons']:
        sp=c['data']['season_paths'][season]
        for lg in c['data']['leagues']:
            code=lg['code']; path=a.data_dir/f'{sp}_{code}.csv'
            with path.open('r',encoding='utf-8-sig',newline='') as f:
                rd=csv.DictReader(f); header=rd.fieldnames or []; miss=[x for x in req if x not in header]
                if miss: raise RuntimeError(f'{path}: missing columns {miss}')
                raw=list(rd)
            done=usable=0
            for r in raw:
                if r.get('Div','').strip()!=code: raise RuntimeError(f'{path}: division mismatch')
                if r.get('FTR','').strip() not in OUTCOME: continue
                done+=1
                if not usable_odds(r,['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA']): invalid+=1; continue
                op=norm_odds([r['AvgH'],r['AvgD'],r['AvgA']]); cp=norm_odds([r['AvgCH'],r['AvgCD'],r['AvgCA']])
                old=top1(op); target=top1(cp); raw_prop=(old!=target); dom=bool(raw_prop and evidence_dominates(op,cp,old,target))
                always=list(op); apr={'executed':False,'reason':'same_argmax','total_variation':0.0}
                cand=list(op); cpr={'executed':False,'reason':'not_dominant_or_same','total_variation':0.0}
                if raw_prop: always,apr=projected(v324,op,target,eps)
                if dom: cand,cpr=projected(v324,op,target,eps)
                rows.append({'season':season,'league':code,'date':parse_date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUTCOME[r['FTR'].strip()],'open':op,'close':cp,'always':always,'candidate':cand,'raw_proposal':raw_prop,'dominant':dom,'always_executed':bool(apr['executed']),'candidate_executed':bool(cpr['executed']),'always_tv':float(apr.get('total_variation',0.0)),'candidate_tv':float(cpr.get('total_variation',0.0))})
                usable+=1
            inventory.append({'season':season,'league':code,'completed_match_count':done,'usable_pre_match_odds_count':usable,'file':path.name})
    rows.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away'],r['season']))
    if not rows: raise RuntimeError('no usable rows')
    opening=metric(rows,'open'); always=metric(rows,'always'); cand=metric(rows,'candidate'); d_open=deltas(opening,cand); d_always=deltas(always,cand)
    nfold=int(c['evaluation']['chronological_folds']); folds=[]; cand_non=always_non=0
    for k in range(nfold):
        lo=len(rows)*k//nfold; hi=len(rows)*(k+1)//nfold; rr=rows[lo:hi]
        eca=eval_slice(rr,'candidate'); eal=eval_slice(rr,'always'); cand_non+=int(eca['ll_nondegrade']); always_non+=int(eal['ll_nondegrade'])
        folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'candidate':eca,'always_project':eal})
    groups=[]; cand_gnon=always_gnon=0
    for season in c['data']['seasons']:
        for lg in c['data']['leagues']:
            rr=[r for r in rows if r['season']==season and r['league']==lg['code']]
            eca=eval_slice(rr,'candidate'); eal=eval_slice(rr,'always'); cand_gnon+=int(eca['ll_nondegrade']); always_gnon+=int(eal['ll_nondegrade'])
            groups.append({'season':season,'league':lg['code'],'n':len(rr),'candidate':eca,'always_project':eal})
    acc=c['evaluation']['acceptance']
    checks={
      'global_ll':d_open['logloss']<=acc['candidate_global_logloss_delta_vs_opening_max'],
      'global_brier':d_open['brier']<=acc['candidate_global_brier_delta_vs_opening_max'],
      'global_rps':d_open['rps']<=acc['candidate_global_rps_delta_vs_opening_max'],
      'global_top1_strict':d_open['top1_accuracy']>acc['candidate_global_top1_delta_vs_opening_min_exclusive'],
      'fold_ll':cand_non>=acc['candidate_chronological_fold_ll_nondegrade_min'],
      'group_ll':cand_gnon>=acc['candidate_league_season_ll_nondegrade_min'],
      'fold_stability_ge_always':(cand_non>=always_non) if acc['candidate_fold_ll_nondegrade_must_be_ge_always_project'] else True,
      'global_ll_le_always':(cand['logloss']<=always['logloss']) if acc['candidate_global_logloss_must_be_le_always_project'] else True
    }
    ok=all(checks.values()); status=c['evaluation']['terminal']['pass'] if ok else c['evaluation']['terminal']['fail']
    raw_n=sum(r['raw_proposal'] for r in rows); dom_n=sum(r['dominant'] for r in rows); ae=sum(r['always_executed'] for r in rows); ce=sum(r['candidate_executed'] for r in rows)
    out={'schema_version':'football3-v3-evidence-dominance-external-confirm-result-v1','status':status,'all_pass':ok,'scientific_role':c['scientific_role'],'row_count':len(rows),'invalid_odds_row_count':invalid,'inventory':inventory,'counts':{'raw_proposal_n':raw_n,'evidence_dominant_n':dom_n,'always_project_executed_n':ae,'candidate_executed_n':ce},'global':{'opening':opening,'always_project':always,'candidate':cand,'candidate_delta_vs_opening':d_open,'candidate_delta_vs_always_project':d_always},'chronological_fold_ll_nondegrade':{'candidate':cand_non,'always_project':always_non,'required_candidate':acc['candidate_chronological_fold_ll_nondegrade_min']},'league_season_ll_nondegrade':{'candidate':cand_gnon,'always_project':always_gnon,'required_candidate':acc['candidate_league_season_ll_nondegrade_min']},'checks':checks,'chronological_folds':folds,'league_season_groups':groups,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,'interpretation':('Zero-parameter evidence dominance validated on the second untouched external cohort. It remains only a pre-frozen arbitration hypothesis for future fresh Big-5 confirmation.' if ok else 'Zero-parameter evidence dominance did not satisfy all frozen second-cohort stability gates. No rescue threshold or selector may be derived from this consumed cohort.')}
    write_json(a.out/'evidence_dominance_external_confirm_result.json',out)
    print(json.dumps({'status':status,'row_count':len(rows),'raw_proposal_n':raw_n,'candidate_executed_n':ce,'candidate_global_delta_vs_opening':d_open,'candidate_fold_ll_nondegrade_n':cand_non,'always_fold_ll_nondegrade_n':always_non,'candidate_group_ll_nondegrade_n':cand_gnon,'always_group_ll_nondegrade_n':always_gnon,'checks':checks},sort_keys=True))
if __name__=='__main__': main()
