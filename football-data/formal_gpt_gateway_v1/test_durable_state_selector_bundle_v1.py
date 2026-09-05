#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import durable_state_contract_v1 as contract
import durable_state_selector_v1 as selector
import runtime as rt

UTC = timezone.utc


def _dt(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="football3-selector-bundle-") as td:
        # GitHub artifact downloads redirect to signed object storage. The GitHub
        # token must authenticate the first request without being forwarded to the
        # redirect target, where it would invalidate the signed storage request.
        captured = []

        def fake_urlopen(request):
            captured.append(request)
            return io.BytesIO(b"artifact-bytes")

        download = Path(td) / "artifact.zip"
        with mock.patch.object(selector.urllib.request, "urlopen", fake_urlopen):
            selector._download("https://api.github.com/repos/o/r/actions/artifacts/1/zip", "secret", download)
        request = captured[0]
        assert request.unredirected_hdrs["Authorization"] == "Bearer secret"
        assert "Authorization" not in request.headers
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://objects.example.invalid/signed.zip"
        )
        assert redirected is not None
        assert redirected.get_header("Authorization") is None
        assert download.read_bytes() == b"artifact-bytes"

        bundle = Path(td) / "bundle"
        state = rt.formal_v2.new_candidate_state()
        manifest = rt.seal_bundle(
            state,
            bundle,
            {
                "schema_version": "selector-bundle-test-source-v1",
                "source_observed_at_utc": "2026-09-04T13:00:00+00:00",
            },
            {"schema_version": "selector-bundle-test-identity-v1"},
            "2026-09-04T13:19:44+00:00",
            "FULL_REBUILD_PATH",
        )
        artifact = {
            "id": 4242,
            "name": "formal-gpt-runner-state-4242",
            "created_at": "2026-09-04T18:30:00+00:00",
            "digest": "sha256:test-only",
        }
        run = {
            "id": 2424,
            "status": "completed",
            "conclusion": "success",
            "head_branch": "football3/formal-gpt-runner-integration-v1",
        }
        target = _dt("2026-09-04T18:00:00Z")
        row = selector._candidate_from_bundle(artifact, run, bundle, target, "ESP_LaLiga")
        selected, evaluated = contract.choose_candidate([row], target, "ESP_LaLiga")
        assert selected is not None, evaluated
        assert selected["artifact_id"] == 4242
        assert selected["state_cutoff"] == "2026-09-04T13:19:44+00:00"
        assert selected["artifact_created_at"] == "2026-09-04T18:30:00+00:00"
        assert selected["state_sha256"] == manifest["state_sha256"]
        assert selected["state_bundle_sha256"] == manifest["state_bundle_sha256"]
        assert selected["schema_ok"] is True
        assert selected["runtime_ok"] is True
        assert selected["model_current_ok"] is True
        assert selected["competition_scope_ok"] is True
        assert selected["pit_ok"] is True
        assert selected["max_source_observed_at"] == "2026-09-04T13:19:44+00:00"

        # Prediction execution is a distinct clock. It may occur after target cutoff without
        # contaminating max_source_observed_at because no new source was observed.
        fake_loaded = {
            "meta": {"historical_cutoff": "2026-09-04T13:19:44+00:00"},
            "state": SimpleNamespace(
                base=SimpleNamespace(last_update_time=_dt("2026-09-04T13:00:00Z")),
                last_apply_time=_dt("2026-09-04T13:10:00Z"),
                last_prediction_time=_dt("2026-09-04T20:00:00Z"),
            ),
            "source": {"source_observed_at_utc": "2026-09-04T13:05:00+00:00"},
        }
        max_source = contract.max_source_observed_at(fake_loaded)
        assert max_source == "2026-09-04T13:19:44+00:00", max_source

        future = dict(row)
        future["state_cutoff"] = "2026-09-04T18:00:01+00:00"
        future["pit_ok"] = False
        selected_future, evaluated_future = contract.choose_candidate([future], target, "ESP_LaLiga")
        assert selected_future is None
        assert "FUTURE_STATE" in evaluated_future[0]["rejection_reasons"]
        assert "PIT" in evaluated_future[0]["rejection_reasons"]

    receipt = {
        "schema_version": "football3-durable-state-selector-bundle-regression-v1",
        "passed": True,
        "candidate_artifact_id": row["artifact_id"],
        "artifact_created_at": row["artifact_created_at"],
        "state_cutoff": row["state_cutoff"],
        "target_cutoff": target.isoformat(),
        "max_source_observed_at": row["max_source_observed_at"],
        "prediction_clock_excluded_from_source_clock": True,
        "future_state_fail_closed": True,
        "state_sha256": row["state_sha256"],
        "state_bundle_sha256": row["state_bundle_sha256"],
        "formal_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "runtime_contract_sha256": contract.runtime_contract_payload()["runtime_contract_sha256"],
        "target_result_read_or_scored": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    receipt["receipt_sha256"] = contract.sha(receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
