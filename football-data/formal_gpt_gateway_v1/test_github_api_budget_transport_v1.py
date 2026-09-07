#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

import build_durable_candidate_inventory_v1 as inventory_builder
import durable_state_selector_v1 as selector
import github_api_budget_transport_v1 as transport


class Response(io.BytesIO):
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None, status: int = 200, url: str = ""):
        super().__init__(payload)
        msg = Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        self.headers = msg
        self.status = status
        self.code = status
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def response_json(obj, headers=None, url=""):
    return Response(json.dumps(obj).encode(), headers, 200, url)


def http_error(url: str, code: int, message: str, headers: dict[str, str] | None = None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    body = io.BytesIO(json.dumps({"message": message, "documentation_url": "https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api"}).encode())
    return urllib.error.HTTPError(url, code, message, msg, body)


class TransportTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("FOOTBALL3_DURABLE_CANDIDATE_INVENTORY", None)
        transport.reset_for_tests()
        transport.install()
        selector._clear_process_cache_for_tests()

    def _artifacts(self, n=93):
        return [{
            "id": 10000 + i,
            "name": f"formal-gpt-runner-state-{30000+i}",
            "created_at": f"2026-09-0{1 + (i % 4)}T00:00:00Z",
            "expired": False,
            "digest": f"sha256:{i:064x}",
            "workflow_run": {
                "id": 30000 + i,
                "head_branch": "football3/formal-gpt-runner-request-carrier-v1",
                "head_sha": f"head-{i}",
            },
        } for i in range(1, n + 1)]

    def test_93_candidates_targeted_gets_no_repository_run_scan(self):
        artifacts = self._artifacts(93)
        calls = []
        def original(req, *args, **kwargs):
            url = req.full_url
            calls.append(url)
            if "/actions/artifacts?" in url:
                page = int(url.split("page=")[-1])
                rows = artifacts if page == 1 else []
                return response_json({"artifacts": rows}, {"X-RateLimit-Remaining": "4900", "X-RateLimit-Reset": str(int(time.time()) + 1000), "X-RateLimit-Resource": "core"}, url)
            if "/actions/runs/" in url and "?" not in url:
                rid = int(url.rsplit("/", 1)[-1])
                return response_json({"id": rid, "head_branch": "football3/formal-gpt-runner-request-carrier-v1", "head_sha": f"head-{rid-30000}", "status": "completed", "conclusion": "success"}, {"X-RateLimit-Remaining": "4800"}, url)
            self.fail(f"unexpected network call {url}")
        with mock.patch.object(transport, "_ORIGINAL_URLOPEN", side_effect=original):
            rows = selector._artifact_pages("FASHI188/FASHI188-football-analysis", "token")
            ids = {int((x.get("workflow_run") or {}).get("id") or 0) for x in rows}
            runs = selector._run_records("FASHI188/FASHI188-football-analysis", "token", ids)
        stats = transport.snapshot()
        self.assertEqual(len(runs), 93)
        self.assertEqual(stats["run_list_pages"], 0)
        self.assertEqual(stats["individual_run_get_count"], 93)
        self.assertGreaterEqual(stats["suppressed_run_list_requests"], 1)
        self.assertTrue(all("/actions/runs?" not in x for x in calls))
        self.assertEqual(stats["rate_limit_remaining_start"], "4900")
        self.assertEqual(stats["rate_limit_remaining_end"], "4800")

    def test_unrelated_repository_run_population_cannot_increase_requests(self):
        artifacts = self._artifacts(2)
        calls = []
        def original(req, *args, **kwargs):
            url=req.full_url; calls.append(url)
            if "/actions/artifacts?" in url:
                page=int(url.split("page=")[-1]); return response_json({"artifacts": artifacts if page==1 else []}, url=url)
            if "/actions/runs/" in url and "?" not in url:
                rid=int(url.rsplit("/",1)[-1]); return response_json({"id":rid,"head_branch":"football3/formal-gpt-runner-request-carrier-v1","head_sha":"h","status":"completed","conclusion":"success"},url=url)
            self.fail(url)
        with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=original):
            rows=selector._artifact_pages("FASHI188/FASHI188-football-analysis","token")
            ids={int(x["workflow_run"]["id"]) for x in rows}; selector._run_records("FASHI188/FASHI188-football-analysis","token",ids)
        self.assertEqual(transport.snapshot()["individual_run_get_count"],2)
        self.assertTrue(all("/actions/runs?" not in x for x in calls))

    def test_concurrent_json_and_artifact_requests_coalesce(self):
        run_url="https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/runs/77"
        zip_url="https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/artifacts/88/zip"
        counts={"run":0,"zip":0}; lock=threading.Lock()
        def original(req,*args,**kwargs):
            with lock:
                if req.full_url==run_url:
                    counts["run"]+=1; return response_json({"id":77,"head_branch":"football3/formal-gpt-runner-request-carrier-v1","head_sha":"h","status":"completed","conclusion":"success"},url=run_url)
                if req.full_url==zip_url:
                    counts["zip"]+=1; return Response(b"zip",{},200,zip_url)
            self.fail(req.full_url)
        out=[]
        with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=original):
            ts=[threading.Thread(target=lambda: out.append(urllib.request.urlopen(urllib.request.Request(run_url)).read())) for _ in range(8)]
            for t in ts:t.start()
            for t in ts:t.join()
            ts=[threading.Thread(target=lambda: out.append(urllib.request.urlopen(urllib.request.Request(zip_url)).read())) for _ in range(8)]
            for t in ts:t.start()
            for t in ts:t.join()
        self.assertEqual(counts,{"run":1,"zip":1})
        self.assertGreaterEqual(transport.snapshot()["cache_hits"],14)

    def test_primary_exhausted_beyond_cap_fails_once(self):
        url="https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/runs/1"; calls=0
        def original(req,*args,**kwargs):
            nonlocal calls; calls+=1
            raise http_error(url,403,"API rate limit exceeded for installation.",{"X-RateLimit-Remaining":"0","X-RateLimit-Reset":"9999999999","X-RateLimit-Resource":"core"})
        with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=original), mock.patch.object(transport.time,"sleep") as sleep:
            with self.assertRaises(transport.GitHubApiBudgetError) as ctx:
                urllib.request.urlopen(urllib.request.Request(url))
        self.assertIn("RATE_LIMIT_EXHAUSTED",str(ctx.exception)); self.assertEqual(calls,1); sleep.assert_not_called()

    def test_secondary_403_and_429_bounded_recovery(self):
        for code,msg in ((403,"secondary rate limit"),(429,"Too many requests")):
            transport.reset_for_tests(); url=f"https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/runs/{code}"; calls=0
            def original(req,*args,_code=code,_msg=msg,**kwargs):
                nonlocal calls; calls+=1
                if calls==1: raise http_error(url,_code,_msg,{"Retry-After":"0","X-RateLimit-Remaining":"100"})
                return response_json({"id":code},url=url)
            with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=original), mock.patch.object(transport.time,"sleep"):
                self.assertTrue(urllib.request.urlopen(urllib.request.Request(url)).read())
            self.assertEqual(calls,2)

    def test_permission_403_immediate_fail_closed(self):
        url="https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/runs/4"; calls=0
        def original(req,*args,**kwargs):
            nonlocal calls; calls+=1
            raise http_error(url,403,"Resource not accessible by integration",{"X-RateLimit-Remaining":"4999"})
        with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=original), mock.patch.object(transport.time,"sleep") as sleep:
            with self.assertRaises(transport.GitHubApiBudgetError) as ctx: urllib.request.urlopen(urllib.request.Request(url))
        self.assertIn("PERMISSION_403",str(ctx.exception)); self.assertEqual(calls,1); sleep.assert_not_called()

    def test_inventory_sha_and_order_are_deterministic(self):
        artifacts=self._artifacts(3)
        runs=[{"id":x["workflow_run"]["id"],"head_branch":"football3/formal-gpt-runner-request-carrier-v1","head_sha":x["workflow_run"]["head_sha"],"status":"completed","conclusion":"success"} for x in artifacts]
        a={"schema_version":transport.INVENTORY_SCHEMA,"repository":"o/r","artifacts":sorted(artifacts,key=lambda x:(x["created_at"],x["id"]),reverse=True),"runs":sorted(runs,key=lambda x:x["id"])}
        b={"schema_version":transport.INVENTORY_SCHEMA,"repository":"o/r","artifacts":sorted(list(reversed(artifacts)),key=lambda x:(x["created_at"],x["id"]),reverse=True),"runs":sorted(list(reversed(runs)),key=lambda x:x["id"])}
        self.assertEqual(a,b)
        self.assertEqual(hashlib.sha256(inventory_builder.canon(a)).hexdigest(),hashlib.sha256(inventory_builder.canon(b)).hexdigest())

    def test_seven_domains_can_share_one_inventory_without_metadata_network(self):
        artifacts=self._artifacts(3)
        runs=[{"id":x["workflow_run"]["id"],"head_branch":"football3/formal-gpt-runner-request-carrier-v1","head_sha":x["workflow_run"]["head_sha"],"status":"completed","conclusion":"success"} for x in artifacts]
        core={"schema_version":transport.INVENTORY_SCHEMA,"repository":"FASHI188/FASHI188-football-analysis","artifacts":sorted(artifacts,key=lambda x:(x["created_at"],x["id"]),reverse=True),"runs":sorted(runs,key=lambda x:x["id"])}
        sha=hashlib.sha256(inventory_builder.canon(core)).hexdigest(); inv={**core,"inventory_sha":sha,"candidate_artifact_count":3,"candidate_run_count":3,"api_stats":{}}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"inventory.json"; p.write_bytes(inventory_builder.canon(inv)); os.environ["FOOTBALL3_DURABLE_CANDIDATE_INVENTORY"]=str(p)
            transport.reset_for_tests()
            with mock.patch.object(transport,"_ORIGINAL_URLOPEN",side_effect=AssertionError("metadata network forbidden")):
                for _ in range(7):
                    a=json.load(urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/artifacts?per_page=100&page=1")))
                    r=json.load(urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/FASHI188/FASHI188-football-analysis/actions/runs?per_page=100&page=1")))
                    self.assertEqual(len(a["artifacts"]),3); self.assertEqual(len(r["workflow_runs"]),3)
            stats=transport.snapshot(); self.assertTrue(stats["inventory_mode"]); self.assertEqual(stats["inventory_sha"],sha); self.assertEqual(stats["network_request_count"],0); self.assertEqual(stats["run_list_pages"],0)

    def test_hard_request_budget(self):
        transport._STATS["api_budget"]=1
        transport._note_network("run_get")
        with self.assertRaises(transport.GitHubApiBudgetError): transport._note_network("run_get")


if __name__ == "__main__":
    unittest.main(verbosity=2)
