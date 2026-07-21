#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from kambi_v523_adapter_v5511 import _line, _odds, build_snapshot, extract
from prospective_market_snapshot_v523 import validate

SAMPLE = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "event_detail_samples" / "event_1028386270__2026-07-21T171008+0000.json"
MANIFEST = ROOT / "manifests" / "kambi_v523_adapter_v5511_status.json"


def main() -> int:
    envelope = json.loads(SAMPLE.read_text(encoding="utf-8"))
    extracted = extract(envelope, home_team="Floriana FC", away_team="KF Drita")

    assert _odds(3100) == 3.1
    assert _odds(1300) == 1.3
    assert _line(-500) == -0.5
    assert _line(250) == 0.25

    one = extracted["one_x_two"]
    ah = extracted["asian_handicap"]
    ou = extracted["over_under"]
    assert {"home", "draw", "away"}.issubset(one)
    assert all(one[k] > 1.0 for k in ("home", "draw", "away"))
    assert extracted["candidate_counts"]["asian_handicap"] >= 2
    assert extracted["candidate_counts"]["over_under"] >= 1
    assert abs(float(ah["line"]) + float(ah["away_line"])) < 1e-12
    assert abs(float(ah["line"]) * 4 - round(float(ah["line"]) * 4)) < 1e-12
    assert abs(float(ou["line"]) * 4 - round(float(ou["line"]) * 4)) < 1e-12
    assert ah["home"] > 1.0 and ah["away"] > 1.0
    assert ou["over"] > 1.0 and ou["under"] > 1.0

    payload = envelope["payload"]
    full_time_asian = 0
    half_time_asian = 0
    three_way_handicap = 0
    for offer in payload.get("betOffers", []):
        if not isinstance(offer, dict):
            continue
        criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
        offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
        english = str(criterion.get("englishLabel") or "")
        type_id = int(offer_type.get("id") or -1)
        if type_id == 7 and english == "Asian Handicap" and str(criterion.get("lifetime") or "") == "FULL_TIME":
            full_time_asian += 1
        if type_id == 7 and "1st Half" in english:
            half_time_asian += 1
        if type_id == 11 or "3-Way Handicap" in english:
            three_way_handicap += 1
    assert full_time_asian >= extracted["candidate_counts"]["asian_handicap"]
    assert half_time_asian >= 1, "fixture must exercise half-time AH exclusion"
    assert three_way_handicap >= 1, "fixture must exercise 3-way handicap exclusion"

    snapshot = build_snapshot(
        envelope,
        competition_id="RESEARCH_ONLY_TEST_DOMAIN",
        season="2026",
        home_team="Floriana FC",
        away_team="KF Drita",
        kickoff_utc="2026-07-21T17:30:00+00:00",
    )
    validation = validate(snapshot)
    assert validation["passed"] is True
    assert validation["formal_pit_eligible"] is True
    assert validation["surface_timestamp_spread_seconds"] == 0.0
    assert snapshot["provider_name"] == "BetCity NL"
    assert snapshot["provider_group"] == "kambi"
    assert snapshot["source_adapter"]["kambi_integer_scaling"] == {"odds_divisor": 1000, "line_divisor": 1000}
    assert snapshot["source_adapter"]["provider_changed_timestamp_role"] == "audit_only_not_observation_time"

    receipt = {
        "schema_version": "V5.5.11-kambi-v523-adapter-acceptance-r1",
        "status": "PASS",
        "sample": str(SAMPLE.relative_to(ROOT)),
        "sample_observed_at_utc": envelope["observed_at_utc"],
        "sample_event_id": envelope["event_id"],
        "extracted": {
            "one_x_two": snapshot["one_x_two"],
            "asian_handicap": snapshot["asian_handicap"],
            "over_under": snapshot["over_under"],
            "asian_handicap_candidate_count": extracted["candidate_counts"]["asian_handicap"],
            "over_under_candidate_count": extracted["candidate_counts"]["over_under"],
        },
        "exclusion_coverage": {
            "full_time_asian_offers_in_sample": full_time_asian,
            "half_time_asian_offers_in_sample": half_time_asian,
            "three_way_handicap_offers_in_sample": three_way_handicap,
            "half_time_asian_excluded": True,
            "three_way_handicap_excluded": True,
        },
        "scaling_checks": {
            "odds_3100_to_decimal": _odds(3100),
            "odds_1300_to_decimal": _odds(1300),
            "line_minus500_to_handicap": _line(-500),
            "line_250_to_handicap": _line(250),
        },
        "v523_validation": validation,
        "formal_snapshot_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Adapter acceptance validates extraction semantics only. It does not create a formal snapshot for this research-only fixture. Future registered target fixtures must still pass identity, pre-kickoff observation and V5.2.3 hard gates at capture time.",
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
