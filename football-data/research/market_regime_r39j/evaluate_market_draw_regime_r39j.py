#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
ALL_ODDS=['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5','AvgAHH','AvgAHA','AvgCAHH','AvgCAHA']
LINES=['AHh','AHCh']

def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids):return htxt('\n'.join(sorted(ids))+'\n')
def valid_odd(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0
def valid_line(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x)
def parse_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)
def ident(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
def devig3(a,b,c):
    q=np.array([1/float(a),1/float(b),1/float(c)],dtype=float);return q/q.sum()
def devig2(a,b):
    q=np.array([1/float(a),1/float(b)],dtype=float);return q/q.sum()
def clip(x,lo=.02,hi=.65):return min(hi,max(lo,float(x)))

def load_market(path,pre_seasons,hold_season):
    rows=[]
    for p in sorted(Path(path).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or []);needed=set(IDCOLS+ALL_ODDS+LINES)
            if not needed<=hdr:continue
            if {'FTR','FTHG','FTAG'}&hdr:raise RuntimeError('label column leaked into market-only input')
            for r in rd:
                if r.get('Season') not in pre_seasons|{hold_season}:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in ALL_ODDS) or not all(valid_line(r.get(c,'')) for c in LINES):continue
                p3=devig3(r['AvgCH'],r['AvgCD'],r['AvgCA']);ou=devig2(r['AvgC>2.5'],r['AvgC<2.5'])
                under=float(ou[1]);gap=abs(float(p3[0])-float(p3[2]));pressure=under*math.exp(-abs(float(r['AHCh'])))
                rows.append({'identity':ident(r),'season':r['Season'],'div':r['Div'],'date':parse_date(r['Date']),'p':p3,'pdraw':float(p3[1]),'gap':gap,'pressure':pressure})
    return rows

def load_labels(raw_dir,seasons,allowed_ids=None):
    out={};seasons=set(seasons)
    for p in sorted(Path(raw_dir).glob('*.csv')):
        season=p.stem.split('_',1)[0]
        if season not in seasons:continue
        with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if not set(IDCOLS[1:]+['FTR'])<=hdr:continue
            for r in rd:
                rr={'Season':season,**r};i=ident(rr)
                if allowed_ids is not None and i not in allowed_ids:continue
                v=str(r.get('FTR','')).strip().upper()
                if v in {'H','D','A'}:out[i]={'H':0,'D':1,'A':2}[v]
    return out

def quantile_cutpoints(fit_rows):
    arrs=[np.array([r[k] for r in fit_rows],dtype=float) for k in ('pdraw','gap','pressure')]
    return {k:[float(x) for x in np.quantile(a,[1/3,2/3],method='linear')] for k,a in zip(('pdraw','gap','pressure'),arrs)}
def bin3(x,c):return int(np.searchsorted(np.asarray(c,dtype=float),float(x),side='right'))
def regime(r,cuts):return f"{bin3(r['pdraw'],cuts['pdraw'])}{bin3(r['gap'],cuts['gap'])}{bin3(r['pressure'],cuts['pressure'])}"

def fit_corrections(rows,labels,cuts,pre,active_ids=None):
    grp=defaultdict(list)
    for r in rows:
        if r['identity'] in labels:grp[regime(r,cuts)].append(r)
    cells={};active=set()
    for rid,rr in sorted(grp.items()):
        ys=np.array([1.0 if labels[x['identity']]==1 else 0.0 for x in rr]);pm=np.array([x['pdraw'] for x in rr]);n=len(rr);res=float(ys.mean()-pm.mean());se=math.sqrt(max(float(pm.mean()*(1-pm.mean())),1e-12)/n);z=abs(res)/se
        eligible=(n>=pre['regime_definition']['minimum_fit_rows_per_active_regime'] and abs(res)>=pre['regime_definition']['minimum_absolute_fit_calibration_residual'] and z>=pre['regime_definition']['minimum_absolute_fit_zscore']) if active_ids is None else rid in active_ids
        corr=(n/(n+pre['correction']['pseudo_count']))*res if eligible else 0.0
        cells[rid]={'n':n,'observed_draw_rate':float(ys.mean()),'mean_market_draw_probability':float(pm.mean()),'raw_residual':res,'absolute_zscore':z,'correction':corr,'active':bool(eligible)}
        if eligible:active.add(rid)
    if active_ids is not None:
        for rid in active_ids:
            if rid not in cells:cells[rid]={'n':0,'observed_draw_rate':None,'mean_market_draw_probability':None,'raw_residual':None,'absolute_zscore':None,'correction':0.0,'active':True};active.add(rid)
    return cells,active

def corrected_probs(rows,cuts,cells):
    out=[]
    for r in rows:
        rid=regime(r,cuts);corr=float(cells.get(rid,{}).get('correction',0.0) or 0.0);pd=clip(r['pdraw']+corr);nondraw=1-pd;den=float(r['p'][0]+r['p'][2]);hshare=float(r['p'][0]/den);out.append(np.array([nondraw*hshare,pd,nondraw*(1-hshare)],dtype=float))
    return out

