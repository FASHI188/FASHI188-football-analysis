#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_r43q0 as q  # noqa: E402


def grouped_folds(rows: list[dict], k: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur_key = None
    cur: list[dict] = []
    for r in rows:
        key = r["kickoff_utc"]
        if cur_key is None or key == cur_key:
            cur.append(r)
            cur_key = key
        else:
            groups.append(cur)
            cur = [r]
            cur_key = key
    if cur:
        groups.append(cur)
    if len(groups) < k:
        raise RuntimeError(f"insufficient kickoff groups {len(groups)} for {k} folds")

    total = sum(len(g) for g in groups)
    folds: list[list[dict]] = []
    acc: list[dict] = []
    cumulative = 0
    for g in groups:
        boundary = total * (len(folds) + 1) / k
        if len(folds) < k - 1 and acc and cumulative + len(g) > boundary:
            folds.append(acc)
            acc = []
        acc.extend(g)
        cumulative += len(g)
    if acc:
        folds.append(acc)
    if len(folds) != k or any(not f for f in folds):
        raise RuntimeError(f"fold construction failed sizes={[len(f) for f in folds]}")
    return folds


q.grouped_folds = grouped_folds

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        q.run()
    elif cmd == "verify":
        q.verify()
    else:
        raise SystemExit(cmd)
