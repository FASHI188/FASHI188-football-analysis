#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import production_base_binding_v1 as binding

FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE = "football3-formal-production-runtime-governance-v1"


class ProductionBaseBindingFailClosedTest(unittest.TestCase):
    HEAD = 'a' * 40
    FORMAL_HEAD = 'b' * 40
    CURRENT_SHA = 'c' * 64

    def base_binding(self) -> dict:
        return {
            'schema_version': binding.SCHEMA,
            'status': 'CHECKOUT_BOUND',
            'run_id': '123',
            'request_id': 'fail-closed-regression',
            'request_sha256': 'd' * 64,
            'request_carrier_ref': None,
            'request_carrier_head': None,
            'canonical_base_ref': binding.CANONICAL_BASE_REF,
            'resolved_live_base_sha': self.HEAD,
            'checkout_head_sha': self.HEAD,
            'resolution_timestamp': '2026-09-12T00:00:00Z',
            'initial_live_ref_query_count': 0,
            'final_live_ref_query_count': 0,
            'total_live_ref_query_count': 0,
            'workflow_identity': 'test',
            'workflow_contract_version': binding.WORKFLOW_CONTRACT_VERSION,
            'workflow_ref': 'test@refs/heads/test',
            'workflow_sha': self.HEAD,
            'runner_code_source': 'CANONICAL_INTEGRATION_EXACT_SHA',
            'request_transport_source': 'COMMITTED_OR_DISPATCH_REQUEST',
            'request_carrier_code_executed': False,
            'repository_run_list_scan_used': False,
        }

    def summary(self, **overrides) -> dict:
        value = {
            'status': 'FAIL_CLOSED',
            'reason': 'HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED',
            'prediction_sha': None,
            'receipt_sha': None,
            'formal_head': self.FORMAL_HEAD,
            'formal_current_sha256': self.CURRENT_SHA,
        }
        value.update(overrides)
        return value

    def args(self, root: pathlib.Path) -> SimpleNamespace:
        return SimpleNamespace(
            binding=str(root / 'binding.json'),
            repo='owner/repo',
            token='unused',
            out_dir=str(root / 'out'),
            stats='',
            prediction_required='true',
            final_timestamp='2026-09-12T00:01:00Z',
        )

    def write_fixture(self, root: pathlib.Path, summary: dict) -> pathlib.Path:
        (root / 'binding.json').write_text(json.dumps(self.base_binding()), encoding='utf-8')
        out = root / 'out'
        out.mkdir()
        (out / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
        return out

    def test_prediction_required_fail_closed_preserves_reason_without_prediction_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            out = self.write_fixture(root, self.summary())
            with self.assertRaisesRegex(
                binding.ProductionBaseBindingError,
                'PRODUCTION_GATEWAY_FAIL_CLOSED:HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED',
            ):
                binding.finalize(self.args(root))
            self.assertFalse((out / 'prediction_receipt.json').exists())
            blocker = json.loads((out / 'production_base_blocker.json').read_text(encoding='utf-8'))
            execution = json.loads((out / 'production_execution_binding_receipt.json').read_text(encoding='utf-8'))
            self.assertEqual(blocker['status'], 'FAIL_CLOSED')
            self.assertEqual(blocker['blocker'], 'HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED')
            self.assertIsNone(blocker['prediction_sha'])
            self.assertIsNone(blocker['receipt_sha'])
            self.assertFalse(blocker['integration_moved_during_run'])
            self.assertEqual(execution['status'], 'FAIL_CLOSED')
            self.assertEqual(execution['reason'], blocker['blocker'])
            self.assertEqual(execution['resolved_live_base_sha'], self.HEAD)
            self.assertEqual(execution['checkout_head_sha'], self.HEAD)
            self.assertEqual(execution['final_live_base_sha'], self.HEAD)
            self.assertIsNone(execution['prediction_sha'])
            self.assertIsNone(execution['receipt_sha'])

    def test_fail_closed_cannot_hide_nonnull_prediction_or_receipt_sha(self) -> None:
        for field in ('prediction_sha', 'receipt_sha'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                out = self.write_fixture(root, self.summary(**{field: 'e' * 64}))
                with self.assertRaisesRegex(binding.ProductionBaseBindingError, 'PRODUCTION_GATEWAY_FAIL_CLOSED_OUTPUT_INVALID'):
                    binding.finalize(self.args(root))
                self.assertFalse((out / 'production_base_blocker.json').exists())
                self.assertFalse((out / 'production_execution_binding_receipt.json').exists())

    def test_pass_summary_does_not_enter_fail_closed_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            out = self.write_fixture(root, self.summary(status='PASS', reason=None, prediction_sha='f' * 64, receipt_sha='1' * 64))
            with self.assertRaises(FileNotFoundError):
                binding.finalize(self.args(root))
            self.assertFalse((out / 'production_base_blocker.json').exists())
            self.assertFalse((out / 'production_execution_binding_receipt.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
