#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.special import betaln, gammaln

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import run_r43d1 as d  # noqa: E402

RESULTS = ("H", "D", "A")
RINDEX = {r: i for i, r in enumerate(RESULTS)}

# Fixed score-grid arrays. This only vectorizes the exact same predeclared
# beta-binomial seed and IPF mathematics; it does not change candidates,
# selection, split, labels, or any scoring rule.
_H, _A = np.meshgrid(
    np.arange(d.MAXG + 1, dtype=int),
    np.arange(d.MAXG + 1, dtype=int),
    indexing="ij",
)
_HF = _H.ravel()
_AF = _A.ravel()
_NF = _HF + _AF
_MAXT = int(_NF.max())
_LOGCOMB = gammaln(_NF + 1.0) - gammaln(_HF + 1.0) - gammaln(_AF + 1.0)
_KEYS = [(int(h), int(a)) for h, a in zip(_HF, _AF)]
_RI = np.asarray([RINDEX[d.result_key(int(h), int(a))] for h, a in _KEYS], dtype=int)


def fast_beta_seed(prior: dict, mu_h: float, mu_a: float, concentration: float) -> dict:
    p = float(mu_h) / max(float(mu_h) + float(mu_a), 1e-12)
    p = min(1.0 - 1e-8, max(1e-8, p))
    alpha = p * float(concentration)
    beta = (1.0 - p) * float(concentration)

    logw = _LOGCOMB + betaln(_HF + alpha, _AF + beta) - betaln(alpha, beta)
    w = np.exp(logw)
    z_by_total = np.bincount(_NF, weights=w, minlength=_MAXT + 1)

    prior_vec = np.fromiter((float(prior[k]) for k in _KEYS), dtype=float, count=len(_KEYS))
    total_mass = np.bincount(_NF, weights=prior_vec, minlength=_MAXT + 1)
    q = total_mass[_NF] * w / np.maximum(z_by_total[_NF], 1e-300)
    q /= q.sum()
    return {k: float(v) for k, v in zip(_KEYS, q)}


def fast_ipf_preserve_total_and_result(seed: dict, prior: dict):
    prior_vec = np.fromiter((float(prior[k]) for k in _KEYS), dtype=float, count=len(_KEYS))
    seed_vec = np.fromiter((float(seed[k]) for k in _KEYS), dtype=float, count=len(_KEYS))

    target_t = np.bincount(_NF, weights=prior_vec, minlength=_MAXT + 1)
    target_r = np.bincount(_RI, weights=prior_vec, minlength=3)
    seed_tab = np.zeros((_MAXT + 1, 3), dtype=float)
    np.add.at(seed_tab, (_NF, _RI), seed_vec)

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

    factor_tab = np.ones_like(tab)
    nz = seed_tab > 0
    factor_tab[nz] = tab[nz] / seed_tab[nz]
    out_vec = seed_vec * factor_tab[_NF, _RI]
    out_vec /= out_vec.sum()

    out_t = np.bincount(_NF, weights=out_vec, minlength=_MAXT + 1)
    out_r = np.bincount(_RI, weights=out_vec, minlength=3)
    rt = float(np.max(np.abs(out_t - target_t)))
    rr = float(np.max(np.abs(out_r - target_r)))
    out = {k: float(v) for k, v in zip(_KEYS, out_vec)}
    return out, rr, rt, it_used


d.beta_seed = fast_beta_seed
d.ipf_preserve_total_and_result = fast_ipf_preserve_total_and_result

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        d.run()
    elif cmd == "verify":
        d.verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
