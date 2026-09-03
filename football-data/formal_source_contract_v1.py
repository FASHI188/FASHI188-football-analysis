from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-formal-source-contract-adapter-v1"


def _source_rows(understat_db: Path, confirmation_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise rt.RuntimeGateError("Understat frozen database SHA mismatch")
    cident = confirmation_dir / "confirmation_identity.jsonl"
    cvault = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    if rt._sha_file(cident) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation identity SHA mismatch")
    if rt._sha_file(cvault) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation vault SHA mismatch")

    rows: list[dict[str, Any]] = []
    con = sqlite3.connect(str(understat_db)); con.row_factory = sqlite3.Row
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
    old_sha = rt.BINDINGS["understat_frozen.db"]["sha256"]
    for r in raw:
        dt = datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        rows.append({
            "competition_id": rt.BIG5[str(r["league"])],
            "source_date": dt.date().isoformat(), "source_kickoff": dt,
            "home_team": str(r["team_h"]), "away_team": str(r["team_a"]),
            "source_fixture_id": f"understat:{int(r['fid'])}",
            "home_goals": int(r["h_goals"]), "away_goals": int(r["a_goals"]),
            "home_xg": float(r["h_xg"]), "away_xg": float(r["a_xg"]),
            "release_at": dt + timedelta(hours=3), "source_sha256": old_sha,
        })

    identities = [json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities) != 1752 or len(vault_rows) != 1752:
        raise rt.RuntimeGateError("confirmation row count mismatch")
    vault = {str(r["fixture_id"]): r for r in vault_rows}
    if len(vault) != 1752:
        raise rt.RuntimeGateError("confirmation vault duplicate id")
    conf_sha = rt._sha_file(cident) + "+" + rt._sha_file(cvault)
    for r in identities:
        sid = str(r["fixture_id"]); v = vault.get(sid)
        if v is None or str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        rows.append({
            "competition_id": rt.BIG5[str(r["league"])],
            "source_date": dt.date().isoformat(), "source_kickoff": dt,
            "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "source_fixture_id": f"understat:{sid}",
            "home_goals": int(v["home_goals"]), "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]), "away_xg": float(v["away_xg"]),
            "release_at": rt._parse_dt(str(v["release_at"]), "release_at"), "source_sha256": conf_sha,
        })
    if len(rows) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"combined frozen XG row count mismatch: {len(rows)}")
    return rows, {
        "understat_db": {"sha256": rt._sha_file(understat_db), "bytes": understat_db.stat().st_size},
        "confirmation_identity": {"sha256": rt._sha_file(cident), "bytes": cident.stat().st_size},
        "confirmation_vault": {"sha256": rt._sha_file(cvault), "bytes": cvault.stat().st_size},
    }


def _learn_team_map(history: list[rt.HistoryFixture], source_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], str], dict[str, Any]]:
    # Identity only: same competition, same calendar date, same home/away role. No goals/xG/odds are read here.
    formal_by_date: dict[tuple[str, str], list[rt.HistoryFixture]] = defaultdict(list)
    for r in history:
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25"):
            formal_by_date[(r.competition_id, r.kickoff.date().isoformat())].append(r)

    support: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    appearances: Counter[tuple[str, str]] = Counter()
    source_teams_by_comp: dict[str, set[str]] = defaultdict(set)
    for s in source_rows:
        comp = str(s["competition_id"]); date = str(s["source_date"])
        home = str(s["home_team"]); away = str(s["away_team"])
        source_teams_by_comp[comp].update((home, away))
        peers = formal_by_date.get((comp, date), [])
        if not peers:
            continue
        appearances[(comp, home)] += 1; appearances[(comp, away)] += 1
        for f in peers:
            support[(comp, home)][f.home_team_name] += 1
            support[(comp, away)][f.away_team_name] += 1

    mapping: dict[tuple[str, str], str] = {}; evidence: list[dict[str, Any]] = []
    for comp in sorted(source_teams_by_comp):
        for source_name in sorted(source_teams_by_comp[comp]):
            key = (comp, source_name); counts = support[key]
            if not counts:
                raise rt.RuntimeGateError(f"FORMAL_SOURCE_IDENTITY_GAP no schedule support for source team: {key}")
            ranked = counts.most_common(); best_name, best_n = ranked[0]
            second_n = ranked[1][1] if len(ranked) > 1 else 0
            total = appearances[key]; ratio = best_n / total if total else 0.0
            if best_n == second_n or ratio < 0.80:
                raise rt.RuntimeGateError(f"FORMAL_SOURCE_IDENTITY_GAP ambiguous source team mapping {key}: best={best_name}:{best_n}/{total} second={second_n}")
            mapping[key] = best_name
            evidence.append({"competition_id": comp, "source_team": source_name, "v1_team": best_name,
                             "support": best_n, "appearances": total, "support_ratio": ratio, "second_support": second_n})
    for comp in sorted(source_teams_by_comp):
        vals = [v for (c, _), v in mapping.items() if c == comp]
        if len(vals) != len(set(vals)):
            dup = [name for name, n in Counter(vals).items() if n > 1]
            raise rt.RuntimeGateError(f"FORMAL_SOURCE_IDENTITY_GAP non-bijective source team map {comp}: {dup}")
    evidence.sort(key=lambda r: (r["competition_id"], r["source_team"]))
    return mapping, {
        "method": "competition+calendar_date+home_away_role_schedule_cooccurrence_only",
        "label_free": True, "mapping_n": len(evidence),
        "mapping_sha256": rt._sha_bytes(rt._canon_bytes(evidence)),
        "min_support_ratio": min((r["support_ratio"] for r in evidence), default=1.0),
    }


