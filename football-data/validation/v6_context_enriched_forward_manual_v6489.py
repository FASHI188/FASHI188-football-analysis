#!/usr/bin/env python3
"""V6.48.9 manual-web context wrapper for the V6.48.6 decision-freeze ledger.

This wrapper deliberately does not scrape any source. It only consumes context evidence that was
manually/live verified and committed under evidence/match_context_live/manual_web. The underlying
V6.48.6 no-backfill rule, market<=context decision-freeze rule, hash-chain ledger and formal_weight=0
semantics remain unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_context_enriched_forward_v6486 as base


def main() -> int:
    base.EVIDENCE = ROOT / "evidence" / "match_context_live" / "manual_web"
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
