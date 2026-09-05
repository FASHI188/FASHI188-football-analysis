#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt
import formal_result_adjudication_v1 as contract

SCHEMA = "football3-formal-frozen-xg-identity-adjudication-v1"
_INSTALLED = False


def _assign(mapping: dict[str, str], reverse: dict[str, str], source: str, target: str,
            evidence: list[dict[str, Any]], reason: str) -> bool:
    if source in mapping:
        if mapping[source] != target:
            raise rt.RuntimeGateError(f"frozen XG identity ambiguous: {source}")
        return False
    other = reverse.get(target)
    if other is not None and other != source:
        raise rt.RuntimeGateError(f"frozen XG identity non-bijective: {target}")
    mapping[source] = target
    reverse[target] = source
    evidence.append({"source_team": source, "formal_team": target, "reason": reason})
    return True


def _learn_identity(comp: str, source_rows: list[dict[str, Any]], formal_rows: list[rt.HistoryFixture]) -> tuple[dict[str, str], dict[str, Any]]:
    formal = [r for r in formal_rows if r.competition_id == comp]
    by_date: dict[str, list[rt.HistoryFixture]] = defaultdict(list)
    formal_names: set[str] = set()
    for r in formal:
        by_date[r.kickoff.date().isoformat()].append(r)
        formal_names.update((r.home_team_name, r.away_team_name))

    src_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_names: set[str] = set()
    for r in source_rows:
        src_by_date[str(r["date"])].append(r)
        source_names.update((str(r["home"]), str(r["away"])))

    norm_targets: dict[str, set[str]] = defaultdict(set)
    for name in formal_names:
        norm_targets[rt._normalize_team(name)].add(name)

    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []

    # Exact normalized identity is the only name-based seed. No result/xG is read.
    for source in sorted(source_names):
        hits = norm_targets.get(rt._normalize_team(source), set())
        if len(hits) == 1:
            _assign(mapping, reverse, source, next(iter(hits)), evidence, "exact_normalized_team_identity")

    # Complete aliases with same-calendar-date, role-preserving schedule identity only.
    changed = True
    while changed:
        changed = False
        for date in sorted(src_by_date):
            peers = by_date.get(date, [])
            if not peers:
                continue
            srcs = src_by_date[date]
            for s in srcs:
                mh = mapping.get(str(s["home"])); ma = mapping.get(str(s["away"]))
                candidates = [
                    r for r in peers
                    if (mh is None or r.home_team_name == mh)
                    and (ma is None or r.away_team_name == ma)
                ]
                if len(candidates) == 1 and (mh is not None or ma is not None or (len(srcs) == 1 and len(peers) == 1)):
                    r = candidates[0]
                    changed |= _assign(mapping, reverse, str(s["home"]), r.home_team_name, evidence, f"schedule_anchor:{date}:home")
                    changed |= _assign(mapping, reverse, str(s["away"]), r.away_team_name, evidence, f"schedule_anchor:{date}:away")

        for source in sorted(source_names - set(mapping)):
            candidate_sets: list[set[str]] = []
            for date, srcs in src_by_date.items():
                peers = by_date.get(date, [])
                for s in srcs:
                    if str(s["home"]) == source:
                        candidate_sets.append({r.home_team_name for r in peers})
                    if str(s["away"]) == source:
                        candidate_sets.append({r.away_team_name for r in peers})
            if candidate_sets:
                inter = set.intersection(*candidate_sets)
                inter = {x for x in inter if x not in reverse or reverse[x] == source}
                if len(inter) == 1:
                    changed |= _assign(mapping, reverse, source, next(iter(inter)), evidence, "multi_date_role_candidate_intersection")

    unresolved = sorted(source_names - set(mapping))
    audit = {
        "competition_id": comp,
        "method": "exact-normalized seed + same-date role-preserving schedule anchors/intersections",
        "label_free": True,
        "result_or_xg_used_for_identity": False,
        "mapped_n": len(mapping),
        "source_team_n": len(source_names),
        "unresolved": unresolved,
        "evidence": evidence,
        "mapping_sha256": rt._sha_bytes(rt._canon_bytes(sorted(mapping.items()))),
    }
    return mapping, audit


