#!/usr/bin/env python3
from __future__ import annotations

# Strict prematch reconstruction: target-match result/events are excluded.
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lyon_fenerbahce_20260826 as b

OUT = HERE / "results"
TARGET_DAY = "2026-08-25"


def resolve_cup(leagues: pd.DataFrame) -> str:
    id_col = "id" if "id" in leagues.columns else "league_id"
    cols = b.string_columns(leagues)
    hits = []
    for row in leagues.itertuples(index=False):
        d = row._asdict()
        texts = [str(d.get(c) or "").strip() for c in cols]
        blob = " | ".join(texts).lower()
        if "england" in blob and ("carabao cup" in blob or ("league cup" in blob and "eng" in blob)):
            hits.append((int(d[id_col]), texts))
    if not hits:
        raise RuntimeError("England Carabao/League Cup not found")
    hits.sort(key=lambda x: (0 if x[0] == 96 else 1, x[0]))
    return str(hits[0][0])


def main():
    rows, st, draw_state, k1, k3 = b.fit_locked_models_and_states()
    tp = b.download_table("teams"); lp = b.download_table("leagues")
    teams = pd.read_parquet(tp); leagues = pd.read_parquet(lp)
    R = lambda n, a=(): b.resolve_team(teams, n, a)

    forest = R("Nottingham Forest", ("Nottm Forest", "Nottingham Forest FC"))
    leeds = R("Leeds United", ("Leeds", "Leeds United FC"))
    cup = resolve_cup(leagues)
    fid = forest["team_id"]; lid = leeds["team_id"]
    fdom = b.latest_comp_for_team(rows, fid, cup)
    ldom = b.latest_comp_for_team(rows, lid, cup)
    prem = fdom
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    target = {"date":TARGET_DAY,"game_id":"WEB_NFO_LEE_CUP_20260825","competition_id":cup,
              "home_team":fid,"away_team":lid,"home_goals":0,"away_goals":0,
              "home_xg":0.0,"away_xg":0.0}
    raw0 = st.pred(target); pred0 = b.predict(k1,k3,draw_state,target,raw0)

    update = {
      "label":"Nottingham Forest 0-1 Leeds United, Premier League",
      "include_xg":True,
      "row":{"date":"2026-08-22","game_id":"WEB_NFO_LEE_PL_20260822","competition_id":prem,
             "home_team":fid,"away_team":lid,"home_goals":0,"away_goals":1,
             "home_xg":0.65,"away_xg":0.49},
      "evidence":"BBC/FotMob post-match xG; score completed three days before target kickoff"
    }
    b.goal_update(st, update["row"], True); draw_state.update(update["row"])
    raw1 = st.pred(target); pred1 = b.predict(k1,k3,draw_state,target,raw1)

    out = {
      "status":"COMPLETE",
      "target":{"display":"Nottingham Forest vs Leeds United","competition":"EFL Cup second round",
                "local_day":TARGET_DAY,"beijing_time":"2026-08-26 03:00","competition_id":cup,
                "home_team_id":fid,"away_team_id":lid},
      "governance":{"strict_prior_snapshot":True,"odds_used":False,"market_prices_used":False,
                    "manual_probability_adjustment":False,"post_match_target_data_used":False,
                    "same_day_leakage":False,"target_result_read_by_model":False,
                    "injury_or_lineup_manual_adjustment":False,"friendlies_used":False,
                    "R15_gate_threshold":b.DRAW_GATE},
      "models":{"K1":"R9b retained baseline","K3":"R14 compact 9-feature challenger",
                "R15_gate":"locked validation-selected draw gate"},
      "team_resolution":{"forest":forest,"leeds":leeds,"cup_comp":cup,
                         "forest_latest_non_cup_comp":fdom,"leeds_latest_non_cup_comp":ldom,
                         "prematch_update_comp":prem},
      "snapshot_only":{"prediction":pred0,"raw":raw0,"score_peaks":b.score_peaks(raw0)},
      "pre_match_updated":{"prediction":pred1,"raw":raw1,"score_peaks":b.score_peaks(raw1)},
      "pre_match_updates":[update],
      "external_context_not_model_inputs":[
        "Nottingham Forest pre-kickoff absences: Morgan Gibbs-White, Ryan Yates, Nicolo Savona reported unavailable",
        "Leeds pre-kickoff absences: Willy Gnonto and Gabriel Gudmundsson not back in team training",
        "Confirmed starting lineups were available before kickoff but current K1/K3 has no player-value interface; no manual probability adjustment",
        "Pre-match reports expected Forest to use a relatively strong cup side and Leeds to rotate; not quantified into probabilities",
        "No odds, handicap or market prices used; user-supplied -1 ignored"
      ]
    }
    OUT.mkdir(parents=True,exist_ok=True)
    p=OUT/"nottingham_leeds_20260825.json"
    p.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
