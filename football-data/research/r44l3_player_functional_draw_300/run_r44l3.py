#!/usr/bin/env python3
from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import random
import statistics
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
FILES = ["games.csv.gz", "game_lineups.csv.gz", "appearances.csv.gz", "players.csv.gz", "player_valuations.csv.gz"]
OUT = Path("r44l3_output")
OUT.mkdir(exist_ok=True)
SEED = 20260812


def fetch(name: str) -> bytes:
    req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "r44l3-player-functional/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def reader_from_gz(data: bytes):
    return csv.DictReader(io.StringIO(gzip.decompress(data).decode("utf-8-sig")))


def fnum(v, default=0.0):
    try:
        if v is None or str(v).strip() == "": return default
        return float(v)
    except Exception:
        return default


def fint(v, default=0):
    try:
        if v is None or str(v).strip() == "": return default
        return int(float(v))
    except Exception:
        return default


def parse_date(v: str):
    try: return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except Exception: return None


def mean(xs): return sum(xs) / len(xs) if xs else 0.0

def stdev0(xs): return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def standardize_fit(X):
    p = len(X[0]) if X else 0
    mu, sd = [], []
    for j in range(p):
        col = [r[j] for r in X]
        m = mean(col); s = stdev0(col)
        mu.append(m); sd.append(s if s > 1e-9 else 1.0)
    return mu, sd


def transform(X, mu, sd):
    return [[(r[j]-mu[j])/sd[j] for j in range(len(mu))] for r in X]


def fit_logistic(X, y, l2=0.10, lr=0.05, iters=800):
    mu, sd = standardize_fit(X)
    Z = transform(X, mu, sd)
    n, p = len(Z), len(Z[0])
    w = [0.0] * (p + 1)
    for _ in range(iters):
        g = [0.0] * (p + 1)
        for r, yy in zip(Z, y):
            pred = sigmoid(w[0] + sum(w[j+1]*r[j] for j in range(p)))
            e = pred - yy
            g[0] += e
            for j in range(p): g[j+1] += e*r[j]
        g[0] /= n
        for j in range(p):
            g[j+1] = g[j+1]/n + l2*w[j+1]
        for j in range(p+1): w[j] -= lr*g[j]
    return {"w": w, "mu": mu, "sd": sd}


def predict(model, X):
    Z = transform(X, model["mu"], model["sd"])
    w = model["w"]
    return [sigmoid(w[0] + sum(w[j+1]*r[j] for j in range(len(r)))) for r in Z]


def logloss(y, p):
    eps=1e-12
    return -mean([yy*math.log(max(eps,min(1-eps,pp)))+(1-yy)*math.log(max(eps,min(1-eps,1-pp))) for yy,pp in zip(y,p)])


def brier(y,p): return mean([(yy-pp)**2 for yy,pp in zip(y,p)])


def auc(y,p):
    pos=[pp for yy,pp in zip(y,p) if yy==1]; neg=[pp for yy,pp in zip(y,p) if yy==0]
    if not pos or not neg: return float("nan")
    wins=0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a>b else 0.5 if a==b else 0.0
    return wins/(len(pos)*len(neg))


def metrics(y,p): return {"n":len(y),"draws":sum(y),"logloss":logloss(y,p),"brier":brier(y,p),"auc":auc(y,p)}


def club_form(hist, k):
    h=hist[-k:]
    if not h: return (0.0,0.0,0.0)
    return (mean([x[0] for x in h]), mean([x[1] for x in h]), mean([x[2] for x in h]))


def latest_valuation(vhist, pid, d):
    arr=vhist.get(pid, [])
    if not arr: return 0.0
    dates=[x[0] for x in arr]
    i=bisect.bisect_right(dates,d)-1
    return arr[i][1] if i>=0 else 0.0


def player_rolling(phist, pid, n=10):
    h=phist.get(pid, [])[-n:]
    mins=sum(x[0] for x in h); goals=sum(x[1] for x in h); assists=sum(x[2] for x in h); cards=sum(x[3]+2*x[4] for x in h)
    return mins, goals, assists, cards


def role_group(position):
    p=(position or "").lower()
    attack = any(t in p for t in ["forward","striker","winger"])
    creator = attack or "attacking midfield" in p or "central midfield" in p
    spine = any(t in p for t in ["goalkeeper","centre-back","center-back","defensive midfield"])
    return attack,creator,spine


