from __future__ import annotations

import os
import re
from collections.abc import Mapping

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_MARKERS = ("GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY")


class CandidateHeadError(RuntimeError):
    pass


def _clean(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _github_candidate_environment(env: Mapping[str, str]) -> bool:
    return bool(_clean(env, "GITHUB_SHA") or any(_clean(env, key) for key in _GITHUB_MARKERS))


def resolve_candidate_exact_head(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    explicit = _clean(env, "FOOTBALL3_CANDIDATE_EXACT_HEAD")
    github_sha = _clean(env, "GITHUB_SHA")
    github_candidate = _github_candidate_environment(env)

    if explicit:
        if github_candidate and _SHA40.fullmatch(explicit) is None:
            raise CandidateHeadError("FOOTBALL3_CANDIDATE_EXACT_HEAD_INVALID")
        return explicit

    if github_sha:
        if _SHA40.fullmatch(github_sha) is None:
            raise CandidateHeadError("GITHUB_SHA_INVALID")
        return github_sha

    if github_candidate:
        raise CandidateHeadError("CANDIDATE_EXACT_HEAD_MISSING")
    return "LOCAL"
