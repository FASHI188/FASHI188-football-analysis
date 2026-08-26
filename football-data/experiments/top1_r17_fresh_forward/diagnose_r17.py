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

try:
    r17.run()
except Exception as exc:
    p = Path(__file__).resolve().parent / "data" / "diagnostic_r17.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"exception_type": type(exc).__name__, "message": str(exc)}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    raise
