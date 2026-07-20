#!/usr/bin/env python3
"""Extract recent-two-season retrospective market references from processed CSVs.

These records are explicitly NOT point-in-time market snapshots because the source
rows do not preserve original quote timestamps. They may support retrospective
market baselines and cross-checks, but they are ineligible for formal question-time
freeze, KL/LOMO promotion, EV, or A-grade synchronized-market evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "config" / "two_season_evidence_scope_v476.json"
PROCESSED = ROOT / "processed"
OUT_ROOT = ROOT / "evidence" / "markets_retrospective"
MANIFEST = ROOT / "manifests" / "retrospective_market_reference_v476_status.json"

FAMILIES = {
    "bet365": {
        "1x2": ("B365H", "B365D", "B365A"),
        "ah": ("AHh", "B365AHH", "B365AHA"),
        "ou": ("2.5", "B365>2.5", "B365<2.5"),
        "source_class": "bookmaker",
    },
    "pinnacle": {
        "1x2": ("PSH", "PSD", "PSA"),
        "ah": ("AHh", "PAHH", "PAHA"),
        "ou": ("2.5", "P>2.5", "P<2.5"),
        "source_class": "bookmaker",
    },
    "market_average": {
        "1x2": ("AvgH", "AvgD", "AvgA"),
        "ah": ("AHh", "AvgAHH", "AvgAHA"),
        "ou": ("2.5", "Avg>2.5", "Avg<2.5"),
        "source_class": "aggregated_reference",
    },
    "market_maximum": {
        "1x2": ("MaxH", "MaxD", "MaxA"),
        "ah": ("AHh", "MaxAHH", "MaxAHA"),
        "ou": ("2.5", "Max>2.5", "Max<2.5"),
        "source_class": "aggregated_reference",
    },
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value: Any, *, odds: bool = False) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if odds and number <= 1.0:
        return None
    return number


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


def _surface(row: dict[str, str], family: dict[str, Any]) -> dict[str, Any] | None:
    h, d, a = family["1x2"]
    one = [_num(row.get(h), odds=True), _num(row.get(d), odds=True), _num(row.get(a), odds=True)]
    line_col, ah_h, ah_a = family["ah"]
    ah = [_num(row.get(line_col)), _num(row.get(ah_h), odds=True), _num(row.get(ah_a), odds=True)]
    ou_line, over_col, under_col = family["ou"]
    ou = [_num(ou_line), _num(row.get(over_col), odds=True), _num(row.get(under_col), odds=True)]
    if any(v is None for v in one + ah + ou):
        return None
    return {
        "one_x_two": {"home": one[0], "draw": one[1], "away": one[2]},
        "asian_handicap": {"line": ah[0], "home": ah[1], "away": ah[2]},
        "over_under": {"line": ou[0], "over": ou[1], "under": ou[2]},
    }


def extract() -> dict[str, Any]:
    config = _load(SCOPE)
    reports: dict[str, Any] = {}
    total = 0
    for cid, scope in sorted((config.get("competitions") or {}).items()):
        directory = PROCESSED / cid
        output_rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        by_family: Counter[str] = Counter()
        by_season: Counter[str] = Counter()
        source_files: Counter[str] = Counter()
        if directory.exists():
            for path in sorted(directory.glob("*.csv")):
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        row = {str(k).strip(): "" if v is None else str(v).strip() for k, v in raw.items() if k}
                        logical = _logical_season(row.get("season") or row.get("Season"), scope)
                        if logical is None:
                            continue
                        date = row.get("Date") or row.get("date") or ""
                        home = row.get("HomeTeam") or ""
                        away = row.get("AwayTeam") or ""
                        if not date or not home or not away:
                            continue
                        for family_name, family in FAMILIES.items():
                            surface = _surface(row, family)
                            if surface is None:
                                continue
                            key = (logical, date, home, away, family_name)
                            if key in seen:
                                continue
                            seen.add(key)
                            output_rows.append({
                                "competition_id": cid,
                                "season": logical,
                                "date": date,
                                "home_team": home,
                                "away_team": away,
                                "provider_group": "football_data_co_uk",
                                "bookmaker_group": family_name,
                                "source_class": family["source_class"],
                                "source_path": str(path.relative_to(ROOT)),
                                "quote_timestamp_status": "unavailable",
                                "formal_pit_eligible": false,
                                "formal_synchronized_snapshot_eligible": false,
                                "usage": "retrospective_market_reference_only",
                                **surface,
                            })
                            by_family[family_name] += 1
                            by_season[logical] += 1
                            source_files[path.name] += 1
        out = OUT_ROOT / cid / "recent_two_seasons.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in output_rows), encoding="utf-8")
        total += len(output_rows)
        reports[cid] = {
            "mandatory_seasons": scope.get("mandatory_seasons"),
            "rows": len(output_rows),
            "by_family": dict(by_family),
            "by_season": dict(by_season),
            "source_files": dict(source_files),
            "formal_pit_eligible_rows": 0,
        }
    return {
        "schema_version": "V4.7.6-retrospective-market-reference",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "competition_count": len(reports),
        "total_rows": total,
        "reports": reports,
        "formal_pit_eligible_rows": 0,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "policy": "All extracted prices lack original quote timestamps and are retrospective reference only. They must never be used as formal pre-match frozen snapshots or direct EV inputs.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    result = extract()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": result["status"],
            "competition_count": result["competition_count"],
            "total_rows": result["total_rows"],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
