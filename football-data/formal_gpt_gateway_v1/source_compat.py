from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt


ADAPTER_SCHEMA = "football3-frozen-source-compat-v1"


def _load_xg_labels_component_safe(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path) -> tuple[dict[str, rt.XGLabel], dict[str, Any]]:
    """Join the two already-frozen component sources without forcing their result labels to be identical.

    Frozen V1 may contain governing-body settlement results while the frozen Understat XG source
    contains the on-field result used by the accepted XG component. The formal fusion architecture
    keeps component updates separate (XG uses update_base=False), so a cross-source result conflict
    is evidence to disclose, not a license to overwrite either component's frozen source.
    """
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
    for r in history:
        if r.competition_id in set(rt.BIG5.values()) and r.season in ("2022/23", "2023/24", "2024/25"):
            k = rt._xg_join_key(r.competition_id, r.kickoff, r.home_team_name, r.away_team_name)
            if k in formal_index:
                raise rt.RuntimeGateError(f"formal XG join identity collision: {k}")
            formal_index[k] = r
            target_big5 += 1
    if target_big5 != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"formal Big5 join universe mismatch: {target_big5}")

    # key -> source fixture id, goals, xg, release time, source sha, source kickoff
    source: dict[tuple[str, str, str, str], tuple[str, int, int, float, float, datetime, str, str]] = {}
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
        comp = rt.BIG5[str(r["league"])]
        k = rt._xg_join_key(comp, dt, str(r["team_h"]), str(r["team_a"]))
        if k in source:
            raise rt.RuntimeGateError(f"duplicate old XG identity: {k}")
        source[k] = (
            f"understat:{int(r['fid'])}", int(r["h_goals"]), int(r["a_goals"]),
            float(r["h_xg"]), float(r["a_xg"]), dt + timedelta(hours=3), old_sha, dt.isoformat(),
        )

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
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        if str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation kickoff mismatch")
        comp = rt.BIG5[str(r["league"])]
        k = rt._xg_join_key(comp, dt, str(r["home_team"]), str(r["away_team"]))
        if k in source:
            raise rt.RuntimeGateError(f"duplicate combined XG identity: {k}")
        source[k] = (
            f"understat:{sid}", int(v["home_goals"]), int(v["away_goals"]),
            float(v["home_xg"]), float(v["away_xg"]),
            rt._parse_dt(str(v["release_at"]), "release_at"), conf_sha, dt.isoformat(),
        )

    labels: dict[str, rt.XGLabel] = {}
    missing: list[Any] = []
    conflicts: list[dict[str, Any]] = []
    for k, f in formal_index.items():
        s = source.get(k)
        if s is None:
            missing.append(k)
            continue
        sid, hg, ag, hx, ax, release, src_sha, source_kickoff = s
        if (hg, ag) != (f.home_goals, f.away_goals):
            conflicts.append({
                "fixture_id": f.fixture_id,
                "competition_id": f.competition_id,
                "season": f.season,
                "date": f.kickoff.date().isoformat(),
                "home_team": f.home_team_name,
                "away_team": f.away_team_name,
                "v1_result": [f.home_goals, f.away_goals],
                "xg_source_result": [hg, ag],
                "xg_source_fixture_id": sid,
                "xg_source_kickoff": source_kickoff,
            })
        labels[f.fixture_id] = rt.XGLabel(rt.hxg.ReleasedLabel(hg, ag, hx, ax, release), sid, src_sha, source_kickoff)

    extra = [k for k in source if k not in formal_index]
    if missing or extra or len(labels) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"XG identity join incomplete missing={len(missing)} extra={len(extra)} joined={len(labels)}")
    conflicts.sort(key=lambda r: (r["date"], r["competition_id"], r["fixture_id"]))
    conflict_sha = rt._sha_bytes(rt._canon_bytes(conflicts))
    return labels, {
        "adapter_schema": ADAPTER_SCHEMA,
        "joined_n": len(labels),
        "old_rows": rt.EXPECTED_XG_OLD_N,
        "confirmation_rows": 1752,
        "understat_db": {"sha256": rt._sha_file(understat_db), "bytes": understat_db.stat().st_size},
        "confirmation_identity": {"sha256": rt._sha_file(cident), "bytes": cident.stat().st_size},
        "confirmation_vault": {"sha256": rt._sha_file(cvault), "bytes": cvault.stat().st_size},
        "cross_source_result_conflict_n": len(conflicts),
        "cross_source_result_conflicts_sha256": conflict_sha,
        "cross_source_result_conflicts": conflicts,
        "result_semantics": "V1 official frozen result retained for V1; frozen Understat on-field result retained for XG; no overwrite",
    }


def _history_delta_events_component_safe(history: list[rt.HistoryFixture], xg_labels: dict[str, rt.XGLabel], lower: datetime | None,
                                         upper: datetime, target_fixture_id: str | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for r in history:
        x = xg_labels.get(r.fixture_id)
        if x is not None and r.kickoff < upper and (lower is None or r.kickoff >= lower):
            if r.fixture_id == target_fixture_id:
                raise rt.RuntimeGateError("target fixture freeze entered pre-target delta")
            e = rt._event_common(r, True)
            e.update({"event_type": "FIXTURE_FREEZE", "event_at": r.kickoff.isoformat()})
            events.append(e)
        release = rt._default_result_release(r, xg_labels)
        if release <= upper and (lower is None or release > lower):
            if r.fixture_id == target_fixture_id:
                raise rt.RuntimeGateError("target label entered pre-target delta")
            e = rt._event_common(r, x is not None)
            e.update({
                "event_type": "LABEL_RELEASE", "event_at": release.isoformat(), "result_available_at": release.isoformat(),
                "home_goals": r.home_goals, "away_goals": r.away_goals,
            })
            if x is not None:
                e["home_xg"] = x.label.home_xg
                e["away_xg"] = x.label.away_xg
                e["xg_home_goals"] = x.label.home_goals
                e["xg_away_goals"] = x.label.away_goals
                e["xg_source_fixture_id"] = x.source_fixture_id
                e["xg_source_sha256"] = x.source_sha256
            events.append(e)
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[e["event_type"]],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
    ))
    return events


