#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import live_gateway_patch_v1 as live_gateway
import runtime as rt
from historical_xg_challenger_v1 import historical_xg_challenger as hxg

SCHEMA = "football3-target-identity-replay-fix-v1"
IDENTITY_REGISTRY = "football-data/config/current_season_team_identity_v5524.json"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _registry(repo_root: Path) -> dict[str, Any]:
    path = repo_root / IDENTITY_REGISTRY
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError("current-season identity registry unreadable") from exc
    if type(obj) is not dict or obj.get("season") != "2026/27":
        raise rt.RuntimeGateError("current-season identity registry mismatch")
    return obj


def _state_has_team(state: Any, comp: str, team_id: str) -> bool:
    return ((comp, team_id) in state.base.teams_local) or (team_id in state.base.teams_global)


def _resolve_team(repo_root: Path, state: Any, comp: str, season: str, requested: str) -> dict[str, Any]:
    requested = str(requested or "").strip()
    if not requested:
        raise rt.RuntimeGateError("empty requested team identity")
    if season != "2026/27":
        team_id = rt._global_team_id(requested)
        return {
            "requested_name": requested,
            "registry_match": False,
            "strength_team_id": team_id,
            "strength_alias": requested,
            "state_evidence": _state_has_team(state, comp, team_id),
        }

    registry = _registry(repo_root)
    comp_obj = (registry.get("competitions") or {}).get(comp)
    if type(comp_obj) is not dict or type(comp_obj.get("teams")) is not list:
        raise rt.RuntimeGateError(f"current-season competition identity missing: {comp}")

    token = rt._normalize_team(requested)
    matches = []
    for row in comp_obj["teams"]:
        if type(row) is not dict:
            continue
        names = [row.get("canonical_name"), row.get("official_name"), *(row.get("aliases") or [])]
        names = [str(x).strip() for x in names if str(x or "").strip()]
        if any(rt._normalize_team(x) == token for x in names):
            matches.append((row, names))
    if len(matches) != 1:
        raise rt.RuntimeGateError(f"current-season team identity not unique: {comp} {requested}")

    row, names = matches[0]
    candidate_names = []
    for x in [*(row.get("aliases") or []), row.get("canonical_name"), row.get("official_name")]:
        x = str(x or "").strip()
        if x and x not in candidate_names:
            candidate_names.append(x)

    state_hits: dict[str, list[str]] = {}
    for alias in candidate_names:
        team_id = rt._global_team_id(alias)
        if _state_has_team(state, comp, team_id):
            state_hits.setdefault(team_id, []).append(alias)

    if len(state_hits) > 1:
        raise rt.RuntimeGateError(f"current-season aliases map to multiple historical strength identities: {comp} {requested}")
    if len(state_hits) == 1:
        team_id, aliases = next(iter(state_hits.items()))
        strength_alias = aliases[0]
        state_evidence = True
        resolution = "UNIQUE_EXISTING_STRENGTH_ID_FROM_EXPLICIT_CURRENT_SEASON_ALIASES"
    else:
        canonical = str(row.get("canonical_name") or requested).strip()
        team_id = rt._global_team_id(canonical)
        strength_alias = canonical
        state_evidence = False
        resolution = "REGISTERED_CURRENT_SEASON_CLUB_WITHOUT_HISTORICAL_STRENGTH_STATE"

    return {
        "requested_name": requested,
        "registry_match": True,
        "registry_canonical_name": str(row.get("canonical_name") or ""),
        "registry_official_name": str(row.get("official_name") or ""),
        "registry_aliases": list(row.get("aliases") or []),
        "strength_team_id": team_id,
        "strength_alias": strength_alias,
        "state_evidence": state_evidence,
        "resolution": resolution,
    }


