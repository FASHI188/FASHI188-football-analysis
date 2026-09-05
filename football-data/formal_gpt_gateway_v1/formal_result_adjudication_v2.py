#!/usr/bin/env python3
from __future__ import annotations

# Compatibility shim for the in-flight PR. The governed implementation lives in
# formal_frozen_xg_identity_adjudication_v2. It performs label-free team identity
# reconciliation plus a unique bounded schedule-date bridge, then applies the
# explicit authoritative result-semantic adjudication. No model, source score,
# sample, CURRENT, or production pointer is rewritten here.
from formal_frozen_xg_identity_adjudication_v2 import (  # noqa: F401
    SCHEMA,
    adjudication_entries,
    install,
)
