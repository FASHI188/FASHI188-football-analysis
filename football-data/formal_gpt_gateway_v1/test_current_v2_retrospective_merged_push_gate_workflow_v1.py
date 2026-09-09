from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "football3-current-v2-retrospective-replay-acceptance.yml"
INTEGRATION_BRANCH = "football3/formal-gpt-runner-integration-v1"
CANDIDATE_BINDING = "CANDIDATE_SHA: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
PUSH_PATHS = {
    ".github/workflows/football3-current-v2-retrospective-replay-acceptance.yml",
    "football-data/formal_gpt_gateway_v1/current_v2_retrospective_*.py",
    "football-data/formal_gpt_gateway_v1/test_current_v2_retrospective_*.py",
    "governance/football3/current_v2_retrospective_*.py",
    "governance/football3/test_current_v2_retrospective_*.py",
}


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _push_block(text: str) -> str:
    start = text.index("  push:\n")
    end = text.index("  workflow_dispatch:\n", start)
    return text[start:end]


def _assert_contract(text: str) -> None:
    assert "  pull_request:\n" in text
    assert "  workflow_dispatch:\n" in text
    push = _push_block(text)
    assert f"    branches:\n      - {INTEGRATION_BRANCH}\n" in push
    paths = set(re.findall(r"^      - '([^']+)'$", push, flags=re.MULTILINE))
    assert paths == PUSH_PATHS
    assert "**" not in push
    assert text.count(CANDIDATE_BINDING) == 4
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" not in text
    assert "github.event_name == 'push'" in text
    assert "actions: write" not in text
    assert "contents: write" not in text
    assert "pull_request_target" not in text
    assert "repository_dispatch" not in text
    assert "/dispatches" not in text
    assert "gh workflow run" not in text


def test_retrospective_merged_push_gate_contract():
    _assert_contract(_text())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("  push:\n", "  push_removed:\n", 1),
        lambda text: text.replace(
            "  push:\n    branches:\n      - football3/formal-gpt-runner-integration-v1\n",
            "  push:\n    branches:\n      - main\n",
            1,
        ),
        lambda text: text.replace(
            "      - 'governance/football3/test_current_v2_retrospective_*.py'\n",
            "      - 'governance/football3/**'\n",
            1,
        ),
        lambda text: text.replace(
            CANDIDATE_BINDING,
            "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            1,
        ),
        lambda text: text.replace("actions: read", "actions: write", 1),
        lambda text: text.replace("  pull_request:\n", "  pull_request_target:\n", 1),
    ],
)
def test_retrospective_merged_push_gate_mutations_fail_closed(mutate):
    with pytest.raises((AssertionError, ValueError)):
        _assert_contract(mutate(_text()))