def team_player_features(starter_rows, d, profiles, vhist, phist, club_totals, club_id):
    pids=[r["player_id"] for r in starter_rows]
    mins5=[]; mins10=[]; g10=a10=[]
    goals=assists=minutes=cards=0.0
    attack_g=attack_m=creator_a=creator_m=spine_m=0.0
    no_recent=established=0
    ages=[]; heights=[]; left=right=0
    vals=[]; captain_min=0.0
    for r in starter_rows:
        pid=r["player_id"]
        m5,g5,a5,c5=player_rolling(phist,pid,5)
        m10,g,a,c=player_rolling(phist,pid,10)
        mins5.append(m5); mins10.append(m10)
        goals+=g; assists+=a; minutes+=m10; cards+=c
        if m5<=0: no_recent+=1
        if m10>=450: established+=1
        attack,creator,spine=role_group(r.get("position",""))
        if attack: attack_g+=g; attack_m+=m10
        if creator: creator_a+=a; creator_m+=m10
        if spine: spine_m+=m10
        pr=profiles.get(pid,{})
        dob=pr.get("date_of_birth")
        if dob: ages.append((d-dob).days/365.25)
        h=pr.get("height",0.0)
        if h>0: heights.append(h)
        foot=(pr.get("foot") or "").lower()
        left += 1 if foot=="left" else 0; right += 1 if foot=="right" else 0
        val=latest_valuation(vhist,pid,d)
        if val>0: vals.append(val)
        if r.get("captain",False): captain_min=m10
    goal90=90*goals/minutes if minutes>0 else 0.0
    ast90=90*assists/minutes if minutes>0 else 0.0
    ga90=90*(goals+assists)/minutes if minutes>0 else 0.0
    card90=90*cards/minutes if minutes>0 else 0.0
    attack_goal90=90*attack_g/attack_m if attack_m>0 else 0.0
    creator_ast90=90*creator_a/creator_m if creator_m>0 else 0.0
    totals=club_totals.get(club_id,{})
    ranked_g=sorted(totals.items(), key=lambda kv:(-kv[1][0],-kv[1][2],kv[0]))
    ranked_a=sorted(totals.items(), key=lambda kv:(-kv[1][1],-kv[1][2],kv[0]))
    ranked_ga=sorted(totals.items(), key=lambda kv:(-(kv[1][0]+kv[1][1]),-kv[1][2],kv[0]))
    sset=set(pids)
    missing_top_goal=1.0 if ranked_g and ranked_g[0][0] not in sset else 0.0
    missing_top_assist=1.0 if ranked_a and ranked_a[0][0] not in sset else 0.0
    missing_top3=sum(1 for pid,_ in ranked_ga[:3] if pid not in sset)
    total_ga=sum(v[0]+v[1] for v in totals.values())
    xi_ga=sum(totals.get(pid,[0,0,0])[0]+totals.get(pid,[0,0,0])[1] for pid in pids)
    ga_cov=xi_ga/total_ga if total_ga>0 else 0.0
    vs=sorted(vals, reverse=True); vsum=sum(vs)
    vmean=mean(vs); vtop3=sum(vs[:3])/vsum if vsum>0 else 0.0
    club_vals=[]
    for pid in totals:
        v=latest_valuation(vhist,pid,d)
        if v>0: club_vals.append(v)
    top11=sum(sorted(club_vals,reverse=True)[:11])
    vcoverage=vsum/top11 if top11>0 else 0.0
    return [
        mean(mins5),mean(mins10),goal90,ast90,ga90,card90,float(no_recent),float(established),
        attack_goal90,creator_ast90,spine_m/11.0,missing_top_goal,missing_top_assist,float(missing_top3),ga_cov,
        math.log1p(vmean),math.log1p(vsum),vtop3,vcoverage,mean(ages),stdev0(ages),mean(heights),
        left/max(1,left+right),captain_min
    ]

