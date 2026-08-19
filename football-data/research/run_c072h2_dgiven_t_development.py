#!/usr/bin/env python3
from __future__ import annotations

import evaluate_c072h2_dgiven_t_development as h2

# Pure source-schema mapping fix verified directly against pinned CSV header.
# Scientific contract/model/features/folds/gates are unchanged.
h2.SRC.update({
    "home_odds_open": "home_open",
    "draw_odds_open": "draw_open",
    "away_odds_open": "away_open",
    "home_odds_close": "home_close",
    "draw_odds_close": "draw_close",
    "away_odds_close": "away_close",
})

if __name__ == "__main__":
    h2.main()
