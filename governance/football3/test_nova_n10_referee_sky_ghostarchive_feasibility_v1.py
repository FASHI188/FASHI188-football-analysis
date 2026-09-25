from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_ghostarchive_feasibility_v1 import (
    anti_bot_marker,
    audit_rows,
    build_search_url,
    classify,
    parse_rows,
    timestamp_candidates,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_ghostarchive_feasibility_registry_v1.json")
TARGET="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"


class T(unittest.TestCase):
    def test_registry_scope_and_contract_pins(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"3f3cfc227641ca7a5dcd90a9123697f11f9e7e97")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["search_contract_sources"][0]["commit"],"55fdc132c9ca5979c8e4ea3f84eb750913793ba3")
        self.assertEqual(p["search_contract_sources"][0]["git_blob_sha"],"bc98c9e8c18b3892af7fabab8e7951897d1af037")
        self.assertEqual(p["search_contract_sources"][1]["git_blob_sha"],"21d02d5025c2351fe4dfeb473399125cc5e774c8")
        self.assertFalse(p["hard_rules"]["archive_snapshot_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["archive_creation_allowed"])

    def test_build_search_url_exact_term(self):
        q=build_search_url("https://ghostarchive.org/search",TARGET)
        self.assertTrue(q.startswith("https://ghostarchive.org/search?"))
        self.assertIn("term=https%3A%2F%2Fsport.sky.it",q)

    def test_parse_rows_and_positive_audit(self):
        raw=f"""
        <html><body><table><tbody>
        <tr><th>URL</th><th>Archive</th><th>Time</th></tr>
        <tr>
          <td><a href="{TARGET}">{TARGET}</a></td>
          <td><a href="/archive/AbCd1">snapshot</a></td>
          <td>2023-05-04 12:00:00 UTC</td>
        </tr>
        </tbody></table></body></html>
        """.encode()
        rows=parse_rows(raw,"https://ghostarchive.org/search?term=x")
        out=audit_rows(rows,TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z")
        self.assertEqual(out["identity_archive_row_n"],1)
        self.assertEqual(out["metadata_insufficient_row_n"],0)
        self.assertEqual(out["witness_pass_n"],1)
        self.assertEqual(out["witnesses"][0]["capture_utc"],"2023-05-04T12:00:00Z")

    def test_target_path_date_alone_is_not_capture_timestamp(self):
        raw=f"""
        <table><tbody><tr>
          <td><a href="{TARGET}">{TARGET}</a></td>
          <td><a href="/archive/NoTime">snapshot</a></td>
        </tr></tbody></table>
        """.encode()
        rows=parse_rows(raw,"https://ghostarchive.org/search?term=x")
        out=audit_rows(rows,TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z")
        self.assertEqual(out["identity_archive_row_n"],1)
        self.assertEqual(out["metadata_insufficient_row_n"],1)
        self.assertEqual(out["witness_pass_n"],0)

    def test_timezone_less_timestamp_not_accepted(self):
        self.assertEqual(timestamp_candidates("2023-05-04 12:00:00"),[])
        self.assertEqual(timestamp_candidates("2023-05-04"),[])

    def test_wrong_original_identity_cannot_pass(self):
        raw=b"""
        <table><tbody><tr>
          <td><a href="https://example.com/wrong">wrong</a></td>
          <td><a href="/archive/AbCd1">snapshot</a></td>
          <td>2023-05-04 12:00:00 UTC</td>
        </tr></tbody></table>
        """
        rows=parse_rows(raw,"https://ghostarchive.org/search?term=x")
        out=audit_rows(rows,TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z")
        self.assertEqual(out["identity_archive_row_n"],0)
        self.assertEqual(out["witness_pass_n"],0)

    def test_anti_bot_detection(self):
        self.assertEqual(anti_bot_marker(b"<html>Verify you are human</html>"),"verify you are human")
        self.assertIsNone(anti_bot_marker(b"<html>ordinary search page</html>"))

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,1,1,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,1,0,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=classify(0,0,1,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_metadata_insufficient"])
        c4,n4=classify(0,0,0,p)
        self.assertEqual(c4,"STOP_DATA_COVERAGE")
        self.assertEqual(n4,p["reasonable_subroutes"]["if_clean_zero"])


if __name__=="__main__":
    unittest.main()
