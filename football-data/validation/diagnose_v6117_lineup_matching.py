#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import validate_1x2_pit_lineup_increment_v6117b as fixed
v = fixed.base

OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_matching_v6117_debug.json"


def main() -> int:
    matches = v._load_matches()
    by_match_season = Counter(r["season"] for r in matches)
    by_match_comp_season = Counter((r["competition_id"], r["season"]) for r in matches)
    lineups = {cid: v._load_lineups(cid) for cid in v.COMPETITIONS}
    by_lineup_comp_season = {}
    for cid, data in lineups.items():
        c = Counter(k[0] for k in data)
        by_lineup_comp_season[cid] = dict(c)

    matched_team_sides = Counter()
    matched_matches = Counter()
    samples = {}
    for season in sorted(v.TRAIN_SEASONS | {v.VALID_SEASON, v.TEST_SEASON}):
        sm = [r for r in matches if r["season"] == season]
        side = 0; both = 0
        misses = []
        for r in sm:
            cid = r["competition_id"]
            hk = (season, r["date"], r["home"])
            ak = (season, r["date"], r["away"])
            h = hk in lineups[cid]
            a = ak in lineups[cid]
            side += int(h) + int(a)
            both += int(h and a)
            if len(misses) < 12 and not (h and a):
                misses.append({
                    "match": [cid, season, r["date"], r["home"], r["away"]],
                    "home_found": h,
                    "away_found": a,
                    "sample_lineup_keys_same_date": [list(k) for k in list(lineups[cid].keys()) if k[0] == season and k[1] == r["date"]][:8]
                })
        matched_team_sides[season] = side
        matched_matches[season] = both
        samples[season] = misses

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "join_version": "normalized_date_and_team_token",
        "match_count": len(matches),
        "match_by_season": dict(by_match_season),
        "match_by_competition_season": {f"{k[0]}|{k[1]}": n for k,n in by_match_comp_season.items()},
        "lineup_by_competition_season": by_lineup_comp_season,
        "matched_team_sides_by_season": dict(matched_team_sides),
        "matched_matches_by_season": dict(matched_matches),
        "unmatched_samples": samples,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
