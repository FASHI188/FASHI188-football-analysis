#!/usr/bin/env python3
"""Audit existing prospective context+market evidence before fitting the V5.1 residual model.

This script does not fit a model and does not mutate formal probabilities. It aligns the
immutable context decision ledger with the official result inbox, evaluates the synchronized
market baseline on the settled overlap, and applies a frozen minimum-sample gate.
"""
from __future__ import annotations

import argparse
import json
import math
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_context_market_prototype_r1.json"
DEFAULT_CONTEXT = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
DEFAULT_RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_pit_context_readiness_r1_status.json"
DIRECTIONS = ("home", "draw", "away")
EPS = 1e-15


class AuditError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuditError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root is not an object: {path.relative_to(ROOT)}")
    return value


def parse_ts(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise AuditError(f"missing {field}")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditError(f"invalid {field}: {text}") from exc
    if dt.tzinfo is None:
        raise AuditError(f"naive {field}: {text}")
    return dt.astimezone(timezone.utc)


def normalize_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum())


def identity_key(fixture: dict[str, Any]) -> tuple[str, str, str, str]:
    kickoff = parse_ts(fixture.get("kickoff_at"), "kickoff_at").isoformat()
    key = (
        str(fixture.get("competition_id") or "").strip(),
        kickoff,
        normalize_team(fixture.get("home_team")),
        normalize_team(fixture.get("away_team")),
    )
    if not all(key):
        raise AuditError(f"incomplete fixture identity: {fixture}")
    return key


def result_key(result: dict[str, Any]) -> tuple[str, str, str, str]:
    return identity_key({
        "competition_id": result.get("competition_id"),
        "kickoff_at": result.get("kickoff_at"),
        "home_team": result.get("home_team"),
        "away_team": result.get("away_team"),
    })


def devig(odds: dict[str, Any]) -> dict[str, float]:
    inv: dict[str, float] = {}
    for direction in DIRECTIONS:
        try:
            price = float(odds[direction])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditError(f"invalid 1X2 price for {direction}: {odds}") from exc
        if not math.isfinite(price) or price <= 1.0:
            raise AuditError(f"non-decimal 1X2 price for {direction}: {price}")
        inv[direction] = 1.0 / price
    overround = sum(inv.values())
    if not math.isfinite(overround) or overround <= 0:
        raise AuditError("invalid market overround")
    return {direction: inv[direction] / overround for direction in DIRECTIONS}


def actual_direction(result: dict[str, Any]) -> str:
    value = str(result.get("actual_result") or "").strip().casefold()
    aliases = {"h": "home", "home": "home", "d": "draw", "draw": "draw", "a": "away", "away": "away"}
    if value in aliases:
        return aliases[value]
    try:
        home = int(result["home_goals_90"])
        away = int(result["away_goals_90"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"result has no valid 90m direction: {result}") from exc
    return "home" if home > away else "away" if away > home else "draw"


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    hits = 0
    log_loss = 0.0
    brier = 0.0
    rps = 0.0
    for row in rows:
        probs = row["market_probabilities"]
        actual = row["actual"]
        pick = max(DIRECTIONS, key=lambda direction: probs[direction])
        hits += int(pick == actual)
        log_loss -= math.log(max(EPS, min(1.0, probs[actual])))
        target = {direction: 1.0 if actual == direction else 0.0 for direction in DIRECTIONS}
        brier += sum((probs[d] - target[d]) ** 2 for d in DIRECTIONS)
        rps += ((probs["home"] - target["home"]) ** 2 +
                (probs["home"] + probs["draw"] - target["home"] - target["draw"]) ** 2) / 2.0
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "log_loss": log_loss / n,
        "brier": brier / n,
        "rps": rps / n,
    }


def draw_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    actual_draws = [row for row in rows if row["actual"] == "draw"]
    market_draw_top1 = [row for row in rows if max(DIRECTIONS, key=lambda d: row["market_probabilities"][d]) == "draw"]
    zero_zero = sum(row.get("score") == [0, 0] for row in rows)
    one_one = sum(row.get("score") == [1, 1] for row in rows)
    two_two_plus = sum(
        isinstance(row.get("score"), list)
        and len(row["score"]) == 2
        and row["score"][0] == row["score"][1]
        and row["score"][0] >= 2
        for row in rows
    )
    mean_pdraw = sum(float(row["market_probabilities"]["draw"]) for row in rows) / len(rows)
    draw_brier = sum(
        (float(row["market_probabilities"]["draw"]) - (1.0 if row["actual"] == "draw" else 0.0)) ** 2
        for row in rows
    ) / len(rows)
    correct_top1 = sum(row["actual"] == "draw" for row in market_draw_top1)
    return {
        "count": len(rows),
        "actual_draw_count": len(actual_draws),
        "actual_draw_rate": len(actual_draws) / len(rows),
        "mean_market_draw_probability": mean_pdraw,
        "market_draw_probability_bias": mean_pdraw - len(actual_draws) / len(rows),
        "draw_brier": draw_brier,
        "market_draw_top1_count": len(market_draw_top1),
        "market_draw_top1_precision": correct_top1 / len(market_draw_top1) if market_draw_top1 else None,
        "market_draw_top1_recall": correct_top1 / len(actual_draws) if actual_draws else None,
        "draw_score_breakdown": {"0-0": zero_zero, "1-1": one_one, "2-2_or_higher": two_two_plus},
    }


