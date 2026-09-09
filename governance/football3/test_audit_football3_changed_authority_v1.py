from __future__ import annotations

import importlib.util
from pathlib import Path

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
    assert router.transport_blockers("football-data/formal_gpt_gateway_v1/entry.py") == []
    assert router.transport_blockers("football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_replay_v1.py") == []
    assert router.research_replay_blockers("football-data/formal_gpt_gateway_v1/current_v2_retrospective_replay_v1.py") == []


def test_unmarked_gateway_python_is_not_silently_exempt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)
    rel = "football-data/formal_gpt_gateway_v1/unmarked.py"
    _write(tmp_path, rel, "def helper():\n    return 1\n")
    assert not router.is_transport(rel)
    assert not router.is_research_replay(rel)


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
