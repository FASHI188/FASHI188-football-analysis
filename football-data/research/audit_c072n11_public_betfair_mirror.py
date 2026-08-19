#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PREFERRED = {
    "OVER_UNDER_05": 0.5,
    "OVER_UNDER_15": 1.5,
    "OVER_UNDER_25": 2.5,
    "OVER_UNDER_35": 3.5,
    "OVER_UNDER_45": 4.5,
}
CUTOFF_HOURS = (24, 6, 1)
SOURCE_REVISION = "90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff"


def dt_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def dt_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        out = datetime.fromisoformat(text)
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except ValueError:
        return None


def open_lines(path: Path):
    if path.suffix.lower() == ".bz2":
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def candidate_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".bz2", ".json", ".txt"}
    )


def safe_def(md: dict[str, Any]) -> dict[str, Any]:
    """Extract only the fields explicitly allowed by the N11 zero-label contract."""
    return {
        "event_id": str(md.get("eventId") or ""),
        "market_type": str(md.get("marketType") or ""),
        "kickoff": dt_iso(md.get("marketTime") or md.get("openDate")),
        "in_play": md.get("inPlay"),
    }


def parse_json_line(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    files = candidate_files(root)
    if not files:
        raise SystemExit("no Betfair stream files found")

    path_manifest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        path_manifest.update(rel.encode("utf-8"))
        path_manifest.update(b"\n")

    # Forbidden fields are intentionally never dereferenced anywhere in this script.
    forbidden_target_fields_accessed = 0
    settlement_fields_accessed = 0
    model_fit = 0

    metas: dict[str, dict[str, Any]] = {}
    parse_errors = 0
    files_with_recognized_ou = 0
    all_ou_market_types: Counter[str] = Counter()

    # Pass 1: discover market metadata without looking at runner status/settlement/outcomes.
    for path in files:
        file_has_ou = False
        try:
            with open_lines(path) as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    msg = parse_json_line(raw)
                    if msg is None:
                        parse_errors += 1
                        continue
                    for mc in msg.get("mc") or []:
                        if not isinstance(mc, dict):
                            continue
                        mid = str(mc.get("id") or "")
                        md = mc.get("marketDefinition")
                        if not mid or not isinstance(md, dict):
                            continue
                        x = safe_def(md)
                        market_type = x["market_type"]
                        if market_type.startswith("OVER_UNDER_"):
                            all_ou_market_types[market_type] += 1
                        if market_type not in PREFERRED:
                            continue
                        if not x["event_id"] or x["kickoff"] is None:
                            continue
                        prior = metas.get(mid)
                        record = {
                            "market_id": mid,
                            "event_id": x["event_id"],
                            "market_type": market_type,
                            "line": PREFERRED[market_type],
                            "kickoff": x["kickoff"],
                        }
                        if prior is None:
                            metas[mid] = record
                        else:
                            # Fail closed on identity/kickoff contradictions.
                            if (
                                prior["event_id"] != record["event_id"]
                                or prior["market_type"] != record["market_type"]
                                or prior["kickoff"] != record["kickoff"]
                            ):
                                prior["identity_conflict"] = True
                        file_has_ou = True
        except (OSError, EOFError, UnicodeError):
            parse_errors += 1
        if file_has_ou:
            files_with_recognized_ou += 1

    # Latest safe LTP at/before each frozen cutoff, kept per market and selection id.
    latest: dict[tuple[str, int, str], tuple[datetime, float]] = {}
    first_pt: datetime | None = None
    last_pt: datetime | None = None
    valid_ltp_updates = 0
    postkickoff_updates_retained = 0

    # Pass 2: read only pt, market id, selection id, and ltp.
    for path in files:
        try:
            with open_lines(path) as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    msg = parse_json_line(raw)
                    if msg is None:
                        continue
                    pt = dt_ms(msg.get("pt"))
                    if pt is None:
                        continue
                    first_pt = pt if first_pt is None else min(first_pt, pt)
                    last_pt = pt if last_pt is None else max(last_pt, pt)
                    for mc in msg.get("mc") or []:
                        if not isinstance(mc, dict):
                            continue
                        mid = str(mc.get("id") or "")
                        meta = metas.get(mid)
                        if meta is None:
                            continue
                        kickoff = meta["kickoff"]
                        if not (pt < kickoff):
                            continue
                        for rc in mc.get("rc") or []:
                            if not isinstance(rc, dict) or "ltp" not in rc:
                                continue
                            rid = str(rc.get("id") or "")
                            if not rid:
                                continue
                            try:
                                ltp = float(rc.get("ltp"))
                            except (TypeError, ValueError):
                                continue
                            if not (math.isfinite(ltp) and ltp > 1.0):
                                continue
                            valid_ltp_updates += 1
                            for hours in CUTOFF_HOURS:
                                cutoff = kickoff - timedelta(hours=hours)
                                if pt <= cutoff:
                                    key = (mid, hours, rid)
                                    old = latest.get(key)
                                    if old is None or pt > old[0]:
                                        latest[key] = (pt, ltp)
        except (OSError, EOFError, UnicodeError):
            parse_errors += 1

    market_complete: dict[tuple[str, int], bool] = {}
    market_selection_counts: dict[tuple[str, int], int] = {}
    for mid in metas:
        for hours in CUTOFF_HOURS:
            selections = {
                rid for (m, h, rid), _value in latest.items()
                if m == mid and h == hours
            }
            market_selection_counts[(mid, hours)] = len(selections)
            market_complete[(mid, hours)] = len(selections) >= 2

    # Event-line completeness is true when a single market for that event/line has both sides.
    event_line_markets: dict[tuple[str, float], list[str]] = defaultdict(list)
    event_kickoffs: dict[str, set[datetime]] = defaultdict(set)
    identity_conflicts = 0
    for mid, meta in metas.items():
        event_line_markets[(meta["event_id"], meta["line"])].append(mid)
        event_kickoffs[meta["event_id"]].add(meta["kickoff"])
        if meta.get("identity_conflict"):
            identity_conflicts += 1
    identity_conflicts += sum(len(v) > 1 for v in event_kickoffs.values())

    events = sorted(event_kickoffs)
    per_line_cutoff: dict[str, dict[str, int]] = {}
    per_line_any: dict[str, int] = {}
    for line in PREFERRED.values():
        key_line = str(line)
        per_line_any[key_line] = sum((event, line) in event_line_markets for event in events)
        per_line_cutoff[key_line] = {}
        for hours in CUTOFF_HOURS:
            n = 0
            for event in events:
                mids = event_line_markets.get((event, line), [])
                if any(market_complete.get((mid, hours), False) for mid in mids):
                    n += 1
            per_line_cutoff[key_line][f"T-{hours}h"] = n

    def line_complete_all_cutoffs(event: str, line: float) -> bool:
        mids = event_line_markets.get((event, line), [])
        return any(
            all(market_complete.get((mid, h), False) for h in CUTOFF_HOURS)
            for mid in mids
        )

    event_complete_line_counts: dict[str, int] = {}
    for event in events:
        event_complete_line_counts[event] = sum(
            line_complete_all_cutoffs(event, line) for line in PREFERRED.values()
        )

    ou25_all3 = sum(line_complete_all_cutoffs(event, 2.5) for event in events)
    events_ge2_all3 = sum(n >= 2 for n in event_complete_line_counts.values())
    events_ge3_all3 = sum(n >= 3 for n in event_complete_line_counts.values())
    events_all5_all3 = sum(n == 5 for n in event_complete_line_counts.values())

    kickoff_values = [next(iter(v)) for v in event_kickoffs.values() if len(v) == 1]
    gates = {
        "recognized_ou_markets_present": bool(metas),
        "ou25_complete_all_three_cutoffs_present": ou25_all3 > 0,
        "two_preferred_lines_complete_all_three_cutoffs_present": events_ge2_all3 > 0,
        "identity_conflicts_zero": identity_conflicts == 0,
        "postkickoff_updates_retained_zero": postkickoff_updates_retained == 0,
        "forbidden_target_fields_accessed_zero": forbidden_target_fields_accessed == 0,
        "settlement_fields_accessed_zero": settlement_fields_accessed == 0,
        "model_fit_zero": model_fit == 0,
    }

    result = {
        "schema_version": "C072N11_PUBLIC_BETFAIR_MIRROR_ZERO_LABEL_AUDIT_V1",
        "project": "football3",
        "source_repo": "marcosf63/bet",
        "source_revision": SOURCE_REVISION,
        "classification": "REPLICATION_STRUCTURE_ONLY_GLOBALLY_CONSUMED_OUTCOME_DOMAIN",
        "fresh_confirmation_eligible": False,
        "files_scanned": len(files),
        "files_with_recognized_preferred_ou": files_with_recognized_ou,
        "file_path_manifest_sha256": path_manifest.hexdigest(),
        "parse_errors": parse_errors,
        "recognized_preferred_ou_markets": len(metas),
        "all_ou_market_type_definition_counts": dict(sorted(all_ou_market_types.items())),
        "unique_events_with_preferred_ou_market": len(events),
        "valid_prematch_ltp_updates": valid_ltp_updates,
        "per_line_event_count": per_line_any,
        "per_line_complete_two_selection_cutoff_count": per_line_cutoff,
        "ou25_complete_all_T24_T6_T1_events": ou25_all3,
        "events_with_ge2_preferred_lines_complete_all_T24_T6_T1": events_ge2_all3,
        "events_with_ge3_preferred_lines_complete_all_T24_T6_T1": events_ge3_all3,
        "events_with_all5_preferred_lines_complete_all_T24_T6_T1": events_all5_all3,
        "publish_time_min": first_pt.isoformat() if first_pt else None,
        "publish_time_max": last_pt.isoformat() if last_pt else None,
        "kickoff_min": min(kickoff_values).isoformat() if kickoff_values else None,
        "kickoff_max": max(kickoff_values).isoformat() if kickoff_values else None,
        "identity_conflicts": identity_conflicts,
        "postkickoff_updates_retained": postkickoff_updates_retained,
        "forbidden_target_fields_accessed": forbidden_target_fields_accessed,
        "settlement_fields_accessed": settlement_fields_accessed,
        "model_fit": model_fit,
        "c073_c077_scientific_results_used": False,
        "c070f_confirmation_opened": False,
        "gates": gates,
        "terminal": "STRUCTURAL_REPLAY_PASS" if all(gates.values()) else "STRUCTURAL_REPLAY_STOP",
        "authorization": "NO_TARGET_ACCESS; full N11 PASS requires a separate globally unconsumed provider export/domain",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
