#!/usr/bin/env python3
"""V6.6.4 repair overlay for incomplete (<18 named players) weekly rosters.

The primary weekly fetch can return a non-empty but incomplete ESPN roster.  That must not be
mistaken for a complete squad and previously failed to trigger fallback.  This pass reads the
latest aggregate, queries TheSportsDB only for sub-18 rosters, and writes a later PIT overlay
*only when the fallback list is larger*.  It replaces the incomplete roster rather than merging
two possibly stale lists, while preserving the primary source's injury/transaction/depth data.
Research context only; never changes formal probabilities.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "team_configuration_weekly"
OUT = ROOT / "manifests" / "v6_team_roster_repair_v664_status.json"
TSD = "https://www.thesportsdb.com/api/v1/json/123"
UA = "football-v6.6.4-roster-repair/1.0"
MIN_PLAYERS = 18
REQUEST_INTERVAL_SECONDS = 2.05
_last_request = 0.0


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def get_json(url: str) -> dict[str, Any]:
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < REQUEST_INTERVAL_SECONDS:
        time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    _last_request = time.monotonic()
    return payload if isinstance(payload, dict) else {}


def latest_aggregate() -> tuple[Path, dict[str, Any]] | None:
    candidates = []
    for path in EVIDENCE.glob("weekly_aggregate__*.json") if EVIDENCE.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            observed = datetime.fromisoformat(str(row.get("observed_at_utc") or "").replace("Z", "+00:00"))
            candidates.append((observed, path, row))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], str(x[1])))
    _ts, path, row = candidates[-1]
    return path, row


def search_team(name: str) -> dict[str, Any] | None:
    payload = get_json(f"{TSD}/searchteams.php?t={urllib.parse.quote_plus(name)}")
    rows = payload.get("teams") or []
    target = slug(name)
    for row in rows:
        names = [row.get("strTeam"), row.get("strTeamAlternate"), row.get("strTeamShort")]
        if any(target == slug(str(value)) for value in names if value):
            return row
    return rows[0] if rows else None


def players_for(team_id: str) -> tuple[list[dict[str, Any]], str]:
    url = f"{TSD}/lookup_all_players.php?id={urllib.parse.quote_plus(team_id)}"
    payload = get_json(url)
    result = []
    for player in payload.get("player") or []:
        name = player.get("strPlayer")
        if not name:
            continue
        result.append({
            "player_id": str(player.get("idPlayer") or "") or None,
            "player_name": name,
            "positions": [player.get("strPosition")] if player.get("strPosition") else [],
            "age": None,
            "shirt_number": player.get("strNumber") or None,
            "squad_status": "roster-listed",
            "roster_source": "TheSportsDB roster fallback",
        })
    # deterministic name de-duplication
    dedup = {}
    for row in result:
        dedup.setdefault(slug(str(row["player_name"])), row)
    return list(dedup.values()), url


def fallback(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    provider_ids = snapshot.get("provider_ids") or {}
    existing_id = str(provider_ids.get("thesportsdb_team_id") or "")
    team = None
    if existing_id:
        team = {"idTeam": existing_id, "strTeam": snapshot.get("team_name")}
    else:
        team = search_team(str(snapshot.get("team_name") or ""))
    if not team or not team.get("idTeam"):
        return [], None, None
    players, url = players_for(str(team["idTeam"]))
    return players, team, url


def main() -> int:
    latest = latest_aggregate()
    generated = now()
    if latest is None:
        payload = {"schema_version": "V6.6.4-roster-repair-status-r1", "generated_at_utc": generated.isoformat(), "status": "NO_WEEKLY_AGGREGATE", "repaired": 0}
        OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0
    source_path, aggregate = latest
    snapshots = [x for x in aggregate.get("snapshots") or [] if isinstance(x, dict)]
    deficient = [x for x in snapshots if len(x.get("players") or []) < MIN_PLAYERS]
    overlays = []
    attempts = []
    for snapshot in deficient:
        before = len(snapshot.get("players") or [])
        record = {"competition_id": snapshot.get("competition_id"), "team_name": snapshot.get("team_name"), "before_players": before, "status": "NOT_REPAIRED"}
        try:
            fallback_players, team, url = fallback(snapshot)
            record["fallback_players"] = len(fallback_players)
            if len(fallback_players) > before:
                repaired = json.loads(json.dumps(snapshot))
                repaired["schema_version"] = "V6.6.4-roster-repair-overlay-r1"
                repaired["observed_at_utc"] = generated.isoformat()
                repaired["players"] = fallback_players
                repaired.setdefault("provider_ids", {})["thesportsdb_team_id"] = (team or {}).get("idTeam")
                health = repaired.setdefault("source_health", {})
                health["primary_named_player_count_before_repair"] = before
                health["fallback_named_player_count"] = len(fallback_players)
                health["named_player_count"] = len(fallback_players)
                health["roster_content_ok"] = len(fallback_players) >= MIN_PLAYERS
                health["roster_fallback_used"] = True
                health["roster_fallback_reason"] = "PRIMARY_NAMED_PLAYER_COUNT_BELOW_18"
                repaired.setdefault("sources", []).append({
                    "source_name": "TheSportsDB free API", "source_tier": "tier_3_fallback", "source_url": url,
                    "source_observed_at_utc": generated.isoformat(), "source_role": "sub18_roster_repair", "source_reached": True,
                })
                repaired.setdefault("governance", {})["sub18_repair_overlay"] = True
                repaired["governance"]["primary_availability_transactions_depth_preserved"] = True
                repaired["governance"]["formal_probability_use"] = False
                overlays.append(repaired)
                record["status"] = "REPAIRED_TO_STRICT_ROSTER" if len(fallback_players) >= MIN_PLAYERS else "IMPROVED_BUT_STILL_SUB18"
            else:
                record["status"] = "FALLBACK_NOT_MORE_COMPLETE"
        except Exception as exc:
            record["status"] = "FALLBACK_ERROR"
            record["error"] = f"{type(exc).__name__}: {exc}"
        attempts.append(record)

    overlay_path = None
    if overlays:
        stamp = generated.strftime("%Y%m%dT%H%M%SZ")
        overlay_path = EVIDENCE / f"weekly_roster_repair__{stamp}.json"
        overlay_payload = {
            "schema_version": "V6.6.4-weekly-roster-repair-aggregate-r1",
            "observed_at_utc": generated.isoformat(),
            "source_weekly_aggregate": str(source_path.relative_to(ROOT)),
            "snapshot_count": len(overlays),
            "snapshots": overlays,
            "governance": {"append_only_overlay": True, "formal_probability_use": False, "replace_only_if_fallback_count_is_larger": True},
        }
        overlay_path.write_text(json.dumps(overlay_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    repaired_strict = sum(1 for x in attempts if x["status"] == "REPAIRED_TO_STRICT_ROSTER")
    payload = {
        "schema_version": "V6.6.4-roster-repair-status-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS" if not any(x["status"] == "FALLBACK_ERROR" for x in attempts) else "WARN",
        "source_weekly_aggregate": str(source_path.relative_to(ROOT)),
        "total_team_snapshots": len(snapshots),
        "sub18_before_repair": len(deficient),
        "repair_attempt_count": len(attempts),
        "strict_repairs_created": repaired_strict,
        "overlays_created": len(overlays),
        "overlay_path": str(overlay_path.relative_to(ROOT)) if overlay_path else None,
        "attempts": attempts,
        "governance": {"research_context_only": True, "no_formal_probability_change": True, "no_cross_source_player_list_merge": True},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
