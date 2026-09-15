#!/usr/bin/env python3
from __future__ import annotations
import copy,io,json,unittest,zipfile
from pathlib import Path
from nova_mhsendur_understat_header_audit_v1 import AuditError,audit,git_blob_sha1,validate_lock
HERE=Path(__file__).resolve().parent; LOCK=json.loads((HERE/"nova_mhsendur_understat_header_lock_v1.json").read_text(encoding="utf-8"))
def make_zip(header:list[str],rows:list[list[str]]|None=None)->bytes:
    rows=rows or []; bio=io.BytesIO()
    with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as zf:
        content=",".join(header)+"\n"
        for row in rows: content += ",".join(row)+"\n"
        zf.writestr("football_data_csv/EPL_2022_Arsenal.csv",content)
    return bio.getvalue()
class HeaderAuditTests(unittest.TestCase):
    def test_lock_contract(self):
        validate_lock(LOCK); self.assertFalse(LOCK["permission"]["production_eligible"]); self.assertFalse(LOCK["governance"]["candidate_confirmation_allowed"])
    def test_qualified_header_reads_no_rows(self):
        lock=copy.deepcopy(LOCK); raw=make_zip(["h_a","xG","ppda","ppda_allowed","deep","deep_allowed","date","result"],[["h","1.2","1","2","3","4","2022-01-01","w"]]); lock["source"]["archive_blob_sha1"]=git_blob_sha1(raw); receipt=audit(lock,raw); self.assertEqual(receipt["status"],"HEADER_SCHEMA_QUALIFIED"); self.assertEqual(receipt["data_rows_read"],0); self.assertEqual(receipt["result_values_read"],0); self.assertEqual(receipt["header_schema_qualified_member_count"],1)
    def test_missing_role_is_not_qualified(self):
        lock=copy.deepcopy(LOCK); raw=make_zip(["h_a","xG","ppda","ppda_allowed","deep","date"]); lock["source"]["archive_blob_sha1"]=git_blob_sha1(raw); receipt=audit(lock,raw); self.assertEqual(receipt["status"],"HEADER_SCHEMA_NOT_QUALIFIED"); self.assertIn("deep_allowed",receipt["members"][0]["missing_locked_roles"])

    def test_cp1252_header_is_supported_without_reading_rows(self):
        lock=copy.deepcopy(LOCK)
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as zf:
            header="h_a,xG,ppda,ppda_allowed,deep,deep_allowed,date,result,club·name\n".encode("cp1252")
            zf.writestr("football_data_csv/EPL_2022_Arsenal.csv",header+b"h,1.2,1,2,3,4,2022-01-01,w,Arsenal\n")
        raw=bio.getvalue(); lock["source"]["archive_blob_sha1"]=git_blob_sha1(raw)
        receipt=audit(lock,raw)
        self.assertEqual(receipt["status"],"HEADER_SCHEMA_QUALIFIED")
        self.assertEqual(receipt["members"][0]["header_encoding"],"cp1252")
        self.assertEqual(receipt["data_rows_read"],0)
        self.assertEqual(receipt["result_values_read"],0)


    def test_packaging_metadata_is_ignored(self):
        lock=copy.deepcopy(LOCK)
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("__MACOSX/football_data_csv/._EPL_2022_Arsenal.csv", b"not,a,real,csv\n")
            zf.writestr("football_data_csv/EPL_2022_Arsenal.csv", "h_a,ppda,ppda_allowed,deep,deep_allowed,date\n".encode())
        raw=bio.getvalue(); lock["source"]["archive_blob_sha1"]=git_blob_sha1(raw)
        receipt=audit(lock,raw)
        self.assertEqual(receipt["status"],"HEADER_SCHEMA_QUALIFIED")
        self.assertEqual(receipt["ignored_packaging_member_count"],1)
        self.assertEqual(receipt["csv_member_count"],1)

    def test_one_real_bad_member_makes_source_not_qualified(self):
        lock=copy.deepcopy(LOCK)
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("football_data_csv/EPL_2022_Arsenal.csv", "h_a,ppda,ppda_allowed,deep,deep_allowed,date\n".encode())
            zf.writestr("football_data_csv/EPL_2022_Chelsea.csv", "h_a,ppda,ppda_allowed,deep,date\n".encode())
        raw=bio.getvalue(); lock["source"]["archive_blob_sha1"]=git_blob_sha1(raw)
        receipt=audit(lock,raw)
        self.assertEqual(receipt["status"],"HEADER_SCHEMA_NOT_QUALIFIED")
        self.assertEqual(receipt["header_schema_not_qualified_member_count"],1)

    def test_blob_drift_fails(self):
        raw=make_zip(["h_a","ppda","ppda_allowed","deep","deep_allowed","date"])
        with self.assertRaises(AuditError): audit(LOCK,raw)
if __name__=="__main__": unittest.main()
