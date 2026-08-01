#!/usr/bin/env python3
from __future__ import annotations

from validate_draw_auto_research_preflight_impl_r1 import *  # noqa: F401,F403

ALLOWED_PATHS.update({
    ".github/workflows/football-draw-auto-research-r1.yml",
    "football-data/research/validate_draw_auto_research_preflight_impl_r1.py",
    "football-data/research/test_draw_auto_research_impl_r1.py",
})

if __name__ == "__main__":
    raise SystemExit(main())
