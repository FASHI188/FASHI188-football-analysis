#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import request_contract_v1 as contract
import request_sha_binding_v1 as binding

CANONICAL_SHA = "a" * 40
CARRIER_SHA = "b" * 40


def _write(path: pathlib.Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _verify(request: dict, transport: dict, base_binding: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        request_path = root / "request.json"
        transport_path = root / "transport.json"
        binding_path = root / "binding.json"
        _write(request_path, request)
        _write(transport_path, transport)
        _write(binding_path, base_binding)
        return binding.verify(str(request_path), str(transport_path), str(binding_path))


def _expect_error(code: str, request: dict, transport: dict, base_binding: dict) -> None:
    try:
        _verify(request, transport, base_binding)
    except binding.RequestBindingError as exc:
        assert str(exc) == code, (code, str(exc))
    else:
        raise AssertionError(f"expected {code}")


def non_carrier_fixture() -> tuple[dict, dict, dict]:
    request = {
        "schema_version": contract.SCHEMA,
        "mode": "cache_reuse_probe",
        "request_id": "transport-aware-selftest",
    }
    request = contract.validate_request(request, carrier_request=False)
    sha = contract.request_sha256(request)
    transport = {
        "transport": "COMMITTED_SELFTEST_REQUEST",
        "request_id": request["request_id"],
        "request_sha256": sha,
        "expected_request_sha256": None,
        "request_sha_verified": False,
        "pr_number": None,
    }
    base_binding = {
        "request_id": request["request_id"],
        "request_sha256": sha,
        "request_carrier_ref": None,
        "request_carrier_head": None,
        "checkout_head_sha": CANONICAL_SHA,
    }
    return request, transport, base_binding


def carrier_fixture() -> tuple[dict, dict, dict]:
    request = {
        "schema_version": contract.SCHEMA,
        "mode": "predict",
        "request_id": "transport-aware-carrier",
        "match": {
            "competition_id": "FRA_Ligue1",
            "season": "2026/27",
            "home_team_name": "Lyon",
            "away_team_name": "Auxerre",
            "kickoff": "2026-09-04T17:00:00+00:00",
            "cutoff": "2026-09-04T16:00:00+00:00",
        },
    }
    request = contract.validate_request(request, carrier_request=True)
    sha = contract.request_sha256(request)
    transport = {
        "transport": "DRAFT_PR_BODY_DISPATCH_SHA_BOUND",
        "request_id": request["request_id"],
        "request_sha256": sha,
        "expected_request_sha256": sha,
        "request_sha_verified": True,
        "pr_number": 341,
    }
    base_binding = {
        "request_id": request["request_id"],
        "request_sha256": sha,
        "request_carrier_ref": "football3/formal-gpt-runner-request-carrier-v1",
        "request_carrier_head": CARRIER_SHA,
        "checkout_head_sha": CANONICAL_SHA,
    }
    return request, transport, base_binding


def main() -> int:
    request, transport, base_binding = non_carrier_fixture()
    result = _verify(request, transport, base_binding)
    assert result["status"] == "NOT_APPLICABLE_NON_CARRIER", result
    assert result["request_sha_verified"] is False, result
    assert result["expected_request_sha256"] is None, result

    bad_transport = dict(transport)
    bad_transport["expected_request_sha256"] = "c" * 64
    _expect_error(
        "PRODUCTION_NON_CARRIER_EXPECTED_REQUEST_SHA_UNEXPECTED",
        request,
        bad_transport,
        base_binding,
    )

    bad_transport = dict(transport)
    bad_transport["request_sha_verified"] = True
    _expect_error(
        "PRODUCTION_NON_CARRIER_SHA_VERIFICATION_FLAG_INVALID",
        request,
        bad_transport,
        base_binding,
    )

    carrier_request, carrier_transport, carrier_binding = carrier_fixture()
    carrier_result = _verify(carrier_request, carrier_transport, carrier_binding)
    assert carrier_result["status"] == "PASS", carrier_result
    assert carrier_result["request_sha_verified"] is True, carrier_result

    bad_carrier_transport = dict(carrier_transport)
    bad_carrier_transport["transport"] = "COMMITTED_SELFTEST_REQUEST"
    _expect_error(
        "PRODUCTION_CARRIER_TRANSPORT_INVALID",
        carrier_request,
        bad_carrier_transport,
        carrier_binding,
    )

    selftest_request = dict(request)
    carrier_binding_for_selftest = dict(carrier_binding)
    carrier_binding_for_selftest["request_id"] = selftest_request["request_id"]
    carrier_binding_for_selftest["request_sha256"] = contract.request_sha256(selftest_request)
    selftest_carrier_transport = dict(carrier_transport)
    selftest_carrier_transport["request_id"] = selftest_request["request_id"]
    selftest_carrier_transport["request_sha256"] = contract.request_sha256(selftest_request)
    selftest_carrier_transport["expected_request_sha256"] = contract.request_sha256(selftest_request)
    _expect_error(
        "FORMAL_REQUEST_MODE_INVALID",
        selftest_request,
        selftest_carrier_transport,
        carrier_binding_for_selftest,
    )

    print("REQUEST_SHA_BINDING_TRANSPORT_TEST_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
