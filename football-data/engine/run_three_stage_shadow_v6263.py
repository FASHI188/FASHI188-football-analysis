#!/usr/bin/env python3
"""Question-time shadow runner for the V6.26 three-stage research architecture.

Input is a JSON object on stdin (or --input file) containing:
  prior_score_matrix: [{home_goals, away_goals, probability}, ...]
  accepted_1x2: {home, draw, away}
  accepted_total_goals: {0,1,2,3,4,5,6,7+}
  optional metadata / asian_handicap_snapshot

The AH snapshot is carried into the audit as read-only context and cannot alter the matrix.
This runner never edits CURRENT or formal outputs; it exists for same-match shadow comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import three_stage_core_v6260 as core  # noqa: E402


def _load(args: argparse.Namespace) -> dict[str, Any]:
    if args.input:
        return json.loads(Path(args.input).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = _load(args)

    prior = payload.get("prior_score_matrix")
    one = payload.get("accepted_1x2")
    total = payload.get("accepted_total_goals")
    if not isinstance(prior, list) or not isinstance(one, dict) or not isinstance(total, dict):
        raise SystemExit("missing prior_score_matrix / accepted_1x2 / accepted_total_goals")

    result = core.build_three_stage_output(prior, one, total)
    result["classification"] = "RESEARCH_SHADOW_ONLY"
    result["formal_current_version"] = "V5.0.1"
    result["formal_probability_mutation"] = False
    result["metadata"] = payload.get("metadata") or {}
    result["asian_handicap_context"] = {
        "snapshot": payload.get("asian_handicap_snapshot"),
        "used_to_mutate_probability": False,
        "role": "read-only auxiliary context; any future feature use requires chronological ablation",
    }

    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
