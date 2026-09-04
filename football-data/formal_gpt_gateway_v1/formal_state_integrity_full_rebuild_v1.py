#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-formal-state-integrity-full-rebuild-v1"
LINKED_SEASON = "2025/26"
LINKED_COMPETITIONS = (
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
)
LINKED_DIR = "football-data/evidence/xg/understat_2025_26_linked"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise rt.RuntimeGateError(f"linked xg unreadable: {path}") from exc
    for line in lines:
        if not line.strip():
            continue
        obj = json.loads(line)
        if type(obj) is not dict:
            raise rt.RuntimeGateError(f"linked xg object required: {path}")
        rows.append(obj)
    return rows


def _key(comp: str, date: str, home: str, away: str) -> tuple[str, str, str, str]:
    return comp, date, rt._normalize_team(home), rt._normalize_team(away)


def _history_index(history) -> tuple[dict[tuple[str, str, str, str], Any], dict[str, int]]:
    index: dict[tuple[str, str, str, str], Any] = {}
    expected: dict[str, int] = {}
    for f in history:
        if f.season != LINKED_SEASON or f.competition_id not in LINKED_COMPETITIONS:
            continue
        k = _key(f.competition_id, f.kickoff.date().isoformat(), f.home_team_name, f.away_team_name)
        if k in index:
            raise rt.RuntimeGateError(f"duplicate formal linked-history key: {k}")
        index[k] = f
        expected[f.competition_id] = expected.get(f.competition_id, 0) + 1
    return index, expected


def augment_available_linked_xg(repo_root: Path, history, base_labels: dict[str, Any], target_cutoff) -> tuple[dict[str, Any], dict[str, Any]]:
    cutoff = target_cutoff
    index, expected = _history_index(history)
    out = dict(base_labels)
    files: dict[str, Any] = {}
    team_counts: dict[str, dict[str, int]] = {}
    added = 0

    for comp in LINKED_COMPETITIONS:
        path = repo_root / LINKED_DIR / f"{comp}.jsonl"
        if not path.is_file():
            raise rt.RuntimeGateError(f"missing frozen linked xg coverage: {path}")
        rows = _load_jsonl(path)
        source_sha = rt._sha_file(path)
        observed = sorted({str(r.get("source_observed_at_utc") or "") for r in rows if str(r.get("source_observed_at_utc") or "")})
        if not observed:
            raise rt.RuntimeGateError(f"linked xg observed_at missing: {comp}")
        observed_dt = [rt._parse_dt(x, "linked xg observed_at") for x in observed]
        eligible = max(observed_dt) <= cutoff
        joined = 0
        unmatched = []
        comp_counts: dict[str, int] = {}
        if eligible:
            for row in rows:
                if str(row.get("competition_id") or "") != comp or str(row.get("season") or "") != LINKED_SEASON:
                    raise rt.RuntimeGateError(f"linked xg scope mismatch: {comp}")
                if row.get("target_match_xg_allowed_as_predictor") is not False:
                    raise rt.RuntimeGateError("linked xg target predictor prohibition missing")
                if str(row.get("source_role") or "") != "RETROSPECTIVE_MATCH_LEVEL_XG":
                    raise rt.RuntimeGateError("linked xg source role mismatch")
                k = _key(
                    comp,
                    str(row.get("official_date") or ""),
                    str(row.get("official_home_team") or ""),
                    str(row.get("official_away_team") or ""),
                )
                f = index.get(k)
                if f is None:
                    unmatched.append(k)
                    continue
                if f.fixture_id in out:
                    raise rt.RuntimeGateError(f"linked xg overlaps existing frozen xg: {f.fixture_id}")
                hg, ag = int(row["home_goals"]), int(row["away_goals"])
                if (hg, ag) != (f.home_goals, f.away_goals):
                    raise rt.RuntimeGateError(f"linked xg frozen result identity conflict: {f.fixture_id}")
                release = f.kickoff + timedelta(hours=3)
                if release >= rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff"):
                    raise rt.RuntimeGateError(f"linked xg row not strictly inside historical base: {f.fixture_id}")
                label = rt.hxg.ReleasedLabel(hg, ag, float(row["home_xg"]), float(row["away_xg"]), release)
                out[f.fixture_id] = rt.XGLabel(
                    label,
                    f"linked-retro:{row.get('understat_match_id')}",
                    source_sha,
                    release.isoformat(),
                )
                joined += 1
                added += 1
                comp_counts[f.home_team_id] = comp_counts.get(f.home_team_id, 0) + 1
                comp_counts[f.away_team_id] = comp_counts.get(f.away_team_id, 0) + 1
            if unmatched:
                raise rt.RuntimeGateError(f"linked xg unmatched formal fixtures: {comp} n={len(unmatched)} sample={unmatched[:3]}")
            if joined != expected.get(comp, 0) or len(rows) != expected.get(comp, 0):
                raise rt.RuntimeGateError(
                    f"linked xg coverage incomplete: {comp} linked={len(rows)} joined={joined} formal={expected.get(comp,0)}"
                )
        team_counts[comp] = comp_counts
        files[comp] = {
            "path": str(path.relative_to(repo_root)),
            "sha256": source_sha,
            "linked_rows": len(rows),
            "formal_rows": expected.get(comp, 0),
            "joined_rows": joined,
            "eligible_for_target": eligible,
            "source_observed_at_utc": observed,
            "historical_pit_claim": False,
            "use_role": "LAGGED_HISTORY_ONLY_AFTER_SOURCE_OBSERVATION",
        }

    audit = {
        "schema_version": "football3-cross-season-xg-coverage-v1",
        "status": "PASS",
        "season": LINKED_SEASON,
        "target_cutoff": cutoff.isoformat(),
        "files": files,
        "team_xg_match_counts": team_counts,
        "preexisting_xg_labels": len(base_labels),
        "added_xg_labels": added,
        "augmented_xg_labels": len(out),
        "target_match_xg_used": False,
        "post_target_information_used": False,
        "model_parameters_or_weights_changed": False,
    }
    return out, audit


