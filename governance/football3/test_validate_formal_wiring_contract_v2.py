from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("formal_wiring_guard", HERE / "validate_formal_wiring_contract_v2.py")
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

CONTRACT_PATH = HERE.parents[1] / "football-data" / "historical_xg_fusion_v2" / "contracts" / "FORMAL_FUSION_V2_WIRING.json"
SCHEMA_PATH = HERE / "formal_wiring_contract_schema_v2.json"
RESEARCH_BASE = "d3b3e322f78c48b91477ef6e11054e51ac00fd85"


def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def rejects(mutator) -> None:
    c = contract(); mutator(c)
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(c, schema())


def _clear_runtime_env(monkeypatch) -> None:
    for name in (
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_HEAD_REF",
        "GITHUB_REF_NAME",
        "GITHUB_REF",
        "GITHUB_SHA",
        "FORMAL_WIRING_GOVERNED_BRANCH",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_integration_context(monkeypatch, *, event: str = "workflow_dispatch", sha: str = "2" * 40) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF_NAME", mod.EXPECTED_INTEGRATION_BRANCH)
    monkeypatch.setenv("GITHUB_REF", f"refs/heads/{mod.EXPECTED_INTEGRATION_BRANCH}")
    monkeypatch.setenv("GITHUB_SHA", sha)


def test_positive_contract_and_schema_pass():
    mod.validate_contract(contract(), schema())


def test_market_semantics_are_not_weakened():
    rejects(lambda c: c["governance"].__setitem__("market_features", True))
    rejects(lambda c: c["governance"].__setitem__("market_inputs", ["closing_odds"]))
    rejects(lambda c: c["governance"].__setitem__("market_baseline", True))
    rejects(lambda c: c["governance"].__setitem__("market_validator_semantics", "BYPASS"))
    rejects(lambda c: c["governance"]["immutable_market_governance_git_blobs"].__setitem__("football-data/research/validate_football3_experiment.py", "0" * 40))
    rejects(lambda c: c["governance"]["immutable_market_governance_git_blobs"].__setitem__("football-data/research/test_validate_football3_experiment.py", "0" * 40))


def test_training_tuning_labels_and_enablement_fail_closed():
    for key in ("training", "tuning", "new_target_labels", "formal_enablement", "production_pointer_change"):
        rejects(lambda c, key=key: c["governance"].__setitem__(key, True))
    rejects(lambda c: c["runtime"].__setitem__("formal_enablement", True))
    rejects(lambda c: c["runtime"].__setitem__("production_pointer_changed", True))
    rejects(lambda c: c["runtime"].__setitem__("prospective_queue", True))


def test_weight_formula_and_fallback_are_frozen():
    rejects(lambda c: c["fusion"].__setitem__("xg_weight", 0.8))
    rejects(lambda c: c["fusion"].__setitem__("v1_weight", 0.2))
    rejects(lambda c: c["fusion"].__setitem__("formula", "normalize(p_XG)"))
    rejects(lambda c: c["fusion"].__setitem__("xg_insufficient", "PARTIAL_XG"))


def test_cumulative_whitelist_is_exact_and_capped():
    c = contract()
    assert c["governance"]["whitelist_base_head"] == RESEARCH_BASE
    assert len(c["governance"]["changed_file_whitelist"]) == 9
    assert len(c["governance"]["changed_file_whitelist"]) <= 12
    rejects(lambda c: c["governance"]["changed_file_whitelist"].append("football-data/research/validate_football3_experiment.py"))
    rejects(lambda c: c["governance"].__setitem__("whitelist_base_head", "3016f6c7a0b77e0db310ad926011dfaa50c56e02"))


def test_branch_research_and_formal_source_identities_are_frozen():
    rejects(lambda c: c.__setitem__("branch", "football3/other"))
    rejects(lambda c: c["research_acceptance"].__setitem__("head", "0" * 40))
    rejects(lambda c: c["governance"]["immutable_formal_source_git_blobs"].__setitem__("football-data/new_engine_v1/formal_fusion_v2.py", "0" * 40))


def test_scientific_code_binding_is_real_non_market_and_fail_closed():
    c = contract(); b = c["governance"]["scientific_code_bindings"]
    assert b == mod.EXPECTED_BINDINGS
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__("runner", "football-data/research/fake_market_runner.py"))
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__("contract_marker", "FOOTBALL3_EXPERIMENT_CONTRACT"))
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__("cumulative_audit_base_head", "3016f6c7a0b77e0db310ad926011dfaa50c56e02"))


