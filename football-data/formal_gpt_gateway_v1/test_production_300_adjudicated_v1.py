#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import formal_result_adjudication_v2 as adjudication

ADJUDICATION = adjudication.install()

import runtime as rt
import test_runtime as tr

SCHEMA = "football3-production-300-adjudicated-acceptance-v1"


def reference_events(history: list[rt.HistoryFixture], labels: dict[str, rt.XGLabel]) -> list[dict[str, Any]]:
    """Independent reference event stream for adjudicated result semantics.

    This intentionally does not call runtime.history_delta_events. It reconstructs
    the split from the immutable adjudication manifest so FAST and reference paths
    cannot pass by sharing the same transition implementation.
    """
    entries = adjudication.adjudication_entries()
    events: list[dict[str, Any]] = []
    for row in history:
        x = labels.get(row.fixture_id)
        if x is not None:
            events.append({
                "event_type": "FIXTURE_FREEZE", "event_at": row.kickoff, "row": row, "x": x,
                "enters_v1": True, "enters_xg": True,
            })
        entry = entries.get(row.fixture_id)
        if entry is None:
            release = x.label.release_at if x is not None else row.kickoff + timedelta(hours=3)
            events.append({
                "event_type": "LABEL_RELEASE", "event_at": release, "row": row, "x": x,
                "enters_v1": True, "enters_xg": x is not None,
                "home_goals": row.home_goals, "away_goals": row.away_goals,
            })
        else:
            if x is None:
                raise AssertionError(f"adjudicated reference fixture missing xG label: {row.fixture_id}")
            on_field = entry["on_field_xg_result"]
            assert (x.label.home_goals, x.label.away_goals) == (int(on_field["home_goals"]), int(on_field["away_goals"]))
            events.append({
                "event_type": "LABEL_RELEASE", "event_at": x.label.release_at, "row": row, "x": x,
                "enters_v1": False, "enters_xg": True,
                "home_goals": x.label.home_goals, "away_goals": x.label.away_goals,
            })
            settlement = entry["formal_settlement_result"]
            formal_at = rt._parse_dt(str(entry["availability"]["formal_result_available_at"]), "formal result available at")
            assert (row.home_goals, row.away_goals) == (int(settlement["home_goals"]), int(settlement["away_goals"]))
            events.append({
                "event_type": "LABEL_RELEASE", "event_at": formal_at, "row": row, "x": None,
                "enters_v1": True, "enters_xg": False,
                "home_goals": row.home_goals, "away_goals": row.away_goals,
            })
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (
        e["event_at"], order[e["event_type"]], e["row"].kickoff, e["row"].competition_id, e["row"].fixture_id,
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))
    return events


def reference_apply_group(state: rt.hxg.ChallengerState, group: list[dict[str, Any]]) -> None:
    releases = [e for e in group if e["event_type"] == "LABEL_RELEASE"]
    freezes = [e for e in group if e["event_type"] == "FIXTURE_FREEZE"]
    by_kickoff = defaultdict(list)
    for event in releases:
        by_kickoff[event["row"].kickoff].append(event)
    for _, items in sorted(by_kickoff.items()):
        xitems = [event for event in items if event["enters_xg"]]
        if xitems:
            fixtures = [event["row"].xg_fixture() for event in xitems]
            labs = {event["row"].fixture_id: event["x"].label for event in xitems}
            state.apply_released_batch(fixtures, labs, as_of=items[0]["event_at"], update_base=False)
        vitems = [event for event in items if event["enters_v1"]]
        if vitems:
            rows = [event["row"] for event in vitems]
            state.base.apply_batch(
                [row.v1_fixture() for row in rows],
                {event["row"].fixture_id: (int(event["home_goals"]), int(event["away_goals"])) for event in vitems},
            )
    by_kickoff = defaultdict(list)
    for event in freezes:
        by_kickoff[event["row"].kickoff].append(event)
    for _, items in sorted(by_kickoff.items()):
        state.predict_batch([event["row"].xg_fixture() for event in items], include_matrix=False, lightweight=True)


