#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SUCCESSOR_FREEZE = "football-data/research/promoted_cold_start_v2_1_successor_retry_v1/SUCCESSOR_RETRY_FREEZE.json"
SUCCESSOR_FREEZE_BLOB = "5bba9664d040019153abb193eaa4a71cf1ec7c8c"
SUCCESSOR_WRAPPER = "football-data/research/promoted_cold_start_v2_1_successor_retry_v1/run_frozen_evaluator_successor_retry_v1.py"
SUCCESSOR_WRAPPER_BLOB = "06fc733db5058e4481cfe5242a790007a0229615"
PREDECESSOR_WRAPPER = "football-data/research/promoted_cold_start_v2_1/run_frozen_evaluator_after_prelabel_v1.py"
PREDECESSOR_WRAPPER_BLOB = "a47141391eb328b24bef269f3328167527aa3fb4"
LOCKED = {
    SUCCESSOR_FREEZE: SUCCESSOR_FREEZE_BLOB,
    SUCCESSOR_WRAPPER: SUCCESSOR_WRAPPER_BLOB,
    PREDECESSOR_WRAPPER: PREDECESSOR_WRAPPER_BLOB,
    "football-data/research/promoted_cold_start_v2_1/PRE_LABEL_SEMANTIC_CLOSURE_RECEIPT.json": "d482ff53313d9e34df07e87a4bdfaa158841ff61",
    "football-data/research/promoted_cold_start_v2_1/PREREGISTRATION.json": "30c45da908540ad99aab51024729d554a7047c7a",
    "football-data/research/promoted_cold_start_v2_1/evaluate.py": "849e92d36b3f9838b3d931ad80c03ad3ce1eb8c1",
    "football-data/formal_fast_runtime_v1/runtime.py": "8994226369094908beb410ce454d19fd7272c3df",
    "football-data/config/team_aliases.json": "6248028352308ceda6800794c0ee3f5ceca223a9",
    "football-data/research/promoted_cold_start_v2_1/CANDIDATE_OUTPUT_INTERFACE_V1.json": "7a57f49b510b8b099726b4be3d47bf3c2b091c42",
}


def blob(rel: str) -> str:
    return subprocess.check_output(["git", "hash-object", rel], cwd=REPO, text=True).strip()


def load_wrapper():
    path = REPO / SUCCESSOR_WRAPPER
    spec = importlib.util.spec_from_file_location("v21_successor_wrapper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load successor wrapper")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_runtime_contract() -> None:
    mod = load_wrapper()
    calls = {"runpy": 0}

    class InstallGood:
        @staticmethod
        def install():
            return {"installed": True, "idempotent": True, "schema_version": "engineering-precheck"}

    def fake_run_path(path: str, run_name: str):
        if Path(path) != REPO / "football-data/research/promoted_cold_start_v2_1/evaluate.py":
            raise AssertionError(path)
        if run_name != "__main__":
            raise AssertionError(run_name)
        calls["runpy"] += 1
        return {}

    mod.formal_adjudication = InstallGood
    mod.runpy.run_path = fake_run_path
    mod.main()
    assert calls["runpy"] == 1

    for receipt in (
        {"installed": False, "idempotent": True},
        {"installed": True, "idempotent": False},
        {"status": "INSTALLED"},
    ):
        mod = load_wrapper()
        mod.formal_adjudication.install = lambda receipt=receipt: receipt
        reached = {"runpy": False}
        mod.runpy.run_path = lambda *a, **k: reached.__setitem__("runpy", True)
        try:
            mod.main()
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"invalid install receipt accepted: {receipt}")
        assert reached["runpy"] is False


def main() -> None:
    observed = {rel: blob(rel) for rel in LOCKED}
    if observed != LOCKED:
        raise SystemExit(json.dumps({"status": "FAIL_BLOB_BINDING", "observed": observed}, sort_keys=True))

    freeze = json.loads((REPO / SUCCESSOR_FREEZE).read_text(encoding="utf-8"))
    assert freeze["status"] == "FROZEN_BEFORE_SUCCESSOR_METRIC_OBSERVATION"
    assert freeze["predecessor_one_shot"]["exact_head"] == "503d2c350fb44cdeb4b4172a41d5be5eb568e0d9"
    assert freeze["predecessor_one_shot"]["run_id"] == 34132629584
    assert freeze["predecessor_one_shot"]["verdict"] == "FAIL_RESEARCH_ONLY"
    assert freeze["predecessor_one_shot"]["candidate_metric_observations"] == 0
    assert freeze["predecessor_one_shot"]["frozen_evaluator_runpy_reached"] is False
    assert freeze["allowed_successor_code_change"]["scope"] == "wrapper return-structure compatibility only"
    assert freeze["successor_execution_contract"]["candidate_metric_observations_before_successor"] == 0
    assert freeze["successor_execution_contract"]["successor_evaluator_max_invocations"] == 1
    assert freeze["pre_label_hard_gates"] == {
        "joined": 5330,
        "expected_joined": 5330,
        "missing": 0,
        "extra": 0,
        "ambiguous": 0,
        "duplicate": 0,
        "time_leakage": 0,
        "pass": True,
    }

    old = (REPO / PREDECESSOR_WRAPPER).read_text(encoding="utf-8")
    new = (REPO / SUCCESSOR_WRAPPER).read_text(encoding="utf-8")
    assert 'install_receipt.get("status") != "INSTALLED"' in old
    assert 'install_receipt.get("status") != "INSTALLED"' not in new
    assert 'install_receipt.get("installed") is not True or install_receipt.get("idempotent") is not True' in new
    assert 'runpy.run_path(str(repo / EVALUATOR_REL), run_name="__main__")' in old
    assert 'runpy.run_path(str(repo / EVALUATOR_REL), run_name="__main__")' in new

    assert_runtime_contract()
    print(json.dumps({
        "status": "PASS_SUCCESSOR_ENGINEERING_PRE_METRIC",
        "candidate_metric_observations": 0,
        "predecessor_preserved": True,
        "successor_wrapper_blob": SUCCESSOR_WRAPPER_BLOB,
        "science_blobs_unchanged": True,
        "wrapper_good_receipt_reaches_fake_runpy_once": True,
        "wrapper_bad_receipts_rejected_before_fake_runpy": True
    }, sort_keys=True))


if __name__ == "__main__":
    main()
