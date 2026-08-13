#!/usr/bin/env python3
"""Zero-label PIT feasibility audit for historical FPL availability snapshots.

Purpose
-------
Determine whether the public vaastav/Fantasy-Premier-League Git history can
reconstruct genuinely pre-match availability/news snapshots with enough
coverage to justify a later, separately preregistered draw experiment.

This script intentionally does NOT train, score, tune, or read the selected
fixture outcomes. Selected fixtures must still be unfinished with blank scores
in the exact historical snapshot used for them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
MIN_LEAD_HOURS = 6.0
MAX_STALENESS_DAYS = 10.0
FORBIDDEN_MODEL_FIELDS = {"ep_this", "ep_next", "xP"}


def parse_dt(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def truthy(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "null"}:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def run_git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return p.stdout


def git_show(repo: Path, sha: str, path: str) -> Optional[str]:
    p = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        return None
    return p.stdout


def csv_rows(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


@dataclass
class TeamSnapshot:
    team_id: str
    player_rows: int = 0
    non_available_count: int = 0
    doubtful_or_worse_count: int = 0
    chance_known_count: int = 0
    news_count: int = 0
    news_timestamp_valid_count: int = 0
    news_timestamp_future_count: int = 0
    regular_count: int = 0
    regular_risk_count: int = 0
    attack_regular_count: int = 0
    attack_regular_risk_count: int = 0
    attack_xgi_available: float = 0.0
    attack_xgi_at_risk: float = 0.0
    attack_bps_available: float = 0.0
    attack_bps_at_risk: float = 0.0


def build_team_snapshot(rows: List[Dict[str, str]], commit_dt: datetime) -> Tuple[Dict[str, TeamSnapshot], Dict[str, object]]:
    if not rows:
        return {}, {"columns": [], "forbidden_present": [], "availability_columns": []}
    columns = list(rows[0].keys())
    forbidden_present = sorted(FORBIDDEN_MODEL_FIELDS.intersection(columns))
    availability_columns = [
        c for c in (
            "status", "news", "news_added",
            "chance_of_playing_this_round", "chance_of_playing_next_round",
            "minutes", "starts", "element_type", "team",
            "expected_goal_involvements", "bps",
        ) if c in columns
    ]
    by_team: Dict[str, TeamSnapshot] = {}
    for r in rows:
        team = str(r.get("team", "")).strip()
        if not team:
            continue
        s = by_team.setdefault(team, TeamSnapshot(team_id=team))
        s.player_rows += 1
        status = str(r.get("status", "")).strip().lower()
        chance = as_float(r.get("chance_of_playing_this_round"))
        non_available = bool(status and status != "a")
        at_risk = non_available or (chance is not None and chance < 100.0)
        if non_available:
            s.non_available_count += 1
        if status in {"d", "i", "s", "u", "n"}:
            s.doubtful_or_worse_count += 1
        if chance is not None:
            s.chance_known_count += 1
        news = str(r.get("news", "") or "").strip()
        if news:
            s.news_count += 1
            news_added = str(r.get("news_added", "") or "").strip()
            if news_added:
                try:
                    ndt = parse_dt(news_added)
                    if ndt <= commit_dt + timedelta(minutes=5):
                        s.news_timestamp_valid_count += 1
                    else:
                        s.news_timestamp_future_count += 1
                except Exception:
                    pass
        starts = as_float(r.get("starts")) or 0.0
        minutes = as_float(r.get("minutes")) or 0.0
        regular = starts >= 3.0 or minutes >= 270.0
        pos = int(as_float(r.get("element_type")) or 0)
        attack = pos in {3, 4}
        xgi = as_float(r.get("expected_goal_involvements")) or 0.0
        bps = as_float(r.get("bps")) or 0.0
        if regular:
            s.regular_count += 1
            if at_risk:
                s.regular_risk_count += 1
            if attack:
                s.attack_regular_count += 1
                if at_risk:
                    s.attack_regular_risk_count += 1
                    s.attack_xgi_at_risk += xgi
                    s.attack_bps_at_risk += bps
                else:
                    s.attack_xgi_available += xgi
                    s.attack_bps_available += bps
    return by_team, {
        "columns": columns,
        "forbidden_present": forbidden_present,
        "availability_columns": availability_columns,
    }


def season_commits(repo: Path, season: str) -> List[Tuple[str, datetime]]:
    path = f"data/{season}/players_raw.csv"
    out = run_git(repo, "log", "--format=%H\t%cI", "--", path)
    result: List[Tuple[str, datetime]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, ts = line.split("\t", 1)
        result.append((sha, parse_dt(ts)))
    result.sort(key=lambda x: x[1])
    return result


def snapshot_candidates(repo: Path, season: str) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    commits = season_commits(repo, season)
    assigned: Dict[str, Dict[str, object]] = {}
    schema_observations: List[Dict[str, object]] = []
    total_future_news_violations = 0
    snapshots_with_required_availability = 0

    for sha, commit_dt in commits:
        players_text = git_show(repo, sha, f"data/{season}/players_raw.csv")
        fixtures_text = git_show(repo, sha, f"data/{season}/fixtures.csv")
        if players_text is None or fixtures_text is None:
            continue
        players = csv_rows(players_text)
        fixtures = csv_rows(fixtures_text)
        teams, schema = build_team_snapshot(players, commit_dt)
        schema_observations.append({
            "sha": sha,
            "commit_time": commit_dt.isoformat(),
            "availability_columns": schema["availability_columns"],
            "forbidden_present_but_not_used": schema["forbidden_present"],
        })
        required = {"status", "news", "news_added", "team", "element_type"}
        if required.issubset(set(schema["columns"])):
            snapshots_with_required_availability += 1
        total_future_news_violations += sum(t.news_timestamp_future_count for t in teams.values())

        for fx in fixtures:
            kickoff_raw = str(fx.get("kickoff_time", "") or "").strip()
            if not kickoff_raw:
                continue
            kickoff = parse_dt(kickoff_raw)
            lead_hours = (kickoff - commit_dt).total_seconds() / 3600.0
            if lead_hours < MIN_LEAD_HOURS or lead_hours > MAX_STALENESS_DAYS * 24.0:
                continue
            # Target selected at this snapshot must be genuinely unplayed with no score present.
            if truthy(fx.get("finished", "")) or truthy(fx.get("started", "")):
                continue
            if str(fx.get("team_h_score", "") or "").strip() or str(fx.get("team_a_score", "") or "").strip():
                continue
            fixture_id = str(fx.get("id", "")).strip()
            if not fixture_id:
                continue
            home = str(fx.get("team_h", "")).strip()
            away = str(fx.get("team_a", "")).strip()
            if home not in teams or away not in teams:
                continue
            key = f"{season}:{fixture_id}"
            candidate = {
                "identity_key": key,
                "season": season,
                "fixture_id": fixture_id,
                "event": str(fx.get("event", "")).strip(),
                "kickoff_time": kickoff.isoformat(),
                "home_team_id": home,
                "away_team_id": away,
                "snapshot_sha": sha,
                "snapshot_time": commit_dt.isoformat(),
                "lead_hours": round(lead_hours, 6),
                "target_finished_at_snapshot": False,
                "target_score_values_accessed": 0,
                "home": asdict(teams[home]),
                "away": asdict(teams[away]),
            }
            prev = assigned.get(key)
            if prev is None or candidate["snapshot_time"] > prev["snapshot_time"]:
                assigned[key] = candidate

    rows = sorted(assigned.values(), key=lambda r: (r["kickoff_time"], r["fixture_id"]))
    leads = [float(r["lead_hours"]) for r in rows]
    season_summary = {
        "season": season,
        "snapshot_commit_count": len(commits),
        "snapshots_with_required_availability": snapshots_with_required_availability,
        "eligible_fixture_count": len(rows),
        "lead_hours_min": min(leads) if leads else None,
        "lead_hours_median": statistics.median(leads) if leads else None,
        "lead_hours_max": max(leads) if leads else None,
        "future_news_timestamp_violations": total_future_news_violations,
        "schema_observations": schema_observations,
    }
    return rows, season_summary


def stable_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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

    all_rows: List[Dict[str, object]] = []
    summaries: List[Dict[str, object]] = []
    for season in SEASONS:
        rows, summary = snapshot_candidates(source, season)
        all_rows.extend(rows)
        summaries.append(summary)

    # No target result columns are ever copied into the manifest.
    manifest_payload = stable_json(all_rows)
    manifest_sha = hashlib.sha256(manifest_payload.encode("utf-8")).hexdigest()
    with (out / "pit_fixture_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2, sort_keys=True)

    total = len(all_rows)
    seasons_150 = sum(1 for s in summaries if int(s["eligible_fixture_count"]) >= 150)
    selected_target_score_accesses = sum(int(r["target_score_values_accessed"]) for r in all_rows)
    required_snapshot_count = sum(int(s["snapshots_with_required_availability"]) for s in summaries)
    future_news_violations = sum(int(s["future_news_timestamp_violations"]) for s in summaries)

    # Feasibility gate only. This is NOT a model or scientific-effect gate.
    gate_pass = (
        total >= 600
        and seasons_150 >= 3
        and selected_target_score_accesses == 0
        and required_snapshot_count >= 50
    )
    status = "PASS_PIT_SOURCE_FEASIBLE_ZERO_LABEL" if gate_pass else "FAIL_PIT_SOURCE_COVERAGE_ZERO_LABEL"
    receipt = {
        "schema_version": "DRAW-PIT-AVAILABILITY-ZERO-LABEL-AUDIT-R1",
        "status": status,
        "source_repository": source_remote,
        "source_checked_out_head": source_head,
        "seasons": SEASONS,
        "minimum_lead_hours": MIN_LEAD_HOURS,
        "maximum_staleness_days": MAX_STALENESS_DAYS,
        "eligible_fixture_count": total,
        "seasons_with_at_least_150_eligible": seasons_150,
        "snapshots_with_required_availability": required_snapshot_count,
        "target_score_values_accessed": selected_target_score_accesses,
        "target_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "formal_model_data_config_current_changes": [0, 0, 0, 0],
        "forbidden_model_fields_not_used": sorted(FORBIDDEN_MODEL_FIELDS),
        "future_news_timestamp_violations_observed": future_news_violations,
        "manifest_sha256": manifest_sha,
        "season_summaries": summaries,
        "interpretation": (
            "PASS means only that true pre-match availability/news snapshots can be reconstructed at useful scale. "
            "It does not establish draw-prediction effectiveness and does not authorize label access."
        ),
    }
    with (out / "audit_receipt.json").open("w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(json.dumps({
        "status": status,
        "eligible_fixture_count": total,
        "seasons_with_at_least_150_eligible": seasons_150,
        "target_labels_read": 0,
        "manifest_sha256": manifest_sha,
        "future_news_timestamp_violations_observed": future_news_violations,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if gate_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
