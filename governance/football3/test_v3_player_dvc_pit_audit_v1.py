import json, unittest
from pathlib import Path
import importlib.util

P = Path(__file__).with_name("validate_v3_player_dvc_pit_audit_v1.py")
spec = importlib.util.spec_from_file_location("v", P)
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class TestAudit(unittest.TestCase):
    def test_descriptor_url_is_descriptor_only(self):
        u = v.descriptor_url("91552aec889cc33c803e6dcd471b333b.dir")
        self.assertTrue(u.endswith(".dir")); self.assertIn("/files/md5/91/", u)
    def test_reject_bad_md5(self):
        with self.assertRaises(ValueError): v.descriptor_url("bad.dir")
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

if __name__ == "__main__": unittest.main()
