#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/football3-formal-gpt-runner-integration-v1.yml"
REQUEST = ROOT / "football-data/formal_gpt_gateway_v1/request.json"


def main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    request = json.loads(REQUEST.read_text(encoding="utf-8"))

    # The committed push selftest is intentionally non-prediction. It must not
    # manufacture a match or a durable bundle merely to make the workflow green.
    assert request["schema_version"] == "football3-formal-gpt-gateway-v1"
    assert request.get("mode") == "cache_reuse_probe"
    assert not isinstance(request.get("match"), dict)

    # Non-prediction mode must be explicit and auditable.
    assert "SKIPPED_NON_PREDICTION_MODE" in text
    assert "SKIPPED_BUNDLE_DEPENDENT_GATEWAY" in text
    assert "NON_PREDICTION_MODE_DOES_NOT_REQUIRE_DURABLE_BUNDLE" in text
    assert "gateway_prediction_executed':False" in text
    assert "fail_closed_relaxed':False" in text

    # A real prediction may enter the formal gateway only after the selector
    # has selected a durable state. There is deliberately no `!= true` bypass.
    expected_execute = (
        "steps.durable_selector.outputs.prediction_required == 'true' && "
        "steps.durable_selector.outputs.selected == 'true'"
    )
    assert expected_execute in text
    assert "steps.durable_selector.outputs.prediction_required != 'true' || steps.durable_selector.outputs.selected == 'true'" not in text

    # Success for a real prediction is stronger than process exit zero: a valid
    # bundle and a non-empty prediction SHA, consistent with the formal receipt,
    # remain mandatory. Gateway/selector/guard fail-closed semantics are untouched.
    assert "test -f .formal_state_cache/bundle/manifest.json" in text
    assert "test -f football-data/formal_gpt_gateway_v1/output/prediction_receipt.json" in text
    assert "assert isinstance(s.get('prediction_sha'),str) and s['prediction_sha']" in text
    assert "assert r.get('prediction_sha')==s['prediction_sha']" in text
    assert "assert r.get('state_integrity_guard',{}).get('status')=='PASS'" in text
    assert "Preserve durable selector failure as job failure" in text
    assert "Preserve main gateway failure as job failure" in text

    print("FORMAL_RUNNER_SELFTEST_CONTROLFLOW_CONTRACT_PASS")


if __name__ == "__main__":
    main()
