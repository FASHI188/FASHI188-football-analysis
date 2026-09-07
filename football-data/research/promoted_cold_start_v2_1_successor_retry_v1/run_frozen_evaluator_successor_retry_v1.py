#!/usr/bin/env python3
from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path

import formal_result_adjudication_v2 as formal_adjudication

SUCCESSOR_FREEZE_REL = "football-data/research/promoted_cold_start_v2_1_successor_retry_v1/SUCCESSOR_RETRY_FREEZE.json"
SUCCESSOR_FREEZE_BLOB = "5bba9664d040019153abb193eaa4a71cf1ec7c8c"
RECEIPT_REL = "football-data/research/promoted_cold_start_v2_1/PRE_LABEL_SEMANTIC_CLOSURE_RECEIPT.json"
RECEIPT_BLOB = "d482ff53313d9e34df07e87a4bdfaa158841ff61"
EVALUATOR_REL = "football-data/research/promoted_cold_start_v2_1/evaluate.py"
EVALUATOR_BLOB = "849e92d36b3f9838b3d931ad80c03ad3ce1eb8c1"
PREREG_REL = "football-data/research/promoted_cold_start_v2_1/PREREGISTRATION.json"
PREREG_BLOB = "30c45da908540ad99aab51024729d554a7047c7a"
RUNTIME_REL = "football-data/formal_fast_runtime_v1/runtime.py"
RUNTIME_BLOB = "8994226369094908beb410ce454d19fd7272c3df"
ALIASES_REL = "football-data/config/team_aliases.json"
ALIASES_BLOB = "6248028352308ceda6800794c0ee3f5ceca223a9"
PREDECESSOR_WRAPPER_REL = "football-data/research/promoted_cold_start_v2_1/run_frozen_evaluator_after_prelabel_v1.py"
PREDECESSOR_WRAPPER_BLOB = "a47141391eb328b24bef269f3328167527aa3fb4"
EXPECTED_HARD = {
    "joined": 5330,
    "expected_joined": 5330,
    "missing": 0,
    "extra": 0,
    "ambiguous": 0,
    "duplicate": 0,
    "time_leakage": 0,
    "pass": True,
}


def blob(repo: Path, rel: str) -> str:
    return subprocess.check_output(["git", "hash-object", rel], cwd=repo, text=True).strip()


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    locked = {
        SUCCESSOR_FREEZE_REL: SUCCESSOR_FREEZE_BLOB,
        RECEIPT_REL: RECEIPT_BLOB,
        EVALUATOR_REL: EVALUATOR_BLOB,
        PREREG_REL: PREREG_BLOB,
        RUNTIME_REL: RUNTIME_BLOB,
        ALIASES_REL: ALIASES_BLOB,
        PREDECESSOR_WRAPPER_REL: PREDECESSOR_WRAPPER_BLOB,
    }
    observed = {rel: blob(repo, rel) for rel in locked}
    if observed != locked:
        raise RuntimeError(f"successor frozen evaluator execution binding drift: {observed}")

    successor = json.loads((repo / SUCCESSOR_FREEZE_REL).read_text(encoding="utf-8"))
    if successor.get("schema_version") != "football3-promoted-cold-start-v2-1-successor-technical-retry-freeze-v1":
        raise RuntimeError("successor retry freeze schema drift")
    if successor.get("status") != "FROZEN_BEFORE_SUCCESSOR_METRIC_OBSERVATION":
        raise RuntimeError("successor retry freeze not active")
    if successor.get("predecessor_one_shot", {}).get("candidate_metric_observations") != 0:
        raise RuntimeError("predecessor metric observation contract drift")
    if successor.get("predecessor_one_shot", {}).get("frozen_evaluator_runpy_reached") is not False:
        raise RuntimeError("predecessor runpy reachability contract drift")
    if successor.get("allowed_successor_code_change", {}).get("scope") != "wrapper return-structure compatibility only":
        raise RuntimeError("successor allowed-change scope drift")
    if successor.get("successor_execution_contract", {}).get("successor_evaluator_max_invocations") != 1:
        raise RuntimeError("successor one-shot contract drift")

    receipt = json.loads((repo / RECEIPT_REL).read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "football3-promoted-cold-start-v2-1-pre-label-semantic-closure-freeze-v1":
        raise RuntimeError("pre-label receipt schema drift")
    if receipt.get("status") != "PASS_PRE_LABEL_HARD_GATES":
        raise RuntimeError("pre-label receipt not PASS")
    if receipt.get("hard_gates") != EXPECTED_HARD:
        raise RuntimeError(f"pre-label hard gate drift: {receipt.get('hard_gates')}")
    science = receipt.get("science_lock") or {}
    required_false = (
        "candidate_evaluator_invoked",
        "candidate_metrics_computed",
        "candidate_parameters_or_gates_modified",
        "candidate_sample_or_cohort_modified",
        "evaluator_modified",
        "preregistration_modified",
        "production_runtime_modified",
        "team_aliases_modified",
        "formal_model_or_current_modified",
    )
    if any(science.get(k) is not False for k in required_false):
        raise RuntimeError(f"pre-label science lock drift: {science}")

    u = (receipt.get("special_semantics") or {}).get("udinese_roma_resumed_same_fixture") or {}
    if (u.get("source_process_date"), u.get("formal_completion_date"), u.get("final_result_released_before_completion")) != (
        "2024-04-14", "2024-04-25", False
    ):
        raise RuntimeError("Udinese-Roma frozen resumed semantics drift")
    b = (receipt.get("special_semantics") or {}).get("union_berlin_bochum_dual_semantics") or {}
    if b.get("formal_result") != [0, 2] or b.get("on_field_xg_result") != [1, 1]:
        raise RuntimeError("Union-Berlin Bochum dual semantics drift")

    install_receipt = formal_adjudication.install()
    if install_receipt.get("installed") is not True or install_receipt.get("idempotent") is not True:
        raise RuntimeError(f"formal adjudication install failed: {install_receipt}")

    runpy.run_path(str(repo / EVALUATOR_REL), run_name="__main__")


if __name__ == "__main__":
    main()
