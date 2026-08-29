#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ad0.json"
R9 = HERE.parent / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
STAT_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/match_stats.parquet?download=true"
FIELDS = [
    "home_yellow_cards", "away_yellow_cards",
    "home_red_cards", "away_red_cards",
    "home_fouls", "away_fouls",
    "home_penalties", "away_penalties",
]


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ad0/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def run() -> dict:
    if not R9.exists():
        raise RuntimeError(f"missing R9 snapshot: {R9}")
    ids = set(pd.read_csv(R9, usecols=["game_id"], dtype=str)["game_id"].astype("int64").tolist())
    if len(ids) != 20000:
        raise RuntimeError(f"expected 20000 R9 ids, got {len(ids)}")

    tmp = HERE / "match_stats.parquet"
    download(STAT_URL, tmp)
    cols = ["fixture_id", "known_at"] + FIELDS
    df = pd.read_parquet(tmp, columns=cols)
    df = df[df["fixture_id"].isin(ids)].drop_duplicates("fixture_id")
    matched = int(df["fixture_id"].nunique())
    if matched != 20000:
        raise RuntimeError(f"expected 20000 matched match_stats rows, got {matched}")

    coverage = {}
    for f in FIELDS:
        nonnull = int(df[f].notna().sum())
        coverage[f] = {"nonnull_rows": nonnull, "nonnull_rate": nonnull / matched}

    pair_defs = {
        "yellow_pair": ["home_yellow_cards", "away_yellow_cards"],
        "red_pair": ["home_red_cards", "away_red_cards"],
        "foul_pair": ["home_fouls", "away_fouls"],
        "penalty_pair": ["home_penalties", "away_penalties"],
        "yellow_red_core": ["home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards"],
        "yellow_red_foul_core": ["home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards", "home_fouls", "away_fouls"],
    }
    pair_coverage = {}
    for name, fs in pair_defs.items():
        n = int(df[fs].notna().all(axis=1).sum())
        pair_coverage[name] = {"complete_rows": n, "complete_rate": n / matched}

    known_at = int(df["known_at"].notna().sum())
    out = {
        "schema_version": "football3-r43ad0-team-discipline-coverage-audit-v1",
        "status": "COMPLETE",
        "classification": "ZERO_MODEL_POSTMATCH_FIELD_AVAILABILITY_AUDIT_ON_CONSUMED_HISTORY",
        "formal_weight": 0,
        "governance": {
            "model_fits": 0,
            "candidate_probabilities": 0,
            "r9_outcome_columns_read": False,
            "postmatch_discipline_values_used_for_prediction": False,
            "audit_reads_postmatch_fields_only_to_measure_historical_availability": True,
            "promotion_allowed": False,
        },
        "source": {"r9_snapshot_rows": 20000, "match_stats_url": STAT_URL},
        "matched_fixture_rows": matched,
        "known_at_nonnull_rows": known_at,
        "known_at_nonnull_rate": known_at / matched,
        "field_coverage": coverage,
        "complete_pair_coverage": pair_coverage,
        "next": "If yellow/red discipline history has broad coverage, preregister a strict-prior team-discipline incremental K1 screen. Current-match discipline values remain forbidden; only earlier-date rows may update state.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.unlink(missing_ok=True)
    print(json.dumps({"status": out["status"], "field_coverage": coverage, "pair_coverage": pair_coverage, "known_at_rate": out["known_at_nonnull_rate"]}, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["matched_fixture_rows"] == 20000
    assert s["governance"]["model_fits"] == 0
    assert s["governance"]["r9_outcome_columns_read"] is False
    assert s["governance"]["postmatch_discipline_values_used_for_prediction"] is False
    print("R43AD0 team discipline coverage audit verified")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
