#!/usr/bin/env python3
"""R41 zero-label kickoff-identity audit for the high-frequency FPL cache.

Purpose: replace the provisional pre-season kickoff timestamps used by the source
coverage audit with the latest score-free fixture metadata snapshot available at
least six hours before each fixture. Then re-run only the 6h/10d FPL-cache
coverage check. No match result is copied, model is fit/scored, or threshold tuned.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import statistics
import subprocess
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

SEASON = "2025-26"
MIN_ROWS = 150
MIN_LEAD_HOURS = 6.0
MAX_STALENESS_DAYS = 10.0
PINNED_CACHE_HEAD = "19f12ec27dc670ebd79e5353585e67f333d070e3"


def parse_dt(x: str) -> datetime:
    return datetime.fromisoformat(x.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def git_show(repo: Path, sha: str, path: str) -> str | None:
    try:
        return run_git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(text.splitlines()))


def truthy(v: object) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y"}


def snapshot_time(path: Path, root: Path) -> datetime:
    rel = path.relative_to(root)
    y, m, d = map(int, rel.parts[-4:-1])
    hhmm = path.name.split(".", 1)[0]
    return datetime(y, m, d, int(hhmm[:2]), int(hhmm[2:4]), tzinfo=timezone.utc)


def stable(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vaastav-dir", required=True, type=Path)
    ap.add_argument("--cache-dir", required=True, type=Path)
    ap.add_argument("--cache-head", required=True)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.cache_head != PINNED_CACHE_HEAD:
        raise SystemExit(f"FAIL_CLOSED: cache head {args.cache_head} != pinned {PINNED_CACHE_HEAD}")

    fixture_path = f"data/{SEASON}/fixtures.csv"
    log = run_git(args.vaastav_dir, "log", "--format=%H\t%cI", "--", fixture_path)
    commits: list[tuple[str, datetime]] = []
    for line in log.splitlines():
        if line.strip():
            sha, ts = line.split("\t", 1)
            commits.append((sha, parse_dt(ts)))
    commits.sort(key=lambda x: x[1])
    if not commits:
        raise SystemExit("FAIL_CLOSED: no fixture history commits")

    assigned: dict[str, dict] = {}
    score_bearing_rows_skipped = 0
    started_rows_skipped = 0
    for sha, cdt in commits:
        text = git_show(args.vaastav_dir, sha, fixture_path)
        if text is None:
            continue
        for fx in rows(text):
            kickoff_raw = str(fx.get("kickoff_time", "") or "").strip()
            fid = str(fx.get("id", "") or "").strip()
            if not kickoff_raw or not fid:
                continue
            kickoff = parse_dt(kickoff_raw)
            lead = (kickoff - cdt).total_seconds() / 3600.0
            if lead < MIN_LEAD_HOURS:
                continue
            # Same zero-label convention as the already-audited R40/R41 PIT builder:
            # score-bearing/started rows are excluded and never copied into evidence.
            if truthy(fx.get("finished")) or truthy(fx.get("started")):
                started_rows_skipped += 1
                continue
            if str(fx.get("team_h_score", "") or "").strip() or str(fx.get("team_a_score", "") or "").strip():
                score_bearing_rows_skipped += 1
                continue
            candidate = {
                "fixture_id": fid,
                "kickoff_time": kickoff.isoformat(),
                "team_h": str(fx.get("team_h", "") or "").strip(),
                "team_a": str(fx.get("team_a", "") or "").strip(),
                "fixture_snapshot_sha": sha,
                "fixture_snapshot_time": cdt.isoformat(),
                "fixture_snapshot_lead_hours": round(lead, 6),
            }
            prev = assigned.get(fid)
            if prev is None or candidate["fixture_snapshot_time"] > prev["fixture_snapshot_time"]:
                assigned[fid] = candidate

    identities = sorted(assigned.values(), key=lambda x: (x["kickoff_time"], int(x["fixture_id"])))
    cache_root = args.cache_dir.resolve()
    paths = sorted(cache_root.glob("*/*/*/*.json.xz"))
    tp = sorted((snapshot_time(p, cache_root), p) for p in paths)
    ts = [x[0].timestamp() for x in tp]
    eligible: list[dict] = []
    leads: list[float] = []
    gaps: list[float] = []
    schema_checked: set[str] = set()
    schema_failures: list[str] = []
    future_news_violations = 0

    for fx in identities:
        kickoff = parse_dt(fx["kickoff_time"])
        idx = bisect_right(ts, kickoff.timestamp() - MIN_LEAD_HOURS * 3600.0) - 1
        if idx < 1:
            continue
        cur_t, cur_p = tp[idx]
        lead = (kickoff - cur_t).total_seconds() / 3600.0
        if lead < MIN_LEAD_HOURS or lead > MAX_STALENESS_DAYS * 24.0:
            continue
        prev_t, prev_p = tp[idx - 1]
        gap = (cur_t - prev_t).total_seconds() / 3600.0
        for p, pdt in ((cur_p, cur_t), (prev_p, prev_t)):
            rel = str(p.relative_to(cache_root))
            if rel in schema_checked:
                continue
            schema_checked.add(rel)
            with lzma.open(p, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            elems = d.get("elements") or []
            if not elems:
                schema_failures.append(rel + ":no-elements")
                continue
            need = {"id", "team", "element_type", "status", "news", "news_added", "chance_of_playing_this_round", "minutes", "starts", "bps"}
            miss = sorted(need.difference(elems[0].keys()))
            if miss:
                schema_failures.append(rel + ":missing=" + ",".join(miss))
            for e in elems:
                na = e.get("news_added")
                if na:
                    try:
                        if parse_dt(str(na)) > pdt:
                            future_news_violations += 1
                    except ValueError:
                        schema_failures.append(rel + ":bad-news-added")
                        break
        eligible.append({
            **fx,
            "current_snapshot": str(cur_p.relative_to(cache_root)),
            "current_snapshot_time": cur_t.isoformat(),
            "previous_snapshot": str(prev_p.relative_to(cache_root)),
            "previous_snapshot_time": prev_t.isoformat(),
            "lead_hours": round(lead, 6),
            "transition_gap_hours": round(gap, 6),
            "target_labels_read": 0,
            "target_score_values_accessed": 0,
        })
        leads.append(lead)
        gaps.append(gap)

    payload_sha = hashlib.sha256(stable(eligible)).hexdigest()
    pass_gate = len(eligible) >= MIN_ROWS and not schema_failures and future_news_violations == 0
    status = "PASS_R41_FPLCACHE_KICKOFF_IDENTITY_ZERO_LABEL_GATE" if pass_gate else "FAIL_R41_FPLCACHE_KICKOFF_IDENTITY_ZERO_LABEL_GATE"
    receipt = {
        "status": status,
        "season": SEASON,
        "fixture_history_commit_count": len(commits),
        "pit_fixture_identity_count": len(identities),
        "eligible_fixture_count": len(eligible),
        "minimum_period_rows_inherited_from_r40": MIN_ROWS,
        "minimum_lead_hours": MIN_LEAD_HOURS,
        "maximum_staleness_days": MAX_STALENESS_DAYS,
        "cache_head": args.cache_head,
        "selected_unique_cache_snapshots": len(schema_checked),
        "schema_failure_count": len(schema_failures),
        "future_news_timestamp_violations": future_news_violations,
        "lead_hours_min": min(leads) if leads else None,
        "lead_hours_median": statistics.median(leads) if leads else None,
        "lead_hours_max": max(leads) if leads else None,
        "transition_gap_hours_min": min(gaps) if gaps else None,
        "transition_gap_hours_median": statistics.median(gaps) if gaps else None,
        "transition_gap_hours_max": max(gaps) if gaps else None,
        "manifest_sha256": payload_sha,
        "score_bearing_rows_skipped": score_bearing_rows_skipped,
        "started_rows_skipped": started_rows_skipped,
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
        "interpretation": "Kickoff identity + source coverage gate only. PASS still does not authorize opening outcomes or claim R41 effect.",
    }
    (args.out / "kickoff_identity_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "kickoff_identity_transition_manifest.json").write_bytes(stable(eligible))
    (args.out / "schema_failures.json").write_text(json.dumps(schema_failures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
