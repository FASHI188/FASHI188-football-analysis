#!/usr/bin/env python3
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path

from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1
from nova_mhsendur_understat_zero_label_coverage_v1 import audit

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "nova_mhsendur_understat_header_lock_v1.json").read_text(encoding="utf-8"))
HEADER = ["h_a","xG","xGA","npxG","npxGA","ppda","ppda_allowed","deep","deep_allowed","scored","missed","xpts","result","date","wins","draws","loses","pts","npxGD"]


def csv_bytes(rows):
    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    for row in rows:
        base = {k:"0" for k in HEADER}
        base.update(row)
        w.writerow(base)
    return sio.getvalue().encode("utf-8")


def build_archive(result_a="w", result_b="l", duplicate=False, break_reciprocal=False):
    home = {"h_a":"h","ppda":"10.5","ppda_allowed":"7.2","deep":"8","deep_allowed":"4","date":"2022-01-01","result":result_a,"scored":"3","missed":"1","xG":"9.9"}
    away = {"h_a":"a","ppda":"7.2","ppda_allowed":"10.5","deep":"4","deep_allowed":"8","date":"2022-01-01","result":result_b,"scored":"1","missed":"3","xG":"0.1"}
    if break_reciprocal:
        away["deep_allowed"] = "9"
    bio = io.BytesIO()
    with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as zf:
        a = csv_bytes([home]); b = csv_bytes([away])
        zf.writestr("football_data_csv/EPL_2022_Alpha.csv",a)
        zf.writestr("football_data_csv/EPL_2022_Beta.csv",b)
        if duplicate:
            zf.writestr("football_data_csv/EPL_2022_Alpha 2.csv",a)
    return bio.getvalue()


class CoverageTests(unittest.TestCase):
    def run_audit(self, raw):
        lock = copy.deepcopy(LOCK)
        lock["source"]["archive_blob_sha1"] = git_blob_sha1(raw)
        return audit(lock, raw)

    def test_reciprocal_pair_qualifies_without_labels(self):
        r = self.run_audit(build_archive())
        self.assertEqual(r["status"], "ZERO_LABEL_COVERAGE_QUALIFIED")
        self.assertEqual(r["paired_match_count"], 1)
        self.assertEqual(r["safe_perspective_row_count"], 2)
        self.assertEqual(r["result_values_used"], 0)
        self.assertEqual(r["score_values_used"], 0)
        self.assertEqual(r["xg_values_used"], 0)
        self.assertEqual(r["forbidden_columns_used_for_identity_or_coverage"], [])

    def test_result_and_xg_mutation_do_not_change_projection(self):
        a = self.run_audit(build_archive("w","l"))
        b = self.run_audit(build_archive("l","w"))
        self.assertEqual(a["feature_projection_sha256"], b["feature_projection_sha256"])
        self.assertEqual(a["paired_match_count"], b["paired_match_count"])

    def test_exact_copy_member_is_collapsed(self):
        r = self.run_audit(build_archive(duplicate=True))
        self.assertEqual(r["status"], "ZERO_LABEL_COVERAGE_QUALIFIED")
        self.assertEqual(r["exact_duplicate_member_count"], 1)
        self.assertEqual(r["safe_perspective_row_count"], 2)
        self.assertEqual(r["paired_match_count"], 1)

    def test_non_reciprocal_rows_are_partial(self):
        r = self.run_audit(build_archive(break_reciprocal=True))
        self.assertEqual(r["status"], "ZERO_LABEL_COVERAGE_PARTIAL")
        self.assertEqual(r["paired_match_count"], 0)
        self.assertEqual(r["unpaired_row_count"], 2)


if __name__ == "__main__":
    unittest.main()
