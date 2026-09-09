#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import current_v2_retrospective_replay_v1 as replay
import live_delta_acquisition_v1 as live
import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-xg-coverage-v1"


def _audit_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def research_xg_labels(rows: list[live.V1Row], lower: datetime, upper: datetime,
                       base_state: Any) -> tuple[dict[str, rt.XGLabel], dict[str, Any]]:
    del base_state
    big5_rows = [r for r in rows if r.competition_id in live.BIG5]
    if not big5_rows:
        return {}, {
            "status": "COMPLETE_WITH_LEGAL_XG_COVERAGE_LIMITATION",
            "joined_results": 0,
            "missing_xg_history_count": 0,
            "target_score_fields_read_before_eligibility_gate": False,
        }
    index = {
        (r.competition_id, r.kickoff.date().isoformat(), rt._normalize_team(r.home_team_name), rt._normalize_team(r.away_team_name)): r
        for r in big5_rows
    }
    out: dict[str, rt.XGLabel] = {}
    sources: list[dict[str, Any]] = []
    for comp, league in live.UNDERSTAT.items():
        for start in live._cross_year_starts(lower, upper):
            try:
                obj, source_sha, url = live._understat_payload(comp, league, start)
            except live.AcquisitionError as exc:
                sources.append({
                    "competition_id": comp,
                    "season_start": start,
                    "status": "LEGAL_XG_SOURCE_UNAVAILABLE",
                    "reason": str(exc),
                    "joined": 0,
                })
                continue
            joined = 0
            for item in obj.get("dates") or []:
                if not isinstance(item, dict):
                    continue
                raw_dt = str(item.get("datetime") or "").strip()
                if not raw_dt:
                    continue
                try:
                    actual = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                    if actual.tzinfo is None:
                        actual = actual.replace(tzinfo=timezone.utc)
                    actual = actual.astimezone(timezone.utc)
                except ValueError:
                    continue
                # Eligibility is resolved before goals/xG fields are touched.
                if actual >= upper:
                    continue
                hraw = str((item.get("h") or {}).get("title") or "").strip()
                araw = str((item.get("a") or {}).get("title") or "").strip()
                row = index.get((comp, actual.date().isoformat(), rt._normalize_team(hraw), rt._normalize_team(araw)))
                if row is None or row.kickoff >= upper or not bool(item.get("isResult")):
                    continue
                xg = item.get("xG") or {}
                goals = item.get("goals") or {}
                try:
                    hx, ax = float(xg.get("h")), float(xg.get("a"))
                    hg, ag = int(float(goals.get("h"))), int(float(goals.get("a")))
                except Exception as exc:
                    raise rt.RuntimeGateError("retrospective xG payload invalid after eligibility gate") from exc
                if (hg, ag) != (row.home_goals, row.away_goals):
                    raise rt.RuntimeGateError("retrospective xG/V1 historical result conflict")
                release = row.kickoff + timedelta(hours=3)
                out[row.fixture_id] = rt.XGLabel(
                    rt.hxg.ReleasedLabel(hg, ag, hx, ax, release),
                    row.fixture_id,
                    source_sha,
                    row.kickoff.isoformat(),
                )
                joined += 1
            sources.append({
                "competition_id": comp,
                "season_start": start,
                "url": url,
                "sha256": source_sha,
                "status": "OBSERVED",
                "joined": joined,
            })
    missing = sorted(r.fixture_id for r in big5_rows if r.fixture_id not in out)
    return out, {
        "status": "COMPLETE" if not missing else "COMPLETE_WITH_LEGAL_XG_COVERAGE_LIMITATION",
        "joined_results": len(out),
        "eligible_big5_v1_results": len(big5_rows),
        "missing_xg_history_count": len(missing),
        "missing_xg_history_fixture_ids_sha256": _audit_sha256(missing),
        "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION",
        "strict_pit_claimed": False,
        "research_release_adapter": "kickoff_plus_3h",
        "sources": sources,
        "target_score_fields_read_before_eligibility_gate": False,
        "missing_xg_delegated_to_formal_evidence_route": True,
        "fallback_not_forced_by_adapter": True,
    }


def install(replay_module) -> dict[str, Any]:
    replay_module._current_xg_labels = research_xg_labels
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "missing_xg_policy": "DELEGATE_TO_CURRENT_FORMAL_V2_EVIDENCE_ROUTE",
        "legal_routes": ["FUSION_V2_ACTIVE", "FROZEN_V1_EXACT_FALLBACK"],
        "fallback_forced": False,
        "evidence_threshold_changed": False,
        "model_or_current_or_weight_changed": False,
    }
