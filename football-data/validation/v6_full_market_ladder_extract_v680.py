#!/usr/bin/env python3
"""V6.8.0 research-only Kambi full-market ladder extractor.

The existing canonical PIT snapshot intentionally stores one main 1X2/AH/OU surface.  Kambi
raw event-detail envelopes contain many alternate full-time Asian handicap and total-goal
lines.  Those lines are valuable for identifiability of total-goal and score distributions.
This extractor preserves them without changing any formal probability or promotion status.

Only full-time prematch markets are accepted.  Team totals and half markets are excluded.
Every derived ladder row remains hash/filename linked to its immutable raw envelope.
The extractor is deterministic except for its receipt-generation timestamp.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi"
OUT_DIR = ROOT / "evidence" / "market_ladders_v680"
OUT_FILE = OUT_DIR / "kambi_full_time_ladders.json"
STATUS_FILE = ROOT / "manifests" / "v6_full_market_ladder_v680_status.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite_decimal(raw: Any, divisor: float = 1000.0) -> float | None:
    try:
        value = float(raw) / divisor
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def english(obj: dict[str, Any]) -> str:
    return str(obj.get("englishLabel") or obj.get("englishName") or obj.get("label") or obj.get("name") or "").strip()


def is_prematch(offer: dict[str, Any]) -> bool:
    tags = {str(tag).upper() for tag in (offer.get("tags") or [])}
    return not tags or "OFFERED_PREMATCH" in tags


def is_full_time(criterion: dict[str, Any], exact_label: str) -> bool:
    label = english(criterion).lower()
    if label != exact_label.lower():
        return False
    lifetime = str(criterion.get("lifetime") or "").upper()
    return lifetime in {"", "FULL_TIME", "MATCH"}


def changed_bounds(outcomes: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    values = sorted(str(item.get("changedDate")) for item in outcomes if item.get("changedDate"))
    return (values[0], values[-1]) if values else (None, None)


def normalize_total_offer(offer: dict[str, Any]) -> dict[str, Any] | None:
    if not is_prematch(offer):
        return None
    criterion = offer.get("criterion") or {}
    offer_type = offer.get("betOfferType") or {}
    type_name = english(offer_type).lower()
    label = english(criterion)
    if type_name not in {"over/under", "asian over/under"}:
        return None
    if not (is_full_time(criterion, "Total Goals") or is_full_time(criterion, "Asian Total")):
        return None
    outcomes = [item for item in (offer.get("outcomes") or []) if isinstance(item, dict)]
    over = next((item for item in outcomes if str(item.get("type")) == "OT_OVER"), None)
    under = next((item for item in outcomes if str(item.get("type")) == "OT_UNDER"), None)
    if not over or not under:
        return None
    line_raw = over.get("line") if over.get("line") is not None else under.get("line")
    line = finite_decimal(line_raw)
    over_price, under_price = finite_decimal(over.get("odds")), finite_decimal(under.get("odds"))
    if line is None or over_price is None or under_price is None or over_price <= 1 or under_price <= 1:
        return None
    changed_min, changed_max = changed_bounds([over, under])
    tags = [str(tag) for tag in (offer.get("tags") or [])]
    return {
        "offer_id": offer.get("id"),
        "market_kind": "asian_total" if type_name == "asian over/under" or label.lower() == "asian total" else "total_goals",
        "criterion": label,
        "line": line,
        "over": over_price,
        "under": under_price,
        "main_line": "MAIN_LINE" in {tag.upper() for tag in tags},
        "changed_at_min": changed_min,
        "changed_at_max": changed_max,
        "tags": tags,
    }


def normalize_ah_offer(offer: dict[str, Any], home_name: str, away_name: str) -> dict[str, Any] | None:
    if not is_prematch(offer):
        return None
    criterion = offer.get("criterion") or {}
    offer_type = offer.get("betOfferType") or {}
    if english(offer_type).lower() != "asian handicap" or not is_full_time(criterion, "Asian Handicap"):
        return None
    outcomes = [item for item in (offer.get("outcomes") or []) if isinstance(item, dict)]
    if len(outcomes) != 2:
        return None
    rows = []
    for item in outcomes:
        price, line = finite_decimal(item.get("odds")), finite_decimal(item.get("line"))
        if price is None or line is None or price <= 1:
            return None
        rows.append({
            "participant": item.get("participant") or item.get("englishLabel") or item.get("label"),
            "line": line,
            "odds": price,
            "outcome_id": item.get("id"),
            "changed_at": item.get("changedDate"),
        })
    changed_min, changed_max = changed_bounds(outcomes)
    tags = [str(tag) for tag in (offer.get("tags") or [])]
    return {
        "offer_id": offer.get("id"),
        "market_kind": "asian_handicap",
        "criterion": english(criterion),
        "home_name_reference": home_name,
        "away_name_reference": away_name,
        "outcomes": rows,
        "main_line": "MAIN_LINE" in {tag.upper() for tag in tags},
        "changed_at_min": changed_min,
        "changed_at_max": changed_max,
        "tags": tags,
    }


def normalize_1x2_offer(offer: dict[str, Any]) -> dict[str, Any] | None:
    if not is_prematch(offer):
        return None
    outcomes = [item for item in (offer.get("outcomes") or []) if isinstance(item, dict)]
    by_type = {str(item.get("type")): item for item in outcomes}
    if not {"OT_ONE", "OT_CROSS", "OT_TWO"}.issubset(by_type):
        return None
    prices = {key: finite_decimal(by_type[type_code].get("odds")) for key, type_code in {"home": "OT_ONE", "draw": "OT_CROSS", "away": "OT_TWO"}.items()}
    if any(value is None or value <= 1 for value in prices.values()):
        return None
    criterion = offer.get("criterion") or {}
    label = english(criterion).lower()
    if "half" in label or "helft" in label:
        return None
    changed_min, changed_max = changed_bounds(outcomes)
    tags = [str(tag) for tag in (offer.get("tags") or [])]
    return {
        "offer_id": offer.get("id"),
        "market_kind": "one_x_two",
        "criterion": english(criterion),
        **prices,
        "main_line": "MAIN_LINE" in {tag.upper() for tag in tags},
        "changed_at_min": changed_min,
        "changed_at_max": changed_max,
        "tags": tags,
    }


def extract_file(path: Path) -> dict[str, Any] | None:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    offers = (payload or {}).get("betOffers") if isinstance(payload, dict) else None
    if not isinstance(offers, list):
        return None
    identity = envelope.get("list_event_identity") or {}
    home = str(identity.get("homeName") or "")
    away = str(identity.get("awayName") or "")
    start = identity.get("start")
    totals, handicaps, one_x_two = [], [], []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        total = normalize_total_offer(offer)
        if total:
            totals.append(total)
        handicap = normalize_ah_offer(offer, home, away)
        if handicap:
            handicaps.append(handicap)
        one = normalize_1x2_offer(offer)
        if one:
            one_x_two.append(one)
    if not totals and not handicaps and not one_x_two:
        return None
    totals.sort(key=lambda row: (row["line"], row["market_kind"], int(row["offer_id"] or 0)))
    handicaps.sort(key=lambda row: (row["outcomes"][0]["line"], int(row["offer_id"] or 0)))
    one_x_two.sort(key=lambda row: (not row["main_line"], int(row["offer_id"] or 0)))
    distinct_total_lines = sorted({float(row["line"]) for row in totals})
    distinct_ah_home_lines = sorted({float(row["outcomes"][0]["line"]) for row in handicaps})
    observed = envelope.get("observed_at_utc")
    return {
        "raw_path": str(path.relative_to(ROOT)),
        "raw_file_sha256": sha256_file(path),
        "payload_sha256": envelope.get("payload_sha256"),
        "provider_name": envelope.get("provider_name"),
        "provider_group": envelope.get("provider_group"),
        "observed_at_utc": observed,
        "event_id": envelope.get("event_id"),
        "home_team_source": home,
        "away_team_source": away,
        "kickoff_utc": start,
        "event_state": identity.get("state"),
        "competition_source": identity.get("group"),
        "one_x_two_offers": one_x_two,
        "total_goal_ladder": totals,
        "asian_handicap_ladder": handicaps,
        "diagnostics": {
            "one_x_two_offer_count": len(one_x_two),
            "total_offer_count": len(totals),
            "distinct_total_line_count": len(distinct_total_lines),
            "distinct_total_lines": distinct_total_lines,
            "asian_handicap_offer_count": len(handicaps),
            "distinct_ah_line_count": len(distinct_ah_home_lines),
            "distinct_ah_home_lines": distinct_ah_home_lines,
            "multi_total_identifiability_context": len(distinct_total_lines) >= 3,
            "multi_ah_context": len(distinct_ah_home_lines) >= 3,
        },
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    paths = sorted(RAW_ROOT.rglob("*.json")) if RAW_ROOT.exists() else []
    bundles = []
    failures = 0
    for path in paths:
        try:
            row = extract_file(path)
            if row:
                bundles.append(row)
        except Exception:
            failures += 1
    bundles.sort(key=lambda row: (str(row.get("observed_at_utc")), str(row.get("event_id")), str(row.get("raw_path"))))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    aggregate = {
        "schema_version": "V6.8.0-kambi-full-market-ladders-r1",
        "generated_at_utc": generated,
        "research_only": True,
        "formal_probability_change": False,
        "bundle_count": len(bundles),
        "bundles": bundles,
    }
    OUT_FILE.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    counters = Counter()
    total_lines = []
    ah_lines = []
    for row in bundles:
        diag = row["diagnostics"]
        counters["with_1x2"] += int(diag["one_x_two_offer_count"] > 0)
        counters["with_total"] += int(diag["total_offer_count"] > 0)
        counters["with_ah"] += int(diag["asian_handicap_offer_count"] > 0)
        counters["with_3plus_total_lines"] += int(diag["distinct_total_line_count"] >= 3)
        counters["with_3plus_ah_lines"] += int(diag["distinct_ah_line_count"] >= 3)
        total_lines.append(diag["distinct_total_line_count"])
        ah_lines.append(diag["distinct_ah_line_count"])
    status = {
        "schema_version": "V6.8.0-full-market-ladder-status-r1",
        "generated_at_utc": generated,
        "status": "PASS" if bundles else "FAIL_NO_RAW_LADDERS",
        "raw_files_scanned": len(paths),
        "raw_parse_failures": failures,
        "bundle_count": len(bundles),
        "coverage": dict(counters),
        "mean_distinct_total_lines": (sum(total_lines) / len(total_lines)) if total_lines else 0.0,
        "mean_distinct_ah_lines": (sum(ah_lines) / len(ah_lines)) if ah_lines else 0.0,
        "derived_evidence_path": str(OUT_FILE.relative_to(ROOT)),
        "governance": {
            "research_only": True,
            "single_provider_ladders_not_promotion_eligible": True,
            "no_current_rule_change": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "half_and_team_totals_excluded": True,
        },
    }
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if bundles else 2


if __name__ == "__main__":
    raise SystemExit(main())
