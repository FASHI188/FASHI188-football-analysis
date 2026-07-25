#!/usr/bin/env python3
"""V6.25.18 prospective correct-score -> exact-total ranking challenger.

Research only; formal_weight=0.

Purpose
-------
The first frozen Kambi sample showed that complete 0-7+ total ladders are rare,
while full-time Correct Score surfaces are commonly present. This challenger
pre-registers simple ranking rules for FUTURE, not-yet-started fixtures only.
It never treats the incomplete Correct Score surface as an exhaustive total-goal
probability distribution.

Frozen ranking arms
-------------------
A. top_score_total:
   total goals of the shortest-priced numeric Correct Score outcome.
B. numeric_mass_total:
   total-goal bucket with the largest SUM of reciprocal decimal odds across all
   currently open numeric Correct Score outcomes. This is a RELATIVE ranking
   score only; omitted/non-numeric outcomes mean it is not an exhaustive PMF.
C. partial_cdf_total:
   exact-total Top-1 only when the frozen Total Goals half-line CDF mathematically
   identifies a bucket without splitting the unresolved tail, using V6.21.1.
D. consensus_total:
   emitted only when A and B agree, and if C is available it must agree too.

Governance
----------
- source is the immutable pre-match raw Kambi response already bound to each
  MARKET_PREDICTION_FROZEN ledger event;
- no network refetch;
- first-run epoch is immutable;
- already-started events at the epoch are NEVER backfilled;
- future reruns may append only newly eligible not-yet-started source events;
- settled outcomes are used only after prediction freeze;
- rankings are not probabilities and cannot alter V5.0.1 formal probabilities.
"""
from __future__ import annotations

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

LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
EPOCH = ROOT / "manifests" / "v6_correct_score_total_forward_epoch_v62518.json"
PREDICTIONS = ROOT / "forward" / "v6_correct_score_total_predictions_v62518.json"
OUT = ROOT / "manifests" / "v6_correct_score_total_forward_v62518_status.json"


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


def _prediction_and_results() -> tuple[dict[str, Any], dict[str, Any]]:
    return market.prediction_and_results(_load(LEDGER))


def _numeric_rows(cs: dict[str, Any] | None) -> list[tuple[tuple[int, int], float]]:
    if not cs:
        return []
    raw = cs.get("numeric_probabilities_relative_only") or {}
    rows: list[tuple[tuple[int, int], float]] = []
    for key, rel in raw.items():
        try:
            h, a = str(key).split("-", 1)
            score = (int(h), int(a))
            p = float(rel)
        except Exception:
            continue
        if p > 0.0 and math.isfinite(p):
            rows.append((score, p))
    return rows


def _arms(env: dict[str, Any]) -> dict[str, Any]:
    cs = market.correct_score_surface(env)
    tl = market.total_ladder(env)
    idt = market.identifiable_total_top(tl)

    top_score_total = None
    top_score = None
    if cs and cs.get("ranked_scores"):
        raw_score = cs["ranked_scores"][0]
        if isinstance(raw_score, (list, tuple)) and len(raw_score) == 2:
            top_score = [int(raw_score[0]), int(raw_score[1])]
            top_score_total = min(7, int(raw_score[0]) + int(raw_score[1]))

    grouped = defaultdict(float)
    for (h, a), rel in _numeric_rows(cs):
        grouped[min(7, h + a)] += float(rel)
    numeric_mass_total = None
    numeric_mass_ranking: list[int] = []
    if grouped:
        numeric_mass_ranking = sorted(grouped, key=lambda bucket: (-grouped[bucket], bucket))
        numeric_mass_total = int(numeric_mass_ranking[0])

    partial_cdf_total = None
    if idt and bool(idt.get("top1_identifiable")):
        partial_cdf_total = int(idt["top1_bucket"])

    consensus_total = None
    consensus_type = None
    if top_score_total is not None and numeric_mass_total is not None and top_score_total == numeric_mass_total:
        if partial_cdf_total is None:
            consensus_total = int(top_score_total)
            consensus_type = "CORRECT_SCORE_TWO_ARM"
        elif partial_cdf_total == top_score_total:
            consensus_total = int(top_score_total)
            consensus_type = "THREE_ARM"

    return {
        "top_score_total": top_score_total,
        "top_score": top_score,
        "numeric_mass_total": numeric_mass_total,
        "numeric_mass_ranking": numeric_mass_ranking,
        "numeric_mass_relative_weights": {str(k): float(v) for k, v in sorted(grouped.items())},
        "partial_cdf_total": partial_cdf_total,
        "partial_cdf_identification": idt,
        "consensus_total": consensus_total,
        "consensus_type": consensus_type,
        "correct_score_offer_price_complete": bool(cs.get("offer_price_complete")) if cs else False,
        "correct_score_probability_exhaustive": False,
        "total_ladder_complete_0_7plus": bool(tl.get("complete_0_7plus")) if tl else False,
        "total_ladder_lines": list(tl.get("lines") or []) if tl else [],
    }