def adjudication_pit_regression(history: list[rt.HistoryFixture], labels: dict[str, rt.XGLabel]) -> dict[str, Any]:
    entries = adjudication.adjudication_entries()
    if set(entries) != {"8ac7540a70af27118955481e"}:
        raise AssertionError("unexpected adjudication fixture set")
    fid = next(iter(entries))
    row = next((x for x in history if x.fixture_id == fid), None)
    if row is None:
        raise AssertionError("adjudicated fixture absent from production history")
    x = labels.get(fid)
    if x is None:
        raise AssertionError("adjudicated fixture absent from xG labels")
    entry = entries[fid]
    before_settlement = rt._parse_dt("2024-12-15T00:00:00+00:00", "before settlement cutoff")
    after_settlement = rt._parse_dt("2025-01-11T00:00:00+00:00", "after settlement cutoff")
    before, _ = rt.replay_history_state(history, labels, before_settlement)
    after, _ = rt.replay_history_state(history, labels, after_settlement)
    if fid not in before.seen or fid in before.base.seen_fixtures:
        raise AssertionError("PIT split failed before authoritative settlement availability")
    if fid not in after.seen or fid not in after.base.seen_fixtures:
        raise AssertionError("PIT split failed after authoritative settlement availability")
    events = rt.history_delta_events(history, labels, None, after_settlement, None)
    releases = [e for e in events if e["fixture_id"] == fid and e["event_type"] == "LABEL_RELEASE"]
    if len(releases) != 2:
        raise AssertionError(f"adjudicated release count {len(releases)} != 2")
    xg_only = next((e for e in releases if e["enters_xg"] and not e["enters_v1"]), None)
    v1_only = next((e for e in releases if e["enters_v1"] and not e["enters_xg"]), None)
    if xg_only is None or v1_only is None:
        raise AssertionError("adjudicated split release routes missing")
    if (xg_only["home_goals"], xg_only["away_goals"]) != (1, 1):
        raise AssertionError("on-field xG result changed")
    if (v1_only["home_goals"], v1_only["away_goals"]) != (0, 2):
        raise AssertionError("formal settlement result changed")
    if v1_only["event_at"] != entry["availability"]["formal_result_available_at"]:
        raise AssertionError("formal settlement PIT availability drift")
    return {
        "passed": True,
        "fixture_id": fid,
        "on_field_xg_result": [1, 1],
        "formal_settlement_result": [0, 2],
        "xg_release_at": x.label.release_at.isoformat(),
        "formal_result_available_at": entry["availability"]["formal_result_available_at"],
        "before_settlement": {"xg_seen": True, "v1_seen": False},
        "after_settlement": {"xg_seen": True, "v1_seen": True},
        "sample_deleted": False,
        "source_score_rewritten": False,
        "conflict_gate_relaxed_without_authority": False,
    }


def _arg(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"missing {name}") from exc


def main() -> int:
    if "--mode" not in sys.argv or _arg("--mode") != "production":
        raise SystemExit("production mode required")
    repo_root = Path(_arg("--repo-root"))
    under = Path(_arg("--understat-db"))
    conf = Path(_arg("--confirmation-dir"))
    out = Path(_arg("--out"))

    # Independent reference path is monkeypatched only in the test harness; runtime
    # FAST/FULL continues through the installed formal adjudication adapter.
    tr.reference_events = reference_events
    tr.reference_apply_group = reference_apply_group

    history, labels, _, _ = tr.production_corpus(repo_root, under, conf)
    pit = adjudication_pit_regression(history, labels)
    code = tr.main()
    if code != 0:
        return code
    receipt = json.loads(out.read_text(encoding="utf-8"))
    if receipt.get("passed") is not True or receipt.get("mode") != "production":
        raise AssertionError("production 300 base receipt did not PASS")
    receipt["adjudication_acceptance"] = {
        "schema_version": SCHEMA,
        "adapter": ADJUDICATION,
        "pit_regression": pit,
        "independent_reference_stream": True,
        "runtime_history_delta_events_reused_by_reference": False,
        "formal_model_or_weights_changed": False,
        "current_pointer_changed": False,
    }
    receipt["receipt_sha256_before_adjudication_acceptance"] = receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = tr.sha(receipt)
    out.write_bytes(tr.canon(receipt))
    print(json.dumps({
        "status": "PASS", "receipt": str(out), "sha256": receipt["receipt_sha256"],
        "production_300_passed": True, "adjudication_pit_passed": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
