#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,copy,hashlib,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
ODDS=['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5','AvgAHH','AvgAHA','AvgCAHH','AvgCAHA']
LINES=['AHh','AHCh']
ALL_SEASONS={'1920','2021','2122','2223','2324','2425','2526'}
PRE_SEASONS={'1920','2021','2122','2223','2324','2425'}

def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids):return htxt('\n'.join(sorted(ids))+'\n')
def ident(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
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
def devig3(a,b,c):
    q=np.array([1/float(a),1/float(b),1/float(c)],dtype=float);return q/q.sum()
def devig2(a,b):
    q=np.array([1/float(a),1/float(b)],dtype=float);return q/q.sum()
def bin3(x,c):return int(np.searchsorted(np.asarray(c,dtype=float),float(x),side='right'))
def clip(x,lo,hi):return min(hi,max(lo,float(x)))

def load_market(path):
    rows=[]
    for p in sorted(Path(path).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or []);needed=set(IDCOLS+ODDS+LINES)
            if {'FTR','FTHG','FTAG','HTR','HTHG','HTAG'}&hdr:raise RuntimeError('label column leaked into market-only input')
            if not needed<=hdr:continue
            for r in rd:
                if r.get('Season') not in ALL_SEASONS:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in ODDS) or not all(valid_line(r.get(c,'')) for c in LINES):continue
                p3=devig3(r['AvgCH'],r['AvgCD'],r['AvgCA']);ou=devig2(r['AvgC>2.5'],r['AvgC<2.5'])
                rows.append({'identity':ident(r),'season':r['Season'],'div':r['Div'],'date':parse_date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'p':p3,'pdraw':float(p3[1]),'gap':abs(float(p3[0])-float(p3[2])),'pressure':float(ou[1])*math.exp(-abs(float(r['AHCh'])))})
    return rows

def load_labels(raw_dir,seasons,allowed_ids=None):
    seasons=set(seasons);out={}
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

def quantile_cutpoints(rows):
    arrs=[np.asarray([r[k] for r in rows],dtype=float) for k in ('pdraw','gap','pressure')]
    return {k:[float(x) for x in np.quantile(a,[1/3,2/3],method='linear')] for k,a in zip(('pdraw','gap','pressure'),arrs)}
def regime(r,cuts):return f"{bin3(r['pdraw'],cuts['pdraw'])}{bin3(r['gap'],cuts['gap'])}{bin3(r['pressure'],cuts['pressure'])}"

def fresh_stat():return {'w':0.0,'s':0.0,'last':None}
def fresh_state():return {'global':fresh_stat(),'regimes':{}}
def decay_stat(st,date,half):
    if st['last'] is None:st['last']=date;return
    days=(date-st['last']).days
    if days<0:raise RuntimeError('time reversal')
    if days:
        fac=math.exp(-math.log(2.0)*days/float(half));st['w']*=fac;st['s']*=fac;st['last']=date

def probs_from_draw(r,pd):
    pd=clip(pd,0.02,0.65);den=float(r['p'][0]+r['p'][2]);hshare=float(r['p'][0]/den);nd=1-pd
    return np.asarray([nd*hshare,pd,nd*(1-hshare)],dtype=float)

def simulate(rows,labels,cuts,half,mode,pre,state=None):
    st=copy.deepcopy(state) if state is not None else fresh_state();pred={};diag={}
    grouped=defaultdict(list)
    for r in sorted(rows,key=lambda x:(x['date'],x['div'],x['home'],x['away'],x['identity'])):grouped[r['date']].append(r)
    gp=float(pre['online_update']['global_prior_strength']);rp=float(pre['online_update']['regime_prior_strength_toward_current_global']);lo,hi=pre['online_update']['correction_clip']
    for date in sorted(grouped):
        day=grouped[date];decay_stat(st['global'],date,half)
        if mode=='regime':
            for rid in {regime(r,cuts) for r in day}:
                cell=st['regimes'].setdefault(rid,fresh_stat());decay_stat(cell,date,half)
        gc=st['global']['s']/(st['global']['w']+gp)
        for r in day:
            if mode=='global':raw=gc;rid=None
            else:
                rid=regime(r,cuts);cell=st['regimes'][rid];raw=(cell['s']+rp*gc)/(cell['w']+rp)
            corr=clip(raw,lo,hi);pred[r['identity']]=probs_from_draw(r,r['pdraw']+corr);diag[r['identity']]={'raw_correction':float(raw),'correction':float(corr),'cap_hit':bool(raw<lo or raw>hi),'regime':rid}
        # Same-day hard barrier: all predictions above are complete before any update below.
        for r in day:
            if r['identity'] not in labels:raise RuntimeError(f'missing label for online update {r["identity"]}')
            res=(1.0 if labels[r['identity']]==1 else 0.0)-r['pdraw'];st['global']['w']+=1.0;st['global']['s']+=res
            if mode=='regime':
                cell=st['regimes'][regime(r,cuts)];cell['w']+=1.0;cell['s']+=res
    return pred,diag,st

