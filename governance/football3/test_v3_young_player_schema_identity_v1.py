import importlib.util, json, unittest
from pathlib import Path
P=Path(__file__).with_name('validate_v3_young_player_schema_identity_v1.py')
s=importlib.util.spec_from_file_location('v',P); v=importlib.util.module_from_spec(s); s.loader.exec_module(v)
class T(unittest.TestCase):
    def test_allowlist_exact_and_appearance_blocked(self):
        a=v.load_allowlist(); self.assertEqual(len(a['objects']),4)
        enabled=[x for x in a['objects'] if x.get('download_allowed')]
        blocked=[x for x in a['objects'] if not x.get('download_allowed')]
        self.assertEqual({x['relpath'] for x in enabled},{'2025/players.json.gz','2025/clubs.json.gz','2025/transfers.json'})
        self.assertEqual([x['relpath'] for x in blocked],['2025/appearances.json.gz'])
        for x in a['objects']:
            self.assertEqual(len(x['md5']),32); self.assertGreater(x['size'],0)
    def test_object_url_is_hash_bound(self):
        u=v.object_url('a'*32); self.assertEqual(u,v.REMOTE+'files/md5/aa/'+'a'*30)
        with self.assertRaises(ValueError): v.object_url('bad')
    def test_required_tables_exclude_appearances_and_games(self):
        self.assertEqual(set(v.REQUIRED),{'players','clubs','transfers'})
        self.assertNotIn('appearances',v.REQUIRED); self.assertNotIn('games',v.REQUIRED)
    def test_offline_zero_label(self):
        r=v.audit(False); self.assertEqual(r['labels_opened'],0); self.assertEqual(r['target_result_or_goal_values_read'],0); self.assertFalse(r['training']); self.assertFalse(r['tuning']); self.assertFalse(r['data_ready']); self.assertEqual(r['decision'],'OFFLINE_SYNTHETIC_ONLY')
        self.assertEqual([x['relpath'] for x in r['blocked_objects']],['2025/appearances.json.gz'])
    def test_date_parser(self):
        self.assertEqual(v.parse_date('2025-01-02').isoformat(),'2025-01-02'); self.assertIsNone(v.parse_date('bad'))
if __name__=='__main__': unittest.main()
