#!/usr/bin/env python3
"""V6.4.0: error atlas for the corrected pooled 17-domain sampled panel.

Research-only diagnostics. Reads the immutable V6.2.5-r4 scored cache and quantifies
where the pooled V6.0.1 architecture fails: class confusion, domain drift, confidence,
and formal/V6 disagreement. No model fitting and no rule changes.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "manifests" / "v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
OUT = ROOT / "manifests" / "v6_error_atlas_v640_status.json"
CLASSES = ("home", "draw", "away")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def acc(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    h = sum(bool(r["hit"]) for r in rows)
    return {"count": n, "hits": h, "accuracy": h / n if n else None}


def confusion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = {p: {t: 0 for t in CLASSES} for p in CLASSES}
    for r in rows:
        matrix[str(r["pick"])][str(r["actual_result"])] += 1
    by_pred = {}
    for p in CLASSES:
        total = sum(matrix[p].values())
        by_pred[p] = {
            "count": total,
            "hits": matrix[p][p],
            "precision": matrix[p][p] / total if total else None,
            "truth_distribution": matrix[p],
        }
    by_truth = {}
    for t in CLASSES:
        total = sum(matrix[p][t] for p in CLASSES)
        by_truth[t] = {
            "count": total,
            "correct": matrix[t][t],
            "recall": matrix[t][t] / total if total else None,
            "predicted_distribution": {p: matrix[p][t] for p in CLASSES},
        }
    return {"matrix_predicted_x_truth": matrix, "by_predicted_direction": by_pred, "by_actual_direction": by_truth}


def confidence_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [(0.0, .05), (.05, .10), (.10, .20), (.20, .30), (.30, .40), (.40, .50), (.50, 1.01)]
    out = []
    for lo, hi in buckets:
        subset = [r for r in rows if lo <= float(r["confidence"]) < hi]
        item = {"low": lo, "high": hi, **acc(subset)}
        item["predicted"] = dict(Counter(str(r["pick"]) for r in subset))
        item["actual"] = dict(Counter(str(r["actual_result"]) for r in subset))
        item["agreement_rate"] = (sum(str(r["pick"]) == str(r["formal_pick"]) for r in subset) / len(subset)) if subset else None
        out.append(item)
    return out


def domain_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    domains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        domains[str(r["competition_id"])].append(r)
    out = {}
    for cid, subset in sorted(domains.items()):
        older = [r for r in subset if r["role"] == "older"]
        newer = [r for r in subset if r["role"] == "newer"]
        older_a, newer_a = acc(older), acc(newer)
        conf = confusion(newer)
        out[cid] = {
            "older": older_a,
            "newer": newer_a,
            "accuracy_drift_pp": 100.0 * (float(newer_a["accuracy"]) - float(older_a["accuracy"])) if older_a["accuracy"] is not None and newer_a["accuracy"] is not None else None,
            "newer_predicted_precision": {k: v["precision"] for k, v in conf["by_predicted_direction"].items()},
            "newer_actual_draw_rate": conf["by_actual_direction"]["draw"]["count"] / len(newer) if newer else None,
            "newer_predicted_draw_rate": conf["by_predicted_direction"]["draw"]["count"] / len(newer) if newer else None,
        }
    return out


def disagreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    agree = [r for r in rows if r["pick"] == r["formal_pick"]]
    disagree = [r for r in rows if r["pick"] != r["formal_pick"]]
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in disagree:
        pairs[f"{r['formal_pick']}->{r['pick']}"] .append(r)
    return {
        "agreement": acc(agree),
        "disagreement": acc(disagree),
        "disagreement_pairs": {k: acc(v) for k, v in sorted(pairs.items())},
    }


def top_error_contributors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r["competition_id"])].append(r)
    items = []
    for cid, subset in groups.items():
        errors = sum(not bool(r["hit"]) for r in subset)
        items.append({"competition_id": cid, "count": len(subset), "errors": errors, "accuracy": 1.0 - errors / len(subset)})
    return sorted(items, key=lambda x: (-x["errors"], x["accuracy"], x["competition_id"]))


def main() -> int:
    cache = load(CACHE)
    rows = list(cache["rows"])
    older = [r for r in rows if r["role"] == "older"]
    newer = [r for r in rows if r["role"] == "newer"]
    payload = {
        "schema_version": "V6.4.0-pooled-error-atlas-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "source": {
            "cache": CACHE.name,
            "panel_sha256": cache.get("panel_sha256"),
            "count": len(rows),
            "architecture": cache.get("architecture"),
        },
        "overall": {"older": acc(older), "newer": acc(newer), "combined": acc(rows)},
        "newer_confusion": confusion(newer),
        "newer_confidence_buckets": confidence_buckets(newer),
        "newer_agreement": disagreement(newer),
        "by_domain": domain_table(rows),
        "newer_error_contributors": top_error_contributors(newer),
        "governance": {
            "diagnostic_only": True,
            "model_change": False,
            "threshold_change": False,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
