#!/usr/bin/env python3
from __future__ import annotations
import argparse, bisect, importlib.util, json, math, pathlib, re, sqlite3, statistics, sys

TOL=1e-12
BIG5={'EPL','La liga','Bundesliga','Serie A','Ligue 1'}
class ShotFanoError(RuntimeError): pass

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
        for a in range(n): out[h+a]+=float(m[h][a])
    return out

def expected_goals(m):
    mh=ma=0.0
    for h,row in enumerate(m):
        for a,x in enumerate(row):
            mh+=h*float(x); ma+=a*float(x)
    return mh,ma

def poisson_pmf(mu,n):
    if n<2: raise ShotFanoError('support too small')
    if mu<=0: return [1.0]+[0.0]*(n-1)
    vals=[math.exp(-mu)]
    for k in range(1,n-1): vals.append(vals[-1]*mu/k)
    tail=max(0.0,1.0-sum(vals)); vals.append(tail)
    s=sum(vals)
    return [x/s for x in vals]

def nb_pmf(mu,fano,n):
    if not math.isfinite(mu) or mu<0 or not math.isfinite(fano) or fano<1: raise ShotFanoError(('invalid moments',mu,fano))
    if mu<=0: return [1.0]+[0.0]*(n-1)
    if fano<=1.0+1e-12: return poisson_pmf(mu,n)
    k=mu/(fano-1.0); p=k/(k+mu); q=1.0-p
    p0=math.exp(k*math.log(p)); vals=[p0]
    for x in range(0,n-2): vals.append(vals[-1]*(k+x)/(x+1)*q)
    tail=max(0.0,1.0-sum(vals)); vals.append(tail)
    s=sum(vals)
    if s<=0 or any((not math.isfinite(x)) or x<0 for x in vals): raise ShotFanoError('invalid NB pmf')
    return [x/s for x in vals]

def outcome_idx(h,a): return 0 if h>a else (1 if h==a else 2)

def iproject(reference,total_target,hda_target,tol=1e-13,max_iter=10000):
    n=len(reference)
    if n<2 or any(len(r)!=n for r in reference): raise ShotFanoError('reference not square')
    q=[[float(x) for x in row] for row in reference]
    if any((not math.isfinite(x)) or x<=0 for row in q for x in row): raise ShotFanoError('reference must be finite positive')
    st=sum(sum(r) for r in q); q=[[x/st for x in r] for r in q]
    total_target=list(map(float,total_target)); hda_target=list(map(float,hda_target))
    if abs(sum(total_target)-1)>1e-10 or abs(sum(hda_target)-1)>1e-10: raise ShotFanoError('target normalization')
    for it in range(1,max_iter+1):
        cur=marginal_total(q)
        for t,want in enumerate(total_target):
            have=cur[t]
            if want>0 and have<=0: raise ShotFanoError(('infeasible total slice',t,want,have))
            fac=0.0 if want==0 else want/have
            for h in range(n):
                a=t-h
                if 0<=a<n: q[h][a]*=fac
        curh=integrate(q)
        for o,want in enumerate(hda_target):
            have=curh[o]
            if want>0 and have<=0: raise ShotFanoError(('infeasible outcome',o,want,have))
            fac=0.0 if want==0 else want/have
            for h in range(n):
                for a in range(n):
                    if outcome_idx(h,a)==o: q[h][a]*=fac
        terr=max(abs(x-y) for x,y in zip(marginal_total(q),total_target))
        herr=max(abs(x-y) for x,y in zip(integrate(q),hda_target))
        if max(terr,herr)<=tol:
            s=sum(sum(r) for r in q); q=[[x/s for x in r] for r in q]
            return q,{'iterations':it,'total_error':terr,'hda_error':herr}
    raise ShotFanoError(('iprojection did not converge',terr,herr,max_iter))

def norm_team(x): return re.sub(r'[^a-z0-9]+','',str(x).casefold())
def pick(row,names):
    for k in names:
        if k in row and row[k] not in (None,''): return row[k]
    return None

def maybe_int(x):
    try: return int(x)
    except Exception: return None

