#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import run_r43d1 as d  # noqa: E402

RESULTS = ("H", "D", "A")
RINDEX = {r: i for i, r in enumerate(RESULTS)}


def fast_ipf_preserve_total_and_result(seed: dict, prior: dict):
    target_t_dict = d.exact_total_marginal(prior)
    max_total = max(target_t_dict)
    target_t = np.zeros(max_total + 1, dtype=float)
    for n, q in target_t_dict.items():
        target_t[int(n)] = float(q)
    tr = d.result_marginal(prior)
    target_r = np.asarray([tr[r] for r in RESULTS], dtype=float)

    seed_tab = np.zeros((max_total + 1, 3), dtype=float)
    for (h, a), q in seed.items():
        seed_tab[h + a, RINDEX[d.result_key(h, a)]] += float(q)
    tab = seed_tab.copy()
    it_used = 0
    for it in range(min(d.IPF_ITERS, 80)):
        it_used = it + 1
        row = tab.sum(axis=1)
        rf = np.ones_like(row)
        ok = row > 0
        rf[ok] = target_t[ok] / row[ok]
        tab *= rf[:, None]

        col = tab.sum(axis=0)
        cf = np.ones_like(col)
        okc = col > 0
        cf[okc] = target_r[okc] / col[okc]
        tab *= cf[None, :]

        rt = float(np.max(np.abs(tab.sum(axis=1) - target_t)))
        rr = float(np.max(np.abs(tab.sum(axis=0) - target_r)))
        if max(rt, rr) <= d.IPF_TOL:
            break

    factors = np.ones_like(tab)
    nz = seed_tab > 0
    factors[nz] = tab[nz] / seed_tab[nz]
    out = {}
    for (h, a), q in seed.items():
        out[(h, a)] = float(q) * float(factors[h + a, RINDEX[d.result_key(h, a)]])
    s = sum(out.values())
    out = {k: v / s for k, v in out.items()}

    out_t = d.exact_total_marginal(out)
    out_r = d.result_marginal(out)
    rt = max(abs(out_t.get(n, 0.0) - target_t_dict[n]) for n in target_t_dict)
    rr = max(abs(out_r[r] - tr[r]) for r in RESULTS)
    return out, float(rr), float(rt), it_used


d.ipf_preserve_total_and_result = fast_ipf_preserve_total_and_result

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        d.run()
    elif cmd == "verify":
        d.verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
