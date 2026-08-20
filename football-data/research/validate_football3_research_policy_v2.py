#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys

ROOT_SHA = "e3e73c998020beef585cc459a69ea5b73b44ddb3"
CHECKPOINT_SHA = "0e088ee91ebd94a2b840b4fe673457f1c6b37193"
POLICY_PATH = pathlib.Path("football-data/research/FOOTBALL3_RESEARCH_POLICY_V2.json")
CURRENT_PATH = pathlib.Path("football-data/research/FOOTBALL3_INDEPENDENT_CURRENT.md")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0


def main() -> None:
    if not POLICY_PATH.exists() or not CURRENT_PATH.exists():
        fail("football3 current/policy file missing")

    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    current = CURRENT_PATH.read_text(encoding="utf-8")

    if policy.get("project_id") != "football3":
        fail("project_id must be football3")
    if policy.get("scientific_root", {}).get("sha") != ROOT_SHA:
        fail("scientific root SHA drift")
    if policy.get("latest_scientific_checkpoint", {}).get("sha") != CHECKPOINT_SHA:
        fail("latest scientific checkpoint drift")
    if policy.get("branch_policy", {}).get("required_prefix") != "football3/":
        fail("football3 branch-prefix gate missing")
    if policy.get("primary_target", {}).get("notation") != "P(T=0,1,2,... )".replace("... ", "..."):
        fail("primary P(T) target drift")
    if not policy.get("fresh_evidence_policy", {}).get("global_consumption"):
        fail("global-consumption rule disabled")
    if not policy.get("sample_policy", {}).get("power_or_precision_plan_required_before_confirmation_labels"):
        fail("confirmation sample-planning gate disabled")
    if policy.get("governance", {}).get("formal_weight") != 0:
        fail("governance must keep formal_weight=0")

    required_text = [
        "C072-C",
        ROOT_SHA,
        "P(T=0,1,2,...)",
        "C073-C077",
        "GLOBALLY CONSUMED",
        "C070-F Confirmation 1597",
        "N18C confirmation150",
        "No downstream Draw optimization is authorized as the next step."
    ]
    for token in required_text:
        if token not in current:
            fail(f"current file missing required boundary: {token}")

    # When run in a checked-out repository, both the scientific root and the
    # latest legal scientific checkpoint must be ancestors of the governance HEAD.
    if pathlib.Path(".git").exists():
        if not git_ok("merge-base", "--is-ancestor", ROOT_SHA, "HEAD"):
            fail("HEAD is outside the C072-C lineage")
        if not git_ok("merge-base", "--is-ancestor", CHECKPOINT_SHA, "HEAD"):
            fail("governance HEAD does not descend from N19R1 checkpoint")

    ref = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or ""
    if ref and not ref.startswith("football3/"):
        fail(f"non-football3 branch attempted to validate football3 policy: {ref}")

    print("PASS: football3 research policy v2 and lineage boundaries validated")


if __name__ == "__main__":
    main()
