#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42I_DIR = HERE.parent / "r42i_player_technical_replication_prev20k"
sys.path.insert(0, str(R42I_DIR))
import run_r42i_prev20k as r42i  # noqa: E402

# Frozen before opening/scoring any of these older blocks.
BLOCK_SKIPS = (40000, 60000, 80000)
BLOCK_SIZE = 20000
MIN_INDIVIDUAL_PASSES = 2
MIN_POOLED_OOS = 3 * r42i.MIN_OOS_MATCHES
MIN_POOLED_GAIN_HITS = 1
MIN_POOLED_POSITIVE_TIME_BLOCKS = 2
MAX_POOLED_NEGATIVE_TIME_BLOCKS = 1
MAX_POOLED_PROPER_SCORE_DELTA = 0.0


def block_label(skip: int) -> str:
    return f"valid_sorted_[-{skip + BLOCK_SIZE}:-{skip}]"


def run_block(skip: int):
    scratch = HERE / "scratch" / f"skip_{skip}"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    # R42I is used only as a frozen computational engine. Its statistical/model
    # spec is unchanged; only the pre-registered disjoint source slice and local
    # output directory are changed.
    r42i.SKIP_NEWEST_N = skip
    r42i.SNAPSHOT_N = BLOCK_SIZE
    r42i.HERE = scratch
    r42i.OUT = scratch / "results"
    r42i.run()

    summary_path = r42i.OUT / "summary_r42i_player_technical_replication_prev20k.json"
    records_path = r42i.OUT / "oos_records_r42i.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = [json.loads(x) for x in records_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    summary["r42j_block_provenance"] = {
        "skip_newest_n": skip,
        "block_size": BLOCK_SIZE,
        "selection": block_label(skip),
        "disjoint_from_latest_r9b_20k": True,
        "disjoint_from_r42i_prev20k": True,
    }
    return summary, records


