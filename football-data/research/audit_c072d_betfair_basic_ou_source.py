#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LINE_RE = re.compile(r"over\s*/\s*under\s+([0-9]+(?:\.[0-9]+)?)\s+goals", re.I)


def iso_ms(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    except Exception:
        return None


def iso_dt(x) -> datetime | None:
    if not x:
        return None
    try:
        s = str(x).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def open_text(path: Path):
    if path.suffix.lower() == ".bz2":
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def q(x, p):
    if not x:
        return None
    vals = sorted(x)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1 - w) + vals[hi] * w


def main() -> int:
    ap = argparse.ArgumentParser(description="C072-D zero-label audit for Betfair BASIC soccer O/U historical files")
    ap.add_argument("root", help="Directory containing Betfair BASIC historical files (.bz2 or plain JSON stream files)")
    ap.add_argument("--out", required=True, help="Output JSON summary path")
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted([p for p in root.rglob("*") if p.is_file() and (p.suffix.lower() == ".bz2" or p.suffix.lower() in {".json", ".txt", ""})])
    if not files:
        raise SystemExit("no candidate Betfair stream files found")

    # market metadata is learned only from stream messages. No result/settlement target labels are joined.
    markets = {}
    runners = defaultdict(dict)
    obs = []
    parse_errors = 0
    sha = hashlib.sha256()

    for path in files:
        sha.update(str(path.relative_to(root)).encode())
        try:
            with open_text(path) as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        parse_errors += 1
                        continue
                    pt = iso_ms(msg.get("pt"))
                    for mc in msg.get("mc", []) or []:
                        mid = str(mc.get("id", ""))
                        if not mid:
                            continue
                        md = mc.get("marketDefinition") or {}
                        if md:
                            name = md.get("name") or ""
                            m = LINE_RE.search(name)
                            if m:
                                line = float(m.group(1))
                                meta = markets.setdefault(mid, {})
                                meta.update({
                                    "market_id": mid,
                                    "event_id": str(md.get("eventId", "")),
                                    "event_name": md.get("eventName"),
                                    "market_name": name,
                                    "market_type": md.get("marketType"),
                                    "kickoff": iso_dt(md.get("marketTime") or md.get("openDate")),
                                    "line": line,
                                })
                                for r in md.get("runners", []) or []:
                                    rid = str(r.get("id", ""))
                                    if rid:
                                        runners[mid][rid] = r.get("name") or ""
                        meta = markets.get(mid)
                        if not meta or pt is None or meta.get("kickoff") is None:
                            continue
                        # Strict PIT: only pre-kickoff observations are allowed into the audit.
                        if not (pt < meta["kickoff"]):
                            continue
                        for rc in mc.get("rc", []) or []:
                            if "ltp" not in rc:
                                continue
                            rid = str(rc.get("id", ""))
                            rname = runners[mid].get(rid, "")
                            side = "over" if "over" in rname.lower() else "under" if "under" in rname.lower() else None
                            if side is None:
                                continue
                            try:
                                ltp = float(rc["ltp"])
                            except Exception:
                                continue
                            if not (math.isfinite(ltp) and ltp > 1.0):
                                continue
                            mins = (meta["kickoff"] - pt).total_seconds() / 60.0
                            obs.append((meta["event_id"], mid, meta["line"], side, pt, mins, ltp, meta["kickoff"]))
        except Exception:
            parse_errors += 1

    by_event_lines = defaultdict(set)
    event_kickoff = {}
    event_line_times = defaultdict(list)
    identity_conflict = 0
    seen_event_kick = {}

    for event_id, mid, line, side, pt, mins, ltp, kickoff in obs:
        if not event_id:
            continue
        if event_id in seen_event_kick and seen_event_kick[event_id] != kickoff:
            identity_conflict += 1
        seen_event_kick[event_id] = kickoff
        event_kickoff[event_id] = kickoff
        by_event_lines[event_id].add(line)
        event_line_times[(event_id, line)].append(mins)

    events = sorted(by_event_lines)
    n_events = len(events)
    valid_kickoff_rate = (len(event_kickoff) / n_events) if n_events else 0.0

    def event_has_line(e, line):
        return line in by_event_lines[e]

    def event_has_minutes(e, line, predicate):
        return any(predicate(x) for x in event_line_times.get((e, line), []))

    n_25 = sum(event_has_line(e, 2.5) for e in events)
    n_2plus = sum(len(by_event_lines[e]) >= 2 for e in events)
    n_3plus = sum(len(by_event_lines[e]) >= 3 for e in events)
    n_25_ge60 = sum(event_has_line(e, 2.5) and event_has_minutes(e, 2.5, lambda x: x >= 60.0) for e in events)
    n_25_final60 = sum(event_has_line(e, 2.5) and event_has_minutes(e, 2.5, lambda x: 0.0 < x <= 60.0) for e in events)

    line_counts = defaultdict(int)
    for e in events:
        for line in by_event_lines[e]:
            line_counts[str(line)] += 1

    mins_all = [x[5] for x in obs]
    gates = {
        "events_ge_3000": n_events >= 3000,
        "identity_conflicts_zero": identity_conflict == 0,
        "valid_kickoff_rate_ge_0_995": valid_kickoff_rate >= 0.995,
        "ou25_event_rate_ge_0_85": (n_25 / n_events if n_events else 0.0) >= 0.85,
        "two_plus_lines_rate_ge_0_75": (n_2plus / n_events if n_events else 0.0) >= 0.75,
        "three_plus_lines_rate_ge_0_50": (n_3plus / n_events if n_events else 0.0) >= 0.50,
        "ou25_ge60min_rate_ge_0_70": (n_25_ge60 / n_25 if n_25 else 0.0) >= 0.70,
        "ou25_final60_rate_ge_0_70": (n_25_final60 / n_25 if n_25 else 0.0) >= 0.70,
        "postkickoff_retained_zero": all(x[5] > 0.0 for x in obs),
        "target_labels_materialized_zero": True,
        "model_fit_zero": True,
    }

    summary = {
        "schema_version": "C072D_BETFAIR_BASIC_ZERO_LABEL_SOURCE_AUDIT_V1",
        "project_line": "football3",
        "parent_head": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "quarantined": "C073-C077",
        "files_scanned": len(files),
        "file_path_manifest_sha256": sha.hexdigest(),
        "parse_errors": parse_errors,
        "unique_ou_markets": len(markets),
        "unique_events_with_valid_prematch_ou_ltp": n_events,
        "identity_conflicts": identity_conflict,
        "valid_kickoff_rate": valid_kickoff_rate,
        "ou_line_event_counts": dict(sorted(line_counts.items(), key=lambda kv: float(kv[0]))),
        "ou25_event_rate": n_25 / n_events if n_events else 0.0,
        "two_plus_lines_event_rate": n_2plus / n_events if n_events else 0.0,
        "three_plus_lines_event_rate": n_3plus / n_events if n_events else 0.0,
        "ou25_ge60min_rate": n_25_ge60 / n_25 if n_25 else 0.0,
        "ou25_final60_rate": n_25_final60 / n_25 if n_25 else 0.0,
        "prematch_minutes_to_kickoff": {
            "n": len(mins_all),
            "p10": q(mins_all, 0.10),
            "median": q(mins_all, 0.50),
            "p90": q(mins_all, 0.90),
            "min": min(mins_all) if mins_all else None,
            "max": max(mins_all) if mins_all else None,
        },
        "target_labels_materialized": 0,
        "model_fit": 0,
        "gates": gates,
        "terminal": "SOURCE_COVERAGE_PASS" if all(gates.values()) else "STOP_SOURCE_COVERAGE",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