def load_games(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    sql='''
    WITH ev AS (
      SELECT match_id,
             SUM(CASE WHEN h_a='h' THEN xG ELSE 0 END) AS hx,
             SUM(CASE WHEN h_a='h' THEN xG*(1.0-xG) ELSE 0 END) AS hb,
             SUM(CASE WHEN h_a='a' THEN xG ELSE 0 END) AS ax,
             SUM(CASE WHEN h_a='a' THEN xG*(1.0-xG) ELSE 0 END) AS ab
      FROM game_events GROUP BY match_id
    )
    SELECT g.id,g.fid,g.h_id,g.a_id,g.date,g.league,g.season,g.team_h,g.team_a,
           COALESCE(ev.hx,0.0) hx,COALESCE(ev.hb,0.0) hb,
           COALESCE(ev.ax,0.0) ax,COALESCE(ev.ab,0.0) ab
      FROM general_game_stats g JOIN ev ON ev.match_id=g.id
     WHERE g.league IN ('EPL','La liga','Bundesliga','Serie A','Ligue 1')
     ORDER BY g.date,g.id
    '''
    games=[dict(r) for r in con.execute(sql)]; con.close()
    if not games: raise ShotFanoError('no frozen games')
    return games

class PrefixSeries:
    def __init__(self,entries):
        es=sorted(entries,key=lambda z:z[0]); self.d=[z[0] for z in es]; self.n=[]; self.sl=[]; self.sl2=[]; self.sb=[]
        sl=sl2=sb=0.0
        for i,(_,lam,bv) in enumerate(es,1):
            sl+=lam; sl2+=lam*lam; sb+=bv; self.n.append(i); self.sl.append(sl); self.sl2.append(sl2); self.sb.append(sb)
    def summary(self,cutoff):
        j=bisect.bisect_left(self.d,cutoff)-1
        if j<0:return None
        n=self.n[j]
        return n,self.sl[j],self.sl2[j],self.sb[j],self.d[j]

def build_state_indices(games):
    raw={k:{} for k in ('th_atk','ta_atk','th_def','ta_def','lh','la')}; gh=[];ga=[]
    def add(bucket,key,item): bucket.setdefault(key,[]).append(item)
    for g in games:
        dt=g['date']; hi=int(g['h_id']); ai=int(g['a_id']); lg=g['league']; h=(dt,float(g['hx']),float(g['hb'])); a=(dt,float(g['ax']),float(g['ab']))
        add(raw['th_atk'],hi,h); add(raw['th_def'],hi,a); add(raw['ta_atk'],ai,a); add(raw['ta_def'],ai,h); add(raw['lh'],lg,h); add(raw['la'],lg,a); gh.append(h);ga.append(a)
    idx={k:{kk:PrefixSeries(vv) for kk,vv in d.items()} for k,d in raw.items()}; idx['gh']=PrefixSeries(gh);idx['ga']=PrefixSeries(ga)
    return idx

def fano_from_summary(s):
    if not s:return None
    n,sl,sl2,sb,last=s
    if n<2 or sl<=0:return None
    mean=sl/n; var=(sl2-sl*sl/n)/(n-1); var=max(0.0,var); f=(sb/n+var)/mean
    if not math.isfinite(f) or f<=0:return None
    return {'fano':f,'n':n,'mean_lambda':mean,'sample_var_lambda':var,'mean_bernoulli_var':sb/n,'last_source_kickoff':last}

def resolve_source(idx,team_kind,team_id,league,role_kind,cutoff):
    candidates=[('team_role',idx[team_kind].get(int(team_id))),('league_role',idx[role_kind].get(league)),('big5_role',idx['gh' if role_kind=='lh' else 'ga'])]
    for level,series in candidates:
        z=fano_from_summary(series.summary(cutoff) if series else None)
        if z is not None:
            if not z['last_source_kickoff']<cutoff: raise ShotFanoError('chronology violation')
            z['level']=level; return z
    raise ShotFanoError(('no valid source state',team_kind,team_id,league,cutoff))

def fixture_maps(games):
    by_fid={int(g['fid']):g for g in games}; by_id={int(g['id']):g for g in games}; by_full={}; by_day={}
    for g in games:
        k=(int(g['season']),norm_team(g['team_h']),norm_team(g['team_a']),str(g['date'])) ; by_full.setdefault(k,[]).append(g)
        kd=(int(g['season']),norm_team(g['team_h']),norm_team(g['team_a']),str(g['date'])[:10]);by_day.setdefault(kd,[]).append(g)
    return by_fid,by_id,by_full,by_day

def map_row(row,maps):
    by_fid,by_id,by_full,by_day=maps; fid=row['fixture_id']; z=maybe_int(fid)
    if z is not None:
        if z in by_fid:return by_fid[z],'fixture_id=fid'
        if z in by_id:return by_id[z],'fixture_id=id'
    for tok in re.findall(r'\d+',str(fid)):
        z=int(tok)
        cand=[]
        if z in by_fid:cand.append(by_fid[z])
        if z in by_id and by_id[z] not in cand:cand.append(by_id[z])
        if len(cand)==1:return cand[0],'embedded_numeric_id'
    season=int(row['season']); home=pick(row,['team_h','home_team','home','h_team','home_name']); away=pick(row,['team_a','away_team','away','a_team','away_name']); dt=pick(row,['kickoff','date','match_date','datetime','utc_date'])
    if home is not None and away is not None and dt is not None:
        ds=str(dt).replace('T',' ')[:19]; k=(season,norm_team(home),norm_team(away),ds); c=by_full.get(k,[])
        if len(c)==1:return c[0],'team_datetime'
        k=(season,norm_team(home),norm_team(away),ds[:10]); c=by_day.get(k,[])
        if len(c)==1:return c[0],'team_date'
    raise ShotFanoError(('cannot map stage row to frozen Understat fixture',fid,season,sorted(row.keys())))

def state_for_fixture(g,idx):
    cutoff=str(g['date']); lg=g['league']; hi=int(g['h_id']); ai=int(g['a_id'])
    ha=resolve_source(idx,'th_atk',hi,lg,'lh',cutoff); ad=resolve_source(idx,'ta_def',ai,lg,'lh',cutoff)
    aa=resolve_source(idx,'ta_atk',ai,lg,'la',cutoff); hd=resolve_source(idx,'th_def',hi,lg,'la',cutoff)
    fh=(ha['fano']+ad['fano'])/2.0; fa=(aa['fano']+hd['fano'])/2.0
    return max(1.0,fh),max(1.0,fa),{'home_attack':ha,'away_defense':ad,'away_attack':aa,'home_defense':hd}

def target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324):
    params=c4['direction_generator']['frozen_t2_params'];epsilon=c4['projection']['epsilon'];allr=[];pm={};dg={};stats=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap];te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322);q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322);p,pd,st=v324.project_direction_map(te,bmap,q,d,v322,epsilon)
        stats.append({'season':s,'fit':fit,**st});allr.extend(te);pm.update(p);dg.update(pd)
    return allr,pm,dg,stats

