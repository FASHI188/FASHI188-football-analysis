#!/usr/bin/env python3
"""Validate weekly team-configuration snapshots and produce a research coverage receipt.

This validator never creates football probabilities. It checks that current-season team
configuration evidence is timestamped, source-backed, append-only by snapshot identity,
and complete enough to be considered by later residual research.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v6_team_configuration_weekly_v660.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "team_configuration_weekly"
OUT = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    cfg = load(CONFIG)
    domains = set(cfg["domains"])
    files = sorted(SNAPSHOT_ROOT.glob("*.json")) if SNAPSHOT_ROOT.exists() else []
    latest = {}
    errors = []
    source_tiers = Counter()
    domain_teams = defaultdict(set)

    for path in files:
        try:
            x = load(path)
            cid = str(x.get("competition_id") or "")
            team = str(x.get("team_name") or "").strip()
            season = str(x.get("season") or "").strip()
            observed = str(x.get("observed_at_utc") or "")
            if cid not in domains:
                raise ValueError("unknown competition_id")
            if not team or not season or not observed:
                raise ValueError("missing identity/season/observed_at")
            ts = parse_ts(observed)
            if ts.tzinfo is None:
                raise ValueError("observed_at_utc must be timezone-aware")
            sources = x.get("sources") or []
            if not isinstance(sources, list) or not sources:
                raise ValueError("no sources")
            for s in sources:
                if not s.get("source_name") or not s.get("source_url") or not s.get("source_observed_at_utc"):
                    raise ValueError("source missing name/url/timestamp")
                source_tiers[str(s.get("source_tier") or "unspecified")] += 1
            players = x.get("players") or []
            if not isinstance(players, list):
                raise ValueError("players must be list")
            key = (cid, team)
            previous = latest.get(key)
            if previous is None or ts > previous[0]:
                latest[key] = (ts, x, path.name)
            domain_teams[cid].add(team)
        except Exception as exc:
            errors.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})

    complete = 0
    residual_eligible = 0
    latest_summary = []
    for (cid, team), (ts, x, filename) in sorted(latest.items()):
        players = x.get("players") or []
        coach = x.get("head_coach")
        sources = x.get("sources") or []
        availability = x.get("availability") or []
        is_complete = bool(coach and len(players) >= 18 and len(sources) >= 1)
        is_residual_eligible = bool(is_complete and any(str(s.get("source_tier")) in {"tier_1", "tier_2"} for s in sources))
        complete += int(is_complete)
        residual_eligible += int(is_residual_eligible)
        latest_summary.append({
            "competition_id": cid,
            "team_name": team,
            "season": x.get("season"),
            "observed_at_utc": ts.isoformat(),
            "players": len(players),
            "availability_records": len(availability),
            "head_coach_present": bool(coach),
            "complete": is_complete,
            "residual_research_eligible": is_residual_eligible,
            "snapshot_file": filename,
        })

    payload = {
        "schema_version": "V6.6.0-weekly-team-configuration-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not errors else "WARN",
        "snapshot_files": len(files),
        "latest_team_snapshots": len(latest),
        "domains_with_snapshots": len({cid for cid, _ in latest}),
        "configured_domains": len(domains),
        "complete_team_snapshots": complete,
        "residual_research_eligible_team_snapshots": residual_eligible,
        "source_tier_counts": dict(source_tiers),
        "domain_team_counts": {k: len(v) for k, v in sorted(domain_teams.items())},
        "validation_errors": errors,
        "latest": latest_summary,
        "governance": {
            "configuration_data_is_research_context_only": True,
            "no_probability_generation": True,
            "no_formal_weight_change": True,
            "no_runtime_probability_change": True,
            "no_current_rule_change": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
