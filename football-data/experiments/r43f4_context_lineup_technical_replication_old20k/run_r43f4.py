#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
F3_DIR = ROOT / "football-data" / "experiments" / "r43f3_context_lineup_technical_translation"
if str(F3_DIR) not in sys.path:
    sys.path.insert(0, str(F3_DIR))
import run_r43f3 as f3  # noqa: E402

SOURCE_R43F3_HEAD = "fcb96f077304f905d2df179ea9fe09653180d428"
SOURCE_R43F3_RUNNER_GIT_BLOB = "e2d80cbd33317d5df47e8987a37b151a4997f5f1"
SKIP_NEWEST_N = 80000
BLOCK_SIZE = 20000
SELECTION = "valid_sorted_[-100000:-80000]"


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    old_here, old_out = f3.HERE, f3.OUT
    old_skip, old_block = f3.SKIP_NEWEST_N, f3.BLOCK_SIZE
    try:
        f3.HERE = HERE
        f3.OUT = OUT
        f3.SKIP_NEWEST_N = SKIP_NEWEST_N
        f3.BLOCK_SIZE = BLOCK_SIZE
        result = f3.run()
    finally:
        f3.HERE, f3.OUT = old_here, old_out
        f3.SKIP_NEWEST_N, f3.BLOCK_SIZE = old_skip, old_block

    # R43F3's scientific mechanism/gate is intentionally unchanged. Only the
    # preregistered disjoint historical source slice and evidence metadata differ.
    result["schema_version"] = "football3-r43f4-context-lineup-technical-replication-old20k-v1"
    result["classification"] = "FROZEN_R43F3_CONTEXT_LINEUP_TECHNICAL_1X2_REPLICATION_ON_DISJOINT_OLDER20K"
    result["question"] = "Does the exact frozen R43F3 context-P(start) expected-XI to R42H half-technical 1X2 translation replicate on a disjoint older 20k era without tuning?"
    g = result["governance"]
    g["source_r43f3_head"] = SOURCE_R43F3_HEAD
    g["source_r43f3_runner_git_blob"] = SOURCE_R43F3_RUNNER_GIT_BLOB
    g["source_slice"] = SELECTION
    g["mechanism_changed_from_r43f3"] = False
    g["gate_changed_from_r43f3"] = False
    g["test_used_for_parameter_or_feature_selection"] = False
    g["source_previously_consumed_by_r43e2"] = False
    g["source_previously_consumed_by_unrelated_r43g0_or_r42j"] = True
    g["r42l_lock_modified"] = False
    result["source"]["selection"] = SELECTION
    passed = bool(result["gate"]["passed"])
    result["gate"]["action"] = (
        "R43F3_TRANSLATION_SURVIVES_DISJOINT_REPLICATION_NEEDS_FORWARD_CONFIRMATION"
        if passed
        else "R43F3_TRANSLATION_FAILS_DISJOINT_REPLICATION_DO_NOT_RETUNE_ON_THIS_TEST"
    )
    result["limitations"] = [
        "This 20k block is disjoint from the R43F3 scored block but was previously used by unrelated R43G0/R42J research, so it is not pristine forward evidence.",
        "The R43F3 mechanism, feature definitions, alpha=0.5 shrinkage, unified 1X2 argmax and gate are unchanged; only the source slice changes.",
        "The expected lineup remains the top-11 implied by P(start), not a full mixture over possible XIs.",
        "Current-match injury publication timestamps and future-match importance are not yet integrated.",
        "R42L remains untouched and no draw output is forced.",
    ]

    intermediate = OUT / "summary_r43f3_context_lineup_technical_translation.json"
    if intermediate.exists():
        intermediate.unlink()
    p = OUT / "summary_r43f4_context_lineup_technical_replication_old20k.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43f4_context_lineup_technical_replication_old20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["governance"]["source_r43f3_head"] == SOURCE_R43F3_HEAD
    assert d["governance"]["source_slice"] == SELECTION
    assert d["source"]["selection"] == SELECTION
    assert d["governance"]["mechanism_changed_from_r43f3"] is False
    assert d["governance"]["gate_changed_from_r43f3"] is False
    assert d["governance"]["test_used_for_parameter_or_feature_selection"] is False
    assert d["governance"]["no_manual_draw_override"] is True
    assert d["governance"]["unified_three_class_argmax_unchanged"] is True
    assert d["governance"]["r42l_lock_modified"] is False
    assert d["outcome_split"]["test_n"] >= f3.MIN_TEST_MATCHES
    print("R43F4 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
