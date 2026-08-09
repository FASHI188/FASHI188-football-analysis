#!/usr/bin/env python3
from __future__ import annotations
import bisect,csv,importlib.util,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
TARGET=HERE/'evaluate_fundamental_residual_r39h.py'
spec=importlib.util.spec_from_file_location('r39h_eval',TARGET)
if spec is None or spec.loader is None: raise RuntimeError('cannot load evaluator')
m=importlib.util.module_from_spec(spec);sys.modules['r39h_eval']=m;spec.loader.exec_module(m)

def load_market_rows_global(market_dir,pre,by_side,hist):
    divmap={'E0':'EPL','D1':'Bundesliga','I1':'Serie_A','SP1':'La_liga','F1':'Ligue_1'}
    cfg={'minimum_individual_team_similarity':.62,'minimum_pair_similarity':.78,'minimum_best_vs_second_pair_margin':.05}
    fd=[]
    for p in sorted(Path(market_dir).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or []);forbidden={'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}
            if forbidden&hdr:raise RuntimeError('label columns in market input')
            for r in rd:
                if r.get('Div') not in divmap:continue
                if not all(str(r.get(c,'')).strip() for c in m.IDCOLS):continue
                if not all(m.valid_odd(r.get(c,'')) for c in ('AvgCH','AvgCD','AvgCA')):continue
                fd.append(r)
    used=set();out=[];unmatched=0
    for r in sorted(fd,key=lambda x:(m.parse_fd_date(x['Date']),x['Div'],x['HomeTeam'],x['AwayTeam'])):
        lg=m.league_norm(divmap[r['Div']]);z=m.choose_match(r,by_side,lg,cfg,used)
        if z is None:
            unmatched+=1;continue
        h,a,off=z;used.update([h['row_key'],a['row_key']]);q=m.devig3(r['AvgCH'],r['AvgCD'],r['AvgCA']);ident=m.fd_identity(r)
        homehist=hist[(lg,m.clean_name(h['club']))];awayhist=hist[(lg,m.clean_name(a['club']))]
        hp=bisect.bisect_left([x['date'] for x in homehist],h['date']);ap=bisect.bisect_left([x['date'] for x in awayhist],a['date'])
        if hp<10 or ap<10:continue
        out.append({'identity':ident,'season':r['Season'],'div':r['Div'],'dt':m.parse_dt(r['Date'],r['Time']),'target_date':h['date'],'home_club':h['club'],'away_club':a['club'],'league':lg,'home_hist':homehist[:hp],'away_hist':awayhist[:ap],'qclose':q.tolist(),'day_offset':off})
    return out,unmatched

m.load_market_rows=load_market_rows_global
if __name__=='__main__':m.main()
