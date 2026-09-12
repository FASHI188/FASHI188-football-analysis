import importlib.util, json, unittest
from pathlib import Path
P=Path(__file__).with_name('validate_v3_young_player_schema_identity_v1.py')
s=importlib.util.spec_from_file_location('v',P); v=importlib.util.module_from_spec(s); s.loader.exec_module(v)
class T(unittest.TestCase):
    def test_exact_targets(self):
        self.assertEqual(sum(map(len,v.TARGETS.values())),4)
        self.assertFalse(any('games.json' in x for xs in v.TARGETS.values() for x in xs))
    def test_descriptor_only_urls(self):
        for md5,_ in v.DESCRIPTORS.values():
            u=v.descriptor_url(md5); self.assertTrue(u.endswith('.dir')); self.assertTrue(u.startswith(v.REMOTE))
    def test_object_url_is_hash_bound(self):
        u=v.object_url('a'*32); self.assertEqual(u, v.REMOTE+'files/md5/aa/'+'a'*30); self.assertFalse(u.endswith('.dir'))
        with self.assertRaises(ValueError): v.object_url('bad')
    def test_parse_allows_missing_size_for_head_only_fallback(self):
        raw=json.dumps([{'relpath':'2025/players.json.gz','md5':'a'*32}]).encode()
        self.assertIsNone(v.parse(raw)[0]['size'])
    def test_offline_zero_label(self):
        r=v.freeze(False); self.assertEqual(r['labels_opened'],0); self.assertEqual(r['referenced_dvc_data_objects_downloaded'],0); self.assertEqual(r['object_get_requests'],0); self.assertFalse(r['training']); self.assertFalse(r['tuning']); self.assertFalse(r['data_ready'])
    def test_forbidden_surfaces_absent(self):
        flat=' '.join(x for xs in v.TARGETS.values() for x in xs); self.assertNotIn('game_lineups',flat); self.assertNotIn('game_events',flat)
if __name__=='__main__': unittest.main()