def _resolved_fixture(repo_root: Path, state: Any, comp: str, season: str, home: str, away: str, kickoff) -> tuple[dict[str, Any], dict[str, Any]]:
    home_r = _resolve_team(repo_root, state, comp, season, home)
    away_r = _resolve_team(repo_root, state, comp, season, away)
    if home_r["strength_team_id"] == away_r["strength_team_id"]:
        raise rt.RuntimeGateError("resolved home/away strength identity collision")
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": home_r["strength_team_id"],
        "away_team_id": away_r["strength_team_id"],
        "home_team_name": home,
        "away_team_name": away,
    }
    audit = {
        "schema_version": SCHEMA,
        "competition_id": comp,
        "season": season,
        "fixture_id": fixture["fixture_id"],
        "home": home_r,
        "away": away_r,
        "identity_only": True,
        "result_or_xg_used_for_identity": False,
        "model_parameters_or_weights_changed": False,
    }
    return fixture, audit


def _source_set_sha(loaded: dict[str, Any]) -> str:
    source = loaded.get("source") or {}
    stack = [source]
    while stack:
        item = stack.pop(0)
        if type(item) is not dict:
            continue
        live_acq = item.get("live_acquisition")
        if type(live_acq) is dict and live_acq.get("source_set_sha256"):
            return str(live_acq["source_set_sha256"])
        for key in ("prior_state_source",):
            if type(item.get(key)) is dict:
                stack.append(item[key])
    return rt._sha_bytes(rt._canon_bytes(source))


def _sealed_replay_input(fixture: dict[str, Any], cutoff, loaded: dict[str, Any]) -> dict[str, Any]:
    empty: list[dict[str, Any]] = []
    coverage = {
        "schema_version": rt.DELTA_SCHEMA,
        "status": "COMPLETE",
        "verification": "VERIFIED_COMPLETE",
        "v1_status": "COMPLETE",
        "xg_status": "COMPLETE",
        "from": cutoff.isoformat(),
        "to": cutoff.isoformat(),
        "records_sha256": rt._sha_bytes(rt._canon_bytes(empty)),
        "source_set_sha256": _source_set_sha(loaded),
        "acquisition_schema": "SEALED_EXACT_CUTOFF_REPLAY",
        "acquisition_observed_at": cutoff.isoformat(),
        "state_preapplied_and_sealed": True,
    }
    return {"schema_version": rt.INPUT_SCHEMA, "fixture": fixture, "cutoff": cutoff.isoformat(), "delta_coverage": coverage, "model_delta": empty}


