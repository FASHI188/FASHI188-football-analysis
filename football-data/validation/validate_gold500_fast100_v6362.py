#!/usr/bin/env python3
"""V6.36.2 reusable Gold500 Fast100 1X2 scorer.

Loads the frozen V6.36 Gold500 files directly; it does not rebuild historical
features. This is the fast path for future model screens.

- A_FAST100 is always scored.
- B_CONFIRM300 remains untouched by this script.
- C_SEALED100 has no emitted labels and cannot be scored here.
- Market/formal baselines are recorded for the same exact 100 matches.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"
OUT = ROOT / "manifests" / "v6_gold500_fast100_baselines_v6362_status.json"
PART = "A_FAST100"
EPS = 1e-15


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metrics(rows: list[dict[str, Any]], labels: dict[int, dict[str, Any]], key: str) -> dict[str, Any]:
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter(); actual = Counter()
    for row in rows:
        idx = int(row["gold_index"])
        lab = labels[idx]
        y = int(lab["label"])
        p = [float(x) for x in row[key]]
        if len(p) != 3 or any(x < 0.0 for x in p) or abs(sum(p) - 1.0) > 1e-8:
            raise RuntimeError(f"invalid probability vector for gold_index={idx} key={key}: {p}")
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y); predicted[str(pick)] += 1; actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n,
        "brier": brier / n,
        "logloss": logloss / n,
        "rps": rps / n,
        "predicted_counts": dict(predicted),
        "actual_counts": dict(actual),
    }


def main() -> int:
    features = load_jsonl(FEATURES)
    label_rows = load_jsonl(LABELS)
    labels = {int(r["gold_index"]): r for r in label_rows}

    partition_counts = Counter(str(r["partition"]) for r in features)
    label_partition_counts = Counter(str(r["partition"]) for r in label_rows)
    if len(features) != 500:
        raise RuntimeError(f"Gold500 feature count changed: {len(features)}")
    if partition_counts != Counter({"A_FAST100": 100, "B_CONFIRM300": 300, "C_SEALED100": 100}):
        raise RuntimeError(f"partition contract changed: {dict(partition_counts)}")
    if label_partition_counts != Counter({"A_FAST100": 100, "B_CONFIRM300": 300}):
        raise RuntimeError(f"development label contract changed: {dict(label_partition_counts)}")
    if any(int(r["gold_index"]) >= 400 for r in label_rows):
        raise RuntimeError("sealed C labels leaked into development label file")

    fast = [r for r in features if r["partition"] == PART]
    if any(int(r["gold_index"]) not in labels for r in fast):
        raise RuntimeError("Fast100 label missing")

    market = metrics(fast, labels, "market")
    formal = metrics(fast, labels, "formal")
    payload = {
        "schema_version": "V6.36.2-gold500-fast100-baselines-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_FIXED_GOLD500_FAST100_BASELINES",
        "gold_contract": {
            "features": 500,
            "development_labels": 400,
            "partition_counts": dict(partition_counts),
            "label_partition_counts": dict(label_partition_counts),
            "sealed_labels_present": False,
        },
        "fast100": {
            "market": market,
            "formal": formal,
            "candidate_continuation_gate": {
                "minimum_market_top1_uplift_pp": 3.0,
                "proper_score_guard": "candidate logloss and RPS should not materially deteriorate",
                "B_CONFIRM300_open_only_after_pass": True,
            },
        },
        "governance": {
            "rebuild_features": False,
            "A_only": True,
            "B_scored": False,
            "C_scored": False,
            "current_unchanged": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "market_top1": market["top1"],
        "formal_top1": formal["top1"],
        "market_hits": market["hits"],
        "formal_hits": formal["hits"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
