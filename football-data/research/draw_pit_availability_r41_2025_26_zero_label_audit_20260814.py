#!/usr/bin/env python3
"""R41 third-period zero-label PIT coverage audit for 2025/26.

This reuses the already-audited R40 historical-PIT reconstruction code and the
same time gates (>=6h before kickoff, <=10 days stale). It does not read final
results, train, score, tune, or modify the frozen R41 candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

from draw_pit_availability_zero_label_audit_20260814 import (
    build_team_snapshot,
    csv_rows,
    git_show,
    run_git,
    season_commits,
    snapshot_candidates,
    stable_json,
)

SEASON = "2025-26"
# Inherited from the frozen R40 season/test-fold minimum; not relaxed for R41.
MIN_PERIOD_ROWS = 150
REQUIRED_COLUMNS = {"status", "news", "news_added", "team", "element_type"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    source = Path(args.source_dir).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    source_head = run_git(source, "rev-parse", "HEAD").strip()
    source_remote = run_git(source, "config", "--get", "remote.origin.url").strip()

    rows, season_summary = snapshot_candidates(source, SEASON)
    commits = season_commits(source, SEASON)
    commit_index = {sha: idx for idx, (sha, _) in enumerate(commits)}
    previous_cache: Dict[str, Tuple[Dict[str, object], dict, datetime] | None] = {}

    transition_rows = []
    for row in rows:
        current_sha = str(row["snapshot_sha"])
        idx = commit_index.get(current_sha)
        if idx is None or idx <= 0:
            continue
        prev_sha, prev_dt = commits[idx - 1]
        cache_key = prev_sha
        if cache_key not in previous_cache:
            players_text = git_show(source, prev_sha, f"data/{SEASON}/players_raw.csv")
            if players_text is None:
                previous_cache[cache_key] = None
            else:
                teams, schema = build_team_snapshot(csv_rows(players_text), prev_dt)
                previous_cache[cache_key] = (teams, schema, prev_dt)
        cached = previous_cache[cache_key]
        if cached is None:
            continue
        prev_teams, prev_schema, prev_dt = cached
        if not REQUIRED_COLUMNS.issubset(set(prev_schema.get("columns", []))):
            continue
        home = str(row["home_team_id"])
        away = str(row["away_team_id"])
        if home not in prev_teams or away not in prev_teams:
            continue
        current_dt = datetime.fromisoformat(str(row["snapshot_time"]))
        gap_days = (current_dt - prev_dt).total_seconds() / 86400.0
        transition_rows.append({
            "identity_key": row["identity_key"],
            "season": SEASON,
            "fixture_id": row["fixture_id"],
            "event": row["event"],
            "kickoff_time": row["kickoff_time"],
            "current_snapshot_sha": current_sha,
            "current_snapshot_time": row["snapshot_time"],
            "previous_snapshot_sha": prev_sha,
            "previous_snapshot_time": prev_dt.isoformat(),
            "transition_gap_days": round(gap_days, 6),
            "lead_hours": row["lead_hours"],
            "target_labels_read": 0,
            "target_score_values_accessed": 0,
        })

    gaps = [float(r["transition_gap_days"]) for r in transition_rows]
    payload_sha = hashlib.sha256(stable_json(transition_rows).encode("utf-8")).hexdigest()

    with (out / "r41_2025_26_transition_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(transition_rows, f, ensure_ascii=False, indent=2, sort_keys=True)

    eligible = len(rows)
    transition_eligible = len(transition_rows)
    gate_pass = eligible >= MIN_PERIOD_ROWS and transition_eligible >= MIN_PERIOD_ROWS
    status = "PASS_R41_THIRD_PERIOD_ZERO_LABEL_COVERAGE" if gate_pass else "FAIL_R41_THIRD_PERIOD_ZERO_LABEL_COVERAGE"
    receipt = {
        "schema_version": "R41-THIRD-PERIOD-ZERO-LABEL-COVERAGE-R1",
        "status": status,
        "season": SEASON,
        "source_repository": source_remote,
        "source_checked_out_head": source_head,
        "minimum_lead_hours": 6.0,
        "maximum_staleness_days": 10.0,
        "minimum_period_rows_inherited_from_r40": MIN_PERIOD_ROWS,
        "snapshot_commit_count": len(commits),
        "eligible_fixture_count": eligible,
        "transition_eligible_fixture_count": transition_eligible,
        "transition_gap_days_min": min(gaps) if gaps else None,
        "transition_gap_days_median": statistics.median(gaps) if gaps else None,
        "transition_gap_days_max": max(gaps) if gaps else None,
        "target_labels_read": 0,
        "target_score_values_accessed": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "formal_model_data_config_current_changes": [0, 0, 0, 0],
        "transition_manifest_sha256": payload_sha,
        "season_snapshot_summary": season_summary,
        "interpretation": (
            "This gate answers only whether 2025/26 has enough frozen-rule PIT and previous-snapshot transition coverage "
            "to justify a later separately authorized label opening. FAIL closes this period without relaxing the 6h/10d gates."
        ),
    }
    with (out / "r41_2025_26_zero_label_receipt.json").open("w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(json.dumps({
        "status": status,
        "snapshot_commit_count": len(commits),
        "eligible_fixture_count": eligible,
        "transition_eligible_fixture_count": transition_eligible,
        "target_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "transition_manifest_sha256": payload_sha,
    }, ensure_ascii=False, sort_keys=True))
    # A scientific coverage FAIL is a valid audited outcome, not an infrastructure failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
