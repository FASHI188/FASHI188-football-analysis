#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-permanent-team-identity-bridge-v1"
CURRENT_REGISTRY = "football-data/config/current_season_team_identity_v5524.json"
FULL17_REGISTRY = "football-data/config/v6_full17_capture_identity_v6484.json"
FULL17_ALIASES = "football-data/config/v6_full17_provider_aliases_v6483.json"
ACTIVE_REGISTRY = "football-data/config/active_domain_identity_registry_v5532.json"
ACTIVE_ALIASES = "football-data/config/active_domain_provider_aliases_v5532.json"


def _json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"identity registry unreadable: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"identity registry object required: {path}")
    return obj


def _previous_season(season: str) -> str | None:
    a, b = rt._season_years(season)
    if a is None:
        return None
    if a == b:
        return str(a - 1)
    return f"{a - 1}/{str(a)[-2:]}"


def _formal_current_season(comp: str) -> str | None:
    seasons = list(rt.TRAINING_SEASONS.get(comp) or ())
    if not seasons:
        return None
    a, b = rt._season_years(seasons[-1])
    if a is None:
        return None
    if a == b:
        return str(a + 1)
    return f"{a + 1}/{str((b or a) + 1)[-2:]}"


def _alias_config(repo_root: Path) -> dict[str, dict[str, str]]:
    return rt._read_aliases(repo_root)


def _processed_roster(repo_root: Path, comp: str, season: str | None) -> set[str]:
    if not season:
        return set()
    directory = repo_root / "football-data" / "processed" / comp
    if not directory.is_dir():
        return set()
    aliases = _alias_config(repo_root)
    out: set[str] = set()
    for path in sorted(directory.glob("*.csv")):
        try:
            fh = path.open("r", encoding="utf-8-sig", newline="")
        except OSError:
            continue
        with fh:
            for row in csv.DictReader(fh):
                if str(row.get("season") or "").strip() != season:
                    continue
                for key in ("HomeTeam", "home_team", "AwayTeam", "away_team"):
                    raw = str(row.get(key) or "").strip()
                    if raw:
                        out.add(rt._canonical_team(comp, raw, aliases))
    return out


def _history_profile(repo_root: Path, comp: str, names: list[str]) -> tuple[list[str], int, dict[str, int]]:
    tokens = {rt._normalize_team(x) for x in names if str(x or "").strip()}
    directory = repo_root / "football-data" / "processed" / comp
    if not directory.is_dir():
        return [], 0, {}
    aliases = _alias_config(repo_root)
    seen: set[tuple[str, str, str, str]] = set()
    by_season: dict[str, int] = {}
    for path in sorted(directory.glob("*.csv")):
        try:
            fh = path.open("r", encoding="utf-8-sig", newline="")
        except OSError:
            continue
        with fh:
            for row in csv.DictReader(fh):
                season = str(row.get("season") or "").strip()
                if not season:
                    continue
                hr = str(row.get("HomeTeam") or row.get("home_team") or "").strip()
                ar = str(row.get("AwayTeam") or row.get("away_team") or "").strip()
                h = rt._canonical_team(comp, hr, aliases) if hr else ""
                a = rt._canonical_team(comp, ar, aliases) if ar else ""
                if not any(rt._normalize_team(x) in tokens for x in (hr, ar, h, a) if x):
                    continue
                date = str(row.get("Date") or row.get("date") or "").strip()
                key = (season, date, h, a)
                if key in seen:
                    continue
                seen.add(key)
                by_season[season] = by_season.get(season, 0) + 1
    return sorted(by_season), len(seen), dict(sorted(by_season.items()))


