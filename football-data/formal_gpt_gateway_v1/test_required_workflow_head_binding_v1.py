from __future__ import annotations

import pathlib
import unittest


def resolve_head_evidence(*, event_name: str, github_sha: str, pr_candidate_head: str, push_head: str, validated_head_sha: str, base_sha: str) -> dict[str, str | None]:
    if event_name == "pull_request":
        expected_head = pr_candidate_head
        event_merge_sha: str | None = github_sha
    else:
        expected_head = push_head
        event_merge_sha = None
    if not expected_head:
        raise AssertionError("expected head must be non-empty")
    if validated_head_sha != expected_head:
        raise AssertionError((validated_head_sha, expected_head))
    return {
        "exact_head": validated_head_sha,
        "candidate_head": expected_head,
        "base_sha": base_sha,
        "event_name": event_name,
        "event_merge_sha": event_merge_sha,
    }


class RequiredWorkflowHeadBindingTests(unittest.TestCase):
    def test_pr_merge_sha_cannot_replace_candidate_exact_head(self) -> None:
        candidate = "c" * 40
        temporary_merge = "m" * 40
        evidence = resolve_head_evidence(
            event_name="pull_request",
            github_sha=temporary_merge,
            pr_candidate_head=candidate,
            push_head=temporary_merge,
            validated_head_sha=candidate,
            base_sha="b" * 40,
        )
        self.assertEqual(evidence["exact_head"], candidate)
        self.assertEqual(evidence["candidate_head"], candidate)
        self.assertEqual(evidence["event_merge_sha"], temporary_merge)
        self.assertNotEqual(evidence["exact_head"], evidence["event_merge_sha"])

    def test_push_binds_validated_checkout_to_github_sha(self) -> None:
        pushed = "p" * 40
        evidence = resolve_head_evidence(
            event_name="push",
            github_sha=pushed,
            pr_candidate_head="",
            push_head=pushed,
            validated_head_sha=pushed,
            base_sha="b" * 40,
        )
        self.assertEqual(evidence["exact_head"], pushed)
        self.assertEqual(evidence["candidate_head"], pushed)
        self.assertIsNone(evidence["event_merge_sha"])

    def test_mismatched_checkout_fails_closed(self) -> None:
        with self.assertRaises(AssertionError):
            resolve_head_evidence(
                event_name="pull_request",
                github_sha="m" * 40,
                pr_candidate_head="c" * 40,
                push_head="m" * 40,
                validated_head_sha="x" * 40,
                base_sha="b" * 40,
            )

    def test_workflow_locks_exact_head_to_validated_checkout(self) -> None:
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        workflow = (repo_root / ".github/workflows/football3-durable-selector-github-api-resilience-required-v1.yml").read_text()
        required = (
            'VALIDATED_HEAD_SHA="$(git rev-parse HEAD)"',
            'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then',
            'EXPECTED_HEAD="$PR_CANDIDATE_HEAD"',
            'EXPECTED_HEAD="$PUSH_HEAD"',
            'test "$VALIDATED_HEAD_SHA" = "$EXPECTED_HEAD"',
            "'exact_head':validated_head_sha",
            "'candidate_head':candidate_head",
            "'base_sha':base_sha",
            "'event_name':event_name",
            "'event_merge_sha':event_merge_sha",
        )
        for needle in required:
            self.assertIn(needle, workflow)
        self.assertNotIn("'exact_head':os.environ['GITHUB_SHA']", workflow)
        self.assertNotIn('BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before || \'7dbb5700c007e40809bb8807f10cf6973e7e9ddd\' }}', workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
