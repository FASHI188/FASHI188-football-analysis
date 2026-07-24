#!/usr/bin/env python3
"""Disjoint 100-match validation of retrospective market-only 1X2 vs formal model.

No tuning is performed. The 4k+ market-matched historical rows are shuffled once with a
fixed seed and partitioned into non-overlapping groups of 100. This directly answers
whether market-only accuracy uplift is broad or an artefact of overlapping resamples.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from diagnose_1x2_market_anchor_v697 import (
    _load_model_rows, _match_market, _model_probs, _market_probs, _pick_probs
)

OUT = ROOT / "manifests" / "v6_1x2_market_anchor_disjoint_v699_status.json"
SEED = 20260724 + 699
BLOCK = 100


def _acc(rows, picker):
    hits = sum(1 for r in rows if picker(r) == r["actual"])
    return hits, len(rows), hits / len(rows) if rows else None


def _mcnemar_exact(b: int, c: int) -> float | None:
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def main() -> int:
    rows, providers = _match_market(_load_model_rows())
    rng = random.Random(SEED)
    rows = list(rows)
    rng.shuffle(rows)
    full_blocks = len(rows) // BLOCK
    blocks = []
    for i in range(full_blocks):
        chunk = rows[i*BLOCK:(i+1)*BLOCK]
        mh, _, ma = _acc(chunk, lambda r: _pick_probs(_model_probs(r)))
        qh, _, qa = _acc(chunk, lambda r: _pick_probs(_market_probs(r)))
        blocks.append({
            "block": i + 1, "n": BLOCK,
            "model_hits": mh, "model_accuracy": ma,
            "market_hits": qh, "market_accuracy": qa,
            "market_uplift_pp": (qa - ma) * 100.0,
        })

    model_hits, n, model_acc = _acc(rows, lambda r: _pick_probs(_model_probs(r)))
    market_hits, _, market_acc = _acc(rows, lambda r: _pick_probs(_market_probs(r)))

    pair = Counter()
    for r in rows:
        mc = _pick_probs(_model_probs(r)) == r["actual"]
        qc = _pick_probs(_market_probs(r)) == r["actual"]
        pair["both_correct" if mc and qc else "model_only_correct" if mc else "market_only_correct" if qc else "both_wrong"] += 1

    by_comp = {}
    for cid in sorted({r["competition_id"] for r in rows}):
        sub = [r for r in rows if r["competition_id"] == cid]
        mh, nn, ma = _acc(sub, lambda r: _pick_probs(_model_probs(r)))
        qh, _, qa = _acc(sub, lambda r: _pick_probs(_market_probs(r)))
        by_comp[cid] = {
            "count": nn, "model_hits": mh, "model_accuracy": ma,
            "market_hits": qh, "market_accuracy": qa,
            "market_uplift_pp": (qa - ma) * 100.0,
        }

    uplifts = [b["market_uplift_pp"] for b in blocks]
    compare = Counter("win" if x > 0 else "tie" if x == 0 else "loss" for x in uplifts)
    b = int(pair["market_only_correct"])
    c = int(pair["model_only_correct"])

    payload = {
        "schema_version": "V6.9.9-market-anchor-disjoint-100-validation-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "matched_count": n,
        "provider_class_counts": providers,
        "overall": {
            "model_hits": model_hits, "model_accuracy": model_acc,
            "market_hits": market_hits, "market_accuracy": market_acc,
            "market_uplift_pp": (market_acc - model_acc) * 100.0,
        },
        "disjoint_100_blocks": {
            "full_block_count": full_blocks,
            "leftover_count": len(rows) - full_blocks*BLOCK,
            "market_vs_model": dict(compare),
            "market_uplift_pp_mean": statistics.mean(uplifts),
            "market_uplift_pp_median": statistics.median(uplifts),
            "market_uplift_pp_min": min(uplifts),
            "market_uplift_pp_max": max(uplifts),
            "blocks": blocks,
        },
        "paired_correctness": {
            **dict(pair),
            "mcnemar_discordant_market_only_correct": b,
            "mcnemar_discordant_model_only_correct": c,
            "mcnemar_exact_two_sided_p": _mcnemar_exact(b, c),
        },
        "by_competition": by_comp,
        "governance": {
            "research_only": True,
            "no_tuning": True,
            "non_overlapping_100_match_blocks": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "overall": payload["overall"],
        "disjoint_100_blocks": {k:v for k,v in payload["disjoint_100_blocks"].items() if k != "blocks"},
        "paired_correctness": payload["paired_correctness"],
        "by_competition": by_comp,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