def build_candidate(rows,bmats,pmap,games,idx,c):
    maps=fixture_maps(games);mm={}; states={}; map_methods={}; iters=[]
    for r in rows:
        fid=r['fixture_id']; b=bmats[fid]; g,method=map_row(r,maps); map_methods[method]=map_methods.get(method,0)+1
        fh,fa,src=state_for_fixture(g,idx); mh,ma=expected_goals(b); n=len(b); ph=nb_pmf(mh,fh,n);pa=nb_pmf(ma,fa,n)
        ref=[[ph[h]*pa[a] for a in range(n)] for h in range(n)]
        q,rec=iproject(ref,marginal_total(b),pmap[fid],tol=float(c['constraint_projection']['tolerance']),max_iter=int(c['constraint_projection']['max_iterations']))
        mm[fid]=q;iters.append(rec['iterations']);states[fid]={'fano_home_est':(src['home_attack']['fano']+src['away_defense']['fano'])/2.0,'fano_away_est':(src['away_attack']['fano']+src['home_defense']['fano'])/2.0,'fano_home_effective':fh,'fano_away_effective':fa,'mu_home':mh,'mu_away':ma,'source_levels':{k:v['level'] for k,v in src.items()},'source_n':{k:v['n'] for k,v in src.items()}}
    return mm,states,{'mapping_methods':map_methods,'iprojection_iterations_min':min(iters),'iprojection_iterations_mean':sum(iters)/len(iters),'iprojection_iterations_max':max(iters)}

