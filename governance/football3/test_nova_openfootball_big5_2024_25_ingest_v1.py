#!/usr/bin/env python3
from __future__ import annotations
import copy
import hashlib
import json
import unittest
from pathlib import Path
from nova_openfootball_big5_2024_25_ingest_v1 import IngestError, git_blob_sha1, ingest, validate_lock

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "nova_openfootball_big5_2024_25_source_lock_v1.json"

def payload(name, n):
    matches = []
    for i in range(n):
        day = (i % 27) + 1
        month = 8 + (i // 270)
        if month > 12:
            month = 12
        matches.append({
            "round": f"Matchday {i // 10 + 1}",
            "date": f"2024-{month:02d}-{day:02d}",
            "time": "15:00",
            "team1": f"{name} Home {i}",
            "team2": f"{name} Away {i}",
            "score": {"ft": [i % 4, (i + 1) % 3]},
        })
    return (json.dumps({"name": name, "matches": matches}, separators=(",", ":")) + "\n").encode()

class OpenFootballIngestTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    def test_lock_contract(self):
        validate_lock(self.lock)
        self.assertEqual(self.lock["expected_total_matches"], 1752)
        self.assertFalse(self.lock["governance"]["fresh_confirmation_eligible_by_default"])

    def build_payloads(self, lock):
        payloads = {}
        for c in lock["competitions"]:
            raw = payload(c["competition_id"], c["expected_matches"])
            c["source_blob_sha1"] = git_blob_sha1(raw)
            c["raw_sha256"] = hashlib.sha256(raw).hexdigest()
            payloads[c["path"]] = raw
        return payloads

    def test_synthetic_ingest_and_exposure_guard(self):
        lock = copy.deepcopy(self.lock)
        payloads = self.build_payloads(lock)
        _, receipt = ingest(lock, payloads, False)
        lock["normalized_set_sha256"] = receipt["normalized_set_sha256"]
        rows2, receipt2 = ingest(lock, payloads, True)
        self.assertEqual(len(rows2), 1752)
        self.assertEqual(receipt2["result_value_present_count"], 1752)
        self.assertEqual(receipt2["result_value_missing_count"], 0)
        self.assertFalse(receipt2["missing_result_values_fabricated"])
        self.assertEqual(receipt2["fresh_confirmation_eligible_count"], 0)
        self.assertTrue(all(r["research_label_exposed_before_assignment"] for r in rows2))
        self.assertTrue(all(not r["fresh_candidate_confirmation_eligible"] for r in rows2))

    def test_missing_result_value_preserves_identity_without_fabrication(self):
        lock = copy.deepcopy(self.lock)
        payloads = self.build_payloads(lock)
        first = lock["competitions"][0]
        obj = json.loads(payloads[first["path"]].decode("utf-8"))
        obj["matches"][0]["score"] = {}
        raw = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        first["source_blob_sha1"] = git_blob_sha1(raw)
        first["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        payloads[first["path"]] = raw
        rows, receipt = ingest(lock, payloads, False)
        missing = [r for r in rows if not r["result_value_present"]]
        self.assertEqual(len(rows), 1752)
        self.assertEqual(receipt["result_value_present_count"], 1751)
        self.assertEqual(receipt["result_value_missing_count"], 1)
        self.assertFalse(receipt["missing_result_values_fabricated"])
        self.assertEqual(len(missing), 1)
        self.assertIsNone(missing[0]["home_goals"])
        self.assertIsNone(missing[0]["away_goals"])
        self.assertIsNone(missing[0]["result_1x2"])
        self.assertTrue(missing[0]["research_label_exposed_before_assignment"])
        self.assertFalse(missing[0]["fresh_candidate_confirmation_eligible"])

    def test_malformed_present_score_still_fails(self):
        lock = copy.deepcopy(self.lock)
        payloads = self.build_payloads(lock)
        first = lock["competitions"][0]
        obj = json.loads(payloads[first["path"]].decode("utf-8"))
        obj["matches"][0]["score"] = {"ft": [1]}
        raw = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        first["source_blob_sha1"] = git_blob_sha1(raw)
        payloads[first["path"]] = raw
        with self.assertRaises(IngestError):
            ingest(lock, payloads, False)

    def test_blob_drift_fails(self):
        lock = copy.deepcopy(self.lock)
        payloads = {}
        for c in lock["competitions"]:
            raw = payload(c["competition_id"], c["expected_matches"])
            c["source_blob_sha1"] = git_blob_sha1(raw)
            payloads[c["path"]] = raw
        first = lock["competitions"][0]["path"]
        payloads[first] += b" "
        with self.assertRaises(IngestError):
            ingest(lock, payloads, False)

    def test_count_drift_fails(self):
        lock = copy.deepcopy(self.lock)
        payloads = {}
        for c in lock["competitions"]:
            n = c["expected_matches"] - (1 if c is lock["competitions"][0] else 0)
            raw = payload(c["competition_id"], n)
            c["source_blob_sha1"] = git_blob_sha1(raw)
            payloads[c["path"]] = raw
        with self.assertRaises(IngestError):
            ingest(lock, payloads, False)

if __name__ == "__main__":
    unittest.main()
