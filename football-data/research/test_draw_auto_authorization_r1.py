#!/usr/bin/env python3
from __future__ import annotations

import unittest

from validate_draw_auto_authorization_r1 import canonical_sha, validate_payload


class AuthorizationGuardTests(unittest.TestCase):
    def objects(self):
        spec = {"budget": {"maximum_candidates": 200, "maximum_cumulative_seconds": 21600}}
        expected = {"path": "engine.py", "git_blob_sha": "a" * 40}
        identity = {"files": {"engine": expected}, "authorization_required_bindings": ["engine"]}
        authorization = {
            "schema_version": "DRAW-AUTO-RESEARCH-AUTHORIZATION-R1.0",
            "status": "AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH",
            "user_authorization_record": "rec0WJJzXiuDvAqSb",
            "data_status": "VIEWED_DEVELOPMENT_DATA",
            "formal_weight": 0,
            "frozen_code_head": "b" * 40,
            "maximum_candidates": 200,
            "maximum_cumulative_seconds": 21600,
            "spec_canonical_sha256": canonical_sha(spec),
            "identity_canonical_sha256": canonical_sha(identity),
            "identity_git_blob_sha": "c" * 40,
            "bindings": {"engine": expected},
        }
        return spec, identity, authorization

    def test_complete_binding_passes(self):
        spec, identity, authorization = self.objects()
        result = validate_payload(authorization, spec, identity, identity_blob_sha="c" * 40)
        self.assertEqual(result["status"], "PASS_AUTHORIZATION_BINDINGS_ZERO_LABEL")
        self.assertEqual(result["labels_parsed"], 0)

    def test_tampered_or_missing_binding_fails_closed(self):
        spec, identity, authorization = self.objects()
        authorization["bindings"]["engine"] = {"path": "engine.py", "git_blob_sha": "d" * 40}
        with self.assertRaises(ValueError):
            validate_payload(authorization, spec, identity, identity_blob_sha="c" * 40)
        authorization["bindings"] = {}
        with self.assertRaises(ValueError):
            validate_payload(authorization, spec, identity, identity_blob_sha="c" * 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
