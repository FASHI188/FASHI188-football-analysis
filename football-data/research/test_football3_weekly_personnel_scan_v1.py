#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

import football3_weekly_personnel_scan_v1 as scan


class WeeklyPersonnelScanTests(unittest.TestCase):
    def test_repository_baseline_is_exact_38_1097(self) -> None:
        validation, teams = scan.load_baseline(Path(__file__).resolve().parent)
        self.assertEqual(validation["teams"], 38)
        self.assertEqual(validation["rows"], 1097)
        self.assertEqual(len(teams), 38)
        self.assertEqual(sum(len(t["baseline_players"]) for t in teams), 1097)

    def test_url_policy_is_default_deny(self) -> None:
        allowed = "https://www.premierleague.com/en/news/example"
        self.assertEqual(
            scan.validate_source_url(allowed, "ENG_PremierLeague"),
            "www.premierleague.com",
        )
        with self.assertRaises(scan.ScanError):
            scan.validate_source_url(
                "http://www.premierleague.com/en/news/example",
                "ENG_PremierLeague",
            )
        with self.assertRaises(scan.ScanError):
            scan.validate_source_url(
                "https://www.premierleague.com/en/news/example?x=1",
                "ENG_PremierLeague",
            )
        with self.assertRaises(scan.ScanError):
            scan.validate_source_url(
                "https://example.com/roster",
                "ENG_PremierLeague",
            )

    def test_html_probe_and_team_adjudication(self) -> None:
        team = {
            "competition_id": "ESP_LaLiga",
            "team_id": "official-schedule-team:test",
            "team_name": "Example Club",
            "authority": "Example Club",
            "source_url": "https://www.fcbarcelona.com/en/football/first-team/squad",
            "source_observed_at": "2026-08-24T00:00:00Z",
            "source_published_at": "-",
            "publication_precision": "UNKNOWN",
            "evidence_type": "CURRENT_ROSTER_SNAPSHOT",
            "evidence_scope": "CURRENT_SOURCE_COMPLETE",
            "baseline_players": ["Joan Garcia", "Pedri", "Raphinha"],
        }
        document = """
        <html><body>
          <div class="player-card"><a>Joan García</a></div>
          <div class="player-card"><a>Pedri</a></div>
          <div class="player-card"><a>Raphinha</a></div>
          <div class="player-card"><a>New Signing</a></div>
        </body></html>
        """
        source = {
            "ok": True,
            "http_status": 200,
            "final_url": team["source_url"],
            "content_type": "text/html",
            "byte_count": len(document.encode()),
            "sha256": "a" * 64,
            "document": document,
            "error": None,
        }
        result = scan.adjudicate_team(team, source, shared_source_scope=False)
        self.assertEqual(result["status"], "BASELINE_HIGH_VISIBILITY")
        self.assertEqual(result["baseline_visible_count"], 3)
        self.assertEqual(result["possible_departure_candidates"], [])
        self.assertIn("New Signing", result["unverified_addition_candidates"])
        self.assertTrue(result["needs_review"])

    def test_low_visibility_does_not_claim_departures(self) -> None:
        team = {
            "competition_id": "GER_Bundesliga",
            "team_id": "official-schedule-team:test",
            "team_name": "Example Club",
            "authority": "Bundesliga",
            "source_url": "https://www.bundesliga.com/en/bundesliga/player",
            "source_observed_at": "2026-08-24T00:00:00Z",
            "source_published_at": "-",
            "publication_precision": "UNKNOWN",
            "evidence_type": "CURRENT_ROSTER_SNAPSHOT",
            "evidence_scope": "CURRENT_SOURCE_COMPLETE",
            "baseline_players": ["Alpha Player", "Beta Player", "Gamma Player", "Delta Player"],
        }
        source = {
            "ok": True,
            "http_status": 200,
            "final_url": team["source_url"],
            "content_type": "text/html",
            "byte_count": 10,
            "sha256": "b" * 64,
            "document": "<html><body><a>Alpha Player</a></body></html>",
            "error": None,
        }
        result = scan.adjudicate_team(team, source, shared_source_scope=True)
        self.assertEqual(result["status"], "SOURCE_NOT_MACHINE_READABLE_OR_ROSTER_DRIFT")
        self.assertEqual(result["possible_departure_candidates"], [])
        self.assertEqual(result["unverified_addition_candidates"], [])
        self.assertTrue(result["needs_review"])

    def test_report_boundary_fields_stay_zero_label(self) -> None:
        report = {
            "generated_at_utc": "2026-08-25T00:00:00Z",
            "source_head": "abc",
            "status": "SCAN_COMPLETE_BASELINE_STABLE",
            "summary": {
                "team_count": 38,
                "baseline_person_count": 1097,
                "source_count": 20,
                "source_fetch_ok": 20,
                "source_fetch_failed": 0,
                "team_review_count": 0,
            },
            "teams": [],
        }
        text = scan.render_markdown(report)
        self.assertIn("real labels read: `0`", text)
        self.assertIn("CURRENT/formal_weight/merge changes: `0`", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
