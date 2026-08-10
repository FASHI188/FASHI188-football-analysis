#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,random,re,sys
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_lineup_coverage_r39i import load_transfermarkt,choose,identity,parse_fd_date,valid_odd,htxt,set_sha

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
TRUTHY={'1','true','yes','y','t'}

def clip(p,lo=1e-8,hi=1-1e-8):return min(hi,max(lo,float(p)))
def logit(p):p=clip(p);return math.log(p/(1-p))
def entropy(q):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in q)
def devig3(a,b,c):
    x=np.array([1/float(a),1/float(b),1/float(c)],dtype=float);x/=x.sum();return x
def parse_dt(d,t):
    for f in ('%d/%m/%Y %H:%M','%d/%m/%y %H:%M'):
        try:return datetime.strptime(f'{str(d).strip()} {str(t).strip()}',f)
        except:pass
    raise ValueError(f'{d} {t}')
def parse_tm_date(s):return datetime.strptime(str(s).strip()[:10],'%Y-%m-%d').date()
def canonical_sha(o):return hashlib.sha256(json.dumps(o,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def norm_type(s):return re.sub(r'[^a-z0-9]+','_',str(s).strip().lower()).strip('_')
def truthy(v):return str(v).strip().lower() in TRUTHY

def position_group(pos):
    s=str(pos).strip().lower()
    if 'goalkeeper' in s:return 'goalkeeper'
    if 'back' in s or 'defender' in s or 'defence' in s:return 'defense'
    if 'midfield' in s:return 'midfield'
    if 'winger' in s or 'forward' in s or 'striker' in s:return 'attack'
    return 'unknown'

def load_market_rows(market_dir,covreg):
    rows=[]
    for p in sorted(Path(market_dir).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}&hdr:raise RuntimeError('result columns in market input')
            for r in rd:
                if r.get('Season') not in covreg['football_data']['seasons'] or r.get('Div') not in covreg['football_data']['divisions']:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in covreg['football_data']['market_requirement']):continue
                q=devig3(r['AvgCH'],r['AvgCD'],r['AvgCA'])
                z=dict(r);z['qclose']=q.tolist();z['identity']=identity(r);z['dt']=parse_dt(r['Date'],r['Time']);rows.append(z)
    rows.sort(key=lambda r:(parse_fd_date(r['Date']),r['Div'],r['HomeTeam'],r['AwayTeam']))
    return rows

def rebuild_mapping(market_dir,games_path,lineups_path,covreg):
    games,by_date,starters,complete,types,lineup_rows=load_transfermarkt(games_path,lineups_path,covreg)
    fd=load_market_rows(market_dir,covreg);used=set();matched=[]
    for r in fd:
        res=choose(r,by_date,covreg,used)
        if res is None:continue
        g,off,pair=res;used.add(g['game_id'])
        if g['game_id'] not in complete:continue
        matched.append({'identity':r['identity'],'season':r['Season'],'div':r['Div'],'dt':r['dt'],'qclose':r['qclose'],'tm_game_id':g['game_id'],'tm_date':g['date'],'home_club_id':g['home_club_id'],'away_club_id':g['away_club_id']})
    return matched

def load_all_history(games_path,lineups_path):
    games={}
    with Path(games_path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
        forbidden={'home_club_goals','away_club_goals','aggregate'}
        if forbidden&hdr:raise RuntimeError('goal/result-like columns in sanitized games history')
        need={'game_id','competition_id','season','date','home_club_id','away_club_id','home_club_name','away_club_name','home_club_manager_name','away_club_manager_name'}
        if not need<=hdr:raise RuntimeError(f'games history missing {sorted(need-hdr)}')
        for r in rd:
            gid=str(r['game_id']).strip();games[gid]={'game_id':gid,'date':parse_tm_date(r['date']),'home_club_id':str(r['home_club_id']).strip(),'away_club_id':str(r['away_club_id']).strip(),'home_manager':str(r.get('home_club_manager_name','')).strip(),'away_manager':str(r.get('away_club_manager_name','')).strip()}
    raw=defaultdict(dict);captain={}
    position_counts=Counter()
    with Path(lineups_path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);need={'game_id','player_id','club_id','type','position','team_captain'};hdr=set(rd.fieldnames or [])
        if not need<=hdr:raise RuntimeError(f'lineups history missing {sorted(need-hdr)}')
        for r in rd:
            if 'starting' not in norm_type(r['type']):continue
            gid=str(r['game_id']).strip()
            if gid not in games:continue
            club=str(r['club_id']).strip();pid=str(r['player_id']).strip();pos=str(r.get('position','')).strip();raw[(gid,club)][pid]=pos;position_counts[position_group(pos)]+=1
            if truthy(r.get('team_captain','')):captain[(gid,club)]=pid
    records=defaultdict(list);by_gid_club={}
    for gid,g in games.items():
        for club,away,manager in ((g['home_club_id'],False,g['home_manager']),(g['away_club_id'],True,g['away_manager'])):
            xi=raw.get((gid,club),{})
            if len(xi)!=11:continue
            rec={'game_id':gid,'club_id':club,'date':g['date'],'away':away,'manager':manager,'starters':xi,'captain':captain.get((gid,club))}
            records[club].append(rec);by_gid_club[(gid,club)]=rec
    for club in records:records[club].sort(key=lambda r:(r['date'],r['game_id']))
    return records,by_gid_club,position_counts

def group_set(rec,group):return {p for p,pos in rec['starters'].items() if position_group(pos)==group}
def unit_change(cur,last,group):
    a,b=group_set(cur,group),group_set(last,group);den=max(len(a),len(b),1)
    return 1.0-len(a&b)/den if (a or b) else 0.0

def team_features(cur,history):
    prior=[r for r in history if r['date']<cur['date']]
    if len(prior)<3:return None
    last=prior[-1];window=prior[-10:];n=len(window);counts=Counter();latest_pos={}
    for r in window:
        for p,pos in r['starters'].items():counts[p]+=1;latest_pos[p]=pos
    core={p for p,c in counts.items() if c/n>=0.60};missing=core-set(cur['starters']);core_attack={p for p in core if position_group(latest_pos.get(p,''))=='attack'}
    curset,lastset=set(cur['starters']),set(last['starters']);xi_change=1-len(curset&lastset)/11.0
    cg=group_set(cur,'goalkeeper');lg=group_set(last,'goalkeeper');gk_changed=float(bool(cg and lg and cg!=lg))
    cap_changed=float(cur.get('captain') is not None and last.get('captain') is not None and cur['captain']!=last['captain'])
    mgr_changed=float(bool(cur.get('manager') and last.get('manager') and cur['manager']!=last['manager']))
    core_abs=len(missing)/max(len(core),1);core_att_abs=len(missing&core_attack)/max(len(core_attack),1) if core_attack else 0.0
    rest=min(14,max(0,(cur['date']-last['date']).days))/14.0
    cut=cur['date']-timedelta(days=14);recent=[r for r in prior if r['date']>=cut];games14=min(5,len(recent))/5.0
    load=sum(len(curset&set(r['starters'])) for r in recent)/11.0;load=min(5.0,load)/5.0
    away=0
    for r in reversed(prior):
        if not r['away']:break
        away+=1
        if away>=3:break
    consecutive=away/3.0
    x=[xi_change,gk_changed,unit_change(cur,last,'defense'),unit_change(cur,last,'midfield'),unit_change(cur,last,'attack'),cap_changed,mgr_changed,core_abs,core_att_abs,rest,games14,load,consecutive]
    audit={'prior_count':len(prior),'max_prior_date':str(last['date']),'unknown_current':sum(position_group(p)=='unknown' for p in cur['starters'].values()),'gk_current':len(cg)}
    return x,audit

def build_feature_rows(mapped,records,by_gid_club):
    out=[];audit={'rows_checked':0,'leakage_violations':0,'unknown_current_positions':0,'current_position_slots':0,'missing_goalkeeper_rows':0,'history_ineligible_rows':0}
    for m in mapped:
        hcur=by_gid_club.get((m['tm_game_id'],m['home_club_id']));acur=by_gid_club.get((m['tm_game_id'],m['away_club_id']))
        if hcur is None or acur is None:continue
        hf=team_features(hcur,records[m['home_club_id']]);af=team_features(acur,records[m['away_club_id']])
        if hf is None or af is None:audit['history_ineligible_rows']+=1;continue
        hx,ha=hf;ax,aa=af;audit['rows_checked']+=1;audit['unknown_current_positions']+=ha['unknown_current']+aa['unknown_current'];audit['current_position_slots']+=22;audit['missing_goalkeeper_rows']+=int(ha['gk_current']!=1 or aa['gk_current']!=1)
        td=m['tm_date'];maxp=max(datetime.strptime(ha['max_prior_date'],'%Y-%m-%d').date(),datetime.strptime(aa['max_prior_date'],'%Y-%m-%d').date())
        if not maxp<td:audit['leakage_violations']+=1
        q=np.array(m['qclose'],dtype=float);base=[abs(float(q[0]-q[2])),entropy(q)];full=base+hx+ax
        if len(full)!=28:raise RuntimeError(f'feature dimension {len(full)}')
        z=dict(m);z.update({'xbase':base,'xfull':full});out.append(z)
    return out,audit

def read_training_labels(raw_dir,ids):
    labels={};access=0
    for p in sorted(Path(raw_dir).glob('*.csv')):
        season=p.stem.split('_',1)[0]
        if season=='2526':continue
        with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f)
            for r in rd:
                z=dict(r);z['Season']=season;ident=identity(z)
                if ident not in ids:continue
                v=str(r.get('FTR','')).strip()
                if v not in {'H','D','A'}:raise RuntimeError(f'bad training label {ident}')
                labels[ident]={'H':0,'D':1,'A':2}[v];access+=1
    if len(labels)!=len(ids):raise RuntimeError(f'training labels {len(labels)} != ids {len(ids)}')
    return labels,access

def read_fixed_labels(raw_dir,ids):
    labels={};access=0
    for p in sorted(Path(raw_dir).glob('2526_*.csv')):
        with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f)
            for r in rd:
                z=dict(r);z['Season']='2526';ident=identity(z)
                if ident not in ids:continue
                v=str(r.get('FTR','')).strip()
                if v not in {'H','D','A'}:raise RuntimeError(f'bad fixed label {ident}')
                labels[ident]={'H':0,'D':1,'A':2}[v];access+=1
    if len(labels)!=len(ids):raise RuntimeError(f'fixed labels {len(labels)} != ids {len(ids)}')
    return labels,access

