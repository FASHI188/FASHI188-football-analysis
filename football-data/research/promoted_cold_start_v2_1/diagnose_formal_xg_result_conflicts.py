#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from formal_fast_runtime_v1 import runtime as rt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    repo = Path(a.repo_root)
    understat_db = Path(a.understat_db)
    confirmation_dir = Path(a.confirmation_dir)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Source-integrity diagnostic only. These are the exact immutable bindings
    # enforced by the unchanged production runtime; do not accept alternate data.
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise rt.RuntimeGateError("Understat frozen database SHA mismatch")
    cident = confirmation_dir / "confirmation_identity.jsonl"
    cvault = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    if rt._sha_file(cident) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation identity SHA mismatch")
    if rt._sha_file(cvault) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation vault SHA mismatch")

    history, v1src = rt.load_frozen_v1_history(repo)
    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    for r in history:
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25"):
            k = rt._xg_join_key(r.competition_id, r.kickoff, r.home_team_name, r.away_team_name)
            if k in formal_index:
                raise rt.RuntimeGateError(f"formal xG join identity collision: {k}")
            formal_index[k] = r

    source: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    con = sqlite3.connect(str(understat_db))
    con.row_factory = sqlite3.Row
    try:
        raw = [dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    if len(raw) != rt.EXPECTED_XG_OLD_N:
        raise rt.RuntimeGateError(f"old XG selected row count mismatch: {len(raw)}")
    for r in raw:
        dt = datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        comp = rt.BIG5[str(r["league"])]
        k = rt._xg_join_key(comp, dt, str(r["team_h"]), str(r["team_a"]))
        if k in source:
            raise rt.RuntimeGateError(f"duplicate old XG identity: {k}")
        source[k] = {
            "source_kind": "understat_frozen_db",
            "source_fixture_id": f"understat:{int(r['fid'])}",
            "home_goals": int(r["h_goals"]),
            "away_goals": int(r["a_goals"]),
            "home_xg": float(r["h_xg"]),
            "away_xg": float(r["a_xg"]),
            "release_at": (dt + timedelta(hours=3)).isoformat(),
        }

    identities = [json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities) != 1752 or len(vault_rows) != 1752:
        raise rt.RuntimeGateError("confirmation row count mismatch")
    vault = {str(r["fixture_id"]): r for r in vault_rows}
    if len(vault) != 1752:
        raise rt.RuntimeGateError("confirmation vault duplicate id")
    for r in identities:
        sid = str(r["fixture_id"])
        v = vault.get(sid)
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        if str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation kickoff mismatch")
        comp = rt.BIG5[str(r["league"])]
        k = rt._xg_join_key(comp, dt, str(r["home_team"]), str(r["away_team"]))
        if k in source:
            raise rt.RuntimeGateError(f"duplicate combined XG identity: {k}")
        source[k] = {
            "source_kind": "confirmation_vault",
            "source_fixture_id": sid,
            "home_goals": int(v["home_goals"]),
            "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]),
            "away_xg": float(v["away_xg"]),
            "release_at": str(v["release_at"]),
            "identity_record": {
                "kickoff": str(r["kickoff"]),
                "league": str(r["league"]),
                "home_team": str(r["home_team"]),
                "away_team": str(r["away_team"]),
            },
        }

    conflicts: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for k, f in formal_index.items():
        s = source.get(k)
        if s is None:
            missing.append({
                "join_key": list(k),
                "formal_fixture_id": f.fixture_id,
                "competition_id": f.competition_id,
                "season": f.season,
                "kickoff": f.kickoff.isoformat(),
                "home_team": f.home_team_name,
                "away_team": f.away_team_name,
                "formal_score": [f.home_goals, f.away_goals],
                "formal_source_path": f.source_path,
                "formal_source_sha256": f.source_sha256,
            })
            continue
        if (s["home_goals"], s["away_goals"]) != (f.home_goals, f.away_goals):
            conflicts.append({
                "join_key": list(k),
                "formal_fixture_id": f.fixture_id,
                "competition_id": f.competition_id,
                "season": f.season,
                "kickoff": f.kickoff.isoformat(),
                "home_team": f.home_team_name,
                "away_team": f.away_team_name,
                "formal_score": [f.home_goals, f.away_goals],
                "formal_source_path": f.source_path,
                "formal_source_sha256": f.source_sha256,
                "xg_source_kind": s["source_kind"],
                "xg_source_fixture_id": s["source_fixture_id"],
                "xg_source_score": [s["home_goals"], s["away_goals"]],
                "xg": [s["home_xg"], s["away_xg"]],
                "xg_release_at": s["release_at"],
                "xg_identity_record": s.get("identity_record"),
            })

    extra = [list(k) for k in source if k not in formal_index]
    payload = {
        "schema_version": "football3-formal-xg-result-conflict-diagnostic-v1",
        "diagnostic_only": True,
        "candidate_metrics_computed": False,
        "candidate_parameters_or_gates_modified": False,
        "production_runtime_modified": False,
        "formal_universe_n": len(formal_index),
        "xg_source_n": len(source),
        "conflict_n": len(conflicts),
        "missing_n": len(missing),
        "extra_n": len(extra),
        "conflicts": conflicts,
        "missing": missing,
        "extra_join_keys": extra,
        "bindings": {
            "understat_sha256": rt._sha_file(understat_db),
            "confirmation_identity_sha256": rt._sha_file(cident),
            "confirmation_vault_sha256": rt._sha_file(cvault),
        },
        "frozen_v1_history": v1src,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS_DIAGNOSTIC",
        "conflict_n": len(conflicts),
        "missing_n": len(missing),
        "extra_n": len(extra),
        "conflict_fixture_ids": [x["formal_fixture_id"] for x in conflicts],
    }, sort_keys=True))
    for x in conflicts:
        print(json.dumps(x, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
