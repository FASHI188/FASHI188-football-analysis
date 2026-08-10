#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,difflib,hashlib,json,math,re,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
ALIASES={
'man united':'manchester united','man city':'manchester city','wolves':'wolverhampton wanderers','newcastle':'newcastle united',"nott'm forest":'nottingham forest','nottm forest':'nottingham forest','brighton':'brighton hove albion','leicester':'leicester city','leeds':'leeds united','west ham':'west ham united','spurs':'tottenham hotspur',
'dortmund':'borussia dortmund',"m'gladbach":'borussia monchengladbach','monchengladbach':'borussia monchengladbach','leverkusen':'bayer leverkusen','frankfurt':'eintracht frankfurt','mainz':'mainz 05','bremen':'werder bremen','koln':'cologne','dusseldorf':'fortuna dusseldorf','bielefeld':'arminia bielefeld','greuther furth':'greuther fuerth','bayern munich':'bayern munchen',
'milan':'ac milan','inter':'inter milan','verona':'hellas verona','spal':'spal 2013','roma':'as roma',
'ath madrid':'atletico madrid','athletic bilbao':'athletic club','ath bilbao':'athletic club','bilbao':'athletic club','sociedad':'real sociedad','betis':'real betis','alaves':'deportivo alaves','vallecano':'rayo vallecano','barcelona':'fc barcelona',
'paris sg':'paris saint germain','st etienne':'saint etienne','st. etienne':'saint etienne','marseille':'olympique marseille','lyon':'olympique lyon','monaco':'as monaco'
}

