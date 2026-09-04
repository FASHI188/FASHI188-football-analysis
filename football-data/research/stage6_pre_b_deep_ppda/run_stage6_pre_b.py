from __future__ import annotations
import argparse, json, math, pathlib, sqlite3
from collections import defaultdict
import common

EPS=1e-12

def logit(p):
    p=min(max(float(p),1e-9),1-1e-9); return math.log(p/(1-p))
def sigmoid(z):
    z=max(-40.0,min(40.0,float(z))); return 1/(1+math.exp(-z))

class State:
    __slots__=('deep','press','n')
    def __init__(self): self.deep=0.0; self.press=0.0; self.n=0
    def update(self,deep,press,alpha):
        if self.n==0: self.deep=float(deep); self.press=float(press)
        else:
            self.deep=(1-alpha)*self.deep+alpha*float(deep)
            self.press=(1-alpha)*self.press+alpha*float(press)
        self.n+=1

def load_games(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in common.LEAGUES)
    rows=[dict(r) for r in con.execute(f"select fid,h_id,a_id,date,league,season,h_deep,a_deep,h_ppda,a_ppda from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,fid",common.LEAGUES)]
    con.close(); return rows

def make_snapshots(db,wanted,half=16.0):
    alpha=1-math.exp(math.log(0.5)/half)
    games=load_games(db); bytime=defaultdict(list)
    for g in games: bytime[str(g['date'])].append(g)
    states=defaultdict(dict); snaps={}; stats={'games':len(games),'target_snapshots':0,'active':0,'missing_team_state':0,'invalid_process_value':0}
    for dt in sorted(bytime):
        batch=sorted(bytime[dt],key=lambda x:int(x['fid']))
        for g in batch:
            fid=f"understat:{int(g['fid'])}"
            if fid not in wanted: continue
            league=str(g['league']); h=str(int(g['h_id'])); a=str(int(g['a_id'])); d=states[league]
            hs=d.get(h); aws=d.get(a); stats['target_snapshots']+=1
            if hs is None or aws is None or hs.n<1 or aws.n<1:
                snaps[fid]={'active':False,'reason':'missing_team_state'}; stats['missing_team_state']+=1; continue
            vals=[s for s in d.values() if s.n>=1]
            md=sum(s.deep for s in vals)/len(vals); mp=sum(s.press for s in vals)/len(vals)
            sd=math.sqrt(sum((s.deep-md)**2 for s in vals)/len(vals)); sp=math.sqrt(sum((s.press-mp)**2 for s in vals)/len(vals))
            if sd<=1e-9 or sp<=1e-9:
                snaps[fid]={'active':False,'reason':'zero_league_sd'}; continue
            hp=0.5*((hs.deep-md)/sd)+0.5*((hs.press-mp)/sp)
            ap=0.5*((aws.deep-md)/sd)+0.5*((aws.press-mp)/sp)
            snaps[fid]={'active':True,'home_process':hp,'away_process':ap,'edge':hp-ap,'home_n':hs.n,'away_n':aws.n,'league_state_n':len(vals)}; stats['active']+=1
        for g in batch:
            try:
                hd=math.log1p(max(0.0,float(g['h_deep']))); ad=math.log1p(max(0.0,float(g['a_deep'])))
                hp=-math.log(max(EPS,float(g['h_ppda']))); ap=-math.log(max(EPS,float(g['a_ppda'])))
            except Exception:
                stats['invalid_process_value']+=1; continue
            d=states[str(g['league'])]
            d.setdefault(str(int(g['h_id'])),State()).update(hd,hp,alpha)
            d.setdefault(str(int(g['a_id'])),State()).update(ad,ap,alpha)
    return snaps,stats

def predict(base,snap,coef=0.10):
    if not snap or not snap.get('active'): return list(map(float,base)),False
    d=float(base[1]); denom=float(base[0])+float(base[2])
    if denom<=0: return list(map(float,base)),False
    hside=float(base[0])/denom; z=logit(hside)+coef*max(-3.0,min(3.0,float(snap['edge']))); q=sigmoid(z)
    p=[(1-d)*q,d,(1-d)*(1-q)]
    return p,True

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    if c['status']!='FROZEN_BEFORE_CONSUMED_2020_2022_SCORING': raise common.Stage84Error('contract status drift')
    b=common.build_frozen_baseline(a,'s84b')
    common.write_json(a.out/'row_receipt.json',b['row_receipt']); common.write_json(a.out/'baseline_receipt.json',{'prediction_n':len(b['bmap']),'segments':b['baseline_receipt']}); common.write_json(a.out/'process_receipt.json',b['process_receipt'])
    wanted={r['fixture_id'] for r in b['rows'] if r['season'] in (2020,2021,2022)}
    snaps,srec=make_snapshots(a.db,wanted,float(c['mechanism']['half_life_matches'])); common.write_json(a.out/'deep_ppda_process_receipt.json',srec)
    cand={}; active={}
    coef=float(c['mechanism']['bridge_coefficient'])
    for r in b['rows']:
        if r['season'] not in (2020,2021,2022) or r['fixture_id'] not in b['bmap']: continue
        p,on=predict(b['bmap'][r['fixture_id']],snaps.get(r['fixture_id']),coef); cand[r['fixture_id']]=p; active[r['fixture_id']]=on
    ev,_=common.score_axis(b['rows'],b['bmap'],b['bmats'],cand,c,active)
    ev['coverage_expected']=int(c['gates']['coverage_n_expected']); ev['prediction_coverage_n']=len(cand)
    con=sqlite3.connect(str(a.db)); qs=','.join('?' for _ in common.LEAGUES); raw_complete=con.execute(f"select count(*) from general_game_stats where league in ({qs}) and season between 2020 and 2022 and h_deep is not null and a_deep is not null and h_ppda is not null and a_ppda is not null",common.LEAGUES).fetchone()[0]; con.close()
    ev['raw_four_field_complete_n']=raw_complete; ev['raw_coverage_pass']=raw_complete==int(c['gates']['coverage_n_expected'])
    ev['coverage_pass']=ev['prediction_coverage_n']==int(c['gates']['coverage_n_expected']) and ev['raw_coverage_pass']
    status=c['terminal']['coverage_fail'] if not ev['coverage_pass'] else (c['terminal']['development_pass'] if ev['all_pass'] else c['terminal']['development_fail'])
    final={'schema_version':'football3-stage6-pre-b-final-v1','status':status,'research_only':True,'post_view_development':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_weights_changed':False,'evaluation':ev}
    common.write_json(a.out/'development_score.json',ev); common.write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
