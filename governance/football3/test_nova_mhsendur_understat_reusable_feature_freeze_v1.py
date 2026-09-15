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
from nova_mhsendur_understat_reusable_feature_freeze_v1 import materialize

HERE = Path(__file__).resolve().parent
SOURCE_LOCK = json.loads(
    (HERE / "nova_mhsendur_understat_header_lock_v1.json").read_text(encoding="utf-8")
)
FREEZE = json.loads(
    (HERE / "nova_mhsendur_understat_reusable_feature_freeze_v1.json").read_text(encoding="utf-8")
)
HEADER = [
    "h_a", "xG", "xGA", "npxG", "npxGA", "ppda", "ppda_allowed",
    "deep", "deep_allowed", "scored", "missed", "xpts", "result",
    "date", "wins", "draws", "loses", "pts", "npxGD",
]


def csv_bytes(rows):
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        base = {field: "0" for field in HEADER}
        base.update(row)
        writer.writerow(base)
    return text.getvalue().encode("utf-8")


def archive_bytes(result_home="w", result_away="l", duplicate=False):
    home = {
        "h_a": "h",
        "ppda": "{'att': 21, 'def': 2}",
        "ppda_allowed": "{'att': 36, 'def': 5}",
        "deep": "8",
        "deep_allowed": "4",
        "date": "2022-01-01",
        "result": result_home,
        "scored": "3",
        "missed": "1",
        "xG": "9.9",
    }
    away = {
        "h_a": "a",
        "ppda": "{'att': 36, 'def': 5}",
        "ppda_allowed": "{'att': 21, 'def': 2}",
        "deep": "4",
        "deep_allowed": "8",
        "date": "2022-01-01",
        "result": result_away,
        "scored": "1",
        "missed": "3",
        "xG": "0.1",
    }
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        alpha = csv_bytes([home])
        zf.writestr("football_data_csv/EPL_2022_Alpha.csv", alpha)
        zf.writestr("football_data_csv/EPL_2022_Beta.csv", csv_bytes([away]))
        if duplicate:
            zf.writestr("football_data_csv/EPL_2022_Alpha 2.csv", alpha)
        # Explicitly retain an excluded partial snapshot to prove fail-closed cohort filtering.
        zf.writestr(
            "football_data_csv/EPL_2023_Alpha.csv",
            csv_bytes([{**home, "date": "2023-08-01"}]),
        )
    return bio.getvalue()


def locks_for(raw):
    source_lock = copy.deepcopy(SOURCE_LOCK)
    source_lock["source"]["archive_blob_sha1"] = git_blob_sha1(raw)
    freeze = copy.deepcopy(FREEZE)
    freeze["source"]["archive_blob_sha1"] = source_lock["source"]["archive_blob_sha1"]
    freeze["qualified_feature_cohorts"]["competitions"] = ["EPL"]
    freeze["qualified_feature_cohorts"]["season_start_min"] = 2022
    freeze["qualified_feature_cohorts"]["season_start_max"] = 2022
    freeze["qualified_feature_cohorts"]["expected_match_counts"] = {"EPL": 1}
    freeze["qualified_feature_cohorts"]["expected_total_match_count"] = 1
    return source_lock, freeze


class ReusableFeatureFreezeTests(unittest.TestCase):
    def run_materialize(self, raw):
        source_lock, freeze = locks_for(raw)
        return materialize(source_lock, freeze, raw)

    def test_feature_projection_is_frozen_and_label_free(self):
        projection, receipt = self.run_materialize(archive_bytes())
        self.assertEqual(len(projection), 1)
        self.assertEqual(receipt["qualified_match_count"], 1)
        self.assertEqual(receipt["qualified_match_counts"], {"EPL": 1})
        self.assertEqual(receipt["result_values_used"], 0)
        self.assertEqual(receipt["score_values_used"], 0)
        self.assertEqual(receipt["xg_values_used"], 0)
        self.assertFalse(receipt["canonical_match_id_binding_complete"])
        self.assertFalse(receipt["candidate_confirmation_allowed"])
        self.assertTrue(receipt["reported_benchmark_predictions_must_be_oof"])

    def test_result_mutation_does_not_change_feature_projection_sha(self):
        _, first = self.run_materialize(archive_bytes("w", "l"))
        _, second = self.run_materialize(archive_bytes("l", "w"))
        self.assertEqual(first["feature_projection_sha256"], second["feature_projection_sha256"])

    def test_exact_duplicate_member_is_collapsed(self):
        projection, receipt = self.run_materialize(archive_bytes(duplicate=True))
        self.assertEqual(len(projection), 1)
        self.assertEqual(receipt["exact_duplicate_member_count_collapsed"], 1)

    def test_excluded_2023_snapshot_never_enters_projection(self):
        projection, _ = self.run_materialize(archive_bytes())
        self.assertEqual({row["season_start"] for row in projection}, {"2022"})


if __name__ == "__main__":
    unittest.main()