def structural(rows,mm,bm,pmap,states,extra):
    norm=tot=hda=0.0; fs=[];levels={}
    for r in rows:
        fid=r['fixture_id'];q=mm[fid];b=bm[fid]; norm=max(norm,abs(sum(sum(x) for x in q)-1.0));tot=max(tot,max(abs(x-y) for x,y in zip(marginal_total(q),marginal_total(b))));hda=max(hda,max(abs(x-y) for x,y in zip(integrate(q),map(float,pmap[fid]))));s=states[fid];fs.extend([s['fano_home_effective'],s['fano_away_effective']])
        for _,lv in s['source_levels'].items(): levels[lv]=levels.get(lv,0)+1
    return {'chronology_violation_n':0,'matrix_sum_max_abs_error':norm,'total_goals_marginal_max_abs_error':tot,'matrix_to_target_1x2_max_abs_error':hda,'fano_effective_min':min(fs),'fano_effective_mean':sum(fs)/len(fs),'fano_effective_max':max(fs),'source_level_counts':levels,**extra}

def per_season_exact(rows,mm,bm,v32dev):
    return [{'season':s,'n':len(rs),'exact_score_logloss_delta':v32dev.exact_ll(rs,mm)-v32dev.exact_ll(rs,bm)} for s in sorted({r['season'] for r in rows}) for rs in [[r for r in rows if r['season']==s]]]

def evaluate_period(name,seasons,rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx):
    rr,pm,dg,stats=target_seasons(seasons,rows,bmap,snaps,core,c3,c4,v322,v323,v324);bm={r['fixture_id']:bmats[r['fixture_id']] for r in rr};mm,states,extra=build_candidate(rr,bm,pm,games,idx,c);se=v32dev.score_eval(rr,mm,bm,core,c,pm);sd=structural(rr,mm,bm,pm,states,extra);sg=c['hard_gates']['structural']
    checks={'chronology_gate':sd['chronology_violation_n']<=sg['chronology_violation_n_max'],'matrix_sum_gate':sd['matrix_sum_max_abs_error']<=sg['matrix_sum_max_abs_error']+TOL,'total_goals_marginal_gate':sd['total_goals_marginal_max_abs_error']<=sg['total_goals_marginal_max_abs_error']+TOL,'matrix_to_target_hda_gate':sd['matrix_to_target_1x2_max_abs_error']<=sg['matrix_to_target_1x2_max_abs_error']+TOL,'finite_nonnegative_gate':all(math.isfinite(x) and x>=0 for m in mm.values() for row in m for x in row),'projection_convergence_gate':True}
    checks['all_pass']=all(checks.values()) and se['checks']['all_pass']
    return {'name':name,'seasons':list(seasons),'n':len(rr),'direction_stats':stats,'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'score':se,'structural':sd,'structural_checks':checks,'per_season_exact_score':per_season_exact(rr,mm,bm,v32dev),'all_pass':checks['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);c=json.loads(a.contract.read_text());c4=json.loads(a.v324_contract.read_text());c3=json.loads(a.v323_contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or not c['new_information_axis'] or not c['not_a_fourth_transport_family']:raise ShotFanoError('contract drift')
    v324=loadmod('sf_v324',a.v324);v323=loadmod('sf_v323',a.v323);v322=loadmod('sf_v322',a.v322);v32dev=loadmod('sf_v32dev',a.v32dev);a.v32dev_module=v32dev
    core=loadmod('sf_core',a.core);v311=loadmod('sf_v311',a.v311);v31=loadmod('sf_v31',a.v31);usr=loadmod('sf_usr',a.usr1);v2=loadmod('sf_v2',a.v2);xg=loadmod('sf_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec);write_json(a.out/'pre2023_process_receipt.json',procrec)
    games=load_games(a.db);idx=build_state_indices(games)
    sanity=evaluate_period('sanity_2019_2020',(2019,2020),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-score-shape-shot-fano-final-v1','status':c['terminal']['failure'],'reason':'shot_fano_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':False,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True));return
    ev=evaluate_period('evaluation_2021_2022',(2021,2022),rows,bmap,bmats,snaps,core,c3,c4,c,v322,v323,v324,v32dev,games,idx);write_json(a.out/'evaluation_2021_2022.json',ev)
    ok=ev['all_pass']; final={'schema_version':'football3-v3-score-shape-shot-fano-final-v1','status':c['terminal']['success'] if ok else c['terminal']['failure'],'reason':'all_fixed_stage6_shot_fano_gates_pass' if ok else 'shot_fano_failed_2021_2022_evaluation','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'2021_2022_opened':True,'2023_opened':False,'3504_opened':False,'macro_stage_7_started':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False};write_json(a.out/'final_status.json',final);print(json.dumps(final,sort_keys=True))

if __name__=='__main__': main()
