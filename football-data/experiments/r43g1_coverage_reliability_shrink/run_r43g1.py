#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
if str(F5_DIR) not in sys.path:
    sys.path.insert(0, str(F5_DIR))
import run_r43f5 as f5  # noqa: E402

SOURCE_R43F5_HEAD = "0140944ee387e4328c14eef0837481d800dfcc38"
TECH_ALPHA = 0.50
MIN_POSITIVE_LL_BLOCKS = 3
MAX_NEGATIVE_LL_BLOCKS = 1


def arr(p: dict) -> np.ndarray:
    return np.clip(np.asarray([p["p_home"], p["p_draw"], p["p_away"]], dtype=float), 1e-12, 1.0)


def reliability(x: dict) -> tuple[float, float]:
    cf = x["base_cf"]
    vals = [
        float(cf.get("home_xi_certainty", 0.0)),
        float(cf.get("away_xi_certainty", 0.0)),
        float(cf.get("home_role_known_share", 0.0)),
        float(cf.get("away_role_known_share", 0.0)),
    ]
    vals = [min(1.0, max(0.0, v)) if np.isfinite(v) else 0.0 for v in vals]
    r_ctx = float(min(vals))
    t = float(x.get("context_weighted_known", 0.0))
    t = min(1.0, max(0.0, t)) if np.isfinite(t) else 0.0
    r_tech = float(min(r_ctx, t))
    return r_ctx, r_tech


def raw_records(split: list[dict]) -> list[dict]:
    out = []
    for x in split:
        p = f5.r9.decorate(arr(x["raw"]))
        out.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": p})
    return out


def reliability_transport(split: list[dict], no: list[dict], full: list[dict]) -> tuple[list[dict], list[dict]]:
    out = []
    no_rel = []
    for x, n, t in zip(split, no, full):
        if not (x["fixture_id"] == n["fixture_id"] == t["fixture_id"]):
            raise RuntimeError("paired fixture drift")
        rctx, rtech = reliability(x)
        pr = arr(x["raw"])
        pn = arr(n["P"])
        pt = arr(t["P"])
        # Pre-registered decomposition: raw generator -> R40C context residual -> R43F5 technical residual.
        # Missing/uncertain context can only shrink a residual toward its lower-information parent; it never changes class direction by hand.
        zn = np.log(pr) + rctx * (np.log(pn) - np.log(pr))
        zn -= float(np.max(zn)); qn = np.exp(zn); qn /= float(np.sum(qn))
        z = np.log(pr) + rctx * (np.log(pn) - np.log(pr)) + TECH_ALPHA * rtech * (np.log(pt) - np.log(pn))
        z -= float(np.max(z)); q = np.exp(z); q /= float(np.sum(q))
        no_rel.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": f5.r9.decorate(qn)})
        out.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": f5.r9.decorate(q)})
    return no_rel, out


def rel_diag(rows: list[dict]) -> dict:
    a = np.asarray([reliability(x)[0] for x in rows], dtype=float)
    b = np.asarray([reliability(x)[1] for x in rows], dtype=float)
    return {
        "n": len(rows),
        "context_mean": float(np.mean(a)), "context_p10": float(np.quantile(a, .10)), "context_p25": float(np.quantile(a, .25)), "context_median": float(np.median(a)),
        "technical_mean": float(np.mean(b)), "technical_p10": float(np.quantile(b, .10)), "technical_p25": float(np.quantile(b, .25)), "technical_median": float(np.median(b)),
        "context_lt_0_50": int(np.sum(a < .50)), "context_lt_0_75": int(np.sum(a < .75)),
    }


