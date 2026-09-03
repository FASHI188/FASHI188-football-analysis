#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import live_delta_acquisition_v1 as live
import runtime as rt

SCHEMA = "football3-live-delta-acquisition-v2"


def _acquire_xg(v1_rows, lower: datetime, ceiling: datetime, state: Any):
    lookup = live._team_lookup(v1_rows)
    v1_index = {(r.competition_id, r.kickoff.date().isoformat(), rt._normalize_team(r.home_team_name), rt._normalize_team(r.away_team_name)): r for r in v1_rows}
    result_xg: dict[str, dict[str, Any]] = {}
    freezes: list[dict[str, Any]] = []
    sources = []
    failures = {}
    existing_pending = set(getattr(state, "pending", {}) or {})
    existing_seen = set(getattr(state, "seen", set()) or set())

    for comp, league in live.UNDERSTAT.items():
        for start in live._cross_year_starts(lower, ceiling):
            try:
                obj, source_sha, url = live._understat_payload(comp, league, start)
            except Exception as exc:
                failures[f"{comp}:{start}"] = f"{type(exc).__name__}: {exc}"
                continue
            rows = obj.get("dates") or []
            joined = 0; scheduled = 0
            for item in rows:
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
                formal_ko = actual.replace(hour=0, minute=0, second=0, microsecond=0)
                if formal_ko >= ceiling:
                    continue
                hraw = str((item.get("h") or {}).get("title") or "").strip()
                araw = str((item.get("a") or {}).get("title") or "").strip()
                if not hraw or not araw:
                    continue
                h = lookup.get((comp, rt._normalize_team(hraw)), hraw)
                a = lookup.get((comp, rt._normalize_team(araw)), araw)
                season = live._season_label_cross(start)
                fid = rt._fixture_id(comp, season, formal_ko, h, a)
                key = (comp, formal_ko.date().isoformat(), rt._normalize_team(h), rt._normalize_team(a))
                vr = v1_index.get(key)
                if vr is not None:
                    fid = vr.fixture_id; h = vr.home_team_name; a = vr.away_team_name; season = vr.season; formal_ko = vr.kickoff

                if fid not in existing_pending and fid not in existing_seen and lower <= formal_ko < ceiling:
                    freezes.append({
                        "event_type": "FIXTURE_FREEZE", "event_at": formal_ko.isoformat(), "fixture_id": fid,
                        "competition_id": comp, "season": season, "home_team_id": rt._global_team_id(h),
                        "away_team_id": rt._global_team_id(a), "home_team_name": h, "away_team_name": a,
                        "kickoff": formal_ko.isoformat(), "source": url, "source_content_sha256": source_sha,
                        "enters_v1": True, "enters_xg": True,
                    }); scheduled += 1

                if not bool(item.get("isResult")):
                    continue
                xg = item.get("xG") or {}; goals = item.get("goals") or {}
                try:
                    hx, ax = float(xg.get("h")), float(xg.get("a")); hg, ag = int(float(goals.get("h"))), int(float(goals.get("a")))
                except Exception:
                    continue
                if vr is None:
                    continue
                if (hg, ag) != (vr.home_goals, vr.away_goals):
                    raise live.AcquisitionError(f"XG/V1 result conflict: {comp} {vr.kickoff.date()} {h} v {a}")
                result_xg[vr.fixture_id] = {
                    "home_xg": hx, "away_xg": ax, "source": url, "source_sha256": source_sha,
                    "source_kickoff": actual.isoformat(), "release_at": (actual + timedelta(hours=3)).isoformat(),
                }
                joined += 1
            sources.append({"competition_id": comp, "season_start": start, "url": url, "sha256": source_sha,
                            "dates_rows": len(rows), "result_joined": joined, "freeze_candidates": scheduled})
    if failures:
        raise live.AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: Understat acquisition failed",
                                    {"stage": "XG_ACQUISITION", "failures": failures, "sources": sources})
    return result_xg, freezes, {"status": "FETCH_COMPLETE", "sources": sources, "joined_results": len(result_xg), "freeze_events": len(freezes)}


def acquire_verified_delta(repo_root, lower: datetime, requested_ceiling: datetime, target_fixture_id: str, state: Any):
    lower = lower.astimezone(timezone.utc); requested_ceiling = requested_ceiling.astimezone(timezone.utc)
    fetch_started = datetime.now(timezone.utc).replace(microsecond=0)
    if requested_ceiling <= fetch_started:
        raise live.AcquisitionError(
            "FORMAL_INPUT_DATA_INCOMPLETE: requested cutoff is not prospective relative to live source observation",
            {"fetch_started": fetch_started.isoformat(), "requested_cutoff": requested_ceiling.isoformat(),
             "hint": "use an AUTO/current prospective cutoff when requesting a live prediction"},
        )

    # Fetch full designated source payloads up to the requested ceiling, then seal at the time by which every payload was actually observed.
    v1_rows, v1_report = live.acquire_v1(repo_root, lower, requested_ceiling)
    xg_map, freeze_events, xg_report = _acquire_xg(v1_rows, lower, requested_ceiling, state)
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
        if at >= effective or e["fixture_id"] == target_fixture_id or e["fixture_id"] in pending_xg or e["fixture_id"] in seen_xg:
            continue
        events.append(e)

    gaps = []
    included_v1 = 0; included_xg = 0
    for r in v1_rows:
        if r.fixture_id == target_fixture_id or r.fixture_id in seen_v1:
            continue
        enters_xg = r.competition_id in live.BIG5
        x = xg_map.get(r.fixture_id)
        if enters_xg:
            if x is None:
                # Only require an XG join after the formal +3h release window has elapsed by the effective cutoff.
                if r.kickoff + timedelta(hours=3) <= effective:
                    gaps.append({"fixture_id": r.fixture_id, "competition_id": r.competition_id,
                                 "kickoff": r.kickoff.isoformat(), "reason": "Big5 result lacks verified Understat XG after release window"})
                continue
            release = rt._parse_dt(str(x["release_at"]), "xg release_at")
        else:
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
        if x is not None:
            e["home_xg"] = float(x["home_xg"]); e["away_xg"] = float(x["away_xg"]); included_xg += 1
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
        "records_sha256": rt._sha_bytes(rt._canon_bytes(checked)), "source_set_sha256": source_set_sha,
        "v1": v1_report, "xg": xg_report,
        "release_semantics": "Big5: Understat source kickoff +3h; non-Big5 V1: formal engineering kickoff +3h; source payloads all observed no later than effective_cutoff",
        "cutoff_semantics": "future requested ceiling is clamped to the time all live source payloads were actually observed",
        "manual_or_auxiliary_fallback_used": False,
    }
    return checked, report


def install() -> dict[str, Any]:
    live.acquire_verified_delta = acquire_verified_delta
    return {"schema_version": SCHEMA, "installed": True,
            "future_cutoff_clamped_to_observation": True, "model_parameters_or_weights_changed": False}