def build_integrity_base(bundle: Path, repo_root: Path, understat_db: Path, confirmation_dir: Path, target_cutoff) -> dict[str, Any]:
    target_cutoff = target_cutoff.astimezone(timezone.utc)
    history, v1_source = rt.load_frozen_v1_history(repo_root)
    base_labels, base_xg_source = rt.load_xg_labels(history, understat_db, confirmation_dir)
    labels, coverage = augment_available_linked_xg(repo_root, history, base_labels, target_cutoff)

    base_cutoff = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff")
    state, replay = rt.replay_history_state(history, labels, base_cutoff)
    baseline_state, _ = rt.replay_history_state(history, base_labels, base_cutoff)
    if rt.serialize_v1_state(state.base) != rt.serialize_v1_state(baseline_state.base):
        raise rt.RuntimeGateError("integrity full rebuild changed Frozen V1 state")
    if len(state.base.seen_fixtures) != rt.EXPECTED_V1_N:
        raise rt.RuntimeGateError("integrity full rebuild V1 cardinality mismatch")

    source = {
        "schema_version": SCHEMA,
        "v1": v1_source,
        "xg_original_frozen": base_xg_source,
        "xg_2025_26_integrity_coverage": coverage,
        "replay": replay,
        "source_scope": "FROZEN_FORMAL_HISTORY_PLUS_TARGET_ELIGIBLE_LINKED_XG",
        "target_eligibility_cutoff": target_cutoff.isoformat(),
        "historical_pit_claim_for_linked_2025_26": False,
    }
    identity = rt._production_identity()
    identity["state_integrity_rebuild"] = {
        "cross_season_xg_coverage_checked": True,
        "identity_for_linked_rows": "persisted linked fixture identity only",
        "target_identity_selection_uses_result_or_xg": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    manifest = rt.seal_bundle(
        state,
        bundle,
        source,
        identity,
        rt.BASE_HISTORY_CUTOFF,
        "FULL_REBUILD_STATE_INTEGRITY_GUARD",
    )
    loaded = rt.validate_bundle(bundle)
    return {
        "status": "PASS",
        "schema_version": SCHEMA,
        "manifest": manifest,
        "loaded": loaded,
        "coverage": coverage,
        "target_cutoff": target_cutoff.isoformat(),
        "v1_seen_n": len(loaded["state"].base.seen_fixtures),
        "xg_seen_n": len(loaded["state"].seen),
        "xg_pending_n": len(loaded["state"].pending),
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
