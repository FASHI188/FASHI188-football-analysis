#!/usr/bin/env python3
"""V6.2.4 r2: rerun xG residual challenge using Understat's current AJAX endpoint.

The original r1 failed only because the legacy HTML `teamsData` extraction no longer matched
Understat's current delivery path. This wrapper replaces that fetcher with the documented
`getLeagueData/<league>/<season>` AJAX JSON endpoint and then runs the unchanged r1 model,
splits, gates and leakage controls.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_understat_xg_residual_v624 as r1
from platform_core import PlatformError

OUT = ROOT / "manifests" / "v6_understat_xg_residual_v624_r2_status.json"


def _fetch_understat_teams_ajax(league: str, year: int) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://understat.com/getLeagueData/{league}/{year}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football-v6.2-xg-research/2.0",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://understat.com/league/{league}/{year}",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PlatformError(f"Understat AJAX JSON decode failed: {url}: {exc}") from exc
    teams = payload.get("teams") if isinstance(payload, dict) else None
    if not isinstance(teams, dict) or not teams:
        raise PlatformError(f"Understat AJAX teams invalid: {url}")
    return teams, {
        "url": url,
        "transport": "ajax_json",
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "team_count": len(teams),
        "payload_keys": sorted(payload.keys()),
    }


def main() -> int:
    r1.OUT = OUT
    r1._fetch_understat_teams = _fetch_understat_teams_ajax
    code = r1.main()
    # Rewrite schema marker so downstream readers can distinguish fixed transport from r1.
    if OUT.exists():
        data = json.loads(OUT.read_text(encoding="utf-8"))
        data["schema_version"] = "V6.2.4-understat-xg-residual-r2-ajax"
        data.setdefault("governance", {})["r1_model_logic_unchanged"] = True
        data["governance"]["data_transport_fix_only"] = True
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
