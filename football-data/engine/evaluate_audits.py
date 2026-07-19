#!/usr/bin/env python3
"""Aggregate immutable postmatch audits without modifying frozen predictions."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from platform_core import ROOT, PlatformError, atomic_write_json, load_json

AUDIT_ROOT = ROOT / "postmatch_audits"
OUTPUT_PATH = ROOT / "manifests" / "postmatch_evaluation_summary.json"


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None


def run(write: bool = True) -> dict[str, Any]:
    audits = []
    if AUDIT_ROOT.exists():
        for path in sorted(AUDIT_ROOT.rglob("*.json")):
            item = load_json(path)
            item["_path"] = str(path.relative_to(ROOT))
            audits.append(item)
    by_competition: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for audit in audits:
        parts = Path(audit["_path"]).parts
        competition_id = parts[-2] if len(parts) >= 2 else "unknown"
        by_competition[competition_id].append(audit)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        one_x_two = [item["scores"]["one_x_two"] for item in items if "one_x_two" in item.get("scores", {})]
        totals = [item["scores"]["total_goals"] for item in items if "total_goals" in item.get("scores", {})]
        exact = [item["scores"]["exact_score"] for item in items if "exact_score" in item.get("scores", {})]
        return {
            "audits": len(items),
            "one_x_two": {
                "count": len(one_x_two),
                "mean_log_score": _mean([float(item["log_score"]) for item in one_x_two]),
                "mean_brier_score": _mean([float(item["brier_score"]) for item in one_x_two]),
                "mean_rps": _mean([float(item["rps"]) for item in one_x_two]),
                "top1_accuracy": _mean([1.0 if item["top1_hit"] else 0.0 for item in one_x_two]),
            },
            "total_goals": {
                "count": len(totals),
                "mean_log_score": _mean([float(item["log_score"]) for item in totals]),
                "mean_rps": _mean([float(item["rps"]) for item in totals]),
                "top1_accuracy": _mean([1.0 if item["top1_hit"] else 0.0 for item in totals]),
                "top2_accuracy": _mean([1.0 if item["top2_hit"] else 0.0 for item in totals]),
            },
            "exact_score": {
                "count": len(exact),
                "mean_log_score": _mean([float(item["log_score"]) for item in exact]),
                "top1_accuracy": _mean([1.0 if item["top1_hit"] else 0.0 for item in exact]),
                "top3_accuracy": _mean([1.0 if item["top3_hit"] else 0.0 for item in exact]),
                "top5_accuracy": _mean([1.0 if item["top5_hit"] else 0.0 for item in exact]),
            }
        }

    report = {
        "schema_version": "1.0",
        "status": "no_audits_yet" if not audits else "available",
        "overall": summarize(audits),
        "by_competition": {key: summarize(value) for key, value in sorted(by_competition.items())},
        "discipline": {
            "accuracy_is_not_a_substitute_for_proper_scoring": True,
            "promotion_requires_time_ordered_prospective_samples": True,
            "single_match_hits_do_not_promote_models": True
        }
    }
    if write:
        atomic_write_json(OUTPUT_PATH, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        report = run(write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
