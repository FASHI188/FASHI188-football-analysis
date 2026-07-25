#!/usr/bin/env python3
"""V6.25.20 fresh Active-Kambi correct-score -> exact-total forward challenger.

Research only; formal_weight=0.

This is the near-freeze successor to V6.25.18. It consumes ONLY immutable
V6.21.2 special-market sidecars produced directly from the current Active-Kambi
PIT capture. No old V6.5.1 prediction snapshot is used as the market source.

Prospective rules are frozen at the first V6.25.20 epoch and only sidecars whose
freeze timestamp is on/after that epoch may enter. Already-started fixtures are
never backfilled. Prediction rules are simple, fixed and outcome-blind:

A top_score_total      = total of shortest-priced numeric Correct Score.
B numeric_mass_total   = argmax summed relative reciprocal-odds mass by total.
C partial_cdf_total    = exact Top-1 only when total half-line CDF identifies it.
D consensus_total      = A==B and, when C exists, C must agree.

Lead-time cohorts are pre-registered, not optimized:
- H0_12:  >0 to 12 hours before kickoff
- H12_24: >12 to 24 hours
- H24_72: >24 to 72 hours
- OUTSIDE: anything else (retained for audit, excluded from 1-72h main review)

These are ranking signals, not exhaustive probability distributions.
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

import v6_special_market_forward_eval_v6211 as market  # noqa: E402

SIDECAR_DIR = ROOT / "evidence" / "markets_special_prospective"
EPOCH = ROOT / "manifests" / "v6_active_correct_score_total_epoch_v62520.json"
PREDICTIONS = ROOT / "forward" / "v6_active_correct_score_total_predictions_v62520.json"
OUT = ROOT / "manifests" / "v6_active_correct_score_total_forward_v62520_status.json"
LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bucket(total: int) -> int:
    return min(7, int(total))


def _lead_bucket(hours: float) -> str:
    if 0.0 < hours <= 12.0:
        return "H0_12"
    if 12.0 < hours <= 24.0:
        return "H12_24"
    if 24.0 < hours <= 72.0:
        return "H24_72"
    return "OUTSIDE"


def _create_or_load_epoch() -> dict[str, Any]:
    if EPOCH.exists():
        payload = _load(EPOCH)
        if payload.get("schema_version") != "V6.25.20-active-score-total-epoch-r1":
            raise RuntimeError("unexpected V6.25.20 epoch schema")
        return payload
    now = _utcnow()
    payload = {
        "schema_version": "V6.25.20-active-score-total-epoch-r1",
        "freeze_at_utc": now.isoformat(),
        "status": "PASS_FROZEN_NO_BACKFILL",
        "lead_buckets_hours": {"H0_12": [0, 12], "H12_24": [12, 24], "H24_72": [24, 72]},
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "source_sidecars_must_be_on_or_after_epoch": True,
            "already_started_backfill": False,
            "ranking_not_probability_distribution": True,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "current_rule_change": False,
        },
    }
    _write(EPOCH, payload)
    return payload


def _load_predictions() -> dict[str, Any]:
    if not PREDICTIONS.exists():
        return {"schema_version": "V6.25.20-active-score-total-predictions-r1", "predictions": []}
    payload = _load(PREDICTIONS)
    if payload.get("schema_version") != "V6.25.20-active-score-total-predictions-r1":
        raise RuntimeError("unexpected V6.25.20 prediction schema")
    return payload


def _top_score_total(cs: dict[str, Any] | None) -> tuple[int | None, list[int] | None]:
    if not cs or not cs.get("ranked_scores"):
        return None, None
    score = cs["ranked_scores"][0]
    if not isinstance(score, (list, tuple)) or len(score) != 2:
        return None, None
    h, a = int(score[0]), int(score[1])
    return _bucket(h + a), [h, a]


def _numeric_mass_total(cs: dict[str, Any] | None) -> tuple[int | None, dict[str, float]]:
    if not cs:
        return None, {}
    raw = cs.get("numeric_probabilities_relative_only") or {}
    grouped = defaultdict(float)
    for key, rel in raw.items():
        try:
            h, a = str(key).split("-", 1)
            p = float(rel)
            if p > 0.0 and math.isfinite(p):
                grouped[_bucket(int(h) + int(a))] += p
        except Exception:
            continue
    if not grouped:
        return None, {}
    pick = min(grouped, key=lambda b: (-grouped[b], b))
    return int(pick), {str(k): float(v) for k, v in sorted(grouped.items())}


def _partial_cdf_total(tl: dict[str, Any] | None) -> tuple[int | None, dict[str, Any] | None]:
    ident = market.identifiable_total_top(tl)
    if not ident or not bool(ident.get("top1_identifiable")):
        return None, ident
    return int(ident["top1_bucket"]), ident


def _arms(sidecar: dict[str, Any]) -> dict[str, Any]:
    cs = sidecar.get("correct_score")
    tl = sidecar.get("total_goals_ladder")
    a, score = _top_score_total(cs)
    b, masses = _numeric_mass_total(cs)
    c, ident = _partial_cdf_total(tl)
    consensus = None
    consensus_type = None
    if a is not None and b is not None and a == b:
        if c is None:
            consensus = a
            consensus_type = "CORRECT_SCORE_TWO_ARM"
        elif c == a:
            consensus = a
            consensus_type = "THREE_ARM"
    return {
        "top_score_total": a,
        "top_score": score,
        "numeric_mass_total": b,
        "numeric_mass_relative_weights": masses,
        "partial_cdf_total": c,
        "partial_cdf_identification": ident,
        "consensus_total": consensus,
        "consensus_type": consensus_type,
        "correct_score_offer_price_complete": bool(cs and cs.get("offer_price_complete")),
        "correct_score_probability_exhaustive": False,
        "total_ladder_complete_0_7plus": bool(tl and tl.get("complete_0_7plus")),
        "total_ladder_lines": list((tl or {}).get("lines") or []),
    }


def _identity(sidecar: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(sidecar.get("competition_id") or ""),
        str(sidecar.get("home_team") or ""),
        str(sidecar.get("away_team") or ""),
        _parse_dt(sidecar.get("kickoff_utc")).isoformat(),
    )


def _freeze_new(epoch: dict[str, Any], preds: dict[str, Any]) -> dict[str, int]:
    epoch_dt = _parse_dt(epoch["freeze_at_utc"])
    now = _utcnow()
    existing_sources = {str(x["source_sidecar_path"]) for x in preds.get("predictions") or []}
    counts = Counter()
    for path in sorted(SIDECAR_DIR.glob("*__special.json")):
        rel = str(path.relative_to(ROOT))
        if rel in existing_sources:
            counts["already_frozen"] += 1
            continue
        sidecar = _load(path)
        freeze = _parse_dt(sidecar.get("freeze_utc"))
        kickoff = _parse_dt(sidecar.get("kickoff_utc"))
        if freeze < epoch_dt:
            counts["before_epoch"] += 1
            continue
        if kickoff <= now or kickoff <= freeze:
            counts["already_started_not_backfilled"] += 1
            continue
        lead = (kickoff - freeze).total_seconds() / 3600.0
        arms = _arms(sidecar)
        if arms["top_score_total"] is None and arms["numeric_mass_total"] is None and arms["partial_cdf_total"] is None:
            counts["no_ranking_surface"] += 1
            continue
        preds["predictions"].append({
            "competition_id": str(sidecar.get("competition_id") or ""),
            "season": str(sidecar.get("season") or ""),
            "home_team": str(sidecar.get("home_team") or ""),
            "away_team": str(sidecar.get("away_team") or ""),
            "kickoff_at": kickoff.isoformat(),
            "market_freeze_at_utc": freeze.isoformat(),
            "lead_hours": lead,
            "lead_bucket": _lead_bucket(lead),
            "source_sidecar_path": rel,
            "source_sidecar_sha256": _sha(path),
            "source_normalized_path": sidecar.get("source_normalized_path"),
            "source_normalized_sha256": sidecar.get("source_normalized_sha256"),
            "source_raw_path": sidecar.get("source_raw_path"),
            "source_raw_file_sha256": sidecar.get("source_raw_file_sha256"),
            "arms": arms,
        })
        counts["new_predictions"] += 1
    preds["predictions"].sort(key=lambda x: (x["kickoff_at"], x["competition_id"], x["home_team"], x["away_team"], x["market_freeze_at_utc"]))
    _write(PREDICTIONS, preds)
    return dict(sorted(counts.items()))


def _result_lookup() -> dict[tuple[str, str, str, str], tuple[int, int]]:
    if not LEDGER.exists():
        return {}
    source_preds, results = market.prediction_and_results(_load(LEDGER))
    out: dict[tuple[str, str, str, str], tuple[int, int]] = {}
    for match_id, result_event in results.items():
        pred = source_preds.get(match_id)
        if pred is None:
            continue
        fixture = pred["payload"]["fixture_identity"]
        result = (result_event.get("payload") or {}).get("result") or {}
        try:
            key = (
                str(fixture["competition_id"]),
                str(fixture["home_team"]),
                str(fixture["away_team"]),
                _parse_dt(fixture["kickoff_at"]).isoformat(),
            )
            out[key] = (int(result["home_goals_90"]), int(result["away_goals_90"]))
        except Exception:
            continue
    return out


def _evaluate(preds: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results = _result_lookup()
    arm_names = ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total")
    stats: dict[str, dict[str, Counter]] = defaultdict(lambda: {name: Counter() for name in arm_names})
    settled = []
    for pred in preds.get("predictions") or []:
        key = (pred["competition_id"], pred["home_team"], pred["away_team"], pred["kickoff_at"])
        score = results.get(key)
        if score is None:
            continue
        actual = _bucket(score[0] + score[1])
        cohort = str(pred["lead_bucket"])
        row = {
            "competition_id": pred["competition_id"],
            "home_team": pred["home_team"],
            "away_team": pred["away_team"],
            "kickoff_at": pred["kickoff_at"],
            "market_freeze_at_utc": pred["market_freeze_at_utc"],
            "lead_hours": pred["lead_hours"],
            "lead_bucket": cohort,
            "actual_total_bucket": actual,
            "arms": {},
        }
        for group in ("ALL_1_72", cohort):
            if group == "ALL_1_72" and cohort == "OUTSIDE":
                continue
            for name in arm_names:
                pick = (pred.get("arms") or {}).get(name)
                if pick is None:
                    continue
                hit = int(int(pick) == actual)
                stats[group][name]["count"] += 1
                stats[group][name]["hits"] += hit
                row["arms"][name] = {"pick": int(pick), "hit": hit}
        settled.append(row)
    summary: dict[str, Any] = {}
    for group, arm_stats in sorted(stats.items()):
        summary[group] = {}
        for name in arm_names:
            n = int(arm_stats[name]["count"])
            h = int(arm_stats[name]["hits"])
            summary[group][name] = {"count": n, "hits": h, "accuracy": h / n if n else None}
    return summary, settled


def main() -> int:
    epoch = _create_or_load_epoch()
    preds = _load_predictions()
    freeze_scan = _freeze_new(epoch, preds)
    metrics, settled = _evaluate(preds)
    availability = Counter()
    lead_counts = Counter()
    for pred in preds.get("predictions") or []:
        lead_counts[str(pred["lead_bucket"])] += 1
        for name in ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total"):
            if (pred.get("arms") or {}).get(name) is not None:
                availability[name] += 1
    payload = {
        "schema_version": "V6.25.20-active-score-total-forward-status-r1",
        "generated_at_utc": _utcnow().isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "FRESH_ACTIVE_KAMBI_PROSPECTIVE_RANKING_FORMAL_WEIGHT_0",
        "epoch_freeze_at_utc": epoch["freeze_at_utc"],
        "prediction_count": len(preds.get("predictions") or []),
        "freeze_scan": freeze_scan,
        "lead_bucket_counts": dict(sorted(lead_counts.items())),
        "arm_availability": dict(sorted(availability.items())),
        "settled_count": len(settled),
        "metrics_by_lead_bucket": metrics,
        "settled": settled,
        "review_state": "PENDING_100_SETTLED_1_72H" if len(settled) < 100 else "REVIEW_READY_100_PLUS",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "source": "V6.21.2 immutable sidecars from fresh Active-Kambi PIT",
            "source_sidecar_must_be_on_or_after_epoch": True,
            "already_started_backfill": False,
            "postmatch_odds": False,
            "network_refetch": False,
            "rankings_not_probability_distributions": True,
            "lead_buckets_preregistered_before_settlement": True,
            "minimum_settled_review_sample": 100,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    _write(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "epoch": payload["epoch_freeze_at_utc"],
        "prediction_count": payload["prediction_count"],
        "freeze_scan": payload["freeze_scan"],
        "lead_bucket_counts": payload["lead_bucket_counts"],
        "arm_availability": payload["arm_availability"],
        "settled_count": payload["settled_count"],
        "metrics": payload["metrics_by_lead_bucket"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
