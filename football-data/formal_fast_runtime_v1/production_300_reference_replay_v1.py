#!/usr/bin/env python3
from __future__ import annotations

import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import test_runtime_core_v1 as core

rt = core.rt
_REFERENCE_V1_AVAILABLE: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
_BASE_BENCHMARK_PATHS = core.benchmark_paths


def reset_reference_state() -> None:
    _REFERENCE_V1_AVAILABLE.clear()


def _active_adjudication_entries() -> dict[str, dict[str, Any]]:
    module = sys.modules.get("formal_result_adjudication_v2")
    if module is None:
        return {}
    return module.adjudication_entries()


def choose_safe_seed_cutoff(sample: list[rt.HistoryFixture]) -> datetime:
    if not sample:
        raise AssertionError("empty mechanical sample")
    first_target_cutoff = sample[0].kickoff - timedelta(minutes=60)
    seed = first_target_cutoff - timedelta(days=1)
    if not seed < first_target_cutoff:
        raise AssertionError("seed cutoff must strictly precede first target cutoff")
    return seed


def reference_events(history: list[rt.HistoryFixture], labels: dict[str, rt.XGLabel]) -> list[dict[str, Any]]:
    """Independent PIT reference stream with authoritative split-result semantics."""
    entries = _active_adjudication_entries()
    events: list[dict[str, Any]] = []
    for row in history:
        x = labels.get(row.fixture_id)
        if x is not None:
            events.append({"event_type": "FIXTURE_FREEZE", "event_at": row.kickoff, "row": row, "x": x})
        entry = entries.get(row.fixture_id)
        if entry is None:
            release = x.label.release_at if x is not None else row.kickoff + timedelta(hours=3)
            events.append({
                "event_type": "LABEL_RELEASE", "event_at": release, "row": row, "x": x,
                "enters_v1": True, "enters_xg": x is not None,
                "home_goals": row.home_goals, "away_goals": row.away_goals,
            })
            continue
        if x is None:
            raise AssertionError(f"adjudicated reference fixture missing xG label: {row.fixture_id}")
        on_field = entry["on_field_xg_result"]
        if (x.label.home_goals, x.label.away_goals) != (int(on_field["home_goals"]), int(on_field["away_goals"])):
            raise AssertionError(f"adjudicated on-field xG result drift: {row.fixture_id}")
        events.append({
            "event_type": "LABEL_RELEASE", "event_at": x.label.release_at, "row": row, "x": x,
            "enters_v1": False, "enters_xg": True,
            "home_goals": x.label.home_goals, "away_goals": x.label.away_goals,
        })
        settlement = entry["formal_settlement_result"]
        if (row.home_goals, row.away_goals) != (int(settlement["home_goals"]), int(settlement["away_goals"])):
            raise AssertionError(f"adjudicated formal settlement drift: {row.fixture_id}")
        formal_at = rt._parse_dt(str(entry["availability"]["formal_result_available_at"]), "formal result available at")
        events.append({
            "event_type": "LABEL_RELEASE", "event_at": formal_at, "row": row, "x": None,
            "enters_v1": True, "enters_xg": False,
            "home_goals": row.home_goals, "away_goals": row.away_goals,
        })
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (
        e["event_at"], order[e["event_type"]], e["row"].kickoff,
        e["row"].competition_id, e["row"].fixture_id,
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))
    return events


def _reference_rebuild_v1(state: rt.hxg.ChallengerState) -> None:
    available = _REFERENCE_V1_AVAILABLE[id(state)]
    rebuilt = rt.v1_engine.EngineState(params=state.base.params)
    by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for event in available.values():
        by_kickoff[event["row"].kickoff].append(event)
    for _, items in sorted(by_kickoff.items()):
        items = sorted(items, key=lambda event: (event["row"].competition_id, event["row"].fixture_id))
        rows = [event["row"] for event in items]
        rebuilt.apply_batch(
            [row.v1_fixture() for row in rows],
            {event["row"].fixture_id: (int(event["home_goals"]), int(event["away_goals"])) for event in items},
        )
    state.base = rebuilt


