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
    c = contract()
    mutator(c)
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(c, schema())


def test_positive_contract_and_schema_pass():
    mod.validate_contract(contract(), schema())


def test_market_semantics_are_not_weakened():
    rejects(lambda c: c["governance"].__setitem__("market_features", True))
    rejects(lambda c: c["governance"].__setitem__("market_inputs", ["closing_odds"]))
    rejects(lambda c: c["governance"].__setitem__("market_baseline", True))
    rejects(lambda c: c["governance"].__setitem__("market_validator_semantics", "BYPASS"))
    rejects(lambda c: c["governance"]["immutable_market_governance_git_blobs"].__setitem__(
        "football-data/research/validate_football3_experiment.py", "0" * 40
    ))
    rejects(lambda c: c["governance"]["immutable_market_governance_git_blobs"].__setitem__(
        "football-data/research/test_validate_football3_experiment.py", "0" * 40
    ))


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
    rejects(lambda c: c["governance"]["immutable_formal_source_git_blobs"].__setitem__(
        "football-data/new_engine_v1/formal_fusion_v2.py", "0" * 40
    ))


def test_scientific_code_binding_is_real_non_market_and_fail_closed():
    c = contract()
    b = c["governance"]["scientific_code_bindings"]
    assert b == mod.EXPECTED_BINDINGS
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__(
        "runner", "football-data/research/fake_market_runner.py"
    ))
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__(
        "contract_marker", "FOOTBALL3_EXPERIMENT_CONTRACT"
    ))
    rejects(lambda c: c["governance"]["scientific_code_bindings"].__setitem__(
        "cumulative_audit_base_head", "3016f6c7a0b77e0db310ad926011dfaa50c56e02"
    ))


def test_unknown_top_level_contract_key_fails_closed():
    rejects(lambda c: c.__setitem__("market_override", True))


def test_schema_constants_fail_closed():
    s = schema()
    s["properties"]["schema_version"]["const"] = 1
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(contract(), s)


def test_schema_scientific_binding_cannot_be_removed():
    s = schema()
    s["properties"]["governance"]["properties"].pop("scientific_code_bindings")
    with pytest.raises(mod.FormalWiringGovernanceError):
        mod.validate_contract(contract(), s)


def test_git_blob_sha1_matches_git_object_formula(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"abc\n")
    expected = hashlib.sha1(b"blob 4\0abc\n").hexdigest()
    assert mod.git_blob_sha1(p) == expected
