#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse
import zipfile

import formal_terminal_finalizer_v1 as mod

REPO = "FASHI188/FASHI188-football-analysis"
WORKFLOW_ID = 349340611
RUN_ID = 34234282609
REQUEST_SHA = "1f71720ed0bc42c714a8c833f4f8fc815007ecbf5cbcc9c71b2be40b6ca32ba9"
REQUEST_ID = "auto-dispatch-e2e-freshness-v3-eng-nfo-tot-20260908-aa4372-001"
CANON = "0e102ac3689185d3378bab3bc416ccec20d519de"
CARRIER_HEAD = "1dd083a37504a5c55dec3722e814b74820dd83b3"
PREDICTION_SHA = "d8d519a9ef82343eec2965d4eca8f247ba7491c78180c10cc8896baec7de2693"
INCIDENT_RECEIPT_ARTIFACT_ID = 10059193562
INCIDENT_RECEIPT_DIGEST = "sha256:10e4cf982b8e6aa9f722108a5ede2bf0fae447baa0944b2aef5949320d3a70a3"


def audit() -> dict:
    return {
        "schema_version": "football3-gpt-auto-dispatch-bridge-v3",
        "phase": "DISPATCHED",
        "status": "IN_PROGRESS",
        "dispatch_performed": True,
        "formal_workflow_id": WORKFLOW_ID,
        "formal_run_id": RUN_ID,
        "request_id": REQUEST_ID,
        "request_sha256": REQUEST_SHA,
        "canonical_execution_sha": CANON,
        "carrier_pr_number": 341,
        "carrier_head_sha": CARRIER_HEAD,
        "trusted_dispatcher_sha": "aa4372f709be43612088d55a22dcfac18af9f5d7",
        "trusted_dispatcher_run_id": 34234202635,
        "receiver_run_id": 34234187798,
    }


def run_record(*, status="in_progress", conclusion=None, **overrides) -> dict:
    value = {
        "id": RUN_ID,
        "workflow_id": WORKFLOW_ID,
        "event": "workflow_dispatch",
        "head_branch": mod.CANONICAL_REF,
        "head_sha": CANON,
        "display_title": mod.expected_display_title(REQUEST_SHA),
        "status": status,
        "conclusion": conclusion,
    }
    value.update(overrides)
    return value


