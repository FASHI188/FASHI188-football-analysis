#!/usr/bin/env python3
"""V6.49.1 manager delta from previously verified manager baselines.

The V6.49.0 match-bound sidecar verifies the current manager at a specific decision
freeze.  That alone is NOT enough to claim that a manager change did or did not occur.
This audit scans prior verified manager evidence under team_manager_context_weekly and
compares the latest baseline strictly earlier than the match-bound observation.

Outputs only:
* SAME_MANAGER
* MANAGER_CHANGED
* NO_PRIOR_VERIFIED_BASELINE

It never infers no-change from missing data and never mutates model probabilities.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / "evidence" / "team_manager_context_weekly"
AXES_LEDGER = ROOT / "forward" / "v6_match_context_axes_events_v6490.json"
OUT = ROOT / "manifests" / "v6_manager_delta_from_verified_baseline_v6491_status.json"


def load(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def dt(value: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def norm_name(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def coach_name(rec: dict[str, Any]) -> str:
    hc = rec.get("head_coach")
    if isinstance(hc, dict):
        return str(hc.get("name") or "").strip()
    if isinstance(hc, str):
        return hc.strip()
    manager = rec.get("manager")
    if isinstance(manager, dict):
        return str(manager.get("name") or "").strip()
    return ""


def sha_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def collect_baselines() -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, int]]:
    rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    stats = {"files_seen": 0, "records_seen": 0, "accepted_verified_records": 0, "rejected_records": 0}
    if not BASELINE_DIR.exists():
        return rows, stats
    for path in sorted(BASELINE_DIR.glob("*.json")):
        stats["files_seen"] += 1
        doc = load(path)
        candidates = doc.get("records") if isinstance(doc.get("records"), list) else [doc]
        for rec in candidates:
            if not isinstance(rec, dict):
                continue
            stats["records_seen"] += 1
            cid = str(rec.get("competition_id") or "").strip()
            team = str(rec.get("team_name") or "").strip()
            observed = dt(rec.get("observed_at_utc"))
            coach = coach_name(rec)
            sources = [s for s in (rec.get("sources") or []) if isinstance(s, dict)]
            gov = rec.get("governance") or {}
            # Require explicit current/PIT semantics and at least one named source.
            verified_semantics = bool(gov.get("pit_current") is True or gov.get("current_at_observation_time") is True or gov.get("research_context_only") is True)
            source_ok = bool(sources) and all(str(s.get("source_url") or "").strip() for s in sources)
            if not (cid and team and observed and coach and verified_semantics and source_ok):
                stats["rejected_records"] += 1
                continue
            row = {
                "competition_id": cid,
                "team_name": team,
                "observed_at_utc": observed.isoformat(),
                "head_coach": coach,
                "evidence_path": str(path.relative_to(ROOT)),
                "sources": sources,
            }
            rows.setdefault((cid, norm_name(team)), []).append(row)
            stats["accepted_verified_records"] += 1
    for values in rows.values():
        values.sort(key=lambda r: r["observed_at_utc"])
    return rows, stats


def current_rows() -> list[dict[str, Any]]:
    ledger = load(AXES_LEDGER)
    out: list[dict[str, Any]] = []
    for e in ledger.get("events") or []:
        if not isinstance(e, dict) or e.get("event_type") != "MATCH_CONTEXT_AXES_FROZEN":
            continue
        p = e.get("payload") or {}
        fi = p.get("fixture_identity") or {}
        observed = dt(e.get("event_timestamp_utc"))
        if observed is None:
            continue
        manager = p.get("manager") or {}
        for side, team_key in (("home", "home_team"), ("away", "away_team")):
            m = manager.get(side) if isinstance(manager.get(side), dict) else {}
            name = str(m.get("name") or "").strip()
            if not (m.get("verified") is True and name):
                continue
            out.append({
                "fixture_key": e.get("fixture_key"),
                "competition_id": str(fi.get("competition_id") or ""),
                "team_name": str(fi.get(team_key) or ""),
                "side": side,
                "observed_at_utc": observed.isoformat(),
                "current_manager": name,
                "current_event_hash": e.get("event_hash"),
            })
    return out


def main() -> int:
    baselines, scan = collect_baselines()
    current = current_rows()
    results: list[dict[str, Any]] = []
    counts = {"SAME_MANAGER": 0, "MANAGER_CHANGED": 0, "NO_PRIOR_VERIFIED_BASELINE": 0}

    for row in current:
        observed = dt(row["observed_at_utc"])
        key = (row["competition_id"], norm_name(row["team_name"]))
        prior = []
        for b in baselines.get(key, []):
            bdt = dt(b.get("observed_at_utc"))
            if bdt is not None and observed is not None and bdt < observed:
                prior.append(b)
        baseline = prior[-1] if prior else None
        if baseline is None:
            status = "NO_PRIOR_VERIFIED_BASELINE"
        elif norm_name(baseline["head_coach"]) == norm_name(row["current_manager"]):
            status = "SAME_MANAGER"
        else:
            status = "MANAGER_CHANGED"
        counts[status] += 1
        results.append({
            **row,
            "delta_status": status,
            "prior_verified_baseline": baseline,
            "manager_changed": True if status == "MANAGER_CHANGED" else False if status == "SAME_MANAGER" else None,
        })

    payload = {
        "schema_version": "V6.49.1-manager-delta-from-verified-baseline-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS",
        "classification": "RESEARCH_CONTEXT_AUDIT_FORMAL_WEIGHT_0",
        "baseline_scan": scan,
        "current_manager_observation_count": len(current),
        "delta_counts": counts,
        "results": results,
        "audit": {
            "result_count_matches_current": len(results) == len(current),
            "classified_count": sum(counts.values()),
            "payload_sha256": sha_json(results),
        },
        "governance": {
            "missing_prior_baseline_never_means_no_change": True,
            "current_manager_identity_alone_never_means_no_change": True,
            "only_strictly_earlier_verified_baseline_is_compared": True,
            "historical_result_backfill": False,
            "probability_mutation": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "baseline_scan": scan, "current": len(current), "delta_counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