def _finalize(rows: list[dict[str, Any]], sources: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        token = rt._normalize_team(row["canonical_name"])
        if not token:
            continue
        dst = merged.setdefault(token, {"canonical_name": row["canonical_name"], "provider_names": [], "aliases": [], "sources": []})
        for key in ("provider_names", "aliases"):
            for x in row.get(key, []):
                x = str(x or "").strip()
                if x and x not in dst[key]:
                    dst[key].append(x)
        for src in row.get("sources", []):
            if src and src not in dst["sources"]:
                dst["sources"].append(src)
    return list(merged.values()), sorted(set(sources))


def _registry_rows(repo_root: Path, comp: str, season: str) -> tuple[list[dict[str, Any]], list[str]]:
    # 1) Exact season-specific registry has highest authority.
    path = repo_root / CURRENT_REGISTRY
    if path.is_file():
        obj = _json(path)
        c = (obj.get("competitions") or {}).get(comp)
        if str(obj.get("season") or "") == season and type(c) is dict and type(c.get("teams")) is list:
            rows = []
            for r in c["teams"]:
                if type(r) is not dict:
                    continue
                canonical = str(r.get("canonical_name") or r.get("official_name") or "").strip()
                official = str(r.get("official_name") or "").strip()
                if canonical:
                    names = [canonical, official, *(r.get("aliases") or [])]
                    rows.append({"canonical_name": canonical, "provider_names": [official] if official else [], "aliases": names, "sources": [CURRENT_REGISTRY]})
            if rows:
                return _finalize(rows, [CURRENT_REGISTRY])

    current = _formal_current_season(comp)
    if season == current:
        # 2) Newer all-formal-domain current identity registry.
        full = repo_root / FULL17_REGISTRY
        aliases_path = repo_root / FULL17_ALIASES
        if full.is_file():
            obj = _json(full)
            c = (obj.get("competitions") or {}).get(comp)
            if type(c) is dict and c.get("status") == "PASS" and type(c.get("teams")) is list:
                amap: dict[str, str] = {}
                if aliases_path.is_file():
                    aobj = _json(aliases_path)
                    raw = (aobj.get("aliases") or {}).get(comp)
                    if type(raw) is dict:
                        amap = {str(k): str(v) for k, v in raw.items()}
                rows = []
                for r in c["teams"]:
                    if type(r) is not dict:
                        continue
                    canonical = str(r.get("canonical_name") or "").strip()
                    if not canonical:
                        continue
                    provider = [str(x).strip() for x in (r.get("provider_alias_tokens") or []) if str(x or "").strip()]
                    provider += [alias for alias, target in amap.items() if rt._normalize_team(target) == rt._normalize_team(canonical)]
                    rows.append({"canonical_name": canonical, "provider_names": provider, "aliases": [canonical, *provider], "sources": [FULL17_REGISTRY] + ([FULL17_ALIASES] if amap else [])})
                if rows:
                    return _finalize(rows, [FULL17_REGISTRY] + ([FULL17_ALIASES] if amap else []))

        # 3) Earlier active-domain observed registry.
        active = repo_root / ACTIVE_REGISTRY
        apath = repo_root / ACTIVE_ALIASES
        amap: dict[str, str] = {}
        if apath.is_file():
            aobj = _json(apath)
            raw = (aobj.get("aliases") or {}).get(comp)
            if type(raw) is dict:
                amap = {str(k): str(v) for k, v in raw.items()}
        if active.is_file():
            obj = _json(active)
            c = (obj.get("competitions") or {}).get(comp)
            if type(c) is dict and type(c.get("teams")) is list:
                rows = []
                for r in c["teams"]:
                    if type(r) is not dict:
                        continue
                    canonical = str(r.get("canonical_name") or "").strip()
                    if not canonical:
                        continue
                    names = [canonical, *(r.get("observed_variants") or []), *(r.get("provider_aliases") or [])]
                    names += [alias for alias, target in amap.items() if rt._normalize_team(target) == rt._normalize_team(canonical)]
                    rows.append({"canonical_name": canonical, "provider_names": list(r.get("provider_aliases") or []), "aliases": names, "sources": [ACTIVE_REGISTRY] + ([ACTIVE_ALIASES] if amap else [])})
                if rows:
                    return _finalize(rows, [ACTIVE_REGISTRY] + ([ACTIVE_ALIASES] if amap else []))

        # 4) Exact current-season processed roster, team-name columns only.
        roster = _processed_roster(repo_root, comp, season)
        if roster:
            amap = _alias_config(repo_root).get(comp, {})
            rows = []
            for canonical in sorted(roster):
                names = [canonical, *[str(alias) for alias, target in amap.items() if rt._normalize_team(str(target)) == rt._normalize_team(canonical)]]
                rows.append({"canonical_name": canonical, "provider_names": [], "aliases": names, "sources": ["processed_current_season_roster_team_names_only", "team_aliases.json"]})
            return _finalize(rows, ["processed_current_season_roster_team_names_only", "team_aliases.json"])
    return [], []


def _state_hit(state: Any, comp: str, team_id: str) -> tuple[bool, bool]:
    base = getattr(state, "base", None)
    return (comp, team_id) in getattr(base, "teams_local", {}), team_id in getattr(base, "teams_global", {})


def resolve_team(repo_root: Path, state: Any, comp: str, season: str, requested: str) -> dict[str, Any]:
    requested = str(requested or "").strip()
    if not requested:
        raise rt.RuntimeGateError("empty requested team identity")
    rows, sources = _registry_rows(repo_root, comp, season)
    token = rt._normalize_team(requested)
    matches = [r for r in rows if any(rt._normalize_team(x) == token for x in [r["canonical_name"], *r["provider_names"], *r["aliases"]])]
    if rows:
        if len(matches) != 1:
            raise rt.RuntimeGateError(f"permanent identity bridge not unique: {comp} {season} {requested}")
        row = matches[0]
        canonical = row["canonical_name"]
        candidates = []
        for x in [requested, *row["aliases"], *row["provider_names"], canonical]:
            x = str(x or "").strip()
            if x and x not in candidates:
                candidates.append(x)
        identity_sources = row["sources"]
    else:
        canonical = rt._canonical_team(comp, requested, _alias_config(repo_root))
        candidates = list(dict.fromkeys([requested, canonical]))
        identity_sources = ["team_aliases_or_exact_canonical_no_current_registry"]

    hits: dict[str, dict[str, Any]] = {}
    for alias in candidates:
        tid = rt._global_team_id(alias)
        local, global_ = _state_hit(state, comp, tid)
        if local or global_:
            h = hits.setdefault(tid, {"aliases": [], "local": False, "global": False})
            h["aliases"].append(alias); h["local"] |= local; h["global"] |= global_
    if len(hits) > 1:
        raise rt.RuntimeGateError(f"identity bridge maps one club to multiple frozen state ids: {comp} {requested}")
    if hits:
        strength_id, hit = next(iter(hits.items()))
        strength_alias = hit["aliases"][0]
        state_evidence = True
        resolution = "UNIQUE_EXISTING_STRENGTH_ID_FROM_EXPLICIT_CURRENT_SEASON_ALIASES" if len(candidates) > 1 else "UNIQUE_EXISTING_STRENGTH_ID_FROM_PERMANENT_BRIDGE"
    else:
        strength_alias = canonical
        strength_id = rt._global_team_id(canonical)
        state_evidence = False
        hit = {"local": False, "global": False}
        resolution = "REGISTERED_CURRENT_SEASON_CLUB_WITHOUT_HISTORICAL_STRENGTH_STATE" if rows else "EXACT_CANONICAL_WITHOUT_HISTORICAL_STRENGTH_STATE"

    names = list(dict.fromkeys([canonical, requested, *candidates]))
    previous = _previous_season(season)
    previous_roster = _processed_roster(repo_root, comp, previous)
    historical_seasons, historical_match_count, historical_by_season = _history_profile(repo_root, comp, names)
    tokens = {rt._normalize_team(x) for x in names}
    continuing = any(rt._normalize_team(x) in tokens for x in previous_roster)
    if continuing:
        relation = "CONTINUING_TOP_FLIGHT"
    elif state_evidence:
        relation = "PROMOTED_OR_RETURNING_WITH_FROZEN_HISTORY"
    else:
        relation = "PROMOTED_OR_NEW_TO_FORMAL_HISTORY"
    return {
        "schema_version": SCHEMA,
        "requested_name": requested,
        "provider_names": list(dict.fromkeys([requested, *[x for x in candidates if x != requested]])),
        "canonical_name": canonical,
        "canonical_team_id": strength_id,
        "strength_team_id": strength_id,
        "strength_alias": strength_alias,
        "aliases": candidates,
        "registry_match": bool(rows),
        "registry_sources": identity_sources or sources,
        "state_evidence": state_evidence,
        "state_local_evidence": bool(hit.get("local")),
        "state_global_evidence": bool(hit.get("global")),
        "resolution": resolution,
        "season_relation": relation,
        "previous_season": previous,
        "historical_seasons": historical_seasons,
        "historical_match_count": historical_match_count,
        "historical_matches_by_season": historical_by_season,
        "identity_selection_uses_result_or_xg": False,
        "fuzzy_cross_club_substitution": False,
    }


def resolve_fixture(repo_root: Path, state: Any, comp: str, season: str, home: str, away: str, kickoff) -> tuple[dict[str, Any], dict[str, Any]]:
    h = resolve_team(repo_root, state, comp, season, home)
    a = resolve_team(repo_root, state, comp, season, away)
    if h["strength_team_id"] == a["strength_team_id"]:
        raise rt.RuntimeGateError("resolved home/away strength identity collision")
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": h["strength_team_id"],
        "away_team_id": a["strength_team_id"],
        "home_team_name": home,
        "away_team_name": away,
    }
    audit = {
        "schema_version": SCHEMA,
        "competition_id": comp,
        "season": season,
        "fixture_id": fixture["fixture_id"],
        "home": h,
        "away": a,
        "identity_only": True,
        "result_or_xg_used_for_identity": False,
        "postponement_or_resumption_policy": "same competition+season+canonical team ids; kickoff may change but identity may not",
        "rename_policy": "explicit registered alias only; otherwise fail closed",
        "model_parameters_or_weights_changed": False,
    }
    return fixture, audit