def _load_or_create_epoch() -> dict[str, Any]:
    if EPOCH.exists():
        epoch = _load(EPOCH)
        if epoch.get("schema_version") != "V6.25.18-correct-score-total-forward-epoch-r1":
            raise RuntimeError("unexpected V6.25.18 epoch schema")
        return epoch
    now = _utcnow()
    epoch = {
        "schema_version": "V6.25.18-correct-score-total-forward-epoch-r1",
        "freeze_at_utc": now.isoformat(),
        "status": "PASS_FROZEN_NO_BACKFILL",
        "ranking_rules": {
            "top_score_total": "total of shortest-priced open numeric Correct Score outcome",
            "numeric_mass_total": "argmax total of summed reciprocal-odds relative mass over open numeric Correct Score outcomes",
            "partial_cdf_total": "V6.21.1 mathematically identifiable exact Top1 from contiguous total-goal half-line CDF",
            "consensus_total": "A==B and, when C exists, C must also agree",
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "no_backfill": True,
            "no_network_refetch": True,
            "ranking_not_probability_distribution": True,
            "formal_probability_change": False,
            "current_rule_change": False,
        },
    }
    _write(EPOCH, epoch)
    return epoch


def _existing_predictions() -> dict[str, Any]:
    if not PREDICTIONS.exists():
        return {
            "schema_version": "V6.25.18-correct-score-total-predictions-r1",
            "predictions": [],
        }
    payload = _load(PREDICTIONS)
    if payload.get("schema_version") != "V6.25.18-correct-score-total-predictions-r1":
        raise RuntimeError("unexpected V6.25.18 prediction schema")
    return payload


def _freeze_predictions(epoch: dict[str, Any], preds: dict[str, Any]) -> tuple[dict[str, Any], int, int, int]:
    source_preds, _ = _prediction_and_results()
    existing_ids = {str(x["match_id"]) for x in preds.get("predictions") or []}
    now = _utcnow()
    added = 0
    already_started = 0
    unavailable = 0

    # No event can be frozen with a timestamp earlier than the immutable first-run epoch.
    epoch_dt = _parse_dt(epoch["freeze_at_utc"])
    freeze_dt = max(epoch_dt, now)

    for match_id, source in sorted(source_preds.items()):
        if match_id in existing_ids:
            continue
        fixture = source["payload"]["fixture_identity"]
        kickoff = _parse_dt(fixture["kickoff_at"])
        if kickoff <= freeze_dt:
            already_started += 1
            continue
        try:
            _, env = market.raw_for_prediction(source)
            arms = _arms(env)
        except Exception:
            unavailable += 1
            continue
        if arms["top_score_total"] is None and arms["numeric_mass_total"] is None and arms["partial_cdf_total"] is None:
            unavailable += 1
            continue
        source_observed = _parse_dt(env["observed_at_utc"])
        preds["predictions"].append({
            "match_id": match_id,
            "competition_id": str(fixture["competition_id"]),
            "season": str(fixture.get("season") or ""),
            "kickoff_at": kickoff.isoformat(),
            "home_team": str(fixture["home_team"]),
            "away_team": str(fixture["away_team"]),
            "prediction_frozen_at_utc": freeze_dt.isoformat(),
            "source_prediction_event_hash": source.get("event_hash"),
            "source_observed_at_utc": source_observed.isoformat(),
            "source_age_hours_at_challenger_freeze": (freeze_dt - source_observed).total_seconds() / 3600.0,
            "arms": arms,
        })
        added += 1

    preds["predictions"].sort(key=lambda x: (x["kickoff_at"], x["competition_id"], x["match_id"]))
    return preds, added, already_started, unavailable


