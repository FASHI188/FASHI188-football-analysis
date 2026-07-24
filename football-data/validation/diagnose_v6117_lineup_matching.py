#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import validate_1x2_pit_lineup_increment_v6117c as fixed
v = fixed.base

OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_matching_v6117_debug.json"


def main() -> int:
    matches = v._load_matches()
    lineups = {cid: v._load_lineups(cid) for cid in v.COMPETITIONS}
    matched_team_sides = Counter(); matched_matches = Counter(); by_comp = Counter(); misses = []
    for r in matches:
        cid, season = r["competition_id"], r["season"]
        h = (season, r["date"], r["home"]) in lineups[cid]
        a = (season, r["date"], r["away"]) in lineups[cid]
        matched_team_sides[season] += int(h)+int(a)
        matched_matches[season] += int(h and a)
        by_comp[(cid, season)] += int(h and a)
        if season == v.TEST_SEASON and not (h and a) and len(misses) < 30:
            misses.append({"competition_id":cid,"date":r["date"],"home":r["home"],"away":r["away"],"home_found":h,"away_found":a})
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status":"PASS",
        "join_version":"research_only_competition_season_bijection",
        "match_count":len(matches),
        "matched_team_sides_by_season":dict(matched_team_sides),
        "matched_matches_by_season":dict(matched_matches),
        "matched_matches_by_competition_season":{f"{k[0]}|{k[1]}":n for k,n in by_comp.items()},
        "test_unmatched_samples":misses,
        "identity_join_audit": fixed._ensure()[2],
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
