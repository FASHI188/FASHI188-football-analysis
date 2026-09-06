#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from test_runtime_core_v1 import *
import test_runtime_core_v1 as core
from production_300_reference_replay_v1 import (
    benchmark_paths,
    choose_safe_seed_cutoff,
    reference_advance,
    reference_apply_group,
    reference_events,
    reset_reference_state,
    run_equivalence,
)

rt = core.rt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("local", "production"), required=True)
    ap.add_argument("--repo-root")
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=300)
    args = ap.parse_args()
    under = Path(args.understat_db)
    conf = Path(args.confirmation_dir)
    production_adjudication = None
    if args.mode == "production":
        if not args.repo_root:
            raise SystemExit("--repo-root required in production mode")
        import formal_result_adjudication_v2
        production_adjudication = formal_result_adjudication_v2.install()
        history, labels, source, identity = production_corpus(Path(args.repo_root), under, conf)
    else:
        history, labels, source, identity = local_corpus(under, conf)

    reset_reference_state()
    with tempfile.TemporaryDirectory(prefix="football3-fast-runtime-test-") as td:
        tmp = Path(td)
        eq = run_equivalence(history, labels, source, identity, args.n, tmp)
        sample = eq.pop("sample")
        fallback = exact_fallback_check()
        same = same_kickoff_isolation(history, labels)
        target = target_label_isolation(history, labels, sample[len(sample) // 2])
        routes = route_tests(history, labels, source, identity, sample, tmp)
        receipt = {
            "schema_version": "football3-fast-runtime-equivalence-receipt-v1",
            "mode": args.mode,
            "formal_head": rt.FORMAL_HEAD,
            "formal_weights": {"xg": 0.75, "v1": 0.25},
            "frozen_source_sha256": {
                "understat_db": rt._sha_file(under),
                "confirmation_identity": rt._sha_file(conf / "confirmation_identity.jsonl"),
                "confirmation_vault": rt._sha_file(conf / "confirmation_xg_result_vault.jsonl"),
            },
            "corpus": {
                "fixture_n": len(history),
                "xg_label_n": len(labels),
                "identity_sha256": sha([{"fixture_id": r.fixture_id, "kickoff": r.kickoff.isoformat()} for r in history]),
            },
            "production_result_adjudication": production_adjudication,
            "equivalence_300": eq,
            "v1_exact_fallback": fallback,
            "same_kickoff_isolation": same,
            "target_label_isolation": target,
            "automatic_routing": routes,
            "reference_replay_module": "production_300_reference_replay_v1",
            "reference_replay_monkeypatch_used": False,
            "claim": "CACHE_FULL_EQUIVALENT_ON_FROZEN_HISTORY_AUTOROUTE_RELIABLE_PENDING_INDEPENDENT_ENGINEERING_REVIEW",
            "current_data_complete_claimed": False,
            "formal_enablement_changed": False,
            "current_pointer_changed": False,
        }
        receipt["passed"] = all((eq["passed"], fallback["passed"], same["passed"], target["passed"], routes["passed"]))
        receipt["receipt_sha256"] = sha(receipt)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(canon(receipt))
        print(json.dumps({
            "status": "PASS" if receipt["passed"] else "FAIL",
            "receipt": str(out),
            "sha256": receipt["receipt_sha256"],
            "max_1x2": eq["max_abs_1x2"],
            "max_matrix": eq["max_abs_score_matrix_cell"],
            "fast_median_s": eq["route_benchmark"]["fast"]["median_s"],
            "full_median_s": eq["route_benchmark"]["full"]["median_s"],
        }, sort_keys=True))
        return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