def _evaluate(preds: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, results = _prediction_and_results()
    names = ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total")
    stats = {name: Counter() for name in names}
    settled_rows: list[dict[str, Any]] = []

    for pred in preds.get("predictions") or []:
        match_id = str(pred["match_id"])
        result_event = results.get(match_id)
        if result_event is None:
            continue
        result = (result_event.get("payload") or {}).get("result") or {}
        hg = int(result["home_goals_90"])
        ag = int(result["away_goals_90"])
        actual = min(7, hg + ag)
        row = {
            "match_id": match_id,
            "competition_id": pred["competition_id"],
            "kickoff_at": pred["kickoff_at"],
            "home_team": pred["home_team"],
            "away_team": pred["away_team"],
            "actual_total_bucket": actual,
            "arms": {},
        }
        for name in names:
            pick = (pred.get("arms") or {}).get(name)
            if pick is None:
                continue
            hit = int(int(pick) == actual)
            stats[name]["count"] += 1
            stats[name]["hits"] += hit
            row["arms"][name] = {"pick": int(pick), "hit": hit}
        settled_rows.append(row)

    summary = {}
    for name in names:
        count = int(stats[name]["count"])
        hits = int(stats[name]["hits"])
        summary[name] = {
            "count": count,
            "hits": hits,
            "accuracy": hits / count if count else None,
        }
    return summary, settled_rows


def main() -> int:
    epoch = _load_or_create_epoch()
    preds = _existing_predictions()
    before = len(preds.get("predictions") or [])
    preds, added, already_started, unavailable = _freeze_predictions(epoch, preds)
    _write(PREDICTIONS, preds)
    summary, settled_rows = _evaluate(preds)

    arm_availability = Counter()
    lead_hours = []
    consensus_types = Counter()
    for pred in preds.get("predictions") or []:
        lead_hours.append(float(pred["source_age_hours_at_challenger_freeze"]))
        arms = pred.get("arms") or {}
        for name in ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total"):
            if arms.get(name) is not None:
                arm_availability[name] += 1
        if arms.get("consensus_type"):
            consensus_types[str(arms["consensus_type"])] += 1

    payload = {
        "schema_version": "V6.25.18-correct-score-total-forward-status-r1",
        "generated_at_utc": _utcnow().isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "PROSPECTIVE_MARKET_RANKING_CHALLENGER_FORMAL_WEIGHT_0",
        "freeze_at_utc": epoch["freeze_at_utc"],
        "source_prediction_count": len(_prediction_and_results()[0]),
        "prediction_count": len(preds.get("predictions") or []),
        "previous_prediction_count": before,
        "new_prediction_count": added,
        "already_started_not_backfilled": already_started,
        "unavailable": unavailable,
        "arm_availability": dict(sorted(arm_availability.items())),
        "consensus_types": dict(sorted(consensus_types.items())),
        "source_age_hours": {
            "min": min(lead_hours) if lead_hours else None,
            "max": max(lead_hours) if lead_hours else None,
            "mean": sum(lead_hours) / len(lead_hours) if lead_hours else None,
        },
        "settled_count": len(settled_rows),
        "settled_arm_metrics": summary,
        "settled": settled_rows,
        "review_state": "PENDING_100_SETTLED" if len(settled_rows) < 100 else "REVIEW_READY_100_PLUS",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "no_started_event_backfill": True,
            "network_refetch": False,
            "postmatch_odds_used": False,
            "exact_source_snapshot_binding": True,
            "rankings_not_exhaustive_probability_distributions": True,
            "historical_11_settled_used_for_rule_selection": False,
            "minimum_settled_review_sample": 100,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    _write(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
