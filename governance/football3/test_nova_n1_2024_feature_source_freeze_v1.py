#!/usr/bin/env python3
import pathlib
import sqlite3
import tempfile
import unittest

import nova_n1_2024_feature_source_freeze_v1 as m


class FeatureSourceFreezeTests(unittest.TestCase):
    def _db(self, *, bad_ppda=False):
        td = tempfile.TemporaryDirectory()
        p = pathlib.Path(td.name) / "x.db"
        con = sqlite3.connect(p)
        con.execute("""create table general_game_stats(
            id integer,date text,league text,season text,team_h text,team_a text,h_id integer,a_id integer,
            h_deep real,a_deep real,h_ppda real,a_ppda real,h_goals integer,a_goals integer,h_xg real,a_xg real
        )""")
        rows = [
            (1,"2024-08-15 12:00:00","EPL",9999,"A","B",10,20,5,4,9 if not bad_ppda else 0,10,3,1,2.1,0.7),
            (2,"2025-05-25 12:00:00","La liga","2024/25","C","D",30,40,6,7,8,11,1,1,1.2,1.3),
        ]
        con.executemany("insert into general_game_stats values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit(); con.close()
        return td, p

    def test_extract_is_feature_only(self):
        td, p = self._db()
        try:
            rows, audit = m.extract_feature_rows(p, expected_n=None, expected_counts=None, expected_fixture_sha=None)
            self.assertEqual(len(rows), 2)
            forbidden = {"h_goals","a_goals","h_xg","a_xg","result"}
            self.assertTrue(all(not (set(r) & forbidden) for r in rows))
            self.assertEqual(audit["labels_read"], 0)
            self.assertFalse(audit["score_or_result_columns_read"])
            self.assertFalse(audit["xg_columns_read"])
        finally:
            td.cleanup()

    def test_ppda_must_be_positive(self):
        td, p = self._db(bad_ppda=True)
        try:
            with self.assertRaises(m.FeatureFreezeError):
                m.extract_feature_rows(p, expected_n=None, expected_counts=None, expected_fixture_sha=None)
        finally:
            td.cleanup()

    def test_fixture_set_hash_is_order_invariant(self):
        self.assertEqual(m.fixture_set_sha(["2","1"]), m.fixture_set_sha(["1","2"]))

    def test_selection_uses_locked_date_window_not_provider_season_key(self):
        td, p = self._db()
        try:
            rows, audit = m.extract_feature_rows(p, expected_n=None, expected_counts=None, expected_fixture_sha=None)
            self.assertEqual(len(rows), 2)
            self.assertEqual(audit["target_date_window"], ["2024-08-15", "2025-05-26"])
            self.assertEqual(set(audit["source_season_keys"]), {"9999", "2024/25"})
        finally:
            td.cleanup()

    def test_query_contract_excludes_labels(self):
        names = {x.casefold() for x in m.QUERY_COLUMNS}
        self.assertFalse(names & {x.casefold() for x in m.FORBIDDEN_QUERY_COLUMNS})
        self.assertEqual(set(m.QUERY_COLUMNS), {"id","date","league","season","team_h","team_a","h_id","a_id","h_deep","a_deep","h_ppda","a_ppda"})


if __name__ == "__main__":
    unittest.main()
