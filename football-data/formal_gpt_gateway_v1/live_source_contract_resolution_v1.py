#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import live_delta_acquisition_v1 as live
import live_delta_semantics_v2 as sem
import live_xg_identity_patch_v1 as xid
import runtime as rt

SCHEMA = "football3-live-source-contract-resolution-v1"

# Identity-only mappings already established by repository evidence:
# - football-data/manifests/recent_xg_identity_bridge_v512_status.json
# - football-data/config/current_season_team_identity_v5524.json
# No score or xG value is used to select these identities.
TRUSTED_ALIAS_SEEDS: dict[str, dict[str, str]] = {
    "GER_Bundesliga": {
        "RasenBallsport Leipzig": "RB Leipzig",
        "Borussia M.Gladbach": "M'gladbach",
        "Borussia Dortmund": "Dortmund",
        "Hamburger SV": "Hamburg",
    },
    "FRA_Ligue1": {
        "Paris Saint Germain": "Paris SG",
        "Rennes": "Rennes",
    },
}

# Official Ligue 1 changed the venue/home-away ordering for the 2026-08-23
# Rennes v PSG fixture. Understat retained the original source-side role order.
# This is therefore a source-role contract, not a fuzzy identity inference.
ROLE_REVERSAL = {
    "competition_id": "FRA_Ligue1",
    "date": "2026-08-23",
    "source_home": "Paris Saint Germain",
    "source_away": "Rennes",
    "formal_home": "Rennes",
    "formal_away": "Paris SG",
    "official_evidence": "https://ligue1.com/fr/articles/l1_article_5699-j1-psg-rennes-inverse-l1-2627",
    "policy": "swap source home/away roles, goals and xG into the official formal fixture orientation; do not alter event time",
}

_ORIGINAL_LEARN = xid._learn_identity
_ORIGINAL_ACQUIRE = xid._acquire_xg


def _learn_identity(comp: str, source_rows: list[dict[str, Any]], v1_rows):
    mapping, audit = _ORIGINAL_LEARN(comp, source_rows, v1_rows)
    seeds = TRUSTED_ALIAS_SEEDS.get(comp, {})
    if not seeds:
        return mapping, audit

    source_names = {str(r.get("home") or "") for r in source_rows} | {str(r.get("away") or "") for r in source_rows}
    formal_names = {
        n
        for r in v1_rows
        if r.competition_id == comp
        for n in (r.home_team_name, r.away_team_name)
    }
    reverse = {target: source for source, target in mapping.items()}
    evidence = audit.setdefault("evidence", [])
    applied: list[dict[str, str]] = []
    for source, target in sorted(seeds.items()):
        if source not in source_names or target not in formal_names:
            continue
        before = mapping.get(source)
        xid._assign(
            mapping, reverse, source, target, evidence,
            "repository_trusted_alias_seed:recent_xg_identity_bridge_v512+current_season_team_identity_v5524",
        )
        if before is None:
            applied.append({"source_team": source, "formal_team": target})

    audit["mapped_n"] = len(mapping)
    audit["unresolved"] = sorted(source_names - set(mapping))
    audit["mapping_sha256"] = live.sha_bytes(live.canon(sorted(mapping.items())))
    audit["trusted_alias_seed_applied"] = applied
    audit["trusted_alias_seed_policy"] = "repository identity evidence only; no result/xG matching"
    return mapping, audit


