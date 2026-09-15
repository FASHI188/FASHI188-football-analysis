#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from nova_mhsendur_openfootball_canonical_binding_v1 import (
    BindingError,
    bind_projection,
    build_alias_maps,
    kickoff_utc,
    mhsendur_kickoff_utc,
    normalize_openfootball_match,
    surface_key,
    team_key,
)

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "nova_mhsendur_openfootball_canonical_binding_v1.json").read_text(encoding="utf-8"))


def source_row(match_id: str, home: str, away: str, kickoff: str, comp: str = "DE_BUNDESLIGA") -> dict:
    return {
        "match_id": match_id,
        "competition_id": comp,
        "season": "2014/15",
        "kickoff": kickoff,
        "home_team_id": "h",
        "away_team_id": "a",
        "home_team_name": home,
        "away_team_name": away,
        "source_id": "OPENFOOTBALL_FOOTBALL_JSON",
        "source_revision": CONFIG["openfootball_revision"],
        "source_path": "2014-15/de.1.json",
        "input_sha256": "a" * 64,
    }


def feat(home: str, away: str, date: str = "2014-08-22 19:30:00", league: str = "Bundesliga") -> dict:
    return {
        "source_local_match_key": hashlib.sha256(f"{home}-{away}-{date}".encode()).hexdigest(),
        "source_id": "MHSENDUR_UNDERSTAT_2014_2023_RESEARCH",
        "source_revision": "7fc2d32037ea9bcb83edfbae366c70c938f03e26",
        "league": league,
        "season_start": "2014",
        "date": date,
        "home_team": home,
        "away_team": away,
        "home_ppda": "7",
        "away_ppda": "8",
        "home_deep": "10",
        "away_deep": "9",
        "feature_input_sha256": hashlib.sha256(f"features-{home}-{away}".encode()).hexdigest(),
    }


class CanonicalBindingTests(unittest.TestCase):
    def test_timezone_bridge_is_exact_utc(self):
        self.assertEqual(
            kickoff_utc("2014-08-22", "20:30", "Europe/Berlin"),
            mhsendur_kickoff_utc("2014-08-22 19:30:00", "Europe/London"),
        )
        self.assertEqual(
            kickoff_utc("2014-12-12", "20:30", "Europe/Berlin"),
            mhsendur_kickoff_utc("2014-12-12 19:30:00", "Europe/London"),
        )

    def test_predeclared_aliases_cover_known_cross_source_names(self):
        maps = build_alias_maps(CONFIG)
        pairs = [
            ("Bayern Munich", "Bayern München"),
            ("FC Cologne", "1. FC Köln"),
            ("Borussia M.Gladbach", "Bor. Mönchengladbach"),
            ("Brighton", "Brighton & Hove Albion"),
            ("West Ham", "West Ham United"),
            ("Paris Saint Germain", "Paris Saint-Germain"),
            ("Verona", "Hellas Verona"),
            ("Parma Calcio 1913", "Parma FC"),
        ]
        for mh, of in pairs:
            self.assertEqual(team_key(mh, "mhsendur", maps), team_key(of, "openfootball", maps), (mh, of))

    def test_score_is_not_required_for_openfootball_identity(self):
        comp = CONFIG["competitions"]["Bundesliga"]
        row = normalize_openfootball_match(
            {"date": "2014-08-22", "time": "20:30", "team1": "Bayern München", "team2": "VfL Wolfsburg"},
            comp, 2014, "2014-15/de.1.json", CONFIG["openfootball_revision"], "b" * 64,
        )
        self.assertEqual(row["season"], "2014/15")
        self.assertNotIn("score", row)
        self.assertNotIn("result_1x2", row)

    def test_exact_alias_date_kickoff_binding_passes(self):
        kickoff = kickoff_utc("2014-08-22", "20:30", "Europe/Berlin")
        bound, receipt = bind_projection(
            [feat("Bayern Munich", "Wolfsburg")],
            [source_row("openfootball:one", "Bayern München", "VfL Wolfsburg", kickoff)],
            CONFIG,
        )
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["match_id"], "openfootball:one")
        self.assertEqual(bound[0]["binding_mode"], "EXACT_KICKOFF_TEAM")
        self.assertEqual(receipt["status"], "CANONICAL_MATCH_ID_BINDING_PASS")
        self.assertTrue(receipt["canonical_match_id_binding_complete"])
        self.assertEqual(receipt["candidate_roles_assigned"], 0)
        self.assertEqual(receipt["score_values_used"], 0)

    def test_unique_date_team_fallback_passes_when_source_time_drifts(self):
        source_kickoff = kickoff_utc("2014-08-22", "20:45", "Europe/Berlin")
        bound, receipt = bind_projection(
            [feat("Bayern Munich", "Wolfsburg")],
            [source_row("openfootball:one", "Bayern München", "VfL Wolfsburg", source_kickoff)],
            CONFIG,
        )
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["binding_mode"], "UNIQUE_DATE_TEAM_FALLBACK")
        self.assertNotEqual(bound[0]["kickoff"], bound[0]["feature_kickoff"])
        self.assertEqual(receipt["binding_mode_counts"], {"UNIQUE_DATE_TEAM_FALLBACK": 1})

    def test_ambiguous_binding_fails_closed(self):
        kickoff = kickoff_utc("2014-08-22", "20:30", "Europe/Berlin")
        rows = [
            source_row("openfootball:one", "Bayern München", "VfL Wolfsburg", kickoff),
            source_row("openfootball:two", "Bayern München", "VfL Wolfsburg", kickoff),
        ]
        with self.assertRaisesRegex(BindingError, "fail-closed"):
            bind_projection([feat("Bayern Munich", "Wolfsburg")], rows, CONFIG)

    def test_unmatched_binding_fails_closed_with_same_date_context(self):
        kickoff = kickoff_utc("2014-08-22", "20:30", "Europe/Berlin")
        with self.assertRaisesRegex(BindingError, "same_date_source_pairs"):
            bind_projection(
                [feat("Bayern Munich", "Wolfsburg")],
                [source_row("openfootball:one", "Borussia Dortmund", "Bayer Leverkusen", kickoff)],
                CONFIG,
            )

    def test_duplicate_canonical_consumption_fails(self):
        kickoff = kickoff_utc("2014-08-22", "20:30", "Europe/Berlin")
        f1 = feat("Bayern Munich", "Wolfsburg")
        f2 = copy.deepcopy(f1)
        f2["source_local_match_key"] = "c" * 64
        with self.assertRaisesRegex(BindingError, "fail-closed"):
            bind_projection(
                [f1, f2],
                [source_row("openfootball:one", "Bayern München", "VfL Wolfsburg", kickoff)],
                CONFIG,
            )

    def test_surface_normalization_is_not_fuzzy(self):
        self.assertEqual(surface_key("Saint-Étienne"), "saint etienne")
        self.assertNotEqual(surface_key("Leicester"), surface_key("Leicester City"))


if __name__ == "__main__":
    unittest.main()