def auc_binary(y,s):
    y=np.asarray(y,dtype=int);s=np.asarray(s,dtype=float);n1=int(y.sum());n0=len(y)-n1
    if n1==0 or n0==0:return None
    order=np.argsort(s,kind='mergesort');ranks=np.empty(len(s),dtype=float);i=0
    while i<len(s):
        j=i+1
        while j<len(s) and s[order[j]]==s[order[i]]:j+=1
        rank=(i+1+j)/2.0;ranks[order[i:j]]=rank;i=j
    return float((ranks[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def metric_pack(rows,pred,labels):
    ys=np.asarray([labels[r['identity']] for r in rows],dtype=int);P=np.asarray([pred[r['identity']] for r in rows],dtype=float);eps=1e-15
    ll=float(np.mean([-math.log(max(float(P[i,y]),eps)) for i,y in enumerate(ys)]));one=np.eye(3)[ys];brier=float(np.mean(np.sum((P-one)**2,axis=1)))
    pc=np.cumsum(P,axis=1)[:,:2];oc=np.cumsum(one,axis=1)[:,:2];rps=float(np.mean(np.sum((pc-oc)**2,axis=1)/2));yd=(ys==1).astype(int);pd=P[:,1]
    dll=float(np.mean(-(yd*np.log(np.clip(pd,eps,1-eps))+(1-yd)*np.log(np.clip(1-pd,eps,1-eps)))));predtop=np.argmax(P,axis=1)
    return {'HDA_LogLoss':ll,'HDA_Brier':brier,'RPS':rps,'binary_Draw_LogLoss':dll,'Draw_AUC':auc_binary(yd,pd),'Top1_accuracy':float(np.mean(predtop==ys)),'Top1_draw_count':int(np.sum(predtop==1)),'actual_draw_count':int(np.sum(ys==1))}
def market_pred(rows):return {r['identity']:r['p'] for r in rows}
def cap_fraction(rows,diag):return float(np.mean([1.0 if diag[r['identity']]['cap_hit'] else 0.0 for r in rows])) if rows else 0.0
def per_div_wins(rows,pred_a,pred_b,labels):
    detail={};wins=0
    for d in sorted({r['div'] for r in rows}):
        rr=[r for r in rows if r['div']==d];a=metric_pack(rr,pred_a,labels);b=metric_pack(rr,pred_b,labels);w=a['HDA_LogLoss']<b['HDA_LogLoss'];wins+=int(w);detail[d]={'rows':len(rr),'candidate_HDA_LogLoss':a['HDA_LogLoss'],'benchmark_HDA_LogLoss':b['HDA_LogLoss'],'win':w}
    return wins,detail

def state_json(st):
    def conv(x):return {'w':float(x['w']),'s':float(x['s']),'last':str(x['last']) if x['last'] else None}
    return {'global':conv(st['global']),'regimes':{k:conv(v) for k,v in sorted(st.get('regimes',{}).items())}}
def state_sha(st):return htxt(json.dumps(state_json(st),sort_keys=True,separators=(',',':')))

def choose_half(rows,labels,cuts,mode,pre):
    score_seasons=set(pre['hyperparameter_selection']['development_score_seasons']);through={'1920'}|score_seasons;rr=[r for r in rows if r['season'] in through];score=[r for r in rr if r['season'] in score_seasons];board=[]
    for half in pre['online_update']['half_life_days_candidates']:
        pred,diag,_=simulate(rr,labels,cuts,half,mode,pre);m=metric_pack(score,pred,labels);per={s:metric_pack([r for r in score if r['season']==s],pred,labels) for s in sorted(score_seasons)};board.append({'half_life_days':half,'pooled':m,'per_season':per,'cap_hit_fraction':cap_fraction(score,diag)})
    sel=sorted(board,key=lambda x:(x['pooled']['HDA_LogLoss'],x['pooled']['binary_Draw_LogLoss'],-x['half_life_days']))[0]
    return sel,board

def segment_gate(rows,market,glob,reg,regdiag,labels,cfg):
    mm=metric_pack(rows,market,labels);gm=metric_pack(rows,glob,labels);rm=metric_pack(rows,reg,labels);wm,dm=per_div_wins(rows,reg,market,labels);wg,dg=per_div_wins(rows,reg,glob,labels);cap=cap_fraction(rows,regdiag)
    ok=(rm['HDA_LogLoss']<mm['HDA_LogLoss'] and rm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and rm['HDA_Brier']<=mm['HDA_Brier'] and rm['RPS']<=mm['RPS'] and rm['HDA_LogLoss']<gm['HDA_LogLoss'] and rm['binary_Draw_LogLoss']<gm['binary_Draw_LogLoss'] and wm>=cfg['per_division_HDA_LogLoss_wins_vs_market_min'] and wg>=cfg['per_division_HDA_LogLoss_wins_vs_global_min'] and cap<=cfg['correction_cap_hit_fraction_max'])
    return {'market':mm,'global_online':gm,'regime_online':rm,'division_wins_vs_market':wm,'division_detail_vs_market':dm,'division_wins_vs_global':wg,'division_detail_vs_global':dg,'regime_cap_hit_fraction':cap,'gate_pass':ok}

def select_fixed(hold,pre):
    old=sorted(hold,key=lambda r:htxt(f"51146|{r['identity']}"))[:100];oldsha=set_sha([r['identity'] for r in old])
    if oldsha!=pre['prior_research_separation']['r39j_fixed100_identity_sha256']:raise RuntimeError(f'R39J fixed100 drift {oldsha}')
    oldids={r['identity'] for r in old};chosen=sorted([r for r in hold if r['identity'] not in oldids],key=lambda r:(r['date'],r['div'],r['home'],r['away'],r['identity']))[:100]
    return chosen,set_sha([r['identity'] for r in chosen]),len({r['identity'] for r in chosen}&oldids)

def self_test():
    p=devig3(2,3,4);assert abs(float(p.sum())-1)<1e-12
    cuts={'pdraw':[.25,.35],'gap':[.1,.2],'pressure':[.2,.4]};r={'pdraw':.3,'gap':.15,'pressure':.5};assert regime(r,cuts)=='112'
    print('PASS_R39K_SELF_TEST')

def write_stop(outdir,base,status,freeze_extra):
    base['status']=status;base['holdout_labels_accessed']=0;(outdir/'freeze_receipt_r39k.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,**freeze_extra},ensure_ascii=False,indent=2));(outdir/'r39k_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());
    if pre['source_binding']['fixed100_identity_sha256']=='PENDING_ZERO_LABEL_LOCK':raise RuntimeError('holdout binding still pending')
    rows=load_market(a.market_dir);pre_rows=sorted([r for r in rows if r['season'] in PRE_SEASONS],key=lambda r:(r['date'],r['div'],r['home'],r['away'],r['identity']));hold=sorted([r for r in rows if r['season']=='2526'],key=lambda r:(r['date'],r['div'],r['home'],r['away'],r['identity']))
    if len(pre_rows)!=pre['source_binding']['complete_preholdout_rows'] or len(hold)!=pre['source_binding']['complete_2526_rows']:raise RuntimeError(f'source count drift pre={len(pre_rows)} hold={len(hold)}')
    fixed,sha,overlap=select_fixed(hold,pre)
    if sha!=pre['source_binding']['fixed100_identity_sha256'] or overlap!=0:raise RuntimeError(f'R39K holdout identity drift sha={sha} overlap={overlap}')
    cut_rows=[r for r in pre_rows if r['season']=='1920'];cuts=quantile_cutpoints(cut_rows)
    # Preholdout labels only. No 2025/26 file is opened by this call.
    lab=load_labels(a.raw_dir,PRE_SEASONS)
    if any(r['identity'] not in lab for r in pre_rows):raise RuntimeError('missing preholdout labels')
    gsel,gboard=choose_half(pre_rows,lab,cuts,'global',pre);rsel,rboard=choose_half(pre_rows,lab,cuts,'regime',pre)
    devseas=set(pre['hyperparameter_selection']['development_score_seasons']);dev=[r for r in pre_rows if r['season'] in devseas];mdev=metric_pack(dev,market_pred(dev),lab);grows=[r for r in pre_rows if r['season'] in ({'1920'}|devseas)];gpred,_,_=simulate(grows,lab,cuts,gsel['half_life_days'],'global',pre);rpred,_,_=simulate(grows,lab,cuts,rsel['half_life_days'],'regime',pre);gdev=metric_pack(dev,gpred,lab);rdev=metric_pack(dev,rpred,lab)
    devpass=rdev['HDA_LogLoss']<mdev['HDA_LogLoss'] and rdev['binary_Draw_LogLoss']<mdev['binary_Draw_LogLoss'] and rdev['HDA_LogLoss']<gdev['HDA_LogLoss']
    outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':len(pre_rows),'holdout_pool':len(hold)},'fixed100_identity_sha256':sha,'fixed100_overlap_r39j':overlap,'regime_cutpoints_1920_market_only':cuts,'selected_half_lives':{'global':gsel['half_life_days'],'regime':rsel['half_life_days']},'candidate_leaderboards':{'global':gboard,'regime':rboard},'development':{'market':mdev,'global_online':gdev,'regime_online':rdev,'gate_pass':devpass},'training_labels_accessed':len(pre_rows),'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not devpass:
        write_stop(outdir,base,pre['hyperparameter_selection']['development_gate_for_regime']['if_fail'],{'development_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    gp,gdiag,gstate=simulate(pre_rows,lab,cuts,gsel['half_life_days'],'global',pre);rp,rdiag,rstate=simulate(pre_rows,lab,cuts,rsel['half_life_days'],'regime',pre);mp=market_pred(pre_rows)
    val=[r for r in pre_rows if r['season']==pre['confirmation_windows']['validation_season']];pol=[r for r in pre_rows if r['season']==pre['confirmation_windows']['policy_season']]
    v=segment_gate(val,mp,gp,rp,rdiag,lab,pre['validation_gate_all_required']);base['validation']=v
    if not v['gate_pass']:
        write_stop(outdir,base,pre['validation_gate_all_required']['if_fail'],{'development_pass':True,'validation_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    p=segment_gate(pol,mp,gp,rp,rdiag,lab,pre['policy_gate_all_required']);base['policy']=p
    if not p['gate_pass']:
        write_stop(outdir,base,pre['policy_gate_all_required']['if_fail'],{'development_pass':True,'validation_pass':True,'policy_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    freeze={'final_freeze_completed':True,'holdout_labels_accessed_before_freeze':0,'development_pass':True,'validation_pass':True,'policy_pass':True,'selected_half_lives':base['selected_half_lives'],'regime_cutpoints':cuts,'global_state_sha256':state_sha(gstate),'regime_state_sha256':state_sha(rstate),'fixed100_identity_sha256':sha,'frozen_at_utc':datetime.now(timezone.utc).isoformat()};(outdir/'freeze_receipt_r39k.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2))
    # Only after the freeze receipt exists: access exactly the locked 100 labels.
    ids={r['identity'] for r in fixed};hlab=load_labels(a.raw_dir,{'2526'},ids)
    if set(hlab)!=ids:raise RuntimeError(f'holdout label access mismatch {len(hlab)} != 100')
    hg,hgd,_=simulate(fixed,hlab,cuts,gsel['half_life_days'],'global',pre,gstate);hr,hrd,_=simulate(fixed,hlab,cuts,rsel['half_life_days'],'regime',pre,rstate);hm=market_pred(fixed)
    hmarket=metric_pack(fixed,hm,hlab);hglobal=metric_pack(fixed,hg,hlab);hreg=metric_pack(fixed,hr,hlab);hgates=pre['blind_holdout_protocol']['gate_all_required'];hpass=(hreg['HDA_LogLoss']<hmarket['HDA_LogLoss'] and hreg['binary_Draw_LogLoss']<hmarket['binary_Draw_LogLoss'] and hreg['HDA_Brier']<=hmarket['HDA_Brier'] and hreg['RPS']<=hmarket['RPS'] and hreg['HDA_LogLoss']<hglobal['HDA_LogLoss'] and hreg['binary_Draw_LogLoss']<hglobal['binary_Draw_LogLoss'])
    base['holdout']={'market':hmarket,'global_online':hglobal,'regime_online':hreg,'regime_cap_hit_fraction':cap_fraction(fixed,hrd),'gate_pass':hpass};base['holdout_labels_accessed']=100;base['status']=pre['blind_holdout_protocol']['pass_status'] if hpass else pre['blind_holdout_protocol']['fail_status'];(outdir/'r39k_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':base['status'],'selected_half_lives':base['selected_half_lives'],'validation':v,'policy':p,'holdout':base['holdout'],'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