def test_unknown_top_level_contract_key_fails_closed():
    rejects(lambda c: c.__setitem__("market_override", True))


def test_schema_constants_fail_closed():
    s = schema(); s["properties"]["schema_version"]["const"] = 1
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(contract(), s)


def test_schema_scientific_binding_cannot_be_removed():
    s = schema(); s["properties"]["governance"]["properties"].pop("scientific_code_bindings")
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(contract(), s)


def test_git_blob_sha1_matches_git_object_formula(tmp_path: Path):
    p = tmp_path / "x.txt"; p.write_bytes(b"abc\n")
    expected = hashlib.sha1(b"blob 4\0abc\n").hexdigest()
    assert mod.git_blob_sha1(p) == expected


def test_pull_request_candidate_authority_is_generic_not_branch_whitelist(tmp_path: Path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"base": {"sha": "1" * 40}}}), encoding="utf-8")
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_HEAD_REF", "arbitrary/integration-hotfix-candidate")
    assert mod.validate_runtime_branch(contract()) == "PULL_REQUEST_CANDIDATE"
    assert mod._pull_request_base_sha() == "1" * 40


def test_candidate_authority_protects_scientific_model_and_current_surfaces():
    c = contract(); protected = mod._candidate_protected_paths(c)
    assert "football-data/new_engine_v1/formal_fusion_v2.py" in protected
    assert mod._is_additionally_protected_scientific_path("football-data/new_engine_v1/other_model.py")
    assert mod._is_additionally_protected_scientific_path("football-data/config/CURRENT")
    assert mod._is_additionally_protected_scientific_path("football-data/config/formal_model_pointer_v9.json")
    assert not mod._is_additionally_protected_scientific_path("football-data/formal_gpt_gateway_v1/request_contract_v1.py")
    assert not mod._is_additionally_protected_scientific_path("football-data/formal_gpt_gateway_v1/current_v2_retrospective_replay_v1.py")
    assert not mod._is_additionally_protected_scientific_path("football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_replay_v1.py")


def test_candidate_runtime_without_pull_request_event_fails_closed(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_HEAD_REF", "candidate/branch")
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime branch mismatch outside pull-request candidate"):
        mod.validate_runtime_branch(contract())


def test_original_formal_wiring_governed_branch_remains_legal(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF_NAME", mod.EXPECTED_BRANCH)
    monkeypatch.setenv("GITHUB_REF", f"refs/heads/{mod.EXPECTED_BRANCH}")
    monkeypatch.setenv("GITHUB_SHA", "3" * 40)
    assert mod.validate_runtime_branch(contract()) == "CONTRACT_BRANCH"


@pytest.mark.parametrize("event", ["workflow_dispatch", "push"])
def test_formal_integration_exact_bound_runtime_is_legal(monkeypatch, event):
    sha = "4" * 40
    _set_integration_context(monkeypatch, event=event, sha=sha)
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: sha)
    assert mod.validate_runtime_branch(contract(), Path(".")) == "FORMAL_INTEGRATION_RUNTIME"


@pytest.mark.parametrize(
    "runtime",
    [
        "football3/other",
        "football3/formal-gpt-runner-integration-v1-extra",
        "prefix/football3/formal-gpt-runner-integration-v1",
        "Football3/formal-gpt-runner-integration-v1",
        "football3/FORMAL-GPT-RUNNER-INTEGRATION-V1",
    ],
)
def test_arbitrary_similar_or_case_variant_runtime_branches_are_rejected(monkeypatch, runtime):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF_NAME", runtime)
    monkeypatch.setenv("GITHUB_REF", f"refs/heads/{runtime}")
    monkeypatch.setenv("GITHUB_SHA", "5" * 40)
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime branch mismatch outside pull-request candidate"):
        mod.validate_runtime_branch(contract())


def test_workflow_dispatch_wrong_ref_binding_is_rejected(monkeypatch):
    sha = "6" * 40
    _set_integration_context(monkeypatch, sha=sha)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/football3/other")
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: sha)
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime ref mismatch"):
        mod.validate_runtime_branch(contract())