def _xg_trigger_audit(state: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    clone = rt.deserialize_state(rt.serialize_v1_state(state.base), rt.serialize_xg_state(state))
    f = hxg.FixtureRow(
        fixture["fixture_id"], fixture["competition_id"], fixture["season"],
        rt._parse_dt(fixture["kickoff"], "fixture kickoff"), fixture["home_team_id"], fixture["away_team_id"],
        fixture["home_team_name"], fixture["away_team_name"],
    )
    xg_predictions, _ = clone.predict_batch([f], include_matrix=False, lightweight=True)
    if len(xg_predictions) != 1:
        raise rt.RuntimeGateError("XG trigger diagnostic cardinality mismatch")
    dynamic = xg_predictions[0].get("dynamic")
    if type(dynamic) is not dict:
        raise rt.RuntimeGateError("XG trigger diagnostic metadata missing")
    return {
        "schema_version": "football3-xg-trigger-audit-v1",
        "fixture_id": fixture["fixture_id"],
        "dynamic": dynamic,
        "frozen_min_effective_evidence": 3.0,
        "diagnostic_state_clone_only": True,
        "model_parameters_or_weights_changed": False,
    }


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        m = req.get("match")
        if type(m) is not dict:
            raise rt.RuntimeGateError("prediction request missing match")
        comp = str(m.get("competition_id") or "")
        season = str(m.get("season") or "")
        home = str(m.get("home_team_name") or "").strip()
        away = str(m.get("away_team_name") or "").strip()
        kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
        requested_ceiling = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
        if comp not in rt.FORMAL_SCOPE:
            raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")
        if requested_ceiling > kickoff - timedelta(minutes=60):
            raise rt.RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
        if not season or not home or not away:
            raise rt.RuntimeGateError("canonical fixture identity incomplete")

        base_limit = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff")
        if requested_ceiling <= base_limit:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        bundle = state_root / "bundle"

        # Exact-cutoff replay is intentionally source-silent: it can only reuse a fully validated
        # durable bundle already sealed at the requested cutoff. This is the audited path for a
        # technical prematch rerun after the original acquisition has been frozen.
        try:
            loaded = rt.validate_bundle(bundle)
            lower = rt._parse_dt(loaded["meta"]["historical_cutoff"], "cached cutoff")
        except rt.RuntimeGateError:
            loaded = None
            lower = None
        if loaded is not None and lower == requested_ceiling:
            fixture, identity_audit = _resolved_fixture(repo_root, loaded["state"], comp, season, home, away, kickoff)
            inp = _sealed_replay_input(fixture, requested_ceiling, loaded)
            input_path = out / "runtime_input.json"
            _write_json(input_path, inp)
            _write_json(out / "target_identity_audit.json", identity_audit)
            _write_json(out / "xg_trigger_audit.json", _xg_trigger_audit(loaded["state"], fixture))
            receipt = rt.predict_match(comp, home, away, kickoff, requested_ceiling, bundle, input_path,
                                       repo_root, understat_db, confirmation_dir, False)
            _write_json(out / "prediction_receipt.json", receipt)
            return {
                "status": "PASS",
                "probe": "PREDICTION",
                "fixture": fixture,
                "requested_ceiling": requested_ceiling.isoformat(),
                "cutoff": requested_ceiling.isoformat(),
                "gateway_route": "SEALED_EXACT_CUTOFF_REPLAY",
                "calculation_path": receipt["calculation_path"],
                "model_route": receipt["model_route"],
                "fallback_exact_v1": receipt["fallback_exact_v1"],
                "input_sha": receipt["runtime_input_sha"],
                "state_sha": receipt["state_sha256"],
                "state_bundle_sha": receipt["state_bundle_sha"],
                "prediction_sha": receipt["prediction_sha"],
                "receipt_sha": receipt["receipt_sha"],
                "acquisition_records": 0,
                "acquisition_source_set_sha256": _source_set_sha(loaded),
                "acquisition_observed_at": requested_ceiling.isoformat(),
                "manual_or_auxiliary_fallback_used": False,
                "source_refetch_used": False,
            }

        # Normal prospective path retains the existing acquisition and fail-closed semantics.
        # Only the target strength ids are re-resolved from the explicit current-season identity
        # registry against the already-built state before the frozen runner receives the fixture.
        provisional = live_gateway._fixture(comp, season, home, away, kickoff)
        gateway_route = "FAST_ACQUIRE"
        fast_attempt_gap = None
        try:
            current = rt.validate_bundle(bundle)
            current_cutoff = rt._parse_dt(current["meta"]["historical_cutoff"], "cached cutoff")
            if current_cutoff < base_limit or current_cutoff >= requested_ceiling:
                raise rt.RuntimeGateError("cached state outside prospective live continuity window")
        except rt.RuntimeGateError:
            live_gateway._build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            gateway_route = "FULL_ACQUIRE_REBUILD"

        try:
            live_result = live_gateway._acquire_apply_and_seal(bundle, requested_ceiling, provisional["fixture_id"], repo_root)
        except live.AcquisitionError as exc:
            fast_attempt_gap = {"reason": str(exc), "report": exc.report}
            base = live_gateway._build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            gateway_route = "FULL_ACQUIRE_REBUILD"
            try:
                live_result = live_gateway._acquire_apply_and_seal(
                    bundle, requested_ceiling, provisional["fixture_id"], repo_root,
                    source_prefix=base["loaded"]["source"], identity_prefix=base["loaded"]["identity"],
                )
            except live.AcquisitionError as full_exc:
                gap = {
                    "schema_version": "football3-formal-live-gap-v2",
                    "status": "FORMAL_INPUT_DATA_INCOMPLETE",
                    "probe": "PREDICTION",
                    "fixture": provisional,
                    "requested_ceiling": requested_ceiling.isoformat(),
                    "gateway_route": gateway_route,
                    "fast_attempt": fast_attempt_gap,
                    "full_attempt": {"reason": str(full_exc), "report": full_exc.report},
                    "prediction_sha": None,
                    "receipt_sha": None,
                    "manual_or_auxiliary_fallback_used": False,
                }
                _write_json(out / "formal_gap.json", gap)
                _write_json(out / "live_acquisition_report.json", full_exc.report)
                return gap

        report = live_result["report"]
        effective = live_result["effective"]
        checked = live_result["checked"]
        fixture, identity_audit = _resolved_fixture(repo_root, checked["state"], comp, season, home, away, kickoff)
        _write_json(out / "target_identity_audit.json", identity_audit)
        _write_json(out / "xg_trigger_audit.json", _xg_trigger_audit(checked["state"], fixture))
        _write_json(out / "live_acquisition_report.json", report)
        _write_json(out / "live_state_apply.json", {
            "gateway_route": gateway_route,
            "apply": live_result["stats"],
            "state_bundle_sha": live_result["manifest"]["state_bundle_sha256"],
            "state_sha": live_result["manifest"]["state_sha256"],
            "effective_cutoff": effective.isoformat(),
        })
        inp = live_gateway._sealed_input(fixture, effective, report)
        input_path = out / "runtime_input.json"
        _write_json(input_path, inp)
        try:
            receipt = rt.predict_match(comp, home, away, kickoff, effective, bundle, input_path,
                                       repo_root, understat_db, confirmation_dir, False)
        except rt.RuntimeGateError as exc:
            gap = {
                "schema_version": "football3-formal-live-gap-v2",
                "status": "FORMAL_INPUT_DATA_INCOMPLETE",
                "probe": "PREDICTION",
                "fixture": fixture,
                "requested_ceiling": requested_ceiling.isoformat(),
                "effective_cutoff": effective.isoformat(),
                "gateway_route": gateway_route,
                "reason": str(exc),
                "acquisition": report,
                "prediction_sha": None,
                "receipt_sha": None,
                "manual_or_auxiliary_fallback_used": False,
            }
            _write_json(out / "formal_gap.json", gap)
            return gap
        _write_json(out / "prediction_receipt.json", receipt)
        return {
            "status": "PASS",
            "probe": "PREDICTION",
            "fixture": fixture,
            "requested_ceiling": requested_ceiling.isoformat(),
            "cutoff": effective.isoformat(),
            "gateway_route": gateway_route,
            "calculation_path": receipt["calculation_path"],
            "model_route": receipt["model_route"],
            "fallback_exact_v1": receipt["fallback_exact_v1"],
            "input_sha": receipt["runtime_input_sha"],
            "state_sha": receipt["state_sha256"],
            "state_bundle_sha": receipt["state_bundle_sha"],
            "prediction_sha": receipt["prediction_sha"],
            "receipt_sha": receipt["receipt_sha"],
            "acquisition_records": report["records"],
            "acquisition_source_set_sha256": report["source_set_sha256"],
            "acquisition_observed_at": report["observed_at"],
            "manual_or_auxiliary_fallback_used": False,
            "source_refetch_used": True,
        }

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "target identity resolution plus exact-cutoff sealed-state replay",
        "identity_basis": "explicit current-season aliases + unique existing frozen strength state id",
        "source_refetch_for_exact_replay": False,
        "result_or_xg_used_for_identity": False,
        "formal_current_or_production_pointer_changed": False,
        "model_parameters_or_weights_changed": False,
    }
