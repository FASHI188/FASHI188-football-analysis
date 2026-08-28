#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
if str(F5_DIR) not in sys.path:
    sys.path.insert(0, str(F5_DIR))
import run_r43f5 as f5  # noqa: E402

SOURCE_F5_BRANCH = "football3/r43f5-probability-weighted-technical-mixture"
SOURCE_F5_RUNNER_BLOB = "83a2d20ec0e38b6fca61da7751b7a18846414b13"
SOURCE_F5_RESULT_BLOB = "4a320dfa28aca8e1756555152f41a6a8a5e34bc2"
SOURCE_M1_RESULT_BLOB = "29af3f09d5bf6d76ce60f6a2b85e89a3e3fa3697"
SKIP_NEWEST_N = 100000
BLOCK_SIZE = 20000
SELECTION = "valid_sorted_[-120000:-100000]"
TECH_ALPHA = 0.5
CACHE = HERE / "source_cache"
CACHE.mkdir(parents=True, exist_ok=True)

# Engineering-only source caching/retry. Scientific code remains the frozen R43F5 runner.
_original_r42i_download = f5.r42i.download
_original_f0_download = f5.f0.download
_original_download_stats = f5.r42h.download_stats
_cached_stats_path: Path | None = None


def _cache_path(url: str) -> Path:
    suffix = ".parquet" if ".parquet" in url else ".bin"
    return CACHE / (hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix)


def _retry_call(fn, *args):
    last: Exception | None = None
    for attempt in range(7):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429 or attempt == 6:
                raise
            time.sleep(min(90, 5 * (2 ** attempt)))
    raise RuntimeError(f"download retries exhausted: {last}")


def cached_r42i_download(url: str, path: Path, user_agent: str) -> None:
    path = Path(path)
    cache = _cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and cache.stat().st_size > 0:
        shutil.copyfile(cache, path)
        return
    _retry_call(_original_r42i_download, url, path, user_agent)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"empty r42i source: {url}")
    shutil.copyfile(path, cache)


def cached_f0_download(url: str, path: Path) -> None:
    path = Path(path)
    cache = _cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    if cache.exists() and cache.stat().st_size > 0:
        shutil.copyfile(cache, path)
        return
    _retry_call(_original_f0_download, url, path)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"empty f0 source: {url}")
    shutil.copyfile(path, cache)


def cached_download_stats() -> Path:
    global _cached_stats_path
    if _cached_stats_path is not None and _cached_stats_path.exists() and _cached_stats_path.stat().st_size > 0:
        return _cached_stats_path
    p = Path(_retry_call(_original_download_stats))
    if not p.exists() or p.stat().st_size == 0:
        raise RuntimeError("download_stats returned missing/empty file")
    _cached_stats_path = p
    return p


f5.r42i.download = cached_r42i_download
f5.f0.download = cached_f0_download
f5.r42h.download_stats = cached_download_stats