def standardize_fit(X):
    mean=X.mean(axis=0);std=X.std(axis=0);std=np.where(std<1e-12,1.0,std);return (X-mean)/std,mean,std

def fit_offset(X,y,offset,l2,max_iter=80,tol=1e-8):
    Z=np.column_stack([np.ones(len(X)),X]);beta=np.zeros(Z.shape[1]);pen=np.zeros_like(beta);pen[1:]=float(l2);conv=False;dm=None
    for it in range(1,max_iter+1):
        eta=np.asarray(offset)+Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None);grad=Z.T@(p-y)+pen*beta;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-10;delta=np.linalg.solve(H,grad);beta-=delta;dm=float(np.max(np.abs(delta)))
        if dm<tol:conv=True;break
    return beta,{'iterations':it,'converged':conv,'coefficient_delta_max':dm}
def pred_offset(X,offset,beta):
    Z=np.column_stack([np.ones(len(X)),X]);return 1/(1+np.exp(-np.clip(np.asarray(offset)+Z@beta,-40,40)))
def threeway(q,pd):
    pd=clip(pd);h,a=float(q[0]),float(q[2]);s=h+a;return np.array([(1-pd)*h/s,pd,(1-pd)*a/s])
def prob_metrics(probs,actual):
    ll=br=rps=0.
    for p,y in zip(probs,actual):
        one=np.zeros(3);one[y]=1;ll-=math.log(max(float(p[y]),1e-15));br+=float(((p-one)**2).sum());rps+=0.5*((float(p[0]-one[0]))**2+(float(p[0]+p[1]-one[0]-one[1]))**2)
    n=len(actual);return {'rows':n,'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def binary_ll(pd,actual):
    y=np.array([1. if x==1 else 0. for x in actual]);p=np.clip(np.asarray(pd),1e-15,1-1e-15);return float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p))))
