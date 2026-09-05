#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-formal-result-adjudication-v1"
MANIFEST_SCHEMA = "football3-result-adjudications-v1"
MANIFEST_CANONICAL_SHA256 = "52d41083aca9ee13561aa435f7de23d5a7b5f1131620f1698908b1a6b88c7e47"
MANIFEST_PATH = Path(__file__).with_name("result_adjudications_v1.json")
_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _manifest() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        obj = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError("result adjudication manifest unreadable") from exc
    if type(obj) is not dict or obj.get("schema_version") != MANIFEST_SCHEMA:
        raise rt.RuntimeGateError("result adjudication manifest schema mismatch")
    if rt._sha_bytes(rt._canon_bytes(obj)) != MANIFEST_CANONICAL_SHA256:
        raise rt.RuntimeGateError("result adjudication manifest identity drift")
    rows = obj.get("entries")
    if type(rows) is not list or not rows:
        raise rt.RuntimeGateError("result adjudication manifest entries missing")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if type(row) is not dict:
            raise rt.RuntimeGateError("invalid result adjudication entry")
        fid = str(row.get("fixture_id") or "")
        if not fid or fid in index:
            raise rt.RuntimeGateError("duplicate/empty result adjudication fixture")
        if row.get("semantics") != "ADMINISTRATIVE_AWARD_SPLIT_V1_SETTLEMENT_AND_XG_ON_FIELD":
            raise rt.RuntimeGateError("unsupported result adjudication semantics")
        if row.get("sample_deleted") is not False or row.get("score_rewritten") is not False:
            raise rt.RuntimeGateError("result adjudication may not delete/rewrite source rows")
        if row.get("conflict_gate_relaxed_without_authority") is not False:
            raise rt.RuntimeGateError("result conflict gate may not be relaxed without authority")
        index[fid] = row
    return obj, index


def _validate_adjudication(
    entry: dict[str, Any], fixture: rt.HistoryFixture, source_row: dict[str, Any], confirmation_dir: Path,
) -> dict[str, Any]:
    expected_identity = (
        entry.get("competition_id"), entry.get("season"), entry.get("kickoff_date"),
        entry.get("home_team"), entry.get("away_team"),
    )
    actual_identity = (
        fixture.competition_id, fixture.season, fixture.kickoff.date().isoformat(),
        fixture.home_team_name, fixture.away_team_name,
    )
    if actual_identity != expected_identity:
        raise rt.RuntimeGateError(f"adjudication identity mismatch: {fixture.fixture_id}")
    settlement = entry.get("formal_settlement_result") or {}
    on_field = entry.get("on_field_xg_result") or {}
    formal_goals = (fixture.home_goals, fixture.away_goals)
    expected_formal = (int(settlement.get("home_goals", -1)), int(settlement.get("away_goals", -1)))
    xg_goals = (int(source_row["home_goals"]), int(source_row["away_goals"]))
    expected_xg = (int(on_field.get("home_goals", -1)), int(on_field.get("away_goals", -1)))
    if formal_goals != expected_formal or xg_goals != expected_xg or formal_goals == xg_goals:
        raise rt.RuntimeGateError(f"adjudication result semantics mismatch: {fixture.fixture_id}")

    xgs = entry.get("xg_source") or {}
    if source_row["family"] != xgs.get("family") or str(source_row["raw_fixture_id"]) != str(xgs.get("fixture_id")):
        raise rt.RuntimeGateError(f"adjudication xG source identity mismatch: {fixture.fixture_id}")
    if source_row["family"] == "CONFIRMATION_FROZEN_VAULT":
        if source_row["identity_sha256"] != xgs.get("identity_sha256") or source_row["vault_sha256"] != xgs.get("vault_sha256"):
            raise rt.RuntimeGateError(f"adjudication frozen confirmation hash mismatch: {fixture.fixture_id}")
        expected_raw = str(xgs.get("raw_page_sha256") or "")
        raw_hits = [p for p in sorted((confirmation_dir / "raw_pages").glob("*.json")) if rt._sha_file(p) == expected_raw]
        if len(raw_hits) != 1:
            raise rt.RuntimeGateError(f"adjudication raw xG page identity mismatch: {fixture.fixture_id}")
    else:
        raise rt.RuntimeGateError(f"unsupported adjudicated xG source family: {source_row['family']}")

    availability = entry.get("availability") or {}
    formal_at = rt._parse_dt(str(availability.get("formal_result_available_at") or ""), "formal result available at")
    if formal_at <= fixture.kickoff or formal_at <= source_row["release_at"]:
        raise rt.RuntimeGateError(f"adjudication PIT availability invalid: {fixture.fixture_id}")
    if availability.get("policy") != "NEXT_UTC_MIDNIGHT_AFTER_DATE_ONLY_OFFICIAL_DFB_RULING_PUBLICATION":
        raise rt.RuntimeGateError(f"adjudication availability policy drift: {fixture.fixture_id}")
    return {
        "fixture_id": fixture.fixture_id,
        "formal_result": list(formal_goals),
        "on_field_xg_result": list(xg_goals),
        "xg_release_at": source_row["release_at"].isoformat(),
        "formal_result_available_at": formal_at.isoformat(),
        "semantics": entry["semantics"],
    }


