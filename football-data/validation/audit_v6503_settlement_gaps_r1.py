#!/usr/bin/env python3
"""Read-only audit of unsettled frozen V6.50.3 O/U -> Direct-T events.

This diagnostic performs no provider/network request and reads only repository-local
frozen prediction events, the existing timestamped result inbox, and the rebuilt
historical score ledger. It does not inspect any new outcome source, fit a model,
change a threshold, or open the latest confirmation block. formal_weight=0.
"""
from __future__ import annotations

import csv
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "forward" / "v6_ou_kl_direct_total_events_v6503.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
HIST = ROOT / "manifests" / "v510_existing_score_market_pit_ledger_r1_rows.csv"
OUT = ROOT / "manifests" / "v6503_settlement_gap_audit_r1.json"


def norm_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum())


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def iso_second(value: Any) -> str:
    dt = parse_dt(value)
    return dt.replace(microsecond=0).isoformat() if dt else ""


def date_key(value: Any) -> str:
    dt = parse_dt(value)
    return dt.date().isoformat() if dt else ""


def fixture_key(comp: Any, kickoff: Any, home: Any, away: Any) -> tuple[str, str, str, str]:
    return (str(comp or ""), iso_second(kickoff), norm_team(home), norm_team(away))


def date_team_key(comp: Any, kickoff: Any, home: Any, away: Any) -> tuple[str, str, str, str]:
    return (str(comp or ""), date_key(kickoff), norm_team(home), norm_team(away))


def load_json(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"JSON root is not an object: {path}")
    return x


