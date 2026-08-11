#!/usr/bin/env python3
"""Independent deterministic tests for the R43A Betfair BASIC coverage auditor."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import audit_r43a_betfair_basic_pit_coverage as mod  # noqa: E402


def main() -> None:
    cfg = mod.load_json(mod.DEFAULT_CONFIG)
    kick = mod.iso_to_ms("2026-01-10T15:00:00.000Z")
    runner_sets = {
        "1.mo": [(1, "Home"), (2, "Away"), (3, "The Draw")],
        "1.ah": [(1, "Home"), (2, "Away")],
        "1.ou15": [(10, "Under 1.5 Goals"), (11, "Over 1.5 Goals")],
        "1.ou25": [(20, "Under 2.5 Goals"), (21, "Over 2.5 Goals")],
        "1.ou35": [(30, "Under 3.5 Goals"), (31, "Over 3.5 Goals")],
        "1.ou45": [(40, "Under 4.5 Goals"), (41, "Over 4.5 Goals")],
    }
    market_types = {
        "1.mo": ("MATCH_ODDS", "Match Odds", "ODDS"),
        "1.ah": ("ASIAN_HANDICAP", "Asian Handicap", "ASIAN_HANDICAP_DOUBLE_LINE"),
        "1.ou15": ("OVER_UNDER_15", "Over/Under 1.5 Goals", "ODDS"),
        "1.ou25": ("OVER_UNDER_25", "Over/Under 2.5 Goals", "ODDS"),
        "1.ou35": ("OVER_UNDER_35", "Over/Under 3.5 Goals", "ODDS"),
        "1.ou45": ("OVER_UNDER_45", "Over/Under 4.5 Goals", "ODDS"),
    }
    definitions = []
    for mid, runners in runner_sets.items():
        mtype, name, betting = market_types[mid]
        definitions.append({
            "id": mid,
            "marketDefinition": {
                "eventId": "E1",
                "eventName": "Home v Away",
                "eventTypeId": "1",
                "countryCode": "GB",
                "marketType": mtype,
                "name": name,
                "bettingType": betting,
                "marketTime": "2026-01-10T15:00:00.000Z",
                "runners": [{"id": rid, "name": rname, "handicap": 0.0} for rid, rname in runners],
            },
        })

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "event.jsonl"
        rows = []
        for idx, minutes in enumerate((1440, 360, 60)):
            pt = kick - minutes * 60_000
            price_map = {
                mid: [(rid, 2.0 + 0.01 * idx + j * 0.1) for j, (rid, _) in enumerate(runners)]
                for mid, runners in runner_sets.items()
            }
            rows.append(mod.synthetic_message(pt, definitions if idx == 0 else [], price_map))
        rows.append({
            "op": "mcm",
            "pt": kick + 1000,
            "mc": [{"id": "1.mo", "marketDefinition": {"runners": [{"id": 1, "status": "WINNER"}]}}],
        })
        path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")

        local = json.loads(json.dumps(cfg))
        local["coverage_gate"]["minimum_events_all_freezes_complete"] = 1
        local["coverage_gate"]["minimum_distinct_countries"] = 1
        local["coverage_gate"]["minimum_distinct_calendar_months"] = 1
        markets, receipt, _ = mod.parse_files([path], local)
        coverage = mod.audit(markets, local)
        assert coverage["coverage_gate_pass"] is True
        assert coverage["events_all_required_freezes_complete"] == 1
        assert receipt.runner_ltp_updates == 39
        assert mod.ou_line(markets["1.ou25"], local) == 2.5
        assert mod.category(markets["1.ah"], local)[0] == "ASIAN_HANDICAP"

        stale = json.loads(json.dumps(local))
        stale["freeze_contract"][2]["max_quote_age_minutes"] = 30
        stale_cov = mod.audit(markets, stale)
        assert stale_cov["events_all_required_freezes_complete"] == 0

    assert cfg["governance"]["model_fit"] == 0
    assert cfg["governance"]["protected_blind_access"] == 0
    assert cfg["coverage_gate"]["labels_used_for_gate"] is False
    print(json.dumps({"status": "PASS_R43A_INDEPENDENT_SELF_TEST", "synthetic_ltp_updates": 39}))


if __name__ == "__main__":
    main()
