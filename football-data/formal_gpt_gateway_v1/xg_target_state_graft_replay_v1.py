#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

# Install the exact formal source-contract/quarantine stack before loading frozen history.
import entry as installed_gateway_stack  # noqa: F401
import runtime as rt
import target_identity_replay_fix_v1 as identity_fix
import xg_coverage_rebuild_replay_v1 as coverage_repair

SCHEMA = "football3-target-xg-state-graft-replay-v1"
TARGET_COMP = "GER_Bundesliga"
TARGET_SEASON = "2026/27"
HIST_SEASON = "2025/26"
TARGET_HOME = "VfB Stuttgart"
TARGET_AWAY = "FC Koln"
TARGET_KICKOFF = "2026-09-04T18:30:00+00:00"
TARGET_CUTOFF = "2026-09-04T00:25:59+00:00"
EXPECTED_BASE_BUNDLE_SHA = "5c6e262179d6d5d06b64057bf9cb184756b72d456dcd5de466d303630d20658d"
EXPECTED_FINAL_BUNDLE_SHA = "64cec8b2d68b3aa5989bbac7ef5125602425f30f282e833cb8cab23e8b7f46e7"
EXPECTED_FINAL_STATE_SHA = "7627578d26510d7240cb95cac2befac8515990c8218ddef2d7e325ecc50736fb"
EXPECTED_LIVE_SOURCE_SET_SHA = "de20070f7fc5c49f5b7c45e0b6c028c0087d3c15cb6861b67fb57f0d626eac63"
EXPECTED_LIVE_RECORDS_SHA = "6a02bb78f2bf453a67d2614b593e6860fcaf1968f964ba5b50fde6e9f86ef8e2"
STUTTGART_ID = "gteam_9dc5bad7a55031b9"
KOLN_ID = "gteam_1c33ac6f1f97b37a"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def find_value(obj: Any, key: str):
    if type(obj) is dict:
        if key in obj:
            return obj[key]
        for value in obj.values():
            hit = find_value(value, key)
            if hit is not None:
                return hit
    elif type(obj) is list:
        for value in obj:
            hit = find_value(value, key)
            if hit is not None:
                return hit
    return None


def snapshot(residual, when, season: str, params) -> tuple[float, float]:
    if residual is None:
        return 0.0, 0.0
    snap = residual.snapshot(when, season, params)
    return float(snap[0]), float(snap[1])


def residual_same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return (
        a.signal_sum == b.signal_sum and a.weight == b.weight and
        a.last_time == b.last_time and a.last_season == b.last_season
    )


