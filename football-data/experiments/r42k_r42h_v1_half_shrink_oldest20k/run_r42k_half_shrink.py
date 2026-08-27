#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42I_DIR = HERE.parent / "r42i_player_technical_replication_prev20k"
sys.path.insert(0, str(R42I_DIR))
import run_r42i_prev20k as r42i  # noqa: E402

# Frozen before opening/scoring this last full 20k untouched historical block.
SKIP_NEWEST_N = 100000
BLOCK_SIZE = 20000
TECH_DELTA_ALPHA = 0.50
MIN_OOS_MATCHES = r42i.MIN_OOS_MATCHES
MIN_GAIN_HITS = 1
MIN_POSITIVE_BLOCKS = 2
MAX_NEGATIVE_BLOCKS = 1
MAX_PROPER_SCORE_DELTA = 0.0


def blend_prob(base_p: dict, tech_p: dict, alpha: float) -> dict:
    b = np.clip(np.asarray([base_p["p_home"], base_p["p_draw"], base_p["p_away"]], dtype=float), 1e-12, 1.0)
    c = np.clip(np.asarray([tech_p["p_home"], tech_p["p_draw"], tech_p["p_away"]], dtype=float), 1e-12, 1.0)
    # Geometric interpolation in log-probability space. alpha=0 is baseline,
    # alpha=1 is full R42H V1. alpha=0.5 is a fixed no-fit shrinkage midpoint.
    z = np.exp((1.0 - alpha) * np.log(b) + alpha * np.log(c))
    z = z / z.sum()
    top1 = int(np.argmax(z))
    return {"p_home": float(z[0]), "p_draw": float(z[1]), "p_away": float(z[2]), "top1": top1}


