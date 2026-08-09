#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,csv,difflib,hashlib,json,math,re,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
ALIASES={
'man united':'manchester united','man city':'manchester city','wolves':'wolverhampton wanderers','newcastle':'newcastle united',
"nott'm forest":'nottingham forest','nottm forest':'nottingham forest','brighton':'brighton and hove albion','leicester':'leicester city','leeds':'leeds united','west ham':'west ham united',
'dortmund':'borussia dortmund',"m'gladbach":'borussia monchengladbach','monchengladbach':'borussia monchengladbach','leverkusen':'bayer leverkusen','frankfurt':'eintracht frankfurt','mainz':'mainz 05','bremen':'werder bremen','koln':'fc cologne','cologne':'fc cologne','dusseldorf':'fortuna dusseldorf','bielefeld':'arminia bielefeld','greuther furth':'greuther fuerth',
'milan':'ac milan','verona':'hellas verona','spal':'spal 2013',
'ath madrid':'atletico madrid','athletic bilbao':'athletic club','bilbao':'athletic club','sociedad':'real sociedad','betis':'real betis','alaves':'deportivo alaves','vallecano':'rayo vallecano',
'paris sg':'paris saint germain','st etienne':'saint etienne','st. etienne':'saint etienne'
}

def htxt(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids:list[str])->str:return htxt('\n'.join(sorted(ids))+'\n')
def valid_odd(v)->bool:
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0

def clean_name(s:str)->str:
    s=unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower().replace('&',' and ')
    s=re.sub(r"[.'’`-]",' ',s);s=re.sub(r'\b(fc|cf|afc|ssc|calcio|football club)\b',' ',s);s=re.sub(r'\s+',' ',s).strip()
    return ALIASES.get(s,s)

def sim(a:str,b:str)->float:
    a,b=clean_name(a),clean_name(b)
    if a==b:return 1.0
    ta,tb=set(a.split()),set(b.split())
    jac=len(ta&tb)/len(ta|tb) if ta|tb else 0.0
    seq=difflib.SequenceMatcher(None,a,b).ratio()
    return max(seq,0.65*seq+0.35*jac)

def parse_fd_date(s:str):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)
def parse_us_date(s:str):
    t=str(s).strip().replace('Z','+00:00')
    try:return datetime.fromisoformat(t).date()
    except:pass
    for f in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d','%d/%m/%Y'):
        try:return datetime.strptime(t[:19],f).date()
        except:pass
    raise ValueError(s)
