#!/usr/bin/env python3
"""V6.50.0 one-shot A_FAST100 validation on the V6.49.3 Full500 freeze.

This validator does not tune on A100. It scores three already-frozen probability tracks:
1. retrospective market baseline carried in the Full500 feature artifact;
2. formal V5 probability vector carried in the same artifact;
3. V6.38-style cross-bookmaker closing consensus using a predeclared geometric pool
   of all complete individual closing 1X2 bookmaker triplets (>=2 books).

The third track is not fitted on A100: each book is de-vigged, probabilities are pooled
by equal-weight geometric mean, then normalized. Aggregate Avg/Max/BbAv/BbMx fields are
excluded exactly as in the prior V6.38 research family.

A_FAST100 is read once. B_CONFIRM300 is not read. C_SEALED100 is not read.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import v6_market_residual_fusion_v620 as marketmod  # noqa: E402

FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
OUT = ROOT / "manifests" / "v6_full500_fast100_baselines_v6500_status.json"
PART = "A_FAST100"
EPS = 1e-12
EXCLUDE_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_a100_labels(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("label artifact ended inside A_FAST100")
            r = json.loads(line)
            if str(r.get("partition")) != PART:
                raise RuntimeError(f"non-A100 label encountered: {r.get('partition')}")
            out[int(r["full_index"])] = r
    if set(out) != set(range(100)):
        raise RuntimeError("A_FAST100 label index contract changed")
    return out


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def devig3(h: float, d: float, a: float) -> np.ndarray:
    inv = np.asarray([1.0 / h, 1.0 / d, 1.0 / a], dtype=float)
    return inv / inv.sum()


def individual_books(raw: dict[str, str]) -> list[np.ndarray]:
    probs = []
    for key in raw:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_PREFIXES:
            continue
        dk, ak = prefix + "CD", prefix + "CA"
        if dk not in raw or ak not in raw:
            continue
        h, d, a = fnum(raw, key), fnum(raw, dk), fnum(raw, ak)
        if h is None or d is None or a is None:
            continue
        probs.append(devig3(h, d, a))
    return probs


def geometric_pool(probs: list[np.ndarray]) -> list[float] | None:
    if len(probs) < 2:
        return None
    arr = np.asarray(probs, dtype=float)
    g = np.exp(np.log(np.clip(arr, EPS, 1.0)).mean(axis=0))
    g /= g.sum()
    return [float(x) for x in g]


def raw_lookup(cid: str) -> dict[tuple[str, str, str, str], dict[str, str]]:
    out = {}
    directory = ROOT / "processed" / cid
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season != "2025/26":
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                except Exception:
                    continue
                home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if not home or not away:
                    continue
                key = (season, date, home, away)
                out.setdefault(key, raw)
    return out


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter(); actual = Counter()
    for r in rows:
        p = [float(x) for x in r[key]]
        y = int(r["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1; actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1*c1 + c2*c2) / 2.0
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
    features = [r for r in load_jsonl(FEATURES) if str(r.get("partition")) == PART]
    if len(features) != 100 or set(int(r["full_index"]) for r in features) != set(range(100)):
        raise RuntimeError("Full500 A_FAST100 feature contract changed")
    labels = load_a100_labels(LABELS)

    lookups = {cid: raw_lookup(cid) for cid in sorted({str(r["competition_id"]) for r in features})}
    rows = []
    micro_miss = 0
    book_counts = []
    for f in features:
        idx = int(f["full_index"])
        lab = labels[idx]
        cid = str(f["competition_id"])
        key = (
            str(f["season"]), str(f["date"]),
            v632._token(cid, str(f["home_team"])),
            v632._token(cid, str(f["away_team"])),
        )
        raw = lookups[cid].get(key)
        gp = None
        if raw is not None:
            books = individual_books(raw)
            book_counts.append(len(books))
            gp = geometric_pool(books)
        if gp is None:
            micro_miss += 1
            # Fail closed rather than silently substituting market.
            continue
        rows.append({
            "full_index": idx,
            "competition_id": cid,
            "date": str(f["date"]),
            "home_team": str(f["home_team"]),
            "away_team": str(f["away_team"]),
            "y": int(lab["label"]),
            "market": [float(x) for x in f["market"]],
            "formal": [float(x) for x in f["formal"]],
            "micro_geo": gp,
        })

    if micro_miss or len(rows) != 100:
        raise RuntimeError(f"A100 microstructure coverage incomplete: rows={len(rows)} misses={micro_miss}")

    market = metrics(rows, "market")
    formal = metrics(rows, "formal")
    micro = metrics(rows, "micro_geo")
    gate = {
        "required_candidate_hits": 63,
        "required_uplift_vs_market_pp": 3.0,
        "candidate": "V6.38_FIXED_EQUAL_WEIGHT_GEOMETRIC_BOOK_POOL",
        "candidate_hits": micro["hits"],
        "market_hits": market["hits"],
        "uplift_vs_market_pp": 100.0 * (micro["top1"] - market["top1"]),
        "top1_gate": micro["hits"] >= 63,
        "uplift_gate": (micro["top1"] - market["top1"]) >= 0.03 - 1e-12,
        "proper_score_guard": micro["logloss"] <= market["logloss"] + 0.01 and micro["rps"] <= market["rps"] + 0.01,
    }
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])

    payload = {
        "schema_version": "V6.50.0-full500-fast100-baselines-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "full500_identity_freeze_sha256": "0c9d7883d45935bca3379f15d74268a2513e6d3038b5c1123e768195924f56c8",
        "partition_seed": 649503,
        "governance": {
            "A_FAST100_read_once": True,
            "B_CONFIRM300_labels_read": False,
            "C_SEALED100_labels_read": False,
            "A100_tuning": False,
            "league_dropping": False,
            "confidence_filtering": False,
            "seed_replacement": False,
            "CURRENT_unchanged": True,
        },
        "coverage": {
            "A_FAST100": len(rows),
            "microstructure_missing": micro_miss,
            "individual_closing_books_mean": float(np.mean(book_counts)),
            "individual_closing_books_min": int(min(book_counts)),
            "individual_closing_books_max": int(max(book_counts)),
            "competition_counts": dict(Counter(r["competition_id"] for r in rows)),
        },
        "market": market,
        "formal_V5": formal,
        "research_micro_geo": micro,
        "A_gate": gate,
        "next_step": "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300_RESEARCH_NEW_SIGNAL_OR_MODEL",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
