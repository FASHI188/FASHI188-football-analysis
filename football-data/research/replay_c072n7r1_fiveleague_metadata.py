#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ORIGINAL = Path("football-data/research/audit_c072n7_footiqo_fiveleague_metadata.py")
OUT = Path("football-data/research/c072n7_metadata_result.json")
FORBIDDEN_SECRET_KEYS = {"nonce_value", "raw_nonce", "request_body", "wdtNonce"}


def assert_no_secret_keys(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in FORBIDDEN_SECRET_KEYS:
                raise AssertionError(f"forbidden secret-bearing key: {k}")
            assert_no_secret_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            assert_no_secret_keys(v)


def safe_persist(result: dict) -> int:
    assert_no_secret_keys(result)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    spec = importlib.util.spec_from_file_location("c072n7_frozen", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen N7 evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Binding replay: replace only the evidence-persistence function.
    module.persist = safe_persist
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
