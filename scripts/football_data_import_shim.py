from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'football-data' / 'engine'
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
from platform_core import normalize_team_token  # noqa: E402,F401
