#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import formal_state_integrity_full_rebuild_v1 as full_rebuild
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt
from historical_xg_challenger_v1 import historical_xg_challenger as hxg

SCHEMA = "football3-formal-state-integrity-guard-v1"
BINDING_SCHEMA = "football3-fast-cache-binding-v1"
MIN_XG_EVIDENCE = 3.0
MIN_EXPECTED_LINKED_XG_MATCHES = 3


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _nested_dicts(obj: Any):
    if type(obj) is dict:
        yield obj
        for v in obj.values():
            yield from _nested_dicts(v)
    elif type(obj) is list:
        for v in obj:
            yield from _nested_dicts(v)


def _latest_available_at(loaded: dict[str, Any]) -> str:
    values = [str(loaded["meta"]["historical_cutoff"])]
    for d in _nested_dicts(loaded.get("source")):
        for key in ("observed_at", "acquisition_observed_at", "source_observed_at_utc"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                values.append(v.strip())
            elif isinstance(v, list):
                values.extend(str(x).strip() for x in v if str(x or "").strip())
    parsed = []
    for v in values:
        try:
            parsed.append((rt._parse_dt(v, "available_at"), v))
        except rt.RuntimeGateError:
            continue
    if not parsed:
        return str(loaded["meta"]["historical_cutoff"])
    parsed.sort(key=lambda x: x[0])
    return parsed[-1][0].isoformat()


def _latest_live_report(loaded: dict[str, Any]) -> dict[str, Any] | None:
    hits = []
    for d in _nested_dicts(loaded.get("source")):
        if d.get("status") == "VERIFIED_COMPLETE" and d.get("source_set_sha256") and d.get("effective_cutoff"):
            try:
                dt = rt._parse_dt(str(d["effective_cutoff"]), "effective_cutoff")
            except rt.RuntimeGateError:
                continue
            hits.append((dt, d))
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    return hits[-1][1]


def _coverage_audit(loaded: dict[str, Any]) -> dict[str, Any] | None:
    for d in _nested_dicts(loaded.get("source")):
        if d.get("schema_version") == "football3-cross-season-xg-coverage-v1":
            return d
    return None


def _linked_count(loaded: dict[str, Any], comp: str, team_id: str) -> int:
    cov = _coverage_audit(loaded) or {}
    return int((((cov.get("team_xg_match_counts") or {}).get(comp) or {}).get(team_id) or 0))


def _binding_payload(loaded: dict[str, Any], comp: str, season: str, cutoff, identity_audit: dict[str, Any]) -> dict[str, Any]:
    waterline = str(loaded["meta"]["historical_cutoff"])
    requested_cutoff = cutoff.isoformat()
    try:
        waterline_dt = rt._parse_dt(waterline, "cache data waterline")
        covers_requested = waterline_dt >= cutoff
    except rt.RuntimeGateError:
        covers_requested = False
    core = {
        "schema_version": BINDING_SCHEMA,
        "formal_head": loaded["meta"]["formal_head"],
        "current_sha256": loaded["meta"]["current_sha256"],
        "model_sha256": loaded["meta"]["model_bindings_sha256"],
        "data_source_sha256": loaded["meta"]["source_identity_sha256"],
        "competition_id": comp,
        "season": season,
        "cutoff": requested_cutoff,
        "home_canonical_id": identity_audit["home"]["strength_team_id"],
        "away_canonical_id": identity_audit["away"]["strength_team_id"],
        "data_waterline": waterline,
        "data_waterline_covers_requested_cutoff": covers_requested,
        "latest_data_available_at": _latest_available_at(loaded),
        "state_sha256": loaded["manifest"]["state_sha256"],
        "state_bundle_sha256": loaded["manifest"]["state_bundle_sha256"],
    }
    core["binding_sha256"] = rt._sha_bytes(rt._canon_bytes(core))
    return core


def _read_binding(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return obj if type(obj) is dict else None


def _cache_preflight(state_root: Path, repo_root: Path, comp: str, season: str, home: str, away: str, kickoff, cutoff) -> dict[str, Any]:
    bundle = state_root / "bundle"
    binding_path = state_root / "fast_cache_binding_v1.json"
    try:
        loaded = rt.validate_bundle(bundle)
        fixture, identity = identity_bridge.resolve_fixture(repo_root, loaded["state"], comp, season, home, away, kickoff)
        expected = _binding_payload(loaded, comp, season, cutoff, identity)
    except rt.RuntimeGateError as exc:
        return {
            "fast_eligible": False,
            "reason": f"CACHE_VALIDATION_FAILED:{exc}",
            "loaded": None,
            "fixture": None,
            "identity": None,
            "expected_binding": None,
        }
    actual = _read_binding(binding_path)
    if actual != expected:
        return {
            "fast_eligible": False,
            "reason": "CACHE_BINDING_MISSING_OR_MISMATCH",
            "loaded": loaded,
            "fixture": fixture,
            "identity": identity,
            "expected_binding": expected,
            "actual_binding": actual,
        }
    return {
        "fast_eligible": True,
        "reason": None,
        "loaded": loaded,
        "fixture": fixture,
        "identity": identity,
        "expected_binding": expected,
        "actual_binding": actual,
    }


def _clear_cache(state_root: Path) -> None:
    shutil.rmtree(state_root / "bundle", ignore_errors=True)
    for name in ("fast_cache_binding_v1.json",):
        try:
            (state_root / name).unlink()
        except FileNotFoundError:
            pass


def _v1_aux(state: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    f = rt.v1_engine.Fixture(
        fixture["fixture_id"], fixture["competition_id"], fixture["season"],
        rt._parse_dt(fixture["kickoff"], "fixture kickoff"),
        fixture["home_team_id"], fixture["away_team_id"],
    )
    p = state.base.predict(f)
    return {
        "cold_start_bucket": p["cold_start_bucket"],
        "effective_home_history": float(p["effective_home_history"]),
        "effective_away_history": float(p["effective_away_history"]),
        "effective_competition_history": float(p["effective_competition_history"]),
        "prior_source": p["prior_source"],
    }


def _xg_trigger_audit(state: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    """Generic, read-only XG trigger metadata audit over a state clone.

    The frozen challenger's lightweight prediction intentionally omits `dynamic`.
    Integrity inspection therefore uses the normal metadata-bearing prediction with
    score-matrix generation disabled. The cloned state is never persisted or applied
    back, so this cannot alter model parameters, weights, or production state.
    """
    clone = rt.deserialize_state(rt.serialize_v1_state(state.base), rt.serialize_xg_state(state))
    f = hxg.FixtureRow(
        fixture["fixture_id"], fixture["competition_id"], fixture["season"],
        rt._parse_dt(fixture["kickoff"], "fixture kickoff"),
        fixture["home_team_id"], fixture["away_team_id"],
        fixture["home_team_name"], fixture["away_team_name"],
    )
    xg_predictions, _ = clone.predict_batch([f], include_matrix=False)
    if len(xg_predictions) != 1:
        raise rt.RuntimeGateError("XG trigger diagnostic cardinality mismatch")
    row = xg_predictions[0]
    dynamic = row.get("dynamic")
    if type(dynamic) is not dict:
        raise rt.RuntimeGateError(
            "XG trigger diagnostic metadata missing from metadata-bearing challenger prediction"
        )
    return {
        "schema_version": "football3-xg-trigger-audit-v2",
        "fixture_id": fixture["fixture_id"],
        "dynamic": dynamic,
        "frozen_min_effective_evidence": MIN_XG_EVIDENCE,
        "diagnostic_prediction_keys": sorted(str(k) for k in row.keys()),
        "diagnostic_state_clone_only": True,
        "score_matrix_requested": False,
        "lightweight_mode_used": False,
        "persisted_state_mutated": False,
        "target_specific_replay_used": False,
        "model_parameters_or_weights_changed": False,
    }


def classify_state(
    loaded: dict[str, Any],
    fixture: dict[str, Any],
    identity_audit: dict[str, Any],
    trigger: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    aux = _v1_aux(loaded["state"], fixture)
    evidence = [float(x) for x in ((trigger.get("dynamic") or {}).get("evidence") or [])]
    home_xg_n = _linked_count(loaded, fixture["competition_id"], fixture["home_team_id"])
    away_xg_n = _linked_count(loaded, fixture["competition_id"], fixture["away_team_id"])
    reasons: list[str] = []

    for side, key in (("home", "effective_home_history"), ("away", "effective_away_history")):
        relation = str(identity_audit[side].get("season_relation") or "")
        if aux[key] <= 1e-12 and relation != "PROMOTED_OR_NEW_TO_FORMAL_HISTORY":
            reasons.append(f"{side.upper()}_ESTABLISHED_HISTORY_ZERO")

    receipt_cutoff = str(receipt.get("cutoff") or "")
    waterline = str(loaded["meta"]["historical_cutoff"])
    if receipt_cutoff and receipt_cutoff != waterline:
        reasons.append(f"DATA_WATERLINE_CUTOFF_MISMATCH:{waterline}!={receipt_cutoff}")

    live_report = _latest_live_report(loaded)
    release_audit = {
        "status": "NO_LIVE_REPORT_REQUIRED",
        "all_due_releases_verified": None,
        "v1_label_releases": None,
        "xg_label_releases": None,
        "quarantined_v1_label_releases": None,
        "interval_from": None,
        "interval_to": loaded["meta"]["historical_cutoff"],
    }
    if live_report is not None:
        release_audit = {
            "status": str(live_report.get("status")),
            "all_due_releases_verified": live_report.get("status") == "VERIFIED_COMPLETE",
            "v1_label_releases": int(live_report.get("v1_label_releases", 0)),
            "xg_label_releases": int(live_report.get("xg_label_releases", 0)),
            "quarantined_v1_label_releases": int(live_report.get("quarantined_v1_label_releases", 0)),
            "interval_from": live_report.get("from"),
            "interval_to": live_report.get("effective_cutoff"),
        }
        if live_report.get("status") != "VERIFIED_COMPLETE":
            reasons.append("CURRENT_SEASON_RELEASE_AUDIT_NOT_VERIFIED_COMPLETE")

    fallback = bool(receipt.get("fallback_exact_v1"))
    expected_legal_xg = (
        home_xg_n >= MIN_EXPECTED_LINKED_XG_MATCHES
        and away_xg_n >= MIN_EXPECTED_LINKED_XG_MATCHES
    )
    if fallback and evidence and min(evidence) >= MIN_XG_EVIDENCE:
        reasons.append(
            "FALLBACK_DESPITE_EFFECTIVE_EVIDENCE_THRESHOLD:"
            + ",".join(f"{x:.12g}" for x in evidence)
        )
    elif fallback and expected_legal_xg:
        reasons.append(
            "XG_EXPECTED_BUT_EFFECTIVE_EVIDENCE_INSUFFICIENT:"
            + ",".join(f"{x:.12g}" for x in evidence)
        )

    anomaly = bool(reasons)
    if anomaly:
        fallback_class = "DATA_STATE_ANOMALY"
        fallback_reason = ";".join(reasons)
    elif fallback:
        fallback_class = "NORMAL_FALLBACK"
        fallback_reason = (
            "LEGAL_XG_NOT_SUFFICIENT_FOR_BOTH_TEAMS:"
            f"home_linked_2025_26={home_xg_n},away_linked_2025_26={away_xg_n},"
            f"effective_evidence={evidence},threshold={MIN_XG_EVIDENCE}"
        )
    else:
        fallback_class = "NONE"
        fallback_reason = None

    return {
        "schema_version": SCHEMA,
        "status": "DATA_STATE_ANOMALY" if anomaly else "PASS",
        "fallback_class": fallback_class,
        "fallback_reason": fallback_reason,
        "v1": {
            "state_seen_fixtures": len(loaded["state"].base.seen_fixtures),
            "home_historical_match_count": int(identity_audit["home"].get("historical_match_count", 0)),
            "away_historical_match_count": int(identity_audit["away"].get("historical_match_count", 0)),
            "home_historical_matches_by_season": identity_audit["home"].get("historical_matches_by_season", {}),
            "away_historical_matches_by_season": identity_audit["away"].get("historical_matches_by_season", {}),
            "effective_home_history": aux["effective_home_history"],
            "effective_away_history": aux["effective_away_history"],
            "effective_competition_history": aux["effective_competition_history"],
            "cold_start_bucket": aux["cold_start_bucket"],
            "prior_source": aux["prior_source"],
        },
        "historical_xg": {
            "state_seen_fixtures": len(loaded["state"].seen),
            "state_pending_fixtures": len(loaded["state"].pending),
            "effective_evidence": evidence,
            "min_effective_evidence": min(evidence) if evidence else None,
            "frozen_threshold": MIN_XG_EVIDENCE,
            "home_linked_2025_26_matches": home_xg_n,
            "away_linked_2025_26_matches": away_xg_n,
            "expected_legal_xg_for_both_teams": expected_legal_xg,
            "dynamic": trigger.get("dynamic"),
        },
        "current_season_release_audit": release_audit,
        "latest_data_available_at": _latest_available_at(loaded),
        "data_waterline": loaded["meta"]["historical_cutoff"],
        "prediction_cutoff": receipt_cutoff or None,
        "data_waterline_covers_prediction_cutoff": (not receipt_cutoff) or receipt_cutoff == waterline,
        "identity": identity_audit,
        "anomaly_reasons": reasons,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }


def _enrich_receipt(
    out: Path,
    result: dict[str, Any],
    audit: dict[str, Any],
    execution_mode: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    path = out / "prediction_receipt.json"
    if not path.is_file():
        raise rt.RuntimeGateError("prediction receipt missing after PASS")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    old_receipt_sha = receipt.pop("receipt_sha", None)
    receipt["model_receipt_sha_before_integrity_enrichment"] = old_receipt_sha
    receipt["execution_mode"] = execution_mode
    receipt["fusion_status"] = "V1_FALLBACK" if receipt.get("fallback_exact_v1") else "FUSION_ACTIVE"
    receipt["fallback_class"] = audit["fallback_class"]
    receipt["fallback_reason"] = audit["fallback_reason"]
    receipt["history_counts"] = {
        "v1_state_seen_fixtures": audit["v1"]["state_seen_fixtures"],
        "v1_home_historical_matches": audit["v1"]["home_historical_match_count"],
        "v1_away_historical_matches": audit["v1"]["away_historical_match_count"],
        "v1_home_historical_matches_by_season": audit["v1"]["home_historical_matches_by_season"],
        "v1_away_historical_matches_by_season": audit["v1"]["away_historical_matches_by_season"],
        "xg_state_seen_fixtures": audit["historical_xg"]["state_seen_fixtures"],
        "xg_state_pending_fixtures": audit["historical_xg"]["state_pending_fixtures"],
        "v1_effective_home_history": audit["v1"]["effective_home_history"],
        "v1_effective_away_history": audit["v1"]["effective_away_history"],
        "xg_effective_evidence": audit["historical_xg"]["effective_evidence"],
        "home_linked_2025_26_xg_matches": audit["historical_xg"]["home_linked_2025_26_matches"],
        "away_linked_2025_26_xg_matches": audit["historical_xg"]["away_linked_2025_26_matches"],
    }
    receipt["latest_data_available_at"] = audit["latest_data_available_at"]
    receipt["data_waterline"] = audit["data_waterline"]
    receipt["data_waterline_covers_cutoff"] = audit["data_waterline_covers_prediction_cutoff"]
    receipt["current_season_release_audit"] = audit["current_season_release_audit"]
    receipt["team_identity_bridge"] = {
        "home": audit["identity"]["home"],
        "away": audit["identity"]["away"],
    }
    receipt["fast_cache_binding_sha256"] = binding["binding_sha256"]
    receipt["state_integrity_guard"] = {
        "schema_version": SCHEMA,
        "status": audit["status"],
        "fallback_class": audit["fallback_class"],
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    receipt["sha256"] = {
        "input_sha256": receipt.get("runtime_input_sha"),
        "state_sha256": receipt.get("state_sha256"),
        "state_bundle_sha256": receipt.get("state_bundle_sha"),
        "prediction_sha256": receipt.get("prediction_sha"),
        "receipt_sha256_before_integrity_enrichment": old_receipt_sha,
    }
    receipt["receipt_sha"] = rt._sha_bytes(rt._canon_bytes(receipt))
    _write_json(path, receipt)
    result["receipt_sha"] = receipt["receipt_sha"]
    result["execution_mode"] = execution_mode
    result["fallback_class"] = audit["fallback_class"]
    result["fallback_reason"] = audit["fallback_reason"]
    result["latest_data_available_at"] = audit["latest_data_available_at"]
    return receipt


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
        cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
        if comp not in rt.FORMAL_SCOPE:
            raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")
        if rt.FORMAL_HEAD != "e12f5d1193be5d81f60301cf34ab2140e11712a9":
            raise rt.RuntimeGateError("formal HEAD identity drift")
        if rt.CURRENT_SHA256 != "ecf5fb99aaf2eb551c2c06bc5d37d3c656a2a1fcc280fd52045986f5894874f8":
            raise rt.RuntimeGateError("formal CURRENT identity drift")

        pre = _cache_preflight(state_root, repo_root, comp, season, home, away, kickoff, cutoff)
        full_reason = None
        if not pre["fast_eligible"]:
            full_reason = pre["reason"]
            _clear_cache(state_root)
            rebuilt = full_rebuild.build_integrity_base(
                state_root / "bundle", repo_root, understat_db, confirmation_dir, cutoff
            )
            loaded = rebuilt["loaded"]
            _, ident = identity_bridge.resolve_fixture(repo_root, loaded["state"], comp, season, home, away, kickoff)
            pre_binding = _binding_payload(loaded, comp, season, cutoff, ident)
            _write_json(state_root / "fast_cache_binding_v1.json", pre_binding)

        result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if result.get("status") != "PASS":
            _write_json(out / "state_integrity_audit.json", {
                "schema_version": SCHEMA,
                "status": "UPSTREAM_FAIL_CLOSED",
                "gateway_result": result,
                "cache_preflight": {"fast_eligible": pre["fast_eligible"], "reason": pre["reason"]},
                "model_parameters_or_weights_changed": False,
            })
            return result

        def inspect_current():
            loaded = rt.validate_bundle(state_root / "bundle")
            fixture, ident = identity_bridge.resolve_fixture(repo_root, loaded["state"], comp, season, home, away, kickoff)
            trigger = _xg_trigger_audit(loaded["state"], fixture)
            receipt = json.loads((out / "prediction_receipt.json").read_text(encoding="utf-8"))
            audit = classify_state(loaded, fixture, ident, trigger, receipt)
            return loaded, fixture, ident, trigger, receipt, audit

        loaded, fixture, ident, trigger, receipt, audit = inspect_current()
        rebuilt_after_anomaly = False
        if audit["status"] == "DATA_STATE_ANOMALY" and full_reason is None:
            rebuilt_after_anomaly = True
            full_reason = "DATA_STATE_ANOMALY:" + ";".join(audit["anomaly_reasons"])
            _clear_cache(state_root)
            full_rebuild.build_integrity_base(
                state_root / "bundle", repo_root, understat_db, confirmation_dir, cutoff
            )
            result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
            if result.get("status") != "PASS":
                return result
            loaded, fixture, ident, trigger, receipt, audit = inspect_current()

        if audit["status"] == "DATA_STATE_ANOMALY":
            if (out / "prediction_receipt.json").exists():
                shutil.move(out / "prediction_receipt.json", out / "rejected_prediction_receipt.json")
            gap = {
                "schema_version": SCHEMA,
                "status": "DATA_STATE_ANOMALY",
                "probe": "PREDICTION",
                "fixture": fixture,
                "cutoff": result.get("cutoff"),
                "anomaly_reasons": audit["anomaly_reasons"],
                "fallback_class": "DATA_STATE_ANOMALY",
                "prediction_sha": None,
                "receipt_sha": None,
                "manual_or_auxiliary_fallback_used": False,
            }
            _write_json(out / "formal_gap.json", gap)
            _write_json(out / "state_integrity_audit.json", audit)
            return gap

        binding = _binding_payload(loaded, comp, season, cutoff, ident)
        _write_json(state_root / "fast_cache_binding_v1.json", binding)
        _write_json(out / "fast_cache_binding_v1.json", binding)
        _write_json(out / "team_identity_bridge.json", ident)
        _write_json(out / "xg_trigger_audit.json", trigger)

        execution_mode = "FULL" if (full_reason is not None or str(result.get("gateway_route") or "").startswith("FULL")) else "FAST"
        audit["execution_mode"] = execution_mode
        audit["full_rebuild_reason"] = full_reason
        audit["rebuilt_after_anomaly"] = rebuilt_after_anomaly
        audit["cache_preflight"] = {
            "fast_eligible": pre["fast_eligible"],
            "reason": pre["reason"],
        }
        _write_json(out / "state_integrity_audit.json", audit)
        enriched = _enrich_receipt(out, result, audit, execution_mode, binding)
        result["state_integrity_guard"] = {
            "status": "PASS",
            "execution_mode": execution_mode,
            "fallback_class": audit["fallback_class"],
            "receipt_sha": enriched["receipt_sha"],
        }
        return result

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "pre_prediction_checks": [
            "formal_scope", "formal_head_and_current", "cache_binding", "data_waterline",
            "unique_team_identity", "cross_season_history", "current_season_release_completeness",
            "v1_and_historical_xg_effective_history",
        ],
        "fallback_classes": ["NORMAL_FALLBACK", "DATA_STATE_ANOMALY"],
        "fast_cache_binding_fields": [
            "formal_head", "current_sha256", "model_sha256", "data_source_sha256",
            "competition_id", "season", "cutoff", "home_canonical_id", "away_canonical_id",
            "data_waterline", "latest_data_available_at", "state_sha256", "state_bundle_sha256",
        ],
        "data_state_anomaly_forces_full_rebuild": True,
        "identity_fail_closed": True,
        "generic_xg_trigger_metadata_from_state_clone": True,
        "target_specific_replay_dependency": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