def metric_delta(base: dict, cand: dict) -> dict:
    return {
        "hits": int(cand["hits"] - base["hits"]),
        "accuracy_pp": 100.0 * float(cand["top1_accuracy"] - base["top1_accuracy"]),
        "logloss": float(cand["logloss"] - base["logloss"]),
        "brier": float(cand["brier"] - base["brier"]),
        "rps": float(cand["rps"] - base["rps"]),
        "draw_binary_logloss": float(cand["draw_binary"]["logloss"] - base["draw_binary"]["logloss"]),
        "draw_binary_brier": float(cand["draw_binary"]["brier"] - base["draw_binary"]["brier"]),
    }


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    work = HERE / "scratch_block"
    shutil.rmtree(work, ignore_errors=True)
    (work / "results").mkdir(parents=True, exist_ok=True)

    old_here, old_out = f5.HERE, f5.OUT
    old_skip, old_sel, old_block = f5.SKIP_NEWEST_N, f5.SELECTION, f5.BLOCK_SIZE
    try:
        f5.HERE = work
        f5.OUT = work / "results"
        f5.SKIP_NEWEST_N = SKIP_NEWEST_N
        f5.SELECTION = SELECTION
        f5.BLOCK_SIZE = BLOCK_SIZE
        source = f5.run()
    finally:
        f5.HERE, f5.OUT = old_here, old_out
        f5.SKIP_NEWEST_N, f5.SELECTION, f5.BLOCK_SIZE = old_skip, old_sel, old_block

    if source["status"] != "COMPLETE" or source["formal_weight"] != 0:
        raise RuntimeError("unexpected frozen R43F5 source result")
    if source["source"]["selection"] != SELECTION or int(source["source"]["snapshot_rows"]) != BLOCK_SIZE:
        raise RuntimeError("source block drift")
    g = source["governance"]
    if g["parameter_search"] or g["feature_search"]:
        raise RuntimeError("scientific contract drift")
    if float(g["tech_alpha"]) != TECH_ALPHA or float(g["mixture_temperature_or_power"]) != 1.0:
        raise RuntimeError("frozen technical mixture drift")

    val_base = source["validation"]["no"]
    val_cand = source["validation"]["base_weighted_half"]
    test_base = source["test"]["no_tech"]
    test_cand = source["test"]["base_weighted_half"]
    vd = metric_delta(val_base, val_cand)
    td = metric_delta(test_base, test_cand)

    val_pass = bool(vd["hits"] >= 0 and vd["logloss"] < 0 and vd["brier"] < 0 and vd["rps"] < 0)
    test_pass = bool(
        td["hits"] >= 0
        and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
        and td["draw_binary_logloss"] < 0 and td["draw_binary_brier"] < 0
    )
    passed = bool(val_pass and test_pass)

    out = {
        "schema_version": "football3-r43m2-base-weighted-tech-oldest20k-confirmation-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "POST_HYPOTHESIS_OLDER20K_COMPATIBILITY_OF_FROZEN_BASE_PSTART_WEIGHTED_TECHNICAL_LAYER",
        "question": "After context reweighting failed to replicate, does the already-existing R43F5 base-P(start)-weighted technical half-layer retain its three-class and draw proper-score gains on an older disjoint 20k block without tuning?",
        "governance": {
            "hypothesis_formed_after_r43m1": True,
            "source_f5_branch": SOURCE_F5_BRANCH,
            "source_f5_runner_blob": SOURCE_F5_RUNNER_BLOB,
            "source_f5_result_blob": SOURCE_F5_RESULT_BLOB,
            "source_m1_result_blob": SOURCE_M1_RESULT_BLOB,
            "selection_locked_before_scoring": SELECTION,
            "candidate": "R43F5 base_weighted_half",
            "candidate_definition": "base P(start) distribution -> P(start)-weighted R42H technical context -> same technical translation -> fixed alpha=0.5 geometric half-shrink versus no-tech",
            "context_reweighting_used": False,
            "parameter_search": False,
            "feature_search": False,
            "threshold_search": False,
            "alpha_search": False,
            "tech_alpha": TECH_ALPHA,
            "mixture_temperature_or_power": 1.0,
            "manual_draw_override": False,
            "draw_threshold": False,
            "draw_class_weight": False,
            "result_driven_retuning": False,
            "formal_promotion_allowed": False,
            "block_pristine_status_asserted": False,
            "transport_cache_changes_scientific_logic": False,
        },
        "discovery_audit_from_three_consumed_blocks": {
            "test_n": 14027,
            "hits": 23,
            "accuracy_pp": 0.1639694874171241,
            "logloss": -0.0018540027900769546,
            "brier": -0.0013531459785276032,
            "rps": -0.0006153729628227464,
            "interpretation": "motivates this older-block compatibility check only; not formal evidence",
        },
        "source": {
            "selection": SELECTION,
            "skip_newest_n": SKIP_NEWEST_N,
            "snapshot_rows": source["source"]["snapshot_rows"],
            "first_date": source["source"]["first_date"],
            "last_date": source["source"]["last_date"],
            "outcome_split": source["outcome_split"],
            "technical_coverage_test": source["technical_coverage_test"],
        },
        "validation": {
            "no_tech": val_base,
            "base_weighted_half": val_cand,
            "base_weighted_minus_no_tech": vd,
        },
        "test": {
            "no_tech": test_base,
            "base_weighted_half": test_cand,
            "base_weighted_minus_no_tech": td,
        },
        "confirmation_gate": {
            "validation_hits_nonnegative": vd["hits"] >= 0,
            "validation_proper_scores_all_improve": vd["logloss"] < 0 and vd["brier"] < 0 and vd["rps"] < 0,
            "test_hits_nonnegative": td["hits"] >= 0,
            "test_proper_scores_all_improve": td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0,
            "test_draw_binary_scores_both_improve": td["draw_binary_logloss"] < 0 and td["draw_binary_brier"] < 0,
            "passed": passed,
            "action": "FREEZE_BASE_WEIGHTED_TECH_LAYER_FOR_TRUE_FORWARD_IMPLEMENTATION" if passed else "BASE_WEIGHTED_TECH_NOT_STABLE_ENOUGH_DO_NOT_RETUNE_ON_THIS_BLOCK",
        },
        "limitations": [
            "The architecture choice was motivated after observing R43M1, so formal_weight remains zero even though this source block is disjoint from the three discovery blocks.",
            "No claim is made that this older block was pristine to all prior project research.",
            "A pass only justifies a new prospectively frozen implementation; it does not itself promote the model.",
        ],
    }
    p = OUT / "summary_r43m2_base_weighted_tech_oldest20k_confirmation.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    p = OUT / "summary_r43m2_base_weighted_tech_oldest20k_confirmation.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["selection_locked_before_scoring"] == SELECTION
    assert g["candidate"] == "R43F5 base_weighted_half"
    assert g["context_reweighting_used"] is False
    assert g["parameter_search"] is False and g["feature_search"] is False and g["threshold_search"] is False and g["alpha_search"] is False
    assert float(g["tech_alpha"]) == 0.5 and float(g["mixture_temperature_or_power"]) == 1.0
    assert g["manual_draw_override"] is False and g["draw_threshold"] is False and g["draw_class_weight"] is False
    assert g["result_driven_retuning"] is False and g["formal_promotion_allowed"] is False
    assert d["source"]["selection"] == SELECTION and int(d["source"]["snapshot_rows"]) == BLOCK_SIZE
    print("R43M2 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(cmd)
