#!/usr/bin/env python3
"""Build a cost-aware external evidence backfill plan for the recent two seasons.

The plan never calls paid APIs. It quantifies the exact fixture universe and the
maximum requested freeze points so provider access can later be used deliberately
rather than scanning older history or unrelated competitions.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import read_processed_matches  # noqa: E402

SCOPE = ROOT / "config" / "two_season_evidence_scope_v476.json"
OUT = ROOT / "manifests" / "two_season_backfill_plan_v476_status.json"
FREEZES = ["T-60", "T-30", "T-10", "last_verified_pre_kickoff"]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _logical_season(raw: Any, scope: dict[str, Any]) -> str | None:
    token = str(raw or "").strip()
    mandatory = [str(x) for x in scope.get("mandatory_seasons", [])]
    if token in mandatory:
        return token
    aliases = scope.get("accepted_evidence_season_aliases") or {}
    for logical, values in aliases.items():
        if token in {str(v) for v in values}:
            return str(logical)
    return None


def build() -> dict[str, Any]:
    config = _load(SCOPE)
    reports: dict[str, Any] = {}
    total_matches = 0
    total_market_freezes = 0
    total_lineup_labels = 0
    errors = []

    for cid, scope in sorted((config.get("competitions") or {}).items()):
        counts: Counter[str] = Counter()
        try:
            rows = read_processed_matches(cid)
        except Exception as exc:
            errors.append({"competition_id": cid, "error": str(exc)})
            continue
        for row in rows:
            logical = _logical_season(row.season, scope)
            if logical is not None:
                counts[logical] += 1
        matches = sum(counts.values())
        freezes = matches * len(FREEZES)
        labels = matches * 2
        total_matches += matches
        total_market_freezes += freezes
        total_lineup_labels += labels
        reports[cid] = {
            "mandatory_seasons": scope.get("mandatory_seasons"),
            "completed_matches_currently_available": matches,
            "completed_matches_by_season": dict(counts),
            "planned_market_freeze_points": freezes,
            "planned_lineup_team_match_labels": labels,
            "planned_injury_suspension_fixture_checks": matches,
            "market_freezes": FREEZES,
            "forward_capture_target": scope.get("forward_capture_target"),
            "stage_gate": scope.get("stage_gate"),
        }

    return {
        "schema_version": "V4.7.6-two-season-backfill-plan",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors and len(reports) == 17 else "FAIL",
        "competition_count": len(reports),
        "errors": errors,
        "totals": {
            "completed_matches_currently_available": total_matches,
            "planned_market_freeze_points": total_market_freezes,
            "planned_lineup_team_match_labels": total_lineup_labels,
            "planned_injury_suspension_fixture_checks": total_matches,
        },
        "reports": reports,
        "execution_policy": {
            "historical_market": "query only fixtures in this plan; never scan older history; validate complete 1X2/AH/OU before persistence",
            "lineup": "use observed XI labels for model training; preserve PIT status separately",
            "injury_suspension": "accept only source observations at or before the target freeze",
            "cost_control": "paid provider collectors must support per-competition and per-season execution plus dry-run before network calls",
        },
        "formal_weight_change": False,
        "automatic_promotion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": result["status"],
            "competition_count": result["competition_count"],
            "totals": result["totals"],
            "error_count": len(result["errors"]),
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and result["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
