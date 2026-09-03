from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt
import source_compat as base

ADAPTER_SCHEMA = "football3-frozen-source-compat-v2"


def _learn_team_map(history: list[rt.HistoryFixture], source_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], str], dict[str, Any]]:
    """Learn source-name -> frozen-V1-name aliases from schedule co-occurrence only.

    No score, xG, odds, or target labels are used. For every completed source fixture date, a source
    home (away) team accumulates support against every V1 home (away) team playing that same date.
    The true alias repeats over a season; incidental co-occurrences do not. Mapping must be unique,
    one-to-one within a competition, and have >=80% schedule support.
    """
    formal_by_date: dict[tuple[str, str], list[rt.HistoryFixture]] = defaultdict(list)
    for r in history:
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25"):
            formal_by_date[(r.competition_id, r.kickoff.date().isoformat())].append(r)

    support: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    appearances: Counter[tuple[str, str]] = Counter()
    source_teams_by_comp: dict[str, set[str]] = defaultdict(set)
    absent_dates: Counter[tuple[str, str]] = Counter()
    for s in source_rows:
        comp = str(s["competition_id"])
        date = str(s["source_date"])
        peers = formal_by_date.get((comp, date), [])
        if not peers:
            absent_dates[(comp, date)] += 1
            continue
        home = str(s["home_team"])
        away = str(s["away_team"])
        source_teams_by_comp[comp].update((home, away))
        appearances[(comp, home)] += 1
        appearances[(comp, away)] += 1
        for f in peers:
            support[(comp, home)][f.home_team_name] += 1
            support[(comp, away)][f.away_team_name] += 1
    if absent_dates:
        sample = sorted((c, d, n) for (c, d), n in absent_dates.items())[:10]
        raise rt.RuntimeGateError(f"source/V1 schedule date coverage gap n={sum(absent_dates.values())} sample={sample}")

    mapping: dict[tuple[str, str], str] = {}
    evidence: list[dict[str, Any]] = []
    for comp in sorted(source_teams_by_comp):
        for source_name in sorted(source_teams_by_comp[comp]):
            key = (comp, source_name)
            counts = support[key]
            if not counts:
                raise rt.RuntimeGateError(f"no schedule support for source team: {key}")
            ranked = counts.most_common()
            best_name, best_n = ranked[0]
            second_n = ranked[1][1] if len(ranked) > 1 else 0
            total = appearances[key]
            ratio = best_n / total if total else 0.0
            if best_n == second_n or ratio < 0.80:
                raise rt.RuntimeGateError(
                    f"ambiguous source team mapping {key}: best={best_name}:{best_n}/{total} second={second_n}"
                )
            mapping[key] = best_name
            evidence.append({
                "competition_id": comp, "source_team": source_name, "v1_team": best_name,
                "support": best_n, "appearances": total, "support_ratio": ratio, "second_support": second_n,
            })

    for comp in sorted(source_teams_by_comp):
        vals = [v for (c, _), v in mapping.items() if c == comp]
        if len(vals) != len(set(vals)):
            dup = [name for name, n in Counter(vals).items() if n > 1]
            raise rt.RuntimeGateError(f"non-bijective cross-source team map {comp}: {dup}")

    evidence.sort(key=lambda r: (r["competition_id"], r["source_team"]))
    evidence_sha = rt._sha_bytes(rt._canon_bytes(evidence))
    return mapping, {
        "mapping_method": "competition+calendar_date+home_away_role_schedule_cooccurrence_only",
        "label_free": True,
        "mapping_n": len(evidence),
        "mapping_sha256": evidence_sha,
        "min_support_ratio": min((r["support_ratio"] for r in evidence), default=1.0),
        "mapping": evidence,
    }


