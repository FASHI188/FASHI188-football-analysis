#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ab0.json"
ROOT = HERE.parents[1]
R9 = ROOT / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
FIX_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixtures.parquet?download=true"
TOKENS = ("venue", "stadium", "referee", "neutral", "timezone", "city", "round", "stage")


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ab0/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def run() -> dict:
    if not R9.exists():
        raise RuntimeError(f"missing frozen R9 snapshot: {R9}")
    ids = set(pd.read_csv(R9, usecols=["game_id"])["game_id"].astype("int64").tolist())
    if len(ids) != 20000:
        raise RuntimeError(f"expected 20000 R9 ids, got {len(ids)}")

    tmp = HERE / "fixtures.parquet"
    download(FIX_URL, tmp)
    pf = pq.ParquetFile(tmp)
    names = list(pf.schema.names)
    candidates = [c for c in names if any(t in c.lower() for t in TOKENS)]
    if "id" not in names:
        raise RuntimeError("fixtures schema missing id")

    cols = ["id"] + candidates
    df = pd.read_parquet(tmp, columns=cols)
    df = df[df["id"].isin(ids)].copy()
    matched = int(df["id"].nunique())
    coverage = {}
    for c in candidates:
        s = df[c]
        nonnull = int(s.notna().sum())
        distinct = int(s.dropna().astype(str).nunique())
        coverage[c] = {
            "dtype": str(s.dtype),
            "nonnull_rows": nonnull,
            "nonnull_rate": (nonnull / matched) if matched else 0.0,
            "distinct_nonnull": distinct,
            "examples": s.dropna().astype(str).drop_duplicates().head(5).tolist(),
        }

    out = {
        "schema_version": "football3-r43ab0-fixture-context-schema-audit-v1",
        "status": "COMPLETE",
        "classification": "ZERO_MODEL_SCHEMA_AND_COVERAGE_AUDIT_ON_CONSUMED_R9_HISTORY",
        "formal_weight": 0,
        "governance": {
            "model_fits": 0,
            "candidate_probabilities": 0,
            "new_fresh_labels_consumed": False,
            "r9_historical_labels_already_consumed": True,
            "r9_snapshot_rows": 20000,
            "current_result_fields_read_by_this_audit": False,
            "promotion_allowed": False,
        },
        "source": {"fixtures_url": FIX_URL, "r9_snapshot": str(R9)},
        "all_fixture_columns": names,
        "candidate_context_columns": candidates,
        "matched_r9_fixture_ids": matched,
        "coverage": coverage,
        "next": "IF venue/referee/neutral fields have usable coverage, preregister a strict-prior incremental K1 screen; otherwise close this axis without fitting.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.unlink(missing_ok=True)
    print(json.dumps({"status": out["status"], "matched": matched, "candidates": candidates, "coverage": coverage}, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE"
    assert s["formal_weight"] == 0
    assert s["governance"]["model_fits"] == 0
    assert s["governance"]["candidate_probabilities"] == 0
    assert s["matched_r9_fixture_ids"] == 20000
    print("R43AB0 fixture context schema audit verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(cmd)