def auc_draw(pd,actual):
    pos=[float(p) for p,y in zip(pd,actual) if y==1];neg=[float(p) for p,y in zip(pd,actual) if y!=1]
    if not pos or not neg:return None
    w=0.
    for a in pos:
        for b in neg:w+=1. if a>b else .5 if a==b else 0.
    return w/(len(pos)*len(neg))
def decision_metrics(pred,actual):
    n=len(actual);hits=sum(p==y for p,y in zip(pred,actual));dp=sum(p==1 for p in pred);ad=sum(y==1 for y in actual);tp=sum(p==1 and y==1 for p,y in zip(pred,actual));prec=tp/dp if dp else 0.;rec=tp/ad if ad else 0.;f1=2*prec*rec/(prec+rec) if prec+rec else 0.
    return {'rows':n,'hits':hits,'accuracy':hits/n,'predicted_draw_count':dp,'actual_draw_count':ad,'draw_true_positive':tp,'draw_precision':prec,'draw_recall':rec,'draw_f1':f1}
def eval_model(rows,labels,key,l2s):
    n=len(rows);nf=int(n*.70);nv=int(n*.15);fit=rows[:nf];val=rows[nf:nf+nv];policy=rows[nf+nv:]
    Xf=np.array([r[key] for r in fit]);Xv=np.array([r[key] for r in val]);yf=np.array([1. if labels[r['identity']]==1 else 0. for r in fit]);actual=[labels[r['identity']] for r in val];offf=np.array([logit(r['qclose'][1]) for r in fit]);offv=np.array([logit(r['qclose'][1]) for r in val]);Xs,mean,std=standardize_fit(Xf);out=[]
    for l2 in l2s:
        beta,diag=fit_offset(Xs,yf,offf,l2)
        if not diag['converged']:raise RuntimeError(f'nonconverged {key} {l2}')
        pd=pred_offset((Xv-mean)/std,offv,beta);p3=[threeway(r['qclose'],p) for r,p in zip(val,pd)];pm=prob_metrics(p3,actual);out.append({'l2':float(l2),'diag':diag,'HDA':pm,'binary_draw_log_loss':binary_ll(pd,actual),'draw_auc':auc_draw(pd,actual),'beta':beta.tolist(),'mean':mean.tolist(),'std':std.tolist()})
    sel=min(out,key=lambda x:(x['HDA']['log_loss'],x['binary_draw_log_loss'],x['l2']))
    return {'fit':fit,'validation':val,'policy':policy,'candidates':out,'selected':sel}
