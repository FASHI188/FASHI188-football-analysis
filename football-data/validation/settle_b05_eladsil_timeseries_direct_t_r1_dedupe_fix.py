#!/usr/bin/env python3
"""Engineering-only duplicate-row fix for the already-consumed B05 settlement.

No scientific contract changes. The source result table contains duplicate match_id rows.
For approved B05 ids only, exact duplicate result records are collapsed. Any duplicate
whose identity/result fields conflict fails closed. Non-target rows still expose only
their first raw CSV field (match_id); their outcome fields are not parsed.
"""
from __future__ import annotations

import csv
import zipfile

import settle_b05_eladsil_timeseries_direct_t_r1 as base


def read_target_only_labels_dedup(target_rows: list[dict], manifest: dict):
    if base.sha256(base.SOURCE_ZIP) != base.EXPECTED["source_zip_sha256"]:
        raise RuntimeError("SOURCE_ZIP_SHA_MISMATCH")

    target = {str(r["match_id"]) for r in target_rows}
    expected_meta = {
        str(m["match_id"]): (str(m["home"]), str(m["away"])) for m in manifest["matches"]
    }
    labels: dict[str, int] = {}
    canonical_target_rows: dict[str, tuple] = {}
    scanned_non_target = 0
    target_result_lines_parsed = 0
    identical_duplicates_collapsed = 0

    with zipfile.ZipFile(base.SOURCE_ZIP) as z:
        names = [n for n in z.namelist() if n.endswith("Matches_Results.csv")]
        if len(names) != 1:
            raise RuntimeError(f"RESULT_MEMBER_COUNT:{names}")
        with z.open(names[0], "r") as fh:
            header_raw = fh.readline()
            header = next(csv.reader([header_raw.decode("utf-8-sig").rstrip("\r\n")]))
            expected_header = [
                "match_id", "date_start", "competition_name", "home_team_name",
                "away_team_name", "home_team_score", "away_team_score", "final_result",
            ]
            if header != expected_header:
                raise RuntimeError(f"RESULT_HEADER_MISMATCH:{header}")

            for raw in fh:
                mid = base.raw_first_csv_field(raw)
                if mid not in target:
                    scanned_non_target += 1
                    continue

                # Only approved B05 result rows are fully parsed.
                row = next(csv.reader([raw.decode("utf-8").rstrip("\r\n")]))
                target_result_lines_parsed += 1
                if len(row) != len(expected_header) or row[0] != mid:
                    raise RuntimeError(f"TARGET_RESULT_PARSE_MISMATCH:{mid}")

                home, away = row[3], row[4]
                exp_home, exp_away = expected_meta[mid]
                if home != exp_home or away != exp_away:
                    raise RuntimeError(f"TARGET_IDENTITY_MISMATCH:{mid}:{home}:{away}")
                try:
                    gh = int(float(row[5]))
                    ga = int(float(row[6]))
                except Exception as exc:
                    raise RuntimeError(f"TARGET_SCORE_PARSE:{mid}:{row[5]}:{row[6]}") from exc
                if gh < 0 or ga < 0:
                    raise RuntimeError(f"TARGET_NEGATIVE_SCORE:{mid}")

                canonical = (row[1], row[2], row[3], row[4], gh, ga, row[7])
                if mid in canonical_target_rows:
                    if canonical_target_rows[mid] != canonical:
                        raise RuntimeError(
                            f"CONFLICTING_DUPLICATE_TARGET_RESULT:{mid}:"
                            f"{canonical_target_rows[mid]}!={canonical}"
                        )
                    identical_duplicates_collapsed += 1
                    continue

                canonical_target_rows[mid] = canonical
                labels[mid] = min(gh + ga, 4)

    if len(labels) != base.EXPECTED["rows"] or set(labels) != target:
        missing = sorted(target - set(labels))[:20]
        raise RuntimeError(f"TARGET_LABEL_COVERAGE:{len(labels)}:{missing}")

    audit = {
        "approved_target_rows_parsed": len(labels),
        "approved_target_result_lines_parsed": target_result_lines_parsed,
        "approved_target_score_pairs_dereferenced": target_result_lines_parsed,
        "identical_duplicate_target_rows_collapsed": identical_duplicates_collapsed,
        "conflicting_duplicate_target_rows": 0,
        "non_target_result_lines_id_scanned": scanned_non_target,
        "non_target_score_values_semantically_dereferenced": 0,
        "non_target_final_result_values_semantically_dereferenced": 0,
        "b06_plus_outcome_values_semantically_dereferenced": 0,
    }
    return labels, audit


if __name__ == "__main__":
    base.read_target_only_labels = read_target_only_labels_dedup
    base.main()
