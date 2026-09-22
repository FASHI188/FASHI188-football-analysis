from __future__ import annotations
import json,tempfile,unittest
from pathlib import Path
from nova_n10_referee_data_coverage_audit_v1 import run
REG=Path(__file__).with_name('nova_n10_referee_data_coverage_registry_v1.json')

class T(unittest.TestCase):
    def test_real_registry(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run(REG,Path(d))['classification'],'STOP_DATA_COVERAGE')
    def test_no_labels(self):
        p=json.loads(REG.read_text()); self.assertEqual(p['safety']['result_labels_read'],0); self.assertFalse(p['safety']['training_performed'])
    def test_three_sample_leagues(self):
        p=json.loads(REG.read_text()); self.assertEqual(p['discovery_summary']['official_target_season_sample_pit_evidence_league_n'],3)
    def test_two_blocked_leagues(self):
        p=json.loads(REG.read_text()); self.assertEqual(set(p['discovery_summary']['blocked_leagues']),{'Bundesliga','Ligue_1'})
    def test_retrospective_archives_not_pit(self):
        p=json.loads(REG.read_text()); m={x['id']:x for x in p['candidates']}
        self.assertEqual(m['TRANSFERMARKT_DATASET_REFEREE']['status'],'REJECT_PIT_FOR_TARGET_ASSIGNMENT')
        self.assertEqual(m['FOOTBALL_DATA_CO_UK_REFEREE']['status'],'REJECT_PIT_FOR_TARGET_ASSIGNMENT')
if __name__=='__main__': unittest.main()
