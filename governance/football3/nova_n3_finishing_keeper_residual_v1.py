from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

FORMAL_V2_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
FORMAL_V2_WEIGHT = 0.75
EXPECTED = {2020: 1826, 2021: 1826}
LEAGUES = {
    "EPL": {"slug": "EPL", "expected": 380},
    "Bundesliga": {"slug": "Bundesliga", "expected": 306},
    "La_liga": {"slug": "La_liga", "expected": 380},
    "Ligue_1": {"slug": "Ligue_1", "expected": 380},
    "Serie_A": {"slug": "Serie_A", "expected": 380},
}
LEAGUE_CANON = {
    "EPL": "EPL",
    "Bundesliga": "Bundesliga",
    "La_liga": "La_liga",
    "La liga": "La_liga",
    "Ligue_1": "Ligue_1",
    "Ligue 1": "Ligue_1",
    "Serie_A": "Serie_A",
    "Serie A": "Serie_A",
}
ROUTES = {
    "R1_W5": ("w", 5, 0.25),
    "R2_W10": ("w", 10, 0.25),
    "R3_EWMA035": ("ewma", 0.35, 0.25),
    "R4_W5_W10_STACK": ("stack", None, 0.10),
}
RELEASE_DELAY_HOURS = 3
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 730301


class N3Error(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise N3Error(message)


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_dt(value: Any, field: str = "datetime") -> datetime:
    text = str(value or "").strip().replace("T", " ").removesuffix("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise N3Error(f"{field}:UNSUPPORTED_DATETIME:{value!r}") from exc


def finite(value: Any, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise N3Error(f"{field}:BOOLEAN_NOT_NUMERIC")
    try:
        result = float(value)
    except Exception as exc:
        raise N3Error(f"{field}:NOT_NUMERIC") from exc
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise N3Error(f"{field}:INVALID")
    return result


def truthy(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes"}


def team_id(value: Any, field: str) -> str:
    raw = str((value or {}).get("id") or "").strip()
    require(raw.isdigit(), f"{field}:INVALID_TEAM_ID")
    return raw


def team_name(value: Any, field: str) -> str:
    title = str((value or {}).get("title") or "").strip()
    require(bool(title), f"{field}:MISSING_TEAM_NAME")
    return title


def fetch_json(url: str, referer: str, tries: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Football3-Nova-N3-Research/1.0; noncommercial-research)",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }
    last: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                wire = response.read()
                encoding = str(response.headers.get("Content-Encoding") or "").lower()
            body = gzip.decompress(wire) if encoding == "gzip" or wire[:2] == b"\x1f\x8b" else wire
            payload = json.loads(body.decode("utf-8"))
            require(isinstance(payload, dict), "PAYLOAD_ROOT_NOT_OBJECT")
            return payload, {"sha256": sha256_bytes(body), "bytes": len(body)}
        except Exception as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(2 * (attempt + 1))
    raise N3Error(f"FETCH_FAILED:{url}:{last}")


def history_state(raw: Any, team: str) -> dict[str, Any]:
    require(isinstance(raw, Mapping), f"team:{team}:HISTORY_ROW_NOT_OBJECT")
    ha = str(raw.get("h_a") or "").strip().lower()
    require(ha in {"h", "a"}, f"team:{team}.h_a:INVALID")
    npxg = finite(raw.get("npxG"), f"team:{team}.npxG", nonnegative=True)
    npxga = finite(raw.get("npxGA"), f"team:{team}.npxGA", nonnegative=True)
    scored = finite(raw.get("scored"), f"team:{team}.scored", nonnegative=True)
    missed = finite(raw.get("missed"), f"team:{team}.missed", nonnegative=True)
    return {
        "kickoff": parse_dt(raw.get("date"), f"team:{team}.date"),
        "h_a": ha,
        "npxg": npxg,
        "npxga": npxga,
        "scored": scored,
        "missed": missed,
        "finishing_residual": scored - npxg,
        "keeper_residual": npxga - missed,
    }


def close(a: float, b: float, tol: float = 1e-8) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


def validate_reciprocal(home: Mapping[str, Any], away: Mapping[str, Any]) -> None:
    require(close(home["npxga"], away["npxg"]), "RECIPROCAL_NPXG_MISMATCH_HOME")
    require(close(away["npxga"], home["npxg"]), "RECIPROCAL_NPXG_MISMATCH_AWAY")
    require(close(home["scored"], away["missed"]), "RECIPROCAL_SCORE_MISMATCH_HOME")
    require(close(away["scored"], home["missed"]), "RECIPROCAL_SCORE_MISMATCH_AWAY")


def project_payload(payload: Mapping[str, Any], league: str, season: int, expected: int) -> list[dict[str, Any]]:
    dates, teams = payload.get("dates"), payload.get("teams")
    require(isinstance(dates, list), f"{league}|{season}:DATES_NOT_LIST")
    require(isinstance(teams, Mapping), f"{league}|{season}:TEAMS_NOT_OBJECT")
    history: dict[tuple[str, datetime], dict[str, Any]] = {}
    for raw_id, raw_team in teams.items():
        tid = str(raw_id).strip()
        require(tid.isdigit() and isinstance(raw_team, Mapping), f"{league}|{season}:BAD_TEAM_RECORD")
        rows = raw_team.get("history")
        require(isinstance(rows, list), f"{league}|{season}:TEAM_HISTORY_MISSING:{tid}")
        for raw in rows:
            state = history_state(raw, tid)
            key = (tid, state["kickoff"])
            require(key not in history, f"{league}|{season}:DUPLICATE_HISTORY_KEY")
            history[key] = state

    completed = [row for row in dates if isinstance(row, Mapping) and truthy(row.get("isResult"))]
    require(len(completed) == expected, f"{league}|{season}:COMPLETED_COUNT:{len(completed)}!={expected}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    used: set[tuple[str, datetime]] = set()
    for fixture in completed:
        mid = str(fixture.get("id") or "").strip()
        require(mid.isdigit(), f"{league}|{season}:BAD_MATCH_ID")
        fixture_id = f"understat:{mid}"
        kickoff = parse_dt(fixture.get("datetime"), f"{fixture_id}.datetime")
        hid = team_id(fixture.get("h"), f"{fixture_id}.home")
        aid = team_id(fixture.get("a"), f"{fixture_id}.away")
        hkey, akey = (hid, kickoff), (aid, kickoff)
        home, away = history.get(hkey), history.get(akey)
        require(fixture_id not in seen, f"{league}|{season}:DUPLICATE_FIXTURE:{fixture_id}")
        seen.add(fixture_id)
        require(home is not None and away is not None, f"{fixture_id}:HISTORY_JOIN_MISSING")
        require(home["h_a"] == "h" and away["h_a"] == "a", f"{fixture_id}:H_A_MISMATCH")
        validate_reciprocal(home, away)
        require(hkey not in used and akey not in used, f"{fixture_id}:HISTORY_ROW_REUSED")
        used.update((hkey, akey))
        release_at = kickoff + timedelta(hours=RELEASE_DELAY_HOURS)
        out.append({
            "fixture_id": fixture_id,
            "league": league,
            "season_start": season,
            "kickoff": kickoff.isoformat().replace("+00:00", "Z"),
            "release_at": release_at.isoformat().replace("+00:00", "Z"),
            "home_team_id": f"understat-team:{hid}",
            "away_team_id": f"understat-team:{aid}",
            "home_team_name": team_name(fixture.get("h"), f"{fixture_id}.home"),
            "away_team_name": team_name(fixture.get("a"), f"{fixture_id}.away"),
            "home_finishing_residual": float(home["finishing_residual"]),
            "home_keeper_residual": float(home["keeper_residual"]),
            "away_finishing_residual": float(away["finishing_residual"]),
            "away_keeper_residual": float(away["keeper_residual"]),
        })
    out.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canon(row).decode("utf-8") + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                require(isinstance(row, dict), f"ROW_NOT_OBJECT:{path}")
                rows.append(row)
    return rows


def source_precheck(prereg_path: Path, season: int, out_dir: Path) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    require(prereg.get("status") == "PRECHECK_LOCKED_NO_LABEL_FIT", "PREREG_STATUS")
    require(season in EXPECTED, f"BAD_SEASON:{season}")
    rows: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for league, spec in LEAGUES.items():
        slug = spec["slug"]
        url = f"https://understat.com/getLeagueData/{slug}/{season}"
        payload, meta = fetch_json(url, f"https://understat.com/league/{slug}/{season}")
        part = project_payload(payload, league, season, int(spec["expected"]))
        sources[league] = {"url": url, "payload_sha256": meta["sha256"], "payload_bytes": meta["bytes"], "match_count": len(part)}
        rows.extend(part)
    rows.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    require(len(rows) == EXPECTED[season], f"TOTAL_COUNT:{len(rows)}!={EXPECTED[season]}")
    ids = [row["fixture_id"] for row in rows]
    require(len(ids) == len(set(ids)), "GLOBAL_FIXTURE_DUPLICATE")
    out_dir.mkdir(parents=True, exist_ok=True)
    projection = out_dir / f"source_projection_{season}.jsonl"
    write_jsonl(projection, rows)
    receipt = {
        "schema_version": "football3-nova-n3-finishing-keeper-source-v1",
        "status": "N3_SOURCE_PRECHECK_PASS",
        "season": season,
        "match_count": len(rows),
        "fixture_id_unique_count": len(set(ids)),
        "fixture_identity_sha256": sha256_bytes(canon(ids)),
        "state_projection_sha256": sha256_file(projection),
        "source_payloads": sources,
        "release_delay_hours": RELEASE_DELAY_HOURS,
        "same_kickoff_atomic_required": True,
        "feature_semantics": {
            "finishing_residual": "prior released match goals_scored minus npxG",
            "keeper_residual": "prior released match npxGA minus goals_conceded",
        },
        "current_match_feature_use": False,
        "future_feature_use": False,
        "market_feature_use": False,
        "target_label_extracted": False,
        "training_performed": False,
        "candidate_probabilities_generated": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    (out_dir / f"source_receipt_{season}.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def normalize_kickoff(value: Any) -> str:
    return str(value).replace("Z", "+00:00")


def baseline_join_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    league = LEAGUE_CANON.get(str(row.get("league")))
    require(league is not None, f"UNKNOWN_LEAGUE:{row.get('league')}")
    return (normalize_kickoff(row["kickoff"]), str(row["home_team_id"]), str(row["away_team_id"]), league)


def mix_formal(v1: Mapping[str, Any], xg: Mapping[str, Any]) -> dict[str, float]:
    keys = ("p_home", "p_draw", "p_away")
    fallback = bool((xg.get("dynamic") or {}).get("fallback_exact_v1", False))
    if fallback:
        for key in keys:
            require(abs(float(v1[key]) - float(xg[key])) <= 1e-15, f"FALLBACK_NOT_EXACT:{key}")
        return {key: float(v1[key]) for key in keys}
    raw = {key: (1.0 - FORMAL_V2_WEIGHT) * float(v1[key]) + FORMAL_V2_WEIGHT * float(xg[key]) for key in keys}
    total = sum(raw.values())
    require(total > 0.0, "BAD_NORMALIZER")
    return {key: raw[key] / total for key in keys}


def baseline_seal(prereg_path: Path, v2_final_path: Path, v2_dev_path: Path, xg_path: Path, out_dir: Path) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    require(prereg.get("formal_v2", {}).get("head") == FORMAL_V2_HEAD, "FORMAL_HEAD_DRIFT")
    v2_final = json.loads(v2_final_path.read_text(encoding="utf-8"))
    require(float(v2_final.get("selected_weight")) == FORMAL_V2_WEIGHT, "FORMAL_WEIGHT_DRIFT")
    require(v2_final.get("development_pass") is True and v2_final.get("confirmation_pass") is True, "FORMAL_V2_NOT_FROZEN_PASS")
    xg_rows = read_jsonl(xg_path)
    v2_rows = read_jsonl(v2_dev_path)

    xg_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in xg_rows:
        if int(row["season"]) not in {2020, 2021, 2022}:
            continue
        key = baseline_join_key(row)
        require(key not in xg_by_key, f"XG_AMBIGUOUS:{key}")
        xg_by_key[key] = row
    v2_2022: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in v2_rows:
        if int(row["season"]) != 2022:
            continue
        key = baseline_join_key(row)
        require(key not in v2_2022, f"V2_AMBIGUOUS:{key}")
        v2_2022[key] = row
    require(len(v2_2022) == 1826, f"V2_2022_COUNT:{len(v2_2022)}")

    max_repro = 0.0
    for key, row in v2_2022.items():
        parent = xg_by_key.get(key)
        require(parent is not None, f"FORMAL_PARENT_MISSING_2022:{key}")
        recomputed = mix_formal(parent["v1"], parent["challenger"])
        frozen = row["fusion"]
        for field in ("p_home", "p_draw", "p_away"):
            max_repro = max(max_repro, abs(float(frozen[field]) - recomputed[field]))
    require(max_repro <= 1e-12, f"FORMAL_2022_REPRO_DRIFT:{max_repro}")

    out_dir.mkdir(parents=True, exist_ok=True)
    shas: dict[str, str] = {}
    counts: dict[str, int] = {}
    for season in (2020, 2021):
        selected = [row for row in xg_rows if int(row["season"]) == season]
        require(len(selected) == EXPECTED[season], f"XG_COUNT:{season}:{len(selected)}")
        output: list[dict[str, Any]] = []
        for row in sorted(selected, key=lambda item: (normalize_kickoff(item["kickoff"]), item["fixture_id"])):
            probabilities = mix_formal(row["v1"], row["challenger"])
            output.append({
                "n3_fixture_id": str(row["fixture_id"]),
                "formal_fixture_id": str(row["fixture_id"]),
                "league": LEAGUE_CANON[str(row["league"])],
                "season_start": season,
                "kickoff": normalize_kickoff(row["kickoff"]),
                "home_team_id": str(row["home_team_id"]),
                "away_team_id": str(row["away_team_id"]),
                "formal_v2_1x2": [probabilities["p_home"], probabilities["p_draw"], probabilities["p_away"]],
                "fallback_exact_v1": bool((row["challenger"].get("dynamic") or {}).get("fallback_exact_v1", False)),
                "model_head": FORMAL_V2_HEAD,
                "target_label_read": False,
            })
        path = out_dir / f"formal_v2_baseline_{season}_label_free.jsonl"
        write_jsonl(path, output)
        shas[str(season)] = sha256_file(path)
        counts[str(season)] = len(output)

    receipt = {
        "schema_version": "football3-nova-n3-formal-v2-baseline-seal-v1",
        "status": "N3_FORMAL_V2_BASELINE_LABEL_FREE_SEAL_PASS",
        "formal_v2_head": FORMAL_V2_HEAD,
        "formal_v2_weight": FORMAL_V2_WEIGHT,
        "development_season": 2020,
        "isolated_season": 2021,
        "counts": counts,
        "prediction_sha256": shas,
        "max_abs_2022_formal_reproduction_diff": max_repro,
        "result_labels_read": 0,
        "score_values_read": 0,
        "isolated_labels_read": 0,
        "training_performed": False,
        "candidate_probabilities_generated": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    (out_dir / "baseline_seal_receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def extract_labels_from_payload(payload: Mapping[str, Any], league: str, season: int, expected: int) -> list[dict[str, Any]]:
    dates = payload.get("dates")
    require(isinstance(dates, list), f"{league}|{season}:DATES_NOT_LIST")
    completed = [row for row in dates if isinstance(row, Mapping) and truthy(row.get("isResult"))]
    require(len(completed) == expected, f"{league}|{season}:COMPLETED_COUNT:{len(completed)}!={expected}")
    labels: list[dict[str, Any]] = []
    for fixture in completed:
        mid = str(fixture.get("id") or "").strip()
        require(mid.isdigit(), f"{league}|{season}:BAD_MATCH_ID")
        goals = fixture.get("goals")
        require(isinstance(goals, Mapping), f"understat:{mid}:GOALS_NOT_OBJECT")
        home = finite(goals.get("h"), f"understat:{mid}.goals.h", nonnegative=True)
        away = finite(goals.get("a"), f"understat:{mid}.goals.a", nonnegative=True)
        require(abs(home - round(home)) <= 1e-9 and abs(away - round(away)) <= 1e-9, f"understat:{mid}:NONINTEGER_GOALS")
        outcome = "home" if home > away else "away" if away > home else "draw"
        labels.append({"fixture_id": f"understat:{mid}", "league": league, "season_start": season, "outcome": outcome})
    return labels


def label_vault(prereg_path: Path, season: int, out_dir: Path) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    require(prereg.get("status") == "PRECHECK_LOCKED_NO_LABEL_FIT", "PREREG_STATUS")
    require(season in EXPECTED, f"BAD_SEASON:{season}")
    labels: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for league, spec in LEAGUES.items():
        slug = spec["slug"]
        url = f"https://understat.com/getLeagueData/{slug}/{season}"
        payload, meta = fetch_json(url, f"https://understat.com/league/{slug}/{season}")
        labels.extend(extract_labels_from_payload(payload, league, season, int(spec["expected"])))
        source_hashes[league] = meta["sha256"]
    labels.sort(key=lambda row: row["fixture_id"])
    require(len(labels) == EXPECTED[season], f"LABEL_COUNT:{season}:{len(labels)}")
    ids = [row["fixture_id"] for row in labels]
    require(len(ids) == len(set(ids)), "LABEL_DUPLICATE")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"labels_{season}.jsonl"
    write_jsonl(path, labels)
    receipt = {
        "schema_version": "football3-nova-n3-label-vault-v1",
        "status": "N3_LABEL_VAULT_OPENED",
        "season": season,
        "label_count": len(labels),
        "label_sha256": sha256_file(path),
        "source_payload_sha256": source_hashes,
        "purpose": "DEVELOPMENT_ONLY" if season == 2020 else "ISOLATED_OPEN_ONCE_AFTER_ROUTE_FREEZE",
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    (out_dir / f"label_receipt_{season}.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def kickoff_groups(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end]["kickoff"] == rows[start]["kickoff"]:
            end += 1
        groups.append((start, end))
        start = end
    return groups


def team_state(history: list[tuple[float, float]], kind: str, parameter: float | int) -> tuple[float | None, float | None, int]:
    if not history:
        return None, None, 0
    if kind == "w":
        sample = history[-int(parameter):]
        return sum(row[0] for row in sample) / len(sample), sum(row[1] for row in sample) / len(sample), len(history)
    if kind == "ewma":
        alpha = float(parameter)
        finishing, keeper = history[0]
        for current_finishing, current_keeper in history[1:]:
            finishing = alpha * current_finishing + (1.0 - alpha) * finishing
            keeper = alpha * current_keeper + (1.0 - alpha) * keeper
        return finishing, keeper, len(history)
    raise N3Error(f"UNKNOWN_STATE:{kind}")


def build_raw_features(rows: list[dict[str, Any]], route: str) -> list[list[float | None]]:
    kind, parameter, _ridge = ROUTES[route]
    histories: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pending: deque[dict[str, Any]] = deque()
    output: list[list[float | None] | None] = [None] * len(rows)
    for start, end in kickoff_groups(rows):
        target = parse_dt(rows[start]["kickoff"], "kickoff")
        while pending and parse_dt(pending[0]["release_at"], "release_at") <= target:
            released = pending.popleft()
            histories[released["home_team_id"]].append((float(released["home_finishing_residual"]), float(released["home_keeper_residual"])))
            histories[released["away_team_id"]].append((float(released["away_finishing_residual"]), float(released["away_keeper_residual"])))
        for index in range(start, end):
            row = rows[index]
            home_history = histories[row["home_team_id"]]
            away_history = histories[row["away_team_id"]]
            if kind in {"w", "ewma"}:
                hf, hk, hn = team_state(home_history, kind, parameter)
                af, ak, an = team_state(away_history, kind, parameter)
                output[index] = [hf, hk, af, ak, math.log1p(hn), math.log1p(an)]
            else:
                hf5, hk5, hn = team_state(home_history, "w", 5)
                af5, ak5, an = team_state(away_history, "w", 5)
                hf10, hk10, _ = team_state(home_history, "w", 10)
                af10, ak10, _ = team_state(away_history, "w", 10)
                output[index] = [hf5, hk5, af5, ak5, hf10, hk10, af10, ak10, math.log1p(hn), math.log1p(an)]
        for index in range(start, end):
            pending.append(rows[index])
    require(all(row is not None for row in output), "FEATURE_ROW_MISSING")
    return [list(row) for row in output if row is not None]


def fit_scaler(raw: list[list[float | None]], indices: list[int]) -> tuple[list[float], list[float]]:
    means: list[float] = []
    stds: list[float] = []
    for column in range(len(raw[0])):
        values = [float(raw[index][column]) for index in indices if raw[index][column] is not None and math.isfinite(float(raw[index][column]))]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        std = math.sqrt(variance)
        means.append(mean)
        stds.append(std if std >= 1e-9 else 1.0)
    return means, stds


def transform(row: list[float | None], means: list[float], stds: list[float]) -> list[float]:
    return [((means[i] if value is None else float(value)) - means[i]) / stds[i] for i, value in enumerate(row)]


def softmax_offset(base: list[float], features: list[float], beta: list[list[float]]) -> list[float]:
    eps = 1e-15
    vector = [1.0] + features
    home_logit = math.log(max(base[0], eps) / max(base[2], eps)) + sum(c * vector[i] for i, c in enumerate(beta[0]))
    draw_logit = math.log(max(base[1], eps) / max(base[2], eps)) + sum(c * vector[i] for i, c in enumerate(beta[1]))
    maximum = max(home_logit, draw_logit, 0.0)
    home = math.exp(home_logit - maximum)
    draw = math.exp(draw_logit - maximum)
    away = math.exp(-maximum)
    total = home + draw + away
    return [home / total, draw / total, away / total]


def loss_gradient(beta: list[list[float]], features: list[list[float]], bases: list[list[float]], outcomes: list[int], ridge_c: float) -> tuple[float, list[list[float]]]:
    n = len(features)
    dimensions = len(beta[0])
    gradient = [[0.0] * dimensions, [0.0] * dimensions]
    loss = 0.0
    for row, base, outcome in zip(features, bases, outcomes):
        probabilities = softmax_offset(base, row, beta)
        loss -= math.log(max(probabilities[outcome], 1e-15))
        vector = [1.0] + row
        for klass in (0, 1):
            error = probabilities[klass] - (1.0 if outcome == klass else 0.0)
            for index, value in enumerate(vector):
                gradient[klass][index] += error * value
    loss /= n
    gradient = [[value / n for value in row] for row in gradient]
    regularization = 1.0 / (ridge_c * n)
    for klass in (0, 1):
        for index in range(1, dimensions):
            loss += 0.5 * regularization * beta[klass][index] ** 2
            gradient[klass][index] += regularization * beta[klass][index]
    return loss, gradient


def fit_model(features: list[list[float]], bases: list[list[float]], outcomes: list[int], ridge_c: float) -> list[list[float]]:
    dimensions = len(features[0]) + 1
    beta = [[0.0] * dimensions, [0.0] * dimensions]
    loss, gradient = loss_gradient(beta, features, bases, outcomes, ridge_c)
    for _ in range(300):
        norm_squared = sum(value * value for row in gradient for value in row)
        if norm_squared < 1e-12:
            break
        step = 1.0
        accepted = False
        while step > 1e-8:
            candidate = [[beta[k][i] - step * gradient[k][i] for i in range(dimensions)] for k in (0, 1)]
            candidate_loss, candidate_gradient = loss_gradient(candidate, features, bases, outcomes, ridge_c)
            if candidate_loss <= loss - 1e-4 * step * norm_squared:
                beta, loss, gradient = candidate, candidate_loss, candidate_gradient
                accepted = True
                break
            step *= 0.5
        if not accepted or max(abs(value) for row in gradient for value in row) < 1e-6:
            break
    return beta


def outcome_index(value: str) -> int:
    mapping = {"home": 0, "draw": 1, "away": 2}
    require(value in mapping, f"BAD_OUTCOME:{value}")
    return mapping[value]


def metrics(probabilities: list[list[float]], outcomes: list[int]) -> dict[str, float | int]:
    n = len(outcomes)
    require(n > 0, "METRICS_EMPTY")
    logloss = sum(-math.log(max(probability[outcome], 1e-15)) for probability, outcome in zip(probabilities, outcomes)) / n
    brier = sum(sum((probability[i] - (1.0 if outcome == i else 0.0)) ** 2 for i in range(3)) for probability, outcome in zip(probabilities, outcomes)) / n
    rps = sum(((probability[0] - (1.0 if outcome == 0 else 0.0)) ** 2 + (probability[0] + probability[1] - (1.0 if outcome in (0, 1) else 0.0)) ** 2) / 2.0 for probability, outcome in zip(probabilities, outcomes)) / n
    top1 = sum(max(range(3), key=lambda i: probability[i]) == outcome for probability, outcome in zip(probabilities, outcomes)) / n
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for probability, outcome in zip(probabilities, outcomes):
        predicted = max(range(3), key=lambda i: probability[i])
        confidence = probability[predicted]
        bins[min(9, int(confidence * 10))].append((confidence, 1.0 if predicted == outcome else 0.0))
    ece = sum(len(bucket) / n * abs(sum(c for c, _ in bucket) / len(bucket) - sum(ok for _, ok in bucket) / len(bucket)) for bucket in bins if bucket)
    return {"n": n, "logloss": logloss, "brier": brier, "rps": rps, "top1": top1, "ece": ece}


def bootstrap_ci(values: list[float], repetitions: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED) -> list[float]:
    require(bool(values), "BOOTSTRAP_EMPTY")
    generator = random.Random(seed)
    n = len(values)
    samples = sorted(sum(values[generator.randrange(n)] for _ in range(n)) / n for _ in range(repetitions))
    return [samples[int(0.025 * (repetitions - 1))], samples[math.ceil(0.975 * (repetitions - 1))]]


def make_blocks(rows: list[dict[str, Any]], warm_fraction: float = 0.2, block_count: int = 5) -> tuple[int, list[tuple[int, int]]]:
    groups = kickoff_groups(rows)
    warm_target = math.ceil(len(rows) * warm_fraction)
    cumulative = 0
    group_index = 0
    while group_index < len(groups) and cumulative < warm_target:
        cumulative += groups[group_index][1] - groups[group_index][0]
        group_index += 1
    warm_end = groups[group_index - 1][1]
    remaining = groups[group_index:]
    target_each = (len(rows) - warm_end) / block_count
    blocks: list[tuple[int, int]] = []
    block_start = warm_end
    accumulated = 0
    for index, group in enumerate(remaining):
        accumulated += group[1] - group[0]
        groups_left = len(remaining) - index - 1
        if len(blocks) < block_count - 1 and accumulated >= target_each and groups_left >= block_count - len(blocks) - 1:
            blocks.append((block_start, group[1]))
            block_start = group[1]
            accumulated = 0
    blocks.append((block_start, len(rows)))
    require(len(blocks) == block_count, f"OOF_BLOCK_COUNT:{len(blocks)}")
    return warm_end, blocks


def join_inputs(source_rows: list[dict[str, Any]], baseline_rows: list[dict[str, Any]], label_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[list[float]], list[int]]:
    baseline = {row["n3_fixture_id"]: row for row in baseline_rows}
    labels = {row["fixture_id"]: row for row in label_rows}
    require(len(baseline) == len(source_rows), f"BASELINE_COUNT:{len(baseline)}!={len(source_rows)}")
    require(len(labels) == len(source_rows), f"LABEL_COUNT:{len(labels)}!={len(source_rows)}")
    rows = sorted(source_rows, key=lambda row: (row["kickoff"], row["fixture_id"]))
    bases: list[list[float]] = []
    outcomes: list[int] = []
    for row in rows:
        b = baseline.get(row["fixture_id"])
        label = labels.get(row["fixture_id"])
        require(b is not None and label is not None, f"JOIN_MISSING:{row['fixture_id']}")
        require(str(b["league"]) == str(row["league"]), f"LEAGUE_MISMATCH:{row['fixture_id']}")
        bases.append([float(value) for value in b["formal_v2_1x2"]])
        outcomes.append(outcome_index(str(label["outcome"])))
    return rows, bases, outcomes


def route_report(rows: list[dict[str, Any]], eval_indices: list[int], base_eval: list[list[float]], candidate: list[list[float]], outcomes_eval: list[int]) -> dict[str, Any]:
    formal = metrics(base_eval, outcomes_eval)
    cand = metrics(candidate, outcomes_eval)
    effects = [-math.log(max(base_eval[i][outcomes_eval[i]], 1e-15)) + math.log(max(candidate[i][outcomes_eval[i]], 1e-15)) for i in range(len(candidate))]
    league_report: dict[str, Any] = {}
    for league in LEAGUES:
        positions = [j for j, source_index in enumerate(eval_indices) if rows[source_index]["league"] == league]
        require(bool(positions), f"LEAGUE_EVAL_EMPTY:{league}")
        formal_probs = [base_eval[j] for j in positions]
        cand_probs = [candidate[j] for j in positions]
        league_outcomes = [outcomes_eval[j] for j in positions]
        fm = metrics(formal_probs, league_outcomes)
        cm = metrics(cand_probs, league_outcomes)
        league_effects = [-math.log(max(formal_probs[i][league_outcomes[i]], 1e-15)) + math.log(max(cand_probs[i][league_outcomes[i]], 1e-15)) for i in range(len(positions))]
        league_report[league] = {
            "n": len(positions),
            "coverage": 1.0,
            "formal": fm,
            "candidate": cm,
            "formal_minus_candidate_logloss": float(fm["logloss"]) - float(cm["logloss"]),
            "candidate_minus_formal_brier": float(cm["brier"]) - float(fm["brier"]),
            "candidate_minus_formal_rps": float(cm["rps"]) - float(fm["rps"]),
            "candidate_minus_formal_top1": float(cm["top1"]) - float(fm["top1"]),
            "candidate_minus_formal_ece": float(cm["ece"]) - float(fm["ece"]),
            "paired_bootstrap_95ci": bootstrap_ci(league_effects, seed=BOOTSTRAP_SEED + len(positions)),
        }
    return {
        "formal": formal,
        "candidate": cand,
        "formal_minus_candidate_logloss": float(formal["logloss"]) - float(cand["logloss"]),
        "candidate_minus_formal_brier": float(cand["brier"]) - float(formal["brier"]),
        "candidate_minus_formal_rps": float(cand["rps"]) - float(formal["rps"]),
        "candidate_minus_formal_top1": float(cand["top1"]) - float(formal["top1"]),
        "candidate_minus_formal_ece": float(cand["ece"]) - float(formal["ece"]),
        "paired_bootstrap_95ci": bootstrap_ci(effects),
        "league_report": league_report,
        "J1": {"status": "NOT_AVAILABLE", "n": 0, "coverage": 0.0, "weight": 0, "matrix_delta": 0},
        "K1": {"status": "NOT_AVAILABLE", "n": 0, "coverage": 0.0, "weight": 0, "matrix_delta": 0},
    }


def development_oof(source_path: Path, baseline_path: Path, labels_path: Path, out_dir: Path) -> dict[str, Any]:
    source_rows = read_jsonl(source_path)
    baseline_rows = read_jsonl(baseline_path)
    label_rows = read_jsonl(labels_path)
    require(len(source_rows) == EXPECTED[2020], f"DEVELOPMENT_SOURCE_COUNT:{len(source_rows)}")
    rows, bases, outcomes = join_inputs(source_rows, baseline_rows, label_rows)
    warm_end, blocks = make_blocks(rows)
    eval_indices = [index for start, end in blocks for index in range(start, end)]
    base_eval = [bases[index] for index in eval_indices]
    outcomes_eval = [outcomes[index] for index in eval_indices]
    route_results: list[dict[str, Any]] = []
    for route, (_kind, _parameter, ridge_c) in ROUTES.items():
        raw = build_raw_features(rows, route)
        predictions: list[list[float] | None] = [None] * len(rows)
        for start, end in blocks:
            train_indices = list(range(start))
            means, stds = fit_scaler(raw, train_indices)
            train_features = [transform(raw[i], means, stds) for i in train_indices]
            model = fit_model(train_features, [bases[i] for i in train_indices], [outcomes[i] for i in train_indices], float(ridge_c))
            for index in range(start, end):
                predictions[index] = softmax_offset(bases[index], transform(raw[index], means, stds), model)
        candidate = [predictions[index] for index in eval_indices]
        require(all(row is not None for row in candidate), "PREDICTION_MISSING")
        report = route_report(rows, eval_indices, base_eval, [list(row) for row in candidate if row is not None], outcomes_eval)
        qualified = (
            report["formal_minus_candidate_logloss"] > 0.0
            and report["candidate_minus_formal_brier"] <= 0.0005
            and report["candidate_minus_formal_rps"] <= 0.0005
            and report["candidate_minus_formal_ece"] <= 0.005
        )
        report.update({"route": route, "qualified": qualified})
        route_results.append(report)

    qualified = [row for row in route_results if row["qualified"]]
    best = max(route_results, key=lambda row: row["formal_minus_candidate_logloss"])
    if qualified:
        selected = max(qualified, key=lambda row: (row["formal_minus_candidate_logloss"], -row["candidate_minus_formal_brier"], -row["candidate_minus_formal_rps"]))
        classification = "DEVELOPMENT_ROUTE_QUALIFIED"
        selected_route = selected["route"]
        isolated_open_allowed = True
    elif best["formal_minus_candidate_logloss"] > 0.0:
        classification = "POSITIVE_SIGNAL_DEVELOPMENT_ONLY"
        selected_route = None
        isolated_open_allowed = False
    else:
        classification = "FAIL_RESEARCH_DIRECTION"
        selected_route = None
        isolated_open_allowed = False

    result = {
        "schema_version": "football3-nova-n3-finishing-keeper-development-oof-v1",
        "status": "N3_DEVELOPMENT_OOF_COMPLETE",
        "classification": classification,
        "development_season": 2020,
        "development_n": EXPECTED[2020],
        "oof_evaluation_n": len(eval_indices),
        "warmup_end": warm_end,
        "selected_route": selected_route,
        "best_signal_route": best["route"],
        "best_formal_minus_candidate_logloss": best["formal_minus_candidate_logloss"],
        "isolated_open_allowed": isolated_open_allowed,
        "isolated_2021_labels_read": 0,
        "routes": route_results,
        "score_matrix": {"formal_v2_unchanged": True, "candidate_matrix_delta": 0, "exact_score_metrics_changed": False},
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "development_oof_result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    freeze = {
        "status": "N3_ROUTE_FROZEN" if isolated_open_allowed else "N3_NO_ROUTE_FROZEN",
        "selected_route": selected_route,
        "classification": classification,
        "isolated_open_allowed": isolated_open_allowed,
        "isolated_2021_labels_read": 0,
        "experiment_budget_remaining": 1 if isolated_open_allowed else 0,
    }
    (out_dir / "route_freeze.json").write_text(json.dumps(freeze, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def isolated_eval(dev_source_path: Path, iso_source_path: Path, dev_baseline_path: Path, iso_baseline_path: Path, dev_labels_path: Path, iso_labels_path: Path, route_freeze_path: Path, out_dir: Path) -> dict[str, Any]:
    freeze = json.loads(route_freeze_path.read_text(encoding="utf-8"))
    require(freeze.get("status") == "N3_ROUTE_FROZEN" and freeze.get("isolated_open_allowed") is True, "ISOLATED_NOT_AUTHORIZED")
    route = str(freeze.get("selected_route") or "")
    require(route in ROUTES, "BAD_FROZEN_ROUTE")
    dev_source = read_jsonl(dev_source_path)
    iso_source = read_jsonl(iso_source_path)
    require(len(dev_source) == EXPECTED[2020] and len(iso_source) == EXPECTED[2021], "SOURCE_COUNTS")
    all_rows = sorted(dev_source + iso_source, key=lambda row: (row["kickoff"], row["fixture_id"]))
    raw = build_raw_features(all_rows, route)
    index_by_id = {row["fixture_id"]: i for i, row in enumerate(all_rows)}
    require(len(index_by_id) == len(all_rows), "COMBINED_DUPLICATE")

    dev_baselines = read_jsonl(dev_baseline_path)
    iso_baselines = read_jsonl(iso_baseline_path)
    dev_labels = read_jsonl(dev_labels_path)
    iso_labels = read_jsonl(iso_labels_path)
    dev_rows, dev_bases, dev_outcomes = join_inputs(dev_source, dev_baselines, dev_labels)
    iso_rows, iso_bases, iso_outcomes = join_inputs(iso_source, iso_baselines, iso_labels)
    dev_indices = [index_by_id[row["fixture_id"]] for row in dev_rows]
    iso_indices = [index_by_id[row["fixture_id"]] for row in iso_rows]
    means, stds = fit_scaler(raw, dev_indices)
    train_features = [transform(raw[index], means, stds) for index in dev_indices]
    _kind, _parameter, ridge_c = ROUTES[route]
    model = fit_model(train_features, dev_bases, dev_outcomes, float(ridge_c))
    candidate = [softmax_offset(iso_bases[pos], transform(raw[index], means, stds), model) for pos, index in enumerate(iso_indices)]
    report = route_report(iso_rows, list(range(len(iso_rows))), iso_bases, candidate, iso_outcomes)
    positive_leagues = sum(1 for value in report["league_report"].values() if value["formal_minus_candidate_logloss"] > 0.0)
    worst_degradation = max(-value["formal_minus_candidate_logloss"] for value in report["league_report"].values())
    pass_all = (
        report["formal_minus_candidate_logloss"] > 0.0
        and positive_leagues >= 3
        and worst_degradation <= 0.01
        and report["candidate_minus_formal_brier"] <= 0.0005
        and report["candidate_minus_formal_rps"] <= 0.0005
        and report["candidate_minus_formal_ece"] <= 0.005
    )
    if pass_all:
        classification = "RESEARCH_CANDIDATE"
    elif report["formal_minus_candidate_logloss"] > 0.0:
        classification = "POSITIVE_SIGNAL"
    else:
        classification = "FAIL_CURRENT_IMPLEMENTATION"
    result = {
        "schema_version": "football3-nova-n3-finishing-keeper-isolated-v1",
        "status": "N3_ISOLATED_EVALUATION_COMPLETE",
        "classification": classification,
        "route": route,
        "isolated_season": 2021,
        "isolated_n": EXPECTED[2021],
        "coverage": 1.0,
        "positive_test_leagues": positive_leagues,
        "worst_league_logloss_degradation": worst_degradation,
        "isolated_2021_labels_read": EXPECTED[2021],
        **report,
        "score_matrix": {"formal_v2_unchanged": True, "candidate_matrix_delta": 0, "exact_score_metrics_changed": False},
        "candidate_activation_allowed": False,
        "promotion_allowed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "isolated_result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def finalize(dev_result_path: Path, isolated_result_path: Path | None, out_dir: Path) -> dict[str, Any]:
    dev = json.loads(dev_result_path.read_text(encoding="utf-8"))
    isolated: dict[str, Any] | None = None
    if isolated_result_path is not None and isolated_result_path.exists():
        isolated = json.loads(isolated_result_path.read_text(encoding="utf-8"))
    if dev["classification"] == "FAIL_RESEARCH_DIRECTION":
        classification = "FAIL_RESEARCH_DIRECTION"
        isolated_labels = 0
    elif dev["classification"] == "POSITIVE_SIGNAL_DEVELOPMENT_ONLY":
        classification = "POSITIVE_SIGNAL"
        isolated_labels = 0
    else:
        require(isolated is not None, "ISOLATED_RESULT_REQUIRED")
        classification = str(isolated["classification"])
        isolated_labels = int(isolated["isolated_2021_labels_read"])
    receipt = {
        "schema_version": "football3-nova-n3-finishing-keeper-final-v1",
        "status": "N3_TERMINAL",
        "classification": classification,
        "research_question": "Lagged finishing and keeper residual persistence beyond frozen Formal V2",
        "development_classification": dev["classification"],
        "selected_route": dev["selected_route"],
        "best_signal_route": dev["best_signal_route"],
        "development_best_formal_minus_candidate_logloss": dev["best_formal_minus_candidate_logloss"],
        "isolated_2021_labels_read": isolated_labels,
        "j1": {"status": "NOT_AVAILABLE", "n": 0, "coverage": 0.0, "weight": 0, "matrix_delta": 0},
        "k1": {"status": "NOT_AVAILABLE", "n": 0, "coverage": 0.0, "weight": 0, "matrix_delta": 0},
        "score_matrix": {"formal_v2_unchanged": True, "candidate_matrix_delta": 0, "exact_score_metrics_changed": False},
        "candidate_activation_allowed": False,
        "promotion_allowed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    if isolated is not None:
        receipt["isolated_summary"] = {
            "classification": isolated["classification"],
            "formal_minus_candidate_logloss": isolated["formal_minus_candidate_logloss"],
            "candidate_minus_formal_brier": isolated["candidate_minus_formal_brier"],
            "candidate_minus_formal_rps": isolated["candidate_minus_formal_rps"],
            "candidate_minus_formal_top1": isolated["candidate_minus_formal_top1"],
            "candidate_minus_formal_ece": isolated["candidate_minus_formal_ece"],
            "paired_bootstrap_95ci": isolated["paired_bootstrap_95ci"],
            "positive_test_leagues": isolated["positive_test_leagues"],
            "worst_league_logloss_degradation": isolated["worst_league_logloss_degradation"],
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "final_receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("source-precheck")
    pre.add_argument("--prereg", type=Path, required=True)
    pre.add_argument("--season", type=int, required=True)
    pre.add_argument("--out", type=Path, required=True)

    seal = sub.add_parser("baseline-seal")
    seal.add_argument("--prereg", type=Path, required=True)
    seal.add_argument("--v2-final", type=Path, required=True)
    seal.add_argument("--v2-development", type=Path, required=True)
    seal.add_argument("--xg-frozen", type=Path, required=True)
    seal.add_argument("--out", type=Path, required=True)

    labels = sub.add_parser("label-vault")
    labels.add_argument("--prereg", type=Path, required=True)
    labels.add_argument("--season", type=int, required=True)
    labels.add_argument("--out", type=Path, required=True)

    dev = sub.add_parser("development-oof")
    dev.add_argument("--source", type=Path, required=True)
    dev.add_argument("--baseline", type=Path, required=True)
    dev.add_argument("--labels", type=Path, required=True)
    dev.add_argument("--out", type=Path, required=True)

    iso = sub.add_parser("isolated-eval")
    iso.add_argument("--dev-source", type=Path, required=True)
    iso.add_argument("--iso-source", type=Path, required=True)
    iso.add_argument("--dev-baseline", type=Path, required=True)
    iso.add_argument("--iso-baseline", type=Path, required=True)
    iso.add_argument("--dev-labels", type=Path, required=True)
    iso.add_argument("--iso-labels", type=Path, required=True)
    iso.add_argument("--route-freeze", type=Path, required=True)
    iso.add_argument("--out", type=Path, required=True)

    fin = sub.add_parser("finalize")
    fin.add_argument("--development-result", type=Path, required=True)
    fin.add_argument("--isolated-result", type=Path)
    fin.add_argument("--out", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "source-precheck":
        result = source_precheck(args.prereg, args.season, args.out)
    elif args.command == "baseline-seal":
        result = baseline_seal(args.prereg, args.v2_final, args.v2_development, args.xg_frozen, args.out)
    elif args.command == "label-vault":
        result = label_vault(args.prereg, args.season, args.out)
    elif args.command == "development-oof":
        result = development_oof(args.source, args.baseline, args.labels, args.out)
    elif args.command == "isolated-eval":
        result = isolated_eval(args.dev_source, args.iso_source, args.dev_baseline, args.iso_baseline, args.dev_labels, args.iso_labels, args.route_freeze, args.out)
    else:
        result = finalize(args.development_result, args.isolated_result, args.out)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