def predict_with(model,rows,key):
    X=np.array([r[key] for r in rows]);mean=np.array(model['mean']);std=np.array(model['std']);off=np.array([logit(r['qclose'][1]) for r in rows]);pd=pred_offset((X-mean)/std,off,np.array(model['beta']));p3=[threeway(r['qclose'],p) for r,p in zip(rows,pd)];return pd,p3
def refit(rows,labels,key,l2):
    X=np.array([r[key] for r in rows]);y=np.array([1. if labels[r['identity']]==1 else 0. for r in rows]);off=np.array([logit(r['qclose'][1]) for r in rows]);Xs,mean,std=standardize_fit(X);beta,diag=fit_offset(Xs,y,off,l2)
    if not diag['converged']:raise RuntimeError('refit nonconverged')
    return {'l2':float(l2),'beta':beta.tolist(),'mean':mean.tolist(),'std':std.tolist(),'diag':diag}
def per_div_ll(rows,probs,labels):
    g=defaultdict(list)
    for r,p in zip(rows,probs):g[r['div']].append((p,labels[r['identity']]))
    return {k:prob_metrics([x[0] for x in v],[x[1] for x in v])['log_loss'] for k,v in g.items()}
def market_probs(rows):return [np.array(r['qclose'],dtype=float) for r in rows]
def market_pred(rows):return [int(np.argmax(np.array(r['qclose']))) for r in rows]
def policy_predictions(rows,p3,threshold):
    pred=[]
    for r,p in zip(rows,p3):
        score=float(p[1]-max(p[0],p[2]))
        if score>=threshold:pred.append(1)
        else:pred.append(0 if r['qclose'][0]>=r['qclose'][2] else 2)
    return pred
