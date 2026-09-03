#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import source_contract_resolution_v1 as source_resolution
import runtime as rt


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _fixture(comp: str, season: str, home: str, away: str, kickoff) -> dict[str, Any]:
    return {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": rt._global_team_id(home),
        "away_team_id": rt._global_team_id(away),
        "home_team_name": home,
        "away_team_name": away,
    }


def _build_frozen_base(bundle: Path, repo_root: Path, understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    return source_resolution.build_resolved_base(repo_root, understat_db, confirmation_dir, bundle)


def _sealed_input(fixture: dict[str, Any], cutoff, report: dict[str, Any]) -> dict[str, Any]:
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
        "source_set_sha256": report["source_set_sha256"],
        "acquisition_schema": report["schema_version"],
        "acquisition_observed_at": report["observed_at"],
        "state_preapplied_and_sealed": True,
    }
    return {"schema_version": rt.INPUT_SCHEMA, "fixture": fixture, "cutoff": cutoff.isoformat(), "delta_coverage": coverage, "model_delta": empty}


def _acquire_apply_and_seal(bundle: Path, requested_ceiling, fixture_id: str, repo_root: Path,
                            source_prefix: dict[str, Any] | None = None, identity_prefix: dict[str, Any] | None = None):
    current = rt.validate_bundle(bundle)
    state = current["state"]
    lower = rt._parse_dt(current["meta"]["historical_cutoff"], "bundle cutoff")
    delta, report = live.acquire_verified_delta(repo_root, lower, requested_ceiling, fixture_id, state)
    effective = rt._parse_dt(str(report["effective_cutoff"]), "effective live cutoff")
    stats = rt._apply_events(state, delta, effective)
    source = {
        "prior_state_source": current["source"] if source_prefix is None else source_prefix,
        "live_acquisition": report,
        "live_apply": stats,
        "source_scope": "VERIFIED_PROSPECTIVE_LIVE_STATE",
    }
    identity = {
        "prior_state_identity": current["identity"] if identity_prefix is None else identity_prefix,
        "live_acquisition_schema": report["schema_version"],
        "live_source_set_sha256": report["source_set_sha256"],
        "live_interval": {"from": lower.isoformat(), "to": effective.isoformat()},
        "target_fixture_excluded_from_delta": fixture_id,
    }
    manifest = rt.seal_bundle(state, bundle, source, identity, effective.isoformat(), "GATEWAY_PREAPPLIED_VERIFIED_LIVE_STATE")
    checked = rt.validate_bundle(bundle)
    return {
        "lower": lower, "effective": effective, "delta": delta, "report": report, "stats": stats,
        "manifest": manifest, "checked": checked,
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

        fixture = _fixture(comp, season, home, away, kickoff)
        bundle = state_root / "bundle"
        gateway_route = "FAST_ACQUIRE"
        fast_attempt_gap = None

        # FAST starts only from a durable state that is already at/after the immutable base cutoff.
        try:
            loaded = rt.validate_bundle(bundle)
            lower = rt._parse_dt(loaded["meta"]["historical_cutoff"], "cached cutoff")
            if lower < base_limit or lower >= requested_ceiling:
                raise rt.RuntimeGateError("cached state outside prospective live continuity window")
        except rt.RuntimeGateError:
            _build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            gateway_route = "FULL_ACQUIRE_REBUILD"

        try:
            live_result = _acquire_apply_and_seal(bundle, requested_ceiling, fixture["fixture_id"], repo_root)
        except live.AcquisitionError as exc:
            fast_attempt_gap = {"reason": str(exc), "report": exc.report}
            # FAST incompleteness automatically escalates to FULL from the immutable complete V1 + explicit-XG-quarantine base.
            base = _build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            gateway_route = "FULL_ACQUIRE_REBUILD"
            try:
                live_result = _acquire_apply_and_seal(
                    bundle, requested_ceiling, fixture["fixture_id"], repo_root,
                    source_prefix=base["loaded"]["source"], identity_prefix=base["loaded"]["identity"],
                )
            except live.AcquisitionError as full_exc:
                gap = {
                    "schema_version": "football3-formal-live-gap-v2",
                    "status": "FORMAL_INPUT_DATA_INCOMPLETE",
                    "probe": "PREDICTION",
                    "fixture": fixture,
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
        _write_json(out / "live_acquisition_report.json", report)
        _write_json(out / "live_state_apply.json", {
            "gateway_route": gateway_route,
            "apply": live_result["stats"],
            "state_bundle_sha": live_result["manifest"]["state_bundle_sha256"],
            "state_sha": live_result["manifest"]["state_sha256"],
            "effective_cutoff": effective.isoformat(),
        })
        inp = _sealed_input(fixture, effective, report)
        input_path = out / "runtime_input.json"
        _write_json(input_path, inp)

        # Frozen original runner receives a fully validated state sealed at the real source-observation cutoff.
        # Its delta is empty and VERIFIED_COMPLETE; it still owns formal model prediction and receipt creation.
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
        }

    gateway_module.normal_request = normal_request
    return {
        "schema_version": "football3-live-gateway-patch-v2",
        "installed": True,
        "frozen_runtime_head": gateway_module.BASELINE_RUNTIME_HEAD,
        "base_history_cutoff": rt.BASE_HISTORY_CUTOFF,
        "fast_then_full": True,
        "full_base_policy": "complete Frozen V1 + row-local explicit XG quarantine, no time/result substitution",
        "runner_receives_prevalidated_state_at_effective_cutoff": True,
    }
