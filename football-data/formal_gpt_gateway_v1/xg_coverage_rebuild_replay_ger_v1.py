#!/usr/bin/env python3
from __future__ import annotations

import xg_coverage_rebuild_replay_v1 as repair

# Target-scoped repair: only the Bundesliga historical xG coverage can influence
# Stuttgart/Koeln challenger residual state. Avoid touching unrelated formal domains.
repair.COMPETITIONS = ("GER_Bundesliga",)

if __name__ == "__main__":
    raise SystemExit(repair.main())
