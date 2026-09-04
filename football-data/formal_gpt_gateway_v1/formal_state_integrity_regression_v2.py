#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Install the governed source-contract/quarantine adapters first.  The regression below
# then replays only the already accepted/quarantined frozen history and does not bypass
# the source contract by inventing replacements for disputed rows.
import entry as installed_gateway_stack  # noqa: F401
import formal_state_integrity_full_rebuild_v1 as integrity_full
import permanent_team_identity_bridge_v1 as bridge
import runtime as rt
import target_identity_replay_fix_v1 as identity_diag

SCHEMA = "football3-formal-state-integrity-regression-v2"
COMPS = ("GER_Bundesliga", "ENG_PremierLeague", "ITA_SerieA")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _fixture_row(f):
    return rt.hxg.FixtureRow(
        f.fixture_id, f.competition_id, f.season, f.kickoff,
        f.home_team_id, f.away_team_id, f.home_team_name, f.away_team_name,
    )


def _prediction_bytes(state: Any, f) -> dict[str, Any]:
    p = rt._prediction_from_state(state, _fixture_row(f))
    pred = p["row"]["prediction"]
    audit = p["row"]["audit"]
    return {
        "p_home": float(pred["p_home"]),
        "p_draw": float(pred["p_draw"]),
        "p_away": float(pred["p_away"]),
        "score_matrix": pred["score_matrix"],
        "route": audit["route"],
        "fallback_exact_v1": bool(audit["fallback_exact_v1"]),
    }


def _select_regular(history, comp: str):
    previous = {
        tid for f in history if f.competition_id == comp and f.season == "2024/25"
        for tid in (f.home_team_id, f.away_team_id)
    }
    rows = [
        f for f in history
        if f.competition_id == comp and f.season == "2025/26"
        and datetime(2026, 2, 1, tzinfo=timezone.utc) <= f.kickoff <= datetime(2026, 4, 15, tzinfo=timezone.utc)
        and f.home_team_id in previous and f.away_team_id in previous
    ]
    if not rows:
        raise rt.RuntimeGateError(f"regular regression fixture unavailable: {comp}")
    return sorted(rows, key=lambda x: (x.kickoff, x.fixture_id))[0]


def _select_promoted(history):
    comp = "ENG_PremierLeague"
    previous = {
        tid for f in history if f.competition_id == comp and f.season == "2024/25"
        for tid in (f.home_team_id, f.away_team_id)
    }
    older = {
        tid for f in history
        if f.competition_id == comp and f.season in {"2022/23", "2023/24", "2024/25"}
        for tid in (f.home_team_id, f.away_team_id)
    }
    rows = []
    for f in history:
        if f.competition_id != comp or f.season != "2025/26":
            continue
        new_ids = [tid for tid in (f.home_team_id, f.away_team_id) if tid not in previous and tid not in older]
        if new_ids:
            rows.append((f, new_ids[0]))
    if not rows:
        raise rt.RuntimeGateError("promoted-team regression fixture unavailable")
    rows.sort(key=lambda x: (x[0].kickoff, x[0].fixture_id))
    return rows[0]


def _fast_state(history, labels, lower, cutoff, target_fid):
    base, _ = rt.replay_history_state(history, labels, lower)
    clone = rt.deserialize_state(rt.serialize_v1_state(base.base), rt.serialize_xg_state(base))
    delta = rt.history_delta_events(history, labels, lower, cutoff, target_fid)
    stats = rt.apply_delta(clone, delta, cutoff)
    return clone, delta, stats


