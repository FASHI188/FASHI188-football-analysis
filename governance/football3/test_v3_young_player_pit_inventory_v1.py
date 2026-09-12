import json
import unittest
from pathlib import Path
import importlib.util

P = Path(__file__).with_name("validate_v3_young_player_pit_inventory_v1.py")
spec = importlib.util.spec_from_file_location("v", P)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class TestYoungPlayerPitInventory(unittest.TestCase):
    def test_frozen_inventory_total(self):
        self.assertEqual(sum(v.PRE_STAGE6_COUNTS.values()), 99)

    def test_dvc_urls_descriptor_only(self):
        urls = v.dvc_descriptor_urls(v.HISTORICAL_DIR_MD5)
        self.assertEqual(len(urls), 2)
        for u in urls:
            self.assertTrue(u.endswith(".dir"))
            self.assertTrue(u.startswith(v.REMOTE))

    def test_bad_md5_rejected(self):
        with self.assertRaises(ValueError):
            v.dvc_descriptor_urls("bad.dir")

    def test_parse_descriptor_metadata_only(self):
        raw = json.dumps([
            {"md5": "a"*32, "relpath": "players.jsonl", "size": 12},
            {"md5": "b"*32, "relpath": "clubs.jsonl", "size": 22},
            {"md5": "c"*32, "relpath": "transfers.jsonl", "size": 32},
        ]).encode()
        rows = v.parse_descriptor(raw)
        self.assertEqual([r["relpath"] for r in rows], ["players.jsonl", "clubs.jsonl", "transfers.jsonl"])

    def test_path_traversal_blocked(self):
        raw = json.dumps([{"md5": "a"*32, "relpath": "../secret", "size": 1}]).encode()
        with self.assertRaises(ValueError):
            v.parse_descriptor(raw)

    def test_category_surface(self):
        cats = v.categorize(["players.jsonl", "clubs.jsonl", "transfers.jsonl", "player_valuations.jsonl", "games.jsonl"])
        for k in ("players", "clubs", "transfers", "valuations", "games"):
            self.assertIn(k, cats)

    def test_offline_is_zero_label(self):
        r = v.run(network=False)
        self.assertEqual(r["labels_opened"], 0)
        self.assertFalse(r["training"])
        self.assertFalse(r["tuning"])
        self.assertEqual(r["result_or_goal_values_read"], 0)
        self.assertEqual(r["referenced_dvc_data_objects_downloaded"], 0)
        self.assertFalse(r["data_ready"])

    def test_inventory_not_claimed_fresh(self):
        r = v.run(network=False)
        inv = r["confirmation_inventory_upper_bound"]
        self.assertEqual(inv["max_n"], 99)
        self.assertIsNone(inv["fresh_unconsumed_n"])
        self.assertEqual(inv["freshness_status"], "UNRESOLVED_IDENTITY_CONSUMPTION_AUDIT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
