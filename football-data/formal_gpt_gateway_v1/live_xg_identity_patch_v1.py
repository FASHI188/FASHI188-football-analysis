#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import live_delta_acquisition_v1 as live
import live_delta_semantics_v2 as sem
import runtime as rt

SCHEMA = "football3-live-xg-identity-patch-v1"


def _assign(mapping: dict[str, str], reverse: dict[str, str], source: str, target: str, evidence: list[dict[str, Any]], reason: str) -> bool:
    if source in mapping:
        if mapping[source] != target:
            raise live.AcquisitionError(
                "FORMAL_XG_IDENTITY_AMBIGUOUS",
                {"source_team": source, "existing": mapping[source], "candidate": target, "reason": reason},
            )
        return False
    other = reverse.get(target)
    if other is not None and other != source:
        raise live.AcquisitionError(
            "FORMAL_XG_IDENTITY_NON_BIJECTIVE",
            {"target_team": target, "source_team": source, "already_source_team": other, "reason": reason},
        )
    mapping[source] = target
    reverse[target] = source
    evidence.append({"source_team": source, "formal_team": target, "reason": reason})
    return True


def _learn_identity(comp: str, source_rows: list[dict[str, Any]], v1_rows) -> tuple[dict[str, str], dict[str, Any]]:
    formal = [r for r in v1_rows if r.competition_id == comp]
    by_date: dict[str, list[Any]] = defaultdict(list)
    formal_names: set[str] = set()
    for r in formal:
        by_date[r.kickoff.date().isoformat()].append(r)
        formal_names.update((r.home_team_name, r.away_team_name))

    src_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_names: set[str] = set()
    for r in source_rows:
        src_by_date[r["date"]].append(r)
        source_names.update((r["home"], r["away"]))

    norm_targets: dict[str, set[str]] = defaultdict(set)
    for name in formal_names:
        norm_targets[rt._normalize_team(name)].add(name)

    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []

    # Seed only exact normalized identity. No result/xG values are consulted.
    for source in sorted(source_names):
        hits = norm_targets.get(rt._normalize_team(source), set())
        if len(hits) == 1:
            _assign(mapping, reverse, source, next(iter(hits)), evidence, "exact_normalized_team_identity")

    # Resolve aliases mechanically from same-calendar-date home/away schedule identity.
    # Known exact identities anchor their opponent; a unique single-fixture date may also anchor both sides.
    changed = True
    while changed:
        changed = False
        for date in sorted(src_by_date):
            peers = by_date.get(date, [])
            if not peers:
                continue
            srcs = src_by_date[date]
            for s in srcs:
                mh = mapping.get(s["home"]); ma = mapping.get(s["away"])
                candidates = [
                    r for r in peers
                    if (mh is None or r.home_team_name == mh)
                    and (ma is None or r.away_team_name == ma)
                ]
                if len(candidates) == 1 and (mh is not None or ma is not None or (len(srcs) == 1 and len(peers) == 1)):
                    r = candidates[0]
                    changed |= _assign(mapping, reverse, s["home"], r.home_team_name, evidence, f"schedule_anchor:{date}:home")
                    changed |= _assign(mapping, reverse, s["away"], r.away_team_name, evidence, f"schedule_anchor:{date}:away")

        # For unresolved names, intersect role-preserving candidates over every observed completed date.
        for source in sorted(source_names - set(mapping)):
            candidate_sets: list[set[str]] = []
            for date, srcs in src_by_date.items():
                peers = by_date.get(date, [])
                for s in srcs:
                    if s["home"] == source:
                        candidate_sets.append({r.home_team_name for r in peers})
                    if s["away"] == source:
                        candidate_sets.append({r.away_team_name for r in peers})
            if candidate_sets:
                inter = set.intersection(*candidate_sets)
                inter = {x for x in inter if x not in reverse or reverse[x] == source}
                if len(inter) == 1:
                    changed |= _assign(mapping, reverse, source, next(iter(inter)), evidence, "multi_date_role_candidate_intersection")

    unresolved = sorted(source_names - set(mapping))
    audit = {
        "schema_version": SCHEMA,
        "competition_id": comp,
        "method": "exact-normalized seed + same-date role-preserving schedule anchors/intersections",
        "label_free": True,
        "result_or_xg_used_for_identity": False,
        "mapped_n": len(mapping),
        "source_team_n": len(source_names),
        "unresolved": unresolved,
        "evidence": evidence,
        "mapping_sha256": live.sha_bytes(live.canon(sorted(mapping.items()))),
    }
    return mapping, audit