def auc_binary(y,s):
    y=np.asarray(y,dtype=int);s=np.asarray(s,dtype=float);n1=int(y.sum());n0=len(y)-n1
    if n1==0 or n0==0:return None
    order=np.argsort(s,kind='mergesort');ranks=np.empty(len(s),dtype=float);i=0
    while i<len(s):
        j=i+1
        while j<len(s) and s[order[j]]==s[order[i]]:j+=1
        rank=(i+1+j)/2.0;ranks[order[i:j]]=rank;i=j
    return float((ranks[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def metric_pack(rows,probs,labels):
    ys=np.array([labels[r['identity']] for r in rows],dtype=int);P=np.asarray(probs,dtype=float);eps=1e-15
    ll=float(np.mean([-math.log(max(float(P[i,y]),eps)) for i,y in enumerate(ys)]));one=np.eye(3)[ys];brier=float(np.mean(np.sum((P-one)**2,axis=1)))
    pc=np.cumsum(P,axis=1)[:,:2];oc=np.cumsum(one,axis=1)[:,:2];rps=float(np.mean(np.sum((pc-oc)**2,axis=1)/2.0));yd=(ys==1).astype(int);pd=P[:,1]
    dll=float(np.mean(-(yd*np.log(np.clip(pd,eps,1-eps))+(1-yd)*np.log(np.clip(1-pd,eps,1-eps)))));auc=auc_binary(yd,pd);pred=np.argmax(P,axis=1);acc=float(np.mean(pred==ys))
    return {'HDA_LogLoss':ll,'HDA_Brier':brier,'RPS':rps,'binary_Draw_LogLoss':dll,'Draw_AUC':auc,'Top1_accuracy':acc,'Top1_draw_count':int(np.sum(pred==1)),'actual_draw_count':int(np.sum(ys==1))}
def market_probs(rows):return [r['p'] for r in rows]
def per_div_wins(rows,pcorr,labels):
    wins=0;detail={}
    for d in sorted({r['div'] for r in rows}):
        ix=[i for i,r in enumerate(rows) if r['div']==d];rr=[rows[i] for i in ix];cc=[pcorr[i] for i in ix];m=metric_pack(rr,market_probs(rr),labels);c=metric_pack(rr,cc,labels);w=c['HDA_LogLoss']<m['HDA_LogLoss'];wins+=int(w);detail[d]={'rows':len(rr),'market_HDA_LogLoss':m['HDA_LogLoss'],'corrected_HDA_LogLoss':c['HDA_LogLoss'],'win':w}
    return wins,detail
def sign_persistence(rows,labels,cuts,active,fit_cells,minrows):
    grp=defaultdict(list)
    for r in rows:
        rid=regime(r,cuts)
        if rid in active and r['identity'] in labels:grp[rid].append(r)
    eligible=0;same=0;detail={}
    for rid in sorted(active):
        rr=grp.get(rid,[])
        if len(rr)<minrows:continue
        ys=np.array([1.0 if labels[x['identity']]==1 else 0.0 for x in rr]);pm=np.array([x['pdraw'] for x in rr]);res=float(ys.mean()-pm.mean());base=float(fit_cells[rid]['raw_residual']);ok=(res==0 and base==0) or res*base>0;eligible+=1;same+=int(ok);detail[rid]={'rows':len(rr),'segment_residual':res,'fit_residual':base,'same_sign':ok}
    return {'eligible_regimes':eligible,'same_sign_regimes':same,'same_sign_fraction':(same/eligible if eligible else 0.0),'details':detail}
def gate_metrics(c,m):return c['HDA_LogLoss']<m['HDA_LogLoss'] and c['binary_Draw_LogLoss']<m['binary_Draw_LogLoss'] and c['HDA_Brier']<=m['HDA_Brier'] and c['RPS']<=m['RPS']

def self_test():
    p=devig3(2,3,4);assert abs(float(p.sum())-1)<1e-12;assert bin3(.2,[.3,.5])==0 and bin3(.4,[.3,.5])==1 and bin3(.7,[.3,.5])==2
    print('PASS_R39J_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());pre_seasons={'1920','2021','2122','2223','2324','2425'};hold='2526';rows=load_market(a.market_dir,pre_seasons,hold);pre_rows=sorted([r for r in rows if r['season'] in pre_seasons],key=lambda r:(r['date'],r['div'],r['identity']));hold_rows=[r for r in rows if r['season']==hold]
    if len(pre_rows)!=pre['source_binding']['complete_preholdout_rows'] or len(hold_rows)!=pre['source_binding']['complete_holdout_rows']:raise RuntimeError(f"identity drift pre={len(pre_rows)} hold={len(hold_rows)}")
    fixed=sorted(hold_rows,key=lambda r:htxt(f"{pre['source_binding']['fixed100_seed']}|{r['identity']}"))[:100];sha=set_sha([r['identity'] for r in fixed])
    if sha!=pre['source_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 identity drift {sha}')
    n=len(pre_rows);i1=round(n*.60);i2=round(n*.80);fit=pre_rows[:i1];val=pre_rows[i1:i2];pol=pre_rows[i2:]
    cuts=quantile_cutpoints(fit)
    # Only now access preholdout labels. 2025/26 files are not opened in this call.
    lab=load_labels(a.raw_dir,pre_seasons)
    if any(r['identity'] not in lab for r in pre_rows):raise RuntimeError('missing preholdout labels')
    fit_cells,active=fit_corrections(fit,lab,cuts,pre)
    val_corr=corrected_probs(val,cuts,fit_cells);val_m=metric_pack(val,market_probs(val),lab);val_c=metric_pack(val,val_corr,lab);vw,vdetail=per_div_wins(val,val_corr,lab);vs=sign_persistence(val,lab,cuts,active,fit_cells,pre['validation_gate_all_required']['active_regime_validation_rows_min_for_sign_check'])
    vg=pre['validation_gate_all_required'];validation_pass=(len(active)>=vg['active_regimes_min'] and gate_metrics(val_c,val_m) and vs['same_sign_fraction']>=vg['active_regime_same_sign_fraction_min'] and vw>=vg['per_division_HDA_LogLoss_wins_min'])
    outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'preholdout_rows':n,'split':{'fit':len(fit),'validation':len(val),'policy':len(pol)},'cutpoints':cuts,'fit_active_regimes':sorted(active),'fit_cells':fit_cells,'validation':{'market':val_m,'corrected':val_c,'sign_persistence':vs,'division_wins':vw,'division_detail':vdetail,'gate_pass':validation_pass},'training_labels_accessed':n,'holdout_labels_accessed':0,'fixed100_identity_sha256':sha,'hard_limits':pre['hard_limits']}
    if not validation_pass:
        base['status']=vg['if_fail'];(outdir/'freeze_receipt_r39j.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,'validation_pass':False,'policy_pass':None,'fixed100_identity_sha256':sha},indent=2));(outdir/'r39j_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':base['status'],'active_regimes':len(active),'validation':base['validation'],'holdout_labels_accessed':0},indent=2));return
    # Same cells and cutpoints; only magnitudes are recomputed on fit+validation.
    fv=fit+val;fv_cells,_=fit_corrections(fv,lab,cuts,pre,active);pol_corr=corrected_probs(pol,cuts,fv_cells);pol_m=metric_pack(pol,market_probs(pol),lab);pol_c=metric_pack(pol,pol_corr,lab);pw,pdetail=per_div_wins(pol,pol_corr,lab);ps=sign_persistence(pol,lab,cuts,active,fit_cells,pre['policy_confirmation']['all_required']['active_regime_policy_rows_min_for_sign_check']);pg=pre['policy_confirmation']['all_required'];policy_pass=(gate_metrics(pol_c,pol_m) and ps['same_sign_fraction']>=pg['active_regime_same_sign_fraction_min'] and pw>=pg['per_division_HDA_LogLoss_wins_min'])
    base['policy']={'market':pol_m,'corrected':pol_c,'sign_persistence':ps,'division_wins':pw,'division_detail':pdetail,'gate_pass':policy_pass}
    if not policy_pass:
        base['status']=pre['policy_confirmation']['if_fail'];(outdir/'freeze_receipt_r39j.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,'validation_pass':True,'policy_pass':False,'fixed100_identity_sha256':sha},indent=2));(outdir/'r39j_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':base['status'],'validation_pass':True,'policy':base['policy'],'holdout_labels_accessed':0},indent=2));return
    final_cells,_=fit_corrections(pre_rows,lab,cuts,pre,active)
    freeze={'final_freeze_completed':True,'generated_at_utc':datetime.now(timezone.utc).isoformat(),'holdout_labels_accessed_before_freeze':0,'fixed100_identity_sha256':sha,'cutpoints':cuts,'active_regimes':sorted(active),'final_corrections':{rid:final_cells[rid] for rid in sorted(active)},'validation_pass':True,'policy_pass':True}
    (outdir/'freeze_receipt_r39j.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2))
    # First and only semantic access to 2025/26 result labels: fixed identities only.
    fids={r['identity'] for r in fixed};hlab=load_labels(a.raw_dir,{hold},fids)
    if set(hlab)!=fids:raise RuntimeError(f'fixed100 label mismatch got={len(hlab)} expected=100')
    hc=corrected_probs(fixed,cuts,final_cells);hm=metric_pack(fixed,market_probs(fixed),hlab);hh=metric_pack(fixed,hc,hlab);hg=pre['holdout']['gate_all_required'];hpass=gate_metrics(hh,hm);base['holdout']={'market':hm,'corrected':hh,'gate_pass':hpass};base['holdout_labels_accessed']=100;base['status']=pre['holdout']['pass_status'] if hpass else pre['holdout']['fail_status'];(outdir/'r39j_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':base['status'],'validation_pass':True,'policy_pass':True,'holdout':base['holdout'],'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
