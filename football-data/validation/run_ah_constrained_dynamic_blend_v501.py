#!/usr/bin/env python3
from __future__ import annotations

import platform_core
import validate_ah_constrained_dynamic_blend_v501 as validation

# The base research module imports read_processed_matches but not the shared team-token
# normalizer. Bind the canonical implementation explicitly before executing the replay.
validation.base.normalize_team_token = platform_core.normalize_team_token

if __name__ == "__main__":
    raise SystemExit(validation.main())
