#!/usr/bin/env python3
"""V6.48.8 build the exact-match live-context resolution queue.

The queue is the authoritative bridge between automatic market acquisition and the
CURRENT requirement to verify manager / availability / suspensions / predicted XI at a
specific match freeze. It does not scrape sources that prohibit automated collection.

A fixture enters the queue from prospective market evidence. Any independently supplied
pre-kickoff context evidence can satisfy individual axes, but missing axes remain
LIVE_RESOLUTION_REQUIRED rather than being silently interpreted as no injury/no change.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKETS = ROOT / "evidence" / "markets_prospective"
CONTEXT_DIRS = [
    ROOT / "evidence" / "match_context_pre_kickoff",
    ROOT / "evidence" / "match_context_live" / "fotmob",
]
OUT = ROOT / "manifests" / "v6_match_context_resolution_queue_v6488_status.json"
WINDOW = timedelta(hours=72)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(v: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(v or "").strip().replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def load(path: Path) -> dict[str, Any]:
    try:
        x = json.loads(path.read_text(encoding="utf-8"))
        return x if isinstance(x, dict) else {}
    except Exception:
        return {}


def key(cid: object, kickoff: object, home: object, away: object) -> tuple[str, str, str, str]:
    return (str(cid or "").strip(), str(kickoff or "").strip(), str(home or "").strip(), str(away or "").strip())


def fixture_from_market(x: dict[str, Any]) -> tuple[str, str, str, str]:
    ko = parse_dt(x.get("kickoff_utc"))
    return key(x.get("competition_id"), ko.isoformat() if ko else "", x.get("home_team"), x.get("away_team"))


def latest_markets(t: datetime) -> dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]]:
    out = {}
    if not MARKETS.exists():
        return out
    for p in MARKETS.glob("*.json"):
        x = load(p); ko = parse_dt(x.get("kickoff_utc")); obs = parse_dt(x.get("source_observed_at_utc") or x.get("freeze_utc"))
        if ko is None or obs is None or not (t < ko <= t + WINDOW) or obs >= ko:
            continue
        k = fixture_from_market(x)
        old = out.get(k)
        if old is None or obs > old[0]:
            out[k] = (obs, p, x)
    return out


def context_index() -> dict[tuple[str, str, str, str], list[tuple[datetime, Path, dict[str, Any]]]]:
    out: dict[tuple[str, str, str, str], list[tuple[datetime, Path, dict[str, Any]]]] = {}
    for base in CONTEXT_DIRS:
        if not base.exists():
            continue
        for p in base.glob("*.json"):
            x = load(p); fi = x.get("fixture_identity") or {}; obs = parse_dt(x.get("observed_at_utc"))
            ko = parse_dt(fi.get("kickoff_at") or fi.get("kickoff_utc"))
            if obs is None or ko is None or obs >= ko:
                continue
            k = key(fi.get("competition_id"), ko.isoformat(), fi.get("home_team"), fi.get("away_team"))
            out.setdefault(k, []).append((obs, p, x))
    for rows in out.values():
        rows.sort(key=lambda r: r[0])
    return out


def evidence_axes(rows: list[tuple[datetime, Path, dict[str, Any]]], cutoff: datetime) -> dict[str, Any]:
    axes = {"manager": False, "availability": False, "suspensions": False, "predicted_xi": False, "material_roster_delta": False}
    evidence = []
    for obs, p, x in rows:
        if obs > cutoff:
            continue
        ctx = x.get("context") or {}; elig = x.get("eligibility") or {}; sources = x.get("sources") or []
        # Generic source-role documents (V6.31 contract).
        for s in sources if isinstance(sources, list) else []:
            if not isinstance(s, dict):
                continue
            role = str(s.get("source_role") or "").casefold()
            if any(tok in role for tok in ("injury", "availability", "fitness", "team news")):
                axes["availability"] = True
            if "suspension" in role:
                axes["suspensions"] = True
            if any(tok in role for tok in ("predicted xi", "expected xi", "probable xi", "projected xi", "predicted lineup", "expected lineup", "probable lineup", "projected lineup")):
                players = s.get("predicted_xi") or []
                axes["predicted_xi"] = len({str(v).strip().casefold() for v in players if str(v).strip()}) >= 11
            if "manager" in role or "head coach" in role:
                axes["manager"] = True
            if "transfer" in role or "roster delta" in role:
                axes["material_roster_delta"] = True
        # Conservative live-context docs.
        if elig.get("availability_candidate_present") is True:
            axes["availability"] = True
        if elig.get("predicted_xi_home_eligible") is True and elig.get("predicted_xi_away_eligible") is True:
            axes["predicted_xi"] = True
        evidence.append({"path": str(p.relative_to(ROOT)), "observed_at_utc": obs.isoformat()})
    return {"axes": axes, "evidence": evidence}


def main() -> int:
    t = now(); markets = latest_markets(t); contexts = context_index(); queue = []; counts = Counter()
    for k, (market_obs, market_path, market) in sorted(markets.items(), key=lambda kv: kv[0][1]):
        cid, kickoff_s, home, away = k; ko = parse_dt(kickoff_s)
        if ko is None:
            continue
        resolution = evidence_axes(contexts.get(k, []), market_obs)
        axes = resolution["axes"]; missing = [name for name, ok in axes.items() if not ok]
        status = "PIT_READY" if not missing else "LIVE_RESOLUTION_REQUIRED"
        counts[status] += 1
        for m in missing:
            counts[f"missing_{m}"] += 1
        queue.append({
            "competition_id": cid, "kickoff_at_utc": kickoff_s, "hours_to_kickoff": round((ko - t).total_seconds()/3600.0, 3),
            "home_team": home, "away_team": away,
            "market_freeze_at_utc": market_obs.isoformat(), "market_snapshot_path": str(market_path.relative_to(ROOT)),
            "status": status, "verified_axes_at_market_freeze": axes, "missing_axes": missing,
            "context_evidence_at_or_before_market_freeze": resolution["evidence"],
            "resolution_instruction": "Before using team-context features, perform live web/source verification at the decision freeze. Missing evidence means abstain that feature; never infer no injury/no change.",
        })
    payload = {
        "schema_version": "V6.48.8-match-context-resolution-queue-r1",
        "generated_at_utc": t.isoformat(), "formal_current_version": "V5.0.1", "status": "PASS",
        "window_hours": 72, "fixture_count": len(queue), "counts": dict(sorted(counts.items())), "queue": queue,
        "governance": {
            "automatic_market_discovery": True,
            "automatic_context_completeness_claim": False,
            "live_resolution_required_for_missing_axes": True,
            "missing_means_unknown_not_no_change": True,
            "post_market_freeze_evidence_cannot_backfill_that_market_freeze": True,
            "context_may_be_refrozen_at_a_new_decision_time_with_market_snapshot_no_later_than_that_time": True,
            "formal_weight": 0, "runtime_probability_change": False, "current_rule_change": False
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"PASS","fixture_count":len(queue),"counts":payload["counts"]},ensure_ascii=False,indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
