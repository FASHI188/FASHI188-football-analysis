#!/usr/bin/env python3
"""V6.12.0 research-only 100-match rapid screen for manager/referee information.

Purpose: test whether prematch-knowable manager tenure/change and referee historical-bias
features add Top-1 1X2 accuracy over the devigged market anchor.

Chronology:
- all manager/referee statistics use only matches strictly earlier than the target match;
- the final 100 joined 2025/26 matches are untouched TEST;
- the preceding 200 joined 2025/26 matches are VALIDATION;
- all earlier joined matches are TRAIN;
- target match outcome is never used in its own features.

The Transfermarkt historical file does not preserve the original publication timestamp of
manager/referee appointments, and the historical odds file lacks original quote timestamps.
Therefore this is RETROSPECTIVE_RESEARCH_ONLY and cannot alter formal CURRENT.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import validate_1x2_pit_lineup_increment_v6117c as joins
from platform_core import normalize_team_token

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_1x2_manager_referee_fast100_v6120_status.json"
GAMES_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz"
TM_COMP = {
    "ENG_PremierLeague": "GB1",
    "GER_Bundesliga": "L1",
    "ITA_SerieA": "IT1",
    "FRA_Ligue1": "FR1",
    "ESP_LaLiga": "ES1",
}
INV_TM_COMP = {v:k for k,v in TM_COMP.items()}
LABEL = {"home":0, "draw":1, "away":2}
CS = (0.001, 0.003, 0.01, 0.03, 0.1)


def fetch_games():
    req = urllib.request.Request(GAMES_URL, headers={"User-Agent":"football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        raw = response.read()
    text = gzip.decompress(raw).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    rows = []
    for r in reader:
        comp = str(r.get("competition_id") or "").strip()
        if comp not in INV_TM_COMP:
            continue
        try:
            sy = int(str(r.get("season") or "").strip())
        except ValueError:
            continue
        season = f"{sy}/{str((sy+1)%100).zfill(2)}"
        if season not in {"2021/22","2022/23","2023/24","2024/25","2025/26"}:
            continue
        date = str(r.get("date") or "").strip()[:10]
        home = str(r.get("home_club_name") or r.get("home_club_name_x") or "").strip()
        away = str(r.get("away_club_name") or r.get("away_club_name_x") or "").strip()
        hm = str(r.get("home_club_manager_name") or "").strip()
        am = str(r.get("away_club_manager_name") or "").strip()
        ref = str(r.get("referee") or "").strip()
        if not date or not home or not away:
            continue
        rows.append({
            "competition_id": INV_TM_COMP[comp], "season": season, "date": date,
            "home_raw": home, "away_raw": away,
            "home_tm": normalize_team_token(home), "away_tm": normalize_team_token(away),
            "home_manager": hm, "away_manager": am, "referee": ref,
        })
    return rows, {"url":GAMES_URL,"compressed_bytes":len(raw),"columns":columns,"row_count":len(rows)}


def market_matches():
    rows = joins.base._load_matches()
    out = []
    for r in rows:
        rr = dict(r)
        rr["date"] = str(rr["date"])[:10]
        rr["home"] = normalize_team_token(rr["home"])
        rr["away"] = normalize_team_token(rr["away"])
        out.append(rr)
    return out


def build_identity_maps(mm, tm):
    market_sets = defaultdict(set); tm_sets = defaultdict(set)
    for r in mm:
        market_sets[(r["competition_id"],r["season"])].update((r["home"],r["away"]))
    for r in tm:
        tm_sets[(r["competition_id"],r["season"])].update((r["home_tm"],r["away_tm"]))
    maps = {}; audit=[]
    for key,left in market_sets.items():
        mp, diag = joins._greedy_bijection(left, tm_sets.get(key,set()))
        maps[key] = mp
        audit.append({
            "competition_id":key[0],"season":key[1],"market_team_count":len(left),
            "tm_team_count":len(tm_sets.get(key,set())),"mapped_count":len(mp),
            "unmapped_market":sorted(left-set(mp)),
            "unmapped_tm":sorted(tm_sets.get(key,set())-set(mp.values())),
            "non_exact_count":len(diag),
        })
    return maps,audit


def join_rows(mm, tm):
    maps,audit = build_identity_maps(mm,tm)
    idx={}
    for r in tm:
        inv={v:k for k,v in maps.get((r["competition_id"],r["season"]),{}).items()}
        h=inv.get(r["home_tm"]); a=inv.get(r["away_tm"])
        if h is None or a is None:
            continue
        idx[(r["competition_id"],r["season"],r["date"],h,a)] = r
    joined=[]
    for r in mm:
        tr=idx.get((r["competition_id"],r["season"],r["date"],r["home"],r["away"]))
        if not tr:
            continue
        x=dict(r); x.update({"home_manager":tr["home_manager"],"away_manager":tr["away_manager"],"referee":tr["referee"]})
        joined.append(x)
    joined.sort(key=lambda r:(r["date"],r["competition_id"],r["home"],r["away"]))
    return joined,audit


def actual_points(result, side):
    if result=="draw": return 1.0
    if side=="home": return 3.0 if result=="home" else 0.0
    return 3.0 if result=="away" else 0.0


def expected_points(p, side):
    ph,pd,pa=p
    return 3*ph+pd if side=="home" else 3*pa+pd


def build_features(rows):
    team_last_mgr={}
    team_mgr_tenure=defaultdict(int)
    mgr_resid_sum=defaultdict(float); mgr_n=defaultdict(int)
    ref_obs=defaultdict(lambda: np.zeros(3,float)); ref_exp=defaultdict(lambda: np.zeros(3,float)); ref_n=defaultdict(int)
    out=[]
    for r in rows:
        hm=r.get("home_manager") or ""; am=r.get("away_manager") or ""; ref=r.get("referee") or ""
        hk=(r["competition_id"],r["home"]); ak=(r["competition_id"],r["away"])
        hprev=team_last_mgr.get(hk); aprev=team_last_mgr.get(ak)
        hchange=1.0 if hprev and hm and hm!=hprev else 0.0
        achange=1.0 if aprev and am and am!=aprev else 0.0
        hten=team_mgr_tenure[(hk,hm)] if hm else 0
        aten=team_mgr_tenure[(ak,am)] if am else 0
        hmr=mgr_resid_sum[hm]/(mgr_n[hm]+12.0) if hm else 0.0
        amr=mgr_resid_sum[am]/(mgr_n[am]+12.0) if am else 0.0
        if ref and ref_n[ref]:
            resid=(ref_obs[ref]-ref_exp[ref])/(ref_n[ref]+20.0)
            rh,rd,ra=[float(z) for z in resid]
        else:
            rh=rd=ra=0.0
        p=r["opening"]
        base=[p[0],p[1],p[2],max(p),sorted(p,reverse=True)[0]-sorted(p,reverse=True)[1]]
        manager=[hchange,achange,float(hten<=2 and hm!=''),float(aten<=2 and am!=''),math.log1p(hten),math.log1p(aten),hmr,amr,hmr-amr]
        referee=[rh,rd,ra,float(ref_n[ref]) if ref else 0.0]
        out.append({**r,"manager_feat":base+manager,"ref_feat":base+referee,"both_feat":base+manager+referee})
        # update histories strictly after feature creation
        if hm:
            team_last_mgr[hk]=hm; team_mgr_tenure[(hk,hm)]+=1
            mgr_resid_sum[hm]+=actual_points(r["actual"],"home")-expected_points(p,"home"); mgr_n[hm]+=1
        if am:
            team_last_mgr[ak]=am; team_mgr_tenure[(ak,am)]+=1
            mgr_resid_sum[am]+=actual_points(r["actual"],"away")-expected_points(p,"away"); mgr_n[am]+=1
        if ref:
            y=LABEL[r["actual"]]; ref_obs[ref][y]+=1.0; ref_exp[ref]+=np.asarray(p); ref_n[ref]+=1
    return out


def fit_select(train,val,test,feat):
    Xtr=np.asarray([r[feat] for r in train]); ytr=np.asarray([LABEL[r["actual"]] for r in train])
    Xv=np.asarray([r[feat] for r in val]); yv=np.asarray([LABEL[r["actual"]] for r in val])
    Xt=np.asarray([r[feat] for r in test]); yt=np.asarray([LABEL[r["actual"]] for r in test])
    board=[]
    for C in CS:
        model=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=2000,multi_class="auto"))
        model.fit(Xtr,ytr); pv=model.predict(Xv); board.append((float((pv==yv).mean()),C,model))
    board.sort(key=lambda x:(x[0],-x[1]),reverse=True)
    vacc,C,model=board[0]
    pred=model.predict(Xt)
    mh=np.asarray([int(np.argmax(r["opening"])) for r in test])
    market_correct=mh==yt; model_correct=pred==yt
    return {"selected_C":C,"validation_accuracy":vacc,"test_hits":int(model_correct.sum()),"test_accuracy":float(model_correct.mean()),
            "paired":{"both_correct":int((market_correct&model_correct).sum()),"market_only":int((market_correct&~model_correct).sum()),"model_only":int((~market_correct&model_correct).sum()),"both_wrong":int((~market_correct&~model_correct).sum())}}


def main():
    mm=market_matches(); tm,source=fetch_games(); joined,identity=join_rows(mm,tm); rows=build_features(joined)
    latest=[r for r in rows if r["season"]=="2025/26"]
    if len(latest)<400: raise RuntimeError(f"insufficient 2025/26 joined rows: {len(latest)}")
    test=latest[-100:]; val=latest[-300:-100]
    test_keys={(r["competition_id"],r["date"],r["home"],r["away"]) for r in test}
    val_keys={(r["competition_id"],r["date"],r["home"],r["away"]) for r in val}
    train=[r for r in rows if (r["competition_id"],r["date"],r["home"],r["away"]) not in test_keys|val_keys and (r["season"]!="2025/26" or r["date"]<val[0]["date"])]
    yt=np.asarray([LABEL[r["actual"]] for r in test]); mp=np.asarray([int(np.argmax(r["opening"])) for r in test])
    market_hits=int((mp==yt).sum())
    payload={
        "schema_version":"V6.12.0-manager-referee-fast100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS",
        "formal_current_version":"V5.0.1","classification":"RETROSPECTIVE_RESEARCH_ONLY_PREMATCH_KNOWABLE_NO_ORIGINAL_ANNOUNCEMENT_OR_ODDS_TIMESTAMP",
        "governance":{"test_matches":100,"test_untouched_for_selection":True,"all_rolling_features_strictly_prior":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
        "source":source,"sample":{"joined_all":len(rows),"joined_2025_26":len(latest),"train":len(train),"validation":len(val),"test":len(test),"test_first":test[0]["date"],"test_last":test[-1]["date"]},
        "test":{"market":{"hits":market_hits,"accuracy":market_hits/100.0},"market_plus_manager":fit_select(train,val,test,"manager_feat"),"market_plus_referee":fit_select(train,val,test,"ref_feat"),"market_plus_manager_referee":fit_select(train,val,test,"both_feat")},
        "identity_audit":identity,
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload["test"],indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
