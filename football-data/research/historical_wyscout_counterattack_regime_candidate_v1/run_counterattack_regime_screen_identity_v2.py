from __future__ import annotations

import collections
import difflib
import importlib.util
import pathlib
import re
import unicodedata
from datetime import date

BASE_PATH = pathlib.Path(__file__).with_name("run_counterattack_regime_screen.py")
spec = importlib.util.spec_from_file_location("counterattack_screen_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen counterattack screening runner")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

EXPECTED_TEAMS = {"EPL": 20, "La liga": 20, "Serie A": 20, "Bundesliga": 18, "Ligue 1": 20}


def canonical_name(value: str) -> str:
    x = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    x = re.sub(r"[^a-z0-9]+", "", x)
    aliases = {
        "internazionale": "inter",
        "internazionalemilano": "inter",
        "intermilan": "inter",
        "1fckoln": "cologne",
        "fckoln": "cologne",
        "fccologne": "cologne",
        "koln": "cologne",
        "cologne": "cologne",
        "vfbstuttgart": "stuttgart",
        "stuttgart": "stuttgart",
        "bayernmunchen": "bayernmunich",
        "rasenballsportleipzig": "rbleipzig",
        "parissaintgermain": "psg",
        "olympiquemarseille": "marseille",
        "olympiquelyonnais": "lyon",
        "asmonaco": "monaco",
        "losclille": "lille",
        "fcgirondinsdebordeaux": "bordeaux",
        "staderennais": "rennes",
        "enavantguingamp": "guingamp",
        "estactroyes": "troyes",
        "smcaen": "caen",
        "fcmetz": "metz",
        "toulousefc": "toulouse",
        "scoangers": "angers",
        "brightonhovealbion": "brighton",
        "huddersfieldtown": "huddersfield",
        "westbromwichalbion": "westbromwich",
        "afcbournemouth": "bournemouth",
        "athleticclub": "athleticbilbao",
        "deportivoalaves": "alaves",
        "alaves": "alaves",
        "deportivolacoruna": "deportivolacoruna",
        "realclubdeportivodelacoruna": "deportivolacoruna",
        "celtadevigo": "celtavigo",
        "celtavigo": "celtavigo",
        "spal2013": "spal",
        "hellasverona": "verona",
    }
    return aliases.get(x, x)


def team_similarity(a: str, b: str) -> float:
    aa, bb = canonical_name(a), canonical_name(b)
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.75 + 0.25 * min(len(aa), len(bb)) / max(len(aa), len(bb))
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def maximum_weight_assignment(scores: list[list[float]]) -> list[int]:
    """Deterministic Hungarian assignment; returns assigned column per row."""
    n = len(scores)
    if n == 0 or any(len(row) != n for row in scores):
        raise RuntimeError("team assignment matrix must be non-empty and square")
    max_score = max(max(row) for row in scores)
    cost = [[max_score - float(v) for v in row] for row in scores]
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, n + 1):
        if p[j] != 0:
            assignment[p[j] - 1] = j - 1
    if any(j < 0 for j in assignment) or len(set(assignment)) != n:
        raise RuntimeError(f"invalid Hungarian team assignment: {assignment}")
    return assignment