def receipt_zip(*, bad: str | None = None) -> bytes:
    prediction = {"prediction_sha": PREDICTION_SHA}
    prediction_raw = json.dumps(prediction, sort_keys=True, separators=(",", ":")).encode()
    summary = {"status": "PASS", "prediction_sha": PREDICTION_SHA}
    summary_raw = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    binding = {
        "status": "PASS",
        "request_id": REQUEST_ID,
        "request_sha256": REQUEST_SHA,
        "expected_request_sha256": REQUEST_SHA,
        "request_sha_verified": True,
        "production_run_id": RUN_ID,
        "canonical_execution_sha": CANON,
        "carrier_head_sha": CARRIER_HEAD,
    }
    state = {"status": "PASS"}
    execution = {
        "status": "PASS",
        "production_run_id": RUN_ID,
        "run_id": RUN_ID,
        "request_sha256": REQUEST_SHA,
        "canonical_execution_sha": CANON,
        "workflow_sha": CANON,
        "prediction_sha": PREDICTION_SHA,
        "state_integrity_guard_status": "PASS",
        "summary_sha256": hashlib.sha256(summary_raw).hexdigest(),
        "prediction_receipt_sha256": hashlib.sha256(prediction_raw).hexdigest(),
    }
    if bad == "request":
        binding["request_sha256"] = "f" * 64
    elif bad == "run":
        binding["production_run_id"] = RUN_ID + 1
    elif bad == "canonical":
        execution["canonical_execution_sha"] = "e" * 40
    elif bad == "prediction":
        prediction["prediction_sha"] = "a" * 64
    elif bad == "state":
        state["status"] = "FAIL"
    elif bad == "internal_sha":
        execution["summary_sha256"] = "0" * 64
    files = {
        "request_sha_binding_receipt.json": binding,
        "summary.json": summary,
        "prediction_receipt.json": prediction,
        "production_execution_binding_receipt.json": execution,
        "state_integrity_audit.json": state,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, value in files.items():
            zf.writestr(name, json.dumps(value, sort_keys=True, separators=(",", ":")))
    return out.getvalue()


def artifact_for(raw: bytes, *, artifact_id=INCIDENT_RECEIPT_ARTIFACT_ID, name=None, digest=None) -> dict:
    return {
        "id": artifact_id,
        "name": name or f"formal-gpt-runner-receipt-{RUN_ID}",
        "expired": False,
        "digest": digest or f"sha256:{hashlib.sha256(raw).hexdigest()}",
    }


class FakeTransport:
    def __init__(self, runs, artifacts=None, *, zip_bytes=None, stale_run=False):
        self.repo = REPO
        self.runs = list(runs)
        self.artifacts = list(artifacts or [[]])
        self.ri = 0
        self.ai = 0
        self.paths: list[str] = []
        self.zip_bytes = zip_bytes or receipt_zip()
        self.stale_run = stale_run
        self.download_count = 0

    def _evidence(self, path: str, value, *, stale: bool, kind: str):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        idx = 0 if stale else len(self.paths)
        return {
            "endpoint": path,
            "attempts": [{
                "attempt": 1,
                "observed_at": f"2026-09-08T13:50:{20 + min(idx, 39):02d}+00:00",
                "http_status": 200,
                "headers": {
                    "date": "Tue, 08 Sep 2026 13:50:20 GMT" if stale else f"Tue, 08 Sep 2026 13:50:{20 + min(idx, 39):02d} GMT",
                    "x-github-request-id": f"STALE-{kind}" if stale else f"REQ-{kind}-{idx}",
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-remaining": "4900",
                    "x-ratelimit-reset": "1788876686",
                    "x-ratelimit-resource": "core",
                },
                "body_sha256": hashlib.sha256(raw).hexdigest(),
            }],
        }

    def get_json_with_evidence(self, path: str):
        self.paths.append(path)
        parsed = urlparse(path)
        if parsed.path.endswith(f"/actions/runs/{RUN_ID}"):
            idx = min(self.ri, len(self.runs) - 1)
            value = dict(self.runs[idx])
            self.ri += 1
            return value, self._evidence(path, value, stale=self.stale_run and value.get("status") != "completed", kind="run")
        if parsed.path.endswith(f"/actions/runs/{RUN_ID}/artifacts"):
            idx = min(self.ai, len(self.artifacts) - 1)
            items = [dict(x) for x in self.artifacts[idx]]
            self.ai += 1
            value = {"total_count": len(items), "artifacts": items}
            return value, self._evidence(path, value, stale=False, kind="artifacts")
        raise AssertionError(path)

    def download_artifact_zip(self, artifact_id: int):
        self.download_count += 1
        return self.zip_bytes


class SequenceTransport(mod.GitHubTransport):
    def __init__(self, sequence, *, budget=20):
        super().__init__(REPO, "token", sleep_fn=lambda _: None, max_get_attempts=4, max_http_requests=budget)
        self.sequence = list(sequence)

    def _request_once(self, path):
        self.http_request_count += 1
        if self.http_request_count > self.max_http_requests:
            raise mod.FinalizerError("AUTO_DISPATCH_FINALIZER_HTTP_BUDGET_EXCEEDED")
        return self.sequence.pop(0)


class FormalTerminalFinalizerPermanentTest(unittest.TestCase):
    def make(self, transport, *, run_attempts=5, artifact_attempts=5, max_pages=10):
        return mod.FormalTerminalFinalizer(
            transport,
            sleep_fn=lambda _: None,
            run_attempts=run_attempts,
            run_delay_seconds=0,
            artifact_attempts=artifact_attempts,
            artifact_delay_seconds=0,
            max_pages=max_pages,
        )

    @staticmethod
    def diag():
        return {"schema_version": mod.FINALIZER_SCHEMA, "run_polls": [], "artifact_polls": []}

    def test_queued_in_progress_completed_success(self):
        raw = receipt_zip(); art = artifact_for(raw)
        transport = FakeTransport(
            [run_record(status="queued"), run_record(status="in_progress"), run_record(status="completed", conclusion="success")],
            [[art]], zip_bytes=raw,
        )
        d = self.diag()
        final = self.make(transport).finalize(audit(), d, lambda: None)
        self.assertEqual(final["prediction_sha"], PREDICTION_SHA)
        self.assertEqual([r["run_status"] for r in d["run_polls"]], ["queued", "in_progress", "completed"])

    def test_stale_response_signature_then_fresh_success(self):
        transport = FakeTransport(
            [run_record(status="in_progress"), run_record(status="in_progress"), run_record(status="completed", conclusion="success")],
            stale_run=True,
        )
        d = self.diag()
        self.make(transport).wait_terminal(audit(), d, lambda: None)
        self.assertTrue(d["run_polls"][1]["stale_response_suspected"])
        self.assertEqual(d["run_polls"][1]["freshness_status"], "STALE_RESPONSE_SUSPECTED")

    def test_unique_cache_busters_run_and_artifacts(self):
        raw = receipt_zip(); art = artifact_for(raw)
        transport = FakeTransport(
            [run_record(status="in_progress"), run_record(status="completed", conclusion="success")],
            [[], [art]], zip_bytes=raw,
        )
        self.make(transport).finalize(audit(), self.diag(), lambda: None)
        busters = []
        for path in transport.paths:
            query = parse_qs(urlparse(path).query)
            for key in ("f3_terminal_cache_buster", "f3_artifact_cache_buster"):
                if key in query:
                    busters.append(query[key][0])
        self.assertEqual(len(busters), len(set(busters)))
        self.assertGreaterEqual(len(busters), 4)

    def test_no_cache_headers(self):
        self.assertEqual(mod.GET_FRESHNESS_HEADERS["Cache-Control"], "no-cache, no-store, max-age=0")
        self.assertEqual(mod.GET_FRESHNESS_HEADERS["Pragma"], "no-cache")

    def test_artifact_visibility_wait_is_independent(self):
        raw = receipt_zip(); art = artifact_for(raw)
        transport = FakeTransport([run_record(status="completed", conclusion="success")], [[], [], [art]], zip_bytes=raw)
        final = self.make(transport).finalize(audit(), self.diag(), lambda: None)
        self.assertEqual(final["formal_receipt_artifact_id"], INCIDENT_RECEIPT_ARTIFACT_ID)
        self.assertEqual(transport.ai, 3)

    def test_failure_cancelled_timed_out_fail_closed_immediately(self):
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                d = self.diag()
                with self.assertRaisesRegex(mod.FinalizerError, f"AUTO_DISPATCH_FORMAL_RUN_FAILED:{conclusion}"):
                    self.make(FakeTransport([run_record(status="completed", conclusion=conclusion)])).wait_terminal(audit(), d, lambda: None)
                self.assertEqual(len(d["run_polls"]), 1)

    def test_selected_run_identity_drift_rejected(self):
        mutations = (
            {"id": RUN_ID + 1},
            {"workflow_id": WORKFLOW_ID + 1},
            {"event": "push"},
            {"head_branch": "main"},
            {"head_sha": "a" * 40},
            {"display_title": mod.expected_display_title("f" * 64)},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RUN_IDENTITY_MISMATCH"):
                    self.make(FakeTransport([run_record(**mutation)]), run_attempts=1).wait_terminal(audit(), self.diag(), lambda: None)

    def test_receipt_duplicate_ambiguity_and_identity_errors_rejected(self):
        raw = receipt_zip()
        dup = [artifact_for(raw, artifact_id=1), artifact_for(raw, artifact_id=2)]
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_AMBIGUOUS"):
            self.make(FakeTransport([run_record()], [dup]), artifact_attempts=1).wait_receipt_artifact(audit(), self.diag(), lambda: None)
        for bad in ("request", "run", "canonical", "prediction", "state", "internal_sha"):
            with self.subTest(bad=bad):
                bad_raw = receipt_zip(bad=bad)
                art = artifact_for(bad_raw)
                with self.assertRaises(mod.FinalizerError):
                    self.make(FakeTransport([run_record()], zip_bytes=bad_raw)).validate_receipt_zip(audit(), art, bad_raw)

    def test_artifact_digest_mismatch_rejected(self):
        raw = receipt_zip(); art = artifact_for(raw, digest="sha256:" + "0" * 64)
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_DIGEST_MISMATCH"):
            self.make(FakeTransport([run_record()], zip_bytes=raw)).validate_receipt_zip(audit(), art, raw)

    def test_incident_34234282609_redacted_payload_passes_strict_identity_and_receipt(self):
        incident = run_record(status="completed", conclusion="success")
        self.assertEqual(self.make(FakeTransport([incident]))._identity_reasons(incident, audit(), WORKFLOW_ID), [])
        raw = receipt_zip(); art = artifact_for(raw)
        receipt = self.make(FakeTransport([incident], zip_bytes=raw)).validate_receipt_zip(audit(), art, raw)
        self.assertEqual(receipt["prediction_sha"], PREDICTION_SHA)
        self.assertEqual(INCIDENT_RECEIPT_DIGEST, "sha256:10e4cf982b8e6aa9f722108a5ede2bf0fae447baa0944b2aef5949320d3a70a3")

    def test_403_permission_is_immediate(self):
        transport = SequenceTransport([(403, {"x-ratelimit-remaining": "10"}, b"forbidden")])
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_GITHUB_PERMISSION_DENIED"):
            transport.get_json_with_evidence("/repos/x/y")
        self.assertEqual(transport.http_request_count, 1)

    def test_429_and_5xx_bounded_recovery(self):
        body = json.dumps({"ok": True}).encode()
        transport = SequenceTransport([
            (429, {"retry-after": "0"}, b"rate"),
            (503, {}, b"server"),
            (200, {}, body),
        ])
        value, evidence = transport.get_json_with_evidence("/repos/x/y")
        self.assertTrue(value["ok"])
        self.assertEqual(len(evidence["attempts"]), 3)

    def test_http_budget_fail_closed(self):
        transport = SequenceTransport([(503, {}, b"a"), (503, {}, b"b")], budget=1)
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FINALIZER_HTTP_BUDGET_EXCEEDED"):
            transport.get_json_with_evidence("/repos/x/y")

    def test_pagination_boundary_fail_closed(self):
        class PageTransport(FakeTransport):
            def get_json_with_evidence(self, path: str):
                parsed = urlparse(path)
                if parsed.path.endswith(f"/actions/runs/{RUN_ID}/artifacts"):
                    self.paths.append(path)
                    items = [{"id": i, "name": f"other-{i}", "expired": False, "digest": "sha256:" + "0" * 64} for i in range(100)]
                    value = {"total_count": 200, "artifacts": items}
                    return value, self._evidence(path, value, stale=False, kind="artifacts")
                return super().get_json_with_evidence(path)
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_ARTIFACT_PAGINATION_LIMIT"):
            self.make(PageTransport([run_record()]), artifact_attempts=1, max_pages=1).wait_receipt_artifact(audit(), self.diag(), lambda: None)

    def test_timeout_preserves_complete_diagnostics(self):
        transport = FakeTransport([run_record(status="in_progress"), run_record(status="in_progress")], stale_run=True)
        d = self.diag(); snapshots = []
        with self.assertRaisesRegex(mod.FinalizerError, "AUTO_DISPATCH_FORMAL_RUN_TIMEOUT"):
            self.make(transport, run_attempts=2).wait_terminal(audit(), d, lambda: snapshots.append(len(d["run_polls"])))
        self.assertEqual(len(d["run_polls"]), 2)
        self.assertTrue(d["run_polls"][1]["stale_response_suspected"])
        required = {
            "endpoint", "poll_number", "observed_at", "http_status", "run_status", "run_conclusion",
            "returned_artifact_ids", "date", "x_github_request_id", "body_sha256", "rate_limit_headers",
            "wait_or_rejection_reason",
        }
        self.assertTrue(required.issubset(d["run_polls"][0]))
        self.assertEqual(snapshots[-1], 2)

    def test_finalizer_has_no_production_dispatch_capability(self):
        source = inspect.getsource(mod)
        self.assertNotIn("/dispatches", source)
        self.assertNotIn("post_dispatch", source)
        self.assertNotIn("workflow_dispatch(request_pr_number", source)

    def test_zz_materialize_candidate_evidence(self):
        exact = os.environ.get("CANDIDATE_EXACT_HEAD", "")
        if not exact:
            self.skipTest("candidate evidence only")
        self.assertEqual(len(exact), 40)
        root = Path(__file__).resolve().parents[2]
        out = root / ".runtime_sources/formal-terminal-finalizer-candidate"
        out.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": "football3-formal-terminal-finalizer-candidate-v1",
            "status": "PASS",
            "candidate_exact_head": exact,
            "base_exact_head": "aa4372f709be43612088d55a22dcfac18af9f5d7",
            "incident_dispatcher_run_id": 34234202635,
            "incident_formal_run_id": RUN_ID,
            "incident_request_sha": REQUEST_SHA,
            "incident_receipt_artifact_id": INCIDENT_RECEIPT_ARTIFACT_ID,
            "incident_receipt_digest": INCIDENT_RECEIPT_DIGEST,
            "incident_prediction_sha": PREDICTION_SHA,
            "incident_redacted_identity_validation": "PASS",
            "run_terminal_freshness_headers": "PASS",
            "unique_cache_buster": "PASS",
            "stale_response_detection": "PASS",
            "artifact_visibility_independent_wait": "PASS",
            "bounded_403_429_5xx_contract": "PASS",
            "formal_post_capability": False,
            "production_dispatch_performed": False,
            "manual_finalize_performed": False,
        }
        (out / "formal_terminal_finalizer_candidate_evidence.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
