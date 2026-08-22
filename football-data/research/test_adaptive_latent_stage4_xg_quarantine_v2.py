#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adaptive_latent_stage4_xg_quarantine_v2 as q


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def target(event_id: int = 101, kickoff: datetime | None = None) -> dict:
    kickoff = kickoff or datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    return {
        "competition_id": "ENG_PremierLeague",
        "fixture_id": f"fixture-projection:{event_id}",
        "provider_event_id": event_id,
        "kickoff_at": z(kickoff),
        "home_team_id": "fixture-projection:1",
        "away_team_id": "fixture-projection:2",
        "prediction_cutoff": z(kickoff - timedelta(minutes=15)),
    }


def identity_hash(t: dict) -> str:
    return q._identity_lock_hash(q.canonical_target(t))


def projection(t: dict | None = None, *, event_id: int | None = None, when: datetime | None = None) -> dict:
    t = t or target(event_id or 101)
    event_id = event_id or t["provider_event_id"]
    when = when or datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
    return {
        "schema": q.PROJECTION_SCHEMA,
        "source_kind": q.PROJECTION_SOURCE_KIND,
        "source_identity": f"statistics-projection:{event_id}",
        "source_url": f"https://example.invalid/statistics/{event_id}",
        "payload_sha256": "a" * 64,
        "collector_run_id": "synthetic-run-1",
        "provider_event_id": event_id,
        "target_identity_sha256": identity_hash(t),
        "home_xg": 1.25,
        "away_xg": 0.75,
        "collector_first_observed_at": z(when),
        "retrieved_at": z(when + timedelta(seconds=1)),
        "ingested_at": z(when + timedelta(seconds=2)),
        "raw_provider_payload_persisted": False,
        "real_labels_read": 0,
        "completion_status": q.UNKNOWN_COMPLETION,
        "final_xg_status": q.UNKNOWN_FINAL_XG,
        "formal_pit_eligible": False,
        "eligible_for_latent_update": False,
        "formal_weight": 0.0,
        "research_only": True,
    }