def map_identity(wys: list[dict], under_target: list[dict]) -> tuple[dict[int, dict], dict]:
    # Dates are audit-only because postponed fixtures can carry different schedule/played dates.
    # Outcomes are never used. Resolve a league-wide one-to-one team alias map first, then map
    # each fixture by league + mapped ordered home/away team identity.
    wys_names = collections.defaultdict(set)
    under_names = collections.defaultdict(set)
    for w in wys:
        wys_names[w["league"]].update((w["home_name"], w["away_name"]))
    for u in under_target:
        under_names[u["league"]].update((u["team_h"], u["team_a"]))

    team_map: dict[tuple[str, str], str] = {}
    team_rows = []
    min_team_score = 1.0
    min_team_margin = 1.0
    for league, expected_n in EXPECTED_TEAMS.items():
        wn = sorted(wys_names[league], key=lambda x: (canonical_name(x), x))
        un = sorted(under_names[league], key=lambda x: (canonical_name(x), x))
        if len(wn) != expected_n or len(un) != expected_n:
            raise RuntimeError(f"team inventory drift {league}: wys={len(wn)} under={len(un)} expected={expected_n}")
        matrix = [[team_similarity(w, u) for u in un] for w in wn]
        assignment = maximum_weight_assignment(matrix)
        for i, j in enumerate(assignment):
            assigned = float(matrix[i][j])
            alternatives = sorted((float(matrix[i][k]) for k in range(len(un)) if k != j), reverse=True)
            runner_up = alternatives[0] if alternatives else -1.0
            margin = assigned - runner_up
            if assigned < 0.62:
                raise RuntimeError(
                    f"team alias confidence too low {league}: {wn[i]} -> {un[j]} score={assigned:.6f} runner_up={runner_up:.6f}"
                )
            team_map[(league, wn[i])] = un[j]
            min_team_score = min(min_team_score, assigned)
            min_team_margin = min(min_team_margin, margin)
            team_rows.append({
                "league": league,
                "wys_team": wn[i],
                "under_team": un[j],
                "wys_canonical": canonical_name(wn[i]),
                "under_canonical": canonical_name(un[j]),
                "score": assigned,
                "runner_up_score": runner_up,
                "local_margin": margin,
                "canonical_exact": canonical_name(wn[i]) == canonical_name(un[j]),
            })
        mapped = [team_map[(league, w)] for w in wn]
        if len(set(mapped)) != expected_n:
            raise RuntimeError(f"team map is not bijective for {league}")

    fixture_index = collections.defaultdict(list)
    for u in under_target:
        fixture_index[(u["league"], u["team_h"], u["team_a"])].append(u)
    duplicates = [k for k, rows in fixture_index.items() if len(rows) != 1]
    if duplicates:
        raise RuntimeError(f"Understat directed fixture identity not unique: {duplicates[:10]}")

    used = set()
    out = {}
    evidence = []
    for w in sorted(wys, key=lambda z: (z["date"], z["source_file"], z["wys_match_id"])):
        mapped_home = team_map[(w["league"], w["home_name"])]
        mapped_away = team_map[(w["league"], w["away_name"])]
        matches = fixture_index.get((w["league"], mapped_home, mapped_away), [])
        if len(matches) != 1:
            raise RuntimeError(
                f"directed fixture map failed {w['wys_match_id']} {w['league']} {w['home_name']}-{w['away_name']} -> {mapped_home}-{mapped_away} n={len(matches)}"
            )
        u = matches[0]
        uid = int(u["id"])
        if uid in used:
            raise RuntimeError(f"Understat fixture reused: {uid}")
        wd = date.fromisoformat(w["date"])
        ud = date.fromisoformat(str(u["date"])[:10])
        dd = (ud - wd).days
        used.add(uid)
        out[w["wys_match_id"]] = u
        hs = team_similarity(w["home_name"], u["team_h"])
        as_ = team_similarity(w["away_name"], u["team_a"])
        evidence.append({
            "wys_match_id": w["wys_match_id"],
            "understat_id": uid,
            "understat_fid": int(u["fid"]),
            "league": w["league"],
            "wys_date": w["date"],
            "under_date": str(u["date"])[:10],
            "under_minus_wys_days": dd,
            "wys_home": w["home_name"],
            "wys_away": w["away_name"],
            "under_home": u["team_h"],
            "under_away": u["team_a"],
            "name_score": hs + as_,
            "runner_up_margin": None,
        })

    if len(out) != 1826 or len(used) != 1826:
        raise RuntimeError(f"identity mapping incomplete: map={len(out)} used={len(used)}")
    delta_counts = collections.Counter(int(x["under_minus_wys_days"]) for x in evidence)
    max_abs = max(abs(int(x["under_minus_wys_days"])) for x in evidence)
    return out, {
        "mapped_n": len(out),
        "team_mapping_n": len(team_rows),
        "team_mapping_bijective": True,
        "min_name_score": min(float(x["name_score"]) for x in evidence),
        "min_runner_up_margin": min_team_margin,
        "min_team_assignment_score": min_team_score,
        "date_delta_days_counts": dict(sorted(delta_counts.items())),
        "max_abs_date_delta_days": max_abs,
        "mapping_keys": ["league", "bijective_team_alias", "ordered_home_team", "ordered_away_team"],
        "date_used_as_hard_key": False,
        "mapping_used_outcome": False,
        "team_mapping_used_outcome": False,
        "team_alias_rows": team_rows,
        "rows": evidence,
    }


# Patch identity mechanics only. All feature construction, model parameters, folds and gates
# continue to execute from the frozen screening runner unchanged.
base.norm_name = canonical_name
base.name_sim = team_similarity
base.map_identity = map_identity

if __name__ == "__main__":
    raise SystemExit(base.main())
