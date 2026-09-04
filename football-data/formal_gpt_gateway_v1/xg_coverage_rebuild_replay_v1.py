#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import live_gateway_patch_v1 as live_gateway
import runtime as rt
import target_identity_replay_fix_v1 as identity_fix

SCHEMA = "football3-xg-coverage-rebuild-replay-v1"
COMPETITIONS = (
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
)
EXPECTED_ORIGINAL_SOURCE_SET_SHA = "de20070f7fc5c49f5b7c45e0b6c028c0087d3c15cb6861b67fb57f0d626eac63"
EXPECTED_ORIGINAL_RECORDS_SHA = "6a02bb78f2bf453a67d2614b593e6860fcaf1968f964ba5b50fde6e9f86ef8e2"
ORIGINAL_CUTOFF = "2026-09-04T00:25:59+00:00"
ORIGINAL_REQUESTED = "2026-09-04T17:30:00+00:00"
TARGET_KICKOFF = "2026-09-04T18:30:00+00:00"


def canon(obj: Any) -> bytes:
    return rt._canon_bytes(obj)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canon(obj))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            if type(obj) is not dict:
                raise rt.RuntimeGateError(f"non-object xg row: {path}")
            rows.append(obj)
    return rows


def formal_key(comp: str, date: str, home: str, away: str) -> tuple[str, str, str, str]:
    return comp, date, rt._normalize_team(home), rt._normalize_team(away)


