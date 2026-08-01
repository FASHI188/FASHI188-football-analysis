#!/usr/bin/env python3
"""Registry-backed façade for the route-aware V5.0.2 closure engine."""
from __future__ import annotations

import base64
import sys
import zlib
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import draw_signal_closure_engine_v502_r3 as base
from draw_signal_closure_engine_v502_r3 import *  # noqa: F401,F403


def load_expected_asset_registry(root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or (root / "football-data" / "audit" / "draw_signal_asset_registry_v502.b85")
    registry_text = zlib.decompress(base64.b85decode(path.read_bytes())).decode("utf-8")
    entries = []
    metadata: dict[str, Any] = {"schema_version": "DRAW-SIGNAL-ASSET-REGISTRY-V502-1.0"}
    for raw_line in registry_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "=" in line:
                key, value = line[1:].split("=", 1)
                metadata[key.strip()] = value.strip()
            continue
        flag, rel = line.split("\t", 1)
        if flag not in {"I", "E"}:
            raise ValueError(f"invalid registry flag: {flag}")
        entries.append({"path": rel, "expected_included": flag == "I"})
    if not entries:
        raise ValueError("expected asset registry entries missing")
    metadata["entries"] = entries
    return metadata


# Patch only the registry loader. All audit/decision logic remains in the reviewed
# route-aware engine and therefore uses this independently frozen expected universe.
base.load_expected_asset_registry = load_expected_asset_registry


def build_audit(root: Path):
    return base.build_audit(root)


def write_audit(out: Path, audit):
    return base.write_audit(out, audit)


def main(argv=None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