def run():
    summaries = []
    all_records = []
    seen_fixtures = set()
    for skip in BLOCK_SKIPS:
        s, records = run_block(skip)
        ids = {str(x["fixture_id"]) for x in records}
        if seen_fixtures & ids:
            raise RuntimeError(f"R42J block overlap detected for skip={skip}")
        seen_fixtures |= ids
        summaries.append(s)
        for x in records:
            all_records.append({**x, "r42j_skip_newest_n": skip})

    baseline = [x["baseline_bridge"] for x in all_records]
    candidate = [x["candidate_bridge"] for x in all_records]
    bm = r42i.r33.metrics(baseline)
    cm = r42i.r33.metrics(candidate)
    pair = r42i.r33.paired_blocks(baseline, candidate)
    delta = {
        "gain_hits": int(cm["hits"] - bm["hits"]),
        "gain_top1_pp": 100.0 * float(cm["top1_accuracy"] - bm["top1_accuracy"]),
        "logloss_delta": float(cm["logloss"] - bm["logloss"]),
        "brier_delta": float(cm["brier"] - bm["brier"]),
        "rps_delta": float(cm["rps"] - bm["rps"]),
    }
    boot = r42i.r42h.paired_bootstrap(all_records)
    individual_passes = sum(bool(x["gate"]["passed"]) for x in summaries)
    pooled_pass = bool(
        len(all_records) >= MIN_POOLED_OOS
        and delta["gain_hits"] >= MIN_POOLED_GAIN_HITS
        and pair["positive_time_blocks"] >= MIN_POOLED_POSITIVE_TIME_BLOCKS
        and pair["negative_time_blocks"] <= MAX_POOLED_NEGATIVE_TIME_BLOCKS
        and delta["logloss_delta"] <= MAX_POOLED_PROPER_SCORE_DELTA
        and delta["brier_delta"] <= MAX_POOLED_PROPER_SCORE_DELTA
        and delta["rps_delta"] <= MAX_POOLED_PROPER_SCORE_DELTA
    )
    battery_pass = bool(individual_passes >= MIN_INDIVIDUAL_PASSES and pooled_pass)

    blocks = []
    for skip, s in zip(BLOCK_SKIPS, summaries):
        m = s["main_oos"]
        blocks.append({
            "skip_newest_n": skip,
            "selection": block_label(skip),
            "source_first_date": s["source"]["first_date"],
            "source_last_date": s["source"]["last_date"],
            "n": m["n"],
            "baseline_hits": m["baseline_bridge"]["hits"],
            "candidate_hits": m["candidate_bridge_plus_technical"]["hits"],
            "gain_hits": m["gain_hits"],
            "gain_top1_pp": m["gain_top1_pp"],
            "logloss_delta": m["logloss_delta"],
            "brier_delta": m["brier_delta"],
            "rps_delta": m["rps_delta"],
            "positive_time_blocks": m["paired"]["positive_time_blocks"],
            "negative_time_blocks": m["paired"]["negative_time_blocks"],
            "individual_gate_passed": bool(s["gate"]["passed"]),
            "technical_known_share_mean": s["technical_translation"]["oos_tech_known_share_min_mean"],
        })

    result = {
        "schema_version": "football3-r42j-r42h-v1-three-block-replication-battery-v1",
        "status": "COMPLETE",
        "classification": "PRE_REGISTERED_THREE_DISJOINT_OLDER_20K_RETROSPECTIVE_REPLICATION_BATTERY",
        "formal_weight": 0,
        "question": "Does frozen R42H V1 retain 1X2 value across three additional non-overlapping older 20k eras without any tuning?",
        "governance": {
            "r42h_v1_spec_unchanged": True,
            "r42i_engine_sha_expected": "17155fe709877801299b1b8f2d965cb11da4cb81c5d2fc5733cf7536bbe2faad",
            "block_skips_frozen_before_label_inspection": list(BLOCK_SKIPS),
            "block_size": BLOCK_SIZE,
            "parameter_search": False,
            "all_blocks_disjoint": True,
            "latest_r9b_20k_excluded": True,
            "r42i_prev20k_excluded": True,
            "automatic_promotion": False,
        },
        "blocks": blocks,
        "pooled": {
            "n": len(all_records),
            "baseline_bridge": bm,
            "candidate_bridge_plus_technical": cm,
            **delta,
            "paired": pair,
            "paired_bootstrap": boot,
        },
        "battery_gate": {
            "minimum_individual_passes": MIN_INDIVIDUAL_PASSES,
            "individual_passes": individual_passes,
            "minimum_pooled_oos": MIN_POOLED_OOS,
            "minimum_pooled_gain_hits": MIN_POOLED_GAIN_HITS,
            "minimum_pooled_positive_time_blocks": MIN_POOLED_POSITIVE_TIME_BLOCKS,
            "maximum_pooled_negative_time_blocks": MAX_POOLED_NEGATIVE_TIME_BLOCKS,
            "maximum_pooled_proper_score_delta": MAX_POOLED_PROPER_SCORE_DELTA,
            "pooled_gate_passed": pooled_pass,
            "passed": battery_pass,
            "action": "LOCK_R42H_V1_FOR_TRUE_FORWARD_CONFIRMATION_ONLY" if battery_pass else "DO_NOT_TREAT_R42H_V1_AS_ERA_STABLE",
        },
        "limitations": [
            "All three blocks are retrospective; this battery strengthens era replication but is not prospective evidence.",
            "The battery gate and exact block slices were frozen after R42I but before any of these three blocks were scored.",
            "The R42I computational engine is reused unchanged; R42J changes only the pre-registered disjoint historical slice and aggregates results.",
            "Provider collection timestamps for player technical rows remain not independently hash-bound per historical row.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42j_multiblock_replication.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (OUT / "pooled_oos_records_r42j.jsonl").open("w", encoding="utf-8") as f:
        for x in all_records:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    shutil.rmtree(HERE / "scratch", ignore_errors=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42j_multiblock_replication.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["r42h_v1_spec_unchanged"] is True
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["block_skips_frozen_before_label_inspection"] == list(BLOCK_SKIPS)
    assert len(d["blocks"]) == len(BLOCK_SKIPS)
    assert d["pooled"]["n"] == sum(x["n"] for x in d["blocks"])
    assert len({x["selection"] for x in d["blocks"]}) == len(BLOCK_SKIPS)
    print("R42J_MULTIBLOCK_REPLICATION_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42j_multiblock.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