def augment_2025_26_labels(repo_root: Path, history, labels: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    index: dict[tuple[str, str, str, str], Any] = {}
    expected_by_comp: dict[str, int] = {}
    for f in history:
        if f.season != "2025/26" or f.competition_id not in COMPETITIONS:
            continue
        k = formal_key(f.competition_id, f.kickoff.date().isoformat(), f.home_team_name, f.away_team_name)
        if k in index:
            raise rt.RuntimeGateError(f"duplicate formal 2025/26 fixture key: {k}")
        index[k] = f
        expected_by_comp[f.competition_id] = expected_by_comp.get(f.competition_id, 0) + 1

    out = dict(labels)
    files = {}
    linked_total = 0
    unmatched = []
    for comp in COMPETITIONS:
        path = repo_root / "football-data" / "evidence" / "xg" / "understat_2025_26_linked" / f"{comp}.jsonl"
        if not path.is_file():
            raise rt.RuntimeGateError(f"missing frozen 2025/26 linked xg: {path}")
        source_sha = rt._sha_file(path)
        rows = load_jsonl(path)
        used = 0
        observed = set()
        for row in rows:
            if str(row.get("competition_id")) != comp or str(row.get("season")) != "2025/26":
                raise rt.RuntimeGateError(f"linked xg scope mismatch: {comp}")
            if row.get("target_match_xg_allowed_as_predictor") is not False:
                raise rt.RuntimeGateError("retrospective xg target-use guard missing")
            if str(row.get("source_role")) != "RETROSPECTIVE_MATCH_LEVEL_XG":
                raise rt.RuntimeGateError("retrospective xg source role mismatch")
            observed.add(str(row.get("source_observed_at_utc") or ""))
            k = formal_key(comp, str(row.get("official_date") or ""), str(row.get("official_home_team") or ""), str(row.get("official_away_team") or ""))
            f = index.get(k)
            if f is None:
                unmatched.append(k)
                continue
            if f.fixture_id in out:
                raise rt.RuntimeGateError(f"2025/26 xg overlaps pre-existing formal label: {f.fixture_id}")
            hg = int(row["home_goals"]); ag = int(row["away_goals"])
            if (hg, ag) != (f.home_goals, f.away_goals):
                raise rt.RuntimeGateError(f"2025/26 xg/formal result conflict: {f.fixture_id}")
            source_dt = datetime.fromisoformat(str(row["match_datetime_source"])).replace(tzinfo=timezone.utc)
            release = source_dt + timedelta(hours=3)
            if release >= rt._parse_dt(ORIGINAL_CUTOFF, "target cutoff"):
                raise rt.RuntimeGateError("historical xg release not strictly pretarget")
            lab = rt.hxg.ReleasedLabel(hg, ag, float(row["home_xg"]), float(row["away_xg"]), release)
            out[f.fixture_id] = rt.XGLabel(lab, f"understat-retro:{row.get('understat_match_id')}", source_sha, release.isoformat())
            used += 1
            linked_total += 1
        files[comp] = {
            "path": str(path.relative_to(repo_root)),
            "sha256": source_sha,
            "linked_rows": len(rows),
            "joined_rows": used,
            "formal_2025_26_rows": expected_by_comp.get(comp, 0),
            "source_observed_at_utc": sorted(observed),
            "formal_pit_eligible_for_2025_26_targets": False,
            "allowed_role_here": "lagged historical feature known before 2026-09-04 target",
        }

    if unmatched:
        raise rt.RuntimeGateError(f"2025/26 linked xg has unmatched formal fixtures: {len(unmatched)} sample={unmatched[:5]}")
    ger = files["GER_Bundesliga"]
    if ger["joined_rows"] != 306 or ger["formal_2025_26_rows"] != 306:
        raise rt.RuntimeGateError(f"GER 2025/26 coverage is not exact 306/306: {ger}")
    audit = {
        "schema_version": "football3-pretarget-retrospective-xg-coverage-v1",
        "status": "PASS",
        "season": "2025/26",
        "files": files,
        "added_xg_labels": linked_total,
        "preexisting_formal_xg_labels": len(labels),
        "augmented_formal_xg_labels": len(out),
        "target_cutoff": ORIGINAL_CUTOFF,
        "target_match_xg_used": False,
        "post_target_match_data_used": False,
        "model_parameters_or_weights_changed": False,
    }
    return out, audit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    understat_db = Path(args.understat_db).resolve()
    confirmation_dir = Path(args.confirmation_dir).resolve()
    state_root = Path(args.state_root).resolve()
    bundle = state_root / "bundle"
    out_dir = Path(args.out).resolve(); out_dir.mkdir(parents=True, exist_ok=True)

    history, v1_source = rt.load_frozen_v1_history(repo_root)
    old_labels, old_xg_source = rt.load_xg_labels(history, understat_db, confirmation_dir)
    labels, coverage = augment_2025_26_labels(repo_root, history, old_labels)
    write_json(out_dir / "xg_coverage_repair_audit.json", coverage)

    base_cutoff = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base cutoff")
    state, replay = rt.replay_history_state(history, labels, base_cutoff)
    if len(state.base.seen_fixtures) != rt.EXPECTED_V1_N:
        raise rt.RuntimeGateError("V1 cardinality changed during xg-only coverage repair")
    if len(state.seen) != len(labels):
        raise rt.RuntimeGateError(f"repaired xg replay cardinality mismatch: seen={len(state.seen)} labels={len(labels)}")
    source = {
        "v1": v1_source,
        "xg_original_frozen": old_xg_source,
        "xg_2025_26_pretarget_lagged_repair": coverage,
        "replay": replay,
        "source_scope": "FROZEN_FORMAL_HISTORY_PLUS_PRETARGET_LAGGED_XG_COVERAGE_REPAIR",
        "repair_observation_policy": "2025/26 retrospective xG was persisted 2026-07-21 before target; used only as lagged historical state, never as target-match predictor",
    }
    identity = rt._production_identity()
    identity["xg_2025_26_coverage_repair"] = {
        "identity_bridge": "persisted linked official fixture identities only",
        "result_or_xg_used_to_select_target_identity": False,
        "model_parameters_or_weights_changed": False,
    }
    base_manifest = rt.seal_bundle(state, bundle, source, identity, rt.BASE_HISTORY_CUTOFF, "PRETARGET_XG_COVERAGE_REPAIR_BASE")
    checked_base = rt.validate_bundle(bundle)
    write_json(out_dir / "xg_coverage_base_build.json", {
        "schema_version": SCHEMA,
        "status": "PASS",
        "historical_cutoff": checked_base["meta"]["historical_cutoff"],
        "v1_seen_n": len(checked_base["state"].base.seen_fixtures),
        "xg_seen_n": len(checked_base["state"].seen),
        "xg_pending_n": len(checked_base["state"].pending),
        "state_bundle_sha256": base_manifest["state_bundle_sha256"],
        "state_sha256": base_manifest["state_sha256"],
    })

    kickoff = rt._parse_dt(TARGET_KICKOFF, "target kickoff")
    target_cutoff = rt._parse_dt(ORIGINAL_CUTOFF, "target cutoff")
    comp = "GER_Bundesliga"; season = "2026/27"; home = "VfB Stuttgart"; away = "FC Koln"
    target_fid = rt._fixture_id(comp, season, kickoff, home, away)
    live_result = live_gateway._acquire_apply_and_seal(bundle, target_cutoff, target_fid, repo_root)
    report = live_result["report"]
    if report.get("source_set_sha256") != EXPECTED_ORIGINAL_SOURCE_SET_SHA:
        raise rt.RuntimeGateError(f"live source bytes drifted from original frozen run: {report.get('source_set_sha256')}")
    if report.get("records_sha256") != EXPECTED_ORIGINAL_RECORDS_SHA:
        raise rt.RuntimeGateError(f"live event set drifted from original frozen run: {report.get('records_sha256')}")
    if int(report.get("records", -1)) != 903:
        raise rt.RuntimeGateError("original live delta record count mismatch")
    if live_result["effective"] != target_cutoff:
        raise rt.RuntimeGateError(f"effective cutoff drifted: {live_result['effective'].isoformat()}")
    write_json(out_dir / "original_live_delta_exact_reuse_proof.json", {
        "schema_version": "football3-original-live-delta-exact-reuse-proof-v1",
        "status": "PASS",
        "original_run_id": 33821690846,
        "source_set_sha256": report["source_set_sha256"],
        "records_sha256": report["records_sha256"],
        "records": report["records"],
        "effective_cutoff": live_result["effective"].isoformat(),
        "byte_and_event_identity_to_original_run": True,
        "post_cutoff_events_applied": False,
    })
    write_json(out_dir / "live_acquisition_report.json", report)
    write_json(out_dir / "live_state_apply.json", {
        "apply": live_result["stats"],
        "state_bundle_sha": live_result["manifest"]["state_bundle_sha256"],
        "state_sha": live_result["manifest"]["state_sha256"],
        "effective_cutoff": live_result["effective"].isoformat(),
    })

    checked = live_result["checked"]
    fixture, identity_audit = identity_fix._resolved_fixture(repo_root, checked["state"], comp, season, home, away, kickoff)
    write_json(out_dir / "target_identity_audit.json", identity_audit)
    trigger = identity_fix._xg_trigger_audit(checked["state"], fixture)
    write_json(out_dir / "xg_trigger_audit.json", trigger)
    inp = identity_fix._sealed_replay_input(fixture, target_cutoff, checked)
    input_path = out_dir / "runtime_input.json"; write_json(input_path, inp)
    receipt = rt.predict_match(comp, home, away, kickoff, target_cutoff, bundle, input_path, repo_root, understat_db, confirmation_dir, False)
    write_json(out_dir / "prediction_receipt.json", receipt)

    if receipt.get("fallback_exact_v1") is not False:
        raise rt.RuntimeGateError(f"xg coverage repair did not clear fallback: {trigger}")
    if receipt.get("model_route") == "FROZEN_V1_EXACT_FALLBACK":
        raise rt.RuntimeGateError("receipt remained on V1 exact fallback")

    summary = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "fixture": fixture,
        "requested_ceiling_original": ORIGINAL_REQUESTED,
        "cutoff": target_cutoff.isoformat(),
        "gateway_route": "REBUILT_PRETARGET_XG_COVERAGE_PLUS_ORIGINAL_LIVE_DELTA_EXACT_REUSE",
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
        "original_live_source_set_sha256": report["source_set_sha256"],
        "original_live_records_sha256": report["records_sha256"],
        "original_live_records": report["records"],
        "target_match_xg_used": False,
        "post_target_information_used": False,
        "model_parameters_or_weights_changed": False,
        "fusion_weights": {"xg": 0.75, "frozen_v1": 0.25},
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