PLAYER_NAMES=[
    "avg_min5","avg_min10","goal90_10","assist90_10","ga90_10","card90_10","no_recent_count","established_count",
    "attack_goal90","creator_assist90","spine_avg_min10","missing_top_goal","missing_top_assist","missing_top3_ga","xi_ga_coverage",
    "log_xi_value_mean","log_xi_value_sum","xi_value_top3_share","xi_value_top11_coverage","age_mean","age_sd","height_mean","left_foot_share","captain_min10"
]
BASE_NAMES=[
    "h5_gf","h5_ga","h5_ppg","a5_gf","a5_ga","a5_ppg","h10_gf","h10_ga","h10_ppg","a10_gf","a10_ga","a10_ppg",
    "abs_gf5_diff","abs_ga5_diff","combined5_goal_env","abs_ppg10_diff"
]


def main():
    blobs={n:fetch(n) for n in FILES}
    hashes={n:hashlib.sha256(b).hexdigest() for n,b in blobs.items()}
    headers={}
    for n,b in blobs.items():
        rr=reader_from_gz(b); headers[n]=rr.fieldnames or []

    # Profiles: stable fields only.
    profiles={}
    for r in reader_from_gz(blobs["players.csv.gz"]):
        pid=str(r.get("player_id","")).strip()
        if not pid: continue
        profiles[pid]={"date_of_birth":parse_date(r.get("date_of_birth","")),"foot":r.get("foot",""),"height":fnum(r.get("height_in_cm"))}

    # Historical valuations only; actual lookup is <= match date.
    vhist=defaultdict(list)
    for r in reader_from_gz(blobs["player_valuations.csv.gz"]):
        pid=str(r.get("player_id","")).strip(); d=parse_date(r.get("date",r.get("valuation_date","")))
        v=fnum(r.get("market_value_in_eur"))
        if pid and d and v>0: vhist[pid].append((d,v))
    for pid in vhist: vhist[pid].sort()

    # Relevant EPL games.
    games=[]; game_by_id={}
    for r in reader_from_gz(blobs["games.csv.gz"]):
        if r.get("competition_id")!="GB1": continue
        season=fint(r.get("season"),-1)
        if season not in (2023,2024,2025): continue
        d=parse_date(r.get("date","")); gid=str(r.get("game_id","")).strip()
        if not gid or not d: continue
        row={"game_id":gid,"date":d,"season":season,"home":str(r.get("home_club_id","")),"away":str(r.get("away_club_id","")),
             "hg":fint(r.get("home_club_goals")),"ag":fint(r.get("away_club_goals"))}
        games.append(row); game_by_id[gid]=row
    games.sort(key=lambda r:(r["date"],fint(r["game_id"])))
    relevant=set(game_by_id)

    # Current historical lineups. This is the known PIT-unverified element.
    lineups=defaultdict(list)
    for r in reader_from_gz(blobs["game_lineups.csv.gz"]):
        gid=str(r.get("game_id","")).strip()
        if gid not in relevant: continue
        typ=(r.get("type") or "").lower()
        if "start" not in typ: continue
        club=str(r.get("club_id","")).strip(); pid=str(r.get("player_id","")).strip()
        if not club or not pid: continue
        cap=(r.get("team_captain") or "").strip().lower() in ("1","true","yes","captain")
        lineups[(gid,club)].append({"player_id":pid,"position":r.get("position","") or "","captain":cap})

    # Appearance outcomes are preloaded but only applied after each game feature row is frozen.
    apps_by_game=defaultdict(list)
    for r in reader_from_gz(blobs["appearances.csv.gz"]):
        gid=str(r.get("game_id","")).strip()
        if gid not in relevant: continue
        pid=str(r.get("player_id","")).strip(); club=str(r.get("player_club_id","")).strip()
        if not pid or not club: continue
        apps_by_game[gid].append({"pid":pid,"club":club,"min":fnum(r.get("minutes_played")),"g":fnum(r.get("goals")),"a":fnum(r.get("assists")),
                                  "yc":fnum(r.get("yellow_cards")),"rc":fnum(r.get("red_cards"))})

    # Freeze latest 300 2025/26 game identities before modelling.
    season25=[g for g in games if g["season"]==2025]
    season25.sort(key=lambda r:(r["date"],fint(r["game_id"])))
    test300=season25[-300:]
    test_ids=[g["game_id"] for g in test300]; test_index={gid:i for i,gid in enumerate(test_ids)}

    club_hist=defaultdict(list); phist=defaultdict(list); club_totals=defaultdict(lambda:defaultdict(lambda:[0.0,0.0,0.0]))
    rows=[]; coverage={"all_games":0,"both_xi_11":0,"target_rows":0,"test300_games":len(test300)}

    for g in games:
        coverage["all_games"]+=1
        hs=lineups.get((g["game_id"],g["home"]),[]); as_=lineups.get((g["game_id"],g["away"]),[])
        valid=len({x["player_id"] for x in hs})==11 and len({x["player_id"] for x in as_})==11
        if valid:
            coverage["both_xi_11"]+=1
            h5=club_form(club_hist[g["home"]],5); a5=club_form(club_hist[g["away"]],5)
            h10=club_form(club_hist[g["home"]],10); a10=club_form(club_hist[g["away"]],10)
            base=[*h5,*a5,*h10,*a10,abs(h5[0]-a5[0]),abs(h5[1]-a5[1]),h5[0]+h5[1]+a5[0]+a5[1],abs(h10[2]-a10[2])]
            hp=team_player_features(hs,g["date"],profiles,vhist,phist,club_totals,g["home"])
            ap=team_player_features(as_,g["date"],profiles,vhist,phist,club_totals,g["away"])
            player=[]
            for x,y in zip(hp,ap): player.extend([abs(x-y),(x+y)/2.0])
            margin=abs(g["hg"]-g["ag"])
            if margin in (0,1):
                coverage["target_rows"]+=1
                rows.append({"gid":g["game_id"],"date":g["date"].isoformat(),"season":g["season"],"base":base,"player":player,"y":1 if margin==0 else 0,
                             "test_idx":test_index.get(g["game_id"],-1)})

        # Only now reveal/update current game's team result and player appearances.
        hpnt=3.0 if g["hg"]>g["ag"] else 1.0 if g["hg"]==g["ag"] else 0.0
        apnt=3.0 if g["ag"]>g["hg"] else 1.0 if g["hg"]==g["ag"] else 0.0
        club_hist[g["home"]].append((g["hg"],g["ag"],hpnt)); club_hist[g["away"]].append((g["ag"],g["hg"],apnt))
        starter_sets={(g["home"]):{x["player_id"] for x in hs},(g["away"]):{x["player_id"] for x in as_}}
        for a in apps_by_game.get(g["game_id"],[]):
            phist[a["pid"]].append((a["min"],a["g"],a["a"],a["yc"],a["rc"]))
            t=club_totals[a["club"]][a["pid"]]; t[0]+=a["g"]; t[1]+=a["a"]; t[2]+=a["min"]

    fold_results=[]; pooled_y=[]; pooled_b=[]; pooled_p=[]; pred_rows=[]
    for fold in range(3):
        lo=fold*100; hi=(fold+1)*100
        fold_game_ids=set(test_ids[lo:hi])
        first_key=(test300[lo]["date"],fint(test300[lo]["game_id"]))
        tr=[r for r in rows if (parse_date(r["date"]),fint(r["gid"])) < first_key]
        te=[r for r in rows if r["gid"] in fold_game_ids]
        Xb=[r["base"] for r in tr]; Xp=[r["base"]+r["player"] for r in tr]; y=[r["y"] for r in tr]
        if len(set(y))<2 or not te: raise RuntimeError(f"insufficient fold {fold+1}: train={len(tr)} test={len(te)}")
        mb=fit_logistic(Xb,y); mp=fit_logistic(Xp,y)
        ty=[r["y"] for r in te]; pb=predict(mb,[r["base"] for r in te]); pp=predict(mp,[r["base"]+r["player"] for r in te])
        bm=metrics(ty,pb); pm=metrics(ty,pp)
        fold_results.append({"fold":fold+1,"game_window":[lo,hi-1],"train_target_n":len(tr),"test_target_n":len(te),"baseline":bm,"player":pm,"delta_ll":pm["logloss"]-bm["logloss"],"delta_brier":pm["brier"]-bm["brier"],"delta_auc":pm["auc"]-bm["auc"]})
        pooled_y+=ty; pooled_b+=pb; pooled_p+=pp
        for r,bb,ppp in zip(te,pb,pp): pred_rows.append({"game_id":r["gid"],"date":r["date"],"fold":fold+1,"y_draw":r["y"],"p_baseline":bb,"p_player":ppp})

    bm=metrics(pooled_y,pooled_b); pm=metrics(pooled_y,pooled_p)
    rng=random.Random(SEED); deltas=[]; n=len(pooled_y)
    for _ in range(5000):
        idx=[rng.randrange(n) for _ in range(n)]
        yy=[pooled_y[i] for i in idx]; bb=[pooled_b[i] for i in idx]; pp=[pooled_p[i] for i in idx]
        deltas.append(logloss(yy,pp)-logloss(yy,bb))
    deltas.sort(); p05=deltas[int(.05*len(deltas))]; p95=deltas[int(.95*len(deltas))-1]
    pooled={"baseline":bm,"player":pm,"delta_ll":pm["logloss"]-bm["logloss"],"delta_brier":pm["brier"]-bm["brier"],"delta_auc":pm["auc"]-bm["auc"],"bootstrap90_delta_ll":[p05,p95]}
    pass_signal=pooled["delta_ll"]<0 and p95<0 and sum(1 for x in fold_results if x["delta_ll"]<=0)>=2
    verdict="RETROSPECTIVE_SIGNAL_PASS" if pass_signal else "RETROSPECTIVE_SIGNAL_FAIL"

    inventory={
      "already_tested_r44l2":["games.home_club_formation","games.away_club_formation","game_lineups.player_id/name/position","position category counts","XI overlap/formation change"],
      "newly_tested_r44l3":["appearances.minutes_played/goals/assists/yellow_cards/red_cards PRIOR-ONLY","players.date_of_birth/foot/height_in_cm stable profile","player_valuations historical date/value PRIOR-ONLY","game_lineups.team_captain","functional core-absence proxies","role-conditioned prior production"],
      "present_but_excluded":["players.current market_value_in_eur (current-state leakage for historical match)","players.highest_market_value_in_eur","players.contract_expiration_date","current-match appearances performance","current/future game_events"],
      "not_represented_as_reliable_prematch_fields":["injury observed_at/available_at","confirmed pre-kickoff availability timestamp","set-piece/penalty/free-kick duty assignment","coach-declared tactical task","expected pressing/build-up role","verified T-minus lineup publication timestamp"],
      "actual_headers":headers
    }
    result={"study_id":"r44l3_player_functional_draw_300","formal_weight":0,"pit_eligible":False,"terminal_status":verdict,"source_sha256":hashes,"coverage":coverage,"test_identity":{"season25_games":len(season25),"latest300_first":test300[0]["date"].isoformat(),"latest300_last":test300[-1]["date"].isoformat()},"feature_names":{"baseline":BASE_NAMES,"player_match":[f"{kind}_{n}" for n in PLAYER_NAMES for kind in ("absdiff","mean")]},"folds":fold_results,"pooled":pooled,"inventory":inventory,"notes":["Historical current-match lineup identities are PIT-unverified; result is retrospective only.","Current-match player outcomes are applied only after feature freeze.","No Provider/API-Football/paid API used."]}
    (OUT/"result_r44l3.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    with (OUT/"predictions_r44l3.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["game_id","date","fold","y_draw","p_baseline","p_player"]); w.writeheader(); w.writerows(pred_rows)
    md=["# R44L3 Result",f"- terminal_status: `{verdict}`",f"- pooled target rows: {len(pooled_y)}",f"- pooled baseline LL: {bm['logloss']:.8f}",f"- pooled player LL: {pm['logloss']:.8f}",f"- ΔLL(player-baseline): {pooled['delta_ll']:+.8f}",f"- bootstrap90 ΔLL: [{p05:+.8f}, {p95:+.8f}]",f"- baseline AUC: {bm['auc']:.6f}",f"- player AUC: {pm['auc']:.6f}",f"- ΔAUC: {pooled['delta_auc']:+.6f}","","## Folds"]
    for x in fold_results: md.append(f"- fold{x['fold']}: n={x['test_target_n']}, ΔLL={x['delta_ll']:+.8f}, ΔAUC={x['delta_auc']:+.6f}")
    md += ["","## Boundary","- formal_weight=0","- historical lineup available_at remains unverified; no formal promotion is possible from this run."]
    (OUT/"result_r44l3.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps({"terminal_status":verdict,"pooled":pooled,"folds":fold_results,"coverage":coverage},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
