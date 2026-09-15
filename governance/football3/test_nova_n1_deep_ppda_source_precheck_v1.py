from __future__ import annotations

import copy
import gzip
import hashlib
import json
import unittest
from pathlib import Path

import nova_n1_deep_ppda_source_precheck_v1 as m


def payload() -> dict:
    return {
        "dates": [{"id":"100","datetime":"2024-08-10 15:00:00","isResult":True,"h":{"id":"1","title":"Home FC"},"a":{"id":"2","title":"Away FC"},"goals":{"h":"9","a":"8"},"xG":{"h":"9.9","a":"8.8"}}],
        "teams": {
            "1":{"title":"Home FC","history":[{"date":"2024-08-10 15:00:00","h_a":"h","deep":"7","deep_allowed":"5","ppda":{"att":"24","def":"6"},"ppda_allowed":{"att":"30","def":"5"},"result":"w","xG":99}]},
            "2":{"title":"Away FC","history":[{"date":"2024-08-10 15:00:00","h_a":"a","deep":"5","deep_allowed":"7","ppda":{"att":"30","def":"5"},"ppda_allowed":{"att":"24","def":"6"},"result":"l","xG":0}]}
        }
    }


class SourcePrecheckTests(unittest.TestCase):
    def test_projection_uses_only_safe_fields(self) -> None:
        rows=m.project_payload(payload(),league="EPL",season=2024,expected_matches=1,release_delay_hours=3)
        self.assertEqual(rows[0]["home_ppda"],4.0); self.assertEqual(rows[0]["away_ppda"],6.0)
        self.assertEqual(rows[0]["home_deep"],7.0); self.assertEqual(rows[0]["away_deep"],5.0)
        self.assertEqual(rows[0]["release_at"],"2024-08-10T18:00:00Z")

    def test_result_score_xg_mutation_does_not_change_projection(self) -> None:
        a=payload(); b=copy.deepcopy(a)
        b["dates"][0]["goals"]={"h":"0","a":"0"}; b["dates"][0]["xG"]={"h":"0.1","a":"7.7"}
        b["teams"]["1"]["history"][0]["result"]="l"; b["teams"]["1"]["history"][0]["xG"]=-12345
        ra=m.project_payload(a,league="EPL",season=2024,expected_matches=1,release_delay_hours=3)
        rb=m.project_payload(b,league="EPL",season=2024,expected_matches=1,release_delay_hours=3)
        self.assertEqual(hashlib.sha256(m._canon(ra)).hexdigest(),hashlib.sha256(m._canon(rb)).hexdigest())

    def test_reciprocal_mismatch_fails_closed(self) -> None:
        p=payload(); p["teams"]["2"]["history"][0]["deep"]="6"
        with self.assertRaisesRegex(m.SourcePrecheckError,"RECIPROCAL_MISMATCH"):
            m.project_payload(p,league="EPL",season=2024,expected_matches=1,release_delay_hours=3)

    def test_completed_count_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(m.SourcePrecheckError,"COMPLETED_COUNT"):
            m.project_payload(payload(),league="EPL",season=2024,expected_matches=2,release_delay_hours=3)

    def test_duplicate_history_key_fails_closed(self) -> None:
        p=payload(); p["teams"]["1"]["history"].append(copy.deepcopy(p["teams"]["1"]["history"][0]))
        with self.assertRaisesRegex(m.SourcePrecheckError,"DUPLICATE_HISTORY_KEY"):
            m.project_payload(p,league="EPL",season=2024,expected_matches=1,release_delay_hours=3)

    def test_zero_ppda_denominator_matches_frozen_semantics(self) -> None:
        p=payload(); p["teams"]["1"]["history"][0]["ppda"]={"att":"24","def":"0"}; p["teams"]["2"]["history"][0]["ppda_allowed"]={"att":"24","def":"0"}
        rows=m.project_payload(p,league="EPL",season=2024,expected_matches=1,release_delay_hours=3)
        self.assertEqual(rows[0]["home_ppda"],0.0)

    def test_fetch_json_accepts_gzip_transport(self) -> None:
        raw=json.dumps(payload(),sort_keys=True,separators=(",",":")).encode("utf-8")
        wire=gzip.compress(raw)
        class FakeResponse:
            status=200
            headers={"Content-Encoding":"gzip"}
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self): return wire
        original=m.urllib.request.urlopen
        m.urllib.request.urlopen=lambda *args,**kwargs: FakeResponse()
        try:
            got,meta=m.fetch_json("https://example.invalid/data",referer="https://example.invalid",tries=1)
        finally:
            m.urllib.request.urlopen=original
        self.assertEqual(got,payload())
        self.assertEqual(meta["sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual(meta["bytes"],len(raw))

    def test_config_is_prelabel_and_research_only(self) -> None:
        config=json.loads(Path("nova_n1_deep_ppda_source_precheck_v1.json").read_text())
        self.assertEqual(config["status"],"PRECHECK_LOCKED_BEFORE_LABEL_READ_OR_FIT")
        self.assertTrue(config["research_only"]); self.assertFalse(config["source"]["production_eligible"])
        self.assertEqual(config["governance"]["label_values_read"],0); self.assertFalse(config["governance"]["training_performed"])
        self.assertEqual(config["governance"]["candidate_weight"],0)


if __name__=="__main__":
    unittest.main()