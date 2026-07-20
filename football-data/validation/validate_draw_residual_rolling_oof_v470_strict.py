#!/usr/bin/env python3
"""Leakage-safe completed-season entrypoint for draw residual rolling OOF.

The base rolling validator is capped at each domain's last completed season as of
2026-07-20. This prevents current/transition folds (for example SWE 2026) from
entering a completed-season research verdict. Formal weight remains zero.
"""
from __future__ import annotations

import validate_draw_residual_rolling_oof_v470 as base
from backtest_last_complete_season_all_domains_v470 import _requested_last_complete_season


def _completed_target_outer_seasons(report):
    cid = str(report.get("competition_id") or "")
    if not cid:
        raise base.PlatformError("competition_id missing from rolling OOF source report")
    max_year = base._season_year(_requested_last_complete_season(cid))
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if not season or season in seasons:
            continue
        if base._season_year(season) <= max_year:
            seasons.append(season)
    seasons.sort(key=base._season_year)
    # At least one prior completed outer season is needed to train the residual layer.
    return seasons[1:]


base._target_outer_seasons = _completed_target_outer_seasons


if __name__ == "__main__":
    raise SystemExit(base.main())
