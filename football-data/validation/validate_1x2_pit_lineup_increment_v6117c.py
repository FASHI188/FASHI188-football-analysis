#!/usr/bin/env python3
"""V6.11.7c research-only source identity matcher for the PIT lineup test.

This changes ONLY historical data joining. It does not edit formal team_aliases.json.
For each competition+season, processed-market team labels are matched one-to-one to
historical-lineup team labels using deterministic string similarity, constrained to
that competition and season. The match model, feature construction, chronology,
train/validation/test split and thresholds remain exactly those in v6117.
"""
from __future__ import annotations

import difflib
from collections import defaultdict
from pathlib import Path
from typing import Any

import validate_1x2_pit_lineup_increment_v6117 as base
from platform_core import normalize_team_token

_original_load_matches = base._load_matches
_original_load_lineups = base._load_lineups


def _shorten(token: str) -> str:
    t = normalize_team_token(token)
    # Common legal/location words carried by one source but not the other.
    for frag in (
        "association", "sportive", "stadede", "stade", "olympique", "footballclub",
        "sportingclub", "athleticclub", "clubde", "realclub", "bayer04", "borussia",
        "eintracht", "racingclub", "girondinsde", "losc", "estac", "ogc", "rcd", "rc",
        "as", "ac", "ss", "ssc", "uc", "us", "tsg1899", "vfl", "1fsv", "1fc", "fsv",
    ):
        if t.startswith(frag) and len(t) - len(frag) >= 4:
            t = t[len(frag):]
    return t


def _sim(a: str, b: str) -> float:
    a0, b0 = normalize_team_token(a), normalize_team_token(b)
    if a0 == b0:
        return 1.0
    a1, b1 = _shorten(a0), _shorten(b0)
    if a1 == b1 and len(a1) >= 4:
        return 0.99
    score = difflib.SequenceMatcher(None, a0, b0).ratio()
    score2 = difflib.SequenceMatcher(None, a1, b1).ratio()
    score = max(score, score2)
    # Strong substring evidence for abbreviations like monaco/asmonaco or lille/losclille.
    if min(len(a0), len(b0)) >= 4 and (a0 in b0 or b0 in a0):
        score = max(score, 0.92)
    if min(len(a1), len(b1)) >= 4 and (a1 in b1 or b1 in a1):
        score = max(score, 0.94)
    return score


def _greedy_bijection(left: set[str], right: set[str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    # Exact first.
    mapping: dict[str, str] = {}
    unused_l, unused_r = set(left), set(right)
    for l in sorted(list(unused_l)):
        if l in unused_r:
            mapping[l] = l
            unused_l.remove(l); unused_r.remove(l)
    # Deterministic highest-similarity one-to-one assignment.
    candidates = []
    for l in unused_l:
        for r in unused_r:
            candidates.append((_sim(l, r), l, r))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    diagnostics = []
    for score, l, r in candidates:
        if l not in unused_l or r not in unused_r:
            continue
        # 0.44 is deliberately permissive because mapping is season/competition-constrained;
        # ambiguous low-score pairs are separately recorded for audit.
        if score < 0.44:
            continue
        mapping[l] = r
        unused_l.remove(l); unused_r.remove(r)
        diagnostics.append({"market_token": l, "lineup_token": r, "similarity": score})
    return mapping, diagnostics


def _load_all():
    matches = _original_load_matches()
    # Normalize market dates/tokens but preserve source-facing fields only inside research rows.
    for r in matches:
        r["date"] = str(r["date"])[:10]
        r["home"] = normalize_team_token(r["home"])
        r["away"] = normalize_team_token(r["away"])

    raw_lineups = {cid: _original_load_lineups(cid) for cid in base.COMPETITIONS}
    # Build mapping per competition+season using the full season team sets (identity join only).
    market_sets: dict[tuple[str,str], set[str]] = defaultdict(set)
    lineup_sets: dict[tuple[str,str], set[str]] = defaultdict(set)
    for r in matches:
        key = (r["competition_id"], r["season"])
        market_sets[key].update((r["home"], r["away"]))
    for cid, data in raw_lineups.items():
        for season, _, team in data:
            lineup_sets[(cid, season)].add(normalize_team_token(team))

    maps: dict[tuple[str,str], dict[str,str]] = {}
    audit = []
    for key, left in market_sets.items():
        mp, diag = _greedy_bijection(left, lineup_sets.get(key, set()))
        maps[key] = mp
        audit.append({
            "competition_id": key[0], "season": key[1],
            "market_team_count": len(left), "lineup_team_count": len(lineup_sets.get(key,set())),
            "mapped_count": len(mp),
            "unmapped_market": sorted(left - set(mp)),
            "unmapped_lineup": sorted(lineup_sets.get(key,set()) - set(mp.values())),
            "non_exact": diag,
        })

    fixed_lineups: dict[str, dict[tuple[str,str,str],dict[str,Any]]] = {}
    for cid, data in raw_lineups.items():
        out = {}
        for (season, date, team), item in data.items():
            lt = normalize_team_token(team)
            # Invert mapping so lineup label lands on market label.
            inv = {v:k for k,v in maps.get((cid,season),{}).items()}
            mt = inv.get(lt)
            if mt is not None:
                out[(season, date, mt)] = item
        fixed_lineups[cid] = out
    return matches, fixed_lineups, audit

_CACHE = None


def _ensure():
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_all()
    return _CACHE


def _load_matches_fixed():
    return [dict(r) for r in _ensure()[0]]


def _load_lineups_fixed(cid: str):
    return dict(_ensure()[1].get(cid, {}))

base._load_matches = _load_matches_fixed
base._load_lineups = _load_lineups_fixed
base.OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_increment_v6117c_status.json"

_original_main = base.main

def main():
    code = _original_main()
    # Append identity-mapping diagnostics to the successful receipt.
    import json
    payload = json.loads(base.OUT.read_text(encoding="utf-8"))
    payload["identity_join"] = {
        "scope": "RESEARCH_ONLY_COMPETITION_SEASON_BIJECTION",
        "formal_alias_config_changed": False,
        "audits": _ensure()[2],
    }
    base.OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
