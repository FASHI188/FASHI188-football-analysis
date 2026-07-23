#!/usr/bin/env python3
"""V6.6.11 build an auditable queue of unresolved strict current-roster gaps.

The queue is derived from existing validated receipts only. A team is unresolved when the latest
weekly baseline fails the >=18 strict-current gate and there is no passing V6.6.9 current-roster
overlay addition for that same team. Prior-season provisional continuity is reported separately and
never upgrades strict-current status.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"
TEAM = M / "v6_team_configuration_weekly_v660_status.json"
CURRENT = M / "v6_current_roster_overlay_v669_status.json"
PROV = M / "v6_team_provisional_roster_v667_status.json"
EFFECTIVE = M / "v6_team_context_effective_v6610_status.json"
OUT = M / "v6_roster_gap_inventory_v6611_status.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def key(cid, team) -> tuple[str, str]:
    return str(cid), norm(str(team))


def main() -> int:
    team = load(TEAM); current = load(CURRENT); prov = load(PROV); effective = load(EFFECTIVE)
    latest = [row for row in team.get("latest") or [] if isinstance(row, dict)]
    if not latest:
        raise SystemExit("weekly team latest inventory missing")

    overlay_additions = set()
    for row in current.get("matched_overlays") or []:
        if row.get("strict_roster_addition") is True:
            overlay_additions.add(key(row.get("competition_id"), row.get("resolved_team_name")))

    prov_map = {}
    for row in prov.get("attempts") or []:
        if not isinstance(row, dict):
            continue
        k = key(row.get("competition_id"), row.get("team_name"))
        if row.get("status") == "PROVISIONAL_CONTINUITY_AVAILABLE":
            prov_map[k] = {
                "previous_season": row.get("previous_season"),
                "previous_player_count": int(row.get("previous_player_count") or 0),
            }

    gaps = []
    for row in latest:
        if row.get("roster_research_eligible") is True:
            continue
        k = key(row.get("competition_id"), row.get("team_name"))
        if k in overlay_additions:
            continue
        p = prov_map.get(k)
        gaps.append({
            "competition_id": row.get("competition_id"),
            "team_name": row.get("team_name"),
            "season": row.get("season"),
            "base_named_players": int(row.get("players") or 0),
            "provisional_previous_season_available": p is not None,
            "previous_season": p.get("previous_season") if p else None,
            "previous_season_player_count": p.get("previous_player_count") if p else 0,
            "priority": "A_NO_CURRENT_CONTEXT" if p is None else "B_HAS_PROVISIONAL_ONLY",
            "required_resolution": "CURRENT_FIRST_TEAM_OR_CURRENT_REGISTERED_SQUAD_CONTRACT_QUALIFIED",
        })

    gaps.sort(key=lambda r: (r["priority"], str(r["competition_id"]), str(r["team_name"])))
    by_comp = Counter(str(r["competition_id"]) for r in gaps)
    no_context = sum(r["priority"] == "A_NO_CURRENT_CONTEXT" for r in gaps)
    provisional_only = len(gaps) - no_context
    expected = ((effective.get("roster_context_states") or {}).get("NO_ROSTER_CONTEXT"), (effective.get("roster_context_states") or {}).get("PROVISIONAL_ONLY"))
    consistency = no_context == int(expected[0] or 0) and provisional_only == int(expected[1] or 0)

    payload = {
        "schema_version": "V6.6.11-roster-gap-inventory-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if consistency else "FAIL_CONTEXT_STATE_MISMATCH",
        "formal_current_version": "V5.0.1",
        "team_count": len(latest),
        "effective_strict_current_count": int(((effective.get("roster_context_states") or {}).get("STRICT_CURRENT")) or 0),
        "unresolved_strict_current_gap_count": len(gaps),
        "priority_counts": {
            "A_NO_CURRENT_CONTEXT": no_context,
            "B_HAS_PROVISIONAL_ONLY": provisional_only,
        },
        "competition_gap_counts": dict(sorted(by_comp.items())),
        "gaps": gaps,
        "consistency_check": {
            "effective_no_roster_context": int(expected[0] or 0),
            "effective_provisional_only": int(expected[1] or 0),
            "inventory_matches_effective_states": consistency,
            "probability_conservation": len(gaps) + int(((effective.get("roster_context_states") or {}).get("STRICT_CURRENT")) or 0) == len(latest),
        },
        "governance": {
            "derived_from_validated_receipts_only": True,
            "provisional_never_counts_as_strict": True,
            "no_player_list_union": True,
            "research_context_only": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("status", "effective_strict_current_count", "unresolved_strict_current_gap_count", "priority_counts", "competition_gap_counts", "consistency_check")}, ensure_ascii=False, indent=2))
    return 0 if consistency else 2


if __name__ == "__main__":
    raise SystemExit(main())