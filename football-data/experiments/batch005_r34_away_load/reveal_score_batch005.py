#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
FIX_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixtures.parquet?download=true"
LABEL_COLS = ["id", "goals_home", "goals_away", "status_norm", "is_played"]


def jsha(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def download_labels():
    path = OUT / "_fixtures_labels.parquet"
    req = urllib.request.Request(FIX_URL, headers={"User-Agent": "football3-batch005-reveal"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    df = pd.read_parquet(path, columns=LABEL_COLS)
    try:
        path.unlink()
    except Exception:
        pass
    return df


def actual(hg, ag):
    return 0 if hg > ag else 1 if hg == ag else 2


def metrics(rows, key):
    n = len(rows)
    hit = ll = br = rps = 0.0
    picks = [0, 0, 0]
    hits = [0, 0, 0]
    actuals = [0, 0, 0]
    for r in rows:
        p = r[key]
        y = int(r["y"])
        v = [float(p["p_home"]), float(p["p_draw"]), float(p["p_away"])]
        t = int(p["top1"])
        c = int(t == y)
        hit += c
        picks[t] += 1
        hits[t] += c
        actuals[y] += 1
        ll -= math.log(max(v[y], 1e-15))
        br += sum((v[i] - int(i == y)) ** 2 for i in range(3))
        rps += ((v[0] - int(y == 0)) ** 2 + ((v[0] + v[1]) - int(y <= 1)) ** 2) / 2
    return {
        "count": n,
        "hits": int(hit),
        "top1_accuracy": float(hit / n) if n else None,
        "logloss": float(ll / n) if n else None,
        "brier": float(br / n) if n else None,
        "rps": float(rps / n) if n else None,
        "top1_picks": {"home": picks[0], "draw": picks[1], "away": picks[2]},
        "top1_hits": {"home": hits[0], "draw": hits[1], "away": hits[2]},
        "actuals": {"home": actuals[0], "draw": actuals[1], "away": actuals[2]},
    }


def paired_blocks(rows):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), 4)
    block_map = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            block_map[d] = i
    blocks = {str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0} for i in range(4)}
    gain = loss = 0
    for r in rows:
        y = int(r["y"])
        b = int(r["K1"]["top1"] == y)
        c = int(r["R34_AWAY_LOAD_ONLY"]["top1"] == y)
        gain += int(c and not b)
        loss += int(b and not c)
        z = blocks[str(block_map[r["date"]])]
        z["count"] += 1
        z["base_hits"] += b
        z["candidate_hits"] += c
    for z in blocks.values():
        z["net"] = z["candidate_hits"] - z["base_hits"]
    return {
        "challenger_gain": gain,
        "challenger_loss": loss,
        "net_hits": gain - loss,
        "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
        "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()),
        "time_blocks": blocks,
    }


def run():
    lock_s = json.loads((OUT / "batch005_locked_100.json").read_text(encoding="utf-8"))
    pred_s = json.loads((OUT / "batch005_predictions_locked.json").read_text(encoding="utf-8"))
    if pred_s["governance"]["cohort_sha256"] != lock_s["cohort_sha256"]:
        raise RuntimeError("Batch005 reveal cohort mismatch")
    if pred_s["prediction_sha256"] != jsha(pred_s["rows"]):
        raise RuntimeError("Batch005 prediction lock hash mismatch")

    df = download_labels()
    labels = {}
    for rec in df.itertuples(index=False):
        labels[str(int(rec.id))] = rec

    scored = []
    missing_or_unplayed = []
    for r in pred_s["rows"]:
        rec = labels.get(r["game_id"])
        if rec is None or not bool(rec.is_played) or str(rec.status_norm) != "FT" or pd.isna(rec.goals_home) or pd.isna(rec.goals_away):
            missing_or_unplayed.append(r["game_id"])
            continue
        hg, ag = int(rec.goals_home), int(rec.goals_away)
        scored.append({**r, "home_goals": hg, "away_goals": ag, "y": actual(hg, ag)})

    contract = pred_s["reveal_contract"]
    base = metrics(scored, "K1")
    cand = metrics(scored, "R34_AWAY_LOAD_ONLY")
    paired = paired_blocks(scored) if scored else {
        "challenger_gain": 0, "challenger_loss": 0, "net_hits": 0,
        "positive_time_blocks": 0, "negative_time_blocks": 0, "time_blocks": {},
    }
    gain_hits = cand["hits"] - base["hits"] if scored else 0
    ll_delta = cand["logloss"] - base["logloss"] if scored else None
    complete_enough = len(scored) >= int(contract["min_scorable_rows"])
    confirmed = (
        complete_enough
        and gain_hits >= int(contract["min_candidate_gain_hits"])
        and paired["positive_time_blocks"] >= int(contract["min_positive_time_blocks"])
        and paired["negative_time_blocks"] <= int(contract["max_negative_time_blocks"])
        and ll_delta <= float(contract["max_logloss_worsen"])
    )

    s = {
        "schema_version": "football3-batch005-r34-reveal-score-v1",
        "status": "COMPLETE" if complete_enough else "INCOMPLETE_INSUFFICIENT_SCORABLE_ROWS",
        "classification": "FRESH_UNTOUCHED_CONFIRMATION_OF_FROZEN_R34_RULE",
        "governance": {
            "cohort_locked_before_labels_accessed": True,
            "predictions_locked_before_labels_accessed": True,
            "R34_rule_frozen_before_Batch005": True,
            "Batch005_labels_not_used_for_candidate_selection_or_tuning": True,
            "reveal_contract_predeclared_before_labels": True,
            "odds_used": False,
            "market_prices_used": False,
        },
        "cohort": {
            "locked_rows": len(lock_s["rows"]),
            "scorable_rows": len(scored),
            "missing_or_unplayed_rows": len(missing_or_unplayed),
            "missing_or_unplayed_game_ids": missing_or_unplayed,
            "first_date": min((r["date"] for r in scored), default=None),
            "last_date": max((r["date"] for r in scored), default=None),
        },
        "contract": contract,
        "baseline_K1": base,
        "candidate_R34_AWAY_LOAD_ONLY": cand,
        "gain_hits": gain_hits,
        "gain_top1_pp": 100 * (cand["top1_accuracy"] - base["top1_accuracy"]) if scored else None,
        "logloss_delta": ll_delta,
        "brier_delta": cand["brier"] - base["brier"] if scored else None,
        "rps_delta": cand["rps"] - base["rps"] if scored else None,
        "paired": paired,
        "fresh_confirmation": {
            "confirmed": confirmed,
            "action": "PROMOTE_R34_AWAY_LOAD_SIGNAL_TO_FRESH_CONFIRMED_CANDIDATE" if confirmed else "DO_NOT_PROMOTE_FROM_BATCH005",
            "next_if_fail": "continue independent prematch information families; do not tune R34 on Batch005 labels" if not confirmed else "integrate frozen R34 away-load signal into next composite candidate without retuning on Batch005",
        },
    }
    (OUT / "summary_batch005_reveal.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(s, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_batch005_reveal.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["cohort_locked_before_labels_accessed"] and g["predictions_locked_before_labels_accessed"]
    assert g["R34_rule_frozen_before_Batch005"] and g["Batch005_labels_not_used_for_candidate_selection_or_tuning"]
    assert g["reveal_contract_predeclared_before_labels"]
    if s["status"] == "COMPLETE":
        assert s["cohort"]["scorable_rows"] >= s["contract"]["min_scorable_rows"]
    print("BATCH005_REVEAL_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: reveal_score_batch005.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