def _acquire_xg(v1_rows, lower: datetime, ceiling: datetime, state: Any):
    result_xg: dict[str, dict[str, Any]] = {}
    freezes: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    identity_audits: list[dict[str, Any]] = []
    existing_pending = set(getattr(state, "pending", {}) or {})
    existing_seen = set(getattr(state, "seen", set()) or set())

    for comp, league in live.UNDERSTAT.items():
        comp_v1 = [r for r in v1_rows if r.competition_id == comp]
        v1_index = {
            (r.kickoff.date().isoformat(), r.home_team_name, r.away_team_name): r
            for r in comp_v1
        }
        for start in live._cross_year_starts(lower, ceiling):
            try:
                obj, source_sha, url = live._understat_payload(comp, league, start)
            except Exception as exc:
                failures[f"{comp}:{start}"] = f"{type(exc).__name__}: {exc}"
                continue

            rows = obj.get("dates") or []
            parsed: list[dict[str, Any]] = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                raw_dt = str(item.get("datetime") or "").strip()
                hraw = str((item.get("h") or {}).get("title") or "").strip()
                araw = str((item.get("a") or {}).get("title") or "").strip()
                if not raw_dt or not hraw or not araw:
                    continue
                try:
                    actual = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                    if actual.tzinfo is None:
                        actual = actual.replace(tzinfo=timezone.utc)
                    actual = actual.astimezone(timezone.utc)
                except ValueError:
                    continue
                parsed.append({
                    "date": actual.date().isoformat(), "actual": actual,
                    "home": hraw, "away": araw, "item": item,
                })

            # Learn names using only the portion for which the V1 source already supplies schedule identity.
            relevant = [r for r in parsed if r["actual"] < ceiling]
            mapping, identity_audit = _learn_identity(comp, relevant, comp_v1)
            identity_audit.update({"season_start": start, "url": url, "source_sha256": source_sha})
            identity_audits.append(identity_audit)

            joined = 0; scheduled = 0; unjoined_results: list[dict[str, Any]] = []
            for row in parsed:
                actual = row["actual"]
                if actual >= ceiling:
                    continue
                h = mapping.get(row["home"]); a = mapping.get(row["away"])
                if h is None or a is None:
                    if bool(row["item"].get("isResult")):
                        unjoined_results.append({"date": row["date"], "home": row["home"], "away": row["away"], "reason": "unresolved_schedule_identity"})
                    continue
                season = live._season_label_cross(start)
                formal_ko = actual.replace(hour=0, minute=0, second=0, microsecond=0)
                vr = v1_index.get((formal_ko.date().isoformat(), h, a))
                if vr is not None:
                    fid = vr.fixture_id; h = vr.home_team_name; a = vr.away_team_name; season = vr.season; formal_ko = vr.kickoff
                else:
                    fid = rt._fixture_id(comp, season, formal_ko, h, a)

                if fid not in existing_pending and fid not in existing_seen and lower <= formal_ko < ceiling:
                    freezes.append({
                        "event_type": "FIXTURE_FREEZE", "event_at": formal_ko.isoformat(), "fixture_id": fid,
                        "competition_id": comp, "season": season, "home_team_id": rt._global_team_id(h),
                        "away_team_id": rt._global_team_id(a), "home_team_name": h, "away_team_name": a,
                        "kickoff": formal_ko.isoformat(), "source": url, "source_content_sha256": source_sha,
                        "enters_v1": True, "enters_xg": True,
                    })
                    scheduled += 1

                item = row["item"]
                if not bool(item.get("isResult")):
                    continue
                xg = item.get("xG") or {}; goals = item.get("goals") or {}
                try:
                    hx, ax = float(xg.get("h")), float(xg.get("a"))
                    hg, ag = int(float(goals.get("h"))), int(float(goals.get("a")))
                except Exception:
                    unjoined_results.append({"date": row["date"], "home": row["home"], "away": row["away"], "reason": "completed_understat_row_missing_result_or_xg"})
                    continue
                if vr is None:
                    unjoined_results.append({"date": row["date"], "home": row["home"], "away": row["away"], "reason": "no_exact_formal_schedule_identity_after_alias_resolution"})
                    continue
                if (hg, ag) != (vr.home_goals, vr.away_goals):
                    raise live.AcquisitionError(
                        "XG/V1 result conflict",
                        {"competition_id": comp, "formal_fixture_id": vr.fixture_id, "formal_kickoff": vr.kickoff.isoformat(),
                         "home": h, "away": a, "v1_result": [vr.home_goals, vr.away_goals], "xg_source_result": [hg, ag],
                         "xg_source_actual_kickoff": actual.isoformat(), "source": url},
                    )
                result_xg[vr.fixture_id] = {
                    "home_xg": hx, "away_xg": ax, "source": url, "source_sha256": source_sha,
                    "source_kickoff": actual.isoformat(), "release_at": (actual + timedelta(hours=3)).isoformat(),
                }
                joined += 1

            sources.append({
                "competition_id": comp, "season_start": start, "url": url, "sha256": source_sha,
                "dates_rows": len(rows), "result_joined": joined, "freeze_candidates": scheduled,
                "unjoined_result_n": len(unjoined_results), "unjoined_results": unjoined_results[:50],
                "identity_mapping_sha256": identity_audit["mapping_sha256"],
            })

    if failures:
        raise live.AcquisitionError(
            "FORMAL_INPUT_DATA_INCOMPLETE: Understat acquisition failed",
            {"stage": "XG_ACQUISITION", "failures": failures, "sources": sources, "identity_audits": identity_audits},
        )
    return result_xg, freezes, {
        "status": "FETCH_COMPLETE", "sources": sources, "joined_results": len(result_xg),
        "freeze_events": len(freezes), "identity_audits": identity_audits,
        "identity_policy": "label-free schedule identity only; no result/xG used for alias learning",
    }


def install() -> dict[str, Any]:
    sem._acquire_xg = _acquire_xg
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "identity_policy": "exact normalized seed plus auditable same-date home/away schedule identity; no result/xG matching",
        "time_or_result_substitution": False,
        "model_parameters_or_weights_changed": False,
    }
