#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / "football-data" / "training_datasets"
OUT_ROOT = ROOT / "football-data" / "research" / "historical_pit_1000_r45" / "out"

REQUIRED = {"competition", "season", "date", "home_team", "away_team", "FTR"}
LABEL_DENY = {
    "FTHG", "FTAG", "total_goals", "total_goals_bucket", "goal_diff_ft", "FTR"
}
META_DENY = {"competition", "season", "split", "stage", "date", "home_team", "away_team", "source_file"}
PROTECTED_NAME_RE = re.compile(r"(gold|reserve|fixed|blind|holdout|sealed|full500|full_500)", re.I)


def sha256_text(lines: list[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def ident(row: dict[str, str]) -> str:
    return "|".join([
        norm(row.get("competition", "")),
        (row.get("date") or "").strip(),
        norm(row.get("home_team", "")),
        norm(row.get("away_team", "")),
    ])


def is_holdout_2526(season: str) -> bool:
    s = re.sub(r"[^0-9]", "", str(season or ""))
    return s in {"2526", "202526", "20252026"}


def main() -> None:
    files = sorted(DATA_ROOT.glob("*/point_in_time.csv"))
    if len(files) != 17:
        raise SystemExit(f"expected 17 point_in_time.csv files, got {len(files)}")

    per_file = []
    all_ids: list[str] = []
    id_locations: dict[str, list[str]] = defaultdict(list)
    all_headers: Counter[tuple[str, ...]] = Counter()
    all_rows = 0
    holdout_2526_rows = 0
    malformed_dates = []
    missing_required = []
    empty_identity = []

    for p in files:
        rows = 0
        dates: list[str] = []
        seasons: Counter[str] = Counter()
        outcomes: Counter[str] = Counter()
        missing: Counter[str] = Counter()
        ids: list[str] = []
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            fields = tuple(rd.fieldnames or [])
            all_headers[fields] += 1
            absent = sorted(REQUIRED - set(fields))
            if absent:
                missing_required.append({"path": str(p.relative_to(ROOT)), "missing": absent})
            for row_no, row in enumerate(rd, start=2):
                rows += 1
                d = (row.get("date") or "").strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                    malformed_dates.append({"path": str(p.relative_to(ROOT)), "row": row_no, "date": d})
                else:
                    dates.append(d)
                season = (row.get("season") or "").strip()
                seasons[season] += 1
                if is_holdout_2526(season):
                    holdout_2526_rows += 1
                outcome = (row.get("FTR") or "").strip()
                outcomes[outcome] += 1
                for c in fields:
                    if row.get(c) in (None, ""):
                        missing[c] += 1
                identity = ident(row)
                if identity.startswith("||") or "||" in identity:
                    empty_identity.append({"path": str(p.relative_to(ROOT)), "row": row_no, "identity": identity})
                ids.append(identity)
                all_ids.append(identity)
                id_locations[identity].append(str(p.relative_to(ROOT)))

        all_rows += rows
        per_file.append({
            "competition_dir": p.parent.name,
            "path": str(p.relative_to(ROOT)),
            "blob_bytes": p.stat().st_size,
            "rows": rows,
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
            "seasons": dict(sorted(seasons.items())),
            "outcomes": dict(sorted(outcomes.items())),
            "header_columns": list(fields),
            "feature_columns_candidate": [c for c in fields if c not in LABEL_DENY | META_DENY],
            "label_columns_denied": [c for c in fields if c in LABEL_DENY],
            "missing_cells": {k: v for k, v in sorted(missing.items()) if v},
            "identity_sha256": sha256_text(sorted(ids)),
            "unique_identities": len(set(ids)),
            "duplicate_rows_within_file": rows - len(set(ids)),
        })

    cross_dupes = {
        k: v for k, v in id_locations.items()
        if len(v) > 1
    }

    protected_paths = []
    for p in sorted((ROOT / "football-data").rglob("*")):
        if p.is_file() and PROTECTED_NAME_RE.search(str(p.relative_to(ROOT))):
            protected_paths.append(str(p.relative_to(ROOT)))

    payload = {
        "schema_version": "r45-existing-pit-pool-inventory-v1",
        "mode": "READ_ONLY_EXISTING_REPOSITORY_DATA_NO_NEW_MATCH_DOWNLOAD",
        "formal_weight": 0,
        "files": len(files),
        "rows_total": all_rows,
        "unique_identities_total": len(set(all_ids)),
        "duplicate_rows_global": len(all_ids) - len(set(all_ids)),
        "cross_file_duplicate_identity_count": len(cross_dupes),
        "holdout_2526_name_match_rows": holdout_2526_rows,
        "header_variant_count": len(all_headers),
        "header_variant_file_counts": [
            {"file_count": n, "columns": list(h)} for h, n in all_headers.items()
        ],
        "missing_required": missing_required,
        "malformed_dates": malformed_dates[:100],
        "malformed_dates_count": len(malformed_dates),
        "empty_identity_count": len(empty_identity),
        "per_file": per_file,
        "protected_path_candidates": protected_paths,
        "hard_gates": {
            "full500_excluded": True,
            "gold1000_reserve500_excluded": True,
            "holdout_2526_excluded_from_new_experiment": True,
            "no_training_performed": True,
            "no_label_driven_selection": True,
            "no_formal_model_mutation": True,
            "no_current_mutation": True,
        },
    }

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "existing_pool_inventory_r45.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (OUT_ROOT / "existing_pool_inventory_r45.csv").open("w", encoding="utf-8", newline="") as f:
        cols = ["competition_dir", "rows", "date_min", "date_max", "unique_identities", "duplicate_rows_within_file", "identity_sha256"]
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for r in per_file:
            wr.writerow({k: r[k] for k in cols})

    print(json.dumps({
        "status": "PASS_R45_EXISTING_POOL_INVENTORY" if not missing_required and not malformed_dates and not empty_identity else "FAIL_R45_EXISTING_POOL_INVENTORY",
        "files": len(files),
        "rows_total": all_rows,
        "unique_identities_total": len(set(all_ids)),
        "global_duplicate_rows": len(all_ids) - len(set(all_ids)),
        "cross_file_duplicate_identity_count": len(cross_dupes),
        "holdout_2526_name_match_rows": holdout_2526_rows,
        "header_variant_count": len(all_headers),
        "protected_path_candidates": len(protected_paths),
    }, indent=2))

    if missing_required or malformed_dates or empty_identity:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