def reference_apply_group(state: rt.hxg.ChallengerState, group: list[dict[str, Any]]) -> None:
    """Apply one event_at transaction without forcing an old kickoff through V1."""
    if not group:
        return
    event_at = group[0]["event_at"]
    if any(event["event_at"] != event_at for event in group):
        raise AssertionError("reference same-time group contains multiple event_at values")
    releases = [event for event in group if event["event_type"] == "LABEL_RELEASE"]
    freezes = [event for event in group if event["event_type"] == "FIXTURE_FREEZE"]
    by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for event in releases:
        by_kickoff[event["row"].kickoff].append(event)
    for _, items in sorted(by_kickoff.items()):
        items = sorted(items, key=lambda event: (event["row"].competition_id, event["row"].fixture_id))
        xitems = [event for event in items if bool(event.get("enters_xg"))]
        if xitems:
            fixtures = [event["row"].xg_fixture() for event in xitems]
            xg_labels = {event["row"].fixture_id: event["x"].label for event in xitems}
            state.apply_released_batch(fixtures, xg_labels, as_of=event_at, update_base=False)
        vitems = [event for event in items if bool(event.get("enters_v1", True))]
        if vitems:
            available = _REFERENCE_V1_AVAILABLE[id(state)]
            for event in vitems:
                fid = event["row"].fixture_id
                if fid in available:
                    raise AssertionError(f"duplicate reference V1 release: {fid}")
                available[fid] = event
            last = state.base.last_update_time
            late = last is not None and any(event["row"].kickoff < last for event in vitems)
            if late:
                _reference_rebuild_v1(state)
            else:
                rows = [event["row"] for event in vitems]
                state.base.apply_batch(
                    [row.v1_fixture() for row in rows],
                    {event["row"].fixture_id: (int(event["home_goals"]), int(event["away_goals"])) for event in vitems},
                )
    by_kickoff = defaultdict(list)
    for event in freezes:
        by_kickoff[event["row"].kickoff].append(event)
    for _, items in sorted(by_kickoff.items()):
        items = sorted(items, key=lambda event: (event["row"].competition_id, event["row"].fixture_id))
        state.predict_batch([event["row"].xg_fixture() for event in items], include_matrix=False, lightweight=True)


def reference_advance(state: rt.hxg.ChallengerState, events: list[dict[str, Any]], pos: int, cutoff: datetime) -> int:
    """Advance a monotone event cursor; labels at cutoff enter, freezes at cutoff wait."""
    if pos < 0 or pos > len(events):
        raise AssertionError("reference cursor out of range")
    while pos < len(events):
        at = events[pos]["event_at"]
        if pos and events[pos - 1]["event_at"] > at:
            raise AssertionError("reference cursor source ordering regressed")
        end = pos + 1
        while end < len(events) and events[end]["event_at"] == at:
            end += 1
        if at > cutoff:
            break
        if at < cutoff:
            reference_apply_group(state, events[pos:end])
            pos = end
            continue
        eligible_end = pos
        while eligible_end < end and events[eligible_end]["event_type"] == "LABEL_RELEASE":
            eligible_end += 1
        if any(events[k]["event_type"] == "LABEL_RELEASE" for k in range(eligible_end, end)):
            raise AssertionError("reference event ordering violated at exact cutoff")
        if eligible_end > pos:
            reference_apply_group(state, events[pos:eligible_end])
            pos = eligible_end
        break
    return pos


def benchmark_paths(history, labels, source, identity, sample, tmp: Path) -> dict[str, Any]:
    mid = len(sample) // 2
    order = sorted(range(len(sample)), key=lambda i: (abs(i - mid), i))
    skipped = 0
    for rank, i in enumerate(order):
        target = sample[i]
        candidate_tmp = tmp / f"bench-fast-eligible-{rank}"
        candidate_tmp.mkdir(parents=True, exist_ok=True)
        try:
            result = _BASE_BENCHMARK_PATHS(history, labels, source, identity, [target], candidate_tmp)
        except AssertionError as exc:
            if str(exc).startswith("FAST benchmark route mismatch: V1_LATE_RELEASE_REQUIRES_FULL_REBUILD"):
                skipped += 1
                continue
            raise
        result["benchmark_target_fixture_id"] = target.fixture_id
        result["benchmark_target_kickoff"] = target.kickoff.isoformat()
        result["skipped_late_release_windows_n"] = skipped
        result["selection_policy"] = "nearest mechanical-sample midpoint window that is FAST-eligible; delayed authoritative V1 settlement remains trusted-FULL-only"
        return result
    raise AssertionError("no FAST-eligible benchmark window after delayed-settlement exclusions")