def _apply_events_component_safe(state: rt.hxg.ChallengerState, events: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events = sorted(events, key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[str(e["event_type"])],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
    ))
    applied_v1 = applied_xg = frozen_xg = 0
    component_result_conflicts_applied = 0
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
            for ko, rows in sorted(by_kickoff.items()):
                xrows = [r for r in rows if r["enters_xg"]]
                if xrows:
                    xf = [rt.hxg.FixtureRow(
                        str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), ko,
                        str(r["home_team_id"]), str(r["away_team_id"]), str(r["home_team_name"]), str(r["away_team_name"]),
                    ) for r in xrows]
                    for f in xf:
                        if f.fixture_id not in state.pending or state.pending[f.fixture_id]["fixture"] != f:
                            raise rt.RuntimeGateError("released XG label lacks exact cached fixture freeze")
                    labs = {}
                    for r in xrows:
                        xhg = int(r.get("xg_home_goals", r["home_goals"]))
                        xag = int(r.get("xg_away_goals", r["away_goals"]))
                        if (xhg, xag) != (int(r["home_goals"]), int(r["away_goals"])):
                            component_result_conflicts_applied += 1
                        labs[str(r["fixture_id"])] = rt.hxg.ReleasedLabel(
                            xhg, xag, float(r["home_xg"]), float(r["away_xg"]),
                            rt._parse_dt(str(r["result_available_at"]), "result_available_at"),
                        )
                    state.apply_released_batch(xf, labs, as_of=at, update_base=False)
                    applied_xg += len(xrows)
                vf = [rt.v1_engine.Fixture(
                    str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), ko,
                    str(r["home_team_id"]), str(r["away_team_id"]),
                ) for r in rows]
                state.base.apply_batch(vf, {str(r["fixture_id"]): (int(r["home_goals"]), int(r["away_goals"])) for r in rows})
                applied_v1 += len(rows)
        if freezes:
            by_kickoff = defaultdict(list)
            for e in freezes:
                by_kickoff[rt._parse_dt(str(e["kickoff"]), "kickoff")].append(e)
            for ko, rows in sorted(by_kickoff.items()):
                xf = [rt.hxg.FixtureRow(
                    str(r["fixture_id"]), str(r["competition_id"]), str(r["season"]), ko,
                    str(r["home_team_id"]), str(r["away_team_id"]), str(r["home_team_name"]), str(r["away_team_name"]),
                ) for r in rows]
                if any(f.fixture_id in state.pending or f.fixture_id in state.seen for f in xf):
                    raise rt.RuntimeGateError("duplicate XG fixture freeze event")
                state.predict_batch(xf, include_matrix=False, lightweight=True)
                frozen_xg += len(xf)
    return {
        "applied_v1": applied_v1, "applied_xg": applied_xg, "frozen_xg": frozen_xg,
        "as_of": as_of.isoformat(), "pending_xg_n": len(state.pending),
        "component_result_conflicts_applied": component_result_conflicts_applied,
        "adapter_schema": ADAPTER_SCHEMA,
    }


def _replay_history_state_component_safe(history: list[rt.HistoryFixture], xg_labels: dict[str, rt.XGLabel], cutoff: datetime):
    cutoff = cutoff.astimezone(timezone.utc)
    events = _history_delta_events_component_safe(history, xg_labels, None, cutoff, None)
    state = rt.formal_v2.new_candidate_state()
    stats = _apply_events_component_safe(state, events, cutoff)
    stats.update({
        "event_n": len(events), "historical_cutoff": cutoff.isoformat(),
        "last_v1_update": rt._iso(state.base.last_update_time), "last_xg_apply": rt._iso(state.last_apply_time),
        "v1_only_release_adapter": "kickoff_plus_3h_for_frozen_engineering_replay",
        "component_result_semantics": "separate frozen V1 official result and frozen XG source result",
    })
    return state, stats


def install() -> dict[str, str]:
    """Install only source/replay compatibility adapters into the frozen runtime process.

    The immutable scientific modules and formal prediction function are not replaced.
    The runtime's routing, input validation, bundle sealing/validation and receipt code remain original.
    """
    rt.load_xg_labels = _load_xg_labels_component_safe
    rt.history_delta_events = _history_delta_events_component_safe
    rt._apply_events = _apply_events_component_safe
    rt.replay_history_state = _replay_history_state_component_safe
    return {
        "adapter_schema": ADAPTER_SCHEMA,
        "formal_runner": "new_engine_v1.formal_fusion_v2.predict_formal_batch",
        "formal_runner_sha256": rt.BINDINGS["formal_fusion_v2.py"]["sha256"],
        "runtime_wrapper_sha256": rt._sha_file(Path(rt.__file__).resolve()),
    }
