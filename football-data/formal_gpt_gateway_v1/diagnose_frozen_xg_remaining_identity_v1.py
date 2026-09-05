#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import runtime as rt
import formal_frozen_xg_identity_adjudication_v1 as bridge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo_root)
    under = Path(args.understat_db)
    conf = Path(args.confirmation_dir)

    history, _ = rt.load_frozen_v1_history(repo)
    formal_rows = [
        r for r in history
        if r.competition_id in set(rt.BIG5.values())
        and r.season in ("2022/23", "2023/24", "2024/25")
    ]
    formal_index = {
        rt._xg_join_key(r.competition_id, r.kickoff, r.home_team_name, r.away_team_name): r
        for r in formal_rows
    }

    sources, _ = bridge._read_sources(under, conf)
    mappings = {}
    audits = []
    for comp in sorted(rt.BIG5.values()):
        src = [r for r in sources if r["competition_id"] == comp]
        formal = [r for r in formal_rows if r.competition_id == comp]
        mapping, audit = bridge._learn_identity(comp, src, formal)
        mappings[comp] = mapping
        audits.append(audit)

    source_index = {}
    for s in sources:
        comp = str(s["competition_id"])
        h = mappings[comp][str(s["home"])]
        a = mappings[comp][str(s["away"])]
        key = (comp, str(s["date"]), rt._normalize_team(h), rt._normalize_team(a))
        source_index[key] = s

    missing = [k for k in formal_index if k not in source_index]
    extra = [k for k in source_index if k not in formal_index]

    out = {
        "schema_version": "football3-frozen-xg-remaining-identity-diagnostic-v1",
        "missing_n": len(missing),
        "extra_n": len(extra),
        "missing": [
            {
                "key": list(k),
                "fixture_id": formal_index[k].fixture_id,
                "season": formal_index[k].season,
                "home": formal_index[k].home_team_name,
                "away": formal_index[k].away_team_name,
                "formal_result": [formal_index[k].home_goals, formal_index[k].away_goals],
            }
            for k in missing
        ],
        "extra": [
            {
                "key": list(k),
                "family": source_index[k]["family"],
                "raw_fixture_id": source_index[k]["raw_fixture_id"],
                "source_home": source_index[k]["home"],
                "source_away": source_index[k]["away"],
                "mapped_home": mappings[k[0]][source_index[k]["home"]],
                "mapped_away": mappings[k[0]][source_index[k]["away"]],
                "source_result": [source_index[k]["home_goals"], source_index[k]["away_goals"]],
            }
            for k in extra
        ],
        "identity_audits": [
            {
                "competition_id": a["competition_id"],
                "mapped_n": a["mapped_n"],
                "source_team_n": a["source_team_n"],
                "unresolved": a["unresolved"],
                "mapping_sha256": a["mapping_sha256"],
            }
            for a in audits
        ],
        "result_or_xg_used_for_identity": False,
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    if len(missing) != 8 or len(extra) != 8:
        raise AssertionError(f"expected current 8/8 remainder, got {len(missing)}/{len(extra)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
