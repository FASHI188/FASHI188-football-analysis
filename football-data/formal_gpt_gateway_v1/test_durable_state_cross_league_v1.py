#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import timedelta
from pathlib import Path

import runtime as rt
import test_runtime as tr


def _pick_target(history, comp: str):
    rows = [r for r in history if r.competition_id == comp and r.season == "2024/25"]
    rows.sort(key=lambda r: (r.kickoff, r.fixture_id))
    if len(rows) < 10:
        raise AssertionError(f"insufficient frozen rows for {comp}")
    return rows[len(rows) // 2]


def _state_equal(a, b) -> bool:
    return rt.serialize_v1_state(a.base) == rt.serialize_v1_state(b.base) and rt.serialize_xg_state(a) == rt.serialize_xg_state(b)


def run_one(history, labels, source, identity, comp: str, root: Path):
    target = _pick_target(history, comp)
    cutoff = target.kickoff - timedelta(minutes=60)
    lower = cutoff - timedelta(days=2)
    delta = tr.make_delta(history, labels, lower, cutoff, target.fixture_id)
    for _ in range(12):
        if delta:
            break
        lower -= timedelta(days=2)
        delta = tr.make_delta(history, labels, lower, cutoff, target.fixture_id)
    if not delta:
        raise AssertionError(f"non-empty verified delta unavailable for {comp}")

    # FAST with verified non-empty delta.
    fast_dir = root / f"{comp}-fast"
    base_state, _ = rt.replay_history_state(history, labels, lower)
    rt.seal_bundle(base_state, fast_dir, source, identity, lower.isoformat(), "FULL_REBUILD_PATH")
    fast_input = tr.runtime_input(target, lower, cutoff, delta, "COMPLETE")
    rfast = rt.resolve_state_for_cutoff(
        fast_dir, fast_input, tr.target_payload(target), cutoff,
        engineering_history=history, engineering_xg_labels=labels,
        engineering_source=source, engineering_identity=identity,
    )
    if rfast["path"] != "FAST_PATH":
        raise AssertionError(f"FAST route mismatch {comp}: {rfast.get('fast_failure')}")

    # FULL from no durable cache / unverified delta availability. FULL replay is independent.
    full_dir = root / f"{comp}-full"
    full_input = tr.runtime_input(target, cutoff, cutoff, [], "UNKNOWN")
    rfull = rt.resolve_state_for_cutoff(
        full_dir, full_input, tr.target_payload(target), cutoff,
        engineering_history=history, engineering_xg_labels=labels,
        engineering_source=source, engineering_identity=identity,
    )
    if rfull["path"] != "FULL_REBUILD_PATH":
        raise AssertionError(f"FULL route mismatch {comp}: {rfull.get('fast_failure')}")

    # Exact sealed empty-delta FAST: no FULL history refetch is permitted or necessary.
    empty_dir = root / f"{comp}-empty"
    exact_state, _ = rt.replay_history_state(history, labels, cutoff)
    rt.seal_bundle(exact_state, empty_dir, source, identity, cutoff.isoformat(), "FULL_REBUILD_PATH")
    empty_input = tr.runtime_input(target, cutoff, cutoff, [], "COMPLETE")
    rempty = rt.resolve_state_for_cutoff(
        empty_dir, empty_input, tr.target_payload(target), cutoff,
        engineering_history=history, engineering_xg_labels=labels,
        engineering_source=source, engineering_identity=identity,
    )
    if rempty["path"] != "FAST_PATH":
        raise AssertionError(f"empty-delta FAST route mismatch {comp}: {rempty.get('fast_failure')}")

    if not _state_equal(rfast["state"], rfull["state"]):
        raise AssertionError(f"FAST/FULL state mismatch {comp}")
    if not _state_equal(rempty["state"], rfull["state"]):
        raise AssertionError(f"empty/FULL state mismatch {comp}")

    pfast = rt._prediction_from_state(rfast["state"], target.xg_fixture())
    pfull = rt._prediction_from_state(rfull["state"], target.xg_fixture())
    pempty = rt._prediction_from_state(rempty["state"], target.xg_fixture())
    eq_fast = rt._prediction_equivalent(pfast, pfull)
    eq_empty = rt._prediction_equivalent(pempty, pfull)
    if not eq_fast["passed"] or not eq_empty["passed"]:
        raise AssertionError(f"FAST/FULL prediction mismatch {comp}")

    return {
        "competition_id": comp,
        "fixture_id": target.fixture_id,
        "cutoff": cutoff.isoformat(),
        "non_empty_delta_n": len(delta),
        "fast_path": rfast["path"],
        "full_path": rfull["path"],
        "empty_delta_path": rempty["path"],
        "empty_delta_n": 0,
        "fast_full_state_exact": True,
        "empty_full_state_exact": True,
        "fast_full_prediction_equivalent": eq_fast,
        "empty_full_prediction_equivalent": eq_empty,
        "model_route": pfull["row"]["audit"]["route"],
        "fallback_exact_v1": bool(pfull["row"]["audit"]["fallback_exact_v1"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    history, labels, source, identity = tr.production_corpus(
        Path(args.repo_root), Path(args.understat_db), Path(args.confirmation_dir)
    )
    big5 = ["ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"]
    with tempfile.TemporaryDirectory(prefix="football3-durable-big5-") as td:
        root = Path(td)
        matrix = [run_one(history, labels, source, identity, comp, root) for comp in big5]
    la = next(row for row in matrix if row["competition_id"] == "ESP_LaLiga")
    assert la["non_empty_delta_n"] > 0 and la["empty_delta_n"] == 0
    receipt = {
        "schema_version": "football3-durable-state-cross-league-matrix-v1",
        "formal_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "big5_fast_full_matrix": matrix,
        "la_liga_delta_and_empty_delta": {
            "non_empty_delta_n": la["non_empty_delta_n"],
            "empty_delta_n": 0,
            "fast_non_empty": la["fast_path"] == "FAST_PATH",
            "fast_empty": la["empty_delta_path"] == "FAST_PATH",
            "full": la["full_path"] == "FULL_REBUILD_PATH",
        },
        "passed": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    receipt["receipt_sha256"] = tr.sha(receipt)
    Path(args.out).write_bytes(tr.canon(receipt))
    print(json.dumps({"status": "PASS", "sha256": receipt["receipt_sha256"], "matrix": matrix}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