def _read_sources(understat_db: Path, confirmation_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        old = [dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    if len(old) != rt.EXPECTED_XG_OLD_N:
        raise rt.RuntimeGateError(f"old XG selected row count mismatch: {len(old)}")
    old_sha = rt._sha_file(understat_db)
    for r in old:
        actual = datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        rows.append({
            "family": "UNDERSTAT_FROZEN_DB", "raw_fixture_id": str(int(r["fid"])),
            "source_fixture_id": f"understat:{int(r['fid'])}", "competition_id": rt.BIG5[str(r["league"])],
            "date": actual.date().isoformat(), "home": str(r["team_h"]), "away": str(r["team_a"]),
            "home_goals": int(r["h_goals"]), "away_goals": int(r["a_goals"]),
            "home_xg": float(r["h_xg"]), "away_xg": float(r["a_xg"]),
            "release_at": actual + timedelta(hours=3), "source_sha256": old_sha,
        })

    identities = [json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities) != 1752 or len(vault_rows) != 1752:
        raise rt.RuntimeGateError("confirmation row count mismatch")
    vault = {str(r["fixture_id"]): r for r in vault_rows}
    if len(vault) != 1752:
        raise rt.RuntimeGateError("confirmation vault duplicate id")
    identity_sha = rt._sha_file(cident); vault_sha = rt._sha_file(cvault); combined = identity_sha + "+" + vault_sha
    for r in identities:
        sid = str(r["fixture_id"]); v = vault.get(sid)
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        actual = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        if str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation kickoff mismatch")
        rows.append({
            "family": "CONFIRMATION_FROZEN_VAULT", "raw_fixture_id": sid,
            "source_fixture_id": f"understat:{sid}", "competition_id": rt.BIG5[str(r["league"])],
            "date": actual.date().isoformat(), "home": str(r["home_team"]), "away": str(r["away_team"]),
            "home_goals": int(v["home_goals"]), "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]), "away_xg": float(v["away_xg"]),
            "release_at": rt._parse_dt(str(v["release_at"]), "release_at"), "source_sha256": combined,
            "identity_sha256": identity_sha, "vault_sha256": vault_sha,
        })
    if len(rows) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"frozen XG source cardinality mismatch: {len(rows)}")
    return rows, {
        "understat_db": {"sha256": old_sha, "bytes": understat_db.stat().st_size},
        "confirmation_identity": {"sha256": identity_sha, "bytes": cident.stat().st_size},
        "confirmation_vault": {"sha256": vault_sha, "bytes": cvault.stat().st_size},
    }


def _validate_adjudicated_source(entry: dict[str, Any], fixture: rt.HistoryFixture, source: dict[str, Any], confirmation_dir: Path) -> dict[str, Any]:
    formal = entry["formal_settlement_result"]; on_field = entry["on_field_xg_result"]
    if (fixture.home_goals, fixture.away_goals) != (int(formal["home_goals"]), int(formal["away_goals"])):
        raise rt.RuntimeGateError(f"authoritative settlement drift: {fixture.fixture_id}")
    if (source["home_goals"], source["away_goals"]) != (int(on_field["home_goals"]), int(on_field["away_goals"])):
        raise rt.RuntimeGateError(f"on-field xG result drift: {fixture.fixture_id}")
    xgs = entry["xg_source"]
    if source["family"] != xgs["family"] or source["raw_fixture_id"] != str(xgs["fixture_id"]):
        raise rt.RuntimeGateError(f"adjudicated xG source identity drift: {fixture.fixture_id}")
    if source.get("identity_sha256") != xgs["identity_sha256"] or source.get("vault_sha256") != xgs["vault_sha256"]:
        raise rt.RuntimeGateError(f"adjudicated frozen source SHA drift: {fixture.fixture_id}")
    raw_sha = str(xgs["raw_page_sha256"])
    raw_hits = [p for p in sorted((confirmation_dir / "raw_pages").glob("*.json")) if rt._sha_file(p) == raw_sha]
    if len(raw_hits) != 1:
        raise rt.RuntimeGateError(f"adjudicated raw page SHA drift: {fixture.fixture_id}")
    formal_at = rt._parse_dt(str(entry["availability"]["formal_result_available_at"]), "formal result available at")
    if formal_at <= fixture.kickoff or formal_at <= source["release_at"]:
        raise rt.RuntimeGateError(f"adjudicated PIT boundary invalid: {fixture.fixture_id}")
    return {
        "fixture_id": fixture.fixture_id,
        "formal_result": [fixture.home_goals, fixture.away_goals],
        "on_field_xg_result": [source["home_goals"], source["away_goals"]],
        "xg_source_fixture_id": source["source_fixture_id"],
        "xg_release_at": source["release_at"].isoformat(),
        "formal_result_available_at": formal_at.isoformat(),
        "raw_page_sha256": raw_sha,
        "semantics": entry["semantics"],
    }


def _load_xg_labels(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path):
    _, adjudications = contract._manifest()
    formal_rows = [
        r for r in history
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25")
    ]
    if len(formal_rows) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"formal Big5 join universe mismatch: {len(formal_rows)}")
    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    for r in formal_rows:
        key = rt._xg_join_key(r.competition_id, r.kickoff, r.home_team_name, r.away_team_name)
        if key in formal_index:
            raise rt.RuntimeGateError(f"formal xG join identity collision: {key}")
        formal_index[key] = r

    sources, source_receipt = _read_sources(understat_db, confirmation_dir)
    identity_audits: list[dict[str, Any]] = []
    mappings: dict[str, dict[str, str]] = {}
    for comp in sorted(rt.BIG5.values()):
        src = [r for r in sources if r["competition_id"] == comp]
        formal = [r for r in formal_rows if r.competition_id == comp]
        mapping, audit = _learn_identity(comp, src, formal)
        if audit["unresolved"]:
            raise rt.RuntimeGateError(f"frozen XG identity unresolved {comp}: {audit['unresolved']}")
        mappings[comp] = mapping; identity_audits.append(audit)

    source_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for s in sources:
        comp = str(s["competition_id"]); mapping = mappings[comp]
        h = mapping.get(str(s["home"])); a = mapping.get(str(s["away"]))
        if h is None or a is None:
            raise rt.RuntimeGateError(f"frozen XG source unmapped identity: {comp} {s['home']} {s['away']}")
        key = (comp, str(s["date"]), rt._normalize_team(h), rt._normalize_team(a))
        if key in source_index:
            raise rt.RuntimeGateError(f"duplicate mapped frozen XG identity: {key}")
        source_index[key] = s

    missing = [key for key in formal_index if key not in source_index]
    extra = [key for key in source_index if key not in formal_index]
    if missing or extra or len(source_index) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"mapped XG identity join incomplete missing={len(missing)} extra={len(extra)} joined={rt.EXPECTED_XG_JOIN_N-len(missing)}")

    labels: dict[str, rt.XGLabel] = {}
    applied: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for key, fixture in formal_index.items():
        s = source_index[key]
        if (s["home_goals"], s["away_goals"]) != (fixture.home_goals, fixture.away_goals):
            entry = adjudications.get(fixture.fixture_id)
            if entry is None:
                raise rt.RuntimeGateError(f"XG/formal result conflict: {fixture.fixture_id}")
            applied.append(_validate_adjudicated_source(entry, fixture, s, confirmation_dir)); consumed.add(fixture.fixture_id)
        elif fixture.fixture_id in adjudications:
            raise rt.RuntimeGateError(f"stale adjudication no longer matches conflict: {fixture.fixture_id}")
        label = rt.hxg.ReleasedLabel(
            int(s["home_goals"]), int(s["away_goals"]), float(s["home_xg"]), float(s["away_xg"]), s["release_at"]
        )
        labels[fixture.fixture_id] = rt.XGLabel(label, str(s["source_fixture_id"]), str(s["source_sha256"]), s["release_at"].isoformat())
    if consumed != set(adjudications):
        raise rt.RuntimeGateError(f"result adjudication consumption mismatch used={sorted(consumed)} expected={sorted(adjudications)}")
    if len(labels) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"mapped XG label count mismatch: {len(labels)}")

    source_receipt.update({
        "joined_n": len(labels),
        "old_rows": rt.EXPECTED_XG_OLD_N,
        "confirmation_rows": 1752,
        "identity_bridge": {
            "schema_version": SCHEMA,
            "method": "label-free schedule identity only",
            "result_or_xg_used_for_identity": False,
            "audits": identity_audits,
            "all_source_teams_mapped": True,
            "missing_n": 0,
            "extra_n": 0,
        },
        "result_adjudication": {
            "manifest_sha256": contract.MANIFEST_CANONICAL_SHA256,
            "entry_n": len(adjudications),
            "applied": applied,
            "sample_deleted": False,
            "source_score_rewritten": False,
            "conflict_gate_relaxed_without_authority": False,
        },
    })
    return labels, source_receipt


def adjudication_entries() -> dict[str, dict[str, Any]]:
    return contract.adjudication_entries()


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent": True, "manifest_sha256": contract.MANIFEST_CANONICAL_SHA256}
    contract._manifest()
    rt.load_xg_labels = _load_xg_labels
    rt.history_delta_events = contract._history_delta_events
    rt._validate_delta_records = contract._validate_delta_records
    rt._apply_events = contract._apply_events
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "manifest_sha256": contract.MANIFEST_CANONICAL_SHA256,
        "entry_n": len(adjudication_entries()),
        "identity_policy": "exact-normalized + same-date role-preserving schedule identity; label-free and result-free",
        "result_semantics": "SPLIT_AUTHORITATIVE_SETTLEMENT_V1_FROM_ON_FIELD_XG",
        "sample_deleted": False,
        "source_score_rewritten": False,
        "conflict_gate_relaxed_without_authority": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
