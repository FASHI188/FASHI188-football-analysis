#!/usr/bin/env python3
"""Build the exact identity surface used by live full17 market capture.

The long-term 17-domain registry remains the base. During a dated competition phase
whose participants are independently frozen from an official source (currently the
2026/27 UCL second qualifying round), those phase identities are added ONLY to the
capture surface. They do not mutate the long-term 36-team UCL league-phase baseline.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "config" / "v6_full17_identity_registry_v6482.json"
UCL = ROOT / "config" / "v6_ucl_qualifying_identity_v6484.json"
OUT = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
TRANSLATE = str.maketrans({"ø":"o","Ø":"o","ł":"l","Ł":"l","đ":"d","Đ":"d","ð":"d","Ð":"d","þ":"th","Þ":"th","æ":"ae","Æ":"ae","œ":"oe","Œ":"oe"})


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").translate(TRANSLATE)).casefold()
    chars = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        chars.append(ch if ch.isalnum() else " ")
    return " ".join("".join(chars).split())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_date(value: object) -> date:
    return date.fromisoformat(str(value))


def main() -> int:
    base = json.loads(BASE.read_text(encoding="utf-8"))
    if base.get("status") != "PASS_ALL_17":
        raise SystemExit(f"base identity registry not PASS_ALL_17: {base.get('status')}")

    payload: dict[str, Any] = json.loads(json.dumps(base))
    payload["schema_version"] = "V6.48.4-full17-live-capture-identity-r1"
    payload["generated_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["base_identity_registry_path"] = str(BASE.relative_to(ROOT))
    payload["base_identity_registry_sha256"] = sha(BASE)
    payload["supplements"] = []

    if UCL.exists():
        ucl = json.loads(UCL.read_text(encoding="utf-8"))
        window = ucl.get("valid_window") or {}
        today = datetime.now(timezone.utc).date()
        start = parse_date(window.get("start_date"))
        end = parse_date(window.get("end_date"))
        if start <= today <= end:
            comp = payload["competitions"]["UEFA_ChampionsLeague"]
            teams = comp.get("teams") or []
            by_token = {str(t.get("normalized_identity") or norm(t.get("canonical_name"))): t for t in teams if isinstance(t, dict)}
            supplement_aliases = {norm(k): str(v) for k, v in (ucl.get("exact_provider_aliases") or {}).items()}
            added = []
            for canonical in ucl.get("teams") or []:
                canonical = str(canonical).strip()
                token = norm(canonical)
                if not token:
                    continue
                if token not in by_token:
                    row = {
                        "canonical_name": canonical,
                        "normalized_identity": token,
                        "provider_alias_tokens": [],
                        "source_path": str(UCL.relative_to(ROOT)),
                        "identity_scope": "CURRENT_UCL_QUALIFYING_ROUND_ONLY"
                    }
                    teams.append(row)
                    by_token[token] = row
                    added.append(canonical)
            for source_token, target_name in supplement_aliases.items():
                target_token = norm(target_name)
                target = by_token.get(target_token)
                if target is None:
                    raise SystemExit(f"UCL supplemental alias target absent: {source_token}->{target_name}")
                aliases = target.setdefault("provider_alias_tokens", [])
                if source_token not in aliases:
                    aliases.append(source_token)
            comp["teams"] = teams
            comp["capture_identity_count"] = len(teams)
            comp["long_term_baseline_team_count"] = int(comp.get("team_count") or 0)
            comp["temporary_phase_identity_active"] = True
            comp["temporary_phase_identity_scope"] = "2026/27 UCL second qualifying round"
            comp["temporary_phase_identity_valid_window"] = window
            payload["supplements"].append({
                "path": str(UCL.relative_to(ROOT)),
                "sha256": sha(UCL),
                "competition_id": "UEFA_ChampionsLeague",
                "active": True,
                "added_identity_count": len(added),
                "source_team_count": len(ucl.get("teams") or []),
                "valid_window": window,
            })

    payload["policy"] = (
        "Long-term current-team identity plus dated official phase identities for live capture only. "
        "No fuzzy matching. Supplemental phase identities never alter long-term competition membership."
    )
    payload["formal_weight_change"] = False
    payload["runtime_probability_change"] = False
    payload["current_rule_change"] = False
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload.get("status"),
        "base_sha256": payload["base_identity_registry_sha256"],
        "supplements": payload["supplements"],
        "ucl_capture_identity_count": ((payload.get("competitions") or {}).get("UEFA_ChampionsLeague") or {}).get("capture_identity_count")
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