def graft_component(mapping_name: str, key, old_state, final_state, repaired_state, target_state, audit: list[dict[str, Any]]) -> None:
    old_map = getattr(old_state, mapping_name)
    final_map = getattr(final_state, mapping_name)
    repaired_map = getattr(repaired_state, mapping_name)
    target_map = getattr(target_state, mapping_name)
    old = old_map.get(key)
    final = final_map.get(key)
    repaired = repaired_map.get(key)

    if final is None:
        if repaired is not None:
            target_map[key] = copy.deepcopy(repaired)
            audit.append({"component": mapping_name, "key": list(key) if type(key) is tuple else key,
                          "route": "REPAIRED_HISTORY_ONLY_NO_ORIGINAL_LIVE_COMPONENT"})
        return

    # If original live window did not touch this component, retain the repaired 2025/26 history state.
    if residual_same(old, final):
        if repaired is None:
            target_map.pop(key, None)
        else:
            target_map[key] = copy.deepcopy(repaired)
        audit.append({"component": mapping_name, "key": list(key) if type(key) is tuple else key,
                      "route": "REPAIRED_HISTORY_ONLY_ORIGINAL_LIVE_UNCHANGED"})
        return

    if final.last_time is None or final.last_season != TARGET_SEASON:
        raise rt.RuntimeGateError(f"unexpected target live component transition: {mapping_name} {key}")
    if final.last_time >= rt._parse_dt(TARGET_CUTOFF, "target cutoff"):
        raise rt.RuntimeGateError(f"target live component update is not strictly pre-cutoff: {mapping_name} {key}")

    old_signal, old_weight = snapshot(old, final.last_time, final.last_season, final_state.p)
    increment_signal = float(final.signal_sum) - old_signal
    increment_weight = float(final.weight) - old_weight
    if abs(increment_weight - 1.0) > 1e-9:
        raise rt.RuntimeGateError(
            f"original target component is not a single frozen live xg update: {mapping_name} {key} weight={increment_weight}"
        )
    repaired_signal, repaired_weight = snapshot(repaired, final.last_time, final.last_season, repaired_state.p)
    target_map[key] = rt.hxg.ResidualState(
        repaired_signal + increment_signal,
        repaired_weight + increment_weight,
        final.last_time,
        final.last_season,
    )
    audit.append({
        "component": mapping_name,
        "key": list(key) if type(key) is tuple else key,
        "route": "REPAIRED_HISTORY_PLUS_ORIGINAL_FROZEN_SINGLE_LIVE_INCREMENT",
        "original_live_update_at": final.last_time.isoformat(),
        "increment_signal": increment_signal,
        "increment_weight": increment_weight,
        "old_pre_live_effective_weight": old_weight,
        "repaired_pre_live_effective_weight": repaired_weight,
        "final_repaired_weight": target_map[key].weight,
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--original-base-bundle", required=True)
    ap.add_argument("--original-final-bundle", required=True)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    understat_db = Path(args.understat_db).resolve()
    confirmation_dir = Path(args.confirmation_dir).resolve()
    out_dir = Path(args.out).resolve(); out_dir.mkdir(parents=True, exist_ok=True)
    target_bundle = Path(args.state_root).resolve() / "bundle"

    original_base = rt.validate_bundle(Path(args.original_base_bundle).resolve())
    original_final = rt.validate_bundle(Path(args.original_final_bundle).resolve())
    if original_base["manifest"]["state_bundle_sha256"] != EXPECTED_BASE_BUNDLE_SHA:
        raise rt.RuntimeGateError("pinned original base bundle hash mismatch")
    if original_final["manifest"]["state_bundle_sha256"] != EXPECTED_FINAL_BUNDLE_SHA:
        raise rt.RuntimeGateError("pinned original final bundle hash mismatch")
    if original_final["manifest"]["state_sha256"] != EXPECTED_FINAL_STATE_SHA:
        raise rt.RuntimeGateError("pinned original final state hash mismatch")
    if original_base["meta"]["historical_cutoff"] != rt.BASE_HISTORY_CUTOFF:
        raise rt.RuntimeGateError("pinned original base cutoff mismatch")
    if original_final["meta"]["historical_cutoff"] != TARGET_CUTOFF:
        raise rt.RuntimeGateError("pinned original final cutoff mismatch")

    source_set_sha = find_value(original_final["source"], "source_set_sha256")
    records_sha = find_value(original_final["source"], "records_sha256")
    if source_set_sha != EXPECTED_LIVE_SOURCE_SET_SHA:
        raise rt.RuntimeGateError(f"pinned final source-set identity mismatch: {source_set_sha}")
    if records_sha != EXPECTED_LIVE_RECORDS_SHA:
        raise rt.RuntimeGateError(f"pinned final event-set identity mismatch: {records_sha}")

    history, v1_source = rt.load_frozen_v1_history(repo_root)
    old_labels, old_xg_source = rt.load_xg_labels(history, understat_db, confirmation_dir)
    coverage_repair.COMPETITIONS = (TARGET_COMP,)
    labels, coverage = coverage_repair.augment_2025_26_labels(repo_root, history, old_labels)

    # These retrospective xG rows were observed on 2026-07-21, before this target but after
    # their own matches. For state reconstruction we do NOT claim historical PIT availability.
    # Align their replay release to the frozen V1 engineering release (formal fixture +3h),
    # preserving the exact V1 state trajectory while adding only lagged xG residual history.
    added_ids = set(labels) - set(old_labels)
    history_by_id = {f.fixture_id: f for f in history}
    if len(added_ids) != 306:
        raise rt.RuntimeGateError(f"Bundesliga 2025/26 xg repair cardinality mismatch: {len(added_ids)}")
    for fid in sorted(added_ids):
        f = history_by_id[fid]
        x = labels[fid]
        release = f.kickoff + timedelta(hours=3)
        labels[fid] = rt.XGLabel(
            rt.hxg.ReleasedLabel(x.label.home_goals, x.label.away_goals, x.label.home_xg, x.label.away_xg, release),
            x.source_fixture_id,
            x.source_sha256,
            release.isoformat(),
        )
    coverage["replay_release_adapter"] = "formal_fixture_kickoff_plus_3h_preserves_frozen_v1_trajectory"
    coverage["historical_pit_claim"] = False
    coverage["target_lagged_feature_eligibility"] = "PRETARGET_OBSERVED_2026_07_21"
    write_json(out_dir / "xg_coverage_repair_audit.json", coverage)

    repaired_base, replay = rt.replay_history_state(history, labels, rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base cutoff"))
    # V1 must remain byte-for-byte state-equivalent to the original base. This repair is XG-only.
    if rt.serialize_v1_state(repaired_base.base) != rt.serialize_v1_state(original_base["state"].base):
        raise rt.RuntimeGateError("xg coverage repair changed Frozen V1 state trajectory")

    kickoff = rt._parse_dt(TARGET_KICKOFF, "target kickoff")
    cutoff = rt._parse_dt(TARGET_CUTOFF, "target cutoff")
    final_state = original_final["state"]
    old_state = original_base["state"]
    candidate = rt.deserialize_state(rt.serialize_v1_state(final_state.base), rt.serialize_xg_state(final_state))

    target_ids = (STUTTGART_ID, KOLN_ID)
    component_audit: list[dict[str, Any]] = []
    for tid in target_ids:
        for venue in ("home", "away"):
            for name in ("venue_attack", "venue_defence"):
                graft_component(name, (tid, venue), old_state, final_state, repaired_base, candidate, component_audit)
        for name in ("pooled_attack", "pooled_defence"):
            graft_component(name, tid, old_state, final_state, repaired_base, candidate, component_audit)

    # Only mark the repaired 2025/26 labels involving the target teams as newly represented
    # in this target-scoped candidate; all other live-state bookkeeping remains pinned to Run 65.
    target_hist_ids = {
        fid for fid in added_ids
        if history_by_id[fid].home_team_id in target_ids or history_by_id[fid].away_team_id in target_ids
    }
    candidate.seen = set(final_state.seen) | target_hist_ids
    candidate.pending = copy.deepcopy(final_state.pending)
    candidate.last_prediction_time = final_state.last_prediction_time
    candidate.last_apply_time = final_state.last_apply_time

    # Frozen V1 must be exactly the original final state; model params must remain exact.
    if rt.serialize_v1_state(candidate.base) != rt.serialize_v1_state(final_state.base):
        raise rt.RuntimeGateError("candidate Frozen V1 differs from original final state")
    if candidate.p != final_state.p:
        raise rt.RuntimeGateError("candidate XG parameters changed")

    source = {
        "schema_version": SCHEMA,
        "original_base_bundle_sha256": EXPECTED_BASE_BUNDLE_SHA,
        "original_final_bundle_sha256": EXPECTED_FINAL_BUNDLE_SHA,
        "original_final_state_sha256": EXPECTED_FINAL_STATE_SHA,
        "original_live_source_set_sha256": source_set_sha,
        "original_live_records_sha256": records_sha,
        "xg_original_frozen": old_xg_source,
        "v1_original_frozen": v1_source,
        "xg_2025_26_bundesliga_repair": coverage,
        "target_component_graft": component_audit,
        "source_refetch_used": False,
        "post_target_information_used": False,
        "target_match_xg_used": False,
    }
    identity = rt._production_identity()
    identity["target_scoped_repair"] = {
        "competition_id": TARGET_COMP,
        "target_team_ids": list(target_ids),
        "identity_resolution": "explicit current-season aliases plus pinned original state evidence",
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    manifest = rt.seal_bundle(candidate, target_bundle, source, identity, TARGET_CUTOFF, "TARGET_SCOPED_PRETARGET_XG_STATE_REPLAY")
    checked = rt.validate_bundle(target_bundle)

    fixture, identity_audit = identity_fix._resolved_fixture(repo_root, checked["state"], TARGET_COMP, TARGET_SEASON, TARGET_HOME, TARGET_AWAY, kickoff)
    if fixture["home_team_id"] != STUTTGART_ID or fixture["away_team_id"] != KOLN_ID:
        raise rt.RuntimeGateError(f"target identity mismatch after repair: {fixture}")
    write_json(out_dir / "target_identity_audit.json", identity_audit)
    trigger = identity_fix._xg_trigger_audit(checked["state"], fixture)
    write_json(out_dir / "xg_trigger_audit.json", trigger)

    inp = identity_fix._sealed_replay_input(fixture, cutoff, checked)
    input_path = out_dir / "runtime_input.json"; write_json(input_path, inp)
    receipt = rt.predict_match(TARGET_COMP, TARGET_HOME, TARGET_AWAY, kickoff, cutoff, target_bundle, input_path,
                               repo_root, understat_db, confirmation_dir, False)
    write_json(out_dir / "prediction_receipt.json", receipt)
    if receipt.get("fallback_exact_v1") is not False:
        raise rt.RuntimeGateError(f"target xg coverage repair did not clear exact V1 fallback: {trigger}")
    if receipt.get("model_route") == "FROZEN_V1_EXACT_FALLBACK":
        raise rt.RuntimeGateError("target replay remained on Frozen V1 exact fallback")

    proof = {
        "schema_version": "football3-pinned-original-state-delta-reuse-proof-v1",
        "status": "PASS",
        "original_base_bundle_sha256": EXPECTED_BASE_BUNDLE_SHA,
        "original_final_bundle_sha256": EXPECTED_FINAL_BUNDLE_SHA,
        "original_final_state_sha256": EXPECTED_FINAL_STATE_SHA,
        "original_live_source_set_sha256": source_set_sha,
        "original_live_records_sha256": records_sha,
        "target_component_transitions": component_audit,
        "target_2025_26_repaired_fixture_n": len(target_hist_ids),
        "source_refetch_used": False,
        "current_or_postcutoff_source_read_for_state": False,
        "post_target_information_used": False,
    }
    write_json(out_dir / "original_state_delta_reuse_proof.json", proof)
    write_json(out_dir / "state_build_receipt.json", {
        "schema_version": SCHEMA,
        "status": "PASS",
        "historical_cutoff": TARGET_CUTOFF,
        "state_bundle_sha256": manifest["state_bundle_sha256"],
        "state_sha256": manifest["state_sha256"],
        "v1_seen_n": len(checked["state"].base.seen_fixtures),
        "xg_seen_n": len(checked["state"].seen),
        "xg_pending_n": len(checked["state"].pending),
    })

    summary = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "fixture": fixture,
        "cutoff": TARGET_CUTOFF,
        "gateway_route": "PINNED_ORIGINAL_STATE_TARGET_XG_REPLAY_NO_REFETCH",
        "calculation_path": receipt["calculation_path"],
        "model_route": receipt["model_route"],
        "fallback_exact_v1": receipt["fallback_exact_v1"],
        "formal_head": receipt["formal_head"],
        "formal_current_sha256": receipt["formal_current_sha256"],
        "runtime_input_sha": receipt["runtime_input_sha"],
        "state_sha256": receipt["state_sha256"],
        "state_bundle_sha": receipt["state_bundle_sha"],
        "prediction_sha": receipt["prediction_sha"],
        "receipt_sha": receipt["receipt_sha"],
        "original_final_bundle_sha256": EXPECTED_FINAL_BUNDLE_SHA,
        "original_live_source_set_sha256": source_set_sha,
        "original_live_records_sha256": records_sha,
        "source_refetch_used": False,
        "target_match_xg_used": False,
        "post_target_information_used": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
        "fusion_weights": {"xg": 0.75, "frozen_v1": 0.25},
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
