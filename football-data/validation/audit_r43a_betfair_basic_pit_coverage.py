#!/usr/bin/env python3
"""R43A zero-label Betfair BASIC synchronized-market coverage audit.

This stage intentionally does NOT read football results or fit a model. It only asks
whether timestamped Betfair historical stream files contain enough pre-kickoff market
shape to support a later independent fixed-200 experiment.

The audit uses one common cutoff per event/freeze. For every required market it takes
the latest LTP observation at or before the cutoff and rejects stale observations using
pre-registered age limits. Numeric prices are not used to select events beyond checking
that a valid positive decimal LTP exists.
"""
from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r43a_betfair_basic_pit_coverage.json"
DEFAULT_OUT = ROOT / "manifests" / "r43a_betfair_basic_pit_coverage_status.json"


class AuditError(RuntimeError):
    pass


@dataclass
class Market:
    market_id: str
    event_id: str | None = None
    event_name: str | None = None
    event_type_id: str | None = None
    country_code: str | None = None
    market_type: str | None = None
    market_name: str | None = None
    betting_type: str | None = None
    market_time_ms: int | None = None
    runner_ids: list[int] = field(default_factory=list)
    runner_names: dict[int, str] = field(default_factory=dict)
    runner_handicaps: dict[int, float] = field(default_factory=dict)
    observations: dict[int, list[tuple[int, float]]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class ParseReceipt:
    files: int = 0
    json_messages: int = 0
    market_changes: int = 0
    runner_ltp_updates: int = 0
    malformed_lines: int = 0
    post_kickoff_messages_ignored: int = 0


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"config must be object: {path}")
    return value


def iso_to_ms(value: str) -> int:
    text = str(value).replace("Z", "+00:00")
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def iter_input_files(cfg: dict[str, Any], explicit: list[Path] | None = None) -> list[Path]:
    accepted = {str(x).lower() for x in cfg["source_contract"]["accepted_extensions"]}
    if explicit:
        candidates = [p.resolve() for p in explicit]
    else:
        candidates: list[Path] = []
        for raw_root in cfg["source_contract"]["input_roots"]:
            base = ROOT.parent / str(raw_root) if not str(raw_root).startswith("football-data/") else ROOT.parent / str(raw_root)
            if base.exists():
                candidates.extend(p for p in base.rglob("*") if p.is_file())
    return sorted({p for p in candidates if p.suffix.lower() in accepted})


def open_lines(path: Path) -> Iterable[str]:
    if path.suffix.lower() == ".bz2":
        with bz2.open(path, "rt", encoding="utf-8", errors="strict") as handle:
            yield from handle
    else:
        with path.open("rt", encoding="utf-8", errors="strict") as handle:
            yield from handle