def main() -> None:
    events_root = load_json(EVENTS)
    results_root = load_json(RESULTS)
    events = list(events_root.get("events", []))
    results = list(results_root.get("results", []))

    primary_exact: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    primary_date_rows: Counter[tuple[str, str]] = Counter()
    primary_pair: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    inbox_dates_by_comp: dict[str, list[str]] = defaultdict(list)
    for row in results:
        comp = str(row.get("competition_id") or "")
        kickoff = row.get("kickoff_at")
        d = date_key(kickoff)
        h = norm_team(row.get("home_team")); a = norm_team(row.get("away_team"))
        primary_exact[fixture_key(comp, kickoff, h, a)].append(row)
        if comp and d:
            primary_date_rows[(comp, d)] += 1
            inbox_dates_by_comp[comp].append(d)
        if comp and h and a:
            primary_pair[(comp, h, a)].append(row)

    if not HIST.is_file():
        raise RuntimeError("historical score ledger missing; rebuild audit ledger first")
    historical_exact: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    historical_date_rows: Counter[tuple[str, str]] = Counter()
    hist_dates_by_comp: dict[str, list[str]] = defaultdict(list)
    with HIST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            comp = str(row.get("competition_id") or "")
            d = str(row.get("date_key") or "")
            h = norm_team(row.get("home_team")); a = norm_team(row.get("away_team"))
            if comp and d:
                historical_date_rows[(comp, d)] += 1
                hist_dates_by_comp[comp].append(d)
            if comp and d and h and a:
                historical_exact[(comp, d, h, a)].append(row)

    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    comp_status: dict[str, Counter[str]] = defaultdict(Counter)
    date_status: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        payload = event.get("payload") or {}
        fixture = payload.get("fixture_identity") or {}
        comp = str(fixture.get("competition_id") or "")
        kickoff = fixture.get("kickoff_at")
        d = date_key(kickoff)
        home = str(fixture.get("home_team") or "")
        away = str(fixture.get("away_team") or "")
        h = norm_team(home); a = norm_team(away)
        pkey = fixture_key(comp, kickoff, home, away)
        hkey = (comp, d, h, a)
        exact_primary_n = len(primary_exact.get(pkey, []))
        exact_hist_n = len(historical_exact.get(hkey, []))
        same_pair_rows = primary_pair.get((comp, h, a), [])
        same_pair_other_kickoffs = sorted({iso_second(r.get("kickoff_at")) for r in same_pair_rows if fixture_key(comp, r.get("kickoff_at"), home, away) != pkey})
        inbox_same_date_n = int(primary_date_rows[(comp, d)])
        hist_same_date_n = int(historical_date_rows[(comp, d)])
        inbox_max_date = max(inbox_dates_by_comp.get(comp, [""]))
        hist_max_date = max(hist_dates_by_comp.get(comp, [""]))

        if exact_primary_n == 1:
            status = "SETTLED_PRIMARY_EXACT"
        elif exact_primary_n > 1:
            status = "BLOCKED_PRIMARY_DUPLICATE"
        elif exact_hist_n >= 1:
            status = "SETTLED_HISTORICAL_DATE_TEAM"
        else:
            status = "UNMATCHED_LOCAL_REPOSITORY"

        status_counts[status] += 1
        comp_status[comp][status] += 1
        date_status[d][status] += 1
        rows.append({
            "sequence": int(event.get("sequence") or 0),
            "match_id": str(event.get("match_id") or ""),
            "competition_id": comp,
            "kickoff_at": iso_second(kickoff),
            "date": d,
            "home_team": home,
            "away_team": away,
            "status": status,
            "primary_exact_n": exact_primary_n,
            "historical_exact_date_team_n": exact_hist_n,
            "result_inbox_same_comp_date_n": inbox_same_date_n,
            "historical_same_comp_date_n": hist_same_date_n,
            "result_inbox_same_pair_other_kickoffs": same_pair_other_kickoffs,
            "result_inbox_comp_max_date": inbox_max_date,
            "historical_comp_max_date": hist_max_date,
            "event_after_result_inbox_comp_max_date": bool(d and (not inbox_max_date or d > inbox_max_date)),
            "event_after_historical_comp_max_date": bool(d and (not hist_max_date or d > hist_max_date)),
        })

    unmatched = [r for r in rows if r["status"] == "UNMATCHED_LOCAL_REPOSITORY"]
    unmatched_reason_counts = Counter()
    for r in unmatched:
        if r["event_after_result_inbox_comp_max_date"]:
            unmatched_reason_counts["EVENT_AFTER_RESULT_INBOX_COMP_MAX_DATE"] += 1
        if r["result_inbox_same_comp_date_n"] == 0:
            unmatched_reason_counts["NO_RESULT_INBOX_ROWS_SAME_COMP_DATE"] += 1
        if r["event_after_historical_comp_max_date"]:
            unmatched_reason_counts["EVENT_AFTER_HISTORICAL_COMP_MAX_DATE"] += 1
        if r["historical_same_comp_date_n"] == 0:
            unmatched_reason_counts["NO_HISTORICAL_ROWS_SAME_COMP_DATE"] += 1
        if r["result_inbox_same_pair_other_kickoffs"]:
            unmatched_reason_counts["SAME_TEAM_PAIR_EXISTS_OTHER_KICKOFF"] += 1

    result = {
        "schema_version": "V6503_SETTLEMENT_GAP_AUDIT_R1",
        "classification": "REPOSITORY_LOCAL_READ_ONLY_SETTLEMENT_GAP_DIAGNOSTIC",
        "event_count": len(events),
        "result_inbox_count": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "unmatched_count": len(unmatched),
        "unmatched_reason_signals": dict(sorted(unmatched_reason_counts.items())),
        "competition_status": {k: dict(sorted(v.items())) for k, v in sorted(comp_status.items())},
        "date_status": {k: dict(sorted(v.items())) for k, v in sorted(date_status.items())},
        "unmatched_by_competition": dict(sorted(Counter(r["competition_id"] for r in unmatched).items())),
        "unmatched_by_date": dict(sorted(Counter(r["date"] for r in unmatched).items())),
        "rows": rows,
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "new_outcome_source_opened": False,
            "latest_confirmation_block_opened": False,
            "model_fit_performed": False,
            "parameter_search_performed": False,
            "formal_asset_changes": 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "classification": result["classification"],
        "event_count": result["event_count"],
        "result_inbox_count": result["result_inbox_count"],
        "status_counts": result["status_counts"],
        "unmatched_count": result["unmatched_count"],
        "unmatched_reason_signals": result["unmatched_reason_signals"],
        "unmatched_by_competition": result["unmatched_by_competition"],
        "unmatched_by_date": result["unmatched_by_date"],
        "governance": result["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
