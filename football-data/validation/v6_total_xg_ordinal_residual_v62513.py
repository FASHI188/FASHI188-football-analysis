#!/usr/bin/env python3
"""V6.25.13 strict-PIT xG ordinal residual challenger for exact total goals.

Research only; formal_weight=0.

Why this challenger exists
--------------------------
V6.24.2 diagnosed a decision-level modal collapse: aggregate 0-7+ probabilities are
not grossly miscalibrated, but per-match Top-1 is concentrated around two goals.
V6.25.8/10/11 showed that shot-only residuals and an unconstrained multinomial head
cannot reliably solve this. V6.18.9 now provides an immutable, hash-bound, label-free
pre-match Understat state panel (xG/xGA/npxG/npxGA/PPDA/deep/xPTS) for five leagues.

This model respects the ordinal structure of total goals. For thresholds k=0..6 it fits
seven offset logistic residuals

    logit P(T > k | X) = logit P_formal(T > k) + residual_k(X)

using only frozen pre-match xG-state features. At prediction time the seven survival
probabilities are projected to a non-increasing sequence using deterministic PAV, then
converted back to P(T=0),...,P(T=6),P(T=7+). The candidate total marginal is mapped
back to the SAME formal joint score matrix, preserving P(score | total).

Chronology
----------
- fit: 2022/23 + 2023/24
- model/regularization/strength selection: 2024/25 only
- untouched holdout: 2025/26 exactly once
- no holdout result is used for fitting or selection
- selection requires Total RPS and exact-total log loss both nonworse than formal;
  only then is exact-total Top-1 maximized.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_market_offset_residual_v621 as binary  # noqa: E402
import v6_total_shot_residual_v6181 as shotbase  # noqa: E402
import v6_total_shot_residual_v6181a as shotfix  # noqa: E402
import v6_strict_daily_pit_rows_v6181c as strict  # noqa: E402
import v6_understat_xg_feature_panel_freeze_v6189 as panelmod  # noqa: E402
import v6_total_shot_23split_v6256 as reweight  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _total_distribution  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_xg_ordinal_residual_v62513_status.json"
PANEL_STATUS = ROOT / "manifests" / "v6_understat_xg_feature_panel_freeze_v6189_status.json"
PANEL = ROOT / "models" / "challengers_v6189" / "understat_xg_prematch_panel_v6189.jsonl"

TRAIN_SEASONS = {"2022/23", "2023/24"}
VALID_SEASON = "2024/25"
HOLDOUT_SEASON = "2025/26"
THRESHOLDS = tuple(range(7))
L2_GRID = (1.0, 10.0, 100.0, 1000.0)
ALPHA_GRID = (0.0, 0.25, 0.50, 0.75, 1.0)
EPS = 1e-12
PROPER_TOL = 1e-12


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _panel_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(record["competition_id"]),
        str(record["season"]),
        str(record["platform_date"]),
        str(record["platform_home_team"]),
        str(record["platform_away_team"]),
    )


def _load_panel() -> tuple[dict[tuple[str, str, str, str, str], dict[str, Any]], dict[str, Any]]:
    if not PANEL_STATUS.exists() or not PANEL.exists():
        raise PlatformError("V6.18.9 frozen xG panel/status missing")
    status = load_json(PANEL_STATUS)
    raw = PANEL.read_bytes()
    if status.get("status") != "PASS" or status.get("xg_model_research_feature_gate") != "PASS":
        raise PlatformError("V6.18.9 xG research feature gate not PASS")
    if status.get("panel_sha256") != _sha256(raw):
        raise PlatformError("V6.18.9 panel sha256 mismatch")
    lookup: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        key = _panel_key(record)
        if key in lookup:
            raise PlatformError(f"duplicate frozen panel identity: {key}")
        lookup[key] = record
    if len(lookup) != int(status.get("panel_rows") or 0):
        raise PlatformError("V6.18.9 panel row-count mismatch")
    return lookup, status


def _total_features(home: dict[str, Any], away: dict[str, Any]) -> list[float]:
    """Compact total-intensity feature vector from frozen pre-match states."""
    h = home["v"]
    a = away["v"]
    hxg = float(h["xg"])
    axg = float(a["xg"])
    hxga = float(h["xga"])
    axga = float(a["xga"])
    hn = float(h["npxg"])
    an = float(a["npxg"])
    hna = float(h["npxga"])
    ana = float(a["npxga"])
    h_edge = hxg - axga
    a_edge = axg - hxga
    hn_edge = hn - ana
    an_edge = an - hna
    return [
        1.0,
        hxg + axg,
        hxga + axga,
        hn + an,
        hna + ana,
        h_edge + a_edge,
        hn_edge + an_edge,
        abs(h_edge - a_edge),
        float(h["deep"]) + float(a["deep"]),
        float(h["deep_allowed"]) + float(a["deep_allowed"]),
        float(h["ppda"]) + float(a["ppda"]),
        float(h["oppda"]) + float(a["oppda"]),
        float(h["xpts"]) + float(a["xpts"]),
        abs(float(h["xpts"]) - float(a["xpts"])),
        min(int(home["n"]), int(away["n"])) / 20.0,
    ]


def _strict_rows_with_xg() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    panel, panel_status = _load_panel()
    raw, _ = shotbase.raw_stat_matches()
    shot_lookup, _ = shotfix.lagged_shot_lookup_fixed(raw)
    rows, strict_meta = strict.strict_formal_score_rows(shot_lookup)
    attached: list[dict[str, Any]] = []
    miss = Counter()
    by_domain = Counter()
    for row in rows:
        if row["competition_id"] not in panelmod.DOMAINS or row["season"] not in panelmod.SEASONS:
            continue
        day = panelmod.platform_day(row["date"])
        key = (
            str(row["competition_id"]), str(row["season"]), day,
            str(row["home_team"]), str(row["away_team"]),
        )
        p = panel.get(key)
        if p is None:
            miss[str(row["competition_id"])] += 1
            continue
        formal_total = _total_distribution(row["formal_matrix"])
        item = {
            **row,
            "date": day,
            "matrix": row["formal_matrix"],
            "formal_total": {b: float(formal_total[b]) for b in TOTAL_BUCKETS},
            "xg_x": _total_features(p["home_prematch_state"], p["away_prematch_state"]),
            "panel_home_n": int(p["home_prematch_state"]["n"]),
            "panel_away_n": int(p["away_prematch_state"]["n"]),
        }
        actual_total = int(row["home_goals"]) + int(row["away_goals"])
        for k in THRESHOLDS:
            pgt = sum(float(formal_total[b]) for b in TOTAL_BUCKETS if (7 if b == "7+" else int(b)) > k)
            item[f"thr_y_{k}"] = int(actual_total > k)
            item[f"thr_offset_{k}"] = binary._logit(pgt)
        attached.append(item)
        by_domain[str(row["competition_id"])] += 1
    audit = {
        "panel_status_sha256": hashlib.sha256(PANEL_STATUS.read_bytes()).hexdigest(),
        "panel_sha256": panel_status["panel_sha256"],
        "panel_rows": len(panel),
        "attached_rows": len(attached),
        "missing_panel_rows_by_domain": dict(sorted(miss.items())),
        "attached_rows_by_domain": dict(sorted(by_domain.items())),
        "strict_formal_meta": strict_meta,
    }
    return attached, audit


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[int, dict[str, Any]]:
    if len(rows) < 1000:
        raise PlatformError(f"insufficient xG train rows: {len(rows)}")
    return {
        k: binary._fit_offset_binary(rows, "xg_x", f"thr_y_{k}", f"thr_offset_{k}", l2)
        for k in THRESHOLDS
    }


def _predict_threshold(model: dict[str, Any], row: dict[str, Any], k: int, alpha: float) -> float:
    raw = row["xg_x"]
    theta = [float(v) for v in model["theta"]]
    means = [float(v) for v in model["means"]]
    scales = [float(v) for v in model["scales"]]
    x = [1.0] + [(float(raw[j]) - means[j]) / scales[j] for j in range(1, len(raw))]
    residual = sum(theta[j] * x[j] for j in range(len(theta)))
    eta = float(row[f"thr_offset_{k}"]) + float(alpha) * residual
    return binary._clip(binary._sigmoid(eta))


def _pav_nonincreasing(values: list[float]) -> list[float]:
    blocks: list[dict[str, Any]] = []
    for i, value in enumerate(values):
        blocks.append({"start": i, "end": i, "weight": 1.0, "value": float(value)})
        while len(blocks) >= 2 and blocks[-2]["value"] < blocks[-1]["value"]:
            b = blocks.pop()
            a = blocks.pop()
            w = float(a["weight"]) + float(b["weight"])
            blocks.append({
                "start": a["start"], "end": b["end"], "weight": w,
                "value": (float(a["value"]) * float(a["weight"]) + float(b["value"]) * float(b["weight"])) / w,
            })
    out = [0.0] * len(values)
    for block in blocks:
        for i in range(int(block["start"]), int(block["end"]) + 1):
            out[i] = binary._clip(float(block["value"]))
    return out


def _total_from_survival(survival: list[float]) -> dict[str, float]:
    s = _pav_nonincreasing(survival)
    probs = {
        "0": 1.0 - s[0],
        "1": s[0] - s[1],
        "2": s[1] - s[2],
        "3": s[2] - s[3],
        "4": s[3] - s[4],
        "5": s[4] - s[5],
        "6": s[5] - s[6],
        "7+": s[6],
    }
    probs = {k: max(0.0, float(v)) for k, v in probs.items()}
    z = sum(probs.values())
    if z <= 0:
        raise PlatformError("ordinal reconstruction has zero mass")
    return {k: probs[k] / z for k in TOTAL_BUCKETS}


def _candidate_total(row: dict[str, Any], models: dict[int, dict[str, Any]] | None, alpha: float) -> dict[str, float]:
    if models is None or alpha <= 0.0:
        return {b: float(row["formal_total"][b]) for b in TOTAL_BUCKETS}
    survival = [_predict_threshold(models[k], row, k, alpha) for k in THRESHOLDS]
    return _total_from_survival(survival)


def _rows_with_candidate(rows: list[dict[str, Any]], models: dict[int, dict[str, Any]] | None, alpha: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        total = _candidate_total(row, models, alpha)
        candidate = reweight._reweight_matrix(row["matrix"], total)
        out.append({**row, "baseline_matrix": row["matrix"], "candidate_matrix": candidate})
    return out


def _total_log_loss(rows: list[dict[str, Any]], matrix_key: str) -> float:
    loss = 0.0
    for row in rows:
        dist = _total_distribution(row[matrix_key])
        truth = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
        loss -= math.log(max(EPS, float(dist[truth])))
    return loss / len(rows) if rows else float("nan")


def _evaluate(rows: list[dict[str, Any]], models: dict[int, dict[str, Any]] | None, alpha: float) -> dict[str, Any]:
    scored = _rows_with_candidate(rows, models, alpha)
    metric = _score(scored, "candidate_matrix")
    return {
        "metric": metric,
        "total_log_loss": _total_log_loss(scored, "candidate_matrix"),
        "mode_counts": _top1_counts(scored, "candidate_matrix"),
        "rows": scored,
    }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    metric = result["metric"]
    return {
        "count": int(metric["count"]),
        "total_top1_accuracy": float(metric["total_goals_0_7plus"]["top1_accuracy"]),
        "total_top1_hits": int(metric["total_goals_0_7plus"]["top1_hits"]),
        "total_mean_rps": float(metric["total_goals_0_7plus"]["mean_rps"]),
        "total_log_loss": float(result["total_log_loss"]),
        "score_top1_accuracy": float(metric["score"]["top1_accuracy"]),
        "score_top3_accuracy": float(metric["score"]["top3_accuracy"]),
        "score_mean_joint_log_score": float(metric["score"]["mean_joint_log_score"]),
        "one_x_two_top1_accuracy": float(metric["one_x_two"]["top1_accuracy"]),
        "one_x_two_mean_brier": float(metric["one_x_two"]["mean_brier"]),
        "mode_counts": result["mode_counts"],
        "probability_sum_max_residual": float(metric["probability_sum_max_residual"]),
    }


def _delta(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, float]:
    keys = (
        "total_top1_accuracy", "total_mean_rps", "total_log_loss",
        "score_top1_accuracy", "score_top3_accuracy", "score_mean_joint_log_score",
        "one_x_two_top1_accuracy", "one_x_two_mean_brier",
    )
    return {key: float(cand[key]) - float(base[key]) for key in keys}


def main() -> int:
    rows, attach_audit = _strict_rows_with_xg()
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == VALID_SEASON]
    holdout = [r for r in rows if r["season"] == HOLDOUT_SEASON]
    if min(len(train), len(valid), len(holdout)) < 900:
        raise PlatformError(f"insufficient chronology split train={len(train)} valid={len(valid)} holdout={len(holdout)}")

    valid_base_result = _evaluate(valid, None, 0.0)
    valid_base = _summary(valid_base_result)
    leaderboard: list[dict[str, Any]] = []
    model_cache: dict[float, dict[int, dict[str, Any]]] = {}

    # alpha=0 is the exact formal fallback and is recorded once.
    leaderboard.append({
        "l2": None,
        "alpha": 0.0,
        **valid_base,
        "proper_nonworse": True,
    })

    for l2 in L2_GRID:
        models = _fit_models(train, l2)
        model_cache[l2] = models
        for alpha in ALPHA_GRID:
            if alpha <= 0.0:
                continue
            res = _summary(_evaluate(valid, models, alpha))
            res.update({
                "l2": l2,
                "alpha": alpha,
                "proper_nonworse": bool(
                    float(res["total_mean_rps"]) <= float(valid_base["total_mean_rps"]) + PROPER_TOL
                    and float(res["total_log_loss"]) <= float(valid_base["total_log_loss"]) + PROPER_TOL
                ),
            })
            leaderboard.append(res)

    eligible = [r for r in leaderboard if r["proper_nonworse"]]
    chosen = min(
        eligible,
        key=lambda r: (
            -float(r["total_top1_accuracy"]),
            float(r["total_mean_rps"]),
            float(r["total_log_loss"]),
            float(r["alpha"]),
            float(r["l2"] or 1e18),
        ),
    )

    if float(chosen["alpha"]) <= 0.0:
        final_models = None
    else:
        final_models = _fit_models(train + valid, float(chosen["l2"]))

    hold_base_result = _evaluate(holdout, None, 0.0)
    hold_cand_result = _evaluate(holdout, final_models, float(chosen["alpha"]))
    hold_base = _summary(hold_base_result)
    hold_cand = _summary(hold_cand_result)

    per_domain: dict[str, Any] = {}
    for cid in sorted({str(r["competition_id"]) for r in holdout}):
        subset = [r for r in holdout if str(r["competition_id"]) == cid]
        b = _summary(_evaluate(subset, None, 0.0))
        c = _summary(_evaluate(subset, final_models, float(chosen["alpha"])))
        per_domain[cid] = {"baseline": b, "candidate": c, "delta": _delta(b, c)}

    fit_audit = None
    if final_models is not None:
        fit_audit = {
            str(k): {
                "l2": float(final_models[k]["l2"]),
                "iterations": int(final_models[k]["iterations"]),
                "objective": float(final_models[k]["objective"]),
                "max_abs_gradient": float(final_models[k]["max_abs_gradient"]),
                "training_count": int(final_models[k]["training_count"]),
            }
            for k in THRESHOLDS
        }

    payload = {
        "schema_version": "V6.25.13-strict-pit-xg-ordinal-total-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_XG_ORDINAL_TOTAL_FORMAL_WEIGHT_0",
        "chronology": {
            "fit_seasons": sorted(TRAIN_SEASONS),
            "validation_season": VALID_SEASON,
            "untouched_holdout_season": HOLDOUT_SEASON,
            "fit_count": len(train),
            "validation_count": len(valid),
            "holdout_count": len(holdout),
        },
        "feature_contract": {
            "source": "immutable V6.18.9 pre-match Understat state panel",
            "fields": ["xG", "xGA", "npxG", "npxGA", "PPDA", "OPPDA", "deep", "deep_allowed", "xPTS", "history_count"],
            "target_or_postmatch_features": false,
            "panel_web_refetch": false,
        },
        "model": {
            "thresholds": list(THRESHOLDS),
            "equation": "logit P(T>k|X) = logit P_formal(T>k) + alpha * residual_k(X)",
            "l2_grid": list(L2_GRID),
            "alpha_grid": list(ALPHA_GRID),
            "monotonic_repair": "deterministic PAV non-increasing survival probabilities",
            "same_joint_matrix": true,
            "conditional_score_given_total_preserved": true,
        },
        "attachment_audit": attach_audit,
        "validation_baseline": valid_base,
        "validation_leaderboard": leaderboard,
        "selected": chosen,
        "holdout": {
            "baseline": hold_base,
            "candidate": hold_cand,
            "delta": _delta(hold_base, hold_cand),
            "per_domain": per_domain,
        },
        "fit_audit": fit_audit,
        "decision": {
            "candidate_nonzero": bool(float(chosen["alpha"]) > 0.0),
            "validation_proper_gate_pass": bool(chosen["proper_nonworse"]),
            "holdout_exact_top1_improved": bool(float(hold_cand["total_top1_accuracy"]) > float(hold_base["total_top1_accuracy"])),
            "holdout_rps_nonworse": bool(float(hold_cand["total_mean_rps"]) <= float(hold_base["total_mean_rps"]) + PROPER_TOL),
            "holdout_logloss_nonworse": bool(float(hold_cand["total_log_loss"]) <= float(hold_base["total_log_loss"]) + PROPER_TOL),
            "promotion_eligible": false,
        },
        "governance": {
            "research_only": true,
            "formal_weight": 0,
            "current_rule_change": false,
            "runtime_probability_change": false,
            "validation_only_selection": true,
            "holdout_used_for_selection": false,
            "automatic_promotion": false,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "chronology": payload["chronology"],
        "selected": payload["selected"],
        "holdout": payload["holdout"],
        "decision": payload["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
