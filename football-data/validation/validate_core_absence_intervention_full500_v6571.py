#!/usr/bin/env python3
"""V6.57.1 engineering-only driver for V6.57.0.

No research design, data, threshold, grid, gate, or model behavior is changed.
V6.57.0 used the public rule label ``absence`` while the computed context key is
``absence_pressure``. This driver fixes only that name mapping and re-runs the exact
same experiment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "validation") not in sys.path:
    sys.path.insert(0, str(ROOT / "validation"))

import validate_core_absence_intervention_full500_v6570 as v  # noqa: E402

OUT = ROOT / "manifests" / "v6_core_absence_intervention_full500_v6571_status.json"


def fixed_score(ctx: dict[str, float], spec: str) -> float:
    key = "absence_pressure" if spec == "absence" else spec
    return float(ctx[key])


def main() -> int:
    v.score = fixed_score
    v.OUT = OUT
    rc = int(v.main())
    if rc == 0 and OUT.exists():
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.57.1-core-absence-intervention-full500-r1"
        payload["algorithm_relation"] = (
            "V6.57.0 unchanged; V6.57.1 fixes only rule-label 'absence' -> context-key 'absence_pressure'"
        )
        payload.setdefault("governance", {})["research_grid_changed_by_v6571"] = False
        payload["governance"]["thresholds_changed_by_v6571"] = False
        payload["governance"]["data_contract_changed_by_v6571"] = False
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