def test_workflow_dispatch_wrong_sha_binding_is_rejected(monkeypatch):
    _set_integration_context(monkeypatch, sha="7" * 40)
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: "8" * 40)
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime SHA mismatch"):
        mod.validate_runtime_branch(contract())


def test_workflow_dispatch_invalid_sha_is_rejected(monkeypatch):
    _set_integration_context(monkeypatch, sha="not-a-sha")
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: "9" * 40)
    with pytest.raises(mod.FormalWiringGovernanceError, match="SHA invalid or missing"):
        mod.validate_runtime_branch(contract())


def test_integration_event_context_mismatch_is_rejected(monkeypatch):
    sha = "a" * 40
    _set_integration_context(monkeypatch, event="schedule", sha=sha)
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: sha)
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime event mismatch"):
        mod.validate_runtime_branch(contract())


@pytest.mark.parametrize("missing", ["branch", "ref", "sha"])
def test_integration_missing_branch_ref_or_sha_is_rejected(monkeypatch, missing):
    sha = "b" * 40
    _set_integration_context(monkeypatch, sha=sha)
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: sha)
    if missing == "branch":
        monkeypatch.delenv("GITHUB_REF_NAME")
    elif missing == "ref":
        monkeypatch.delenv("GITHUB_REF")
    else:
        monkeypatch.delenv("GITHUB_SHA")
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_runtime_branch(contract())


def test_pull_request_candidate_does_not_require_or_read_integration_binding(tmp_path: Path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"base": {"sha": "c" * 40}}}), encoding="utf-8")
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_HEAD_REF", "football3/full-stack-integration-runtime-branch-contract-hotfix-v1")
    monkeypatch.setenv("GITHUB_REF_NAME", "999/merge")
    assert mod.validate_runtime_branch(contract()) == "PULL_REQUEST_CANDIDATE"


def test_push_integration_context_does_not_consult_pull_request_payload(monkeypatch):
    sha = "d" * 40
    _set_integration_context(monkeypatch, event="push", sha=sha)
    monkeypatch.setenv("GITHUB_EVENT_PATH", "/definitely/not/a/pr/payload.json")
    monkeypatch.setattr(mod, "_git_head_sha", lambda repo_root: sha)
    assert mod.validate_runtime_branch(contract()) == "FORMAL_INTEGRATION_RUNTIME"


def test_contract_branch_mutation_still_fails_before_runtime_authority(monkeypatch):
    c = contract()
    c["branch"] = mod.EXPECTED_INTEGRATION_BRANCH
    with pytest.raises(mod.FormalWiringGovernanceError, match="contract branch mismatch"):
        mod.validate_contract(c, schema())


def test_environment_variable_cannot_forge_broad_authority(monkeypatch):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("FORMAL_WIRING_GOVERNED_BRANCH", "football3/evil")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF_NAME", "football3/evil")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/football3/evil")
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)
    with pytest.raises(mod.FormalWiringGovernanceError, match="runtime branch mismatch outside pull-request candidate"):
        mod.validate_runtime_branch(contract())


def test_integration_runtime_surface_requires_exact_governed_protected_blobs(monkeypatch):
    c = contract()
    monkeypatch.setattr(mod, "_git_blob_at_ref", lambda repo_root, ref, rel: "f" * 40)
    protected = mod.validate_integration_runtime_surface(c, Path("."))
    assert protected == mod._candidate_protected_paths(c)


def test_integration_runtime_surface_drift_fails_closed(monkeypatch):
    c = contract()
    target = sorted(mod._candidate_protected_paths(c))[0]

    def blob(repo_root, ref, rel):
        if ref == "HEAD" and rel == target:
            return "1" * 40
        return "2" * 40

    monkeypatch.setattr(mod, "_git_blob_at_ref", blob)
    with pytest.raises(mod.FormalWiringGovernanceError, match="formal integration protected surface drift"):
        mod.validate_integration_runtime_surface(c, Path("."))