def _load_xg_labels(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path):
    manifest, adjudications = _manifest()
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise rt.RuntimeGateError("Understat frozen database SHA mismatch")
    cident = confirmation_dir / "confirmation_identity.jsonl"
    cvault = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    if rt._sha_file(cident) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation identity SHA mismatch")
    if rt._sha_file(cvault) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation vault SHA mismatch")

    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    target_big5 = 0
    for row in history:
        if row.competition_id in set(rt.BIG5.values()) and row.season in ("2022/23", "2023/24", "2024/25"):
            key = rt._xg_join_key(row.competition_id, row.kickoff, row.home_team_name, row.away_team_name)
            if key in formal_index:
                raise rt.RuntimeGateError(f"formal xG join identity collision: {key}")
            formal_index[key] = row
            target_big5 += 1
    if target_big5 != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"formal Big5 join universe mismatch: {target_big5}")

    source: dict[tuple[str, str, str, str], dict[str, Any]] = {}
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
    for row in raw:
        dt = datetime.fromisoformat(str(row["date"])).replace(tzinfo=timezone.utc)
        comp = rt.BIG5[str(row["league"])]
        key = rt._xg_join_key(comp, dt, str(row["team_h"]), str(row["team_a"]))
        if key in source:
            raise rt.RuntimeGateError(f"duplicate old XG identity: {key}")
        source[key] = {
            "family": "UNDERSTAT_FROZEN_DB", "raw_fixture_id": str(int(row["fid"])),
            "source_fixture_id": f"understat:{int(row['fid'])}", "home_goals": int(row["h_goals"]),
            "away_goals": int(row["a_goals"]), "home_xg": float(row["h_xg"]), "away_xg": float(row["a_xg"]),
            "release_at": dt + timedelta(hours=3), "source_sha256": old_sha,
        }

    identities = [json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities) != 1752 or len(vault_rows) != 1752:
        raise rt.RuntimeGateError("confirmation row count mismatch")
    vault = {str(row["fixture_id"]): row for row in vault_rows}
    if len(vault) != 1752:
        raise rt.RuntimeGateError("confirmation vault duplicate id")
    identity_sha = rt._sha_file(cident); vault_sha = rt._sha_file(cvault)
    conf_sha = identity_sha + "+" + vault_sha
    for row in identities:
        sid = str(row["fixture_id"]); v = vault.get(sid)
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(row["kickoff"]), "confirmation kickoff")
        if str(v.get("kickoff")) != str(row["kickoff"]):
            raise rt.RuntimeGateError("confirmation kickoff mismatch")
        comp = rt.BIG5[str(row["league"])]
        key = rt._xg_join_key(comp, dt, str(row["home_team"]), str(row["away_team"]))
        if key in source:
            raise rt.RuntimeGateError(f"duplicate combined XG identity: {key}")
        source[key] = {
            "family": "CONFIRMATION_FROZEN_VAULT", "raw_fixture_id": sid,
            "source_fixture_id": f"understat:{sid}", "home_goals": int(v["home_goals"]),
            "away_goals": int(v["away_goals"]), "home_xg": float(v["home_xg"]), "away_xg": float(v["away_xg"]),
            "release_at": rt._parse_dt(str(v["release_at"]), "release_at"), "source_sha256": conf_sha,
            "identity_sha256": identity_sha, "vault_sha256": vault_sha,
        }

    labels: dict[str, rt.XGLabel] = {}
    missing: list[Any] = []
    consumed: set[str] = set()
    applied: list[dict[str, Any]] = []
    for key, fixture in formal_index.items():
        src = source.get(key)
        if src is None:
            missing.append(key)
            continue
        formal_goals = (fixture.home_goals, fixture.away_goals)
        xg_goals = (src["home_goals"], src["away_goals"])
        if formal_goals != xg_goals:
            entry = adjudications.get(fixture.fixture_id)
            if entry is None:
                raise rt.RuntimeGateError(f"XG/formal result conflict: {fixture.fixture_id}")
            applied.append(_validate_adjudication(entry, fixture, src, confirmation_dir))
            consumed.add(fixture.fixture_id)
        elif fixture.fixture_id in adjudications:
            raise rt.RuntimeGateError(f"stale adjudication no longer matches a result conflict: {fixture.fixture_id}")
        lab = rt.hxg.ReleasedLabel(
            int(src["home_goals"]), int(src["away_goals"]), float(src["home_xg"]), float(src["away_xg"]), src["release_at"]
        )
        labels[fixture.fixture_id] = rt.XGLabel(
            lab, str(src["source_fixture_id"]), str(src["source_sha256"]), src["release_at"].isoformat()
        )
    extra = [key for key in source if key not in formal_index]
    if missing or extra or len(labels) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"XG identity join incomplete missing={len(missing)} extra={len(extra)} joined={len(labels)}")
    if consumed != set(adjudications):
        raise rt.RuntimeGateError(f"result adjudication consumption mismatch used={sorted(consumed)} expected={sorted(adjudications)}")
    return labels, {
        "joined_n": len(labels), "old_rows": rt.EXPECTED_XG_OLD_N, "confirmation_rows": 1752,
        "understat_db": {"sha256": rt._sha_file(understat_db), "bytes": understat_db.stat().st_size},
        "confirmation_identity": {"sha256": identity_sha, "bytes": cident.stat().st_size},
        "confirmation_vault": {"sha256": vault_sha, "bytes": cvault.stat().st_size},
        "result_adjudication": {
            "schema_version": SCHEMA, "manifest_sha256": MANIFEST_CANONICAL_SHA256,
            "entry_n": len(adjudications), "applied": applied,
            "conflict_gate_relaxed_without_authority": False, "sample_deleted": False, "source_score_rewritten": False,
        },
    }


