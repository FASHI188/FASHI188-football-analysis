#!/usr/bin/env python3
"""Scan every forward JSON object declaring the R27 schema and fail closed."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "forward"
VALIDATOR = ROOT / "validation" / "validate_v511_forward_freeze_capture_contract_r27.py"
CONFIG = ROOT / "config" / "v511_forward_freeze_capture_contract_r27.json"
OUT = ROOT / "manifests" / "v511_forward_freeze_repository_scan_r27_status.json"


class RepositoryScanError(RuntimeError):
    pass


def load_validator():
    spec = importlib.util.spec_from_file_location("r27_validator", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RepositoryScanError("unable to load R27 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def walk_dicts(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    if isinstance(value, dict):
        yield value, path
        for key, child in value.items():
            yield from walk_dicts(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_dicts(child, path + (f"[{index}]",))


def scan_value(value: Any, declared_schema: str, validator, config: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    discovered = 0
    invalid: list[dict[str, Any]] = []
    for event, node_path in walk_dicts(value):
        if event.get("schema_version") != declared_schema:
            continue
        discovered += 1
        errors = validator.validate_event(event, config)
        if errors:
            invalid.append({
                "node_path": "/".join(node_path),
                "event_hash": event.get("event_hash"),
                "errors": errors,
            })
    return discovered, invalid


def self_test() -> None:
    validator = load_validator()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    declared = config["event_contract"]["schema_version"]
    valid, _ = validator.valid_sample()
    discovered, invalid = scan_value({"events": [valid]}, declared, validator, config)
    if discovered != 1 or invalid:
        raise RepositoryScanError("valid R27 repository scan self-test failed")
    broken = copy.deepcopy(valid)
    broken["payload"]["market"].pop("over_under")
    discovered, invalid = scan_value({"events": [broken]}, declared, validator, config)
    if discovered != 1 or len(invalid) != 1 or "MISSING_OVER_UNDER" not in invalid[0]["errors"]:
        raise RepositoryScanError("invalid R27 repository scan self-test failed")
    print(json.dumps({"self_test": "PASS", "valid_discovered": 1, "invalid_rejected": 1}, indent=2))


def run() -> dict[str, Any]:
    validator = load_validator()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    declared = config["event_contract"]["schema_version"]
    files_scanned = 0
    discovered = 0
    invalid: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for path in sorted(FORWARD.rglob("*.json")):
        files_scanned += 1
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_errors.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
            continue
        file_discovered, file_invalid = scan_value(value, declared, validator, config)
        discovered += file_discovered
        for row in file_invalid:
            row["source_path"] = str(path.relative_to(ROOT)).replace("\\", "/")
            invalid.append(row)
    passed = not invalid and not parse_errors
    status = {
        "schema_version": "v511_forward_freeze_repository_scan_r27_status.1",
        "status": "PASS_R27_REPOSITORY_FORWARD_EVENTS_FAIL_CLOSED" if passed else "FAIL_R27_INVALID_FORWARD_EVENT_OR_JSON",
        "classification": "READ_ONLY_R27_REPOSITORY_EVENT_SCAN",
        "formal_weight": 0,
        "forward_json_files_scanned": files_scanned,
        "declared_r27_events_discovered": discovered,
        "invalid_declared_r27_events": len(invalid),
        "all_declared_r27_events_valid": not invalid,
        "forward_json_parse_errors": parse_errors,
        "invalid_events": invalid,
        "ruling": {
            "future_r27_events_are_scanned_on_every_forward_json_change": True,
            "invalid_event_blocks_workflow": True,
            "zero_declared_events_is_allowed_before_new_collection": True,
            "legacy_events_are_not_relabelled_as_r27": True,
            "model_training_performed": False,
            "provider_requests": 0,
            "current_or_main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if not passed:
        raise RepositoryScanError(status["status"])
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
