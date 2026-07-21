#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from marathonbet_v523_adapter_v5517 import build_snapshot, parse_fixture
from prospective_market_snapshot_v523 import validate

CASES = [
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "home_team": "Alaves",
        "away_team": "Getafe",
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
        "html": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "ESP_LaLiga__2026-07-21T173148+0000__2fa6189d33e8.html",
        "meta": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "ESP_LaLiga__2026-07-21T173148+0000__2fa6189d33e8.json",
        "expected": {
            "displayed_time": "15 Aug 18:30",
            "one_x_two": {"home": 2.45, "draw": 2.90, "away": 3.58},
            "asian_handicap": {"line": 0.0, "home": 1.60, "away": 2.34},
            "over_under": {"line": 1.5, "over": 1.68, "under": 2.21},
        },
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "home_team": "Bayern Munich",
        "away_team": "Stuttgart",
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
        "html": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "GER_Bundesliga__2026-07-21T173150+0000__398543dc3b50.html",
        "meta": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "GER_Bundesliga__2026-07-21T173150+0000__398543dc3b50.json",
        "expected": {
            "displayed_time": "28 Aug 19:30",
            "one_x_two": {"home": 1.30, "draw": 6.70, "away": 8.80},
            "asian_handicap": {"line": -2.0, "home": 2.12, "away": 1.72},
            "over_under": {"line": 4.5, "over": 2.39, "under": 1.59},
        },
    },
]


def close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 1e-12


def main() -> int:
    results = []
    for case in CASES:
        raw = case["html"].read_bytes()
        meta = json.loads(case["meta"].read_text(encoding="utf-8"))
        parsed = parse_fixture(
            raw,
            home_team=case["home_team"],
            away_team=case["away_team"],
            target_kickoff_utc=case["kickoff_utc"],
        )
        expected = case["expected"]
        assert parsed["displayed_time"] == expected["displayed_time"]
        assert parsed["display_timezone"] == "Europe/London"
        assert parsed["page_kickoff_utc"] == case["kickoff_utc"]
        assert parsed["kickoff_skew_seconds"] == 0.0
        for key in ("home", "draw", "away"):
            assert close(parsed["one_x_two"][key], expected["one_x_two"][key])
        for key in ("line", "home", "away"):
            assert close(parsed["asian_handicap"][key], expected["asian_handicap"][key])
        for key in ("line", "over", "under"):
            assert close(parsed["over_under"][key], expected["over_under"][key])

        snapshot = build_snapshot(
            raw,
            meta,
            competition_id=case["competition_id"],
            season=case["season"],
            home_team=case["home_team"],
            away_team=case["away_team"],
            kickoff_utc=case["kickoff_utc"],
        )
        validation = validate(snapshot)
        assert validation["passed"] is True
        assert validation["formal_pit_eligible"] is True
        assert validation["surface_timestamp_spread_seconds"] == 0.0
        assert snapshot["provider_name"] == "Marathonbet"
        assert snapshot["provider_group"] == "marathonbet"
        assert snapshot["source_adapter"]["display_timezone"] == "Europe/London"
        assert snapshot["source_adapter"]["kickoff_skew_seconds"] == 0.0
        results.append({
            "competition_id": case["competition_id"],
            "home_team": case["home_team"],
            "away_team": case["away_team"],
            "observed_at_utc": snapshot["freeze_utc"],
            "displayed_kickoff": snapshot["source_adapter"]["displayed_kickoff"],
            "converted_kickoff_utc": snapshot["source_adapter"]["displayed_kickoff_converted_utc"],
            "one_x_two": snapshot["one_x_two"],
            "asian_handicap": snapshot["asian_handicap"],
            "over_under": snapshot["over_under"],
            "v523_validation": validation,
            "raw_html_sha256": snapshot["source_adapter"]["parent_raw_html_sha256"],
        })

    receipt = {
        "schema_version": "V5.5.17-marathonbet-v523-adapter-acceptance-r1",
        "status": "PASS",
        "timezone_contract": {
            "page_locale": "en",
            "display_timezone": "Europe/London",
            "cross_fixture_regression_count": len(CASES),
            "all_kickoff_skews_seconds": [row["v523_validation"].get("surface_timestamp_spread_seconds") for row in results],
            "identity_time_gate": "Exact team pair plus displayed local kickoff converted with Europe/London must equal registered kickoff UTC within 60 seconds.",
        },
        "cases": results,
        "formal_snapshot_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "consensus_change": False,
        "promotion_sample_count_change": 0,
        "policy": "Adapter acceptance only. These directly observed single-provider snapshots may be persisted later as PIT evidence, but they must not enter model-promotion consensus unless a second independent provider_group is captured within the configured synchronization window.",
    }
    out = ROOT / "manifests" / "marathonbet_v523_adapter_v5517_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