def _event_common(row: rt.HistoryFixture, enters_xg: bool) -> dict[str, Any]:
    return {
        "fixture_id": row.fixture_id, "competition_id": row.competition_id, "season": row.season,
        "home_team_id": row.home_team_id, "away_team_id": row.away_team_id,
        "home_team_name": row.home_team_name, "away_team_name": row.away_team_name,
        "kickoff": row.kickoff.isoformat(), "source": row.source_path,
        "source_content_sha256": row.source_sha256, "enters_v1": True, "enters_xg": bool(enters_xg),
    }


def _history_delta_events(history: list[rt.HistoryFixture], xg_labels: dict[str, rt.XGLabel], lower, upper, target_fixture_id=None):
    _, adjudications = _manifest()
    upper = upper.astimezone(timezone.utc); lower = None if lower is None else lower.astimezone(timezone.utc)
    events: list[dict[str, Any]] = []
    for row in history:
        x = xg_labels.get(row.fixture_id)
        if x is not None and row.kickoff < upper and (lower is None or row.kickoff >= lower):
            if row.fixture_id == target_fixture_id:
                raise rt.RuntimeGateError("target fixture freeze entered pre-target delta")
            e = _event_common(row, True); e.update({"event_type": "FIXTURE_FREEZE", "event_at": row.kickoff.isoformat()})
            events.append(e)

        entry = adjudications.get(row.fixture_id)
        releases: list[dict[str, Any]] = []
        if entry is None:
            release = x.label.release_at if x is not None else row.kickoff + timedelta(hours=3)
            e = _event_common(row, x is not None)
            e.update({
                "event_type": "LABEL_RELEASE", "event_at": release.isoformat(), "result_available_at": release.isoformat(),
                "home_goals": row.home_goals, "away_goals": row.away_goals,
            })
            if x is not None:
                e["home_xg"] = x.label.home_xg; e["away_xg"] = x.label.away_xg
            releases.append(e)
        else:
            if x is None:
                raise rt.RuntimeGateError(f"adjudicated fixture missing XG label: {row.fixture_id}")
            on_field = entry["on_field_xg_result"]
            if (x.label.home_goals, x.label.away_goals) != (int(on_field["home_goals"]), int(on_field["away_goals"])):
                raise rt.RuntimeGateError(f"adjudicated on-field XG result drift: {row.fixture_id}")
            xg_release = x.label.release_at
            xe = _event_common(row, True)
            xe.update({
                "enters_v1": False, "source": x.source_fixture_id, "source_content_sha256": x.source_sha256,
                "event_type": "LABEL_RELEASE", "event_at": xg_release.isoformat(), "result_available_at": xg_release.isoformat(),
                "home_goals": x.label.home_goals, "away_goals": x.label.away_goals,
                "home_xg": x.label.home_xg, "away_xg": x.label.away_xg,
                "result_semantics": "ON_FIELD_XG_RESULT", "adjudication_manifest_sha256": MANIFEST_CANONICAL_SHA256,
            })
            releases.append(xe)
            formal_at = rt._parse_dt(str(entry["availability"]["formal_result_available_at"]), "formal result available at")
            ve = _event_common(row, False)
            ve.update({
                "enters_v1": True, "enters_xg": False,
                "event_type": "LABEL_RELEASE", "event_at": formal_at.isoformat(), "result_available_at": formal_at.isoformat(),
                "home_goals": row.home_goals, "away_goals": row.away_goals,
                "result_semantics": "AUTHORITATIVE_FINAL_SETTLEMENT", "adjudication_manifest_sha256": MANIFEST_CANONICAL_SHA256,
                "availability_policy": entry["availability"]["policy"],
            })
            releases.append(ve)

        for e in releases:
            at = rt._parse_dt(str(e["event_at"]), "event_at")
            if at <= upper and (lower is None or at > lower):
                if row.fixture_id == target_fixture_id:
                    raise rt.RuntimeGateError("target label entered pre-target delta")
                events.append(e)
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[e["event_type"]],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))
    return events


