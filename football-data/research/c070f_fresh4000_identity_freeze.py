#!/usr/bin/env python3
import csv
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SOURCE = "julien-c/kaggle-hugomathien-soccer"
REVISION = "80e14cc7aa624cc266470f43a626652dabdfb80a"
EXPECTED_DB_SHA256 = "4df8569777d59fdd690754b1cc8ca1f7989baf65f2eaddd0f1368285f11139a9"
SEED = "C070F_4000_20260818"
N_SELECT = 4000
EVENT_FIELDS = ["goal", "shoton", "shotoff", "foulcommit", "card", "cross", "corner", "possession"]
IDENTITY_FIELDS = [
    "id", "match_api_id", "country_id", "league_id", "season", "stage", "date",
    "home_team_api_id", "away_team_api_id"
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_identity(row: dict) -> str:
    return "|".join([
        SOURCE,
        str(row["match_api_id"]),
        str(row["date"]),
        str(row["home_team_api_id"]),
        str(row["away_team_api_id"]),
        SEED,
    ])


def selection_hash(row: dict) -> str:
    return hashlib.sha256(canonical_identity(row).encode("utf-8")).hexdigest()


def manifest_digest(rows) -> str:
    h = hashlib.sha256()
    for r in rows:
        line = "|".join(str(r[k]) for k in ["match_api_id", "date", "home_team_api_id", "away_team_api_id", "selection_sha256", "block"])
        h.update((line + "\n").encode("utf-8"))
    return h.hexdigest()


def parse_day(date_text: str) -> str:
    # Database dates use ISO-ish timestamps; YYYY-MM-DD prefix is sufficient for whole-day grouping.
    return str(date_text)[:10]


def assign_blocks(rows):
    rows = sorted(rows, key=lambda r: (str(r["date"]), int(r["match_api_id"])))
    by_day = defaultdict(list)
    for r in rows:
        by_day[parse_day(r["date"])].append(r)
    days = sorted(by_day)

    targets = [("warmup", 1200), ("calibration", 1200), ("confirmation", 1600)]
    assigned = []
    idx = 0
    total = 0
    for block, target in targets[:-1]:
        start_total = total
        while idx < len(days) and total - start_total < target:
            day = days[idx]
            for r in by_day[day]:
                rr = dict(r)
                rr["block"] = block
                assigned.append(rr)
            total += len(by_day[day])
            idx += 1
    # everything remaining is confirmation
    while idx < len(days):
        day = days[idx]
        for r in by_day[day]:
            rr = dict(r)
            rr["block"] = "confirmation"
            assigned.append(rr)
        total += len(by_day[day])
        idx += 1

    counts = defaultdict(int)
    for r in assigned:
        counts[r["block"]] += 1
    if counts["calibration"] < 1000 or counts["confirmation"] < 1400:
        raise RuntimeError(f"STOP_DATA_COVERAGE split counts={dict(counts)}")
    return sorted(assigned, key=lambda r: (str(r["date"]), int(r["match_api_id"]))), dict(counts)


def main():
    db_path = Path(os.environ.get("C070F_DB", "database.sqlite"))
    out_dir = Path(os.environ.get("C070F_OUT", "c070f_fresh4000_out"))
    out_dir.mkdir(parents=True, exist_ok=True)

    actual_sha = sha256_file(db_path)
    if actual_sha != EXPECTED_DB_SHA256:
        raise RuntimeError(f"source SHA mismatch: {actual_sha} != {EXPECTED_DB_SHA256}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    schema = {r[1] for r in conn.execute("PRAGMA table_info('Match')")}
    required = set(IDENTITY_FIELDS + EVENT_FIELDS)
    missing = sorted(required - schema)
    if missing:
        raise RuntimeError(f"missing expected Match columns: {missing}")

    # No result/score columns are selected. Event fields are only reduced to NULL/non-NULL presence flags.
    presence_expr = " + ".join([f"CASE WHEN {f} IS NOT NULL AND length(trim({f})) > 0 THEN 1 ELSE 0 END" for f in EVENT_FIELDS])
    q = f"""
    SELECT {', '.join(IDENTITY_FIELDS)},
           CASE WHEN ({presence_expr}) > 0 THEN 1 ELSE 0 END AS has_any_event_payload
    FROM Match
    WHERE match_api_id IS NOT NULL
      AND date IS NOT NULL
      AND home_team_api_id IS NOT NULL
      AND away_team_api_id IS NOT NULL
    """
    rows = [dict(r) for r in conn.execute(q)]

    # Group-level payload-presence coverage only; never condition individual match selection on goal/result content.
    group = defaultdict(lambda: {"n": 0, "with_payload": 0})
    for r in rows:
        key = (r["league_id"], r["season"])
        group[key]["n"] += 1
        group[key]["with_payload"] += int(r["has_any_event_payload"])

    coverage_rows = []
    eligible_groups = set()
    for (league_id, season), v in sorted(group.items(), key=lambda kv: (str(kv[0][1]), int(kv[0][0]))):
        rate = v["with_payload"] / v["n"] if v["n"] else 0.0
        item = {"league_id": league_id, "season": season, "matches": v["n"], "with_any_event_payload": v["with_payload"], "payload_presence_rate": rate}
        coverage_rows.append(item)
        if v["n"] >= 50 and rate >= 0.95:
            eligible_groups.add((league_id, season))

    candidates = []
    for r in rows:
        if (r["league_id"], r["season"]) not in eligible_groups:
            continue
        rr = {k: r[k] for k in IDENTITY_FIELDS}
        rr["selection_sha256"] = selection_hash(rr)
        candidates.append(rr)

    if len(candidates) < N_SELECT:
        raise RuntimeError(f"STOP_DATA_COVERAGE eligible identity candidates={len(candidates)} < {N_SELECT}")

    selected = sorted(candidates, key=lambda r: (r["selection_sha256"], int(r["match_api_id"])))[:N_SELECT]
    if len({r["match_api_id"] for r in selected}) != N_SELECT:
        raise RuntimeError("duplicate match_api_id in frozen selection")

    assigned, block_counts = assign_blocks(selected)
    for r in assigned:
        r["source"] = SOURCE
        r["source_revision"] = REVISION

    cov_path = out_dir / "c070f_league_season_event_presence.csv"
    with cov_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["league_id", "season", "matches", "with_any_event_payload", "payload_presence_rate"])
        w.writeheader(); w.writerows(coverage_rows)

    manifest_path = out_dir / "c070f_4000_identity_manifest.csv"
    fields = ["source", "source_revision"] + IDENTITY_FIELDS + ["selection_sha256", "block"]
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(assigned)

    dates = [str(r["date"]) for r in assigned]
    summary = {
        "status": "PASS_ZERO_LABEL_FRESH4000_IDENTITY_FREEZE",
        "source": SOURCE,
        "revision": REVISION,
        "database_bytes": db_path.stat().st_size,
        "database_sha256": actual_sha,
        "expected_database_sha256": EXPECTED_DB_SHA256,
        "match_table_identity_rows": len(rows),
        "eligible_group_count": len(eligible_groups),
        "eligible_identity_candidates": len(candidates),
        "selected_matches": len(assigned),
        "selected_date_min": min(dates),
        "selected_date_max": max(dates),
        "block_counts": block_counts,
        "identity_manifest_sha256": manifest_digest(assigned),
        "target_score_columns_selected": [],
        "event_payload_contents_parsed": False,
        "model_fits": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "formal_weight": 0,
    }
    (out_dir / "c070f_fresh4000_identity_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    conn.close()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
