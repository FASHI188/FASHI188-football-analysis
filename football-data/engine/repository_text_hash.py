#!/usr/bin/env python3
"""Cross-platform SHA256 for Git-tracked repository text files.

Git may materialize the same tracked text with LF or CRLF line endings depending
on checkout settings. Governance bindings that identify repository text must not
change solely because of that checkout conversion, so all line endings are
canonicalized to LF before hashing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def repository_text_sha256(path: Path) -> str:
    """Return SHA256 after canonicalizing CRLF/CR repository newlines to LF."""
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()
