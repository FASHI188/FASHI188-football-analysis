from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "football3_changed_authority_router_v1",
    HERE / "audit_football3_changed_authority_v1.py",
)
router = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(router)


def _write(root: Path, rel: str, source: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_real_candidate_gateway_roles_are_explicit_and_clean():
    assert router.is_transport("football-data/formal_gpt_gateway_v1/entry.py")
    assert router.is_transport("football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_replay_v1.py")
    assert router.is_research_replay("football-data/formal_gpt_gateway_v1/current_v2_retrospective_replay_v1.py")
    assert router.is_production_governance("football-data/formal_gpt_gateway_v1/formal_durable_state_governance_v1.py")
    assert router.is_production_governance("football-data/formal_gpt_gateway_v1/formal_future_fixture_identity_bridge_v1.py")
    assert router.is_production_governance("football-data/formal_gpt_gateway_v1/test_prematch_state_identity_contract_v1.py")
    assert router.transport_blockers("football-data/formal_gpt_gateway_v1/entry.py") == []
    assert router.transport_blockers("football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_replay_v1.py") == []
    assert router.research_replay_blockers("football-data/formal_gpt_gateway_v1/current_v2_retrospective_replay_v1.py") == []
    assert router.production_governance_blockers("football-data/formal_gpt_gateway_v1/formal_durable_state_governance_v1.py") == []
    assert router.production_governance_blockers("football-data/formal_gpt_gateway_v1/formal_future_fixture_identity_bridge_v1.py") == []
    assert router.production_governance_blockers("football-data/formal_gpt_gateway_v1/test_prematch_state_identity_contract_v1.py") == []


def test_real_utc_day_boundary_test_uses_same_research_replay_contract():
    rel = "football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_utc_day_boundary_v1.py"
    assert router.is_research_replay(rel)
    assert router.research_replay_blockers(rel) == []


def test_legal_research_replay_test_marker_and_mode_are_recognized(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/test_replay_contract.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "def test_contract():\n    assert True\n",
    )
    assert router.is_research_replay(rel)
    assert router.research_replay_blockers(rel) == []


def test_legal_production_governance_marker_is_recognized(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/governance_contract.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE='football3-formal-production-runtime-governance-v1'\n"
        "import runtime as rt\n"
        "def gate(raw):\n    return rt._parse_dt(raw, 'cutoff')\n",
    )
    assert router.is_production_governance(rel)
    assert router.production_governance_blockers(rel) == []


def test_unmarked_gateway_python_is_not_silently_exempt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/unmarked.py"
    _write(tmp_path, rel, "def helper():\n    return 1\n")
    assert not router.is_transport(rel)
    assert not router.is_production_governance(rel)
    assert not router.is_research_replay(rel)


def test_production_governance_direct_scientific_import_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/governance.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE='football3-formal-production-runtime-governance-v1'\n"
        "import new_engine_v1.formal_fusion_v2\n",
    )
    blockers = router.production_governance_blockers(rel)
    assert any("SCIENTIFIC_IMPORT_FORBIDDEN" in x for x in blockers)


def test_production_governance_hardcoded_current_or_head_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/governance.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE='football3-formal-production-runtime-governance-v1'\n"
        "CURRENT_SHA256='forbidden'\n"
        "FORMAL_HEAD='forbidden'\n",
    )
    blockers = router.production_governance_blockers(rel)
    assert any("SCIENTIFIC_CONSTANT_FORBIDDEN:CURRENT_SHA256" in x for x in blockers)
    assert any("SCIENTIFIC_CONSTANT_FORBIDDEN:FORMAL_HEAD" in x for x in blockers)


def test_production_governance_runtime_mutation_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/governance.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE='football3-formal-production-runtime-governance-v1'\n"
        "import runtime as rt\n"
        "rt.FORMAL_SCOPE = ()\n",
    )
    blockers = router.production_governance_blockers(rel)
    assert any("PRODUCTION_GOVERNANCE_RUNTIME_MUTATION_FORBIDDEN:FORMAL_SCOPE" in x for x in blockers)


def test_production_governance_cannot_claim_transport_role_at_same_time(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/governance.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE='football3-formal-production-runtime-governance-v1'\n"
        "FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT='football3-formal-gpt-request-transport-v1'\n",
    )
    blockers = router.production_governance_blockers(rel)
    assert any("MUST_NOT_CLAIM_PRODUCTION_TRANSPORT" in x for x in blockers)


def test_research_replay_direct_scientific_import_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/replay.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "import new_engine_v1.formal_fusion_v2\n",
    )
    blockers = router.research_replay_blockers(rel)
    assert any("SCIENTIFIC_IMPORT_FORBIDDEN" in x for x in blockers)


def test_research_replay_hardcoded_current_or_head_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/replay.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "CURRENT_SHA256='forbidden'\n"
        "FORMAL_HEAD='forbidden'\n",
    )
    blockers = router.research_replay_blockers(rel)
    assert any("SCIENTIFIC_CONSTANT_FORBIDDEN:CURRENT_SHA256" in x for x in blockers)
    assert any("SCIENTIFIC_CONSTANT_FORBIDDEN:FORMAL_HEAD" in x for x in blockers)


def test_research_replay_scientific_disguise_still_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/test_fake_replay.py"
    source = (
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "rt.FUSION_WEIGHTS={'xg':1.0}\n"
    )
    _write(tmp_path, rel, source)

    runtime = SimpleNamespace(FUSION_WEIGHTS={"historical_xg": 0.75, "frozen_v1": 0.25})
    namespace = {"rt": runtime}
    exec(compile(source, rel, "exec"), namespace, namespace)
    assert runtime.FUSION_WEIGHTS == {"xg": 1.0}

    blockers = router.research_replay_blockers(rel)
    assert any("RESEARCH_REPLAY_RUNTIME_MUTATION_FORBIDDEN:FUSION_WEIGHTS" in x for x in blockers)


def test_research_replay_scientific_names_and_receipt_fields_do_not_false_positive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/test_replay_labels.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "def test_FUSION_WEIGHTS_receipt_label():\n"
        "    receipt={'fusion_weights': {'historical_xg': 0.75, 'frozen_v1': 0.25}, 'note': 'FUSION_WEIGHTS'}\n"
        "    return receipt\n",
    )
    assert router.research_replay_blockers(rel) == []


def test_research_replay_dynamic_reflection_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/replay.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "def f(obj, name):\n    return getattr(obj, name)\n",
    )
    blockers = router.research_replay_blockers(rel)
    assert any("AST_DYNAMIC_AUTHORITY_DENIED" in x for x in blockers)


def test_research_replay_runtime_mutation_is_blocked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/replay.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n"
        "import runtime as rt\n"
        "rt.FORMAL_SCOPE = ()\n",
    )
    blockers = router.research_replay_blockers(rel)
    assert any("RUNTIME_MUTATION_FORBIDDEN:FORMAL_SCOPE" in x for x in blockers)


def test_transport_cannot_claim_research_replay_role_at_same_time(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/replay.py"
    _write(
        tmp_path,
        rel,
        "FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT='football3-formal-gpt-request-transport-v1'\n"
        "FOOTBALL3_GOVERNED_RESEARCH_REPLAY='football3-current-formal-retrospective-research-replay-v1'\n"
        "MODE='CURRENT_V2_RETROSPECTIVE_REPLAY'\n",
    )
    blockers = router.research_replay_blockers(rel)
    assert any("MUST_NOT_CLAIM_PRODUCTION_TRANSPORT" in x for x in blockers)
