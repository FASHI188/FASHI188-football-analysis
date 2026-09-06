#!/usr/bin/env python3
from __future__ import annotations

"""Governed UEFA Champions League legacy-core entry.

This wrapper installs the audited 2026/27 league-phase identity bridge, then delegates
calculation and validation unchanged to run_formal_prediction_v460. It does not add
UEFA_ChampionsLeague to Fusion V2 scope and does not alter model parameters/weights.
"""

import json
from pathlib import Path

import match_pipeline
import ucl_league_phase_identity_bridge_v1 as ucl_identity

IDENTITY_CONTRACT = ucl_identity.install(match_pipeline)

import run_formal_prediction_v460 as legacy_runner


def main() -> int:
    code = legacy_runner.main()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
