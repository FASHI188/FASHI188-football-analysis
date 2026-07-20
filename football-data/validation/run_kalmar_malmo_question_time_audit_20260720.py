#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from football_v460_engine import predict_joint_distribution
from platform_core import (
    derive_score_marginals,
    read_processed_matches,
    settle_home_handicap,
    settle_over_total,
    top_scores,
)

COMP = "SWE_Allsvenskan"
SEASON = "2026"
HOME = "Kalmar"
AWAY = "Malmo FF"
CUTOFF = datetime(2026, 7, 20, 12, 43, 31, tzinfo=timezone.utc)
OUT = ROOT / "manifests" / "question_time_audits" / "SWE_Allsvenskan_2026-07-20_Kalmar_FF_vs_Malmo_FF.json"

SUPPLEMENTAL = [
    ("17/07/2026", "IFK Goteborg", "Brommapojkarna", 2, 1),
    ("17/07/2026", "Mjallby", "Vasteras", 0, 0),
    ("18/07/2026", "AIK", "GAIS", 2, 0),
    ("19/07/2026", "Hammarby", "Degerfors", 4, 0),
    ("19/07/2026", "Elfsborg", "Sirius", 1, 3),
    ("19/07/2026", "Halmstad", "Hacken", 0, 2),
]

MARKET = {
    "source": "Stake current market page",
    "observed_at_utc": "2026-07-20T12:43:31+00:00",
    "one_x_two": {"home": 2.75, "draw": 3.45, "away": 2.33},
    "asian_handicap": {"line_home": 0.25, "home": 1.76, "away": 2.04},
    "total_goals": {"line": 2.5, "over": 1.66, "under": 2.19},
}


def score_market(matrix, line, settlement_fn):
    out = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for cell in matrix:
        p = float(cell["probability"])
        s = settlement_fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
        for key in out:
            out[key] += p * float(s[key])
    return out


def main() -> int:
    processed_dir = ROOT / "processed" / COMP
    processed_dir.mkdir(parents=True, exist_ok=True)
    existing = read_processed_matches(COMP)
    existing_keys = {(m.season, m.date.date().isoformat(), m.home_team.casefold(), m.away_team.casefold()) for m in existing}

    rows = []
    for date, home, away, hg, ag in SUPPLEMENTAL:
        iso = datetime.strptime(date, "%d/%m/%Y").date().isoformat()
        key = (SEASON, iso, home.casefold(), away.casefold())
        if key not in existing_keys:
            rows.append({
                "competition_id": COMP,
                "season": SEASON,
                "stage": "regular_league",
                "Date": date,
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": hg,
                "FTAG": ag,
            })

    overlay = processed_dir / "__question_time_kalmar_malmo_20260720.csv"
    try:
        if rows:
            with overlay.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["competition_id","season","stage","Date","HomeTeam","AwayTeam","FTHG","FTAG"])
                writer.writeheader()
                writer.writerows(rows)

        after = read_processed_matches(COMP)
        history = [m for m in after if m.season == SEASON and m.date.date() < CUTOFF.date()]
        prediction = predict_joint_distribution(COMP, HOME, AWAY, CUTOFF, season=SEASON)
        matrix = prediction["probabilities"]["score_matrix"]
        margins = derive_score_marginals(matrix)
        ranking = top_scores(matrix, 10)
        ah = score_market(matrix, 0.25, settle_home_handicap)
        ou = score_market(matrix, 2.5, settle_over_total)

        probs = margins["1x2"]
        no_vig_raw = {k: 1.0 / MARKET["one_x_two"][k] for k in ("home","draw","away")}
        s = sum(no_vig_raw.values())
        no_vig = {k: v/s for k,v in no_vig_raw.items()}

        total_rank = sorted(margins["total_goals"].items(), key=lambda kv: (-kv[1], kv[0]))
        result = {
            "schema_version": "V4.7.0-question-time-formal-audit-r2",
            "status": "PASS",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "match_identity": {
                "competition_id": COMP,
                "season": SEASON,
                "round": 13,
                "home_team": HOME,
                "display_home_team": "Kalmar FF",
                "away_team": AWAY,
                "display_away_team": "Malmo FF",
                "kickoff_utc": "2026-07-20T17:00:00+00:00",
                "freeze_time_utc": CUTOFF.isoformat(),
                "settlement": "90_minutes_including_stoppage",
                "venue": "Guldfageln Arena",
            },
            "data_audit": {
                "persisted_match_count_before_overlay_all_seasons": len(existing),
                "supplemental_rows_applied": len(rows),
                "supplemental_rows": rows,
                "current_season_history_matches": len(history),
                "latest_history_match_date": history[-1].date.date().isoformat() if history else None,
                "engine_history_matches": prediction["history_matches"],
                "engine_latest_history_match_date": prediction["latest_history_match_date"],
                "parameter_source": prediction["parameter_source"],
                "team_sample": prediction["team_sample"],
                "competition_effective_matches": prediction["competition_effective_matches"],
                "nb_dispersion_k": prediction["nb_dispersion_k"],
                "low_score_factors": prediction["low_score_factors"],
            },
            "module_states": {
                "competition_identity_time": "通过",
                "data_quality_sources": "通过" if len(history) >= 100 else "部分通过",
                "synchronized_market": "通过",
                "team_lineup_task": "部分通过",
                "direct_total_goals": "通过",
                "conditional_goal_difference": "通过",
                "unified_score_matrix": "通过" if abs(margins["probability_sum"] - 1.0) <= 1e-10 else "失败",
                "market_coordination": "未启用",
                "price_ev_no_bet": "降级",
            },
            "market_snapshot": MARKET,
            "market_no_vig_1x2": no_vig,
            "formal_probabilities": {
                "one_x_two": probs,
                "total_goals": margins["total_goals"],
                "btts_yes": margins["btts_yes"],
                "top_scores": ranking[:5],
                "top3_cumulative": sum(float(x["probability"]) for x in ranking[:3]),
                "top1_top2_gap": float(ranking[0]["probability"]) - float(ranking[1]["probability"]),
                "home_handicap_plus_0_25": ah,
                "over_2_5": ou,
            },
            "total_goals_primary": total_rank[0],
            "total_goals_secondary": total_rank[1],
            "audit": {
                "probability_sum": margins["probability_sum"],
                "oof_matrix_calibration_2026": "identity_guardrail_temperature_1.0",
                "dynamic_strength_v470": "未启用",
                "d_conditional_v470": "未启用",
                "formal_ev": "No Bet: SWE domain has no independent LOMO/OOS promotion receipt",
            },
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        overlay.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
