#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

import draw_auto_research_artifact_fallback_r1 as fallback
import draw_auto_research_restore_r1 as restore


EXPECTED = {
    "authorization_digest": "a",
    "frozen_code_head": "h",
    "spec_digest": "s",
    "identity_digest": "i",
}


class ArtifactFallbackStateMachineTests(unittest.TestCase):
    def test_confirmed_no_historical_artifact_allows_new(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(fallback, "list_matching_artifacts", return_value=[]):
            result = fallback.restore_latest(
                repository="o/r",
                prefix="p",
                state_dir=pathlib.Path(temp) / "state",
                token="token",
                **EXPECTED,
            )
        self.assertEqual(result["status"], "NO_HISTORICAL_ARTIFACT_CONFIRMED")
        self.assertEqual(result["matching_artifacts"], 0)

    def test_api_query_failure_is_terminal(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(fallback, "list_matching_artifacts", side_effect=OSError("network down")):
            with self.assertRaisesRegex(RuntimeError, "ARTIFACT_API_QUERY_FAILED"):
                fallback.restore_latest(
                    repository="o/r",
                    prefix="p",
                    state_dir=pathlib.Path(temp) / "state",
                    token="token",
                    **EXPECTED,
                )

    def test_matching_artifacts_all_incompatible_are_terminal(self):
        artifacts = [
            {"id": 2, "archive_download_url": "https://example.invalid/2", "created_at": "2026-01-02"},
            {"id": 1, "archive_download_url": "https://example.invalid/1", "created_at": "2026-01-01"},
        ]
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(fallback, "list_matching_artifacts", return_value=artifacts), \
             mock.patch.object(fallback, "download_zip"), \
             mock.patch.object(fallback, "safe_extract"), \
             mock.patch.object(fallback, "restore_artifact", side_effect=restore.ArtifactIncompatibleError("wrong identity")):
            with self.assertRaisesRegex(restore.ArtifactIncompatibleError, "MATCHING_ARTIFACTS_FOUND_BUT_NONE_COMPATIBLE"):
                fallback.restore_latest(
                    repository="o/r",
                    prefix="p",
                    state_dir=pathlib.Path(temp) / "state",
                    token="token",
                    **EXPECTED,
                )

    def test_download_or_extract_failure_is_terminal(self):
        artifacts = [{"id": 7, "archive_download_url": "https://example.invalid/7", "created_at": "2026-01-01"}]
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(fallback, "list_matching_artifacts", return_value=artifacts), \
             mock.patch.object(fallback, "download_zip", side_effect=OSError("download failed")):
            with self.assertRaisesRegex(RuntimeError, "ARTIFACT_DOWNLOAD_OR_EXTRACT_FAILED"):
                fallback.restore_latest(
                    repository="o/r",
                    prefix="p",
                    state_dir=pathlib.Path(temp) / "state",
                    token="token",
                    **EXPECTED,
                )

    def test_integrity_failure_does_not_fall_back_to_new(self):
        artifacts = [{"id": 8, "archive_download_url": "https://example.invalid/8", "created_at": "2026-01-01"}]
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(fallback, "list_matching_artifacts", return_value=artifacts), \
             mock.patch.object(fallback, "download_zip"), \
             mock.patch.object(fallback, "safe_extract"), \
             mock.patch.object(fallback, "restore_artifact", side_effect=restore.ArtifactIntegrityError("tampered")):
            with self.assertRaisesRegex(restore.ArtifactIntegrityError, "tampered"):
                fallback.restore_latest(
                    repository="o/r",
                    prefix="p",
                    state_dir=pathlib.Path(temp) / "state",
                    token="token",
                    **EXPECTED,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
