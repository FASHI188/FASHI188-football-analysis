from __future__ import annotations
import argparse, codecs, hashlib, json, re, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

CUTOFF = datetime.fromisoformat('2026-09-02T17:00:00+00:00')
LEAGUES = {
    'EPL':'EPL',
    'La liga':'La_liga',
    'Bundesliga':'Bundesliga',
    'Serie A':'Serie_A',
    'Ligue 1':'Ligue_1',
}
ALLOWED_SITUATIONS={'OpenPlay','SetPiece','FromCorner','DirectFreekick','Penalty'}
UA='Mozilla/5.0 (compatible; Football3Research/1.0; +noncommercial-research)'

def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def canon(o)->bytes: return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def fetch(url:str, tries:int=3)->bytes:
    err=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml'})
            with urllib.request.urlopen(req,timeout=30) as r:
                if getattr(r,'status',200)!=200: raise RuntimeError(f'HTTP {r.status}')
                return r.read()
        except Exception as e:
            err=e
            if i+1<tries: time.sleep(2*(i+1))
    raise RuntimeError(f'fetch failed {url}: {err}')

def extract_var(html:bytes,name:str):
    text=html.decode('utf-8','replace')
    m=re.search(r"var\s+"+re.escape(name)+r"\s*=\s*JSON\.parse\('(.*?)'\)",text,re.S)
    if not m: raise RuntimeError(f'{name} not found')
    decoded=codecs.decode(m.group(1),'unicode_escape')
    return json.loads(decoded)

def dates_list(obj):
    if isinstance(obj,dict) and isinstance(obj.get('dates'),list): return obj['dates']
    if isinstance(obj,list): return obj
    raise RuntimeError('datesData shape unsupported')

def shots_list(obj):
    if isinstance(obj,dict) and 'shots' in obj: obj=obj['shots']
    if isinstance(obj,dict):
        out=[]
        for k in ('h','a'):
            v=obj.get(k,[])
            if isinstance(v,list): out.extend(v)
        if out: return out
    if isinstance(obj,list): return obj
    raise RuntimeError('shotsData shape unsupported or empty')

def truthy(v):
    if isinstance(v,bool): return v
    return str(v).lower() in {'true','1','yes'}

def parse_dt(v:str)->datetime:
    s=str(v).strip().replace('T',' ')
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try: return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise RuntimeError(f'unsupported datetime {v!r}')

def team_title(x): return str((x or {}).get('title') or '').strip()
def row_identity(r):
    return {'understat_match_id':str(r.get('id') or ''),'home_team':team_title(r.get('h')),'away_team':team_title(r.get('a')),'datetime_raw':str(r.get('datetime') or '')}

def validate_shots(shots):
    situations=set(); sides=set(); n=0
    for s in shots:
        if not isinstance(s,dict): raise RuntimeError('shot row not object')
        for k in ('xG','situation','h_a'):
            if k not in s: raise RuntimeError(f'shot field missing {k}')
        try: x=float(s['xG'])
        except Exception as e: raise RuntimeError('shot xG nonnumeric') from e
        if not (0.0 <= x <= 1.0): raise RuntimeError('shot xG outside [0,1]')
        sit=str(s['situation']); side=str(s['h_a'])
        if sit not in ALLOWED_SITUATIONS: raise RuntimeError(f'unknown situation {sit}')
        if side not in {'h','a'}: raise RuntimeError(f'unknown h_a {side}')
        situations.add(sit); sides.add(side); n+=1
    if n==0 or sides!={'h','a'}: raise RuntimeError('insufficient bilateral shot sample')
    return {'shot_n':n,'situations_seen':sorted(situations),'sides_seen':sorted(sides),'required_fields':['xG','situation','h_a']}

def run(out:Path):
    homepage_url='https://understat.com/'
    home=fetch(homepage_url)
    report={
      'schema_version':'football3-v3-1-1-understat-source-preflight-v1',
      'cutoff_utc':CUTOFF.isoformat().replace('+00:00','Z'),
      'source':'Understat','homepage_url':homepage_url,'homepage_sha256':sha256(home),
      'raw_payload_persisted':False,'raw_data_redistribution':False,
      'license_evidence':{
        'official_site_license_found':False,
        'classification':'SECONDARY_NONCOMMERCIAL_SUPPORT_STATEMENT_NOT_FORMAL_DATA_LICENSE',
        'secondary_reference':'https://github.com/ewenme/understatr/blob/dd6e48fb3b86174b9346e9b08a821045c9415d4d/README.md',
        'secondary_reference_commit':'dd6e48fb3b86174b9346e9b08a821045c9415d4d',
        'support_statement_date':'2018-11-08',
        'scope':'reported by understatr README as free to use for non-commercial purposes; subject to change',
        'use_restriction_here':'research-only; attribute Understat; do not redistribute raw HTML or shot rows'
      },
      'leagues':{}
    }
    total_future=0; total_completed=0
    for league,slug in LEAGUES.items():
        url=f'https://understat.com/league/{slug}/2026'
        b=fetch(url); dates=dates_list(extract_var(b,'datesData'))
        completed=[]; future=[]
        for r in dates:
            if not isinstance(r,dict) or not r.get('datetime'): continue
            dt=parse_dt(r['datetime'])
            if truthy(r.get('isResult')) and dt < CUTOFF: completed.append((dt,r))
            elif (not truthy(r.get('isResult'))) and dt > CUTOFF: future.append((dt,r))
        if not future: raise RuntimeError(f'{league}: no post-cutoff 2026/27 fixtures')
        if not completed: raise RuntimeError(f'{league}: no pre-cutoff completed 2026/27 match for schema sample')
        completed.sort(key=lambda x:x[0]); future.sort(key=lambda x:x[0])
        dt,sample=completed[-1]; mid=str(sample.get('id') or '')
        if not mid.isdigit(): raise RuntimeError(f'{league}: invalid sample match id')
        murl=f'https://understat.com/match/{mid}'
        mb=fetch(murl); schema=validate_shots(shots_list(extract_var(mb,'shotsData')))
        report['leagues'][league]={
          'league_url':url,'league_page_sha256':sha256(b),'season_start_year':2026,
          'schedule_rows_n':len(dates),'completed_pre_cutoff_n':len(completed),'future_post_cutoff_n':len(future),
          'first_future_fixture':{'kickoff_raw':future[0][0].strftime('%Y-%m-%d %H:%M:%S'),'identity':row_identity(future[0][1])},
          'pre_cutoff_schema_sample':{'match_url':murl,'match_page_sha256':sha256(mb),'kickoff_raw':dt.strftime('%Y-%m-%d %H:%M:%S'),'identity':row_identity(sample),'shot_schema':schema},
          'post_cutoff_match_pages_fetched':0
        }
        total_future+=len(future); total_completed+=len(completed); time.sleep(0.5)
    report['total_future_post_cutoff_n']=total_future; report['total_completed_pre_cutoff_n']=total_completed
    report['required_n']=1603; report['capacity_gate']=total_future>=1603
    report['field_semantics_gate']=True; report['all_five_current_big5_operational']=True
    report['source_preflight_status']='PASS' if report['capacity_gate'] else 'FAIL_CAPACITY'
    report['report_sha256']=sha256(canon({k:v for k,v in report.items() if k!='report_sha256'}))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,sort_keys=True))

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args(); run(a.out)
