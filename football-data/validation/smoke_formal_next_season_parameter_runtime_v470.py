#!/usr/bin/env python3
"""Smoke the next-season parameter bridge without weakening current-season gates."""
from __future__ import annotations

import json
from pathlib import Path

import football_v460_engine as engine
from formal_next_season_parameter_runtime_v470 import audit_rollforward_parameters, select_rollforward_parameters
from platform_core import PlatformError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "formal_next_season_parameter_runtime_v470_smoke.json"


def main() -> int:
    results = {}
    checks = {}
    for cid in ("ESP_LaLiga", "NED_Eredivisie"):
        artifact = engine.load_model_artifact(cid)
        params = select_rollforward_parameters(artifact, "2026/27")
        audit = audit_rollforward_parameters(cid, "2026/27")
        checks[f"{cid}_parameter_bridge_passes"] = audit.get("status") == "通过" and bool(params)
        checks[f"{cid}_team_strength_not_rolled"] = audit.get("team_strength_rollforward") is False
        # Explicitly prove that the unchanged formal engine still refuses a target
        # season with zero same-season history even when hyperparameters are legal.
        sample_gate_rejected = False
        try:
            engine.predict_joint_distribution(
                cid,
                "Synthetic Home",
                "Synthetic Away",
                __import__("datetime").datetime(2026, 7, 20, tzinfo=__import__("datetime").timezone.utc),
                season="2026/27",
                selected_parameters=params,
            )
        except PlatformError as exc:
            sample_gate_rejected = "current-season history has 0 matches" in str(exc)
        checks[f"{cid}_zero_sample_still_rejected"] = sample_gate_rejected
        results[cid] = {"audit": audit, "selected_parameters": params}
    out = {
        "schema_version": "V4.7.0-formal-next-season-parameter-runtime-smoke-r1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "results": results,
        "engine_sha256_unchanged": engine.sha256_file(engine.ENGINE_PATH),
        "probability_change": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
