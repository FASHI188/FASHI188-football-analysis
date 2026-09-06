#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FAST = ROOT / "formal_fast_runtime_v1"
GATEWAY = ROOT / "formal_gpt_gateway_v1"
for path in (FAST, GATEWAY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import runtime as rt
import test_runtime as tr


EXPECTED_BINDINGS = {
    ("ITA_SerieA", "2026/27", "AS Roma"): ("2025/26", "Roma"),
    ("JPN_J1", "2026", "Kyoto Sanga"): ("2025", "Kyoto"),
    ("KOR_KLeague1", "2026", "Jeonbuk Hyundai Motors"): ("2025", "전북"),
    ("KOR_KLeague1", "2026", "FC Seoul"): ("2025", "서울"),
}


def _fixture(fid: str, kickoff: datetime, home: str, away: str) -> rt.HistoryFixture:
    return rt.HistoryFixture(
        fid,
        "ITA_SerieA",
        "2025/26",
        kickoff,
        rt._global_team_id(home),
        rt._global_team_id(away),
        home,
        away,
        1,
        0,
        "targeted-reference-regression",
        "0" * 64,
    )


def reference_time_reversal_regression() -> dict[str, Any]:
    older = _fixture(
        "targeted:older",
        datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        "Older Home",
        "Older Away",
    )
    newer = _fixture(
        "targeted:newer",
        datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        "Newer Home",
        "Newer Away",
    )
    events = [
        {
            "event_type": "LABEL_RELEASE",
            "event_at": datetime(2026, 1, 2, 15, tzinfo=timezone.utc),
            "row": newer,
            "x": None,
            "enters_v1": True,
            "enters_xg": False,
            "home_goals": 1,
            "away_goals": 0,
        },
        {
            "event_type": "LABEL_RELEASE",
            "event_at": datetime(2026, 1, 3, 15, tzinfo=timezone.utc),
            "row": older,
            "x": None,
            "enters_v1": True,
            "enters_xg": False,
            "home_goals": 1,
            "away_goals": 0,
        },
    ]
    state = rt.formal_v2.new_candidate_state()
    tr._REFERENCE_V1_AVAILABLE.pop(id(state), None)

    pos = tr.reference_advance(
        state,
        events,
        0,
        datetime(2026, 1, 2, 16, tzinfo=timezone.utc),
    )
    if pos != 1 or newer.fixture_id not in state.base.seen_fixtures:
        raise AssertionError("newer settlement did not enter at first cutoff")

    pos = tr.reference_advance(
        state,
        events,
        pos,
        datetime(2026, 1, 3, 16, tzinfo=timezone.utc),
    )
    if pos != 2:
        raise AssertionError("reference position did not advance monotonically")
    expected_seen = {older.fixture_id, newer.fixture_id}
    if not expected_seen.issubset(state.base.seen_fixtures):
        raise AssertionError("late authoritative settlement rebuild lost a V1 fixture")
    if state.base.last_update_time != newer.kickoff:
        raise AssertionError("V1 rebuild is not ordered by fixture kickoff")
    return {
        "status": "PASS",
        "seen": sorted(expected_seen),
        "last_update_time": state.base.last_update_time.isoformat(),
        "engine_time_reversal_swallowed": False,
    }


def _processed_count(comp: str, historical: str) -> int:
    root = ROOT / "processed" / comp
    if not root.is_dir():
        raise AssertionError(f"processed competition directory missing: {comp}")
    total = 0
    files = sorted(root.glob("*.csv"))
    if not files:
        raise AssertionError(f"processed competition CSV missing: {comp}")
    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            home_key = next((x for x in ("HomeTeam", "home_team", "home") if x in fields), None)
            away_key = next((x for x in ("AwayTeam", "away_team", "away") if x in fields), None)
            if home_key is None or away_key is None:
                continue
            for row in reader:
                total += int(str(row.get(home_key) or "").strip() == historical)
                total += int(str(row.get(away_key) or "").strip() == historical)
    return total


def _strength_matches(comp: str, historical: str) -> int:
    path = ROOT / "team_strengths" / comp / "latest.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    hits = [
        row
        for row in obj.get("teams") or []
        if isinstance(row, dict) and str(row.get("team_name") or "").strip() == historical
    ]
    if len(hits) != 1:
        raise AssertionError(
            f"historical strength entity not unique: {comp} {historical} hits={len(hits)}"
        )
    return int(((hits[0].get("overall") or {}).get("matches")) or 0)


def four_team_authoritative_binding_regression() -> dict[str, Any]:
    path = ROOT / "config" / "cross_season_identity_continuity_v1.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != "football3-cross-season-identity-continuity-v1":
        raise AssertionError("continuity registry schema drift")
    rows = [row for row in obj.get("rows") or [] if isinstance(row, dict)]

    report = {}
    for key, (previous, historical) in EXPECTED_BINDINGS.items():
        comp, season, current = key
        hits = [
            row
            for row in rows
            if str(row.get("competition_id") or "") == comp
            and str(row.get("current_season") or "") == season
            and str(row.get("current_canonical_name") or "") == current
        ]
        if len(hits) != 1:
            raise AssertionError(
                f"continuity row not unique: {comp} {season} {current} hits={len(hits)}"
            )
        row = hits[0]
        if str(row.get("previous_season") or "") != previous:
            raise AssertionError(f"previous-season authority drift: {current}")
        if str(row.get("previous_processed_name") or "") != historical:
            raise AssertionError(f"historical entity drift: {current}")
        evidence = [str(x).strip() for x in row.get("evidence") or [] if str(x).strip()]
        if not evidence:
            raise AssertionError(f"authoritative evidence missing: {current}")

        history_count = _processed_count(comp, historical)
        strength_count = _strength_matches(comp, historical)
        if history_count <= 0:
            raise AssertionError(f"processed historical binding is zero: {current}->{historical}")
        if strength_count <= 0:
            raise AssertionError(f"strength-state binding is zero: {current}->{historical}")
        report[current] = {
            "competition_id": comp,
            "current_season": season,
            "previous_season": previous,
            "historical_entity": historical,
            "processed_team_appearances": history_count,
            "strength_matches": strength_count,
            "authoritative_evidence_n": len(evidence),
            "fuzzy_matching_used": False,
            "score_or_xg_assisted_identity": False,
        }
    return {"status": "PASS", "teams": report}


def main() -> int:
    receipt = {
        "schema_version": "football3-targeted-time-reversal-four-team-binding-regression-v1",
        "production_reference_ordering": reference_time_reversal_regression(),
        "four_team_binding": four_team_authoritative_binding_regression(),
        "full_final_acceptance_run": False,
        "formal_model_or_weights_changed": False,
        "current_pointer_changed": False,
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
