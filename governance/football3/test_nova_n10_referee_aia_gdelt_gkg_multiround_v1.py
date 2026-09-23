from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_gdelt_gkg_multiround_v1 import validate_target
from nova_n10_referee_aia_gdelt_gkg_daily_v1 import normalize_identity

REG=Path(__file__).with_name("nova_n10_referee_aia_gdelt_gkg_multiround_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"23b2fc25297cb011e0c54fe4f4691a1eb8b9f94b")
        self.assertEqual([x["round"] for x in p["targets"]],[1,19,38])
        self.assertEqual(sum(len(x["date_sequence"]) for x in p["targets"]),24)
        self.assertFalse(p["hard_rules"]["switch_source_family_before_completion"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_each_target_identity(self):
        p=json.loads(REG.read_text())
        for t in p["targets"]:
            validate_target(t)
            self.assertEqual(
                normalize_identity(t["official_url"]),
                (t["normalized_host"],t["normalized_path"])
            )

    def test_round1_window(self):
        p=json.loads(REG.read_text())
        t=p["targets"][0]
        self.assertEqual(t["date_sequence"][0],"2022-08-10")
        self.assertEqual(t["date_sequence"][-1],"2022-08-17")
        self.assertEqual(len(t["date_sequence"]),8)

    def test_round19_window(self):
        p=json.loads(REG.read_text())
        t=p["targets"][1]
        self.assertEqual(t["date_sequence"][0],"2023-01-19")
        self.assertEqual(t["date_sequence"][-1],"2023-01-26")
        self.assertEqual(len(t["date_sequence"]),8)

    def test_round38_window(self):
        p=json.loads(REG.read_text())
        t=p["targets"][2]
        self.assertEqual(t["date_sequence"][0],"2023-05-31")
        self.assertEqual(t["date_sequence"][-1],"2023-06-07")
        self.assertEqual(len(t["date_sequence"]),8)

if __name__=="__main__":
    unittest.main()
