#!/usr/bin/env python3
"""Resolve competition-stage routing where official 2026 format evidence is sufficient.

This module is governance/runtime plumbing only. It does not alter CURRENT or any
formal model weight. It resolves only the stage granularity supported by both the
official format evidence and the frozen source rows.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "stage_format_registry_v470.json"
OUT = ROOT / "manifests" / "stage_gate_resolution_v470_status.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(raw: str) -> datetime:
    raw = str(raw).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {raw!r}")


def read_current_rows(path: Path, season: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            token = str(row.get("season") or row.get("Season") or "").strip()
            if token == season:
                rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
    return rows


def audit_arg(config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / "processed" / "ARG_Primera" / "recent_seasons.csv"
    rows = read_current_rows(path, "2026")
    cutoff = datetime.fromisoformat(config["current_snapshot_rule"]["clausura_first_match_date"])
    counts: Counter[str] = Counter()
    dates = []
    for row in rows:
        dt = parse_date(row["Date"])
        dates.append(dt)
        counts["apertura_2026" if dt < cutoff else "clausura_2026"] += 1
    latest = max(dates).date().isoformat() if dates else None
    earliest = min(dates).date().isoformat() if dates else None
    macro_resolved = bool(rows) and counts["clausura_2026"] == 0 and latest < cutoff.date().isoformat()
    return {
        "competition_id": "ARG_Primera",
        "status": "CURRENT_2026_MACRO_STAGE_RESOLVED_APERTURA_SUBSTAGE_GATED" if macro_resolved else "MACRO_STAGE_MIX_REQUIRES_ROW_LEVEL_SPLIT",
        "current_rows": len(rows),
        "earliest_date": earliest,
        "latest_date": latest,
        "macro_stage_counts": dict(counts),
        "clausura_first_match_date": cutoff.date().isoformat(),
        "macro_stage_calibration_allowed": macro_resolved,
        "substage_calibration_allowed": False,
        "substage_blocker": "zone_phase_vs_knockout rows are not explicitly labeled by the frozen archive",
        "formal_weight_change": False,
    }


def audit_mls(config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / "processed" / "USA_MLS" / "recent_seasons.csv"
    rows = read_current_rows(path, "2026")
    start = datetime.fromisoformat(config["regular_season_start"])
    end = datetime.fromisoformat(config["regular_season_end"])
    playoffs = datetime.fromisoformat(config["playoffs_start"])
    dates = [parse_date(row["Date"]) for row in rows]
    outside = [dt for dt in dates if dt < start or dt > end]
    playoff_overlap = [dt for dt in dates if dt >= playoffs]
    resolved = bool(rows) and not outside and not playoff_overlap
    return {
        "competition_id": "USA_MLS",
        "status": "CURRENT_2026_REGULAR_SEASON_STAGE_RESOLVED" if resolved else "CURRENT_2026_STAGE_MIX_REQUIRES_SPLIT",
        "current_rows": len(rows),
        "earliest_date": min(dates).date().isoformat() if dates else None,
        "latest_date": max(dates).date().isoformat() if dates else None,
        "regular_season_window": [start.date().isoformat(), end.date().isoformat()],
        "playoffs_start": playoffs.date().isoformat(),
        "outside_regular_window_rows": len(outside),
        "playoff_overlap_rows": len(playoff_overlap),
        "current_regular_season_calibration_allowed": resolved,
        "playoff_calibration_allowed": False,
        "formal_weight_change": False,
    }


def audit_future_split(cid: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "competition_id": cid,
        "status": "OFFICIAL_STAGE_FORMAT_REGISTERED_TARGET_SEASON_ROWS_PENDING",
        "target_season": config["season"],
        "split_after_round": config["split_after_round"],
        "pre_split_stage_policy_ready": True,
        "post_split_group_policy_ready": True,
        "target_season_calibration_allowed_now": False,
        "blocker": "verified completed target-season rows are not yet present",
        "formal_weight_change": False,
    }


def main() -> int:
    cfg = load_json(CONFIG)["competitions"]
    reports = {
        "ARG_Primera": audit_arg(cfg["ARG_Primera"]),
        "USA_MLS": audit_mls(cfg["USA_MLS"]),
        "SUI_SuperLeague": audit_future_split("SUI_SuperLeague", cfg["SUI_SuperLeague"]),
        "SCO_Premiership": audit_future_split("SCO_Premiership", cfg["SCO_Premiership"]),
    }
    payload = {
        "schema_version": "V4.7.0-stage-gate-resolution",
        "formal_weight": 0,
        "automatic_promotion": False,
        "reports": reports,
        "summary": {
            "current_macro_stage_resolved": [
                cid for cid, item in reports.items()
                if "RESOLVED" in item["status"]
            ],
            "future_format_registered_rows_pending": [
                cid for cid, item in reports.items()
                if item["status"] == "OFFICIAL_STAGE_FORMAT_REGISTERED_TARGET_SEASON_ROWS_PENDING"
            ],
        },
        "policy": "Only evidence-supported stage granularity is enabled; unresolved substages remain fail-closed.",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
