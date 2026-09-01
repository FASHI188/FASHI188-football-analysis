from __future__ import annotations

import json
import pathlib
import shutil
import statistics
from collections import Counter
from typing import Any

import candidate_b_diagnostic as cbd
import historical_pit_replay as core
from candidate_c import (
    UNCERTAINTY_BY_GRADE,
    c1_availability_replacement,
    c2_possible_xi,
    c3_confirmed_xi,
    c4_bench,
    combine_effects,
    evidence_grade,
    probability_mass_supported,
    zero_effect,
)
from candidate_c_historical import contract as historical_uncertainty_contract
from candidate_c_historical import monotonic_contract_holds, uncertainty_only_effect
from player_strength import PlayerVector
from historical_pit_sharded_common import SHARD_N, SHARD_SIZE, ShardError, exact_files, sha_file, shard_bounds, verify_file_set


def _load_vectors(snapshot: dict[str, Any]) -> dict[str, PlayerVector]:
    return {str(pid): PlayerVector(**raw) for pid, raw in (snapshot.get("vectors") or {}).items()}


def _relevant_player_ids(packet: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for side in ("home", "away"):
        for row in ((packet.get("predicted_lineups") or {}).get(side) or []):
            if row.get("player_id"): ids.add(str(row["player_id"]))
        for row in ((packet.get("confirmed_lineups") or {}).get(side) or []):
            if row.get("player_id"): ids.add(str(row["player_id"]))
        for row in ((packet.get("bench") or {}).get(side) or []):
            if row.get("player_id"): ids.add(str(row["player_id"]))
    ids |= {str(x["player_id"]) for x in (packet.get("status_records") or []) if x.get("player_id")}
    return ids


def predict_shard(base: pathlib.Path, source: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    bm = json.load(open(base / "base_freeze_manifest.json")); verify_file_set(base, bm["payload"])
    sm = json.load(open(source / "source_freeze_manifest.json")); verify_file_set(source, sm["payload"])
    if bm.get("labels_read") != 0 or sm.get("labels_read") != 0 or sm.get("scorer_invoked") is not False:
        raise ShardError("offline prediction input lineage indicates label/scorer access")
    shard = int(sm["shard"]); start, end, tag = shard_bounds(shard)
    if int(sm["n"]) != SHARD_SIZE or int(sm["start"]) != start or int(sm["end_exclusive"]) != end:
        raise ShardError(f"{tag} source manifest range mismatch")
    cohort = json.load(open(base / "cohort_manifest.json"))["rows"]
    expected_rows = cohort[start:end]; expected_ids = [str(x["fixture_id"]) for x in expected_rows]
    if list(map(str, sm["fixture_ids"])) != expected_ids:
        raise ShardError(f"{tag} source fixture identity mismatch")
    packets = core.readjl(source / "pit_roster_packets.jsonl"); states = core.readjl(source / "offline_state_snapshots.jsonl")
    if len(packets) != SHARD_SIZE or len(states) != SHARD_SIZE:
        raise ShardError(f"{tag} source row count mismatch")
    pmap = {str(x["fixture_id"]): x for x in packets}; stmap = {str(x["fixture_id"]): x for x in states}
    if len(pmap) != SHARD_SIZE or len(stmap) != SHARD_SIZE or set(pmap) != set(expected_ids) or set(stmap) != set(expected_ids):
        raise ShardError(f"{tag} missing/duplicate/extra source rows")
    v2map = {str(x["fixture_id"]): x for x in core.readjl(base / "protected_v2_prediction_subset.jsonl")}
    lock = json.load(open(base / "v2_lock.json")); eng = cbd.engine()
    predictions: list[dict[str, Any]] = []; grade_n = Counter(); component_n = Counter(); model_active = Counter()
    identity_attempt = identity_matched = ability_attempt = ability_available = 0
    for idx, row in enumerate(expected_rows, 1):
        fid = str(row["fixture_id"]); packet = pmap[fid]; snapshot = stmap[fid]
        if str(packet["fixture_id"]) != fid or str(snapshot["fixture_id"]) != fid:
            raise ShardError(f"{tag} fixture binding mismatch {fid}")
        cutoff = str(row["prediction_cutoff_utc"]); vectors = _load_vectors(snapshot); usage = snapshot.get("usage") or {}
        grade = evidence_grade(packet, cutoff); grade_n[grade] += 1
        identity_attempt += int(packet.get("identity_attempt_n", 0)); identity_matched += int(packet.get("identity_matched_n", 0))
        relevant = _relevant_player_ids(packet); ability_attempt += len(relevant); ability_available += sum(pid in vectors for pid in relevant)
        vp = v2map.get(fid)
        if vp is None: raise ShardError(f"{tag} protected V2 subset missing {fid}")
        base_pred = cbd.pred(vp["v2_joint"]["score_matrix"], eng)
        old, bpred, cmpdiag = core.hist_comparators(base_pred, vectors, usage, str(row["home_team_id"]), str(row["away_team_id"]), cutoff, lock, eng)
        model_active["old_l1_l2"] += int(cmpdiag["old_active"]); model_active["candidate_b"] += int(cmpdiag["b_active"])
        ev_unc = UNCERTAINTY_BY_GRADE[grade]
        if not packet.get("pit_legal") or not vectors:
            c1 = zero_effect("C1", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
            c2 = zero_effect("C2", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
            c3 = zero_effect("C3", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
            c4 = zero_effect("C4", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
        else:
            c1 = c1_availability_replacement(vectors=vectors, home_team_id=str(row["home_team_id"]), away_team_id=str(row["away_team_id"]), status_records=packet.get("status_records") or [], evidence_uncertainty=ev_unc)
            c2 = c2_possible_xi(vectors=vectors, usage=usage, home_team_id=str(row["home_team_id"]), away_team_id=str(row["away_team_id"]), predicted_lineups=packet.get("predicted_lineups") or {}, cutoff=cutoff) if grade == "POSSIBLE_XI_PIT" else zero_effect("C2", "EVIDENCE_GRADE_NOT_POSSIBLE_XI", uncertainty=ev_unc)
            c3 = c3_confirmed_xi(vectors=vectors, usage=usage, home_team_id=str(row["home_team_id"]), away_team_id=str(row["away_team_id"]), confirmed_lineups=packet.get("confirmed_lineups"), cutoff=cutoff) if grade == "CONFIRMED_LINEUP_PIT" else zero_effect("C3", "EVIDENCE_GRADE_NOT_CONFIRMED_XI", uncertainty=ev_unc)
            c4 = c4_bench(vectors=vectors, home_team_id=str(row["home_team_id"]), away_team_id=str(row["away_team_id"]), bench=packet.get("bench"), evidence_uncertainty=ev_unc)
        for effect in (c1, c2, c3, c4): component_n[effect.component] += int(effect.active)
        lineup = c3 if c3.active else c2
        if lineup.active: full = combine_effects([lineup] + ([c4] if c4.active else []), grade=grade)
        else: full = combine_effects(([c1] if c1.active else []) + ([c4] if c4.active else []), grade=grade)
        fixed = uncertainty_only_effect(full, grade)
        c_orig = core.c_effect_pred(base_pred, full, lock, eng); c_hist = core.c_effect_pred(base_pred, fixed, lock, eng)
        model_active["candidate_c_original"] += int(full.active); model_active["candidate_c_historical"] += int(full.active)
        predictions.append({
            "fixture_id": fid, "kickoff_utc": row["kickoff_utc"], "cutoff_utc": cutoff,
            "home_team_id": row["home_team_id"], "away_team_id": row["away_team_id"], "home_team": row["home_team"], "away_team": row["away_team"],
            "round_index": row.get("round_index"), "shared_cold_start_bucket": row.get("shared_cold_start_bucket"), "research_status": "HISTORICAL_PIT_REPLAY",
            "evidence_grade": grade, "protected_v2": base_pred, "old_l1_l2": old, "candidate_b": bpred,
            "candidate_c_original": c_orig, "candidate_c_historical": c_hist,
            "components": {"C1": c1.to_dict(), "C2": c2.to_dict(), "C3": c3.to_dict(), "C4": c4.to_dict()},
            "candidate_c_full_effect": full.to_dict(), "candidate_c_historical_effect": fixed.to_dict(),
            "uncertainty_original": max(float(full.home.uncertainty), float(full.away.uncertainty)),
            "uncertainty_historical": max(float(fixed.home.uncertainty), float(fixed.away.uncertainty)),
            "probability_mass_supported": probability_mass_supported(packet), "probability_mass_redistribution_active": False,
            "packet_sha256": packet.get("packet_sha256") or core.canon(packet), "state_sha256": snapshot["state_sha256"],
            "understat_match_id": snapshot["understat_match_id"], "comparator_diagnostic": cmpdiag,
        })
        if idx % 10 == 0 or idx == SHARD_SIZE: print(f"[offline-predict {tag}] {idx}/50 fixture={fid} grade={grade} active={int(full.active)}", flush=True)
    if [str(x["fixture_id"]) for x in predictions] != expected_ids:
        raise ShardError(f"{tag} prediction order mismatch")
    core.writejl(out / "historical_pit_predictions.jsonl", predictions)
    payload_names = ["historical_pit_predictions.jsonl"]
    manifest = {
        "schema_version": "football3-historical-pit-prediction-shard-v1", "status": "HISTORICAL_PIT_REPLAY_PREDICTION_SHARD_FROZEN",
        "shard": shard, "tag": tag, "start": start, "end_exclusive": end, "n": SHARD_SIZE, "fixture_ids": expected_ids,
        "cohort_identity_sha256": bm["cohort_identity_sha256"], "source_packet_sha256": sm["packet_sha256"], "source_state_snapshot_sha256": sm["state_snapshot_sha256"],
        "prediction_sha256": sha_file(out / "historical_pit_predictions.jsonl"), "labels_read": 0, "scorer_invoked": False, "external_network_requests": 0,
        "evidence_grade_counts": {g: grade_n[g] for g in core.GRADE_ORDER}, "component_activation_n": {k: component_n[k] for k in ("C1", "C2", "C3", "C4")},
        "model_activation_n": dict(model_active), "candidate_c_fallback_n": SHARD_SIZE - model_active["candidate_c_historical"],
        "identity_attempt_n": identity_attempt, "identity_matched_n": identity_matched,
        "historical_capability_attempt_n": ability_attempt, "historical_capability_available_n": ability_available,
        "real_probability_mass_supported_n": sum(bool(x["probability_mass_supported"]) for x in predictions), "probability_mass_redistribution_active_n": 0,
        "payload": exact_files(out, payload_names),
    }
    core.dump(out / "prediction_shard_manifest.json", manifest); return manifest


def _discover_manifests(root: pathlib.Path, filename: str) -> dict[int, tuple[pathlib.Path, dict[str, Any]]]:
    found: dict[int, tuple[pathlib.Path, dict[str, Any]]] = {}
    for path in sorted(root.rglob(filename)):
        manifest = json.load(open(path)); shard = int(manifest["shard"])
        if shard in found: raise ShardError(f"duplicate {filename} shard {shard}")
        found[shard] = (path.parent, manifest)
    if set(found) != set(range(SHARD_N)): raise ShardError(f"{filename} shard set mismatch: {sorted(found)}")
    return found


def merge(base: pathlib.Path, sources: pathlib.Path, predictions: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True); bm = json.load(open(base / "base_freeze_manifest.json")); verify_file_set(base, bm["payload"])
    source_manifests = _discover_manifests(sources, "source_freeze_manifest.json"); pred_manifests = _discover_manifests(predictions, "prediction_shard_manifest.json")
    cohort = json.load(open(base / "cohort_manifest.json"))["rows"]; expected_ids = [str(x["fixture_id"]) for x in cohort]
    all_packets: list[dict[str, Any]] = []; all_predictions: list[dict[str, Any]] = []; source_receipts = []; pred_receipts = []
    grade_n = Counter(); component_n = Counter(); model_active = Counter(); identity_attempt = identity_matched = ability_attempt = ability_available = 0; real_mass = 0
    for shard in range(SHARD_N):
        start, end, tag = shard_bounds(shard); sroot, sm = source_manifests[shard]; proot, pm = pred_manifests[shard]
        verify_file_set(sroot, sm["payload"]); verify_file_set(proot, pm["payload"])
        shard_ids = expected_ids[start:end]
        if int(sm["n"]) != SHARD_SIZE or int(pm["n"]) != SHARD_SIZE or list(map(str, sm["fixture_ids"])) != shard_ids or list(map(str, pm["fixture_ids"])) != shard_ids:
            raise ShardError(f"{tag} exact 50 identity mismatch")
        if sm["cohort_identity_sha256"] != bm["cohort_identity_sha256"] or pm["cohort_identity_sha256"] != bm["cohort_identity_sha256"]:
            raise ShardError(f"{tag} cohort SHA mismatch")
        if pm["source_packet_sha256"] != sm["packet_sha256"] or pm["source_state_snapshot_sha256"] != sm["state_snapshot_sha256"]:
            raise ShardError(f"{tag} prediction/source lineage mismatch")
        if sm.get("labels_read") != 0 or pm.get("labels_read") != 0 or sm.get("scorer_invoked") is not False or pm.get("scorer_invoked") is not False or pm.get("external_network_requests") != 0:
            raise ShardError(f"{tag} pre-score separation violation")
        rows = core.readjl(proot / "historical_pit_predictions.jsonl"); packets = core.readjl(sroot / "pit_roster_packets.jsonl")
        if [str(x["fixture_id"]) for x in rows] != shard_ids or [str(x["fixture_id"]) for x in packets] != shard_ids:
            raise ShardError(f"{tag} payload identity/order mismatch")
        all_predictions.extend(rows); all_packets.extend(packets)
        for g, n in pm["evidence_grade_counts"].items(): grade_n[g] += int(n)
        for k, n in pm["component_activation_n"].items(): component_n[k] += int(n)
        for k, n in pm["model_activation_n"].items(): model_active[k] += int(n)
        identity_attempt += int(pm["identity_attempt_n"]); identity_matched += int(pm["identity_matched_n"])
        ability_attempt += int(pm["historical_capability_attempt_n"]); ability_available += int(pm["historical_capability_available_n"]); real_mass += int(pm["real_probability_mass_supported_n"])
        source_receipts.append({"shard": shard, "tag": tag, "fixture_ids": shard_ids, "packet_sha256": sm["packet_sha256"], "state_snapshot_sha256": sm["state_snapshot_sha256"], "manifest_sha256": sha_file(sroot / "source_freeze_manifest.json")})
        pred_receipts.append({"shard": shard, "tag": tag, "fixture_ids": shard_ids, "prediction_sha256": pm["prediction_sha256"], "manifest_sha256": sha_file(proot / "prediction_shard_manifest.json")})
    actual_ids = [str(x["fixture_id"]) for x in all_predictions]; packet_ids = [str(x["fixture_id"]) for x in all_packets]
    missing = sorted(set(expected_ids) - set(actual_ids)); extra = sorted(set(actual_ids) - set(expected_ids)); duplicate = sorted({x for x in actual_ids if actual_ids.count(x) > 1})
    if actual_ids != expected_ids or packet_ids != expected_ids or missing or extra or duplicate:
        raise ShardError("merged 300 missing/duplicate/extra/order violation")
    core.writejl(out / "historical_pit_predictions.jsonl", all_predictions); core.writejl(out / "pit_roster_packets.jsonl", all_packets)
    shutil.copy2(base / "cohort_manifest.json", out / "cohort_manifest.json"); shutil.copy2(base / "protected_v2_t15_equivalence.json", out / "protected_v2_t15_equivalence.json")
    core.dump(out / "source_shard_receipts.json", {"shards": source_receipts, "count": len(source_receipts), "rows_sha256": core.canon(source_receipts)})
    core.dump(out / "prediction_shard_receipts.json", {"shards": pred_receipts, "count": len(pred_receipts), "rows_sha256": core.canon(pred_receipts)})
    means = {}
    for grade in core.GRADE_ORDER:
        xs = [float(x["uncertainty_historical"]) for x in all_predictions if x["evidence_grade"] == grade]
        means[grade] = None if not xs else statistics.fmean(xs)
    nonempty = [(g, means[g]) for g in core.GRADE_ORDER if means[g] is not None]; mono = all(a <= b for (_, a), (_, b) in zip(nonempty, nonempty[1:]))
    pre = {
        "schema_version": "football3-historical-pit-replay-pre-score-v2-sharded", "status": "HISTORICAL_PIT_REPLAY_PREDICTIONS_FROZEN",
        "labels_read_in_prediction_phase": False, "scorer_invoked": False, "n": len(all_predictions), "source_shard_n": SHARD_N, "prediction_shard_n": SHARD_N,
        "cohort_identity_sha256": bm["cohort_identity_sha256"], "cohort_manifest_sha256": sha_file(out / "cohort_manifest.json"),
        "pit_roster_packet_sha256": sha_file(out / "pit_roster_packets.jsonl"), "prediction_sha256": sha_file(out / "historical_pit_predictions.jsonl"),
        "source_shard_receipts_sha256": sha_file(out / "source_shard_receipts.json"), "prediction_shard_receipts_sha256": sha_file(out / "prediction_shard_receipts.json"),
        "evidence_grade_counts": {g: grade_n[g] for g in core.GRADE_ORDER}, "component_activation_n": {k: component_n[k] for k in ("C1", "C2", "C3", "C4")},
        "model_activation_n": dict(model_active), "candidate_c_fallback_n": len(all_predictions) - model_active["candidate_c_historical"],
        "identity_attempt_n": identity_attempt, "identity_matched_n": identity_matched, "identity_match_rate": None if not identity_attempt else identity_matched / identity_attempt,
        "historical_capability_attempt_n": ability_attempt, "historical_capability_available_n": ability_available, "historical_capability_coverage_rate": None if not ability_attempt else ability_available / ability_attempt,
        "uncertainty_mean_by_grade": means, "uncertainty_monotonicity_observed": mono, "uncertainty_contract_monotonic": monotonic_contract_holds(),
        "real_probability_mass_supported_n": real_mass, "probability_mass_redistribution_active_n": 0, "new_or_target_labels_read_n": 0,
        "api_football_requests": 0, "api_keys_or_secrets_used": 0, "eightbo_used_in_translator": False, "coach_feature_used": False,
        "formal_weight": 0, "formal_promotion_eligible": False, "old_timeout_status": bm["old_timeout_status"],
    }
    core.dump(out / "pre_score_manifest.json", pre); core.dump(out / "candidate_c_historical_contract.json", historical_uncertainty_contract())
    core.dump(out / "historical_pit_gate.json", {"schema_version": "football3-historical-pit-replay-gate-v2-sharded", "pipeline_integrity": "PASS", "status": "HISTORICAL_PIT_REPLAY_PREDICTIONS_FROZEN", "prediction_sha256": pre["prediction_sha256"], "cohort_identity_sha256": pre["cohort_identity_sha256"], "labels_read_in_prediction_phase": False, "scorer_invoked": False, "formal_weight": 0})
    integrity = {"schema_version": "football3-historical-pit-merge-integrity-v1", "n": len(actual_ids), "missing_n": len(missing), "duplicate_n": len(duplicate), "extra_n": len(extra), "missing": missing, "duplicate": duplicate, "extra": extra, "order_exact": actual_ids == expected_ids, "source_shard_n": len(source_receipts), "prediction_shard_n": len(pred_receipts)}
    core.dump(out / "merge_integrity_report.json", integrity)
    payload_names = ["cohort_manifest.json", "protected_v2_t15_equivalence.json", "historical_pit_predictions.jsonl", "pit_roster_packets.jsonl", "source_shard_receipts.json", "prediction_shard_receipts.json", "pre_score_manifest.json", "candidate_c_historical_contract.json", "historical_pit_gate.json", "merge_integrity_report.json"]
    core.dump(out / "merged_prediction_manifest.json", {"schema_version": "football3-historical-pit-merged-prediction-v1", "status": "HISTORICAL_PIT_REPLAY_PREDICTIONS_FROZEN", "n": core.COHORT_N, "cohort_identity_sha256": pre["cohort_identity_sha256"], "prediction_sha256": pre["prediction_sha256"], "source_shard_n": SHARD_N, "prediction_shard_n": SHARD_N, "labels_read": 0, "scorer_invoked": False, "payload": exact_files(out, payload_names)})
    return pre


def score_final(merged: pathlib.Path, label_vault: pathlib.Path, out: pathlib.Path, *, head: str, parent: str, run_id: int, changed_paths: list[str]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True); mm = json.load(open(merged / "merged_prediction_manifest.json")); verify_file_set(merged, mm["payload"])
    if mm.get("labels_read") != 0 or mm.get("scorer_invoked") is not False: raise ShardError("merged prediction freeze already touched labels/scorer")
    for name in mm["payload"]: shutil.copy2(merged / name, out / name)
    if sha_file(out / "historical_pit_predictions.jsonl") != mm["prediction_sha256"]: raise ShardError("total prediction SHA changed before scorer")
    result = core.score(pathlib.Path("."), label_vault, out)
    if result["status"] not in {"HISTORICAL_PIT_CANDIDATE_PASSED", "HISTORICAL_PIT_NOT_PROMOTED"}: raise ShardError(f"unexpected scorer status {result['status']}")
    core.dump(out / "label_access_receipt.json", {"schema_version": "football3-historical-pit-label-access-v2-sharded", "prediction_phase_label_reads": 0, "historical_selected_cohort_labels_scored_after_total_prediction_freeze_n": core.COHORT_N, "selection_changed_after_label_access": False, "scoring_only": True, "scorer_network_requests": 0})
    pre = json.load(open(out / "pre_score_manifest.json")); payload_names = ["cohort_manifest.json", "protected_v2_t15_equivalence.json", "historical_pit_predictions.jsonl", "pit_roster_packets.jsonl", "source_shard_receipts.json", "prediction_shard_receipts.json", "pre_score_manifest.json", "candidate_c_historical_contract.json", "merge_integrity_report.json", "historical_pit_score.json", "historical_pit_gate.json", "label_access_receipt.json"]
    manifest = {
        "schema_version": "football3-context-translator-historical-pit-replay-artifact-v2-sharded", "status": result["status"],
        "scientific_claim": "HISTORICAL_PIT_REPLAY_ONLY_NOT_FUTURE_PROSPECTIVE_NOT_NEW_BLIND_TEST", "branch": "football3/context-translator-historical-pit-replay-v1",
        "base_head": core.BASE_HEAD, "head": head, "parent": parent, "run_id": int(run_id), "n": core.COHORT_N, "full_season_n": core.FULL_SEASON_N, "league": core.LEAGUE, "season": core.SEASON,
        "prediction_cutoff": "kickoff_minus_15_minutes", "cohort_identity_sha256": pre["cohort_identity_sha256"], "total_prediction_sha256": pre["prediction_sha256"],
        "source_shard_n": SHARD_N, "prediction_shard_n": SHARD_N, "evidence_grade_counts": pre["evidence_grade_counts"], "component_activation_n": pre["component_activation_n"],
        "model_activation_n": pre["model_activation_n"], "fallback_n": pre["candidate_c_fallback_n"], "identity_match_rate": pre["identity_match_rate"],
        "historical_capability_coverage_rate": pre["historical_capability_coverage_rate"], "uncertainty_monotonicity": pre["uncertainty_monotonicity_observed"],
        "old_timeout_status": pre["old_timeout_status"], "old_timeout_run_id": 33485502884, "old_timeout_model_pass_fail_claim": None,
        "prediction_phase_label_reads": 0, "historical_selected_cohort_labels_scored_after_prediction_freeze_n": core.COHORT_N,
        "api_football_requests": 0, "api_keys_or_secrets_used": 0, "global_fixture_consumption_registry_extended": False,
        "protected_v2_modified": False, "candidate_c_original_modified": False, "main_modified": False, "current_modified": False, "airtable_modified": False, "pr334_modified": False, "r5_modified": False,
        "formal_weight": 0, "formal_promotion_eligible": False, "formal_enablement": False, "force_used": False, "merge_used": False, "ready_used": False,
        "changed_paths": sorted(changed_paths), "payload": exact_files(out, payload_names),
    }
    core.dump(out / "artifact_manifest.json", manifest); return manifest
