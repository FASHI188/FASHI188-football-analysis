#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Install the governed source-contract/quarantine stack before reading frozen xG labels.
import entry as installed_gateway_stack  # noqa: F401
import formal_state_integrity_full_rebuild_v1 as integrity_full
import permanent_team_identity_bridge_v1 as bridge
import runtime as rt
import target_identity_replay_fix_v1 as identity_diag

SCHEMA = "football3-formal-state-integrity-regression-v1"
COMPS = ("GER_Bundesliga", "ENG_PremierLeague", "ITA_SerieA")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def fixture_obj(f) -> dict[str, Any]:
    return {
        "fixture_id": f.fixture_id,
        "competition_id": f.competition_id,
        "season": f.season,
        "kickoff": f.kickoff.isoformat(),
        "home_team_id": f.home_team_id,
        "away_team_id": f.away_team_id,
        "home_team_name": f.home_team_name,
        "away_team_name": f.away_team_name,
    }


def source_set_sha(delta: list[dict[str, Any]]) -> str:
    vals = sorted({str(r.get("source_content_sha256") or "") for r in delta if str(r.get("source_content_sha256") or "")})
    return rt._sha_bytes(rt._canon_bytes(vals))


def build_input(history, labels, target, lower, cutoff) -> dict[str, Any]:
    delta = rt.history_delta_events(history, labels, lower, cutoff, target.fixture_id)
    return {
        "schema_version": rt.INPUT_SCHEMA,
        "fixture": fixture_obj(target),
        "cutoff": cutoff.isoformat(),
        "delta_coverage": {
            "schema_version": rt.DELTA_SCHEMA,
            "status": "COMPLETE",
            "verification": "VERIFIED_COMPLETE",
            "v1_status": "COMPLETE",
            "xg_status": "COMPLETE",
            "from": lower.isoformat(),
            "to": cutoff.isoformat(),
            "records_sha256": rt._sha_bytes(rt._canon_bytes(delta)),
            "source_set_sha256": source_set_sha(delta) or ("0" * 64),
        },
        "model_delta": delta,
    }


def select_regular(history, comp: str):
    prev_ids = {
        x for f in history if f.competition_id == comp and f.season == "2024/25"
        for x in (f.home_team_id, f.away_team_id)
    }
    candidates = [
        f for f in history
        if f.competition_id == comp and f.season == "2025/26"
        and datetime(2026, 2, 1, tzinfo=timezone.utc) <= f.kickoff <= datetime(2026, 4, 15, tzinfo=timezone.utc)
        and f.home_team_id in prev_ids and f.away_team_id in prev_ids
    ]
    if not candidates:
        raise rt.RuntimeGateError(f"regular historical regression fixture unavailable: {comp}")
    return sorted(candidates, key=lambda f: (f.kickoff, f.fixture_id))[0]


def select_promoted(history):
    comp = "ENG_PremierLeague"
    prior_any = {
        x for f in history
        if f.competition_id == comp and f.season in {"2022/23", "2023/24", "2024/25"}
        for x in (f.home_team_id, f.away_team_id)
    }
    prev = {
        x for f in history if f.competition_id == comp and f.season == "2024/25"
        for x in (f.home_team_id, f.away_team_id)
    }
    candidates = []
    for f in history:
        if f.competition_id != comp or f.season != "2025/26":
            continue
        new_ids = [x for x in (f.home_team_id, f.away_team_id) if x not in prev]
        if not new_ids:
            continue
        never_seen = [x for x in new_ids if x not in prior_any]
        if never_seen:
            candidates.append((f, never_seen[0]))
    if not candidates:
        raise rt.RuntimeGateError("promoted-team historical regression fixture unavailable")
    candidates.sort(key=lambda x: (x[0].kickoff, x[0].fixture_id))
    return candidates[0]


