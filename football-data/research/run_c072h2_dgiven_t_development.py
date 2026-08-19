#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

# Pure engineering compatibility fixes only:
# 1) map the verified public CSV 1X2 header names;
# 2) disambiguate the scientific column named `T` from pandas DataFrame.T (transpose).
# Contract/model/features/folds/gates remain unchanged.
source_path = Path(__file__).with_name("evaluate_c072h2_dgiven_t_development.py")
source = source_path.read_text(encoding="utf-8")
for old, new in {
    "train.T==T": "train['T']==T",
    "test.T==T": "test['T']==T",
    "feat.T.isin": "feat['T'].isin",
    "tr.T==T": "tr['T']==T",
    "te.T==T": "te['T']==T",
}.items():
    source = source.replace(old, new)

ns = {"__name__": "c072h2_fixed", "__file__": str(source_path)}
exec(compile(source, str(source_path), "exec"), ns)
ns["SRC"].update({
    "home_odds_open": "home_open",
    "draw_odds_open": "draw_open",
    "away_odds_open": "away_open",
    "home_odds_close": "home_close",
    "draw_odds_close": "draw_close",
    "away_odds_close": "away_close",
})

if __name__ == "__main__":
    ns["main"]()
