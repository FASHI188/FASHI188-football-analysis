#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import live_delta_acquisition_v1 as live
import live_delta_semantics_v2 as sem
import live_source_contract_resolution_v1 as src
import runtime as rt

SCHEMA = "football3-live-xg-quarantine-v1"


def _dt(value: str) -> datetime:
    x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def _quarantine_role_conflict(v1_rows, lower: datetime, ceiling: datetime, state: Any,
                              result_xg: dict[str, dict[str, Any]], freezes: list[dict[str, Any]], report: dict[str, Any]):
    c = src.ROLE_REVERSAL
    formal = [
        r for r in v1_rows
        if r.competition_id == c["competition_id"]
        and r.kickoff.date().isoformat() == c["date"]
        and r.home_team_name == c["formal_home"]
        and r.away_team_name == c["formal_away"]
    ]
    if len(formal) != 1:
        raise live.AcquisitionError("FORMAL_XG_QUARANTINE_TARGET_NOT_UNIQUE",
                                    {"contract": c, "formal_candidates": len(formal)})
    vr = formal[0]

    obj, source_sha, url = live._understat_payload(c["competition_id"], live.UNDERSTAT[c["competition_id"]], 2026)
    candidates = []
    for item in obj.get("dates") or []:
        if not isinstance(item, dict):
            continue
        raw_dt = str(item.get("datetime") or "").strip()
        if not raw_dt:
            continue
        actual = _dt(raw_dt)
        h = str((item.get("h") or {}).get("title") or "").strip()
        a = str((item.get("a") or {}).get("title") or "").strip()
        if actual.date().isoformat() == c["date"] and h == c["source_home"] and a == c["source_away"]:
            candidates.append((item, actual))
    if len(candidates) != 1:
        raise live.AcquisitionError("FORMAL_XG_QUARANTINE_SOURCE_NOT_UNIQUE",
                                    {"contract": c, "source_candidates": len(candidates), "source": url})
    item, actual = candidates[0]
    if actual >= ceiling or not bool(item.get("isResult")):
        raise live.AcquisitionError("FORMAL_XG_QUARANTINE_SOURCE_NOT_RELEASED",
                                    {"contract": c, "source_actual_kickoff": actual.isoformat(), "ceiling": ceiling.isoformat()})
    goals = item.get("goals") or {}
    try:
        source_hg, source_ag = int(float(goals.get("h"))), int(float(goals.get("a")))
    except Exception as exc:
        raise live.AcquisitionError("FORMAL_XG_QUARANTINE_SOURCE_RESULT_INVALID", {"contract": c, "source": url}) from exc
    swapped = [source_ag, source_hg]
    formal_result = [vr.home_goals, vr.away_goals]
    if swapped == formal_result:
        # A future source correction would make the source role contract internally consistent.
        # Delegate to the original strict role-resolution path in that case.
        return src._ORIGINAL_ROLE_APPLY(v1_rows, lower, ceiling, state, result_xg, freezes, report)

    # Exact formal policy for a source-result conflict: keep official V1, quarantine only XG.
    result_xg.pop(vr.fixture_id, None)
    freezes[:] = [
        e for e in freezes
        if not (
            e.get("competition_id") == c["competition_id"]
            and rt._parse_dt(str(e.get("kickoff")), "kickoff").date().isoformat() == c["date"]
            and {
                str(e.get("home_team_name")), str(e.get("away_team_name"))
            } == {c["formal_home"], c["formal_away"]}
        )
    ]

    qids = report.setdefault("quarantined_fixture_ids", [])
    if vr.fixture_id not in qids:
        qids.append(vr.fixture_id)
    quarantine = {
        "schema_version": SCHEMA,
        "fixture_id": vr.fixture_id,
        "competition_id": vr.competition_id,
        "date": c["date"],
        "formal_home": vr.home_team_name,
        "formal_away": vr.away_team_name,
        "formal_result": formal_result,
        "source_home": c["source_home"],
        "source_away": c["source_away"],
        "role_swapped_source_result": swapped,
        "source": url,
        "source_sha256": source_sha,
        "source_actual_kickoff": actual.isoformat(),
        "official_evidence": c["official_evidence"],
        "resolution": "KEEP_FORMAL_V1_QUARANTINE_XG_ROW_LOCAL",
        "downstream": "later uncontested XG rows remain usable",
        "result_or_xg_used_to_select_fixture_identity": False,
        "model_parameters_or_weights_changed": False,
    }
    report.setdefault("xg_quarantines", []).append(quarantine)
    for s in report.get("sources", []):
        if s.get("competition_id") == c["competition_id"] and s.get("season_start") == 2026:
            s.setdefault("xg_quarantines", []).append(quarantine)
    return None