def _dt(value: str) -> datetime:
    x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def _apply_role_reversal(v1_rows, lower: datetime, ceiling: datetime, state: Any,
                         result_xg: dict[str, dict[str, Any]], freezes: list[dict[str, Any]], report: dict[str, Any]):
    c = ROLE_REVERSAL
    formal = [
        r for r in v1_rows
        if r.competition_id == c["competition_id"]
        and r.kickoff.date().isoformat() == c["date"]
        and r.home_team_name == c["formal_home"]
        and r.away_team_name == c["formal_away"]
    ]
    if len(formal) != 1:
        raise live.AcquisitionError(
            "FORMAL_SOURCE_ROLE_CONTRACT_TARGET_NOT_UNIQUE",
            {"contract": c, "formal_candidates": len(formal)},
        )
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
        raise live.AcquisitionError(
            "FORMAL_SOURCE_ROLE_CONTRACT_SOURCE_NOT_UNIQUE",
            {"contract": c, "source_candidates": len(candidates), "source": url},
        )
    item, actual = candidates[0]
    if actual >= ceiling or not bool(item.get("isResult")):
        raise live.AcquisitionError(
            "FORMAL_SOURCE_ROLE_CONTRACT_RESULT_NOT_AVAILABLE",
            {"contract": c, "source_actual_kickoff": actual.isoformat(), "ceiling": ceiling.isoformat()},
        )

    xg = item.get("xG") or {}; goals = item.get("goals") or {}
    try:
        source_hx, source_ax = float(xg.get("h")), float(xg.get("a"))
        source_hg, source_ag = int(float(goals.get("h"))), int(float(goals.get("a")))
    except Exception as exc:
        raise live.AcquisitionError("FORMAL_SOURCE_ROLE_CONTRACT_PAYLOAD_INVALID", {"contract": c, "source": url}) from exc

    # Source roles are explicitly reversed relative to the official formal fixture.
    formal_hg, formal_ag = source_ag, source_hg
    formal_hx, formal_ax = source_ax, source_hx
    if (formal_hg, formal_ag) != (vr.home_goals, vr.away_goals):
        raise live.AcquisitionError(
            "FORMAL_SOURCE_ROLE_CONTRACT_RESULT_CONFLICT",
            {"contract": c, "formal_fixture_id": vr.fixture_id,
             "formal_result": [vr.home_goals, vr.away_goals],
             "role_swapped_source_result": [formal_hg, formal_ag]},
        )

    result_xg[vr.fixture_id] = {
        "home_xg": formal_hx,
        "away_xg": formal_ax,
        "source": url,
        "source_sha256": source_sha,
        "source_kickoff": actual.isoformat(),
        "release_at": (actual + timedelta(hours=3)).isoformat(),
        "source_role_contract": SCHEMA,
        "source_roles_reversed": True,
    }

    # Remove only the synthetic source-orientation freeze that the generic resolver could create.
    freezes[:] = [
        e for e in freezes
        if not (
            e.get("competition_id") == c["competition_id"]
            and rt._parse_dt(str(e.get("kickoff")), "kickoff").date().isoformat() == c["date"]
            and e.get("home_team_name") == c["formal_away"]
            and e.get("away_team_name") == c["formal_home"]
        )
    ]
    existing_pending = set(getattr(state, "pending", {}) or {})
    existing_seen = set(getattr(state, "seen", set()) or set())
    if vr.fixture_id not in existing_pending and vr.fixture_id not in existing_seen and lower <= vr.kickoff < ceiling:
        if not any(e.get("fixture_id") == vr.fixture_id for e in freezes):
            freezes.append({
                "event_type": "FIXTURE_FREEZE", "event_at": vr.kickoff.isoformat(), "fixture_id": vr.fixture_id,
                "competition_id": vr.competition_id, "season": vr.season,
                "home_team_id": vr.home_team_id, "away_team_id": vr.away_team_id,
                "home_team_name": vr.home_team_name, "away_team_name": vr.away_team_name,
                "kickoff": vr.kickoff.isoformat(), "source": url, "source_content_sha256": source_sha,
                "enters_v1": True, "enters_xg": True,
            })

    # Make the source audit describe the resolved row rather than leaving it as an unexplained unjoined result.
    for s in report.get("sources", []):
        if s.get("competition_id") == c["competition_id"] and s.get("season_start") == 2026:
            old = list(s.get("unjoined_results") or [])
            kept = [r for r in old if not (
                r.get("date") == c["date"] and r.get("home") == c["source_home"] and r.get("away") == c["source_away"]
            )]
            if len(kept) != len(old):
                s["result_joined"] = int(s.get("result_joined", 0)) + 1
                s["unjoined_results"] = kept
                s["unjoined_result_n"] = max(0, int(s.get("unjoined_result_n", len(old))) - 1)
            s.setdefault("resolved_source_role_contracts", []).append({
                "schema_version": SCHEMA,
                "formal_fixture_id": vr.fixture_id,
                "source_home": c["source_home"], "source_away": c["source_away"],
                "formal_home": c["formal_home"], "formal_away": c["formal_away"],
                "official_evidence": c["official_evidence"],
                "source_sha256": source_sha,
                "source_actual_kickoff": actual.isoformat(),
                "roles_swapped": True,
            })

    report.setdefault("source_role_contracts", []).append({
        "schema_version": SCHEMA,
        "formal_fixture_id": vr.fixture_id,
        **c,
        "source_sha256": source_sha,
        "source_actual_kickoff": actual.isoformat(),
        "role_swap_applied": True,
        "result_or_xg_used_to_choose_contract": False,
    })


def _acquire_xg(v1_rows, lower: datetime, ceiling: datetime, state: Any):
    result_xg, freezes, report = _ORIGINAL_ACQUIRE(v1_rows, lower, ceiling, state)
    _apply_role_reversal(v1_rows, lower, ceiling, state, result_xg, freezes, report)
    freezes.sort(key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"),
        rt._parse_dt(str(e["kickoff"]), "kickoff"),
        str(e["competition_id"]), str(e["fixture_id"]),
    ))
    report["joined_results"] = len(result_xg)
    report["freeze_events"] = len(freezes)
    report["source_contract_resolution_schema"] = SCHEMA
    report["source_contract_resolution_sha256"] = live.sha_bytes(live.canon({
        "trusted_alias_seeds": TRUSTED_ALIAS_SEEDS,
        "role_reversal": ROLE_REVERSAL,
    }))
    return result_xg, freezes, report


def install() -> dict[str, Any]:
    xid._learn_identity = _learn_identity
    sem._acquire_xg = _acquire_xg
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "trusted_alias_seed_evidence": [
            "football-data/manifests/recent_xg_identity_bridge_v512_status.json",
            "football-data/config/current_season_team_identity_v5524.json",
        ],
        "official_role_reversal_evidence": ROLE_REVERSAL["official_evidence"],
        "identity_or_role_resolution_uses_result_or_xg_for_selection": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
