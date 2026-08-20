#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

BASE = Path(__file__).with_name('run_c072n18c_market_anchor_xgstate_development.py')
spec = importlib.util.spec_from_file_location('n18c_base', BASE)
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load frozen N18C runner')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def corrected_resolve_result_table(page_html):
    soup = m.BeautifulSoup(page_html, 'html.parser')
    cand = []
    for t in soup.find_all('table'):
        h = m.table_headers(t)
        if h != m.RESULT_HEADERS:
            continue
        tid = str(t.get('data-wpdatatable_id', ''))
        if not tid.isdigit():
            continue
        seasons = m.visible_seasons(t, h)
        yrs = [m.start_year(s) for s in seasons]
        yrs = [y for y in yrs if y is not None]
        cand.append((min(yrs) if yrs else None, t, int(tid), seasons))

    # Protocol drift correction only. If the page exposes exactly one exact-schema
    # result table, there is no ambiguity and it is selected directly. No result
    # row or numeric score is read here.
    if len(cand) == 1:
        _, t, tid, seasons = cand[0]
        return t, tid, seasons

    # If multiple exact-schema tables are present, retain the already-established
    # historical-table rule: unique table with the earliest visible start season.
    with_year = [x for x in cand if x[0] is not None]
    if len(with_year) < 2:
        raise RuntimeError(f'result table protocol ambiguous exact={len(cand)} with_year={len(with_year)}')
    earliest = min(x[0] for x in with_year)
    hist = [x for x in with_year if x[0] == earliest]
    if len(hist) != 1:
        raise RuntimeError(f'result historical protocol nonunique earliest got {len(hist)}')
    _, t, tid, seasons = hist[0]
    return t, tid, seasons


m.resolve_result_table = corrected_resolve_result_table
m.main()
