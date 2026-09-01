from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from candidate_c import ComponentEffect, SideDelta
from candidate_c_historical import (
    GRADE_ORDER,
    UNCERTAINTY_BANDS,
    contract,
    monotonic_contract_holds,
    monotonic_uncertainty,
    uncertainty_only_effect,
)
from historical_pit_replay import (
    allowed_labels,
    bind_packet,
    build_status_records,
    canon,
    possible_lineups,
    resolve_source_name,
)


class HistoricalPITReplayTests(unittest.TestCase):
    def test_uncertainty_contract_is_strictly_ordered(self):
        self.assertTrue(monotonic_contract_holds())
        for left, right in zip(GRADE_ORDER, GRADE_ORDER[1:]):
            self.assertLessEqual(UNCERTAINTY_BANDS[left][1], UNCERTAINTY_BANDS[right][0])
        c = contract()
        self.assertEqual(c["repair_scope"], "UNCERTAINTY_CONTRACT_ONLY")
        self.assertFalse(c["football_delta_mutation"])
        self.assertFalse(c["result_conditioning"])
        self.assertEqual(c["formal_weight"], 0)

    def test_uncertainty_mapping_monotone_within_grade(self):
        for grade in GRADE_ORDER:
            vals = [monotonic_uncertainty(grade, x) for x in (0.0, 0.1, 0.5, 1.0, 5.0)]
            self.assertEqual(vals, sorted(vals))
            lo, hi = UNCERTAINTY_BANDS[grade]
            self.assertTrue(all(lo <= x <= hi for x in vals))

    def test_uncertainty_only_effect_does_not_mutate_football_deltas(self):
        e = ComponentEffect(
            "FULL",
            True,
            SideDelta(0.01, -0.02, 0.03, 1.4),
            SideDelta(-0.04, 0.05, -0.06, 1.2),
            "ACTIVE_COMPONENT_UNION",
            ["p1"],
            2,
            4,
            5,
            "a" * 64,
        )
        z = uncertainty_only_effect(e, "POSSIBLE_XI_PIT")
        self.assertTrue(z.active)
        self.assertEqual((z.home.delta_attack, z.home.delta_defence, z.home.delta_tempo), (0.01, -0.02, 0.03))
        self.assertEqual((z.away.delta_attack, z.away.delta_defence, z.away.delta_tempo), (-0.04, 0.05, -0.06))
        self.assertEqual(z.evidence_sha256, e.evidence_sha256)
        self.assertTrue(UNCERTAINTY_BANDS["POSSIBLE_XI_PIT"][0] <= z.home.uncertainty <= UNCERTAINTY_BANDS["POSSIBLE_XI_PIT"][1])

    def test_possible_xi_parser_requires_two_elevens(self):
        h = "; ".join(f"Home{i}" for i in range(1, 12))
        a = "; ".join(f"Away{i}" for i in range(1, 12))
        text = f"Home possible starting lineup: {h}\nAway possible starting lineup: {a}\nWe say: test"
        parsed = possible_lineups(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed[0]), 11)
        self.assertEqual(len(parsed[1]), 11)
        self.assertIsNone(possible_lineups("Home possible starting lineup: A; B"))

    def test_prior_identity_resolution_fail_closed_on_ambiguous_surname(self):
        registry = {
            "T": {
                "john smith": "p1",
                "adam smith": "p2",
                "unique jones": "p3",
            }
        }
        self.assertEqual(resolve_source_name("John Smith", "T", registry), ("p1", "PRIOR_HISTORY_EXACT"))
        self.assertEqual(resolve_source_name("Jones", "T", registry), ("p3", "PRIOR_HISTORY_UNIQUE_SURNAME"))
        with self.assertRaises(Exception):
            resolve_source_name("Smith", "T", registry)

    def test_status_parser_only_emits_explicit_status_semantics(self):
        registry = {"H": {"john smith": "p1"}, "A": {"adam jones": "p2"}}
        rows = build_status_records("John Smith is suspended. Adam Jones is injured.", "H", "A", registry)
        by = {(x["player_id"], x["status_type"]) for x in rows}
        self.assertIn(("p1", "SUSPENSION"), by)
        self.assertIn(("p2", "INJURY_OR_AVAILABILITY"), by)

    def test_bound_packet_never_invents_probability_or_minutes(self):
        src = {
            "fixture_id": "f1",
            "kickoff_utc": "2023-09-01T19:00:00Z",
            "cutoff_utc": "2023-09-01T18:45:00Z",
            "home_team_id": "H",
            "away_team_id": "A",
            "home_team": "Home",
            "away_team": "Away",
            "pit_legal": True,
            "source": {
                "source_url": "https://example.invalid/pre",
                "proof_url": "https://example.invalid/pre",
                "proof_type": "SOURCE_DECLARED_MODIFIED_AT_PRE_CUTOFF",
                "published_at": "2023-08-31T10:00:00Z",
                "source_proof_at": "2023-08-31T10:00:00Z",
                "collected_at": "2026-09-01T00:00:00Z",
                "page_sha256": "a" * 64,
                "page_bytes": 123,
                "raw_content_scope": "EXACT_H2_TEAM_NEWS_SECTION_ONLY",
                "raw_content_sha256": "b" * 64,
                "modified_at": "2023-08-31T10:00:00Z",
            },
            "source_text_for_identity_only": "",
            "possible_lineup_source_names": {
                "home": [f"H{i}" for i in range(11)],
                "away": [f"A{i}" for i in range(11)],
            },
            "confirmed_lineups": None,
            "bench": None,
        }
        packet = bind_packet(src, {})
        for side in ("home", "away"):
            for row in packet["predicted_lineups"][side]:
                self.assertIsNone(row["starting_probability"])
                self.assertIsNone(row["expected_minutes"])
        self.assertEqual(packet["probability_contract"], "NO_SOURCE_PLAYER_START_PROBABILITIES_DO_NOT_INVENT")
        self.assertEqual(packet["packet_sha256"], canon({k: v for k, v in packet.items() if k != "packet_sha256"}))

    def test_scorer_whitelists_before_json_parse(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "labels.jsonl"
            p.write_text(
                json.dumps({"fixture_id": "allowed", "cutoff": "x", "home_goals": 1, "away_goals": 0}) + "\n" +
                json.dumps({"fixture_id": "other", "cutoff": "y", "home_goals": 9, "away_goals": 9}) + "\n",
                encoding="utf-8",
            )
            labels = allowed_labels(p, {"allowed"})
            self.assertEqual(set(labels), {"allowed"})
            self.assertEqual(labels["allowed"]["home_goals"], 1)


if __name__ == "__main__":
    unittest.main()
