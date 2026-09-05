#!/usr/bin/env python3
from __future__ import annotations

# Compatibility shim for the in-flight PR. The governed implementation lives in
# formal_frozen_xg_identity_adjudication_v1 and performs label-free schedule
# identity reconciliation before applying the single authoritative result-semantic
# adjudication. No model, source file, score row, or sample is rewritten here.
from formal_frozen_xg_identity_adjudication_v1 import (  # noqa: F401
    SCHEMA,
    adjudication_entries,
    install,
)
