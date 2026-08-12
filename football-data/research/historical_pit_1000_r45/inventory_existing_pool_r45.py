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

REQUIRED = {
    "competition_id", "season", "date", "home_team", "away_team", "label_result"
}
LABEL_DENY = {
    "label_home_goals", "label_away_goals", "label_total_goals",
    "label_total_goals_bin", "label_goal_difference", "label_result",
}
META_DENY = {
    "competition_id", "season", "split", "stage", "date",
    "home_team", "away_team", "source_path",
}
EXPECTED_FEATURES = {
    "home_history_matches", "home_history_gf", "home_history_ga", "home_history_ppg",
    "home_venue_matches", "home_venue_gf", "home_venue_ga",
    "home_last5_matches", "home_last5_gf", "home_last5_ga", "home_last5_ppg",
    "away_history_matches", "away_history_gf", "away_history_ga", "away_history_ppg",
    "away_venue_matches", "away_venue_gf", "away_venue_ga",
    "away_last5_matches", "away_last5_gf", "away_last5_ga", "away_last5_ppg",
    "home_elo_pre_match", "away_elo_pre_match", "elo_difference_with_home_advantage",
    "cold_start_flag", "stage_unverified_flag",
}
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
        norm(row.get("competition_id", "")),
        (row.get("date") or "").strip(),
        norm(row.get("home_team", "")),
        norm(row.get("away_team", "")),
    ])


def identity_complete(row: dict[str, str]) -> bool:
    return all((row.get(c) or "").strip() for c in ("competition_id", "date", "home_team", "away_team"))


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
    unexpected_schema = []
    empty_identity = []

    for p in files:
        rows = 0
        dates: list[str] = []
        seasons: Counter[str] = Counter()
        missing: Counter[str] = Counter()
        ids: list[str] = []
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            fields = tuple(rd.fieldnames or [])
            fieldset = set(fields)
            all_headers[fields] += 1
            absent = sorted(REQUIRED - fieldset)
            if absent:
                missing_required.append({"path": str(p.relative_to(ROOT)), "missing": absent})
            missing_features = sorted(EXPECTED_FEATURES - fieldset)
            unexpected_features = sorted((fieldset - LABEL_DENY - META_DENY) - EXPECTED_FEATURES)
            if missing_features or unexpected_features:
                unexpected_schema.append({
                    "path": str(p.relative_to(ROOT)),
                    "missing_expected_features": missing_features,
                    "unexpected_candidate_features": unexpected_features,
                })
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
                for c in fields:
                    if row.get(c) in (None, ""):
                        missing[c] += 1
                if not identity_complete(row):
                    empty_identity.append({"path": str(p.relative_to(ROOT)), "row": row_no})
                identity = ident(row)
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
            "header_columns": list(fields),
            "feature_columns_candidate": [c for c in fields if c in EXPECTED_FEATURES],
            "label_columns_denied": [c for c in fields if c in LABEL_DENY],
            "metadata_columns_denied": [c for c in fields if c in META_DENY],
            "missing_cells": {k: v for k, v in sorted(missing.items()) if v},
            "identity_sha256": sha256_text(sorted(ids)),
            "unique_identities": len(set(ids)),
            "duplicate_rows_within_file": rows - len(set(ids)),
        })

    cross_dupes = {k: v for k, v in id_locations.items() if len(v) > 1}

    protected_paths = []
    for p in sorted((ROOT / "football-data").rglob("*")):
        if p.is_file() and PROTECTED_NAME_RE.search(str(p.relative_to(ROOT))):
            protected_paths.append(str(p.relative_to(ROOT)))

    fail = bool(missing_required or unexpected_schema or malformed_dates or empty_identity or cross_dupes)
    payload = {
        "schema_version": "r45-existing-pit-pool-inventory-v2",
        "mode": "READ_ONLY_EXISTING_REPOSITORY_DATA_NO_NEW_MATCH_DOWNLOAD",
        "formal_weight": 0,
        "status": "FAIL_R45_EXISTING_POOL_INVENTORY" if fail else "PASS_R45_EXISTING_POOL_INVENTORY",
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
        "required_columns": sorted(REQUIRED),
        "expected_feature_allowlist": sorted(EXPECTED_FEATURES),
        "label_denylist": sorted(LABEL_DENY),
        "metadata_denylist": sorted(META_DENY),
        "missing_required": missing_required,
        "unexpected_schema": unexpected_schema,
        "malformed_dates": malformed_dates[:100],
        "malformed_dates_count": len(malformed_dates),
        "empty_identity_count": len(empty_identity),
        "per_file": per_file,
        "protected_path_candidates": protected_paths,
        "hard_gates": {
            "full500_excluded": True,
            "gold1000_reserve500_excluded": True,
            "holdout_2526_excluded_from_new_experiment": True,
            "sample_selection_must_be_label_blind": True,
            "existing_split_column_forbidden_for_new_650_150_200_partition": True,
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
        "status": payload["status"],
        "files": len(files),
        "rows_total": all_rows,
        "unique_identities_total": len(set(all_ids)),
        "global_duplicate_rows": len(all_ids) - len(set(all_ids)),
        "cross_file_duplicate_identity_count": len(cross_dupes),
        "holdout_2526_name_match_rows": holdout_2526_rows,
        "header_variant_count": len(all_headers),
        "missing_required_count": len(missing_required),
        "unexpected_schema_count": len(unexpected_schema),
        "malformed_dates_count": len(malformed_dates),
        "empty_identity_count": len(empty_identity),
        "protected_path_candidates": len(protected_paths),
    }, indent=2))

    if fail:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