def _validate_delta_records(delta: list[dict[str, Any]], target_fixture_id: str, lower, upper):
    seen: set[tuple[str, str, str]] = set(); out: list[dict[str, Any]] = []
    for row in delta:
        if type(row) is not dict:
            raise rt.RuntimeGateError("invalid delta event")
        common = (
            "event_type", "event_at", "fixture_id", "competition_id", "season", "home_team_id", "away_team_id",
            "home_team_name", "away_team_name", "kickoff", "source", "source_content_sha256", "enters_v1", "enters_xg",
        )
        if any(key not in row for key in common):
            raise rt.RuntimeGateError("delta event missing field")
        et = str(row["event_type"]); fid = str(row["fixture_id"])
        if type(row["enters_v1"]) is not bool or type(row["enters_xg"]) is not bool or not (row["enters_v1"] or row["enters_xg"]):
            raise rt.RuntimeGateError("delta route flags invalid")
        route_key = f"v1={int(row['enters_v1'])},xg={int(row['enters_xg'])}"
        key = (et, fid, route_key)
        if et not in {"FIXTURE_FREEZE", "LABEL_RELEASE"} or not fid or key in seen or fid == target_fixture_id:
            raise rt.RuntimeGateError("invalid/duplicate/target delta event")
        seen.add(key)
        if row["competition_id"] not in rt.FORMAL_SCOPE:
            raise rt.RuntimeGateError("delta competition outside formal scope")
        kickoff = rt._parse_dt(str(row["kickoff"]), "delta kickoff"); at = rt._parse_dt(str(row["event_at"]), "event_at")
        in_window = (lower <= at < upper) if et == "FIXTURE_FREEZE" else (lower < at <= upper)
        if not in_window or kickoff >= upper:
            raise rt.RuntimeGateError("delta PIT/time continuity violation")
        if not str(row["source"]).strip() or len(str(row["source_content_sha256"])) < 32:
            raise rt.RuntimeGateError("delta source identity invalid")
        if et == "FIXTURE_FREEZE":
            if at != kickoff or row["enters_xg"] is not True or row["enters_v1"] is not True:
                raise rt.RuntimeGateError("fixture-freeze event invalid")
            if any(k in row for k in {"home_goals", "away_goals", "home_xg", "away_xg", "result_available_at"}):
                raise rt.RuntimeGateError("fixture-freeze carries forbidden target label fields")
        else:
            if any(k not in row for k in ("result_available_at", "home_goals", "away_goals")):
                raise rt.RuntimeGateError("label-release event invalid")
            if rt._parse_dt(str(row["result_available_at"]), "result_available_at") != at or kickoff >= at:
                raise rt.RuntimeGateError("label-release event invalid")
            for g in ("home_goals", "away_goals"):
                if isinstance(row[g], bool) or not isinstance(row[g], int) or row[g] < 0 or row[g] > 30:
                    raise rt.RuntimeGateError("delta goal invalid")
            if row["enters_xg"]:
                for x in ("home_xg", "away_xg"):
                    if x not in row or isinstance(row[x], bool) or not rt.math.isfinite(float(row[x])) or float(row[x]) < 0 or float(row[x]) > 20:
                        raise rt.RuntimeGateError("delta XG invalid")
            elif "home_xg" in row or "away_xg" in row:
                raise rt.RuntimeGateError("V1-only settlement event may not carry XG")
        out.append(row)
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    return sorted(out, key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[e["event_type"]],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))


