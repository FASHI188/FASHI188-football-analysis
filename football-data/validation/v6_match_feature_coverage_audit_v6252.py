#!/usr/bin/env python3
"""V6.25.2 processed match-feature coverage audit.

Read-only research audit. Determines whether the existing processed CSV files
already retain match statistics that the formal MatchRow parser currently drops.
No probability or CURRENT changes.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "processed"
OUT = ROOT / "manifests" / "v6_match_feature_coverage_audit_v6252_status.json"

FEATURES = (
    "HS", "AS", "HST", "AST", "HC", "AC", "HF", "AF", "HY", "AY", "HR", "AR",
    "Avg>2.5", "Avg<2.5", "B365>2.5", "B365<2.5",
)


def _present(value: str | None) -> bool:
    if value is None:
        return False
    token = str(value).strip()
    if not token:
        return False
    try:
        float(token)
        return True
    except ValueError:
        return False


def main() -> int:
    reports: dict[str, Any] = {}
    if not PROCESSED.exists():
        raise RuntimeError(f"missing processed root: {PROCESSED}")
    for directory in sorted(path for path in PROCESSED.iterdir() if path.is_dir()):
        total_rows = 0
        feature_counts = Counter()
        header_union: set[str] = set()
        seasons = defaultdict(lambda: {"rows": 0, "feature_counts": Counter()})
        files = []
        for path in sorted(directory.glob("*.csv")):
            files.append(path.name)
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                header_union.update(reader.fieldnames or [])
                for raw in reader:
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                        continue
                    if not raw.get("FTHG") or not raw.get("FTAG"):
                        continue
                    total_rows += 1
                    season = str(raw.get("season") or raw.get("Season") or "")
                    seasons[season]["rows"] += 1
                    for feature in FEATURES:
                        if _present(raw.get(feature)):
                            feature_counts[feature] += 1
                            seasons[season]["feature_counts"][feature] += 1
        reports[directory.name] = {
            "files": files,
            "row_count": total_rows,
            "header_features_present": [f for f in FEATURES if f in header_union],
            "coverage": {
                f: {
                    "count": int(feature_counts[f]),
                    "rate": feature_counts[f] / total_rows if total_rows else 0.0,
                }
                for f in FEATURES
            },
            "season_coverage": {
                season: {
                    "rows": data["rows"],
                    "coverage": {
                        f: data["feature_counts"][f] / data["rows"] if data["rows"] else 0.0
                        for f in FEATURES
                    },
                }
                for season, data in sorted(seasons.items())
            },
        }
    payload = {
        "schema_version": "V6.25.2-processed-match-feature-coverage-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "classification": "RESEARCH_READ_ONLY_AUDIT",
        "features": list(FEATURES),
        "competition_count": len(reports),
        "reports": reports,
        "governance": {
            "probability_model_changed": False,
            "current_rule_change": False,
            "formal_weight": 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    compact = {
        cid: {
            "rows": report["row_count"],
            "HS": report["coverage"]["HS"]["rate"],
            "HST": report["coverage"]["HST"]["rate"],
            "AS": report["coverage"]["AS"]["rate"],
            "AST": report["coverage"]["AST"]["rate"],
            "Avg>2.5": report["coverage"]["Avg>2.5"]["rate"],
        }
        for cid, report in reports.items()
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
