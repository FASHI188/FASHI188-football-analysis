#!/usr/bin/env python3
import json
from pathlib import Path
import run_experiment_r17 as r17


def exact_league_id(leagues, key):
    spec = {
        "EPL": ("england", "premier league"),
        "La_liga": ("spain", "la liga"),
        "Bundesliga": ("germany", "bundesliga"),
        "Serie_A": ("italy", "serie a"),
        "Ligue_1": ("france", "ligue 1"),
        "RFPL": ("russia", "premier league"),
    }[key]
    hits = []
    for row in leagues.itertuples(index=False):
        d = row._asdict()
        country = str(d.get("country") or "").strip().lower()
        name = str(d.get("name") or "").strip().lower()
        if country == spec[0] and name == spec[1]:
            hits.append(str(int(d["id"])))
    if len(set(hits)) != 1:
        raise RuntimeError(f"exact league map failed {key}: {hits}")
    return hits[0]


r17.league_id = exact_league_id
_original_team_index = r17.team_index
_team_index_calls = 0


def metadata_team_index(teams, allowed):
    global _team_index_calls
    _team_index_calls += 1
    # The first call builds the fallback. Use the full metadata table so newly promoted/newly observed clubs can cold-start.
    if _team_index_calls == 1:
        return _original_team_index(teams, set())
    return _original_team_index(teams, allowed)


r17.team_index = metadata_team_index
_original_resolve = r17.resolve_team
_unresolved = []
ALIASES = {
    "Manchester City": "Man City",
    "Atletico Madrid": "Ath Madrid",
    "Real Betis": "Betis",
    "Celta Vigo": "Celta",
    "Parma Calcio 1913": "Parma",
    "Paris Saint Germain": "Paris SG",
    "Zenit St. Petersburg": "Zenit",
    "FK Akhmat": "Akhmat Grozny",
}


def audited_resolve(title, primary, fallback):
    query = ALIASES.get(title, title)
    tid, candidates = _original_resolve(query, primary, fallback)
    if tid is None:
        _unresolved.append({"title": title, "query": query, "candidates": candidates})
    return tid, candidates


r17.resolve_team = audited_resolve

try:
    r17.run()
except Exception as exc:
    p = Path(__file__).resolve().parent / "data" / "diagnostic_r17.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    unique = {}
    for x in _unresolved:
        unique[x["title"]] = x
    p.write_text(json.dumps({"exception_type": type(exc).__name__, "message": str(exc), "unresolved": list(unique.values())}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    raise
