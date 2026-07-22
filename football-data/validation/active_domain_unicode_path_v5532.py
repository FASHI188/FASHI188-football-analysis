#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import unicodedata


def safe(value: object) -> str:
    """Return a deterministic Unicode-safe path token.

    Python's Unicode-aware ``\w`` preserves Korean and other registered team
    identities. A short digest is used only when the value contains no usable
    alphanumeric characters. This function changes filenames only; it never
    changes canonical team identity, markets, weights or probabilities.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    token = re.sub(r"[^\w-]+", "_", raw, flags=re.UNICODE).strip("_")
    if not token:
        token = "id_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    if len(token) > 120:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        token = token[:96].rstrip("_") + "__" + digest
    return token