def strict_load_xg_labels(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path):
    formal = [r for r in history if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25")]
    if len(formal) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"formal Big5 join universe mismatch: {len(formal)}")
    exact: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    by_teams: dict[tuple[str, str, str], list[rt.HistoryFixture]] = defaultdict(list)
    for f in formal:
        h = rt._normalize_team(f.home_team_name); a = rt._normalize_team(f.away_team_name)
        k = (f.competition_id, f.kickoff.date().isoformat(), h, a)
        if k in exact:
            raise rt.RuntimeGateError(f"formal XG join identity collision: {k}")
        exact[k] = f; by_teams[(f.competition_id, h, a)].append(f)

    source_rows, source_meta = _source_rows(understat_db, confirmation_dir)
    team_map, map_meta = _learn_team_map(history, source_rows)
    labels: dict[str, rt.XGLabel] = {}; used: set[str] = set()
    time_gaps: list[dict[str, Any]] = []; result_conflicts: list[dict[str, Any]] = []
    for s in source_rows:
        comp = str(s["competition_id"])
        mh = team_map[(comp, str(s["home_team"]))]; ma = team_map[(comp, str(s["away_team"]))]
        h = rt._normalize_team(mh); a = rt._normalize_team(ma); source_date = str(s["source_date"])
        f = exact.get((comp, source_date, h, a))
        if f is None:
            # Audit possible schedule identity only; do not use it to alter model time semantics.
            candidates = sorted(by_teams.get((comp, h, a), []), key=lambda x: (x.kickoff, x.fixture_id))
            time_gaps.append({
                "competition_id": comp, "source_date": source_date, "source_kickoff": s["source_kickoff"].isoformat(),
                "home_team": mh, "away_team": ma,
                "candidate_v1_dates": [x.kickoff.date().isoformat() for x in candidates],
                "candidate_fixture_ids": [x.fixture_id for x in candidates],
                "reason": "calendar-date identity differs; integration refuses to shift kickoff/resume/release semantics",
            })
            continue
        if f.fixture_id in used:
            raise rt.RuntimeGateError(f"duplicate mapped XG fixture: {f.fixture_id}")
        used.add(f.fixture_id)
        hg, ag = int(s["home_goals"]), int(s["away_goals"])
        if (hg, ag) != (f.home_goals, f.away_goals):
            result_conflicts.append({
                "fixture_id": f.fixture_id, "competition_id": f.competition_id, "season": f.season,
                "v1_date": f.kickoff.date().isoformat(), "home_team": f.home_team_name, "away_team": f.away_team_name,
                "v1_result": [f.home_goals, f.away_goals], "xg_source_result": [hg, ag],
                "xg_source_fixture_id": s["source_fixture_id"], "xg_source_kickoff": s["source_kickoff"].isoformat(),
                "reason": "original runtime contract requires exact V1/XG result equality; no substitution permitted",
            })
            continue
        labels[f.fixture_id] = rt.XGLabel(
            rt.hxg.ReleasedLabel(hg, ag, float(s["home_xg"]), float(s["away_xg"]), s["release_at"]),
            str(s["source_fixture_id"]), str(s["source_sha256"]), s["source_kickoff"].isoformat(),
        )

    time_gaps.sort(key=lambda r: (r["source_date"], r["competition_id"], r["home_team"], r["away_team"]))
    result_conflicts.sort(key=lambda r: (r["v1_date"], r["competition_id"], r["fixture_id"]))
    if time_gaps or result_conflicts or len(labels) != rt.EXPECTED_XG_JOIN_N:
        gap = {
            "schema_version": SCHEMA,
            "status": "FORMAL_SOURCE_CONTRACT_GAP",
            "expected_join_n": rt.EXPECTED_XG_JOIN_N,
            "safe_join_n": len(labels),
            "time_semantic_gap_n": len(time_gaps),
            "result_conflict_n": len(result_conflicts),
            "time_semantic_gaps": time_gaps,
            "result_conflicts": result_conflicts,
            "team_identity_map": map_meta,
            "policy": {
                "team_aliases": "identity-only mapping allowed; no labels used",
                "time": "no date/kickoff shifting; source release_at retained exactly",
                "results": "no official/on-field score substitution; original equality guard retained",
                "model_input_semantics_changed": False,
            },
        }
        raise rt.RuntimeGateError("FORMAL_SOURCE_CONTRACT_GAP " + json.dumps(gap, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    return labels, {
        "adapter_schema": SCHEMA, "joined_n": len(labels), **source_meta,
        "team_identity_map": map_meta,
        "time_semantic_gap_n": 0, "result_conflict_n": 0,
        "input_semantics": "V1 history/kickoff/results untouched; XG values and release_at byte-source-preserved; only identity-only team alias resolution",
    }


def install() -> dict[str, Any]:
    rt.load_xg_labels = strict_load_xg_labels
    return {
        "schema_version": SCHEMA,
        "mode": "STRICT_FAIL_CLOSED",
        "scientific_modules_changed": False,
        "history_time_mutation": False,
        "result_substitution": False,
        "xg_release_mutation": False,
        "team_alias_mapping": "identity-only same-date schedule cooccurrence",
    }
