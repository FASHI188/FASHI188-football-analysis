#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from pathlib import Path

def hfile(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def num(v):
    try:
        x=float(v);return x if math.isfinite(x) and x>0 else None
    except (TypeError,ValueError): return None

def fair3(h,d,a):
    vals=[num(h),num(d),num(a)]
    if any(v is None for v in vals): return None
    inv=[1/v for v in vals];s=sum(inv)
    return (inv[0]/s,inv[1]/s,inv[2]/s,s)

def fair_under(over,under):
    o,u=num(over),num(under)
    if o is None or u is None:return None
    io,iu=1/o,1/u
    return iu/(io+iu)

def read(path):
    if not path or not path.is_file():return []
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--p0',type=Path,required=True)
    ap.add_argument('--main-market',type=Path)
    ap.add_argument('--extra-market',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    p0=read(a.p0);main={r['match_identity']:r for r in read(a.main_market)};extra={r['match_identity']:r for r in read(a.extra_market)}
    out=[];issues=[]
    new_fields=['market_coverage_level','market_source_url','market_source_file_sha256','open_avg_home_odds','open_avg_draw_odds','open_avg_away_odds','close_avg_home_odds','close_avg_draw_odds','close_avg_away_odds','market_open_fair_home','market_open_fair_draw','market_open_fair_away','market_open_overround','market_close_fair_home','market_close_fair_draw','market_close_fair_away','market_close_overround','market_close_balance','market_close_favorite','market_close_entropy','market_draw_probability_move','market_open_fair_under25','market_close_fair_under25','market_open_ah_line','market_close_ah_line','market_close_basis']
    for r in p0:
        x=dict(r);mid=r['match_identity'];m=main.get(mid);e=extra.get(mid)
        vals={k:'' for k in new_fields}
        if m:
            vals['market_coverage_level']='MAIN_OPEN_CLOSE_OU_AH';vals['market_source_url']=m.get('source_url','');vals['market_source_file_sha256']=m.get('source_file_sha256','')
            op=fair3(m.get('fd_AvgH'),m.get('fd_AvgD'),m.get('fd_AvgA'))
            cp=fair3(m.get('fd_AvgCH'),m.get('fd_AvgCD'),m.get('fd_AvgCA'))
            vals.update({'open_avg_home_odds':m.get('fd_AvgH',''),'open_avg_draw_odds':m.get('fd_AvgD',''),'open_avg_away_odds':m.get('fd_AvgA',''),'close_avg_home_odds':m.get('fd_AvgCH',''),'close_avg_draw_odds':m.get('fd_AvgCD',''),'close_avg_away_odds':m.get('fd_AvgCA',''),'market_close_basis':'AVG_CLOSE'})
            vals['market_open_fair_under25']=fair_under(m.get('fd_Avg>2.5'),m.get('fd_Avg<2.5')) or ''
            vals['market_close_fair_under25']=fair_under(m.get('fd_AvgC>2.5'),m.get('fd_AvgC<2.5')) or ''
            vals['market_open_ah_line']=m.get('fd_AHh','');vals['market_close_ah_line']=m.get('fd_AHCh','')
        elif e:
            vals['market_coverage_level']='EXTRA_CLOSE_1X2';vals['market_source_url']=e.get('source_url','');vals['market_source_file_sha256']=e.get('source_file_sha256','')
            cp=fair3(e.get('fdx_AvgCH'),e.get('fdx_AvgCD'),e.get('fdx_AvgCA'))
            basis='AVG_CLOSE'
            if cp is None:
                cp=fair3(e.get('fdx_MaxCH'),e.get('fdx_MaxCD'),e.get('fdx_MaxCA'));basis='MAX_CLOSE_FALLBACK'
                issues.append({'match_identity':mid,'issue':'invalid_or_missing_avg_close','resolution':basis})
            vals.update({'close_avg_home_odds':e.get('fdx_AvgCH',''),'close_avg_draw_odds':e.get('fdx_AvgCD',''),'close_avg_away_odds':e.get('fdx_AvgCA',''),'market_close_basis':basis})
            op=None
        else:
            op=cp=None
        if op:
            vals['market_open_fair_home'],vals['market_open_fair_draw'],vals['market_open_fair_away'],vals['market_open_overround']=op
        if cp:
            ph,pd,pa,over=cp
            vals['market_close_fair_home']=ph;vals['market_close_fair_draw']=pd;vals['market_close_fair_away']=pa;vals['market_close_overround']=over
            vals['market_close_balance']=abs(ph-pa);vals['market_close_favorite']=max(ph,pa)
            vals['market_close_entropy']=-sum(v*math.log(v) for v in (ph,pd,pa) if v>0)
            if op: vals['market_draw_probability_move']=pd-op[1]
        x.update(vals);out.append(x)
    fields=list(p0[0])+[f for f in new_fields if f not in p0[0]]
    opath=a.out/'GOLD1000_R1_standard_master.csv'
    with opath.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
    ip=a.out/'GOLD1000_R1_standard_master_issues.csv'
    with ip.open('w',encoding='utf-8',newline='') as f:
        fs=['match_identity','issue','resolution'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(issues)
    matched=sum(1 for r in out if r['market_close_fair_draw'] not in ('',None))
    rec={'schema_version':'GOLD1000-STANDARD-MASTER-R1','rows':len(out),'market_rows':matched,'issues':len(issues),'inputs':{'p0':hfile(a.p0),'main_market':hfile(a.main_market) if a.main_market and a.main_market.is_file() else None,'extra_market':hfile(a.extra_market) if a.extra_market and a.extra_market.is_file() else None},'outputs':{opath.name:hfile(opath),ip.name:hfile(ip)}}
    rp=a.out/'GOLD1000_R1_standard_master_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','rows':len(out),'market_rows':matched,'issues':len(issues)},ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