# Save the strict role path before replacing it so a future corrected source can still be validated normally.
src._ORIGINAL_ROLE_APPLY = src._apply_role_reversal
src._apply_role_reversal = _quarantine_role_conflict


def acquire_verified_delta(repo_root, lower: datetime, requested_ceiling: datetime, target_fixture_id: str, state: Any):
    lower = lower.astimezone(timezone.utc); requested_ceiling = requested_ceiling.astimezone(timezone.utc)
    fetch_started = datetime.now(timezone.utc).replace(microsecond=0)
    if requested_ceiling <= fetch_started:
        raise live.AcquisitionError(
            "FORMAL_INPUT_DATA_INCOMPLETE: requested cutoff is not prospective relative to live source observation",
            {"fetch_started": fetch_started.isoformat(), "requested_cutoff": requested_ceiling.isoformat(),
             "hint": "use an AUTO/current prospective cutoff when requesting a live prediction"},
        )

    v1_rows, v1_report = live.acquire_v1(repo_root, lower, requested_ceiling)
    xg_map, freeze_events, xg_report = sem._acquire_xg(v1_rows, lower, requested_ceiling, state)
    quarantined = set(map(str, xg_report.get("quarantined_fixture_ids") or []))
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    effective = min(requested_ceiling, observed)
    if effective <= lower:
        raise live.AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: effective live cutoff does not advance durable state",
                                    {"from": lower.isoformat(), "effective_cutoff": effective.isoformat()})

    seen_v1 = set(getattr(getattr(state, "base", None), "seen_fixtures", set()) or set())
    seen_xg = set(getattr(state, "seen", set()) or set())
    pending_xg = set(getattr(state, "pending", {}) or {})
    last_v1 = getattr(getattr(state, "base", None), "last_update_time", None)
    events: list[dict[str, Any]] = []

    for e in freeze_events:
        at = rt._parse_dt(str(e["event_at"]), "freeze event_at")
        if (at >= effective or e["fixture_id"] == target_fixture_id or e["fixture_id"] in pending_xg
                or e["fixture_id"] in seen_xg or e["fixture_id"] in quarantined):
            continue
        events.append(e)

    gaps = []
    included_v1 = 0; included_xg = 0; included_quarantined_v1 = 0
    for r in v1_rows:
        if r.fixture_id == target_fixture_id or r.fixture_id in seen_v1:
            continue
        big5 = r.competition_id in live.BIG5
        is_quarantined = r.fixture_id in quarantined
        enters_xg = big5 and not is_quarantined
        x = xg_map.get(r.fixture_id)
        if enters_xg:
            if x is None:
                if r.kickoff + timedelta(hours=3) <= effective:
                    gaps.append({"fixture_id": r.fixture_id, "competition_id": r.competition_id,
                                 "kickoff": r.kickoff.isoformat(), "reason": "Big5 result lacks verified Understat XG after release window"})
                continue
            release = rt._parse_dt(str(x["release_at"]), "xg release_at")
        else:
            # Same V1-only release adapter already used by the frozen formal replay for rows without accepted XG.
            release = r.kickoff + timedelta(hours=3)
        if release > effective:
            continue
        if last_v1 is not None and r.kickoff < last_v1:
            gaps.append({"fixture_id": r.fixture_id, "competition_id": r.competition_id,
                         "kickoff": r.kickoff.isoformat(), "reason": "unseen V1 result kickoff precedes cached V1 last_update_time"})
            continue
        if enters_xg and r.fixture_id not in pending_xg and r.fixture_id not in seen_xg and r.kickoff < lower:
            gaps.append({"fixture_id": r.fixture_id, "competition_id": r.competition_id,
                         "kickoff": r.kickoff.isoformat(), "reason": "XG freeze predates cached cutoff and is absent from pending state"})
            continue
        source_sha = r.source_sha256 if x is None else live.sha_bytes((r.source_sha256 + "+" + str(x["source_sha256"])).encode("utf-8"))
        source = r.source if x is None else r.source + " + " + str(x["source"])
        e = {
            "event_type": "LABEL_RELEASE", "event_at": release.isoformat(), "result_available_at": release.isoformat(),
            "fixture_id": r.fixture_id, "competition_id": r.competition_id, "season": r.season,
            "home_team_id": r.home_team_id, "away_team_id": r.away_team_id,
            "home_team_name": r.home_team_name, "away_team_name": r.away_team_name, "kickoff": r.kickoff.isoformat(),
            "source": source, "source_content_sha256": source_sha, "enters_v1": True, "enters_xg": enters_xg,
            "home_goals": r.home_goals, "away_goals": r.away_goals,
        }
        if x is not None and enters_xg:
            e["home_xg"] = float(x["home_xg"]); e["away_xg"] = float(x["away_xg"]); included_xg += 1
        if is_quarantined:
            included_quarantined_v1 += 1
        events.append(e); included_v1 += 1

    if gaps:
        raise live.AcquisitionError("FAST_DELTA_CONTINUITY_GAP_REQUIRES_FULL_REBUILD",
                                    {"stage": "DELTA_CONTINUITY", "gaps": gaps[:100], "gap_n": len(gaps),
                                     "effective_cutoff": effective.isoformat(), "v1": v1_report, "xg": xg_report})

    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (rt._parse_dt(str(e["event_at"]), "event_at"), order[e["event_type"]],
                               rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"])))
    checked = rt._validate_delta_records(events, target_fixture_id, lower, effective)
    source_shas = sorted({str(e["source_content_sha256"]) for e in checked})
    source_set_sha = live.sha_bytes(live.canon(source_shas + [lower.isoformat(), effective.isoformat(), observed.isoformat()]))
    report = {
        "schema_version": SCHEMA, "status": "VERIFIED_COMPLETE", "fetch_started": fetch_started.isoformat(),
        "observed_at": observed.isoformat(), "requested_ceiling": requested_ceiling.isoformat(),
        "effective_cutoff": effective.isoformat(), "from": lower.isoformat(), "to": effective.isoformat(),
        "records": len(checked), "v1_label_releases": included_v1, "xg_label_releases": included_xg,
        "quarantined_xg_fixture_ids": sorted(quarantined), "quarantined_v1_label_releases": included_quarantined_v1,
        "records_sha256": rt._sha_bytes(rt._canon_bytes(checked)), "source_set_sha256": source_set_sha,
        "v1": v1_report, "xg": xg_report,
        "release_semantics": "accepted Big5 XG: Understat source kickoff +3h; explicit row-local XG quarantine and non-Big5 V1: formal engineering kickoff +3h",
        "quarantine_policy": "source-result-conflict XG is excluded row-locally; official V1 result remains; later uncontested rows remain usable",
        "cutoff_semantics": "future requested ceiling is clamped to the time all live source payloads were actually observed",
        "manual_or_auxiliary_fallback_used": False,
    }
    return checked, report


def install() -> dict[str, Any]:
    sem.acquire_verified_delta = acquire_verified_delta
    live.acquire_verified_delta = acquire_verified_delta
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "row_local_xg_quarantine": True,
        "v1_result_preserved_on_xg_source_conflict": True,
        "later_uncontested_xg_rows_remain_usable": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