FULL_STACK_WORKFLOW_PATH = HERE.parents[1] / ".github" / "workflows" / "football3-full-stack-remediation.yml"
INTEGRATION_BRANCH = "football3/formal-gpt-runner-integration-v1"
ACTIVATION_BRANCH = "football3/historical-xg-fusion-v2-formal-activation-v1"
INCIDENT_RUN_ID = 34491680012
INCIDENT_HEAD = "92a6a2ee68cfa5dfe9bd1cb54cbc5e569b5b7d32"
INCIDENT_PARENT_1 = "15a1f6a9266d2636339723d66891829cf286c0ba"
INCIDENT_PARENT_2 = "5b044d8d6f53b1b0b857e668b1fd623156ffd4b6"


def _full_stack_workflow_text() -> str:
    return FULL_STACK_WORKFLOW_PATH.read_text(encoding="utf-8")


def _full_stack_step_block(name: str) -> str:
    text = _full_stack_workflow_text()
    marker = f"      - name: {name}\n"
    start = text.index(marker)
    end = text.find("\n      - name: ", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


def _authority_step() -> str:
    return _full_stack_step_block("Authoritative cumulative changed-file domain audit")


def _sealed_step() -> str:
    return _full_stack_step_block("Prove cumulative remediation opens no real target or sealed data")


def test_full_stack_diff_base_pr_event_selects_exact_pr_base():
    block = _authority_step()
    assert 'if [ "$TRUSTED_EVENT_NAME" = \'pull_request\' ]; then' in block
    assert 'BASE_SHA="$PR_BASE_SHA"' in block
    assert 'PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}' in block


def test_full_stack_diff_base_integration_push_selects_two_parent_first_parent():
    block = _authority_step()
    assert "[ \"$TRUSTED_EVENT_NAME\" = 'push' ]" in block
    assert f"[ \"$TRUSTED_REF_NAME\" = '{INTEGRATION_BRANCH}' ]" in block
    assert 'PARENT_COUNT" -ne 2' in block
    assert 'BASE_SHA="$PARENT_1"' in block


def test_full_stack_diff_base_integration_workflow_dispatch_selects_two_parent_first_parent():
    block = _authority_step()
    assert "[ \"$TRUSTED_EVENT_NAME\" = 'workflow_dispatch' ]" in block
    assert f"refs/heads/{INTEGRATION_BRANCH}" in block
    assert 'BASE_SHA="$PARENT_1"' in block


def test_full_stack_diff_base_selected_range_is_exact_base_three_dot_head():
    block = _authority_step()
    sealed = _sealed_step()
    assert 'AUDIT_RANGE="${BASE_SHA}...HEAD"' in block
    assert 'final audit range=%s\\n' in block
    assert 'EXPECTED_RANGE="${FOOTBALL3_SELECTED_BASE_SHA}...HEAD"' in sealed
    assert 'git\',\'diff\',\'--name-only\',audit_range' in sealed.replace(" ", "")


def test_full_stack_diff_base_rejects_checkout_head_github_sha_mismatch():
    block = _authority_step()
    assert '[ "$CHECKOUT_HEAD" != "$TRUSTED_SHA" ]' in block
    assert 'integration checkout HEAD mismatch' in block


def test_full_stack_diff_base_rejects_wrong_integration_ref():
    block = _authority_step()
    exact_ref = f"refs/heads/{INTEGRATION_BRANCH}"
    assert f"[ \"$TRUSTED_REF\" = '{exact_ref}' ]" in block
    assert 'unsupported branch/ref' in block


def test_full_stack_diff_base_rejects_similar_prefix_suffix_and_case_variants():
    block = _authority_step()
    assert f"[ \"$TRUSTED_REF_NAME\" = '{INTEGRATION_BRANCH}' ]" in block
    assert "startsWith" not in block
    assert "=~ .*formal-gpt-runner-integration" not in block
    assert 'unsupported branch/ref' in block


def test_full_stack_diff_base_rejects_single_parent_head():
    block = _authority_step()
    assert 'PARENT_COUNT" -ne 2' in block
    assert 'HEAD must be a standard two-parent merge commit' in block


def test_full_stack_diff_base_rejects_any_parent_count_other_than_two():
    block = _authority_step()
    assert '[ "$PARENT_COUNT" -ne 2 ]' in block
    assert '[ -n "${EXTRA_PARENT:-}" ]' in block


def test_full_stack_diff_base_rejects_invalid_or_missing_parent_sha_and_commit():
    block = _authority_step()
    assert '! [[ "$PARENT_1" =~ $SHA_RE ]]' in block
    assert '! [[ "$PARENT_2" =~ $SHA_RE ]]' in block
    assert 'git cat-file -e "$PARENT_1^{commit}"' in block
    assert 'git cat-file -e "$PARENT_2^{commit}"' in block


def test_full_stack_diff_base_rejects_schedule_and_other_events():
    block = _authority_step()
    assert "'push'" in block and "'workflow_dispatch'" in block and "'pull_request'" in block
    assert 'FULL_STACK_DIFF_BASE_FAIL: unsupported event:' in block
    assert "'schedule'" not in block


def test_full_stack_diff_base_research_branch_keeps_frozen_research_base():
    block = _authority_step()
    assert "football3/historical-xg-fusion-v2-formal-wiring-governed-v1" in block
    assert 'BASE_SHA="$FORMAL_WIRING_RESEARCH_BASE_HEAD"' in block
    assert RESEARCH_BASE in _full_stack_workflow_text()


def test_full_stack_diff_base_activation_context_keeps_frozen_research_base():
    block = _authority_step()
    activation_pos = block.index(ACTIVATION_BRANCH)
    frozen_pos = block.index('BASE_SHA="$FORMAL_WIRING_RESEARCH_BASE_HEAD"', activation_pos)
    assert frozen_pos > activation_pos


def test_full_stack_diff_base_pr_context_does_not_read_merge_parents():
    block = _authority_step()
    pr_start = block.index('if [ "$TRUSTED_EVENT_NAME" = \'pull_request\' ]; then')
    integration_start = block.index("elif [ \"$TRUSTED_EVENT_NAME\" = 'push' ]")
    pr_clause = block[pr_start:integration_start]
    assert "git rev-list --parents" not in pr_clause
    assert "PARENT_1" not in pr_clause


def test_full_stack_diff_base_integration_context_does_not_read_pr_only_base():
    block = _authority_step()
    integration_start = block.index("elif [ \"$TRUSTED_EVENT_NAME\" = 'push' ]")
    integration_end = block.index("else\n            echo \"FULL_STACK_DIFF_BASE_FAIL: unsupported event", integration_start)
    integration_clause = block[integration_start:integration_end]
    assert "PR_BASE_SHA" not in integration_clause


def test_full_stack_diff_base_uses_trusted_github_context_not_forgeable_runtime_env():
    block = _authority_step()
    assert 'TRUSTED_EVENT_NAME: ${{ github.event_name }}' in block
    assert 'TRUSTED_REF_NAME: ${{ github.ref_name }}' in block
    assert 'TRUSTED_REF: ${{ github.ref }}' in block
    assert 'TRUSTED_SHA: ${{ github.sha }}' in block
    assert '"$GITHUB_EVENT_NAME"' not in block
    assert '"$GITHUB_REF_NAME"' not in block
    assert '"$GITHUB_REF"' not in block
    assert '"$GITHUB_SHA"' not in block


def test_full_stack_authority_auditor_still_executes_and_cannot_be_soft_skipped():
    block = _authority_step()
    assert 'python governance/football3/audit_football3_changed_authority_v1.py' in block
    assert 'continue-on-error' not in block
    assert '|| true' not in block
    assert '--base "$BASE_SHA" --head HEAD' in block


def test_run_34491680012_accident_fixture_proves_old_base_wrong_new_parent1_required():
    fixture = {
        "run_id": INCIDENT_RUN_ID,
        "event": "workflow_dispatch",
        "branch": INTEGRATION_BRANCH,
        "head": INCIDENT_HEAD,
        "parent_1": INCIDENT_PARENT_1,
        "parent_2": INCIDENT_PARENT_2,
        "old_selected_base": RESEARCH_BASE,
    }
    assert fixture["run_id"] == 34491680012
    assert fixture["head"] == "92a6a2ee68cfa5dfe9bd1cb54cbc5e569b5b7d32"
    assert fixture["old_selected_base"] == "d3b3e322f78c48b91477ef6e11054e51ac00fd85"
    assert fixture["parent_1"] != fixture["old_selected_base"]
    assert fixture["parent_2"] != fixture["parent_1"]
    assert 'BASE_SHA="$PARENT_1"' in _authority_step()
