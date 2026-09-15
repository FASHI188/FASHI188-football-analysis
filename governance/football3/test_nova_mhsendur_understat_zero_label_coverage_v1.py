#!/usr/bin/env python3
from __future__ import annotations

import copy
import csv
import io
import json
import unittest
import zipfile
from pathlib import Path

from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1
from nova_mhsendur_understat_zero_label_coverage_v1 import audit

HERE = Path(__file__).resolve().parent
LOCK = json.loads(
    (HERE / "nova_mhsendur_understat_header_lock_v1.json").read_text(encoding="utf-8")
)
HEADER = [
    "h_a", "xG", "xGA", "npxG", "npxGA", "ppda", "ppda_allowed",
    "deep", "deep_allowed", "scored", "missed", "xpts", "result",
    "date", "wins", "draws", "loses", "pts", "npxGD",
]


def csv_bytes(rows):
    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        base = {key: "0" for key in HEADER}
        base.update(row)
        writer.writerow(base)
    return sio.getvalue().encode("utf-8")


def build_archive(
    result_a="w",
    result_b="l",
    duplicate=False,
    break_reciprocal=False,
    mapping_ppda=False,
):
    if mapping_ppda:
        home_ppda = "{'att': 21, 'def': 2}"
        home_allowed = "{'att': 36, 'def': 5}"
        away_ppda = "{'att': 36, 'def': 5}"
        away_allowed = "{'att': 21, 'def': 2}"
    else:
        home_ppda, home_allowed = "10.5", "7.2"
        away_ppda, away_allowed = "7.2", "10.5"

    home = {
        "h_a": "h",
        "ppda": home_ppda,
        "ppda_allowed": home_allowed,
        "deep": "8",
        "deep_allowed": "4",
        "date": "2022-01-01",
        "result": result_a,
        "scored": "3",
        "missed": "1",
        "xG": "9.9",
    }
    away = {
        "h_a": "a",
        "ppda": away_ppda,
        "ppda_allowed": away_allowed,
        "deep": "4",
        "deep_allowed": "8",
        "date": "2022-01-01",
        "result": result_b,
        "scored": "1",
        "missed": "3",
        "xG": "0.1",
    }
    if break_reciprocal:
        away["deep_allowed"] = "9"

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        alpha = csv_bytes([home])
        beta = csv_bytes([away])
        zf.writestr("football_data_csv/EPL_2022_Alpha.csv", alpha)
        zf.writestr("football_data_csv/EPL_2022_Beta.csv", beta)
        if duplicate:
            zf.writestr("football_data_csv/EPL_2022_Alpha 2.csv", alpha)
    return bio.getvalue()


class CoverageTests(unittest.TestCase):
    def run_audit(self, raw):
        lock = copy.deepcopy(LOCK)
        lock["source"]["archive_blob_sha1"] = git_blob_sha1(raw)
        return audit(lock, raw)

    def test_reciprocal_pair_qualifies_without_labels(self):
        receipt = self.run_audit(build_archive())
        self.assertEqual(receipt["status"], "ZERO_LABEL_COVERAGE_QUALIFIED")
        self.assertEqual(receipt["paired_match_count"], 1)
        self.assertEqual(receipt["safe_perspective_row_count"], 2)
        self.assertEqual(receipt["result_values_used"], 0)
        self.assertEqual(receipt["score_values_used"], 0)
        self.assertEqual(receipt["xg_values_used"], 0)
        self.assertEqual(receipt["forbidden_columns_used_for_identity_or_coverage"], [])

    def test_understat_ppda_mapping_is_normalized_without_labels(self):
        receipt = self.run_audit(build_archive(mapping_ppda=True))
        self.assertEqual(receipt["status"], "ZERO_LABEL_COVERAGE_QUALIFIED")
        self.assertEqual(receipt["paired_match_count"], 1)
        self.assertEqual(receipt["ppda_object_values_parsed"], 4)
        self.assertEqual(
            receipt["ppda_normalization"],
            "att_div_def_when_mapping_else_numeric",
        )
        self.assertEqual(receipt["result_values_used"], 0)
        self.assertEqual(receipt["xg_values_used"], 0)

    def test_result_and_xg_mutation_do_not_change_projection(self):
        first = self.run_audit(build_archive("w", "l", mapping_ppda=True))
        second = self.run_audit(build_archive("l", "w", mapping_ppda=True))
        self.assertEqual(
            first["feature_projection_sha256"],
            second["feature_projection_sha256"],
        )
        self.assertEqual(first["paired_match_count"], second["paired_match_count"])

    def test_exact_copy_member_is_collapsed(self):
        receipt = self.run_audit(build_archive(duplicate=True, mapping_ppda=True))
        self.assertEqual(receipt["status"], "ZERO_LABEL_COVERAGE_QUALIFIED")
        self.assertEqual(receipt["exact_duplicate_member_count"], 1)
        self.assertEqual(receipt["safe_perspective_row_count"], 2)
        self.assertEqual(receipt["paired_match_count"], 1)

    def test_non_reciprocal_rows_are_partial(self):
        receipt = self.run_audit(build_archive(break_reciprocal=True, mapping_ppda=True))
        self.assertEqual(receipt["status"], "ZERO_LABEL_COVERAGE_PARTIAL")
        self.assertEqual(receipt["paired_match_count"], 0)
        self.assertEqual(receipt["unpaired_row_count"], 2)


if __name__ == "__main__":
    unittest.main()