def subset_pair(base: list[dict], cand: list[dict], source: list[dict], pred) -> dict:
    ib = [b for b, x in zip(base, source) if pred(reliability(x)[0])]
    ic = [c for c, x in zip(cand, source) if pred(reliability(x)[0])]
    if not ib:
        return {"n": 0}
    return {
        "baseline": f5.f3.enriched_metrics(ib),
        "candidate": f5.f3.enriched_metrics(ic),
        "candidate_minus_baseline": f5.compare(ib, ic),
    }


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = HERE / "scratch"
    shutil.rmtree(scratch, ignore_errors=True); scratch.mkdir(parents=True, exist_ok=True)
    r42i = f5.r42i
    old_here, old_out, old_skip, old_n = r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N
    r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = scratch, scratch / "results", f5.SKIP_NEWEST_N, f5.BLOCK_SIZE
    try:
        rows, snap_meta = r42i.freeze_previous_20k()
    finally:
        r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = old_here, old_out, old_skip, old_n
    if len(rows) != f5.BLOCK_SIZE:
        raise RuntimeError("source block length drift")

    bp, cp, bt, ct, lineup_meta = f5.make_lineup_probabilities(rows)
    records, tech_meta = f5.build_outcome_records(rows, bp, cp, bt, ct, lineup_meta["lineup_train_end"])
    records = sorted(records, key=lambda x: (x["date"], x["fixture_id"]))
    b1 = f5.f3.boundary_date_safe(records, f5.OUTCOME_TRAIN_TARGET)
    b2 = f5.f3.boundary_date_safe(records, b1 + f5.OUTCOME_VAL_TARGET)
    train, val, test = records[:b1], records[b1:b2], records[b2:]

    names = f5.BASE_NAMES + f5.TECH_NAMES
    m0 = f5.fit_records(train, "base_cf", f5.BASE_NAMES)
    mt = f5.fit_records(train, "context_weighted_cf", names)

    def score(split):
        no = f5.score_model(m0, split, "base_cf", f5.BASE_NAMES)
        full = f5.score_model(mt, split, "context_weighted_cf", names)
        baseline = f5.half_records(no, full)
        nr, candidate = reliability_transport(split, no, full)
        return {"raw": raw_records(split), "no": no, "full": full, "baseline": baseline, "no_reliable": nr, "candidate": candidate}

    vs, ts = score(val), score(test)
    vd = f5.compare(vs["baseline"], vs["candidate"])
    td = f5.compare(ts["baseline"], ts["candidate"])
    no_delta = f5.compare(ts["no"], ts["no_reliable"])
    blocks = f5.f3.paired_time_blocks(ts["baseline"], ts["candidate"])
    pos = sum(x["delta_logloss"] < 0 for x in blocks)
    neg = sum(x["delta_logloss"] > 0 for x in blocks)
    passed = bool(
        vd["logloss"] < 0 and vd["brier"] < 0 and vd["rps"] < 0
        and td["hits"] >= 0 and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
        and pos >= MIN_POSITIVE_LL_BLOCKS and neg <= MAX_NEGATIVE_LL_BLOCKS
    )
    result = {
        "schema_version": "football3-r43g1-coverage-reliability-shrink-v1",
        "status": "COMPLETE", "formal_weight": 0,
        "classification": "DEVELOPMENT_PREDEFINED_COVERAGE_RELIABILITY_RESIDUAL_SHRINK_ON_R43F5",
        "question": "Can low PIT lineup/player coverage be treated as uncertainty rather than directional evidence by shrinking context residuals continuously toward the lower-information generator?",
        "governance": {
            "source_r43f5_head": SOURCE_R43F5_HEAD,
            "source_slice": f5.SELECTION,
            "source_previously_consumed": True,
            "formula_fixed_before_scoring": True,
            "parameter_search": False, "threshold_search": False, "feature_search_after_test": False,
            "reliability_formula": "r_ctx=min(home_xi_certainty,away_xi_certainty,home_role_known_share,away_role_known_share); r_tech=min(r_ctx,context_weighted_known_share)",
            "context_residual_parent": "raw R9 generator",
            "technical_residual_parent": "R40C context model",
            "tech_alpha": TECH_ALPHA,
            "no_manual_outcome_override": True, "no_manual_draw_override": True, "no_draw_threshold": True, "no_draw_class_weight": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "R43F5 context-weighted technical half-shrink",
            "candidate": "log P(raw) + r_ctx*[log P(R40C)-log P(raw)] + 0.5*r_tech*[log P(full-tech)-log P(R40C)]",
            "interpretation": "coverage changes confidence in a learned residual only; it never selects home/draw/away",
            "gate": "validation proper scores all improve; test hits nonworse and LogLoss/Brier/RPS all improve; >=3/4 test LogLoss blocks improve",
        },
        "source": {"first_date": rows[0]["date"], "last_date": rows[-1]["date"], "snapshot_rows": len(rows), "fixtures_sha256": snap_meta["fixtures_sha256"], **tech_meta},
        "split": {"train_n": len(train), "val_n": len(val), "test_n": len(test), "train_dates": [train[0]["date"], train[-1]["date"]], "val_dates": [val[0]["date"], val[-1]["date"]], "test_dates": [test[0]["date"], test[-1]["date"]]},
        "reliability": {"validation": rel_diag(val), "test": rel_diag(test)},
        "validation": {"baseline": f5.f3.enriched_metrics(vs["baseline"]), "candidate": f5.f3.enriched_metrics(vs["candidate"]), "candidate_minus_baseline": vd},
        "test": {
            "raw": f5.f3.enriched_metrics(ts["raw"]), "r40c": f5.f3.enriched_metrics(ts["no"]), "baseline_r43f5": f5.f3.enriched_metrics(ts["baseline"]), "candidate": f5.f3.enriched_metrics(ts["candidate"]),
            "candidate_minus_baseline": td, "r40c_reliable_minus_r40c": no_delta,
            "time_blocks": blocks, "positive_logloss_blocks": pos, "negative_logloss_blocks": neg,
            "low_reliability_lt_0_50": subset_pair(ts["baseline"], ts["candidate"], test, lambda r: r < .50),
            "mid_reliability_0_50_0_75": subset_pair(ts["baseline"], ts["candidate"], test, lambda r: .50 <= r < .75),
            "high_reliability_ge_0_75": subset_pair(ts["baseline"], ts["candidate"], test, lambda r: r >= .75),
        },
        "gate": {"passed": passed, "action": "FREEZE_R43G1_FORMULA_FOR_INDEPENDENT_REPLICATION" if passed else "DO_NOT_PROMOTE_R43G1_AND_DO_NOT_RETUNE_ON_THIS_TEST"},
        "limitations": ["This block was previously used in architecture development, so formal_weight remains zero.", "If the formula passes here it still requires a disjoint frozen replication or true forward evaluation before promotion."],
    }
    p = OUT / "summary_r43g1_coverage_reliability_shrink.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "reliability": result["reliability"], "validation_delta": vd, "test_delta": td, "gate": result["gate"]}, indent=2))
    return result


if __name__ == "__main__":
    run()
