import json, unittest
from pathlib import Path
import importlib.util

P = Path(__file__).with_name("validate_v3_player_dvc_pit_audit_v1.py")
spec = importlib.util.spec_from_file_location("v", P)
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class TestAudit(unittest.TestCase):
    def test_descriptor_urls_are_descriptor_only(self):
        urls = v.descriptor_urls("91552aec889cc33c803e6dcd471b333b.dir")
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(u.endswith(".dir") for u in urls))
        self.assertIn("/files/md5/91/", urls[0]); self.assertIn("/dvc/91/", urls[1])
    def test_reject_bad_md5(self):
        with self.assertRaises(ValueError): v.descriptor_urls("bad.dir")
    def test_parse_descriptor_metadata_only(self):
        raw = json.dumps([{"md5":"a"*32,"relpath":"players.jsonl","size":12},{"md5":"b"*32,"relpath":"games.jsonl","size":22}]).encode()
        rows = v.parse_descriptor_bytes(raw)
        self.assertEqual([r["relpath"] for r in rows], ["players.jsonl","games.jsonl"])
    def test_path_traversal_blocked(self):
        raw = json.dumps([{"md5":"a"*32,"relpath":"../secret","size":1}]).encode()
        with self.assertRaises(ValueError): v.parse_descriptor_bytes(raw)
    def test_categories(self):
        c = v.categorize(["players.jsonl","game_lineups.jsonl","transfers.jsonl","foo.txt"])
        self.assertIn("players", c); self.assertIn("lineups", c); self.assertIn("transfers", c)
    def test_offline_never_data_ready(self):
        r = v.run(network=False)
        self.assertEqual(r["labels_opened"],0); self.assertFalse(r["training"]); self.assertFalse(r["tuning"]); self.assertFalse(r["data_ready"])
    def test_no_data_object_download_counter(self):
        r = v.run(network=False); self.assertEqual(r["downloaded_data_objects"], 0)
    def test_canary_is_current_only_transport_control(self):
        self.assertEqual(v.CURRENT_CANARY["dir_md5"], "c9ec80fd8b18310f7bded68fe92b67d3.dir")

if __name__ == "__main__": unittest.main()
