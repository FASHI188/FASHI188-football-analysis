#!/usr/bin/env python3
"""V6.6.17 ingest strict current LaLiga squads from laliga.com's own web backend.

The public laliga.com Next.js page exposes the client-side Azure APIM subscription value in
`props.runtimeConfig.backendSubscription`; this script reads that value at runtime and uses it only
in memory to call the same laliga.com `apim.laliga.com/public-service` JSON surface used by the web
client. The value is NEVER printed, persisted, committed, or copied from a third-party package.

Governance is deliberately fail-closed:
- no login, account credential, private secret, browser-cookie extraction, CAPTCHA bypass, or access-
  control circumvention;
- if the public runtime config disappears, or the official API returns 401/403, ingestion stops;
- the 2026/27 season subscription is discovered from the official subscriptions feed rather than
  guessed/hard-coded;
- only teams currently unresolved by the project's strict-current roster gate are queried;
- each team's player list comes from ONE official `/teams/{slug}/squad` response, never unions;
- >=18 unique named players are required for a V6.6.9 CURRENT_REGISTERED_SQUAD overlay;
- research context only; V5.0.1 formal probabilities and weights are unchanged.
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"
EVIDENCE = ROOT / "evidence" / "team_current_roster_weekly"
GAPS = M / "v6_roster_gap_inventory_v6611_status.json"
OUT = M / "v6_laliga_official_roster_v6617_status.json"
HOME = "https://www.laliga.com/en-GB"
API = "https://apim.laliga.com/public-service"
UA = "Mozilla/5.0 (compatible; football-v6.6.17-laliga-official-roster/1.0)"
MIN_PLAYERS = 18
MAX_PLAYERS = 60
TARGET_YEAR = 2026

# Deterministic identity aliases only for project names that can be strict-roster gaps. Matching is
# one-to-one; ambiguity fails closed. Accents/punctuation are normalized before comparison.
ALIASES: dict[str, set[str]] = {
    "Atlético Madrid": {"atletico madrid", "atletico de madrid", "club atletico de madrid"},
    "Celta Vigo": {"celta vigo", "celta de vigo", "rc celta", "real club celta de vigo"},
    "Deportivo La Coruña": {"deportivo la coruna", "deportivo de la coruna", "rc deportivo", "real club deportivo de la coruna"},
    "Elche": {"elche", "elche cf", "elche club de futbol"},
    "Espanyol": {"espanyol", "rcd espanyol", "rcd espanyol de barcelona"},
    "Málaga": {"malaga", "malaga cf", "malaga club de futbol"},
    "Osasuna": {"osasuna", "ca osasuna", "club atletico osasuna"},
    "Real Betis": {"real betis", "real betis balompie", "betis"},
    "Real Madrid": {"real madrid", "real madrid cf", "real madrid club de futbol"},
    "Real Sociedad": {"real sociedad", "real sociedad de futbol"},
    "Valencia": {"valencia", "valencia cf", "valencia club de futbol"},
    "Villarreal": {"villarreal", "villarreal cf", "villarreal club de futbol"},
}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def fetch_bytes(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, datetime, str | None]:
    req_headers = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            charset = response.headers.get_content_charset()
    except urllib.error.HTTPError as exc:
        # 401/403 are permanent fail-closed auth/access-control outcomes. Do not retry or attempt an
        # alternative credential source.
        if exc.code in {401, 403}:
            raise RuntimeError(f"OFFICIAL_API_ACCESS_DENIED_{exc.code}") from exc
        raise
    return raw, now(), charset


def fetch_html(url: str) -> tuple[str, datetime]:
    raw, observed, charset = fetch_bytes(url, {"Accept": "text/html,application/xhtml+xml"})
    enc = charset or "utf-8"
    try:
        return raw.decode(enc, errors="strict"), observed
    except Exception:
        return raw.decode("utf-8", errors="replace"), observed


def extract_public_runtime_key(markup: str) -> str:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', markup, re.I | re.S)
    if not match:
        raise RuntimeError("LALIGA_PUBLIC_NEXT_DATA_MISSING")
    try:
        payload = json.loads(html.unescape(match.group(1)).strip())
    except Exception as exc:
        raise RuntimeError("LALIGA_PUBLIC_NEXT_DATA_INVALID") from exc
    try:
        value = payload["props"]["runtimeConfig"]["backendSubscription"]
    except Exception:
        value = None
    if not isinstance(value, str) or len(value.strip()) < 16:
        raise RuntimeError("LALIGA_PUBLIC_RUNTIME_SUBSCRIPTION_MISSING")
    return value.strip()


def api_json(path: str, public_key: str, query: dict[str, Any] | None = None) -> tuple[dict[str, Any], datetime, str]:
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    raw, observed, _charset = fetch_bytes(
        url,
        {
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": public_key,
            "Referer": "https://www.laliga.com/",
            "Origin": "https://www.laliga.com",
        },
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("LALIGA_OFFICIAL_API_NON_JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("LALIGA_OFFICIAL_API_UNEXPECTED_ROOT")
    return payload, observed, url


def project_gap_teams() -> list[str]:
    if not GAPS.exists():
        raise RuntimeError("STRICT_ROSTER_GAP_RECEIPT_MISSING")
    payload = json.loads(GAPS.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS":
        raise RuntimeError("STRICT_ROSTER_GAP_RECEIPT_NOT_PASS")
    names = sorted({str(row.get("team_name")) for row in payload.get("gaps") or [] if isinstance(row, dict) and row.get("competition_id") == "ESP_LaLiga" and row.get("team_name")})
    return names


def competition_slug(value: Any) -> str:
    if isinstance(value, dict):
        return norm(value.get("slug") or value.get("name") or "")
    return norm(value or "")


def discover_subscription(public_key: str) -> tuple[dict[str, Any], datetime, str]:
    offset = 0
    seen = 0
    candidates: list[dict[str, Any]] = []
    latest_observed = now(); last_url = ""
    while offset <= 400:
        page, observed, url = api_json("/api/v1/subscriptions", public_key, {"offset": offset})
        latest_observed = max(latest_observed, observed); last_url = url
        rows = page.get("subscriptions") or []
        if not isinstance(rows, list):
            raise RuntimeError("LALIGA_SUBSCRIPTIONS_NOT_LIST")
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                year = int(row.get("year"))
            except Exception:
                continue
            comp = competition_slug(row.get("competition"))
            slug = str(row.get("slug") or "")
            if year == TARGET_YEAR and ("primera division" in comp or "primera-division" in str(row.get("competition") or "") or slug.startswith("laliga-easports-")):
                candidates.append(row)
        seen += len(rows)
        total = int(page.get("total") or seen)
        if candidates or not rows or seen >= total:
            break
        offset += max(20, len(rows))
        time.sleep(0.25)
    unique = {str(row.get("slug") or ""): row for row in candidates if row.get("slug")}
    if len(unique) != 1:
        raise RuntimeError(f"LALIGA_2026_SUBSCRIPTION_AMBIGUOUS_COUNT_{len(unique)}")
    summary = next(iter(unique.values()))
    slug = str(summary["slug"])
    detail, observed, detail_url = api_json(f"/api/v1/subscriptions/{urllib.parse.quote(slug, safe='')}", public_key)
    latest_observed = max(latest_observed, observed)
    subscription = detail.get("subscription") if isinstance(detail.get("subscription"), dict) else detail
    if str(subscription.get("slug") or slug) != slug:
        raise RuntimeError("LALIGA_SUBSCRIPTION_DETAIL_SLUG_MISMATCH")
    return subscription, latest_observed, detail_url


def team_strings(team: dict[str, Any]) -> set[str]:
    values = {team.get("name"), team.get("shortname"), team.get("boundname"), team.get("nickname"), team.get("slug")}
    return {norm(v) for v in values if v}


def resolve_targets(targets: list[str], official_teams: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    resolved: dict[str, dict[str, Any]] = {}; audit=[]
    for project_name in targets:
        aliases = {norm(project_name)} | {norm(x) for x in ALIASES.get(project_name, set())}
        matches=[]
        for official in official_teams:
            strings=team_strings(official)
            if aliases & strings:
                matches.append(official)
        if len(matches) == 1:
            resolved[project_name]=matches[0]
            audit.append({"project_team_name": project_name, "status": "MATCHED", "official_team_name": matches[0].get("name"), "official_team_slug": matches[0].get("slug")})
        else:
            audit.append({"project_team_name": project_name, "status": "FAIL_CLOSED", "match_count": len(matches), "candidate_names": [m.get("name") for m in matches]})
    return resolved, audit


def parse_players(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = payload.get("squads") or []
    if not isinstance(rows, list):
        return [], {"reason": "squads_not_list"}
    players=[]; seen=set(); duplicates=0; skipped_noncurrent=0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("current") is False:
            skipped_noncurrent += 1; continue
        person = row.get("person") or {}
        if not isinstance(person, dict):
            person = {}
        name = str(person.get("name") or row.get("name") or "").strip()
        if not name:
            continue
        identity = str(row.get("opta_id") or row.get("id") or norm(name))
        if identity in seen:
            duplicates += 1; continue
        seen.add(identity)
        position = row.get("position")
        position_name = position.get("name") if isinstance(position, dict) else None
        position_id = position.get("id") if isinstance(position, dict) else position
        players.append({
            "player_id": str(row.get("opta_id") or row.get("id") or "") or None,
            "player_name": name,
            "positions": [str(position_name or position_id)] if (position_name is not None or position_id is not None) else [],
            "shirt_number": row.get("shirt_number"),
            "squad_status": "official-current-squad",
            "roster_source": "LaLiga official website backend current squad",
        })
    return players, {"raw_rows": len(rows), "unique_players": len(players), "duplicates_collapsed": duplicates, "noncurrent_rows_skipped": skipped_noncurrent}


def main() -> int:
    generated = now(); EVIDENCE.mkdir(parents=True, exist_ok=True); OUT.parent.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "schema_version": "V6.6.17-laliga-official-roster-status-r1",
        "generated_at_utc": generated.isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "FAIL_CLOSED",
        "target_year": TARGET_YEAR,
        "target_gap_count": 0,
        "valid_current_roster_count": 0,
        "records": [],
        "identity_audit": [],
        "governance": {
            "official_laliga_web_surfaces_only": True,
            "public_runtime_client_config_only": True,
            "subscription_value_persisted": False,
            "subscription_value_logged": False,
            "login_or_private_credentials_used": False,
            "access_control_bypass": False,
            "http_401_403_fail_closed": True,
            "single_official_squad_response_per_team": True,
            "no_cross_source_union": True,
            "research_context_only": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
        },
    }
    try:
        targets = project_gap_teams(); status["target_gap_count"] = len(targets)
        markup, homepage_observed = fetch_html(HOME)
        public_key = extract_public_runtime_key(markup)  # memory only; never stored below
        subscription, subscription_observed, subscription_url = discover_subscription(public_key)
        subscription_slug = str(subscription.get("slug") or "")
        official_teams = subscription.get("teams") or []
        if not subscription_slug or not isinstance(official_teams, list) or len(official_teams) < 18:
            raise RuntimeError("LALIGA_2026_SUBSCRIPTION_TEAMS_INCOMPLETE")
        resolved, identity_audit = resolve_targets(targets, [x for x in official_teams if isinstance(x, dict)])
        status["identity_audit"] = identity_audit
        if len(resolved) != len(targets):
            raise RuntimeError(f"LALIGA_IDENTITY_RESOLUTION_INCOMPLETE_{len(resolved)}_OF_{len(targets)}")
        evidence_records=[]; team_audit=[]
        for project_name in targets:
            team = resolved[project_name]; slug = str(team.get("slug") or "")
            if not slug:
                team_audit.append({"team_name": project_name, "status": "FAIL_CLOSED", "reason": "official_team_slug_missing"}); continue
            payload, source_observed, squad_url = api_json(f"/api/v1/teams/{urllib.parse.quote(slug, safe='')}/squad", public_key, {"subscription": subscription_slug})
            players, parsed = parse_players(payload)
            valid = MIN_PLAYERS <= len(players) <= MAX_PLAYERS
            team_audit.append({"team_name": project_name, "official_team_name": team.get("name"), "official_team_slug": slug, "status": "PASS_STRICT_CURRENT" if valid else "FAIL_CLOSED", "player_count": len(players), "parse": parsed})
            if valid:
                evidence_records.append({
                    "schema_version": "V6.6.9-current-roster-overlay-r1",
                    "competition_id": "ESP_LaLiga",
                    "team_name": project_name,
                    "observed_at_utc": source_observed.isoformat(),
                    "roster_semantics": "CURRENT_REGISTERED_SQUAD",
                    "players": players,
                    "sources": [{
                        "source_name": "LaLiga official website public-service squad backend",
                        "source_url": squad_url,
                        "source_tier": "tier_1_official",
                        "provider_group": "laliga_official_web_backend",
                        "source_observed_at_utc": source_observed.isoformat(),
                        "source_role": "current_registered_squad",
                    }],
                    "source_metadata": {
                        "official_team_slug": slug,
                        "official_team_name": team.get("name"),
                        "official_team_opta_id": team.get("opta_id"),
                        "official_subscription_slug": subscription_slug,
                        "subscription_discovered_dynamically": True,
                        "homepage_runtime_config_observed_at_utc": homepage_observed.isoformat(),
                        "subscription_detail_observed_at_utc": subscription_observed.isoformat(),
                        "subscription_detail_url": subscription_url,
                        "public_runtime_subscription_value_persisted": False,
                        "parse": parsed,
                    },
                    "governance": {
                        "current_at_observation_time": True,
                        "single_source_player_list": True,
                        "single_endpoint_player_list": True,
                        "cross_source_union": False,
                        "public_web_client_runtime_config_only": True,
                        "no_private_credentials": True,
                        "research_context_only": True,
                        "formal_probability_use": False,
                    },
                })
            time.sleep(0.30)
        status["records"] = team_audit
        status["subscription_slug"] = subscription_slug
        status["official_season_team_count"] = len(official_teams)
        status["valid_current_roster_count"] = len(evidence_records)
        if evidence_records:
            stamp=max(datetime.fromisoformat(r["observed_at_utc"]) for r in evidence_records).strftime("%Y%m%dT%H%M%SZ")
            evidence_path=EVIDENCE/f"laliga_current_rosters__{stamp}.json"
            evidence_path.write_text(json.dumps({"schema_version":"V6.6.17-laliga-current-roster-weekly-aggregate-r1","observed_at_utc":generated.isoformat(),"records":evidence_records,"governance":{"official_laliga_web_backend":True,"public_runtime_subscription_value_persisted":False,"single_endpoint_player_lists":True,"research_context_only":True,"formal_probability_use":False}},ensure_ascii=False,indent=2),encoding="utf-8")
            status["evidence_path"] = str(evidence_path.relative_to(ROOT))
        status["status"] = "PASS_COMPLETE" if len(evidence_records)==len(targets) and targets else "WARN_PARTIAL" if evidence_records else "FAIL_NO_VALID_ROSTERS"
    except Exception as exc:
        status["status"] = "FAIL_CLOSED"
        status["error"] = f"{type(exc).__name__}: {exc}"
    # Deliberately no key variable or value is written to this JSON.
    OUT.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 0 if status["status"] in {"PASS_COMPLETE","WARN_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())