def _apply_events(state, events: list[dict[str, Any]], as_of):
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events = sorted(events, key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[str(e["event_type"])],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))
    applied_v1 = applied_xg = frozen_xg = 0
    i = 0
    while i < len(events):
        at = rt._parse_dt(str(events[i]["event_at"]), "event_at")
        same: list[dict[str, Any]] = []
        while i < len(events) and rt._parse_dt(str(events[i]["event_at"]), "event_at") == at:
            same.append(events[i]); i += 1
        releases = [e for e in same if e["event_type"] == "LABEL_RELEASE"]
        freezes = [e for e in same if e["event_type"] == "FIXTURE_FREEZE"]
        if releases:
            by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
            for e in releases:
                by_kickoff[rt._parse_dt(str(e["kickoff"]), "kickoff")].append(e)
            for kickoff, rows in sorted(by_kickoff.items()):
                xrows = [r for r in rows if r["enters_xg"]]
                if xrows:
                    fixtures = [rt.hxg.FixtureRow(
                        str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), kickoff,
                        str(r["home_team_id"]), str(r["away_team_id"]), str(r["home_team_name"]), str(r["away_team_name"]),
                    ) for r in xrows]
                    for fixture in fixtures:
                        if fixture.fixture_id not in state.pending or state.pending[fixture.fixture_id]["fixture"] != fixture:
                            raise rt.RuntimeGateError("released XG label lacks exact cached fixture freeze")
                    labs = {str(r["fixture_id"]): rt.hxg.ReleasedLabel(
                        int(r["home_goals"]), int(r["away_goals"]), float(r["home_xg"]), float(r["away_xg"]),
                        rt._parse_dt(str(r["result_available_at"]), "result_available_at"),
                    ) for r in xrows}
                    state.apply_released_batch(fixtures, labs, as_of=at, update_base=False); applied_xg += len(xrows)
                vrows = [r for r in rows if r["enters_v1"]]
                if vrows:
                    vfixtures = [rt.v1_engine.Fixture(
                        str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), kickoff,
                        str(r["home_team_id"]), str(r["away_team_id"]),
                    ) for r in vrows]
                    state.base.apply_batch(vfixtures, {str(r["fixture_id"]): (int(r["home_goals"]), int(r["away_goals"])) for r in vrows})
                    applied_v1 += len(vrows)
        if freezes:
            by_kickoff = defaultdict(list)
            for e in freezes:
                by_kickoff[rt._parse_dt(str(e["kickoff"]), "kickoff")].append(e)
            for kickoff, rows in sorted(by_kickoff.items()):
                fixtures = [rt.hxg.FixtureRow(
                    str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), kickoff,
                    str(r["home_team_id"]), str(r["away_team_id"]), str(r["home_team_name"]), str(r["away_team_name"]),
                ) for r in rows]
                if any(f.fixture_id in state.pending or f.fixture_id in state.seen for f in fixtures):
                    raise rt.RuntimeGateError("duplicate XG fixture freeze event")
                state.predict_batch(fixtures, include_matrix=False, lightweight=True); frozen_xg += len(fixtures)
    return {
        "applied_v1": applied_v1, "applied_xg": applied_xg, "frozen_xg": frozen_xg,
        "as_of": as_of.isoformat(), "pending_xg_n": len(state.pending),
    }


def adjudication_entries() -> dict[str, dict[str, Any]]:
    return _manifest()[1]


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {
            "schema_version": SCHEMA, "installed": True, "idempotent": True,
            "manifest_sha256": MANIFEST_CANONICAL_SHA256,
        }
    _manifest()
    _ORIGINALS.update({
        "load_xg_labels": rt.load_xg_labels,
        "history_delta_events": rt.history_delta_events,
        "_validate_delta_records": rt._validate_delta_records,
        "_apply_events": rt._apply_events,
    })
    rt.load_xg_labels = _load_xg_labels
    rt.history_delta_events = _history_delta_events
    rt._validate_delta_records = _validate_delta_records
    rt._apply_events = _apply_events
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "manifest_sha256": MANIFEST_CANONICAL_SHA256,
        "entry_n": len(adjudication_entries()),
        "semantics": "SPLIT_AUTHORITATIVE_SETTLEMENT_V1_FROM_ON_FIELD_XG",
        "pit_policy": "CONSERVATIVE_DATE_ONLY_AUTHORITY_BOUNDARY",
        "conflict_gate_relaxed_without_authority": False,
        "sample_deleted": False,
        "source_score_rewritten": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
