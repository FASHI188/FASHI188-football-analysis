#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import runtime as rt
import formal_result_adjudication_v1 as contract
import formal_frozen_xg_identity_adjudication_v1 as v1

SCHEMA = "football3-formal-frozen-xg-identity-adjudication-v2"
MAX_DATE_BRIDGE_DAYS = 14
CONSERVATIVE_FORMAL_DAY_RELEASE_HOURS = 27
_INSTALLED = False


def _source_index_with_date_bridge(
    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture],
    sources: list[dict[str, Any]],
    mappings: dict[str, dict[str, str]],
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    pair_index: dict[tuple[str, str, str], list[tuple[tuple[str, str, str, str], rt.HistoryFixture]]] = {}
    for key, fixture in formal_index.items():
        pair = (key[0], key[2], key[3])
        pair_index.setdefault(pair, []).append((key, fixture))

    source_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    alignments: list[dict[str, Any]] = []
    for raw in sources:
        s = dict(raw)
        comp = str(s["competition_id"])
        mapping = mappings[comp]
        h = mapping.get(str(s["home"])); a = mapping.get(str(s["away"]))
        if h is None or a is None:
            raise rt.RuntimeGateError(f"frozen XG source unmapped identity: {comp} {s['home']} {s['away']}")
        hn = rt._normalize_team(h); an = rt._normalize_team(a)
        exact_key = (comp, str(s["date"]), hn, an)
        target_key = exact_key
        fixture = formal_index.get(exact_key)
        release_adjusted = False
        original_release = s["release_at"]

        if fixture is None:
            source_day = date.fromisoformat(str(s["date"]))
            candidates = []
            for candidate_key, candidate in pair_index.get((comp, hn, an), []):
                delta_days = (candidate.kickoff.date() - source_day).days
                if 0 < abs(delta_days) <= MAX_DATE_BRIDGE_DAYS:
                    candidates.append((candidate_key, candidate, delta_days))
            if len(candidates) != 1:
                raise rt.RuntimeGateError(
                    f"frozen XG date identity unresolved: {comp} {h} {a} source_date={s['date']} candidates={[(x[0][1], x[2]) for x in candidates]}"
                )
            target_key, fixture, delta_days = candidates[0]
            floor = fixture.kickoff + timedelta(hours=CONSERVATIVE_FORMAL_DAY_RELEASE_HOURS)
            if s["release_at"] < floor:
                s["release_at"] = floor
                release_adjusted = True
            alignments.append({
                "competition_id": comp,
                "fixture_id": fixture.fixture_id,
                "season": fixture.season,
                "home_team_name": h,
                "away_team_name": a,
                "source_family": s["family"],
                "source_fixture_id": s["source_fixture_id"],
                "source_date": str(raw["date"]),
                "formal_date": fixture.kickoff.date().isoformat(),
                "signed_formal_minus_source_days": delta_days,
                "match_rule": "unique mapped home/away schedule identity within bounded date window",
                "result_or_xg_used_for_alignment": False,
                "original_release_at": original_release.isoformat(),
                "effective_release_at": s["release_at"].isoformat(),
                "release_adjusted_to_conservative_formal_day_floor": release_adjusted,
            })

        if target_key in source_index:
            raise rt.RuntimeGateError(f"duplicate mapped frozen XG identity after date bridge: {target_key}")
        source_index[target_key] = s

    return source_index, alignments


def _load_xg_labels(history: list[rt.HistoryFixture], understat_db, confirmation_dir):
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

    sources, source_receipt = v1._read_sources(understat_db, confirmation_dir)
    identity_audits: list[dict[str, Any]] = []
    mappings: dict[str, dict[str, str]] = {}
    for comp in sorted(rt.BIG5.values()):
        src = [r for r in sources if r["competition_id"] == comp]
        formal = [r for r in formal_rows if r.competition_id == comp]
        mapping, audit = v1._learn_identity(comp, src, formal)
        if audit["unresolved"]:
            raise rt.RuntimeGateError(f"frozen XG identity unresolved {comp}: {audit['unresolved']}")
        mappings[comp] = mapping
        identity_audits.append(audit)

    source_index, date_alignments = _source_index_with_date_bridge(formal_index, sources, mappings)
    missing = [key for key in formal_index if key not in source_index]
    extra = [key for key in source_index if key not in formal_index]
    if missing or extra or len(source_index) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(
            f"mapped XG identity join incomplete missing={len(missing)} extra={len(extra)} joined={len(source_index)}"
        )

    labels: dict[str, rt.XGLabel] = {}
    applied: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for key, fixture in formal_index.items():
        s = source_index[key]
        if (s["home_goals"], s["away_goals"]) != (fixture.home_goals, fixture.away_goals):
            entry = adjudications.get(fixture.fixture_id)
            if entry is None:
                raise rt.RuntimeGateError(f"XG/formal result conflict: {fixture.fixture_id}")
            applied.append(v1._validate_adjudicated_source(entry, fixture, s, confirmation_dir))
            consumed.add(fixture.fixture_id)
        elif fixture.fixture_id in adjudications:
            raise rt.RuntimeGateError(f"stale adjudication no longer matches conflict: {fixture.fixture_id}")
        label = rt.hxg.ReleasedLabel(
            int(s["home_goals"]), int(s["away_goals"]), float(s["home_xg"]), float(s["away_xg"]), s["release_at"]
        )
        labels[fixture.fixture_id] = rt.XGLabel(
            label, str(s["source_fixture_id"]), str(s["source_sha256"]), s["release_at"].isoformat()
        )

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
            "method": "label-free team schedule identity plus unique bounded date alignment",
            "result_or_xg_used_for_identity": False,
            "result_or_xg_used_for_date_alignment": False,
            "audits": identity_audits,
            "all_source_teams_mapped": True,
            "date_alignment_max_abs_days": MAX_DATE_BRIDGE_DAYS,
            "date_alignment_n": len(date_alignments),
            "date_alignments": date_alignments,
            "pit_release_policy": (
                "when a source date is bridged, release_at is never earlier than the original frozen source release; "
                "if needed it is conservatively floored at formal calendar date + 27h"
            ),
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
    contract._manifest()
    # Reassert the governed runtime bindings on every call.  Some legacy source
    # adapters are still installed during entry bootstrap and replace
    # ``rt.load_xg_labels`` after this module's first installation.  Treating an
    # already-installed call as a no-op silently restored the pre-adjudication
    # loader in the real gateway while isolated acceptance tests kept the right
    # loader.  Idempotence here means deterministic rebinding, not skipping it.
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
        "identity_policy": "exact-normalized + same-date role-preserving team mapping + unique <=14d schedule date bridge; label-free and result-free",
        "date_bridge_max_abs_days": MAX_DATE_BRIDGE_DAYS,
        "date_bridge_pit_floor_hours_from_formal_day": CONSERVATIVE_FORMAL_DAY_RELEASE_HOURS,
        "result_semantics": "SPLIT_AUTHORITATIVE_SETTLEMENT_V1_FROM_ON_FIELD_XG",
        "sample_deleted": False,
        "source_score_rewritten": False,
        "conflict_gate_relaxed_without_authority": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