def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids):return htxt('\n'.join(sorted(ids))+'\n')
def valid_odd(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0
def clean_name(s):
    s=unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower().replace('&',' and ')
    s=re.sub(r"[.'’`-]",' ',s);s=re.sub(r'\b(fc|cf|afc|ssc|football club|club de futbol|calcio)\b',' ',s);s=re.sub(r'\s+',' ',s).strip();return ALIASES.get(s,s)
def sim(a,b):
    a,b=clean_name(a),clean_name(b)
    if a==b:return 1.0
    ta,tb=set(a.split()),set(b.split());jac=len(ta&tb)/len(ta|tb) if ta|tb else 0.;seq=difflib.SequenceMatcher(None,a,b).ratio();return max(seq,.65*seq+.35*jac)
def parse_fd_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)
def parse_tm_date(s):return datetime.strptime(str(s).strip()[:10],'%Y-%m-%d').date()
def identity(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
def norm_type(s):return re.sub(r'[^a-z0-9]+','_',str(s).strip().lower()).strip('_')

def load_transfermarkt(games_path,lineups_path,reg):
    comp_to_div={v:k for k,v in reg['transfermarkt']['competition_ids'].items()}
    games={};by_date=defaultdict(list)
    with Path(games_path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);required=set(reg['transfermarkt']['games_identity_columns']);hdr=set(rd.fieldnames or [])
        if not required<=hdr:raise RuntimeError(f'games missing {sorted(required-hdr)}')
        if {'home_club_goals','away_club_goals'}&hdr:raise RuntimeError('goal columns present in sanitized games input')
        for r in rd:
            if r['competition_id'] not in comp_to_div:continue
            d=parse_tm_date(r['date']);gid=str(r['game_id']).strip()
            g={'game_id':gid,'competition_id':r['competition_id'],'div':comp_to_div[r['competition_id']],'date':d,'home_club_id':str(r['home_club_id']).strip(),'away_club_id':str(r['away_club_id']).strip(),'home':r['home_club_name'],'away':r['away_club_name']}
            games[gid]=g;by_date[(g['div'],d)].append(g)
    starters=defaultdict(set);type_counts=Counter();lineup_rows=0
    with Path(lineups_path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);required=set(reg['transfermarkt']['lineup_columns']);hdr=set(rd.fieldnames or [])
        if not required<=hdr:raise RuntimeError(f'lineups missing {sorted(required-hdr)}')
        for r in rd:
            gid=str(r['game_id']).strip()
            if gid not in games:continue
            lineup_rows+=1;t=norm_type(r['type']);type_counts[t]+=1
            if 'starting' in t:
                starters[(gid,str(r['club_id']).strip())].add(str(r['player_id']).strip())
    complete=set();nreq=int(reg['transfermarkt']['required_unique_starters_per_club'])
    for gid,g in games.items():
        if len(starters[(gid,g['home_club_id'])])==nreq and len(starters[(gid,g['away_club_id'])])==nreq:complete.add(gid)
    return games,by_date,starters,complete,type_counts,lineup_rows

def choose(row,by_date,reg,used):
    cfg=reg['matching'];d=parse_fd_date(row['Date']);div=row['Div']
    for off in cfg['date_offsets_days']:
        vals=[]
        for g in by_date.get((div,d+timedelta(days=int(off))),[]):
            if g['game_id'] in used:continue
            sh,sa=sim(row['HomeTeam'],g['home']),sim(row['AwayTeam'],g['away']);pair=(sh+sa)/2
            vals.append((pair,sh,sa,g))
        vals.sort(key=lambda x:(-x[0],-x[1],-x[2],x[3]['game_id']))
        if not vals:continue
        b=vals[0];second=vals[1][0] if len(vals)>1 else -1
        if b[1]<cfg['minimum_individual_team_similarity'] or b[2]<cfg['minimum_individual_team_similarity'] or b[0]<cfg['minimum_pair_similarity']:continue
        if len(vals)>1 and b[0]-second<cfg['minimum_best_vs_second_pair_margin']:continue
        return b[3],int(off),b[0]
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',required=True);ap.add_argument('--games',required=True);ap.add_argument('--lineups',required=True);ap.add_argument('--football-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    reg=json.loads(Path(a.registration).read_text());games,by_date,starters,complete,type_counts,lineup_rows=load_transfermarkt(a.games,a.lineups,reg)
    fd=[]
    for p in sorted(Path(a.football_dir).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}&hdr:raise RuntimeError('result columns present in sanitized Football-Data input')
            for r in rd:
                if r.get('Season') not in reg['football_data']['seasons'] or r.get('Div') not in reg['football_data']['divisions']:continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in reg['football_data']['market_requirement']):continue
                fd.append(r)
    fd.sort(key=lambda r:(parse_fd_date(r['Date']),r['Div'],r['HomeTeam'],r['AwayTeam']))
    used=set();matched=[];unmatched=[];methods=Counter();season_counts=Counter();complete_counts=Counter()
    for r in fd:
        res=choose(r,by_date,reg,used)
        if res is None:unmatched.append(identity(r));continue
        g,off,pair=res;used.add(g['game_id']);methods[f'day_offset_{off}']+=1;season_counts[r['Season']]+=1
        ok=g['game_id'] in complete
        if ok:complete_counts[r['Season']]+=1
        matched.append({'identity':identity(r),'season':r['Season'],'div':r['Div'],'tm_game_id':g['game_id'],'tm_date':str(g['date']),'home_tm':g['home'],'away_tm':g['away'],'pair_similarity':pair,'complete_starting_xi':ok})
    hold=reg['identity_lock']['holdout_season'];pre=[x for x in matched if x['season']!=hold and x['complete_starting_xi']];hp=[x for x in matched if x['season']==hold and x['complete_starting_xi']]
    gate=reg['coverage_gate'];ok=len(pre)>=gate['minimum_preholdout_complete_lineup_rows'] and len(hp)>=gate['minimum_2526_complete_lineup_rows']
    locked=[];sha=None
    if ok:
        seed=reg['identity_lock']['seed'];n=reg['identity_lock']['rows'];locked=sorted(hp,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:n]
        ok=len(locked)==n
        if ok:sha=set_sha([x['identity'] for x in locked])
    status='PASS_R39I_LINEUP_COVERAGE_AND_FIXED100_LOCK_ZERO_LABEL' if ok else 'STOP_R39I_LINEUP_COVERAGE_INSUFFICIENT'
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,'football_data_rows':len(fd),'transfermarkt_top5_games':len(games),'transfermarkt_top5_lineup_rows':lineup_rows,'lineup_type_counts':dict(type_counts),'complete_transfermarkt_games':len(complete),'matched_rows':len(matched),'unmatched_rows':len(unmatched),'match_method_counts':dict(methods),'matched_by_season':dict(season_counts),'complete_lineup_by_season':dict(complete_counts),'complete_preholdout_rows':len(pre),'complete_2526_rows':len(hp),'fixed100_rows':len(locked),'fixed100_identity_sha256':sha,'fixed100_seed':reg['identity_lock']['seed'],'fixed100_min_date':min((x['tm_date'] for x in locked),default=None),'fixed100_max_date':max((x['tm_date'] for x in locked),default=None),'unmatched_examples':unmatched[:30],'no_label_audit':reg['no_label_contract'],'prior_blind_sets':reg['prior_blind_sets'],'hard_limits':reg['hard_limits']}
    Path(a.out_dir).mkdir(parents=True,exist_ok=True);Path(a.out_dir,'lineup_coverage_and_lock_r39i.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps({k:out[k] for k in ('status','football_data_rows','transfermarkt_top5_games','transfermarkt_top5_lineup_rows','lineup_type_counts','complete_transfermarkt_games','matched_rows','complete_preholdout_rows','complete_2526_rows','fixed100_rows','fixed100_identity_sha256','fixed100_min_date','fixed100_max_date')},indent=2))
if __name__=='__main__':main()