def _load_xg_labels(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path) -> tuple[dict[str, rt.XGLabel], dict[str, Any]]:
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise rt.RuntimeGateError("Understat frozen database SHA mismatch")
    cident = confirmation_dir / "confirmation_identity.jsonl"
    cvault = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    if rt._sha_file(cident) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation identity SHA mismatch")
    if rt._sha_file(cvault) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation vault SHA mismatch")

    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    for r in history:
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25"):
            k = rt._xg_join_key(r.competition_id, r.kickoff, r.home_team_name, r.away_team_name)
            if k in formal_index:
                raise rt.RuntimeGateError(f"formal XG join identity collision: {k}")
            formal_index[k] = r
    if len(formal_index) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"formal Big5 join universe mismatch: {len(formal_index)}")

    source_rows: list[dict[str, Any]] = []
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
    old_sha = rt.BINDINGS["understat_frozen.db"]["sha256"]
    for r in raw:
        dt = datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        source_rows.append({
            "competition_id": rt.BIG5[str(r["league"])], "source_date": dt.date().isoformat(),
            "source_kickoff": dt, "home_team": str(r["team_h"]), "away_team": str(r["team_a"]),
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
        sid = str(r["fixture_id"])
        v = vault.get(sid)
        if v is None or str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        source_rows.append({
            "competition_id": rt.BIG5[str(r["league"])], "source_date": dt.date().isoformat(),
            "source_kickoff": dt, "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "source_fixture_id": f"understat:{sid}",
            "home_goals": int(v["home_goals"]), "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]), "away_xg": float(v["away_xg"]),
            "release_at": rt._parse_dt(str(v["release_at"]), "release_at"), "source_sha256": conf_sha,
        })
    if len(source_rows) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"combined frozen XG row count mismatch: {len(source_rows)}")

    team_map, map_evidence = _learn_team_map(history, source_rows)
    labels: dict[str, rt.XGLabel] = {}
    used_source_keys: set[tuple[str, str, str, str]] = set()
    conflicts: list[dict[str, Any]] = []
    for s in source_rows:
        comp = str(s["competition_id"])
        mh = team_map[(comp, str(s["home_team"]))]
        ma = team_map[(comp, str(s["away_team"]))]
        k = (comp, str(s["source_date"]), rt._normalize_team(mh), rt._normalize_team(ma))
        if k in used_source_keys:
            raise rt.RuntimeGateError(f"duplicate mapped XG fixture identity: {k}")
        used_source_keys.add(k)
        f = formal_index.get(k)
        if f is None:
            raise rt.RuntimeGateError(
                f"mapped XG fixture absent from V1: {comp} {s['source_date']} {s['home_team']}->{mh} {s['away_team']}->{ma}"
            )
        hg, ag = int(s["home_goals"]), int(s["away_goals"])
        if (hg, ag) != (f.home_goals, f.away_goals):
            conflicts.append({
                "fixture_id": f.fixture_id, "competition_id": f.competition_id, "season": f.season,
                "date": f.kickoff.date().isoformat(), "home_team": f.home_team_name, "away_team": f.away_team_name,
                "v1_result": [f.home_goals, f.away_goals], "xg_source_result": [hg, ag],
                "xg_source_fixture_id": s["source_fixture_id"], "xg_source_kickoff": s["source_kickoff"].isoformat(),
            })
        labels[f.fixture_id] = rt.XGLabel(
            rt.hxg.ReleasedLabel(hg, ag, float(s["home_xg"]), float(s["away_xg"]), s["release_at"]),
            str(s["source_fixture_id"]), str(s["source_sha256"]), s["source_kickoff"].isoformat(),
        )

    if len(labels) != rt.EXPECTED_XG_JOIN_N or set(used_source_keys) != set(formal_index):
        missing = len(set(formal_index) - used_source_keys)
        extra = len(used_source_keys - set(formal_index))
        raise rt.RuntimeGateError(f"mapped XG identity join incomplete missing={missing} extra={extra} joined={len(labels)}")
    conflicts.sort(key=lambda r: (r["date"], r["competition_id"], r["fixture_id"]))
    conflict_sha = rt._sha_bytes(rt._canon_bytes(conflicts))
    return labels, {
        "adapter_schema": ADAPTER_SCHEMA,
        "joined_n": len(labels), "old_rows": rt.EXPECTED_XG_OLD_N, "confirmation_rows": 1752,
        "understat_db": {"sha256": rt._sha_file(understat_db), "bytes": understat_db.stat().st_size},
        "confirmation_identity": {"sha256": rt._sha_file(cident), "bytes": cident.stat().st_size},
        "confirmation_vault": {"sha256": rt._sha_file(cvault), "bytes": cvault.stat().st_size},
        "team_identity_map": map_evidence,
        "cross_source_result_conflict_n": len(conflicts),
        "cross_source_result_conflicts_sha256": conflict_sha,
        "cross_source_result_conflicts": conflicts,
        "result_semantics": "V1 official frozen result retained for V1; frozen Understat on-field result retained for XG; no overwrite",
    }


def install() -> dict[str, Any]:
    meta = base.install()
    rt.load_xg_labels = _load_xg_labels
    meta = dict(meta)
    meta["adapter_schema"] = ADAPTER_SCHEMA
    meta["team_identity_mapping"] = "label-free schedule cooccurrence, unique one-to-one, >=80% support"
    return meta
