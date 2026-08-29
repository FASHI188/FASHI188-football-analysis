from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore


UTC = timezone.utc


def rec(**overrides):
    base = {
        "feature_family": "lineup_pstart",
        "entity_type": "team",
        "canonical_entity_id": "team:a",
        "fixture_id": "fx1",
        "value": {"p": 0.7},
        "source_name": "fixture_history",
        "source_record_id": "r1",
        "source_hash": "sha256:abc",
        "observed_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "known_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "effective_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "expires_at": None,
        "leakage_class": "prematch_history",
        "historical_use_allowed": True,
        "adapter_version": "test-v1",
    }
    base.update(overrides)
    return PITFeatureRecord(**base)


class PITFeatureStoreTests(unittest.TestCase):
    def test_known_after_as_of_is_rejected(self):
        store = PointInTimeFeatureStore([
            rec(source_record_id="early", value=1),
            rec(
                source_record_id="late",
                value=2,
                observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                known_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                effective_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
        ])
        out = store.read("lineup_pstart", "fx1", datetime(2026, 1, 1, 10, 0, tzinfo=UTC), "team:a")
        self.assertEqual(out.status, "active")
        self.assertEqual([r.value for r in out.records], [1])
        self.assertEqual(out.rejected_counts.get("known_after_as_of"), 1)

    def test_latest_never_substitutes_future_snapshot(self):
        store = PointInTimeFeatureStore([
            rec(source_record_id="r1", value="old"),
            rec(
                source_record_id="r2",
                value="new",
                observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                known_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                effective_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
        ])
        before = store.read("lineup_pstart", "fx1", "2026-01-01T11:00:00+00:00", "team:a")
        after = store.read("lineup_pstart", "fx1", "2026-01-01T13:00:00+00:00", "team:a")
        self.assertEqual(before.latest().value, "old")
        self.assertEqual(after.latest().value, "new")

    def test_retrospective_label_is_blocked_for_historical_use(self):
        store = PointInTimeFeatureStore([
            rec(
                feature_family="availability_status",
                historical_use_allowed=False,
                leakage_class="retrospective_label_only",
            )
        ])
        out = store.read("availability_status", "fx1", "2026-01-01T10:00:00+00:00", "team:a")
        self.assertEqual(out.status, "inactive")
        self.assertEqual(out.rejected_counts.get("historical_use_not_allowed"), 1)

    def test_effective_after_as_of_is_rejected(self):
        store = PointInTimeFeatureStore([
            rec(effective_at=datetime(2026, 1, 2, 9, 0, tzinfo=UTC))
        ])
        out = store.read("lineup_pstart", "fx1", "2026-01-01T10:00:00+00:00", "team:a")
        self.assertEqual(out.status, "inactive")
        self.assertEqual(out.rejected_counts.get("effective_after_as_of"), 1)

    def test_expired_record_is_rejected(self):
        store = PointInTimeFeatureStore([
            rec(expires_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC))
        ])
        out = store.read("lineup_pstart", "fx1", "2026-01-01T10:00:00+00:00", "team:a")
        self.assertEqual(out.status, "inactive")
        self.assertEqual(out.rejected_counts.get("expired_at_as_of"), 1)

    def test_entity_mismatch_is_explicit(self):
        store = PointInTimeFeatureStore([rec()])
        out = store.read("lineup_pstart", "fx1", "2026-01-01T10:00:00+00:00", "team:b")
        self.assertEqual(out.status, "inactive")
        self.assertEqual(out.rejected_counts.get("canonical_entity_mismatch"), 1)

    def test_timezone_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            rec(known_at=datetime(2026, 1, 1, 9, 0))

    def test_record_hash_and_store_fingerprint_are_deterministic(self):
        a = rec(source_record_id="a", value={"x": 1})
        b = rec(source_record_id="b", value={"x": 2})
        s1 = PointInTimeFeatureStore([a, b])
        s2 = PointInTimeFeatureStore([b, a])
        self.assertEqual(s1.fingerprint, s2.fingerprint)
        self.assertEqual(a.record_hash, rec(source_record_id="a", value={"x": 1}).record_hash)

    def test_jsonl_round_trip_preserves_hashes(self):
        store = PointInTimeFeatureStore()
        record = rec()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pit.jsonl"
            store.append_jsonl(path, record)
            loaded = PointInTimeFeatureStore.from_jsonl(path)
            self.assertEqual(loaded.read("lineup_pstart", "fx1", "2026-01-01T10:00:00+00:00", "team:a").latest().record_hash, record.record_hash)


if __name__ == "__main__":
    unittest.main()
