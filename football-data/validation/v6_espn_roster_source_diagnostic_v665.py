#!/usr/bin/env python3
"""V6.6.5 diagnose ESPN roster-source completeness for current sub-18 team snapshots.

Read-only diagnostic. For each latest team snapshot with fewer than 18 named players, compare:
1) site API /roster,
2) site API team detail with ?enable=roster,
3) sports.core season team /athletes collection count.
No roster evidence is rewritten here. The goal is to distinguish parser/source-surface defects
from genuine current-source incompleteness before adding another fallback provider.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "team_configuration_weekly"
OUT = ROOT / "manifests" / "v6_espn_roster_source_diagnostic_v665_status.json"
UA = "football-v6.6.5-roster-diagnostic/1.0"
SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
CORE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues"


def get(url: str) -> tuple[Any, str | None]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read().decode("utf-8", errors="replace")), None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def latest_aggregate() -> dict[str, Any]:
    candidates = []
    for path in EVIDENCE.glob("weekly_aggregate__*.json") if EVIDENCE.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(str(row.get("observed_at_utc") or "").replace("Z", "+00:00"))
            candidates.append((ts, path, row))
        except Exception:
            continue
    if not candidates:
        raise RuntimeError("no weekly aggregate")
    candidates.sort(key=lambda x: (x[0], str(x[1])))
    return candidates[-1][2]


def player_names_from_site_roster(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    athletes = payload.get("athletes") or payload.get("players") or []
    out = []
    for group in athletes if isinstance(athletes, list) else []:
        items = group.get("items") if isinstance(group, dict) else None
        seq = items if isinstance(items, list) else [group]
        for player in seq:
            if isinstance(player, dict):
                name = player.get("displayName") or player.get("fullName") or player.get("name")
                if name:
                    out.append(str(name))
    return sorted(set(out))


def player_names_from_enabled_team(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    # ESPN has changed this surface shape over time. Restrict traversal to roster/athlete branches
    # and collect only objects that look like players, not team/league display names.
    roots = []
    for container in (payload, payload.get("team") if isinstance(payload.get("team"), dict) else {}):
        for key in ("athletes", "roster", "players"):
            if key in container:
                roots.append(container[key])
    out = []
    seen_ids = set()

    def walk(node: Any, depth: int = 0):
        if depth > 8:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        name = node.get("displayName") or node.get("fullName")
        pid = node.get("id") or node.get("uid")
        has_player_signal = any(k in node for k in ("position", "jersey", "jerseyNumber", "age", "headshot"))
        if name and has_player_signal:
            token = str(pid or name)
            if token not in seen_ids:
                seen_ids.add(token)
                out.append(str(name))
        for key, value in node.items():
            if key in {"team", "league", "sport"}:
                continue
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    for root in roots:
        walk(root)
    return sorted(set(out))


def season_year(raw: str) -> str:
    match = re.search(r"(20\d{2})", str(raw or ""))
    return match.group(1) if match else "2026"


def main() -> int:
    aggregate = latest_aggregate()
    snapshots = [x for x in aggregate.get("snapshots") or [] if isinstance(x, dict)]
    deficient = [x for x in snapshots if len(x.get("players") or []) < 18 and (x.get("provider_ids") or {}).get("espn_team_id")]
    rows = []
    for snap in deficient:
        ids = snap.get("provider_ids") or {}
        league = str(ids.get("espn_league_slug") or "")
        tid = str(ids.get("espn_team_id") or "")
        year = season_year(str(snap.get("season") or ""))
        roster_url = f"{SITE}/{league}/teams/{tid}/roster"
        enabled_url = f"{SITE}/{league}/teams/{tid}?enable=roster"
        core_url = f"{CORE}/{league}/seasons/{year}/teams/{tid}/athletes?limit=100"
        roster_payload, roster_error = get(roster_url)
        enabled_payload, enabled_error = get(enabled_url)
        core_payload, core_error = get(core_url)
        roster_names = player_names_from_site_roster(roster_payload)
        enabled_names = player_names_from_enabled_team(enabled_payload)
        core_count = None
        core_items = None
        if isinstance(core_payload, dict):
            try:
                core_count = int(core_payload.get("count")) if core_payload.get("count") is not None else None
            except Exception:
                core_count = None
            core_items = len(core_payload.get("items") or []) if isinstance(core_payload.get("items"), list) else None
        rows.append({
            "competition_id": snap.get("competition_id"),
            "team_name": snap.get("team_name"),
            "season": snap.get("season"),
            "espn_team_id": tid,
            "league_slug": league,
            "stored_player_count": len(snap.get("players") or []),
            "site_roster_parsed_count": len(roster_names),
            "enabled_team_parsed_count": len(enabled_names),
            "core_athletes_count": core_count,
            "core_items_returned": core_items,
            "surface_urls": {"roster": roster_url, "enabled_team": enabled_url, "core_athletes": core_url},
            "errors": {k: v for k, v in {"roster": roster_error, "enabled_team": enabled_error, "core_athletes": core_error}.items() if v},
            "diagnosis": (
                "CORE_HAS_FULLER_ROSTER" if isinstance(core_count, int) and core_count >= 18
                else "ENABLED_TEAM_HAS_FULLER_ROSTER" if len(enabled_names) >= 18
                else "SITE_ROSTER_HAS_FULLER_ROSTER" if len(roster_names) >= 18
                else "ALL_ESPN_SURFACES_SUB18_OR_UNAVAILABLE"
            )
        })
    counts = {}
    for row in rows:
        counts[row["diagnosis"]] = counts.get(row["diagnosis"], 0) + 1
    payload = {
        "schema_version": "V6.6.5-espn-roster-source-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "deficient_espn_team_count": len(deficient),
        "diagnosis_counts": dict(sorted(counts.items())),
        "rows": rows,
        "governance": {"read_only": True, "formal_probability_change": False, "no_roster_rewrite": True}
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
