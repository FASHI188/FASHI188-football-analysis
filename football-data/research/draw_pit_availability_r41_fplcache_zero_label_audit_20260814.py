#!/usr/bin/env python3
"""R41 high-frequency FPL cache zero-label coverage audit.

Research-only source qualification. This script MUST NOT read match outcomes, fit a
model, score predictions, tune thresholds, or modify formal assets.

It tests whether Randdalf/fplcache can provide a current + immediately-previous
FPL bootstrap snapshot for enough 2025/26 EPL fixtures while preserving the frozen
R41 lead/staleness gates (>=6h before kickoff, <=10d stale). Fixture identity comes
from the score-free pre-season vaastav fixture snapshot pinned below.

A PASS here is only a source/coverage PASS. It does not authorize label opening or
claim R41 predictive validity. Actual-kickoff identity must still be separately
verified before any confirmation test because later-season fixture times may move.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import statistics
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

SEASON = "2025-26"
MIN_ROWS = 150
MIN_LEAD_HOURS = 6.0
MAX_STALENESS_DAYS = 10.0
SCHEDULE_COMMIT = "46277afda009b340e738336dabc4b0822dd80c57"
REQUIRED_ELEMENT_FIELDS = {
    "id",
    "team",
    "element_type",
    "status",
    "news",
    "news_added",
    "chance_of_playing_this_round",
    "minutes",
    "starts",
    "bps",
}
FORBIDDEN_TARGET_FIELDS = {
    "team_h_score",
    "team_a_score",
    "finished",
    "finished_provisional",
    "stats",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--cache-dir", required=True, type=Path)
    p.add_argument("--fixtures-csv", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--cache-head", default="UNKNOWN")
    return p.parse_args()


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_snapshot_time(path: Path, cache_root: Path) -> datetime:
    rel = path.relative_to(cache_root)
    # cache/{year}/{month}/{day}/{HHMM}.json.xz
    year, month, day = int(rel.parts[-4]), int(rel.parts[-3]), int(rel.parts[-2])
    hhmm = path.name.split(".", 1)[0]
    hour, minute = int(hhmm[:2]), int(hhmm[2:4])
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def load_safe_fixture_identity(path: Path) -> list[dict]:
    """Load only score-free pre-season identity fields.

    The pinned source commit predates GW1 and the score columns are blank. We still
    fail closed if any score value is present, so no outcome-bearing schedule file
    can silently enter this audit.
    """
    out: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"id", "kickoff_time", "team_h", "team_a", "team_h_score", "team_a_score"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"fixture schema missing: {sorted(missing)}")
        for row in reader:
            if (row.get("team_h_score") or "").strip() or (row.get("team_a_score") or "").strip():
                raise SystemExit("FAIL_CLOSED: score-bearing fixture metadata detected")
            kickoff = (row.get("kickoff_time") or "").strip()
            if not kickoff:
                continue
            dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append(
                {
                    "fixture_id": int(row["id"]),
                    "kickoff": dt,
                    "team_h": int(row["team_h"]),
                    "team_a": int(row["team_a"]),
                }
            )
    return out


def load_bootstrap(path: Path) -> dict:
    with lzma.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def validate_snapshot_schema(path: Path) -> dict:
    data = load_bootstrap(path)
    elements = data.get("elements")
    teams = data.get("teams")
    if not isinstance(elements, list) or not elements:
        raise SystemExit(f"FAIL_CLOSED: no elements in {path}")
    if not isinstance(teams, list) or len(teams) != 20:
        raise SystemExit(f"FAIL_CLOSED: team table invalid in {path}")
    missing = REQUIRED_ELEMENT_FIELDS.difference(elements[0].keys())
    if missing:
        raise SystemExit(f"FAIL_CLOSED: missing element fields {sorted(missing)} in {path}")
    # Data-level checks relevant to PIT availability, not outcomes.
    future_news_violations = 0
    for e in elements:
        for key in REQUIRED_ELEMENT_FIELDS:
            if key not in e:
                raise SystemExit(f"FAIL_CLOSED: element missing {key} in {path}")
        na = e.get("news_added")
        if na:
            try:
                ndt = datetime.fromisoformat(str(na).replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError as exc:
                raise SystemExit(f"FAIL_CLOSED: bad news_added {na!r} in {path}") from exc
            snap_dt = parse_snapshot_time(path, path.parents[3])
            if ndt > snap_dt:
                future_news_violations += 1
    return {
        "element_count": len(elements),
        "team_count": len(teams),
        "future_news_timestamp_violations": future_news_violations,
    }


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, round((len(s) - 1) * q)))
    return s[idx]


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cache_root = args.cache_dir.resolve()
    fixtures = load_safe_fixture_identity(args.fixtures_csv)
    snapshot_paths = sorted(cache_root.glob("*/*/*/*.json.xz"))
    if not snapshot_paths:
        raise SystemExit("FAIL_CLOSED: no cache snapshots found")

    times_and_paths = sorted((parse_snapshot_time(p, cache_root), p) for p in snapshot_paths)
    times = [x[0] for x in times_and_paths]
    selected: list[dict] = []
    lead_hours: list[float] = []
    gaps_hours: list[float] = []
    unique_snapshot_paths: set[Path] = set()

    max_stale_h = MAX_STALENESS_DAYS * 24.0
    for fx in fixtures:
        latest_allowed = fx["kickoff"].timestamp() - MIN_LEAD_HOURS * 3600.0
        idx = bisect_right([t.timestamp() for t in times], latest_allowed) - 1
        if idx < 1:
            continue
        current_t, current_p = times_and_paths[idx]
        lead = (fx["kickoff"] - current_t).total_seconds() / 3600.0
        if lead < MIN_LEAD_HOURS - 1e-9 or lead > max_stale_h + 1e-9:
            continue
        prev_t, prev_p = times_and_paths[idx - 1]
        gap = (current_t - prev_t).total_seconds() / 3600.0
        if gap <= 0:
            raise SystemExit("FAIL_CLOSED: non-positive snapshot transition gap")
        selected.append(
            {
                "fixture_id": fx["fixture_id"],
                "kickoff_time": iso_z(fx["kickoff"]),
                "team_h": fx["team_h"],
                "team_a": fx["team_a"],
                "current_snapshot": str(current_p.relative_to(cache_root)),
                "current_snapshot_time": iso_z(current_t),
                "previous_snapshot": str(prev_p.relative_to(cache_root)),
                "previous_snapshot_time": iso_z(prev_t),
                "lead_hours": round(lead, 6),
                "transition_gap_hours": round(gap, 6),
            }
        )
        lead_hours.append(lead)
        gaps_hours.append(gap)
        unique_snapshot_paths.update({current_p, prev_p})

    schema_receipts = []
    future_news_violations = 0
    for p in sorted(unique_snapshot_paths):
        r = validate_snapshot_schema(p)
        future_news_violations += r["future_news_timestamp_violations"]
        schema_receipts.append({"snapshot": str(p.relative_to(cache_root)), **r})

    selected.sort(key=lambda x: (x["kickoff_time"], x["fixture_id"]))
    manifest_bytes = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    coverage_pass = len(selected) >= MIN_ROWS and future_news_violations == 0
    status = (
        "PASS_R41_FPLCACHE_ZERO_LABEL_SOURCE_COVERAGE_PENDING_KICKOFF_IDENTITY_AUDIT"
        if coverage_pass
        else "FAIL_R41_FPLCACHE_ZERO_LABEL_SOURCE_COVERAGE"
    )

    receipt = {
        "status": status,
        "season": SEASON,
        "source_repo": "https://github.com/Randdalf/fplcache.git",
        "source_cache_head": args.cache_head,
        "schedule_source_repo": "https://github.com/vaastav/Fantasy-Premier-League.git",
        "schedule_score_free_commit": SCHEDULE_COMMIT,
        "schedule_fixture_count": len(fixtures),
        "cache_snapshot_count_in_checkout": len(snapshot_paths),
        "eligible_fixture_count": len(selected),
        "minimum_period_rows_inherited_from_r40": MIN_ROWS,
        "minimum_lead_hours": MIN_LEAD_HOURS,
        "maximum_staleness_days": MAX_STALENESS_DAYS,
        "lead_hours_min": min(lead_hours) if lead_hours else None,
        "lead_hours_median": statistics.median(lead_hours) if lead_hours else None,
        "lead_hours_max": max(lead_hours) if lead_hours else None,
        "transition_gap_hours_min": min(gaps_hours) if gaps_hours else None,
        "transition_gap_hours_median": statistics.median(gaps_hours) if gaps_hours else None,
        "transition_gap_hours_p95": pct(gaps_hours, 0.95),
        "transition_gap_hours_max": max(gaps_hours) if gaps_hours else None,
        "unique_selected_snapshots": len(unique_snapshot_paths),
        "future_news_timestamp_violations": future_news_violations,
        "manifest_sha256": manifest_sha,
        "kickoff_identity_status": "PRESEASON_SNAPSHOT_ONLY_REQUIRES_SEPARATE_ACTUAL_KICKOFF_AUDIT",
        "target_labels_read": 0,
        "target_score_values_accessed": 0,
        "model_fits": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "formal_model_data_config_current_changes": [0, 0, 0, 0],
        "forbidden_target_fields": sorted(FORBIDDEN_TARGET_FIELDS),
        "note": "Source/coverage qualification only. No R41 effect test is authorized by this PASS.",
    }

    (args.out / "coverage_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "transition_manifest.json").write_bytes(manifest_bytes)
    (args.out / "schema_receipts.json").write_text(json.dumps(schema_receipts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
