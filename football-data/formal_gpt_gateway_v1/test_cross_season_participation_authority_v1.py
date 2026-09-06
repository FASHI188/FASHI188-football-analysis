#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

import cross_season_participation_authority_v1 as participation
import runtime as rt

REPO = Path(__file__).resolve().parents[2]


class ParticipationAuthorityTests(unittest.TestCase):
    def _ident(self, comp: str, season: str, current: str, classification: str,
               history: int = 0, local: bool = False, global_: bool = False) -> dict:
        return {
            "current_name": current,
            "participation_classification": classification,
            "historical_match_count": history,
            "state_local_evidence": local,
            "state_global_evidence": global_,
            "mapping_provenance": {"competition": comp, "current_season": season},
        }

    def test_seven_domain_promoted_sets_are_frozen_and_exact(self):
        expected = {
            "ENG_PremierLeague": ("2026/27", {"Coventry City", "Hull City", "Ipswich Town"}),
            "ESP_LaLiga": ("2026/27", {"Racing Santander", "Deportivo de la Coruna", "Malaga"}),
            "GER_Bundesliga": ("2026/27", {"Schalke 04", "Elversberg", "Paderborn 07"}),
            "ITA_SerieA": ("2026/27", {"Frosinone", "Monza", "Venezia"}),
            "FRA_Ligue1": ("2026/27", {"Le Mans", "Troyes"}),
            "JPN_J1": ("2026", {"JEF United Ichihara-Chiba", "Mito Hollyhock", "V-Varen Nagasaki"}),
            "KOR_KLeague1": ("2026", {"Bucheon FC 1995", "Incheon United"}),
        }
        for comp, (season, names) in expected.items():
            with self.subTest(comp=comp):
                self.assertEqual(set(participation.authoritative_promoted_names(REPO, comp, season)), names)

    def test_zero_history_cannot_authorize_promotion(self):
        ident = self._ident("ENG_PremierLeague", "2026/27", "Brighton & Hove Albion", "PROMOTED_OR_ENTERING_CLUB", history=0)
        with self.assertRaisesRegex(rt.RuntimeGateError, "PROMOTED_CLASSIFICATION_LACKS_AUTHORITATIVE_PARTICIPATION_EVIDENCE"):
            participation.validate_team(REPO, ident)

    def test_authoritative_promoted_zero_history_is_allowed_by_participation_contract(self):
        ident = self._ident("ENG_PremierLeague", "2026/27", "Coventry City", "PROMOTED_OR_ENTERING_CLUB", history=0)
        out = participation.validate_team(REPO, ident)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["expected_class"], "AUTHORITATIVE_PROMOTED_OR_ENTERING")
        self.assertTrue(out["promotion_evidence"])

    def test_returning_requires_historical_state(self):
        ident = self._ident("ENG_PremierLeague", "2026/27", "Manchester City", "RENAMED_OR_REKEYED_CLUB", history=0)
        with self.assertRaisesRegex(rt.RuntimeGateError, "RETURNING_EXPECTED_HISTORICAL_STATE_MISSING"):
            participation.validate_team(REPO, ident)

    def test_j1_transition_and_k1_natural_year_are_explicit(self):
        j = participation.domain_contract(REPO, "JPN_J1", "2026")
        self.assertEqual(j["official_current_season"], "2026/27")
        self.assertEqual(j["frozen_history_reference_season"], "2025")
        self.assertEqual(j["season_model"], "J1_2026_TRANSITION_TO_AUTUMN_SPRING")
        self.assertEqual(j["transition_predecessor"], "2026_MEIJI_YASUDA_J1_100_YEAR_VISION_LEAGUE")
        k = participation.domain_contract(REPO, "KOR_KLeague1", "2026")
        self.assertEqual(k["official_current_season"], "2026")
        self.assertEqual(k["frozen_history_reference_season"], "2025")
        self.assertEqual(k["season_model"], "NATURAL_YEAR")

    def test_false_promoted_repairs_are_exact_continuity_rows(self):
        obj = json.loads((REPO / "football-data/config/cross_season_identity_continuity_v1.json").read_text(encoding="utf-8"))
        rows = {(x["competition_id"], x["current_canonical_name"]): x["previous_processed_name"] for x in obj["rows"]}
        expected = {
            ("ENG_PremierLeague", "Brighton & Hove Albion"): "Brighton",
            ("ENG_PremierLeague", "Manchester City"): "Man City",
            ("ENG_PremierLeague", "Newcastle United"): "Newcastle",
            ("JPN_J1", "Fagiano Okayama"): "Okayama",
            ("JPN_J1", "Machida Zelvia"): "Machida",
            ("JPN_J1", "Tokyo Verdy 1969"): "Verdy",
            ("JPN_J1", "Urawa Red Diamonds"): "Urawa Reds",
            ("KOR_KLeague1", "Daejeon Hana Citizen"): "대전",
            ("KOR_KLeague1", "FC Anyang"): "안양",
            ("KOR_KLeague1", "Gangwon FC"): "강원",
            ("KOR_KLeague1", "Gimcheon Sangmu"): "김천",
            ("KOR_KLeague1", "Gwangju FC"): "광주",
            ("KOR_KLeague1", "Jeju SK"): "제주",
            ("KOR_KLeague1", "Pohang Steelers"): "포항",
            ("KOR_KLeague1", "Ulsan HD"): "울산",
        }
        for key, previous in expected.items():
            with self.subTest(key=key):
                self.assertEqual(rows.get(key), previous)


if __name__ == "__main__":
    unittest.main(verbosity=2)
