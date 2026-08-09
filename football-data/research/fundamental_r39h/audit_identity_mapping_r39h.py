#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,csv,difflib,hashlib,json,math,re,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
ALIASES={
'man united':'manchester united','man city':'manchester city','wolves':'wolverhampton wanderers','newcastle':'newcastle united',"nott'm forest":'nottingham forest','nottm forest':'nottingham forest','brighton':'brighton and hove albion','leicester':'leicester city','leeds':'leeds united','west ham':'west ham united',
'dortmund':'borussia dortmund',"m'gladbach":'borussia monchengladbach','monchengladbach':'borussia monchengladbach','leverkusen':'bayer leverkusen','frankfurt':'eintracht frankfurt','mainz':'mainz 05','bremen':'werder bremen','koln':'fc cologne','cologne':'fc cologne','dusseldorf':'fortuna dusseldorf','bielefeld':'arminia bielefeld','greuther furth':'greuther fuerth',
'milan':'ac milan','verona':'hellas verona','spal':'spal 2013','ath madrid':'atletico madrid','ath bilbao':'athletic club','athletic bilbao':'athletic club','bilbao':'athletic club','sociedad':'real sociedad','betis':'real betis','alaves':'deportivo alaves','vallecano':'rayo vallecano','paris sg':'paris saint germain','st etienne':'saint etienne','st. etienne':'saint etienne'
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
    ta,tb=set(a.split()),set(b.split());jac=len(ta&tb)/len(ta|tb) if ta|tb else 0.0;seq=difflib.SequenceMatcher(None,a,b).ratio()
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
    wanted={league_norm(v) for v in divmap.values()};rows=[];by_side=defaultdict(list);hist=defaultdict(list);bad=0
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);allowed={'id','league','season','club_name','home_away','date'};hdr=set(rd.fieldnames or [])
        assert hdr<=allowed and {'id','league','club_name','home_away','date'}<=hdr
        for r in rd:
            lg=league_norm(r['league'])
            if lg not in wanted:continue
            side=str(r['home_away']).strip().lower()[:1]
            if side not in {'h','a'}:bad+=1;continue
            try:d=parse_us_date(r['date'])
            except:bad+=1;continue
            x={'row_key':f"{r['id']}|{r['date']}|{side}|{r['club_name']}",'club_id':r['id'],'league':lg,'date':d,'side':side,'club':r['club_name']}
            rows.append(x);by_side[(lg,d,side)].append(x);hist[(lg,clean_name(r['club_name']))].append(d)
    for k in hist:hist[k].sort()
    return rows,by_side,hist,bad

def best_team(cands,target,used,cfg):
    rr=sorted([(sim(target,x['club']),x) for x in cands if x['row_key'] not in used],key=lambda z:(-z[0],z[1]['row_key']))
    if not rr:return None
    best=rr[0];second=rr[1][0] if len(rr)>1 else -1.0
    if best[0]<cfg['minimum_individual_team_similarity']:return None
    if len(rr)>1 and best[0]-second<cfg['minimum_best_vs_second_pair_margin']:return None
    return best

def choose_match(row,by_side,target_league,reg,used):
    d=parse_fd_date(row['Date']);cfg=reg['matching']
    for off in (0,-1,1):
        day=d+timedelta(days=off);h=best_team(by_side.get((target_league,day,'h'),[]),row['HomeTeam'],used,cfg);a=best_team(by_side.get((target_league,day,'a'),[]),row['AwayTeam'],used,cfg)
        if h is None or a is None:continue
        pair=(h[0]+a[0])/2
        if pair<cfg['minimum_pair_similarity']:continue
        return h[1],a[1],off,pair,h[0],a[0]
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--football-dir',type=Path,required=True);ap.add_argument('--understat-identity',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());divmap=reg['matching']['division_to_league'];usrows,by_side,hist,bad=build_understat(a.understat_identity,divmap)
    fd=[]
    for p in sorted(a.football_dir.glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or []);assert not {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}&hdr
            for r in rd:
                if r.get('Div') not in divmap or r.get('Season') not in reg['football_data']['seasons']:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in reg['football_data']['market_requirement']):continue
                fd.append(r)
    used=set();matched=[];unmatched=[];method=Counter();namepairs=Counter()
    for r in sorted(fd,key=lambda x:(parse_fd_date(x['Date']),x['Div'],x['HomeTeam'],x['AwayTeam'])):
        lg=league_norm(divmap[r['Div']]);res=choose_match(r,by_side,lg,reg,used)
        if res is None:unmatched.append(fd_identity(r));continue
        h,a_us,off,pair,sh,sa=res;used.update([h['row_key'],a_us['row_key']]);method[f'day_offset_{off}']+=1;namepairs[(r['HomeTeam'],h['club'])]+=1;namepairs[(r['AwayTeam'],a_us['club'])]+=1
        hp=bisect.bisect_left(hist[(lg,clean_name(h['club']))],h['date']);aprior=bisect.bisect_left(hist[(lg,clean_name(a_us['club']))],a_us['date'])
        matched.append({'identity':fd_identity(r),'season':r['Season'],'div':r['Div'],'league':lg,'us_date':str(h['date']),'home_us':h['club'],'away_us':a_us['club'],'home_prior':hp,'away_prior':aprior,'pair_similarity':pair})
    pre=sum(x['season']!='2425' for x in matched);hold=sum(x['season']=='2425' for x in matched);minhist=int(reg['coverage_gate']['minimum_prior_understat_matches_per_team']);eligible=[x for x in matched if x['home_prior']>=minhist and x['away_prior']>=minhist];ehold=[x for x in eligible if x['season']=='2425'];epre=[x for x in eligible if x['season']!='2425']
    gate=reg['coverage_gate'];ok=pre>=gate['minimum_preholdout_matched_rows'] and hold>=gate['minimum_2425_matched_rows'] and len(ehold)>=gate['minimum_2425_history_eligible_rows'];locked=[];sha=None
    if ok:
        seed=reg['identity_lock']['seed'];n=reg['identity_lock']['rows'];locked=sorted(ehold,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:n]
        if len(locked)==n:sha=set_sha([x['identity'] for x in locked])
        else:ok=False
    status='PASS_R39H_IDENTITY_MAP_AND_FIXED100_LOCK_NO_LABELS' if ok else 'STOP_R39H_IDENTITY_MAPPING_OR_HISTORY_COVERAGE_INSUFFICIENT'
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,'football_closing1x2_rows':len(fd),'understat_top5_team_match_rows':len(usrows),'understat_bad_identity_rows':bad,'matched_rows':len(matched),'matched_preholdout':pre,'matched_2425':hold,'history_eligible_rows':len(eligible),'history_eligible_preholdout':len(epre),'history_eligible_2425':len(ehold),'match_method_counts':dict(method),'unmatched_rows':len(unmatched),'unmatched_identity_examples':unmatched[:30],'name_pair_examples':[{'football_data':x,'understat':y,'count':c} for (x,y),c in namepairs.most_common(80)],'locked_fixed100_rows':len(locked),'locked_fixed100_identity_sha256':sha,'lock_seed':reg['identity_lock']['seed'],'no_label_audit':reg['no_label_contract'],'prior_blind_sets':reg['prior_blind_sets'],'hard_limits':reg['hard_limits']}
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'identity_mapping_and_lock_r39h.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:out[k] for k in ('status','football_closing1x2_rows','understat_top5_team_match_rows','matched_preholdout','matched_2425','history_eligible_2425','locked_fixed100_rows','locked_fixed100_identity_sha256','match_method_counts','unmatched_rows')},indent=2))
if __name__=='__main__':main()