def valid_ltp(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def update_definition(market: Market, md: dict[str, Any]) -> None:
    # Deliberately access only pre-match metadata fields. Settlement/winner status is ignored.
    market.event_id = str(md.get("eventId")) if md.get("eventId") is not None else market.event_id
    market.event_name = str(md.get("eventName")) if md.get("eventName") is not None else market.event_name
    market.event_type_id = str(md.get("eventTypeId")) if md.get("eventTypeId") is not None else market.event_type_id
    market.country_code = str(md.get("countryCode")) if md.get("countryCode") is not None else market.country_code
    market.market_type = str(md.get("marketType")) if md.get("marketType") is not None else market.market_type
    market.market_name = str(md.get("name")) if md.get("name") is not None else market.market_name
    market.betting_type = str(md.get("bettingType")) if md.get("bettingType") is not None else market.betting_type
    if md.get("marketTime") is not None:
        market.market_time_ms = iso_to_ms(str(md["marketTime"]))
    runners = md.get("runners")
    if isinstance(runners, list):
        ids: list[int] = []
        names: dict[int, str] = {}
        handicaps: dict[int, float] = {}
        for row in runners:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            rid = int(row["id"])
            ids.append(rid)
            if row.get("name") is not None:
                names[rid] = str(row["name"])
            if row.get("handicap") is not None:
                try:
                    handicaps[rid] = float(row["handicap"])
                except (TypeError, ValueError):
                    pass
        if ids:
            market.runner_ids = ids
            market.runner_names.update(names)
            market.runner_handicaps.update(handicaps)


def parse_files(paths: list[Path], cfg: dict[str, Any]) -> tuple[dict[str, Market], ParseReceipt, list[dict[str, Any]]]:
    markets: dict[str, Market] = {}
    receipt = ParseReceipt(files=len(paths))
    file_receipts: list[dict[str, Any]] = []

    for path in paths:
        messages = 0
        changes = 0
        updates = 0
        malformed = 0
        ignored = 0
        known_earliest_kick: int | None = None
        for raw in open_lines(path):
            text = raw.strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(msg, dict) or msg.get("pt") is None:
                malformed += 1
                continue
            pt = int(msg["pt"])
            # Event files are published-time ordered. Once the event is at kickoff, stop
            # consuming the file so settlement/result fields cannot enter the audit path.
            if known_earliest_kick is not None and pt >= known_earliest_kick:
                ignored += 1
                break
            messages += 1
            receipt.json_messages += 1
            mc_list = msg.get("mc")
            if not isinstance(mc_list, list):
                continue
            for mc in mc_list:
                if not isinstance(mc, dict) or mc.get("id") is None:
                    continue
                changes += 1
                receipt.market_changes += 1
                mid = str(mc["id"])
                market = markets.setdefault(mid, Market(market_id=mid))
                md = mc.get("marketDefinition")
                if isinstance(md, dict):
                    update_definition(market, md)
                    if market.market_time_ms is not None:
                        known_earliest_kick = (
                            market.market_time_ms
                            if known_earliest_kick is None
                            else min(known_earliest_kick, market.market_time_ms)
                        )
                if market.market_time_ms is not None and pt >= market.market_time_ms:
                    ignored += 1
                    receipt.post_kickoff_messages_ignored += 1
                    continue
                rc = mc.get("rc")
                if not isinstance(rc, list):
                    continue
                for change in rc:
                    if not isinstance(change, dict) or change.get("id") is None or "ltp" not in change:
                        continue
                    price = valid_ltp(change.get("ltp"))
                    if price is None:
                        continue
                    market.observations[int(change["id"])].append((pt, price))
                    updates += 1
                    receipt.runner_ltp_updates += 1
        receipt.malformed_lines += malformed
        receipt.post_kickoff_messages_ignored += ignored
        file_receipts.append({
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "json_messages_pre_kickoff": messages,
            "market_changes_pre_kickoff": changes,
            "valid_ltp_updates_pre_kickoff": updates,
            "malformed_lines": malformed,
            "post_kickoff_boundary_hits": ignored,
        })
    return markets, receipt, file_receipts


def ou_line(market: Market, cfg: dict[str, Any]) -> float | None:
    prefix = str(cfg["market_contract"]["over_under_market_type_prefix"])
    mtype = str(market.market_type or "")
    if mtype.startswith(prefix):
        suffix = mtype[len(prefix):]
        if suffix.isdigit():
            return int(suffix) / 10.0
    name = str(market.market_name or "")
    match = re.search(r"Over/Under\s+([0-9]+(?:\.[0-9]+)?)\s+Goals", name, flags=re.I)
    return float(match.group(1)) if match else None


def category(market: Market, cfg: dict[str, Any]) -> tuple[str, float | None] | None:
    mtype = str(market.market_type or "")
    betting = str(market.betting_type or "")
    contract = cfg["market_contract"]
    if mtype == str(contract["match_odds_market_type"]):
        return "MATCH_ODDS", None
    if str(contract["asian_handicap_market_type_contains"]) in mtype or "ASIAN_HANDICAP" in betting:
        return "ASIAN_HANDICAP", None
    line = ou_line(market, cfg)
    if line is not None:
        recognized = {float(x) for x in contract["recognized_over_under_lines"]}
        if line in recognized:
            return "OU", line
    return None


def latest_prices_at(market: Market, cutoff_ms: int, max_age_ms: int) -> dict[int, float]:
    out: dict[int, float] = {}
    for rid, points in market.observations.items():
        best_pt = -1
        best_price: float | None = None
        for pt, price in points:
            if pt <= cutoff_ms and pt > best_pt:
                best_pt = pt
                best_price = price
        if best_price is not None and cutoff_ms - best_pt <= max_age_ms:
            out[int(rid)] = float(best_price)
    return out


def event_kickoff(markets: list[Market]) -> int | None:
    match_times = [m.market_time_ms for m in markets if m.market_type == "MATCH_ODDS" and m.market_time_ms is not None]
    if match_times:
        return min(match_times)
    all_times = [m.market_time_ms for m in markets if m.market_time_ms is not None]
    return min(all_times) if all_times else None


def freeze_coverage(event_markets: list[Market], freeze: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    kick = event_kickoff(event_markets)
    if kick is None:
        return {"complete": False, "reason": "NO_KICKOFF"}
    cutoff = kick - int(freeze["minutes_before_kickoff"]) * 60_000
    max_age = int(freeze["max_quote_age_minutes"]) * 60_000
    contract = cfg["market_contract"]

    match_ok = False
    ah_ok = False
    lines: set[float] = set()
    market_ids: dict[str, Any] = {"match_odds": [], "asian_handicap": [], "over_under": {}}

    for market in event_markets:
        cat = category(market, cfg)
        if cat is None:
            continue
        prices = latest_prices_at(market, cutoff, max_age)
        kind, line = cat
        if kind == "MATCH_ODDS" and len(prices) >= int(contract["match_odds_required_complete_runners"]):
            match_ok = True
            market_ids["match_odds"].append(market.market_id)
        elif kind == "ASIAN_HANDICAP" and len(prices) >= int(contract["asian_handicap_minimum_complete_runners"]):
            ah_ok = True
            market_ids["asian_handicap"].append(market.market_id)
        elif kind == "OU" and line is not None and len(prices) >= int(contract["over_under_required_complete_runners"]):
            lines.add(float(line))
            market_ids["over_under"].setdefault(str(line), []).append(market.market_id)

    anchor = float(contract["required_anchor_ou_line"])
    enough_lines = len(lines) >= int(contract["minimum_complete_ou_lines"])
    anchor_ok = anchor in lines
    below_ok = (not bool(contract["require_ou_below_anchor"])) or any(x < anchor for x in lines)
    above_ok = (not bool(contract["require_ou_above_anchor"])) or any(x > anchor for x in lines)
    complete = bool(match_ok and ah_ok and enough_lines and anchor_ok and below_ok and above_ok)
    return {
        "complete": complete,
        "cutoff_utc": ms_to_iso(cutoff),
        "match_odds_complete": match_ok,
        "asian_handicap_complete": ah_ok,
        "complete_ou_lines": sorted(lines),
        "ou_line_count": len(lines),
        "anchor_ok": anchor_ok,
        "below_anchor_ok": below_ok,
        "above_anchor_ok": above_ok,
        "market_ids": market_ids,
    }


def audit(markets: dict[str, Market], cfg: dict[str, Any]) -> dict[str, Any]:
    football = [m for m in markets.values() if m.event_type_id == str(cfg["source_contract"]["event_type_id"])]
    by_event: dict[str, list[Market]] = defaultdict(list)
    for market in football:
        if market.event_id:
            by_event[market.event_id].append(market)

    events: list[dict[str, Any]] = []
    all_complete: list[dict[str, Any]] = []
    freeze_counts = Counter()
    for event_id, group in sorted(by_event.items()):
        kick = event_kickoff(group)
        if kick is None:
            continue
        freezes = {str(f["id"]): freeze_coverage(group, f, cfg) for f in cfg["freeze_contract"]}
        for key, value in freezes.items():
            freeze_counts[key] += int(bool(value.get("complete")))
        complete_all = all(bool(x.get("complete")) for x in freezes.values())
        first = group[0]
        row = {
            "event_id": event_id,
            "event_name": first.event_name,
            "country_code": first.country_code,
            "kickoff_utc": ms_to_iso(kick),
            "market_count": len(group),
            "freeze_coverage": freezes,
            "all_required_freezes_complete": complete_all,
        }
        events.append(row)
        if complete_all:
            all_complete.append(row)

    countries = sorted({str(x.get("country_code")) for x in all_complete if x.get("country_code")})
    months = sorted({str(x["kickoff_utc"])[:7] for x in all_complete})
    gate = cfg["coverage_gate"]
    pass_gate = (
        len(all_complete) >= int(gate["minimum_events_all_freezes_complete"])
        and len(countries) >= int(gate["minimum_distinct_countries"])
        and len(months) >= int(gate["minimum_distinct_calendar_months"])
    )
    return {
        "football_markets": len(football),
        "football_events": len(by_event),
        "complete_event_counts_by_freeze": dict(freeze_counts),
        "events_all_required_freezes_complete": len(all_complete),
        "distinct_countries_all_complete": countries,
        "distinct_calendar_months_all_complete": months,
        "coverage_gate_pass": bool(pass_gate),
        "events": events,
    }


def run(cfg: dict[str, Any], out_path: Path, explicit: list[Path] | None = None) -> dict[str, Any]:
    paths = iter_input_files(cfg, explicit)
    if not paths:
        result = {
            "schema_version": cfg["schema_version"],
            "status": "STOP_R43A_BETFAIR_INPUT_ABSENT",
            "scientific_verdict": "NO_COVERAGE_RULING_INPUT_NOT_AVAILABLE",
            "input": {"files": 0, "roots": cfg["source_contract"]["input_roots"]},
            "coverage": None,
            "zero_label_receipt": {
                "result_or_settlement_fields_accessed": 0,
                "post_kickoff_prices_used": 0,
                "model_fits": 0,
                "fixed200_selected": 0,
            },
            "governance": cfg["governance"],
        }
    else:
        markets, parse_receipt, file_receipts = parse_files(paths, cfg)
        coverage = audit(markets, cfg)
        passed = bool(coverage["coverage_gate_pass"])
        result = {
            "schema_version": cfg["schema_version"],
            "status": "PASS_R43A_BETFAIR_ZERO_LABEL_COVERAGE" if passed else "STOP_R43A_BETFAIR_COVERAGE_GATE_FAIL",
            "scientific_verdict": "AUTHORIZE_R43B_FIXED200_PREREGISTRATION" if passed else "DO_NOT_AUTHORIZE_FIXED200_NEED_MORE_OR_BETTER_PIT_COVERAGE",
            "input": {
                "files": len(paths),
                "file_receipts": file_receipts,
                "parse_receipt": parse_receipt.__dict__,
            },
            "coverage": coverage,
            "zero_label_receipt": {
                "result_or_settlement_fields_accessed": 0,
                "post_kickoff_prices_used": 0,
                "model_fits": 0,
                "fixed200_selected": 0,
                "labels_used_for_coverage_gate": False,
                "numeric_price_values_used_for_event_selection": False,
            },
            "governance": cfg["governance"],
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def synthetic_message(pt: int, market_defs: list[dict[str, Any]], prices: dict[str, list[tuple[int, float]]]) -> dict[str, Any]:
    mc: list[dict[str, Any]] = []
    defs = {str(x["id"]): x for x in market_defs}
    for mid, points in prices.items():
        row: dict[str, Any] = {"id": mid, "rc": [{"id": rid, "ltp": price} for rid, price in points]}
        if mid in defs:
            row["marketDefinition"] = defs[mid]["marketDefinition"]
        mc.append(row)
    return {"op": "mcm", "pt": pt, "mc": mc}


def self_test() -> None:
    cfg = load_json(DEFAULT_CONFIG)
    kick = iso_to_ms("2026-01-10T15:00:00.000Z")
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
    definitions: list[dict[str, Any]] = []
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
        rows: list[dict[str, Any]] = []
        for idx, minutes in enumerate((1440, 360, 60)):
            pt = kick - minutes * 60_000
            price_map = {mid: [(rid, 2.0 + 0.01 * idx + j * 0.1) for j, (rid, _) in enumerate(runners)] for mid, runners in runner_sets.items()}
            rows.append(synthetic_message(pt, definitions if idx == 0 else [], price_map))
        rows.append({"op": "mcm", "pt": kick + 1000, "mc": [{"id": "1.mo", "marketDefinition": {"runners": [{"id": 1, "status": "WINNER"}]}}]})
        path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
        local = json.loads(json.dumps(cfg))
        local["coverage_gate"]["minimum_events_all_freezes_complete"] = 1
        local["coverage_gate"]["minimum_distinct_countries"] = 1
        local["coverage_gate"]["minimum_distinct_calendar_months"] = 1
        markets, receipt, _ = parse_files([path], local)
        coverage = audit(markets, local)
        assert coverage["coverage_gate_pass"] is True
        assert coverage["events_all_required_freezes_complete"] == 1
        assert receipt.runner_ltp_updates == 33
        assert ou_line(markets["1.ou25"], local) == 2.5
        assert category(markets["1.ah"], local)[0] == "ASIAN_HANDICAP"
    print(json.dumps({"status": "PASS", "self_test": True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(load_json(args.config), args.out, args.input or None)
    print(json.dumps({
        "status": result["status"],
        "scientific_verdict": result["scientific_verdict"],
        "input": {"files": result["input"]["files"]},
        "coverage": result["coverage"],
        "zero_label_receipt": result["zero_label_receipt"],
        "governance": result["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
