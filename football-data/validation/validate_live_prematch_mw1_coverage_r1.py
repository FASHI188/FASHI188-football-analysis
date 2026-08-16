#!/usr/bin/env python3
"""Zero-label operational coverage test over frozen 2026/27 Premier League MW1 fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from live_prematch_runtime_r12 import run_live_prematch  # noqa: E402

OUT = ROOT / "research" / "artifacts" / "live_prematch_runtime_r1" / "mw1_coverage_status.json"
FREEZE = "2026-08-16T13:34:00Z"

FIXTURES = [
    ("ENG_PL_2026_27_MW1_ARS_COV", "2026-08-21T19:00:00Z", "Arsenal", "Arsenal", "Coventry City", "Coventry City"),
    ("ENG_PL_2026_27_MW1_HUL_MUN", "2026-08-22T11:30:00Z", "Hull City", "Hull City", "Manchester United", "Man United"),
    ("ENG_PL_2026_27_MW1_EVE_CRY", "2026-08-22T14:00:00Z", "Everton", "Everton", "Crystal Palace", "Crystal Palace"),
    ("ENG_PL_2026_27_MW1_IPS_SUN", "2026-08-22T14:00:00Z", "Ipswich Town", "Ipswich", "Sunderland", "Sunderland"),
    ("ENG_PL_2026_27_MW1_NFO_LEE", "2026-08-22T14:00:00Z", "Nottingham Forest", "Nott'm Forest", "Leeds United", "Leeds"),
    ("ENG_PL_2026_27_MW1_BRE_TOT", "2026-08-22T16:30:00Z", "Brentford", "Brentford", "Tottenham Hotspur", "Tottenham"),
    ("ENG_PL_2026_27_MW1_BHA_AVL", "2026-08-23T13:00:00Z", "Brighton & Hove Albion", "Brighton", "Aston Villa", "Aston Villa"),
    ("ENG_PL_2026_27_MW1_MCI_BOU", "2026-08-23T13:00:00Z", "Manchester City", "Man City", "AFC Bournemouth", "Bournemouth"),
    ("ENG_PL_2026_27_MW1_NEW_LIV", "2026-08-23T15:30:00Z", "Newcastle United", "Newcastle", "Liverpool", "Liverpool"),
    ("ENG_PL_2026_27_MW1_FUL_CHE", "2026-08-24T19:00:00Z", "Fulham", "Fulham", "Chelsea", "Chelsea"),
]


def main() -> int:
    rows = []
    for match_id, kickoff, home, sh, away, sa in FIXTURES:
        payload = {
            "event_competition_id": "ENG_PremierLeague", "strength_reference_competition_id": "ENG_PremierLeague",
            "season": "2026/27", "home_team": home, "away_team": away,
            "strength_home_team": sh, "strength_away_team": sa, "kickoff_utc": kickoff,
            "freeze_time_utc": FREEZE, "neutral_venue": False, "evidence": []
        }
        try:
            result = run_live_prematch(payload)
            rows.append({"match_id": match_id, "home_team": home, "away_team": away, "status": "PASS",
                         "route": result["route"]["selected"], "one_x_two": result["probabilities"]["one_x_two"],
                         "top_score": result["conclusions"]["top_score"]})
        except Exception as exc:
            rows.append({"match_id": match_id, "home_team": home, "away_team": away, "status": "FAIL",
                         "error": f"{type(exc).__name__}: {exc}"})
    passed = sum(r["status"] == "PASS" for r in rows); failed = len(rows) - passed
    report = {
        "schema_version": "live-prematch-runtime-r1.2-mw1-coverage",
        "classification": "ZERO_LABEL_FUTURE_FIXTURE_ENGINEERING_COVERAGE",
        "freeze_time_utc": FREEZE, "fixture_count": len(rows), "pass_count": passed, "fail_count": failed,
        "coverage": passed / len(rows), "status": "PASS_FULL_COVERAGE" if failed == 0 else "PARTIAL_COVERAGE",
        "rows": rows, "target_results_read": False, "formal_weight_changed": False
    }
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