def run_case(repo_root: Path, understat: Path, confirmation: Path, history, labels, target, tag: str, expect_promoted_id: str | None = None) -> dict[str, Any]:
    cutoff = target.kickoff - timedelta(hours=1)
    lower = cutoff - timedelta(days=21, minutes=17)
    if cutoff > rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base cutoff"):
        raise rt.RuntimeGateError("historical regression target exceeds frozen base cutoff")

    lower_state, source, identity = rt.build_production_state_at_cutoff(repo_root, understat, confirmation, lower)
    with tempfile.TemporaryDirectory(prefix=f"football3-{tag}-") as td:
        root = Path(td)
        bundle = root / "bundle"
        rt.seal_bundle(lower_state, bundle, source, identity, lower.isoformat(), "REGRESSION_FAST_BASE")
        inp = build_input(history, labels, target, lower, cutoff)
        input_path = root / "input.json"
        write_json(input_path, inp)

        receipt = rt.predict_match(
            target.competition_id, target.home_team_name, target.away_team_name,
            target.kickoff, cutoff, bundle, input_path,
            repo_root, understat, confirmation, True,
        )
        if not receipt.get("equivalence_sample") or receipt["equivalence_sample"].get("passed") is not True:
            raise rt.RuntimeGateError(f"FAST/FULL numerical equivalence failed: {tag}")

        full_state, _, _ = rt.build_production_state_at_cutoff(repo_root, understat, confirmation, cutoff)
        f = rt.hxg.FixtureRow(
            target.fixture_id, target.competition_id, target.season, target.kickoff,
            target.home_team_id, target.away_team_id, target.home_team_name, target.away_team_name,
        )
        full_pred = rt._prediction_from_state(full_state, f)
        fast_matrix = receipt["score_matrix"]
        full_matrix = full_pred["row"]["prediction"]["score_matrix"]
        matrix_byte_equal = rt._canon_bytes(fast_matrix) == rt._canon_bytes(full_matrix)
        prob_byte_equal = rt._canon_bytes({
            "p_home": receipt["p_home"], "p_draw": receipt["p_draw"], "p_away": receipt["p_away"]
        }) == rt._canon_bytes({
            "p_home": full_pred["row"]["prediction"]["p_home"],
            "p_draw": full_pred["row"]["prediction"]["p_draw"],
            "p_away": full_pred["row"]["prediction"]["p_away"],
        })
        if len(fast_matrix) != 225 or not matrix_byte_equal or not prob_byte_equal:
            raise rt.RuntimeGateError(
                f"FAST/FULL byte equivalence failed: {tag} cells={len(fast_matrix)} matrix={matrix_byte_equal} prob={prob_byte_equal}"
            )

        promoted = None
        frozen_v1_exact = None
        if expect_promoted_id is not None:
            side_name = target.home_team_name if target.home_team_id == expect_promoted_id else target.away_team_name
            resolved = bridge.resolve_team(repo_root, full_state, target.competition_id, target.season, side_name)
            if not str(resolved["season_relation"]).startswith("PROMOTED_"):
                raise rt.RuntimeGateError(f"promoted team did not take promoted path: {resolved}")
            promoted = resolved
            if receipt.get("fallback_exact_v1") is not True:
                raise rt.RuntimeGateError("promoted no-prior-history case did not take legal V1 fallback")
            vf = rt.v1_engine.Fixture(
                target.fixture_id, target.competition_id, target.season, target.kickoff,
                target.home_team_id, target.away_team_id,
            )
            v1 = full_state.base.predict(vf)
            frozen_v1_exact = (
                rt._canon_bytes(v1["score_matrix"]) == rt._canon_bytes(receipt["score_matrix"])
                and rt._canon_bytes({
                    "p_home": v1["p_home"], "p_draw": v1["p_draw"], "p_away": v1["p_away"]
                }) == rt._canon_bytes({
                    "p_home": receipt["p_home"], "p_draw": receipt["p_draw"], "p_away": receipt["p_away"]
                })
            )
            if not frozen_v1_exact:
                raise rt.RuntimeGateError("legal fallback not byte-identical to Frozen V1")

        return {
            "tag": tag,
            "competition_id": target.competition_id,
            "season": target.season,
            "fixture_id": target.fixture_id,
            "home": target.home_team_name,
            "away": target.away_team_name,
            "kickoff": target.kickoff.isoformat(),
            "cutoff": cutoff.isoformat(),
            "fast_path": receipt["calculation_path"],
            "model_route": receipt["model_route"],
            "fallback_exact_v1": receipt["fallback_exact_v1"],
            "matrix_cells": len(fast_matrix),
            "fast_full_matrix_byte_equal": matrix_byte_equal,
            "fast_full_probability_byte_equal": prob_byte_equal,
            "equivalence_sample": receipt["equivalence_sample"],
            "promoted_team": promoted,
            "legal_fallback_equals_frozen_v1": frozen_v1_exact,
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()
    understat = Path(args.understat_db).resolve()
    confirmation = Path(args.confirmation_dir).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    history, _ = rt.load_frozen_v1_history(repo_root)
    labels, _ = rt.load_xg_labels(history, understat, confirmation)

    # Cross-season FULL rebuild regression at the frozen Stuttgart target cutoff.
    integrity_cutoff = rt._parse_dt("2026-09-04T00:25:59+00:00", "integrity cutoff")
    with tempfile.TemporaryDirectory(prefix="football3-integrity-full-") as td:
        rebuilt = integrity_full.build_integrity_base(
            Path(td) / "bundle", repo_root, understat, confirmation, integrity_cutoff
        )
        cov = rebuilt["coverage"]
        for comp in COMPS:
            spec = cov["files"][comp]
            if not spec["eligible_for_target"] or spec["joined_rows"] != spec["formal_rows"] or spec["joined_rows"] <= 0:
                raise rt.RuntimeGateError(f"cross-season FULL coverage failed: {comp} {spec}")
        ko = rt._parse_dt("2026-09-04T18:30:00+00:00", "stuttgart kickoff")
        fixture, ident = bridge.resolve_fixture(
            repo_root, rebuilt["loaded"]["state"], "GER_Bundesliga", "2026/27",
            "VfB Stuttgart", "FC Koln", ko,
        )
        trigger = identity_diag._xg_trigger_audit(rebuilt["loaded"]["state"], fixture)
        ev = [float(x) for x in trigger["dynamic"]["evidence"]]
        if min(ev) < 3.0:
            raise rt.RuntimeGateError(f"cross-season FULL rebuild still has insufficient Stuttgart/Koln xg: {ev}")
        cross_season = {
            "status": "PASS",
            "cutoff": integrity_cutoff.isoformat(),
            "coverage": {c: cov["files"][c] for c in COMPS},
            "stuttgart_koln_identity": ident,
            "stuttgart_koln_xg_evidence": ev,
            "v1_seen_n": rebuilt["v1_seen_n"],
            "xg_seen_n": rebuilt["xg_seen_n"],
        }

    cases = []
    for comp in COMPS:
        target = select_regular(history, comp)
        cases.append(run_case(repo_root, understat, confirmation, history, labels, target, comp, None))
    promoted_target, promoted_id = select_promoted(history)
    cases.append(run_case(repo_root, understat, confirmation, history, labels, promoted_target, "PROMOTED", promoted_id))

    summary = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "case_n": len(cases),
        "required_competitions": list(COMPS),
        "promoted_case_present": True,
        "all_fast_full_matrix_byte_equal": all(x["fast_full_matrix_byte_equal"] for x in cases),
        "all_fast_full_probability_byte_equal": all(x["fast_full_probability_byte_equal"] for x in cases),
        "all_matrices_225_cells": all(x["matrix_cells"] == 225 for x in cases),
        "legal_fallback_frozen_v1_exact": cases[-1]["legal_fallback_equals_frozen_v1"],
        "cases": cases,
        "cross_season_full_rebuild": cross_season,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    if summary["case_n"] < 4 or not all([
        summary["all_fast_full_matrix_byte_equal"],
        summary["all_fast_full_probability_byte_equal"],
        summary["all_matrices_225_cells"],
        summary["legal_fallback_frozen_v1_exact"] is True,
    ]):
        raise rt.RuntimeGateError(f"regression gate failed: {summary}")
    write_json(out / "formal_state_integrity_regression.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
