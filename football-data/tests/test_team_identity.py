from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from identity.team_identity import TeamIdentityResolver


class TeamIdentityResolverTests(unittest.TestCase):
    def test_exact_source_id_resolves(self):
        resolver = TeamIdentityResolver([
            {
                "source_namespace": "api_football",
                "source_team_id": 40,
                "canonical_team_id": "team:liverpool",
                "mapping_method": "pinned_crosswalk",
                "provenance_hash": "abc",
            }
        ])
        out = resolver.resolve("API_FOOTBALL", 40, "Liverpool")
        self.assertEqual(out.status, "resolved")
        self.assertEqual(out.canonical_team_id, "team:liverpool")
        self.assertEqual(out.mapping_method, "pinned_crosswalk")
        self.assertEqual(out.provenance_hash, "abc")

    def test_same_source_id_conflict_fails_closed(self):
        resolver = TeamIdentityResolver([
            {"source_namespace": "x", "source_team_id": "1", "canonical_team_id": "a"},
            {"source_namespace": "x", "source_team_id": "1", "canonical_team_id": "b"},
        ])
        out = resolver.resolve("x", "1")
        self.assertEqual(out.status, "ambiguous")
        self.assertIsNone(out.canonical_team_id)

    def test_reverse_conflict_within_namespace_fails_closed(self):
        resolver = TeamIdentityResolver([
            {"source_namespace": "x", "source_team_id": "1", "canonical_team_id": "a"},
            {"source_namespace": "x", "source_team_id": "2", "canonical_team_id": "a"},
        ])
        self.assertEqual(resolver.resolve("x", "1").status, "ambiguous")
        self.assertEqual(resolver.resolve("x", "2").status, "ambiguous")

    def test_explicit_alias_is_exact_not_fuzzy(self):
        resolver = TeamIdentityResolver([
            {
                "source_namespace": "market",
                "canonical_team_id": "team:inter",
                "approved_name_alias": "Inter Milan",
                "mapping_method": "manual_approved_alias",
                "provenance_hash": "def",
            }
        ])
        exact = resolver.resolve("market", source_name="  INTER   MILAN ")
        fuzzy = resolver.resolve("market", source_name="Inter Milano")
        self.assertEqual(exact.status, "resolved")
        self.assertEqual(exact.canonical_team_id, "team:inter")
        self.assertEqual(fuzzy.status, "unresolved")

    def test_alias_conflict_fails_closed(self):
        resolver = TeamIdentityResolver([
            {"source_namespace": "m", "canonical_team_id": "a", "approved_name_alias": "United"},
            {"source_namespace": "m", "canonical_team_id": "b", "approved_name_alias": "United"},
        ])
        self.assertEqual(resolver.resolve("m", source_name="United").status, "ambiguous")

    def test_fingerprint_is_insertion_order_independent(self):
        rows = [
            {"source_namespace": "a", "source_team_id": "1", "canonical_team_id": "c1", "provenance_hash": "h1"},
            {"source_namespace": "b", "source_team_id": "9", "canonical_team_id": "c2", "provenance_hash": "h2"},
            {"source_namespace": "m", "canonical_team_id": "c3", "approved_name_alias": "Club C", "provenance_hash": "h3"},
        ]
        r1 = TeamIdentityResolver(rows)
        r2 = TeamIdentityResolver(list(reversed(rows)))
        self.assertEqual(r1.fingerprint, r2.fingerprint)
        self.assertFalse(r1.diagnostics()["fuzzy_matching_enabled"])

    def test_unresolved_is_explicit(self):
        resolver = TeamIdentityResolver([])
        out = resolver.resolve("unknown", "123", "Some Club")
        self.assertEqual(out.status, "unresolved")
        self.assertEqual(out.reason, "no_exact_source_id_or_approved_alias")


if __name__ == "__main__":
    unittest.main()
