#!/usr/bin/env python3
"""Build a fail-closed current-team identity registry for all 17 formal domains.

Primary source is recent team-configuration / current-roster evidence already frozen in
GitHub. Processed latest-season results are only a fallback when a domain has no recent
current-team evidence. Provider aliases are exact and never fuzzy.

V6.48.3 adds an explicit exact-alias layer for provider/current-evidence naming variants.
An alias target must already exist inside the SAME current domain. This layer can also
collapse duplicate current-evidence names such as RCD Espanyol -> Espanyol, but it can
never create a club that is absent from the current domain.

Research/evidence routing only. No CURRENT/probability/formal-weight mutation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "v6_full17_identity_registry_v6482.json"
WEEKLY = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"
LEGACY_ALIASES = ROOT / "config" / "active_domain_provider_aliases_v5532.json"
EXACT_ALIASES = ROOT / "config" / "v6_full17_provider_aliases_v6483.json"
TARGETS = (
    "ARG_Primera", "BRA_SerieA", "ENG_PremierLeague", "ESP_LaLiga", "FRA_Ligue1",
    "GER_Bundesliga", "ITA_SerieA", "JPN_J1", "KOR_KLeague1", "NED_Eredivisie",
    "NOR_Eliteserien", "POR_PrimeiraLiga", "SCO_Premiership", "SUI_SuperLeague",
    "SWE_Allsvenskan", "UEFA_ChampionsLeague", "USA_MLS",
)
RECENT_DAYS = 45
TRANSLATE = str.maketrans({"ø":"o","Ø":"o","ł":"l","Ł":"l","đ":"d","Đ":"d","ð":"d","Ð":"d","þ":"th","Þ":"th","æ":"ae","Æ":"ae","œ":"oe","Œ":"oe"})


def now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").translate(TRANSLATE)).casefold()
    out = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        x = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def walk_records(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        cid = str(obj.get("competition_id") or "").strip()
        team = str(obj.get("team_name") or obj.get("canonical_name") or "").strip()
        if cid in TARGETS and team:
            yield obj
        for value in obj.values():
            yield from walk_records(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_records(value)


def evidence_roots() -> list[Path]:
    roots = [
        ROOT / "evidence" / "team_configuration_weekly",
        ROOT / "evidence" / "team_current_roster_weekly",
    ]
    return [p for p in roots if p.exists()]


def collect_recent_evidence(now: datetime) -> dict[str, dict[str, dict[str, Any]]]:
    cutoff = now - timedelta(days=RECENT_DAYS)
    rows: dict[str, list[tuple[datetime, str, Path]]] = defaultdict(list)
    for base in evidence_roots():
        for path in sorted(base.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for rec in walk_records(payload):
                cid = str(rec.get("competition_id") or "").strip()
                team = str(rec.get("team_name") or rec.get("canonical_name") or "").strip()
                observed = (
                    parse_dt(rec.get("observed_at_utc"))
                    or parse_dt(rec.get("source_observed_at_utc"))
                    or parse_dt(rec.get("generated_at_utc"))
                )
                if not observed or observed < cutoff or not norm(team):
                    continue
                rows[cid].append((observed, team, path))

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for cid in TARGETS:
        items = rows.get(cid, [])
        if not items:
            out[cid] = {}
            continue
        max_obs = max(x[0] for x in items)
        cluster_cut = max_obs - timedelta(hours=36)
        selected: dict[str, dict[str, Any]] = {}
        for observed, team, path in items:
            if observed < cluster_cut:
                continue
            token = norm(team)
            prev = selected.get(token)
            if prev is None or observed > prev["observed_at"]:
                selected[token] = {
                    "canonical_name": team,
                    "observed_at": observed,
                    "source_path": str(path.relative_to(ROOT)),
                }
        out[cid] = selected
    return out


def season_sort(label: str) -> tuple[int, str]:
    nums = re.findall(r"20\d{2}", str(label))
    return (int(nums[0]) if nums else -1, str(label))


def latest_processed_teams(cid: str) -> tuple[str | None, dict[str, dict[str, Any]]]:
    base = ROOT / "processed" / cid
    if not base.exists():
        return None, {}
    by_season: dict[str, Counter[str]] = defaultdict(Counter)
    for path in sorted(base.glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    season = str(raw.get("season") or raw.get("Season") or path.stem).strip()
                    home = str(raw.get("HomeTeam") or raw.get("Home") or "").strip()
                    away = str(raw.get("AwayTeam") or raw.get("Away") or "").strip()
                    if home:
                        by_season[season][home] += 1
                    if away:
                        by_season[season][away] += 1
        except Exception:
            continue
    if not by_season:
        return None, {}
    season = sorted(by_season, key=season_sort)[-1]
    teams = {}
    for team, count in by_season[season].items():
        token = norm(team)
        if token:
            teams[token] = {
                "canonical_name": team,
                "observation_count": int(count),
                "source_path": f"processed/{cid}",
            }
    return season, teams


def load_expected_counts() -> dict[str, int]:
    try:
        data = json.loads(WEEKLY.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in (data.get("domain_team_counts") or {}).items()}
    except Exception:
        return {}


def load_alias_file(path: Path, *, legacy: bool) -> tuple[dict[str, dict[str, str]], str | None]:
    if not path.exists():
        return {}, None
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if legacy and data.get("fuzzy_matching") is not False:
            return {}, hashlib.sha256(raw).hexdigest()
        aliases = data.get("aliases") or {}
        return {
            str(cid): {str(k): str(v) for k, v in rows.items()}
            for cid, rows in aliases.items() if isinstance(rows, dict)
        }, hashlib.sha256(raw).hexdigest()
    except Exception:
        return {}, None


def main() -> int:
    now = now_dt()
    expected = load_expected_counts()
    recent = collect_recent_evidence(now)
    legacy_aliases, legacy_sha = load_alias_file(LEGACY_ALIASES, legacy=True)
    exact_aliases_cfg, exact_sha = load_alias_file(EXACT_ALIASES, legacy=False)
    competitions: dict[str, Any] = {}
    available = 0

    for cid in TARGETS:
        recent_rows = dict(recent.get(cid, {}))
        latest_season, processed = latest_processed_teams(cid)
        expected_n = int(expected.get(cid) or 0)

        use_recent = bool(recent_rows) and (
            expected_n == 0 or len(recent_rows) >= max(8, int(expected_n * 0.80))
        )
        chosen = dict(recent_rows if use_recent else processed)
        source_mode = (
            "RECENT_CURRENT_TEAM_EVIDENCE" if use_recent
            else "LATEST_PROCESSED_SEASON_FALLBACK"
        )

        # Collapse only explicitly registered same-domain duplicate evidence names when
        # BOTH source and target already exist in the selected current set.
        collapsed_evidence_aliases = []
        for source, canonical in exact_aliases_cfg.get(cid, {}).items():
            stoken, ctoken = norm(source), norm(canonical)
            if stoken and ctoken and stoken != ctoken and stoken in chosen and ctoken in chosen:
                source_row = chosen.pop(stoken)
                collapsed_evidence_aliases.append({
                    "source_name": source,
                    "source_token": stoken,
                    "target_canonical": chosen[ctoken]["canonical_name"],
                    "source_path": source_row.get("source_path"),
                })

        canon_by_norm = {token: row["canonical_name"] for token, row in chosen.items()}
        provider_aliases: dict[str, str] = {}
        alias_errors: list[str] = []

        def bind_alias(source: str, canonical: str, *, strict_target: bool) -> None:
            stoken, ctoken = norm(source), norm(canonical)
            if not stoken:
                return
            if ctoken not in canon_by_norm:
                if strict_target:
                    alias_errors.append(f"ALIAS_TARGET_NOT_CURRENT:{source}->{canonical}")
                return
            target = canon_by_norm[ctoken]
            prev = provider_aliases.get(stoken)
            if prev is not None and prev != target:
                alias_errors.append(f"ALIAS_COLLISION:{source}:{prev}/{target}")
                return
            provider_aliases[stoken] = target

        for source, canonical in legacy_aliases.get(cid, {}).items():
            bind_alias(source, canonical, strict_target=False)
        for source, canonical in exact_aliases_cfg.get(cid, {}).items():
            bind_alias(source, canonical, strict_target=True)

        teams = []
        for token, row in sorted(chosen.items()):
            canonical = row["canonical_name"]
            aliases = sorted(
                source for source, target in provider_aliases.items() if target == canonical
            )
            teams.append({
                "canonical_name": canonical,
                "normalized_identity": token,
                "provider_alias_tokens": aliases,
                "source_path": row.get("source_path"),
            })

        team_count = len(teams)
        count_ok = expected_n == 0 or team_count == expected_n
        status = "PASS" if team_count >= 8 and count_ok and not alias_errors else "PARTIAL_FAIL_CLOSED"
        if status == "PASS":
            available += 1
        competitions[cid] = {
            "competition_id": cid,
            "status": status,
            "identity_source_mode": source_mode,
            "team_count": team_count,
            "expected_team_count_hint": expected_n or None,
            "expected_team_count_matches": count_ok,
            "processed_latest_season_hint": latest_season,
            "teams": teams,
            "collapsed_evidence_aliases": collapsed_evidence_aliases,
            "provider_alias_count": len(provider_aliases),
            "alias_errors": alias_errors,
            "fuzzy_matching": False,
        }

    payload = {
        "schema_version": "V6.48.3-full17-current-team-identity-registry-r2",
        "generated_at_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_ALL_17" if available == 17 else "PASS_PARTIAL_FAIL_CLOSED",
        "target_competition_count": 17,
        "available_competition_count": available,
        "legacy_alias_registry_sha256": legacy_sha,
        "exact_alias_registry_path": str(EXACT_ALIASES.relative_to(ROOT)),
        "exact_alias_registry_sha256": exact_sha,
        "competitions": competitions,
        "policy": (
            "Prefer fresh current-team evidence cluster; fall back to one latest processed season per domain. "
            "Require expected current-domain team count when available. Exact normalized identity and explicit "
            "same-domain aliases only. No fuzzy matching or cross-club guessing."
        ),
        "formal_weight_change": False,
        "runtime_probability_change": False,
        "current_rule_change": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "available": available,
        "team_counts": {cid: competitions[cid]["team_count"] for cid in TARGETS},
        "count_match": {cid: competitions[cid]["expected_team_count_matches"] for cid in TARGETS},
        "alias_counts": {cid: competitions[cid]["provider_alias_count"] for cid in TARGETS},
        "alias_errors": {cid: competitions[cid]["alias_errors"] for cid in TARGETS if competitions[cid]["alias_errors"]},
    }, ensure_ascii=False, indent=2))
    return 0 if available else 2


if __name__ == "__main__":
    raise SystemExit(main())