def build_result_index(results: list[Any]) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    malformed: list[dict[str, Any]] = []
    for index, raw in enumerate(results):
        if not isinstance(raw, dict):
            malformed.append({"index": index, "reason": "result_not_object"})
            continue
        try:
            grouped[result_key(raw)].append(raw)
        except Exception as exc:
            malformed.append({"index": index, "reason": f"{type(exc).__name__}: {exc}"})
    index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = list(malformed)
    for key, candidates in grouped.items():
        signatures = set()
        for result in candidates:
            try:
                signatures.add((int(result["home_goals_90"]), int(result["away_goals_90"]), actual_direction(result)))
            except Exception as exc:
                conflicts.append({"identity": list(key), "reason": f"invalid_result: {exc}"})
        if len(signatures) != 1:
            conflicts.append({"identity": list(key), "reason": "conflicting_result_records", "signatures": [list(x) for x in sorted(signatures)]})
            continue
        index[key] = candidates[0]
    return index, conflicts


def run_audit(config: dict[str, Any], context: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    events = context.get("events")
    result_rows = results.get("results")
    if not isinstance(events, list):
        raise AuditError("context ledger missing events list")
    if not isinstance(result_rows, list):
        raise AuditError("result inbox missing results list")

    result_index, result_conflicts = build_result_index(result_rows)
    context_identity_counts: Counter[tuple[str, str, str, str]] = Counter()
    valid_rows: list[dict[str, Any]] = []
    malformed_context: list[dict[str, Any]] = []
    invalid_market_rows: list[dict[str, Any]] = []
    source_tiers: Counter[str] = Counter()
    provider_groups: Counter[str] = Counter()

    for event_index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event_type") != "CONTEXT_DECISION_FROZEN":
            continue
        payload = event.get("payload") or {}
        fixture = payload.get("fixture_identity") or {}
        try:
            key = identity_key(fixture)
            freeze_at = parse_ts(payload.get("decision_freeze_at_utc") or event.get("event_timestamp_utc"), "decision_freeze_at_utc")
            kickoff = parse_ts(fixture.get("kickoff_at"), "fixture.kickoff_at")
            market = payload.get("market") or {}
            market_observed = parse_ts(market.get("observed_at_utc"), "market.observed_at_utc")
            if not (market_observed <= freeze_at < kickoff):
                raise AuditError("market/context/freeze chronology failed")
            probabilities = devig(market.get("one_x_two") or {})
        except Exception as exc:
            malformed_context.append({"event_index": event_index, "reason": f"{type(exc).__name__}: {exc}"})
            continue

        context_identity_counts[key] += 1
        evidence = payload.get("context_evidence") or {}
        source = evidence.get("source") or {}
        source_tiers[str(source.get("source_tier") or "unknown")] += 1
        provider_groups[str(source.get("provider_group") or "unknown")] += 1
        features = payload.get("context_features_available") or {}
        result = result_index.get(key)
        score = None
        actual = None
        if result is not None:
            try:
                score = [int(result["home_goals_90"]), int(result["away_goals_90"])]
                actual = actual_direction(result)
            except Exception as exc:
                invalid_market_rows.append({"identity": list(key), "reason": f"invalid_settlement: {exc}"})
                result = None

        valid_rows.append({
            "identity": list(key),
            "competition_id": key[0],
            "kickoff_at_utc": key[1],
            "home_team": fixture.get("home_team"),
            "away_team": fixture.get("away_team"),
            "decision_freeze_at_utc": freeze_at.isoformat(),
            "market_observed_at_utc": market_observed.isoformat(),
            "market_probabilities": probabilities,
            "market_pick": max(DIRECTIONS, key=lambda d: probabilities[d]),
            "provider_name": market.get("provider_name"),
            "provider_group": market.get("provider_group"),
            "source_tier": source.get("source_tier"),
            "features": {
                "home_availability": features.get("home_availability") is True,
                "away_availability": features.get("away_availability") is True,
                "home_predicted_xi": features.get("home_predicted_xi") is True,
                "away_predicted_xi": features.get("away_predicted_xi") is True,
            },
            "settled": result is not None,
            "actual": actual,
            "score": score,
        })

    duplicate_context = [list(key) for key, count in context_identity_counts.items() if count != 1]
    ambiguous = set(tuple(x) for x in duplicate_context)
    settled = [row for row in valid_rows if row["settled"] and tuple(row["identity"]) not in ambiguous]
    full_xi = [row for row in settled if row["features"]["home_predicted_xi"] and row["features"]["away_predicted_xi"]]
    both_availability = [row for row in settled if row["features"]["home_availability"] and row["features"]["away_availability"]]
    full_context = [row for row in settled if row in full_xi and row in both_availability]
    draw_rows = [row for row in settled if row["actual"] == "draw"]

    gate_cfg = config.get("pit_fit_gate") or {}
    gates = {
        "minimum_settled_context_rows": len(settled) >= int(gate_cfg.get("minimum_settled_context_rows", 60)),
        "minimum_settled_full_predicted_xi_rows": len(full_xi) >= int(gate_cfg.get("minimum_settled_full_predicted_xi_rows", 30)),
        "minimum_settled_draw_rows": len(draw_rows) >= int(gate_cfg.get("minimum_settled_draw_rows", 15)),
        "identity_conflicts": len(result_conflicts) + len(duplicate_context) <= int(gate_cfg.get("identity_conflicts_allowed", 0)),
        "invalid_market_rows": len(invalid_market_rows) <= int(gate_cfg.get("invalid_market_rows_allowed", 0)),
    }
    ready = all(gates.values())
    return {
        "schema_version": "V5.1.0-pit-context-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_READY_FOR_CONTEXT_RESIDUAL_FIT" if ready else "PASS_INSUFFICIENT_CONTEXT_SAMPLE",
        "classification": "EXISTING_PROSPECTIVE_DATA_AUDIT_NO_PROBABILITY_MUTATION",
        "counts": {
            "context_event_count": len(valid_rows),
            "result_inbox_unique_count": len(result_index),
            "settled_context_overlap": len(settled),
            "settled_full_predicted_xi": len(full_xi),
            "settled_both_teams_availability": len(both_availability),
            "settled_full_context": len(full_context),
            "settled_draws": len(draw_rows),
            "duplicate_context_identities": len(duplicate_context),
            "result_conflicts": len(result_conflicts),
            "malformed_context_events": len(malformed_context),
            "invalid_market_or_settlement_rows": len(invalid_market_rows),
        },
        "market_baseline_on_settled_overlap": metric_summary(settled),
        "draw_audit_on_settled_overlap": draw_summary(settled),
        "coverage": {
            "full_predicted_xi_rate": len(full_xi) / len(settled) if settled else None,
            "both_teams_availability_rate": len(both_availability) / len(settled) if settled else None,
            "full_context_rate": len(full_context) / len(settled) if settled else None,
            "source_tiers": dict(sorted(source_tiers.items())),
            "context_provider_groups": dict(sorted(provider_groups.items())),
        },
        "fit_gate": {"passed": ready, "checks": gates, "thresholds": gate_cfg},
        "rows": settled,
        "audit_errors": {
            "duplicate_context_identities": duplicate_context,
            "result_conflicts": result_conflicts,
            "malformed_context_events": malformed_context,
            "invalid_market_or_settlement_rows": invalid_market_rows,
        },
        "next_action": (
            "fit frozen context residual model on the settled overlap"
            if ready
            else "do not fit context coefficients; run the historical market-residual diagnostic and keep context effects at weight 0"
        ),
        "governance": {
            "formal_weight": 0,
            "training_run": False,
            "probability_generation": False,
            "provider_requests": 0,
            "new_data_collection": False,
            "current_rule_change": False,
        },
    }


def self_test() -> None:
    probabilities = devig({"home": 2.0, "draw": 4.0, "away": 4.0})
    assert abs(sum(probabilities.values()) - 1.0) < 1e-12
    assert max(DIRECTIONS, key=lambda d: probabilities[d]) == "home"
    fixture = {"competition_id": "X", "kickoff_at": "2026-01-01T12:00:00+00:00", "home_team": "Á Home", "away_team": "Away FC"}
    assert identity_key(fixture)[2:] == ("ahome", "awayfc")
    rows = [{"market_probabilities": {"home": 0.4, "draw": 0.35, "away": 0.25}, "actual": "draw", "score": [0, 0]}]
    assert draw_summary(rows)["draw_score_breakdown"]["0-0"] == 1
    assert metric_summary(rows)["count"] == 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}, ensure_ascii=False))
        return 0
    payload = run_audit(load_json(args.config), load_json(args.context), load_json(args.results))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "counts": payload["counts"],
        "market_baseline": payload["market_baseline_on_settled_overlap"],
        "draw_audit": payload["draw_audit_on_settled_overlap"],
        "fit_gate": payload["fit_gate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
