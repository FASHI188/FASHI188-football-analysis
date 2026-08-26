#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
SEASON = "2526"
DIVISIONS = ("E0", "D1", "I1", "SP1", "F1")
START_DATE = "2026-03-01"
TARGET_ROWS = 100
SAFE_COLUMNS = ("Date", "HomeTeam", "AwayTeam")
BASE = "https://www.football-data.co.uk/mmz4281"


def parse_date(s: str) -> str:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unsupported Date format: {s!r}")


def load_safe_rows(div: str):
    url = f"{BASE}/{SEASON}/{div}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "football3-batch001/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not all(c in reader.fieldnames for c in SAFE_COLUMNS):
        raise RuntimeError(f"missing safe columns for {div}: {reader.fieldnames}")
    rows = []
    for r in reader:
        # Governance: intentionally touch ONLY the three safe pre-selection columns.
        ds = (r.get("Date") or "").strip()
        home = (r.get("HomeTeam") or "").strip()
        away = (r.get("AwayTeam") or "").strip()
        if not ds or not home or not away:
            continue
        d = parse_date(ds)
        if d < START_DATE:
            continue
        rows.append({"date": d, "division": div, "home": home, "away": away})
    return rows, url


def canonical_hash(rows) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run():
    candidates = []
    sources = {}
    for div in DIVISIONS:
        rows, url = load_safe_rows(div)
        sources[div] = url
        candidates.extend(rows)
    candidates.sort(key=lambda x: (x["date"], x["division"], x["home"], x["away"]))
    if len(candidates) < TARGET_ROWS:
        raise RuntimeError(f"insufficient candidates: {len(candidates)} < {TARGET_ROWS}")

    locked = []
    for i, x in enumerate(candidates[:TARGET_ROWS], start=1):
        locked.append({
            "batch_index": i,
            **x,
            "cutoff_rule": "T-24h from official kickoff; exact kickoff must be resolved before pre-match research",
            "prediction_status": "UNRESEARCHED_UNSCORED",
        })

    summary = {
        "schema_version": "football3-batch001-lock-v1",
        "status": "LOCKED",
        "purpose": "first 100-match retrospective pseudo-prospective cohort for 1X2 Top1 improvement toward 60%+",
        "selection": {
            "season": SEASON,
            "divisions_predeclared": list(DIVISIONS),
            "start_date_predeclared": START_DATE,
            "target_rows": TARGET_ROWS,
            "ordering": ["date", "division", "home", "away"],
            "selected_rows": len(locked),
            "first_date": locked[0]["date"],
            "last_date": locked[-1]["date"],
        },
        "governance": {
            "source_files_may_contain_outcomes": True,
            "safe_columns_read": list(SAFE_COLUMNS),
            "outcome_columns_read": False,
            "selection_uses_results": False,
            "selection_uses_odds": False,
            "selection_uses_postmatch_stats": False,
            "cohort_locked_before_match_research": True,
            "cohort_locked_before_model_predictions": True,
            "reveal_results_only_after_all_100_predictions_locked": True,
            "baseline": "S60 frozen",
            "primary_metric": "1X2 Top1 accuracy",
        },
        "sources": sources,
        "cohort_sha256": canonical_hash(locked),
        "rows": locked,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "batch001_locked_100.json"
    p.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "selection", "cohort_sha256")}, indent=2, ensure_ascii=False))


def verify():
    p = OUT / "batch001_locked_100.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    g = s["governance"]
    rows = s["rows"]
    assert s["status"] == "LOCKED"
    assert len(rows) == TARGET_ROWS
    assert g["outcome_columns_read"] is False
    assert g["selection_uses_results"] is False
    assert g["selection_uses_odds"] is False
    assert g["cohort_locked_before_match_research"] is True
    assert g["reveal_results_only_after_all_100_predictions_locked"] is True
    assert s["cohort_sha256"] == canonical_hash(rows)
    assert [r["batch_index"] for r in rows] == list(range(1, TARGET_ROWS + 1))
    print("BATCH001_LOCK_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: lock_batch001.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
