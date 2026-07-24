#!/usr/bin/env python3
"""V6.13.2 fixed-rule disjoint replication for V6.13.1 injury-onset signal.

No rule or threshold selection occurs here. The pre-specified rule is exactly the one
flagged by V6.13.1: compare p>=0.58 and p>=0.60 with versus without any expected-XI
favorite player having an injury onset 1-14 days before the target match.

Replication block = the 100 injury-exposed matches immediately preceding the V6.13.1
Fast100 block. It is disjoint from the discovery block. Research only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import validate_1x2_injury_onset_fast100_v6131 as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_1x2_injury_onset_replication100_v6132_status.json"


def main():
    injuries, source = base.load_injury_onsets()
    rows = base.build_rows(injuries)
    scope = [r for r in rows if r["season"] in base.TARGET_SEASONS]
    affected = [r for r in scope if r["home_injured_players_14"] or r["away_injured_players_14"]]
    if len(affected) < 200:
        raise RuntimeError(f"need >=200 affected matches for disjoint replication, found {len(affected)}")

    discovery = affected[-100:]
    test = affected[-200:-100]
    overlap = {
        (r["competition_id"], r["date"], r["home"], r["away"]) for r in discovery
    } & {
        (r["competition_id"], r["date"], r["home"], r["away"]) for r in test
    }
    if overlap:
        raise RuntimeError(f"replication overlap detected: {len(overlap)}")

    fixed = {
        "p_ge_0.58": base.stat(test, lambda r: max(r["opening"]) >= 0.58),
        "p_ge_0.58_exclude_fav_any_14d": base.stat(
            test,
            lambda r: max(r["opening"]) >= 0.58 and r["fav_injured_players_14"] == 0,
        ),
        "p_ge_0.60": base.stat(test, lambda r: max(r["opening"]) >= 0.60),
        "p_ge_0.60_exclude_fav_any_14d": base.stat(
            test,
            lambda r: max(r["opening"]) >= 0.60 and r["fav_injured_players_14"] == 0,
        ),
        "favorite_any_14d": base.stat(test, lambda r: r["fav_injured_players_14"] >= 1),
        "all_affected14": base.stat(test),
    }

    def uplift(base_key, filtered_key):
        a = fixed[base_key]["accuracy"]
        b = fixed[filtered_key]["accuracy"]
        return None if a is None or b is None else (b - a) * 100.0

    payload = {
        "schema_version": "V6.13.2-injury-onset-replication100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_INJURY_ONSET_DATE_NO_ORIGINAL_PUBLICATION_TIMESTAMP",
        "governance": {
            "fixed_rule_no_selection": True,
            "disjoint_from_v6131_discovery": True,
            "only_injury_from_date_used": True,
            "injury_end_date_forbidden": True,
            "days_missed_forbidden": True,
            "games_missed_forbidden": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
        "source": source,
        "sample": {
            "affected_total": len(affected),
            "replication_count": len(test),
            "replication_first": test[0]["date"],
            "replication_last": test[-1]["date"],
            "replication_by_season": {
                season: sum(r["season"] == season for r in test)
                for season in sorted(base.TARGET_SEASONS)
            },
            "discovery_first": discovery[0]["date"],
            "discovery_last": discovery[-1]["date"],
        },
        "test": fixed,
        "uplift_pp": {
            "p58_exclusion": uplift("p_ge_0.58", "p_ge_0.58_exclude_fav_any_14d"),
            "p60_exclusion": uplift("p_ge_0.60", "p_ge_0.60_exclude_fav_any_14d"),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sample": payload["sample"], "test": fixed, "uplift_pp": payload["uplift_pp"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