def fd_identity(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
def league_norm(s):return re.sub(r'[^a-z0-9]','',str(s).lower())

def build_understat(path:Path,divmap:dict):
    wanted={league_norm(v) for v in divmap.values()}
    groups=defaultdict(list)
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        allowed={'id','league','season','club_name','home_away','date'}
        assert set(rd.fieldnames or [])<=allowed and {'id','league','club_name','home_away','date'}<=set(rd.fieldnames or [])
        for r in rd:
            if league_norm(r['league']) not in wanted:continue
            groups[r['id']].append(r)
    matches=[];bad=0
    for mid,rs in groups.items():
        home=[r for r in rs if str(r['home_away']).strip().lower().startswith('h')]
        away=[r for r in rs if str(r['home_away']).strip().lower().startswith('a')]
        if len(home)!=1 or len(away)!=1:bad+=1;continue
        h,a=home[0],away[0]
        if league_norm(h['league'])!=league_norm(a['league']):bad+=1;continue
        try:d=parse_us_date(h['date'])
        except:bad+=1;continue
        matches.append({'id':mid,'league':league_norm(h['league']),'date':d,'home':h['club_name'],'away':a['club_name']})
    by_date=defaultdict(list);hist=defaultdict(list)
    for m in matches:
        by_date[(m['league'],m['date'])].append(m)
        hist[(m['league'],clean_name(m['home']))].append(m['date']);hist[(m['league'],clean_name(m['away']))].append(m['date'])
    for k in hist:hist[k].sort()
    return matches,by_date,hist,bad

def choose_match(row,by_date,target_league,reg,used):
    d=parse_fd_date(row['Date']);hn=row['HomeTeam'];an=row['AwayTeam'];cfg=reg['matching']
    def ranked(day):
        vals=[]
        for m in by_date.get((target_league,day),[]):
            if m['id'] in used:continue
            sh,sa=sim(hn,m['home']),sim(an,m['away']);pair=(sh+sa)/2
            vals.append((pair,sh,sa,m))
        return sorted(vals,key=lambda x:(-x[0],-x[1],-x[2],x[3]['id']))
    for off in (0,-1,1):
        rr=ranked(d+timedelta(days=off))
        if not rr:continue
        best=rr[0];second=rr[1][0] if len(rr)>1 else -1
        if best[1] < cfg['minimum_individual_team_similarity'] or best[2] < cfg['minimum_individual_team_similarity'] or best[0] < cfg['minimum_pair_similarity']:continue
        if len(rr)>1 and best[0]-second < cfg['minimum_best_vs_second_pair_margin']:continue
        return best[3],off,best[0],best[1],best[2]
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--football-dir',type=Path,required=True);ap.add_argument('--understat-identity',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());divmap=reg['matching']['division_to_league']
    matches,by_date,hist,bad=build_understat(a.understat_identity,divmap)
    fd=[]
    for p in sorted(a.football_dir.glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            assert not {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}&hdr
            for r in rd:
                if r.get('Div') not in divmap or r.get('Season') not in reg['football_data']['seasons']:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in reg['football_data']['market_requirement']):continue
                fd.append(r)
    used=set();matched=[];unmatched=[];method=Counter();namepairs=Counter()
    for r in sorted(fd,key=lambda x:(parse_fd_date(x['Date']),x['Div'],x['HomeTeam'],x['AwayTeam'])):
        lg=league_norm(divmap[r['Div']]);res=choose_match(r,by_date,lg,reg,used)
        if res is None:
            unmatched.append(fd_identity(r));continue
        m,off,pair,sh,sa=res;used.add(m['id']);method[f'day_offset_{off}']+=1
        namepairs[(r['HomeTeam'],m['home'])]+=1;namepairs[(r['AwayTeam'],m['away'])]+=1
        d=parse_fd_date(r['Date']);mh=bisect.bisect_left(hist[(lg,clean_name(m['home']))],m['date']);ma=bisect.bisect_left(hist[(lg,clean_name(m['away']))],m['date'])
        matched.append({'identity':fd_identity(r),'season':r['Season'],'div':r['Div'],'fd_date':d,'understat_id':m['id'],'league':lg,'home_us':m['home'],'away_us':m['away'],'home_prior':mh,'away_prior':ma,'pair_similarity':pair})
    pre=sum(x['season']!='2425' for x in matched);hold=sum(x['season']=='2425' for x in matched);minhist=int(reg['coverage_gate']['minimum_prior_understat_matches_per_team'])
    eligible=[x for x in matched if x['home_prior']>=minhist and x['away_prior']>=minhist];ehold=[x for x in eligible if x['season']=='2425'];epre=[x for x in eligible if x['season']!='2425']
    gate=reg['coverage_gate'];ok=pre>=gate['minimum_preholdout_matched_rows'] and hold>=gate['minimum_2425_matched_rows'] and len(ehold)>=gate['minimum_2425_history_eligible_rows']
    locked=[];sha=None
    if ok:
        seed=reg['identity_lock']['seed'];n=reg['identity_lock']['rows'];locked=sorted(ehold,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:n]
        if len(locked)==n:sha=set_sha([x['identity'] for x in locked])
        else:ok=False
    status='PASS_R39H_IDENTITY_MAP_AND_FIXED100_LOCK_NO_LABELS' if ok else 'STOP_R39H_IDENTITY_MAPPING_OR_HISTORY_COVERAGE_INSUFFICIENT'
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
         'football_closing1x2_rows':len(fd),'understat_top5_matches':len(matches),'understat_bad_match_groups':bad,
         'matched_rows':len(matched),'matched_preholdout':pre,'matched_2425':hold,'history_eligible_rows':len(eligible),'history_eligible_preholdout':len(epre),'history_eligible_2425':len(ehold),
         'match_method_counts':dict(method),'unmatched_rows':len(unmatched),'unmatched_identity_examples':unmatched[:30],
         'name_pair_examples':[{'football_data':a,'understat':b,'count':c} for (a,b),c in namepairs.most_common(80)],
         'locked_fixed100_rows':len(locked),'locked_fixed100_identity_sha256':sha,'lock_seed':reg['identity_lock']['seed'],
         'no_label_audit':reg['no_label_contract'],'prior_blind_sets':reg['prior_blind_sets'],'hard_limits':reg['hard_limits']}
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'identity_mapping_and_lock_r39h.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps({k:out[k] for k in ('status','football_closing1x2_rows','understat_top5_matches','matched_preholdout','matched_2425','history_eligible_2425','locked_fixed100_rows','locked_fixed100_identity_sha256','match_method_counts','unmatched_rows')},indent=2))
if __name__=='__main__':main()