def run_equivalence(history, labels, source, identity, n: int, tmp: Path) -> dict[str, Any]:
    sample = core.mechanical_sample(history, n)
    sample_identity = [{"fixture_id": r.fixture_id, "kickoff": r.kickoff.isoformat(), "competition_id": r.competition_id} for r in sample]
    seed_cutoff = choose_safe_seed_cutoff(sample)
    refs = reference_events(history, labels)
    ref_state = rt.formal_v2.new_candidate_state()
    _REFERENCE_V1_AVAILABLE.pop(id(ref_state), None)
    ref_pos = reference_advance(ref_state, refs, 0, seed_cutoff)
    seed_fast, _ = rt.replay_history_state(history, labels, seed_cutoff)
    cache_state = rt.deserialize_state(rt.serialize_v1_state(seed_fast.base), rt.serialize_xg_state(seed_fast))
    prev = seed_cutoff
    max1 = maxm = 0.0
    meta_ok = cutoff_ok = state_equal = True
    fallback = active = 0
    paths = defaultdict(int)
    fast_core: list[float] = []
    late_release_full_rebuild_n = 0
    for target in sample:
        cutoff = target.kickoff - timedelta(minutes=60)
        if cutoff < prev:
            raise AssertionError("mechanical sample cutoff order regressed")
        next_pos = reference_advance(ref_state, refs, ref_pos, cutoff)
        if next_pos < ref_pos:
            raise AssertionError("reference cursor regressed")
        ref_pos = next_pos
        ref_for_prediction = rt.deserialize_state(rt.serialize_v1_state(ref_state.base), rt.serialize_xg_state(ref_state))
        pfull = rt._prediction_from_state(ref_for_prediction, target.xg_fixture())
        delta = core.make_delta(history, labels, prev, cutoff, target.fixture_id)
        inp = core.runtime_input(target, prev, cutoff, delta, "COMPLETE")
        t = time.perf_counter()
        checked = rt.validate_runtime_input(inp, core.target_payload(target), prev, cutoff)
        try:
            rt.apply_delta(cache_state, checked["delta"], cutoff)
        except rt.RuntimeGateError as exc:
            if str(exc) != "V1_LATE_RELEASE_REQUIRES_FULL_REBUILD":
                raise
            cache_state, _ = rt.replay_history_state(history, labels, cutoff)
            late_release_full_rebuild_n += 1
        cache_state = rt.deserialize_state(rt.serialize_v1_state(cache_state.base), rt.serialize_xg_state(cache_state))
        fast_for_prediction = rt.deserialize_state(rt.serialize_v1_state(cache_state.base), rt.serialize_xg_state(cache_state))
        pfast = rt._prediction_from_state(fast_for_prediction, target.xg_fixture())
        fast_core.append(time.perf_counter() - t)
        eq = core.compare_with_cutoff(pfast, pfull, cutoff, cutoff)
        max1 = max(max1, eq["max_1x2"])
        maxm = max(maxm, eq["max_matrix"])
        meta_ok = meta_ok and bool(eq.get("metadata_equal"))
        cutoff_ok = cutoff_ok and bool(eq["cutoff_equal"])
        sv1 = rt.serialize_v1_state(cache_state.base)
        sxg = rt.serialize_xg_state(cache_state)
        rv1 = rt.serialize_v1_state(ref_state.base)
        rxg = rt.serialize_xg_state(ref_state)
        state_equal = state_equal and sv1 == rv1 and sxg == rxg
        if not eq["passed"] or not (sv1 == rv1 and sxg == rxg):
            raise AssertionError(f"equivalence failure fixture={target.fixture_id}: pred={eq} state_equal={sv1 == rv1 and sxg == rxg}")
        audit = pfast["row"]["audit"]
        paths[str(audit["route"])] += 1
        fallback += int(bool(audit["fallback_exact_v1"]))
        active += int(not bool(audit["fallback_exact_v1"]))
        prev = cutoff
    core_times = sorted(fast_core)
    timing = {
        "n": len(core_times), "mean_s": statistics.mean(core_times), "median_s": statistics.median(core_times),
        "p95_s": core_times[min(len(core_times) - 1, math.ceil(.95 * len(core_times)) - 1)],
        "min_s": core_times[0], "max_s": core_times[-1],
    }
    bench = benchmark_paths(history, labels, source, identity, sample, tmp)
    return {
        "passed": max1 <= 1e-12 and maxm <= 1e-12 and meta_ok and cutoff_ok and state_equal,
        "n": n,
        "selection_rule": "sort 2024/25 Big-5 by kickoff,competition,fixture_id; take floor(i*N/n), i=0..n-1",
        "sample_identity_sha256": core.sha(sample_identity), "first": sample_identity[0], "last": sample_identity[-1],
        "seed_cutoff": seed_cutoff.isoformat(), "reference_final_pos": ref_pos, "reference_event_n": len(refs),
        "max_abs_1x2": max1, "max_abs_score_matrix_cell": maxm, "metadata_equal": meta_ok,
        "cutoff_equal": cutoff_ok, "cache_state_exact": state_equal, "formal_routes": dict(paths),
        "active_n": active, "fallback_n": fallback, "fast_core_timing": timing, "route_benchmark": bench,
        "sample": sample, "late_release_full_rebuild_n": late_release_full_rebuild_n,
        "late_release_policy": "FAIL_CLOSED_TO_TRUSTED_FULL_REBUILD",
    }
