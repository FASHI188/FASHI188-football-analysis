#!/usr/bin/env python3
"""V5.0.6 shot/event data readiness audit across all registered domains.

The existing processed historical files may contain full-time shots (HS/AS) and
shots on target (HST/AST). These are observed post-match labels that may be used
only for later fixtures under a strict date-before-freeze rule. Shots-on-target
share is a coarse shot-quality proxy; it must never be labelled xG, xT, OBV or
VAEP.

This audit measures field presence, row completeness, numerical validity,
season coverage and source hashes. It does not train a model or change any
formal probability.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
REGISTRY = FOOTBALL / "config" / "platform_registry.json"
OUT = FOOTBALL / "manifests" / "shot_event_data_readiness_v506_status.json"

SHOT_FIELDS = ("HS", "AS", "HST", "AST")
QUANTITY_FIELDS = ("HS", "AS")
QUALITY_FIELDS = ("HST", "AST")
XG_PAIRS = (
    ("HxG", "AxG"),
    ("HXG", "AXG"),
    ("Home_xG", "Away_xG"),
    ("home_xg", "away_xg"),
    ("home_expected_goals", "away_expected_goals"),
)
MIN_COMPLETE_MATCHES = 1000
MIN_SEASONS = 3
MIN_COMPLETE_RATE = 0.95
MAX_INVALID_RATE = 0.001


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def season_value(row: dict[str, str], path: Path) -> str:
    value = str(row.get("season") or row.get("Season") or "").strip()
    if value:
        return value
    return path.stem.replace("-", "/")


def paired_xg_fields(fieldnames: list[str]) -> tuple[str, str] | None:
    fields = set(fieldnames)
    for home, away in XG_PAIRS:
        if home in fields and away in fields:
            return home, away
    return None


def audit_domain(competition_id: str) -> dict[str, Any]:
    directory = FOOTBALL / "processed" / competition_id
    files = sorted(directory.glob("*.csv")) if directory.is_dir() else []
    total_rows = 0
    quantity_complete = 0
    quartet_complete = 0
    xg_complete = 0
    invalid_quantity = 0
    invalid_quality = 0
    logical_violations = 0
    seasons: set[str] = set()
    field_union: set[str] = set()
    per_season: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "rows": 0,
            "quantity_complete": 0,
            "quartet_complete": 0,
            "xg_complete": 0,
            "invalid_rows": 0,
        }
    )
    file_reports: list[dict[str, Any]] = []

    for path in files:
        rows = 0
        file_quantity = 0
        file_quartet = 0
        file_xg = 0
        file_invalid = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = [str(item) for item in (reader.fieldnames or []) if item]
            field_union.update(fieldnames)
            xg_pair = paired_xg_fields(fieldnames)
            for row in reader:
                if not any(str(value or "").strip() for value in row.values()):
                    continue
                rows += 1
                total_rows += 1
                season = season_value(row, path)
                seasons.add(season)
                per_season[season]["rows"] += 1

                hs = numeric(row.get("HS"))
                away_shots = numeric(row.get("AS"))
                hst = numeric(row.get("HST"))
                ast = numeric(row.get("AST"))
                quantity_ok = hs is not None and away_shots is not None
                quartet_ok = quantity_ok and hst is not None and ast is not None
                invalid = False

                if quantity_ok:
                    if hs < 0 or away_shots < 0:
                        invalid_quantity += 1
                        invalid = True
                    else:
                        quantity_complete += 1
                        file_quantity += 1
                        per_season[season]["quantity_complete"] += 1
                elif any(str(row.get(field) or "").strip() for field in QUANTITY_FIELDS):
                    invalid_quantity += 1
                    invalid = True

                if quartet_ok:
                    if hst < 0 or ast < 0:
                        invalid_quality += 1
                        invalid = True
                    elif hs is not None and away_shots is not None and (
                        hst > hs + 1e-9 or ast > away_shots + 1e-9
                    ):
                        logical_violations += 1
                        invalid = True
                    else:
                        quartet_complete += 1
                        file_quartet += 1
                        per_season[season]["quartet_complete"] += 1
                elif any(str(row.get(field) or "").strip() for field in QUALITY_FIELDS):
                    invalid_quality += 1
                    invalid = True

                if xg_pair is not None:
                    hxg = numeric(row.get(xg_pair[0]))
                    axg = numeric(row.get(xg_pair[1]))
                    if hxg is not None and axg is not None and hxg >= 0 and axg >= 0:
                        xg_complete += 1
                        file_xg += 1
                        per_season[season]["xg_complete"] += 1

                if invalid:
                    file_invalid += 1
                    per_season[season]["invalid_rows"] += 1

        file_reports.append({
            "path": path.relative_to(FOOTBALL).as_posix(),
            "sha256": sha256_file(path),
            "rows": rows,
            "field_count": len(fieldnames),
            "shot_fields_present": {field: field in fieldnames for field in SHOT_FIELDS},
            "paired_xg_fields": list(xg_pair) if xg_pair else None,
            "quantity_complete": file_quantity,
            "quartet_complete": file_quartet,
            "xg_complete": file_xg,
            "invalid_rows": file_invalid,
        })

    quantity_rate = quantity_complete / total_rows if total_rows else 0.0
    quartet_rate = quartet_complete / total_rows if total_rows else 0.0
    xg_rate = xg_complete / total_rows if total_rows else 0.0
    invalid_total = invalid_quantity + invalid_quality + logical_violations
    invalid_rate = invalid_total / total_rows if total_rows else 0.0
    quartet_ready = (
        quartet_complete >= MIN_COMPLETE_MATCHES
        and len(seasons) >= MIN_SEASONS
        and quartet_rate >= MIN_COMPLETE_RATE
        and invalid_rate <= MAX_INVALID_RATE
    )
    quantity_ready = (
        quantity_complete >= MIN_COMPLETE_MATCHES
        and len(seasons) >= MIN_SEASONS
        and quantity_rate >= MIN_COMPLETE_RATE
        and invalid_rate <= MAX_INVALID_RATE
    )
    xg_ready = (
        xg_complete >= MIN_COMPLETE_MATCHES
        and len(seasons) >= MIN_SEASONS
        and xg_rate >= MIN_COMPLETE_RATE
    )

    if quartet_ready:
        status = "SHOT_QUANTITY_QUALITY_PROXY_READY_FOR_SHADOW"
    elif quantity_ready:
        status = "SHOT_QUANTITY_ONLY_READY_FOR_SHADOW"
    elif quantity_complete or quartet_complete:
        status = "SHOT_DATA_PARTIAL_BELOW_GATE"
    else:
        status = "SHOT_DATA_UNAVAILABLE"

    return {
        "competition_id": competition_id,
        "status": status,
        "processed_directory_present": directory.is_dir(),
        "processed_file_count": len(files),
        "total_rows": total_rows,
        "season_count": len(seasons),
        "seasons": sorted(seasons),
        "field_union_contains": {
            "HS": "HS" in field_union,
            "AS": "AS" in field_union,
            "HST": "HST" in field_union,
            "AST": "AST" in field_union,
        },
        "quantity_complete_rows": quantity_complete,
        "quantity_complete_rate": quantity_rate,
        "quartet_complete_rows": quartet_complete,
        "quartet_complete_rate": quartet_rate,
        "xg_complete_rows": xg_complete,
        "xg_complete_rate": xg_rate,
        "invalid_quantity_count": invalid_quantity,
        "invalid_quality_count": invalid_quality,
        "shot_on_target_exceeds_shots_count": logical_violations,
        "invalid_rate": invalid_rate,
        "quantity_ready": quantity_ready,
        "quality_proxy_ready": quartet_ready,
        "true_xg_ready": xg_ready,
        "per_season": dict(sorted(per_season.items())),
        "files": file_reports,
        "timestamp_policy": {
            "input_type": "observed_post_match_statistics",
            "target_fixture_own_stats_prohibited": True,
            "same_day_prior_rows_prohibited_when_exact_completion_time_missing": True,
            "eligible_history": "source match date strictly before target freeze date",
        },
        "semantic_guardrail": "HST/HS and AST/AS are shot-quality proxies only; never label them xG, xT, OBV or VAEP.",
        "formal_weight": 0,
    }


def run(*, write: bool) -> dict[str, Any]:
    registry = load_json(REGISTRY)
    domains = [
        str(item["competition_id"])
        for item in registry.get("competitions", [])
        if isinstance(item, dict) and item.get("competition_id")
    ]
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in domains:
        try:
            reports[competition_id] = audit_domain(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"
    quartet_ready = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["quality_proxy_ready"]
    )
    quantity_ready = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["quantity_ready"]
    )
    xg_ready = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["true_xg_ready"]
    )
    payload = {
        "schema_version": "V5.0.6-shot-event-data-readiness-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if len(reports) == len(domains) and not failures else "PARTIAL",
        "competition_count_requested": len(domains),
        "competition_count_completed": len(reports),
        "shot_quantity_ready_domains": quantity_ready,
        "shot_quantity_quality_proxy_ready_domains": quartet_ready,
        "true_xg_ready_domains": xg_ready,
        "reports": reports,
        "failures": failures,
        "minimum_gates": {
            "complete_match_rows": MIN_COMPLETE_MATCHES,
            "seasons": MIN_SEASONS,
            "complete_rate": MIN_COMPLETE_RATE,
            "maximum_invalid_rate": MAX_INVALID_RATE,
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Data-readiness audit only. Ready domains may enter competition-specific shot quantity/quality-proxy shadow OOF; no true event-value claim or formal probability influence is authorized.",
    }
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    payload = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps({
            "status": payload["status"],
            "shot_quantity_ready_domains": payload["shot_quantity_ready_domains"],
            "shot_quantity_quality_proxy_ready_domains": payload["shot_quantity_quality_proxy_ready_domains"],
            "true_xg_ready_domains": payload["true_xg_ready_domains"],
            "failures": payload["failures"],
        }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
