#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lyon_fenerbahce_20260826 as b  # reuse locked K1/K3 plumbing

OUT = HERE / "results"
TARGET_DAY = "2026-08-26"


def main():
    rows, st, draw_state, k1, k3 = b.fit_locked_models_and_states()
    tp = b.download_table("teams"); lp = b.download_table("leagues")
    teams = pd.read_parquet(tp); leagues = pd.read_parquet(lp)
    R = lambda n, a=(): b.resolve_team(teams, n, a)

    viking = R("Viking", ("Viking FK",))
    dinamo = R("Dinamo Zagreb", ("GNK Dinamo Zagreb", "Dinamo Zagreb"))
    sarps = R("Sarpsborg 08", ("Sarpsborg",))
    sandefjord = R("Sandefjord", ("Sandefjord Fotball",))
    lillestrom = R("Lillestrom", ("Lillestrøm", "Lillestrøm SK"))
    aalesund = R("Aalesund", ("Aalesunds FK", "Aalesunds"))
    start = R("Start", ("IK Start",))
    rosenborg = R("Rosenborg", ("Rosenborg BK",))
    thun = R("Thun", ("FC Thun",))
    slaven = R("Slaven", ("Slaven Belupo", "NK Slaven Belupo"))
    kauno = R("FK Kauno Zalgiris", ("Kauno Zalgiris", "FK Kauno Žalgiris"))
    rudes = R("Rudes", ("Rudeš", "NK Rudes", "NK Rudeš"))
    varazdin = R("NK Varazdin", ("Varazdin", "NK Varaždin"))
    ucl = b.resolve_ucl_league(leagues)
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    vid = viking["team_id"]; did = dinamo["team_id"]
    vdom = b.latest_comp_for_team(rows, vid, ucl)
    ddom = b.latest_comp_for_team(rows, did, ucl)
    target = {"date":TARGET_DAY,"game_id":"WEB_VIK_DIN_20260826","competition_id":ucl,
              "home_team":vid,"away_team":did,"home_goals":0,"away_goals":0,"home_xg":0.0,"away_xg":0.0}

    raw0 = st.pred(target); pred0 = b.predict(k1,k3,draw_state,target,raw0)

    def row(dt,gid,comp,h,a,hg,ag,hx=0.0,ax=0.0):
        return {"date":dt,"game_id":gid,"competition_id":comp,"home_team":h,"away_team":a,
                "home_goals":hg,"away_goals":ag,"home_xg":hx,"away_xg":ax}

    updates = [
      {"label":"Sarpsborg 08 1-0 Viking, Eliteserien","include_xg":False,"row":row("2026-07-12","WEB_SAR_VIK",vdom,sarps["team_id"],vid,1,0),"evidence":"FotMob score; xG omitted"},
      {"label":"Viking 2-1 Sandefjord, Eliteserien","include_xg":False,"row":row("2026-07-18","WEB_VIK_SAN",vdom,vid,sandefjord["team_id"],2,1),"evidence":"FotMob score; xG omitted"},
      {"label":"Thun 1-1 Dinamo Zagreb, UCL Q2 first leg","include_xg":False,"row":row("2026-07-21","WEB_THU_DIN_1",ucl,thun["team_id"],did,1,1),"evidence":"FotMob/UEFA score; xG omitted"},
      {"label":"Lillestrom 1-2 Viking, Eliteserien","include_xg":False,"row":row("2026-07-22","WEB_LIL_VIK",vdom,lillestrom["team_id"],vid,1,2),"evidence":"FotMob score; xG omitted"},
      {"label":"Aalesund 1-1 Viking, Eliteserien","include_xg":True,"row":row("2026-07-26","WEB_AAL_VIK",vdom,aalesund["team_id"],vid,1,1,1.98,1.70),"evidence":"FotMob/Opta post-match xG"},
      {"label":"Dinamo Zagreb 3-2 Thun, UCL Q2 second leg","include_xg":False,"row":row("2026-07-28","WEB_DIN_THU_2",ucl,did,thun["team_id"],3,2),"evidence":"FotMob/UEFA score; xG omitted"},
      {"label":"Dinamo Zagreb 2-0 Slaven, HNL","include_xg":False,"row":row("2026-07-31","WEB_DIN_SLA",ddom,did,slaven["team_id"],2,0),"evidence":"HNL/FotMob score; xG omitted"},
      {"label":"Start 0-3 Viking, Eliteserien","include_xg":False,"row":row("2026-08-01","WEB_STA_VIK",vdom,start["team_id"],vid,0,3),"evidence":"FotMob score; xG omitted"},
      {"label":"Dinamo Zagreb 5-0 FK Kauno Zalgiris, UCL Q3 first leg","include_xg":False,"row":row("2026-08-04","WEB_DIN_KAU_1",ucl,did,kauno["team_id"],5,0),"evidence":"FotMob/UEFA score; xG omitted"},
      {"label":"Viking 2-1 Sarpsborg 08, Eliteserien","include_xg":True,"row":row("2026-08-08","WEB_VIK_SAR",vdom,vid,sarps["team_id"],2,1,2.74,2.58),"evidence":"FotMob/Opta post-match xG"},
      {"label":"FK Kauno Zalgiris 1-2 Dinamo Zagreb, UCL Q3 second leg","include_xg":False,"row":row("2026-08-11","WEB_KAU_DIN_2",ucl,kauno["team_id"],did,1,2),"evidence":"FotMob/UEFA score; xG omitted"},
      {"label":"Rosenborg 2-1 Viking, Eliteserien","include_xg":True,"row":row("2026-08-14","WEB_ROS_VIK",vdom,rosenborg["team_id"],vid,2,1,1.49,1.42),"evidence":"FotMob/Opta post-match xG"},
      {"label":"Dinamo Zagreb 4-0 Rudes, HNL","include_xg":False,"row":row("2026-08-14","WEB_DIN_RUD",ddom,did,rudes["team_id"],4,0),"evidence":"HNL/FotMob score; non-Opta xG deliberately omitted"},
      {"label":"Dinamo Zagreb 2-2 Viking, UCL playoff first leg","include_xg":True,"row":row("2026-08-18","WEB_DIN_VIK_1",ucl,did,vid,2,2,2.63,0.93),"evidence":"FotMob/Opta post-match xG; shots 23-6"},
      {"label":"Dinamo Zagreb 3-1 NK Varazdin, HNL","include_xg":False,"row":row("2026-08-22","WEB_DIN_VAR",ddom,did,varazdin["team_id"],3,1),"evidence":"HNL/FotMob score; conflicting xG sources omitted"},
    ]
    for u in updates:
        b.goal_update(st,u["row"],u["include_xg"]); draw_state.update(u["row"])

    raw1 = st.pred(target); pred1 = b.predict(k1,k3,draw_state,target,raw1)
    out = {
      "status":"COMPLETE",
      "target":{"display":"Viking vs Dinamo Zagreb","uefa_day":TARGET_DAY,"beijing_time":"2026-08-27 03:00","competition_id":ucl,"home_team_id":vid,"away_team_id":did},
      "governance":{"strict_prior_snapshot":True,"odds_used":False,"market_prices_used":False,"manual_probability_adjustment":False,"post_match_target_data_used":False,"same_day_leakage":False,"injury_or_lineup_manual_adjustment":False,"R15_gate_threshold":b.DRAW_GATE},
      "models":{"K1":"R9b retained baseline","K3":"R14 compact 9-feature challenger; R17 fresh winner 46/81 vs K1 45/81","R15_gate":"locked validation-selected draw gate"},
      "team_resolution":{"viking":viking,"dinamo":dinamo,"viking_domestic_comp":vdom,"dinamo_domestic_comp":ddom},
      "snapshot_only":{"prediction":pred0,"raw":raw0,"score_peaks":b.score_peaks(raw0)},
      "pre_match_updated":{"prediction":pred1,"raw":raw1,"score_peaks":b.score_peaks(raw1)},
      "pre_match_updates":updates,
      "external_context_not_model_inputs":[
        "Viking injuries reported: Anders Baertelsen, Martin Ove Roseth, Veton Berisha unavailable",
        "Viking had no weekend league match after first leg; Dinamo beat Varazdin 3-1 on 2026-08-22",
        "Predicted lineups and rest-day context not converted into probabilities",
        "No odds or market prices used"
      ]
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p=OUT/"viking_dinamo_20260826.json"
    p.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
