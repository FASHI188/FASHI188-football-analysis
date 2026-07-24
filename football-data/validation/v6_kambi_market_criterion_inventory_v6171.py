#!/usr/bin/env python3
"""Research-only inventory of score-informative Kambi prematch football markets.

Scans immutable raw Kambi envelopes and reports criterion / bet-offer labels that can add
information beyond 1X2, match totals and Asian handicap. No formal/runtime probabilities change.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi"
OUT = ROOT / "manifests" / "v6_kambi_market_criterion_inventory_v6171_status.json"

KEYWORDS = (
    "team total", "team goals", "home team", "away team", "correct score",
    "exact score", "both teams", "btts", "clean sheet", "win to nil",
    "score", "goals"
)


def english(obj):
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("englishLabel") or obj.get("englishName") or obj.get("label") or obj.get("name") or "").strip()


def prematch(offer):
    tags = {str(x).upper() for x in (offer.get("tags") or [])}
    return not tags or "OFFERED_PREMATCH" in tags


def main():
    criterion_counts = Counter()
    type_counts = Counter()
    pairs = Counter()
    informative = Counter()
    examples = defaultdict(list)
    files = parse_failures = offers_seen = prematch_seen = 0

    for path in sorted(RAW_ROOT.rglob("*.json")) if RAW_ROOT.exists() else []:
        files += 1
        try:
            env = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            parse_failures += 1
            continue
        payload = env.get("payload") if isinstance(env, dict) else None
        offers = (payload or {}).get("betOffers") if isinstance(payload, dict) else None
        if not isinstance(offers, list):
            continue
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offers_seen += 1
            if not prematch(offer):
                continue
            prematch_seen += 1
            crit = english(offer.get("criterion") or {}) or "<blank>"
            typ = english(offer.get("betOfferType") or {}) or "<blank>"
            criterion_counts[crit] += 1
            type_counts[typ] += 1
            pairs[(crit, typ)] += 1
            hay = f"{crit} {typ}".casefold()
            if any(k in hay for k in KEYWORDS):
                informative[(crit, typ)] += 1
                key = f"{crit} || {typ}"
                if len(examples[key]) < 3:
                    examples[key].append({
                        "raw_path": str(path.relative_to(ROOT)),
                        "event_id": env.get("event_id"),
                        "observed_at_utc": env.get("observed_at_utc"),
                        "home": (env.get("list_event_identity") or {}).get("homeName"),
                        "away": (env.get("list_event_identity") or {}).get("awayName"),
                    })

    top_pairs = [
        {"criterion": c, "bet_offer_type": t, "count": n}
        for (c, t), n in pairs.most_common(100)
    ]
    score_info = [
        {"criterion": c, "bet_offer_type": t, "count": n, "examples": examples.get(f"{c} || {t}", [])}
        for (c, t), n in informative.most_common()
    ]
    report = {
        "schema_version": "V6.17.1-kambi-market-criterion-inventory-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "raw_files_scanned": files,
        "raw_parse_failures": parse_failures,
        "offers_seen": offers_seen,
        "prematch_offers_seen": prematch_seen,
        "distinct_criteria": len(criterion_counts),
        "distinct_offer_types": len(type_counts),
        "top_pairs": top_pairs,
        "score_informative_candidates": score_info,
        "governance": {
            "research_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