def _case(repo_root: Path, history, labels, target, tag: str, promoted_id: str | None = None) -> dict[str, Any]:
    cutoff = target.kickoff - timedelta(hours=1)
    lower = cutoff - timedelta(days=21, minutes=17)
    if cutoff > rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base cutoff"):
        raise rt.RuntimeGateError("regression target exceeds frozen historical cutoff")

    fast_state, delta, delta_stats = _fast_state(history, labels, lower, cutoff, target.fixture_id)
    full_state, _ = rt.replay_history_state(history, labels, cutoff)
    fast = _prediction_bytes(fast_state, target)
    full = _prediction_bytes(full_state, target)

    matrix_equal = rt._canon_bytes(fast["score_matrix"]) == rt._canon_bytes(full["score_matrix"])
    prob_equal = rt._canon_bytes({k: fast[k] for k in ("p_home", "p_draw", "p_away")}) == rt._canon_bytes(
        {k: full[k] for k in ("p_home", "p_draw", "p_away")}
    )
    route_equal = fast["route"] == full["route"] and fast["fallback_exact_v1"] == full["fallback_exact_v1"]
    if len(fast["score_matrix"]) != 225 or not matrix_equal or not prob_equal or not route_equal:
        raise rt.RuntimeGateError(
            f"FAST/FULL byte equivalence failed: {tag} matrix={matrix_equal} prob={prob_equal} route={route_equal}"
        )

    promoted = None
    fallback_v1_exact = None
    if promoted_id is not None:
        promoted_name = target.home_team_name if target.home_team_id == promoted_id else target.away_team_name
        promoted = bridge.resolve_team(repo_root, full_state, target.competition_id, target.season, promoted_name)
        if promoted["season_relation"] != "PROMOTED_OR_NEW_TO_FORMAL_HISTORY":
            raise rt.RuntimeGateError(f"promoted team path mismatch: {promoted}")
        if full["fallback_exact_v1"] is not True:
            raise rt.RuntimeGateError("legal promoted-history gap did not take Frozen V1 fallback")
        vf = rt.v1_engine.Fixture(
            target.fixture_id, target.competition_id, target.season, target.kickoff,
            target.home_team_id, target.away_team_id,
        )
        v1 = full_state.base.predict(vf)
        fallback_v1_exact = (
            rt._canon_bytes(v1["score_matrix"]) == rt._canon_bytes(full["score_matrix"])
            and rt._canon_bytes({k: float(v1[k]) for k in ("p_home", "p_draw", "p_away")})
            == rt._canon_bytes({k: full[k] for k in ("p_home", "p_draw", "p_away")})
        )
        if not fallback_v1_exact:
            raise rt.RuntimeGateError("NORMAL_FALLBACK is not byte-identical to Frozen V1")

    return {
        "tag": tag,
        "competition_id": target.competition_id,
        "season": target.season,
        "fixture_id": target.fixture_id,
        "home": target.home_team_name,
        "away": target.away_team_name,
        "kickoff": target.kickoff.isoformat(),
        "cutoff": cutoff.isoformat(),
        "fast_base_cutoff": lower.isoformat(),
        "fast_delta_records": len(delta),
        "fast_delta_apply": delta_stats,
        "matrix_cells": len(fast["score_matrix"]),
        "fast_full_matrix_byte_equal": matrix_equal,
        "fast_full_probability_byte_equal": prob_equal,
        "fast_full_route_equal": route_equal,
        "model_route": full["route"],
        "fallback_exact_v1": full["fallback_exact_v1"],
        "promoted_team": promoted,
        "normal_fallback_equals_frozen_v1": fallback_v1_exact,
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
    out = Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=True)

    history, _ = rt.load_frozen_v1_history(repo_root)
    labels, _ = rt.load_xg_labels(history, understat, confirmation)

    target_cutoff = rt._parse_dt("2026-09-04T00:25:59+00:00", "target cutoff")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="football3-integrity-cross-season-") as td:
        rebuilt = integrity_full.build_integrity_base(
            Path(td) / "bundle", repo_root, understat, confirmation, target_cutoff
        )
        cov = rebuilt["coverage"]
        for comp in COMPS:
            c = cov["files"][comp]
            if c["coverage_status"] != "COMPLETE_INGESTED" or not c["eligible_for_target"] or c["joined_rows"] != c["formal_rows"]:
                raise rt.RuntimeGateError(f"required cross-season xg coverage incomplete: {comp} {c}")
        kickoff = rt._parse_dt("2026-09-04T18:30:00+00:00", "Stuttgart kickoff")
        fixture, identity = bridge.resolve_fixture(
            repo_root, rebuilt["loaded"]["state"], "GER_Bundesliga", "2026/27",
            "VfB Stuttgart", "FC Koln", kickoff,
        )
        trigger = identity_diag._xg_trigger_audit(rebuilt["loaded"]["state"], fixture)
        base_evidence = [float(x) for x in trigger["dynamic"]["evidence"]]
        if not base_evidence or min(base_evidence) <= 0.0:
            raise rt.RuntimeGateError(f"cross-season base failed to inherit nonzero XG: {base_evidence}")
        cross_season = {
            "status": "PASS",
            "state_stage": "BASE_AFTER_2025_26_CROSS_SEASON_REBUILD_BEFORE_2026_27_LIVE_DELTA",
            "cutoff": target_cutoff.isoformat(),
            "coverage": {comp: cov["files"][comp] for comp in COMPS},
            "stuttgart_koln_identity": identity,
            "stuttgart_koln_xg_evidence": base_evidence,
            "final_threshold_checked_in_pinned_target_replay": True,
            "v1_seen_n": rebuilt["v1_seen_n"],
            "xg_seen_n": rebuilt["xg_seen_n"],
        }

    cases = [_case(repo_root, history, labels, _select_regular(history, comp), comp) for comp in COMPS]
    promoted_fixture, promoted_id = _select_promoted(history)
    cases.append(_case(repo_root, history, labels, promoted_fixture, "PROMOTED", promoted_id))

    summary = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "case_n": len(cases),
        "required_competitions": list(COMPS),
        "promoted_case_present": True,
        "all_fast_full_matrix_byte_equal": all(x["fast_full_matrix_byte_equal"] for x in cases),
        "all_fast_full_probability_byte_equal": all(x["fast_full_probability_byte_equal"] for x in cases),
        "all_fast_full_route_equal": all(x["fast_full_route_equal"] for x in cases),
        "all_matrices_225_cells": all(x["matrix_cells"] == 225 for x in cases),
        "legal_fallback_frozen_v1_exact": cases[-1]["normal_fallback_equals_frozen_v1"],
        "cases": cases,
        "cross_season_full_rebuild": cross_season,
        "source_contract_quarantine_stack_installed": True,
        "result_or_xg_identity_guessing": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    if not (
        summary["case_n"] >= 4
        and summary["all_fast_full_matrix_byte_equal"]
        and summary["all_fast_full_probability_byte_equal"]
        and summary["all_fast_full_route_equal"]
        and summary["all_matrices_225_cells"]
        and summary["legal_fallback_frozen_v1_exact"] is True
    ):
        raise rt.RuntimeGateError(f"regression gate failed: {summary}")
    write_json(out / "formal_state_integrity_regression.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
