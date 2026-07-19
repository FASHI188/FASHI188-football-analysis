import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "ingest_batch_002_kleague_adapter.py"
SPEC = importlib.util.spec_from_file_location("ingest_batch_002_kleague_adapter_test", MODULE_PATH)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class KLeagueAdapterTests(unittest.TestCase):
    def setUp(self):
        self.competition = {
            "competition_id": "KOR_KLeague1",
            "exclude_meet_name_tokens": ["승강", "플레이오프"],
        }

    def test_finished_match_is_normalized_and_staged(self):
        payload = {
            "data": {
                "clubList": [
                    {"teamId": "A", "teamNameShort": "서울"},
                    {"teamId": "B", "teamNameShort": "울산"},
                ],
                "scheduleList": [
                    {
                        "leagueId": 1,
                        "meetName": "하나은행 K리그1 2025",
                        "roundId": 12,
                        "gameId": 1001,
                        "gameDate": "2025.05.03",
                        "gameTime": "16:30",
                        "gameStatus": "FE",
                        "endYn": "Y",
                        "homeTeam": "A",
                        "awayTeam": "B",
                        "homeGoal": 2,
                        "awayGoal": 1,
                        "fieldName": "서울월드컵",
                    }
                ],
            }
        }
        rows, audit = mod.parse_kleague_payload(payload, "2025", self.competition)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["HomeTeam"], "서울")
        self.assertEqual(rows[0]["AwayTeam"], "울산")
        self.assertEqual(rows[0]["FTR"], "H")
        self.assertEqual(rows[0]["stage"], "regular_rounds_1_33")
        self.assertEqual(audit["finished_rows_selected"], 1)

    def test_final_round_and_playoff_exclusion(self):
        payload = {
            "data": {
                "scheduleList": [
                    {
                        "meetName": "K리그1 파이널A",
                        "roundId": 35,
                        "gameId": 2001,
                        "gameDate": "2025.11.01",
                        "gameStatus": "FE",
                        "endYn": "Y",
                        "homeTeamName": "전북",
                        "awayTeamName": "포항",
                        "homeGoal": 1,
                        "awayGoal": 1,
                    },
                    {
                        "meetName": "K리그 승강 플레이오프",
                        "roundId": 1,
                        "gameId": 2002,
                        "gameDate": "2025.12.03",
                        "gameStatus": "FE",
                        "endYn": "Y",
                        "homeTeamName": "대구",
                        "awayTeamName": "수원",
                        "homeGoal": 0,
                        "awayGoal": 2,
                    },
                ]
            }
        }
        rows, audit = mod.parse_kleague_payload(payload, "2025", self.competition)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "final_rounds_34_38")
        self.assertEqual(rows[0]["FTR"], "D")
        self.assertEqual(audit["excluded_other_competition_rows"], 1)

    def test_scheduled_match_is_not_treated_as_result(self):
        payload = {
            "data": {
                "scheduleList": [
                    {
                        "meetName": "K리그1",
                        "roundId": 20,
                        "gameId": 3001,
                        "gameDate": "2026.08.01",
                        "gameStatus": "NS",
                        "endYn": "N",
                        "homeTeamName": "서울",
                        "awayTeamName": "전북",
                        "homeGoal": None,
                        "awayGoal": None,
                    }
                ]
            }
        }
        rows, audit = mod.parse_kleague_payload(payload, "2026", self.competition)
        self.assertEqual(rows, [])
        self.assertEqual(audit["scheduled_or_scoreless_rows_skipped"], 1)


if __name__ == "__main__":
    unittest.main()