def run():
    scratch = HERE / "scratch"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    # Frozen R42I/R42H computational engine; only source slice is changed.
    r42i.SKIP_NEWEST_N = SKIP_NEWEST_N
    r42i.SNAPSHOT_N = BLOCK_SIZE
    r42i.HERE = scratch
    r42i.OUT = scratch / "results"
    r42i.run()

    inner_summary = json.loads((r42i.OUT / "summary_r42i_player_technical_replication_prev20k.json").read_text(encoding="utf-8"))
    raw_records = [json.loads(x) for x in (r42i.OUT / "oos_records_r42i.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if not raw_records:
        raise RuntimeError("R42K produced no OOS records")

    records = []
    for x in raw_records:
        base = x["baseline_bridge"]
        full = x["candidate_bridge"]
        mid_p = blend_prob(base["P"], full["P"], TECH_DELTA_ALPHA)
        records.append({
            "date": x["date"],
            "fixture_id": x["fixture_id"],
            "y": x["y"],
            "baseline_bridge": base,
            "full_r42h_v1": full,
            "candidate_bridge": {"date": x["date"], "y": x["y"], "P": mid_p},
            "tech_known_share_min": x.get("tech_known_share_min"),
            "mean_roster_shock": x.get("mean_roster_shock"),
            "max_roster_shock": x.get("max_roster_shock"),
        })

    baseline = [x["baseline_bridge"] for x in records]
    full = [x["full_r42h_v1"] for x in records]
    shrunk = [x["candidate_bridge"] for x in records]
    bm = r42i.r33.metrics(baseline)
    fm = r42i.r33.metrics(full)
    sm = r42i.r33.metrics(shrunk)
    pair = r42i.r33.paired_blocks(baseline, shrunk)
    delta = {
        "gain_hits": int(sm["hits"] - bm["hits"]),
        "gain_top1_pp": 100.0 * float(sm["top1_accuracy"] - bm["top1_accuracy"]),
        "logloss_delta": float(sm["logloss"] - bm["logloss"]),
        "brier_delta": float(sm["brier"] - bm["brier"]),
        "rps_delta": float(sm["rps"] - bm["rps"]),
    }
    full_delta = {
        "gain_hits": int(fm["hits"] - bm["hits"]),
        "gain_top1_pp": 100.0 * float(fm["top1_accuracy"] - bm["top1_accuracy"]),
        "logloss_delta": float(fm["logloss"] - bm["logloss"]),
        "brier_delta": float(fm["brier"] - bm["brier"]),
        "rps_delta": float(fm["rps"] - bm["rps"]),
    }
    boot = r42i.r42h.paired_bootstrap(records)
    passed = bool(
        len(records) >= MIN_OOS_MATCHES
        and delta["gain_hits"] >= MIN_GAIN_HITS
        and pair["positive_time_blocks"] >= MIN_POSITIVE_BLOCKS
        and pair["negative_time_blocks"] <= MAX_NEGATIVE_BLOCKS
        and delta["logloss_delta"] <= MAX_PROPER_SCORE_DELTA
        and delta["brier_delta"] <= MAX_PROPER_SCORE_DELTA
        and delta["rps_delta"] <= MAX_PROPER_SCORE_DELTA
    )

    known = np.asarray([x["tech_known_share_min"] for x in records if x.get("tech_known_share_min") is not None], dtype=float)
    source = inner_summary["source"]
    result = {
        "schema_version": "football3-r42k-r42h-v1-fixed-half-shrink-oldest20k-confirm-v1",
        "status": "COMPLETE",
        "classification": "PRE_REGISTERED_FIXED_SHRINKAGE_CONFIRMATION_ON_LAST_UNTOUCHED_FULL_20K_BLOCK",
        "formal_weight": 0,
        "question": "Does a fixed alpha=0.5 geometric shrinkage of the frozen R42H V1 technical probability delta preserve its proper-score gains while improving Top-1 on the last untouched full 20k historical block?",
        "governance": {
            "r42h_v1_model_and_features_unchanged": True,
            "r42i_engine_sha_expected": "17155fe709877801299b1b8f2d965cb11da4cb81c5d2fc5733cf7536bbe2faad",
            "source_slice_frozen_before_label_inspection": "valid_sorted_[-120000:-100000]",
            "skip_newest_n": SKIP_NEWEST_N,
            "block_size": BLOCK_SIZE,
            "technical_delta_alpha": TECH_DELTA_ALPHA,
            "alpha_selected_without_this_block_labels": True,
            "parameter_search_on_this_block": False,
            "automatic_promotion": False,
        },
        "source": {
            "full_valid_xg_rows": source["full_valid_xg_rows"],
            "snapshot_rows": source["snapshot_rows"],
            "selection": "valid_sorted_[-120000:-100000]",
            "first_date": source["first_date"],
            "last_date": source["last_date"],
            "fixtures_sha256": source["fixtures_sha256"],
            "match_stats_sha256": source["match_stats_sha256"],
            "fixture_players_sha256": source["fixture_players_sha256"],
            "fixture_players_stats_flat_sha256": source["fixture_players_stats_flat_sha256"],
            "model_train_rows": source["model_train_rows"],
            "model_train_end_date": source["model_train_end_date"],
            "detected_gap_openers": source["detected_gap_openers"],
            "two_sided_candidate_matches_before_oos_cut": source["two_sided_candidate_matches_before_oos_cut"],
            "overlap_with_latest100k_used_blocks": 0,
        },
        "technical_coverage": {
            "oos_tech_known_share_min_mean": float(np.mean(known)) if len(known) else None,
            "oos_tech_known_share_min_ge_0_8": int(np.sum(known >= 0.8)) if len(known) else 0,
        },
        "main_oos": {
            "n": len(records),
            "baseline_bridge": bm,
            "full_r42h_v1_diagnostic_only": fm,
            "full_r42h_v1_minus_baseline": full_delta,
            "candidate_half_shrink": sm,
            **delta,
            "paired": pair,
            "paired_bootstrap": boot,
        },
        "gate": {
            "min_oos_matches": MIN_OOS_MATCHES,
            "min_gain_hits": MIN_GAIN_HITS,
            "min_positive_blocks": MIN_POSITIVE_BLOCKS,
            "max_negative_blocks": MAX_NEGATIVE_BLOCKS,
            "max_logloss_delta": MAX_PROPER_SCORE_DELTA,
            "max_brier_delta": MAX_PROPER_SCORE_DELTA,
            "max_rps_delta": MAX_PROPER_SCORE_DELTA,
            "passed": passed,
            "action": "FREEZE_HALF_SHRINK_FOR_TRUE_FORWARD_CONFIRMATION_ONLY" if passed else "REJECT_HALF_SHRINK_DO_NOT_TUNE_ON_THIS_CONSUMED_BLOCK",
        },
        "limitations": [
            "This is the last untouched full 20k block available inside the frozen 127,998-row xG source; it is retrospective, not prospective.",
            "The alpha=0.5 shrinkage rule and gate were frozen before this block was scored.",
            "The full R42H V1 result is diagnostic only and cannot be selected post hoc over the half-shrink candidate.",
            "If this gate fails, this block is consumed and must not be used to tune a replacement rule.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42k_half_shrink_oldest20k.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (OUT / "oos_records_r42k.jsonl").open("w", encoding="utf-8") as f:
        for x in records:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    shutil.rmtree(scratch, ignore_errors=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42k_half_shrink_oldest20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["technical_delta_alpha"] == 0.5
    assert d["governance"]["parameter_search_on_this_block"] is False
    assert d["governance"]["source_slice_frozen_before_label_inspection"] == "valid_sorted_[-120000:-100000]"
    assert d["source"]["snapshot_rows"] == 20000
    assert d["source"]["overlap_with_latest100k_used_blocks"] == 0
    assert d["main_oos"]["n"] > 0
    print("R42K_HALF_SHRINK_OLDEST20K_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42k_half_shrink.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