class QuarantineV2Tests(unittest.TestCase):
    def test_behavior_happy_path_stays_quarantined(self):
        t = target()
        row = q.materialize_quarantine_snapshot(t, projection(t))
        self.assertEqual(row["source_kind"], q.SNAPSHOT_SOURCE_KIND)
        self.assertEqual(row["completion_status"], q.UNKNOWN_COMPLETION)
        self.assertEqual(row["final_xg_status"], q.UNKNOWN_FINAL_XG)
        self.assertFalse(row["formal_pit_eligible"])
        self.assertFalse(row["eligible_for_latent_update"])
        self.assertFalse(row["latent_adapter_compatible"])
        self.assertFalse(row["time_based_completion_inference_used"])
        self.assertEqual(row["real_labels_read"], 0)
        self.assertNotIn("event_completed_at", row)

    def test_contract_target_exact_keys_default_deny_unknown(self):
        t = target()
        t["telemetry"] = "benign-looking"
        with self.assertRaisesRegex(q.QuarantineError, "exact-key contract failed"):
            q.materialize_quarantine_snapshot(t, projection(target()))

    def test_contract_target_exact_keys_default_deny_missing(self):
        t = target()
        del t["away_team_id"]
        with self.assertRaisesRegex(q.QuarantineError, "exact-key contract failed"):
            q.canonical_target(t)

    def test_contract_projection_exact_keys_default_deny_unknown(self):
        t = target()
        p = projection(t)
        p["phase"] = "complete"
        with self.assertRaisesRegex(q.QuarantineError, "exact-key contract failed"):
            q.materialize_quarantine_snapshot(t, p)

    def test_contract_projection_exact_keys_default_deny_missing(self):
        t = target()
        p = projection(t)
        del p["collector_run_id"]
        with self.assertRaisesRegex(q.QuarantineError, "exact-key contract failed"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_completion_cannot_be_claimed(self):
        t = target()
        p = projection(t)
        p["completion_status"] = "FINISHED"
        with self.assertRaisesRegex(q.QuarantineError, "completion_status"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_final_xg_cannot_be_claimed(self):
        t = target()
        p = projection(t)
        p["final_xg_status"] = "FINAL"
        with self.assertRaisesRegex(q.QuarantineError, "final_xg_status"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_pit_and_latent_eligibility_cannot_be_enabled(self):
        for field in ("formal_pit_eligible", "eligible_for_latent_update"):
            t = target()
            p = projection(t)
            p[field] = True
            with self.subTest(field=field):
                with self.assertRaises(q.QuarantineError):
                    q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_real_labels_read_bool_is_not_zero(self):
        t = target()
        p = projection(t)
        p["real_labels_read"] = False
        with self.assertRaisesRegex(q.QuarantineError, "integer zero"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_formal_weight_bool_is_not_zero(self):
        t = target()
        p = projection(t)
        p["formal_weight"] = False
        with self.assertRaisesRegex(q.QuarantineError, "numeric zero"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_identity_hash_mismatch(self):
        t = target()
        p = projection(t)
        p["target_identity_sha256"] = "b" * 64
        with self.assertRaisesRegex(q.QuarantineError, "target_identity_sha256"):
            q.materialize_quarantine_snapshot(t, p)

    def test_fail_closed_provider_event_mismatch(self):
        t = target(101)
        p = projection(t)
        p["provider_event_id"] = 202
        with self.assertRaisesRegex(q.QuarantineError, "provider_event_id"):
            q.materialize_quarantine_snapshot(t, p)

    def test_contract_fixture_event_binding(self):
        t = target(101)
        t["fixture_id"] = "fixture-projection:202"
        with self.assertRaisesRegex(q.QuarantineError, "fixture_id/provider_event_id"):
            q.canonical_target(t)

    def test_contract_cutoff_exact(self):
        t = target()
        t["prediction_cutoff"] = "2026-08-20T17:44:00Z"
        with self.assertRaisesRegex(q.QuarantineError, "15 minutes"):
            q.canonical_target(t)

    def test_contract_timestamp_requires_canonical_utc_z(self):
        t = target()
        p = projection(t)
        p["retrieved_at"] = "2026-08-20T22:00:01+00:00"
        with self.assertRaisesRegex(q.QuarantineError, "canonical UTC Z"):
            q.materialize_quarantine_snapshot(t, p)

    def test_contract_timestamp_ordering(self):
        t = target()
        p = projection(t)
        p["retrieved_at"] = "2026-08-20T21:59:59Z"
        with self.assertRaisesRegex(q.QuarantineError, "first_observed_at must be <= retrieved_at"):
            q.materialize_quarantine_snapshot(t, p)

    def test_collection_floor_does_not_become_completion_proof(self):
        t = target()
        p_early = projection(t, when=datetime(2026, 8, 20, 20, 59, tzinfo=timezone.utc))
        with self.assertRaisesRegex(q.QuarantineError, "collection floor"):
            q.materialize_quarantine_snapshot(t, p_early)
        p_late = projection(t, when=datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc))
        row = q.materialize_quarantine_snapshot(t, p_late)
        self.assertEqual(row["completion_status"], q.UNKNOWN_COMPLETION)
        self.assertNotIn("event_completed_at", row)
        self.assertFalse(row["formal_pit_eligible"])

    def test_contract_source_url_has_no_credentials_query_or_fragment(self):
        bad = (
            "http://example.invalid/x",
            "https://user:pass@example.invalid/x",
            "https://example.invalid/x?score=1",
            "https://example.invalid/x#fragment",
        )
        for url in bad:
            t = target()
            p = projection(t)
            p["source_url"] = url
            with self.subTest(url=url):
                with self.assertRaisesRegex(q.QuarantineError, "source_url"):
                    q.materialize_quarantine_snapshot(t, p)

    def test_contract_xg_must_be_finite_nonnegative_number_not_bool(self):
        bad = (-0.1, float("nan"), float("inf"), True, "1.0")
        for value in bad:
            t = target()
            p = projection(t)
            p["home_xg"] = value
            with self.subTest(value=value):
                with self.assertRaisesRegex(q.QuarantineError, "home_xg"):
                    q.materialize_quarantine_snapshot(t, p)

    def test_batch_exact_pair_shape_and_duplicate_guards(self):
        t1 = target(101)
        t2 = target(102)
        p1 = projection(t1)
        p2 = projection(t2)
        out = q.materialize_quarantine_batch([
            {"target": t1, "projection": p1},
            {"target": t2, "projection": p2},
        ])
        self.assertEqual(out["row_count"], 2)
        self.assertEqual(out["completed_match_xg_row_count"], 0)
        self.assertEqual(out["formal_pit_eligible_row_count"], 0)
        self.assertEqual(out["eligible_for_latent_update_row_count"], 0)
        bad_pair = {"target": t1, "projection": p1, "note": "extra"}
        with self.assertRaisesRegex(q.QuarantineError, "exact-key contract failed"):
            q.materialize_quarantine_batch([bad_pair])
        with self.assertRaisesRegex(q.QuarantineError, "duplicate frozen fixture identity"):
            q.materialize_quarantine_batch([
                {"target": t1, "projection": p1},
                {"target": t1, "projection": p1},
            ])

    def test_output_cannot_satisfy_completed_match_adapter_contract(self):
        t = target()
        row = q.materialize_quarantine_snapshot(t, projection(t))
        required = {"event_completed_at", "source_published_at", "source_published_at_trusted"}
        self.assertTrue(required.isdisjoint(row))
        self.assertNotEqual(row["source_kind"], "completed_match_xg")
        self.assertFalse(row["latent_adapter_compatible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
