#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Any

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"

import request_contract_v1 as contract

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RequestBindingError(RuntimeError):
    pass


def _fail(code: str) -> None:
    raise RequestBindingError(code)


def _load(path: str) -> dict[str, Any]:
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RequestBindingError("PRODUCTION_REQUEST_BINDING_JSON_INVALID") from exc
    if type(value) is not dict:
        _fail("PRODUCTION_REQUEST_BINDING_JSON_INVALID")
    return value


def verify(request_path: str, transport_path: str, binding_path: str) -> dict[str, Any]:
    request = contract.validate_request(_load(request_path), carrier_request=True)
    transport = _load(transport_path)
    binding = _load(binding_path)
    actual = contract.request_sha256(request)
    expected = transport.get("expected_request_sha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        _fail("PRODUCTION_EXPECTED_REQUEST_SHA_MISSING")
    if actual != expected or transport.get("request_sha256") != actual:
        _fail("PRODUCTION_REQUEST_SHA_MISMATCH")
    if transport.get("request_sha_verified") is not True:
        _fail("PRODUCTION_REQUEST_SHA_NOT_VERIFIED")
    if binding.get("request_sha256") != actual:
        _fail("PRODUCTION_BASE_BINDING_REQUEST_SHA_MISMATCH")
    if binding.get("request_id") != request.get("request_id"):
        _fail("PRODUCTION_BASE_BINDING_REQUEST_ID_MISMATCH")
    carrier_head = binding.get("request_carrier_head")
    canonical_execution = binding.get("checkout_head_sha")
    if not isinstance(carrier_head, str) or not SHA_RE.fullmatch(carrier_head):
        _fail("PRODUCTION_CARRIER_HEAD_SHA_MISSING")
    if not isinstance(canonical_execution, str) or not SHA_RE.fullmatch(canonical_execution):
        _fail("PRODUCTION_CANONICAL_EXECUTION_SHA_MISSING")
    return {
        "schema_version": "football3-request-sha-binding-v1",
        "status": "PASS",
        "request_id": request["request_id"],
        "request_sha256": actual,
        "expected_request_sha256": expected,
        "request_sha_verified": True,
        "carrier_pr_number": transport.get("pr_number"),
        "carrier_head_sha": carrier_head,
        "canonical_execution_sha": canonical_execution,
    }


def enrich(transport_path: str, binding_path: str, out_dir: str) -> dict[str, Any]:
    transport = _load(transport_path)
    binding = _load(binding_path)
    expected = transport.get("expected_request_sha256")
    request_sha = binding.get("request_sha256")
    request_id = binding.get("request_id")
    carrier_head = binding.get("request_carrier_head")
    canonical_execution = binding.get("checkout_head_sha")
    carrier_bound = bool(binding.get("request_carrier_ref"))
    if carrier_bound:
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            _fail("PRODUCTION_EXPECTED_REQUEST_SHA_MISSING")
        if request_sha != expected or transport.get("request_sha256") != expected:
            _fail("PRODUCTION_REQUEST_SHA_MISMATCH")
        if transport.get("request_sha_verified") is not True:
            _fail("PRODUCTION_REQUEST_SHA_NOT_VERIFIED")
        if not isinstance(carrier_head, str) or not SHA_RE.fullmatch(carrier_head):
            _fail("PRODUCTION_CARRIER_HEAD_SHA_MISSING")
    if not isinstance(canonical_execution, str) or not SHA_RE.fullmatch(canonical_execution):
        _fail("PRODUCTION_CANONICAL_EXECUTION_SHA_MISSING")

    receipt = {
        "schema_version": "football3-request-sha-binding-v1",
        "status": "PASS" if carrier_bound else "NOT_APPLICABLE_NON_CARRIER",
        "request_id": request_id,
        "request_sha256": request_sha,
        "expected_request_sha256": expected or None,
        "request_sha_verified": bool(carrier_bound and request_sha == expected),
        "carrier_pr_number": transport.get("pr_number"),
        "carrier_head_sha": carrier_head,
        "canonical_execution_sha": canonical_execution,
        "canonical_ref": binding.get("canonical_base_ref"),
        "runner_code_source": binding.get("runner_code_source"),
        "production_run_id": str(os.environ.get("GITHUB_RUN_ID") or "") or None,
    }
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "request_sha_binding_receipt.json").write_bytes(contract.canonical_bytes(receipt) + b"\n")

    execution_path = out / "production_execution_binding_receipt.json"
    if execution_path.exists():
        execution = _load(str(execution_path))
        execution.update(
            {
                "expected_request_sha256": expected or None,
                "request_sha_verified": bool(carrier_bound and request_sha == expected),
                "carrier_pr_number": transport.get("pr_number"),
                "carrier_head_sha": carrier_head,
                "canonical_execution_sha": canonical_execution,
                "production_run_id": receipt["production_run_id"],
            }
        )
        execution_path.write_bytes(contract.canonical_bytes(execution) + b"\n")
    return receipt


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--request", required=True)
    p.add_argument("--transport", required=True)
    p.add_argument("--binding", required=True)
    p = sub.add_parser("enrich")
    p.add_argument("--transport", required=True)
    p.add_argument("--binding", required=True)
    p.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    try:
        if args.cmd == "verify":
            result = verify(args.request, args.transport, args.binding)
        else:
            result = enrich(args.transport, args.binding, args.out_dir)
        print(json.dumps(result, sort_keys=True))
        return 0
    except RequestBindingError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