def choose_policy(rows,p3,labels,coverages):
    actual=[labels[r['identity']] for r in rows];market=decision_metrics(market_pred(rows),actual);scores=[float(p[1]-max(p[0],p[2])) for p in p3];prev=sum(y==1 for y in actual)/len(actual);lanes=[]
    order=sorted(range(len(rows)),key=lambda i:(-scores[i],rows[i]['identity']))
    for c in coverages:
        k=max(1,int(round(float(c)*len(rows))));thr=scores[order[k-1]];pred=policy_predictions(rows,p3,thr);m=decision_metrics(pred,actual);eligible=m['accuracy']>market['accuracy'] and m['draw_precision']>=prev+.05 and m['draw_f1']>=.15;lanes.append({'coverage_requested':float(c),'threshold':thr,'eligible':eligible,'metrics':m})
    elig=[x for x in lanes if x['eligible']]
    sel=max(elig,key=lambda x:(x['metrics']['accuracy'],x['metrics']['draw_f1'],x['metrics']['draw_precision'],-x['coverage_requested'])) if elig else None
    return {'market':market,'draw_prevalence':prev,'lanes':lanes,'selected':sel}
def bootstrap_delta(pred,market,actual,B,seed):
    rng=random.Random(seed);n=len(actual);vals=[]
    dh=[int(p==y)-int(m==y) for p,m,y in zip(pred,market,actual)]
    for _ in range(B):vals.append(sum(dh[rng.randrange(n)] for _ in range(n))/n)
    vals.sort();return {'samples':B,'mean':sum(vals)/B,'p05':vals[int(.05*(B-1))],'p95':vals[int(.95*(B-1))]}

def write_stop(outdir,status,base,freeze):
    base['status']=status;base['holdout_labels_accessed']=0;Path(outdir).mkdir(parents=True,exist_ok=True);Path(outdir,'freeze_receipt_r39i.json').write_text(json.dumps(freeze,indent=2));Path(outdir,'r39i_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def self_test():
    X=np.array([[0.],[1.],[2.],[3.]]);y=np.array([0.,0.,1.,1.]);off=np.zeros(4);Xs,m,s=standardize_fit(X);b,d=fit_offset(Xs,y,off,1.);assert d['converged'];assert position_group('Centre-Back')=='defense';assert position_group('Centre-Forward')=='attack';print('PASS_R39I_SELF_TEST')

def main():
    if '--self-test' in sys.argv:self_test();return
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',required=True);ap.add_argument('--coverage-registration',required=True);ap.add_argument('--market-dir',required=True);ap.add_argument('--raw-dir',required=True);ap.add_argument('--games',required=True);ap.add_argument('--lineups',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    pre=json.loads(Path(a.prereg).read_text());cov=json.loads(Path(a.coverage_registration).read_text());mapped=rebuild_mapping(a.market_dir,a.games,a.lineups,cov)
    hold=pre['source_binding']['holdout_season'];pre_m=[r for r in mapped if r['season']!=hold];hp=[r for r in mapped if r['season']==hold]
    if len(pre_m)!=pre['source_binding']['complete_preholdout_rows'] or len(hp)!=pre['source_binding']['holdout_pool_rows']:raise RuntimeError(f'mapping drift {len(pre_m)} {len(hp)}')
    fixed=sorted(hp,key=lambda r:htxt(f"{pre['source_binding']['fixed100_seed']}|{r['identity']}"))[:pre['source_binding']['fixed100_rows']];sha=set_sha([r['identity'] for r in fixed])
    if sha!=pre['source_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {sha}')
    records,by_gid_club,pos_counts=load_all_history(a.games,a.lineups);features,lag_audit=build_feature_rows(mapped,records,by_gid_club);feat={r['identity']:r for r in features};pre_rows=sorted([r for r in features if r['season']!=hold],key=lambda r:(r['dt'],r['identity']));fixed_ids={r['identity'] for r in fixed};missing_fixed=sorted(fixed_ids-set(feat))
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_binding':pre['source_binding'],'mapped_complete_preholdout':len(pre_m),'mapped_complete_2526':len(hp),'feature_eligible_preholdout':len(pre_rows),'fixed100_feature_ineligible_count':len(missing_fixed),'position_group_counts':dict(pos_counts),'strict_lag_audit':lag_audit,'training_labels_accessed':0,'holdout_labels_before_freeze':0,'hard_limits':pre['hard_limits']}
    prefreeze={'fixed100_identity_sha256':sha,'fixed100_rows':len(fixed),'holdout_labels_accessed_before_freeze':0,'strict_lag_audit':lag_audit,'model_frozen':False,'policy_frozen':False}
    if lag_audit['leakage_violations'] or len(pre_rows)<pre['chronological_design']['minimum_eligible_preholdout_rows_after_strict_history'] or missing_fixed:
        write_stop(a.out_dir,'STOP_R39I_BEFORE_LABELS_FEATURE_OR_LAG_GATE',base,prefreeze);return
    labels,ta=read_training_labels(a.raw_dir,{r['identity'] for r in pre_rows});base['training_labels_accessed']=ta;l2s=pre['models']['l2_candidates_each'];bres=eval_model(pre_rows,labels,'xbase',l2s);fres=eval_model(pre_rows,labels,'xfull',l2s)
    val=bres['validation'];actual=[labels[r['identity']] for r in val];marketp=market_probs(val);marketm=prob_metrics(marketp,actual);marketdll=binary_ll([p[1] for p in marketp],actual);marketauc=auc_draw([p[1] for p in marketp],actual);bsel=bres['selected'];fsel=fres['selected'];bpd,bp3=predict_with(bsel,val,'xbase');fpd,fp3=predict_with(fsel,val,'xfull');bdiv=per_div_ll(val,bp3,labels);fdiv=per_div_ll(val,fp3,labels);wins=sum(fdiv.get(d,1e9)<bdiv.get(d,-1e9) for d in sorted(set(bdiv)&set(fdiv)))
    gates={'HDA_vs_market':fsel['HDA']['log_loss']<marketm['log_loss'],'draw_LL_vs_market':fsel['binary_draw_log_loss']<marketdll,'HDA_vs_context':fsel['HDA']['log_loss']<bsel['HDA']['log_loss'],'draw_LL_vs_context':fsel['binary_draw_log_loss']<bsel['binary_draw_log_loss'],'AUC_vs_context':fsel['draw_auc']>bsel['draw_auc'],'division_wins_ge3':wins>=3};validation={'rows':len(val),'raw_market':{**marketm,'binary_draw_log_loss':marketdll,'draw_auc':marketauc},'market_context_selected':{k:bsel[k] for k in ('l2','HDA','binary_draw_log_loss','draw_auc')},'full_selected':{k:fsel[k] for k in ('l2','HDA','binary_draw_log_loss','draw_auc')},'per_div_context_HDA_LL':bdiv,'per_div_full_HDA_LL':fdiv,'division_wins':wins,'gates':gates,'overall_pass':all(gates.values())};base['validation']=validation
    if not validation['overall_pass']:
        prefreeze.update({'validation':validation,'model_frozen':False});write_stop(a.out_dir,pre['validation_gate_all_required']['if_fail'],base,prefreeze);return
    fv=bres['fit']+bres['validation'];bmodel=refit(fv,labels,'xbase',bsel['l2']);fmodel=refit(fv,labels,'xfull',fsel['l2']);policy_rows=bres['policy'];_,pp3=predict_with(fmodel,policy_rows,'xfull');pol=choose_policy(policy_rows,pp3,labels,pre['policy']['coverages']);base['policy']=pol
    if pol['selected'] is None:
        prefreeze.update({'validation':validation,'model_frozen':True,'model_hash':canonical_sha({'baseline':bmodel,'full':fmodel}),'policy_frozen':False});write_stop(a.out_dir,pre['policy']['if_no_lane'],base,prefreeze);return
    threshold=pol['selected']['threshold'];freeze={'fixed100_identity_sha256':sha,'fixed100_rows':len(fixed),'holdout_labels_accessed_before_freeze':0,'strict_lag_audit':lag_audit,'validation':validation,'model_frozen':True,'model_hash':canonical_sha({'baseline':bmodel,'full':fmodel}),'policy_frozen':True,'policy_threshold':threshold,'policy_coverage_requested':pol['selected']['coverage_requested']};Path(a.out_dir).mkdir(parents=True,exist_ok=True);Path(a.out_dir,'freeze_receipt_r39i.json').write_text(json.dumps(freeze,indent=2))
    fixed_rows=sorted([feat[i] for i in fixed_ids],key=lambda r:(r['dt'],r['identity']));flabels,ha=read_fixed_labels(a.raw_dir,fixed_ids);actual=[flabels[r['identity']] for r in fixed_rows];bpd,bp3=predict_with(bmodel,fixed_rows,'xbase');fpd,fp3=predict_with(fmodel,fixed_rows,'xfull');mp=market_probs(fixed_rows);mm=prob_metrics(mp,actual);bm=prob_metrics(bp3,actual);fm=prob_metrics(fp3,actual);marketdll=binary_ll([p[1] for p in mp],actual);bdll=binary_ll(bpd,actual);fdll=binary_ll(fpd,actual);bauc=auc_draw(bpd,actual);fauc=auc_draw(fpd,actual);pred=policy_predictions(fixed_rows,fp3,threshold);mpred=market_pred(fixed_rows);dm=decision_metrics(pred,actual);mdm=decision_metrics(mpred,actual);boot=bootstrap_delta(pred,mpred,actual,pre['holdout']['bootstrap_samples'],pre['holdout']['bootstrap_seed']);hg=pre['holdout']['gate_all_required'];gates={'decision_accuracy':dm['accuracy']>mdm['accuracy'],'draw_precision':dm['draw_precision']>=hg['draw_precision_min'],'draw_recall':dm['draw_recall']>=hg['draw_recall_min'],'draw_f1':dm['draw_f1']>=hg['draw_f1_min'],'draw_count':hg['predicted_draw_count_min']<=dm['predicted_draw_count']<=hg['predicted_draw_count_max'],'HDA_nonworse_market':fm['log_loss']<=mm['log_loss'],'HDA_better_context':fm['log_loss']<bm['log_loss'],'draw_LL_better_context':fdll<bdll,'AUC_better_context':fauc>bauc};passed=all(gates.values());base.update({'status':pre['holdout']['pass_status'] if passed else pre['holdout']['fail_status'],'holdout_labels_accessed':ha,'holdout':{'rows':100,'raw_market':{**mm,'binary_draw_log_loss':marketdll,'decision':mdm},'market_context':{**bm,'binary_draw_log_loss':bdll,'draw_auc':bauc},'full_discrete_shock':{**fm,'binary_draw_log_loss':fdll,'draw_auc':fauc,'decision':dm},'gates':gates,'overall_pass':passed,'bootstrap_accuracy_delta_vs_market':boot}});Path(a.out_dir,'r39i_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'holdout_labels_accessed':ha,'validation':validation,'policy_selected':pol['selected'],'holdout':base['holdout']},indent=2))
if __name__=='__main__':main()
