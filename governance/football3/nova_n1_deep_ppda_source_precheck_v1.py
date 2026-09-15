#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import pathlib
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class SourcePrecheckError(RuntimeError):
    pass


def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _finite(value: Any, field: str, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool):
        raise SourcePrecheckError(f"{field}:BOOLEAN_NOT_NUMERIC")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SourcePrecheckError(f"{field}:NOT_NUMERIC") from exc
    if not math.isfinite(number) or (nonnegative and number < 0.0):
        raise SourcePrecheckError(f"{field}:INVALID")
    return number


def _parse_dt(value: Any, field: str) -> datetime:
    text = str(value or "").strip().replace("T", " ").removesuffix("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise SourcePrecheckError(f"{field}:UNSUPPORTED_DATETIME:{value!r}")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _team_id(value: Any, field: str) -> str:
    text = str((value or {}).get("id") or "").strip()
    if not text.isdigit():
        raise SourcePrecheckError(f"{field}:INVALID_TEAM_ID")
    return text


def _team_name(value: Any, field: str) -> str:
    text = str((value or {}).get("title") or "").strip()
    if not text:
        raise SourcePrecheckError(f"{field}:MISSING_TEAM_NAME")
    return text


def _ppda_parts(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise SourcePrecheckError(f"{field}:NOT_OBJECT")
    return _finite(value.get("att"), f"{field}.att"), _finite(value.get("def"), f"{field}.def")


def _ppda_value(value: Any, field: str) -> float:
    att, deff = _ppda_parts(value, field)
    return 0.0 if deff == 0.0 else att / deff


def _history_safe_row(row: Any, team_id: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise SourcePrecheckError(f"team:{team_id}:HISTORY_ROW_NOT_OBJECT")
    kickoff = _parse_dt(row.get("date"), f"team:{team_id}.date")
    h_a = str(row.get("h_a") or "").strip().lower()
    if h_a not in {"h", "a"}:
        raise SourcePrecheckError(f"team:{team_id}.h_a:INVALID")
    deep = _finite(row.get("deep"), f"team:{team_id}.deep")
    deep_allowed = _finite(row.get("deep_allowed"), f"team:{team_id}.deep_allowed")
    ppda_att, ppda_def = _ppda_parts(row.get("ppda"), f"team:{team_id}.ppda")
    allowed_att, allowed_def = _ppda_parts(row.get("ppda_allowed"), f"team:{team_id}.ppda_allowed")
    return {
        "kickoff": kickoff,
        "h_a": h_a,
        "deep": deep,
        "deep_allowed": deep_allowed,
        "ppda_att": ppda_att,
        "ppda_def": ppda_def,
        "ppda_allowed_att": allowed_att,
        "ppda_allowed_def": allowed_def,
        "ppda": 0.0 if ppda_def == 0.0 else ppda_att / ppda_def,
    }


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=tol)


def _reciprocal(home: Mapping[str, Any], away: Mapping[str, Any]) -> None:
    checks = (
        (float(home["deep_allowed"]), float(away["deep"]), "home.deep_allowed!=away.deep"),
        (float(away["deep_allowed"]), float(home["deep"]), "away.deep_allowed!=home.deep"),
        (float(home["ppda_allowed_att"]), float(away["ppda_att"]), "home.ppda_allowed.att!=away.ppda.att"),
        (float(home["ppda_allowed_def"]), float(away["ppda_def"]), "home.ppda_allowed.def!=away.ppda.def"),
        (float(away["ppda_allowed_att"]), float(home["ppda_att"]), "away.ppda_allowed.att!=home.ppda.att"),
        (float(away["ppda_allowed_def"]), float(home["ppda_def"]), "away.ppda_allowed.def!=home.ppda.def"),
    )
    for left, right, label in checks:
        if not _close(left, right):
            raise SourcePrecheckError(f"RECIPROCAL_MISMATCH:{label}:{left}:{right}")


def project_payload(payload: Mapping[str, Any], *, league: str, season: int, expected_matches: int, release_delay_hours: int) -> list[dict[str, Any]]:
    dates = payload.get("dates")
    teams = payload.get("teams")
    if not isinstance(dates, list):
        raise SourcePrecheckError(f"{league}|{season}:DATES_NOT_LIST")
    if not isinstance(teams, Mapping):
        raise SourcePrecheckError(f"{league}|{season}:TEAMS_NOT_OBJECT")

    history: dict[tuple[str, datetime], dict[str, Any]] = {}
    titles: dict[str, str] = {}
    for raw_team_id, team in teams.items():
        team_id = str(raw_team_id).strip()
        if not team_id.isdigit() or not isinstance(team, Mapping):
            raise SourcePrecheckError(f"{league}|{season}:BAD_TEAM_RECORD:{raw_team_id!r}")
        title = str(team.get("title") or "").strip()
        if not title:
            raise SourcePrecheckError(f"{league}|{season}:TEAM_TITLE_MISSING:{team_id}")
        rows = team.get("history")
        if not isinstance(rows, list):
            raise SourcePrecheckError(f"{league}|{season}:TEAM_HISTORY_MISSING:{team_id}")
        titles[team_id] = title
        for row in rows:
            safe = _history_safe_row(row, team_id)
            key = (team_id, safe["kickoff"])
            if key in history:
                raise SourcePrecheckError(f"{league}|{season}:DUPLICATE_HISTORY_KEY:{team_id}:{safe['kickoff'].isoformat()}")
            history[key] = safe

    completed = [row for row in dates if isinstance(row, Mapping) and _truthy(row.get("isResult"))]
    if len(completed) != expected_matches:
        raise SourcePrecheckError(f"{league}|{season}:COMPLETED_COUNT:{len(completed)}!={expected_matches}")

    projection: list[dict[str, Any]] = []
    seen_fixture_ids: set[str] = set()
    used_history: set[tuple[str, datetime]] = set()
    for fixture in completed:
        match_id = str(fixture.get("id") or "").strip()
        if not match_id.isdigit():
            raise SourcePrecheckError(f"{league}|{season}:BAD_MATCH_ID")
        fixture_id = f"understat:{match_id}"
        if fixture_id in seen_fixture_ids:
            raise SourcePrecheckError(f"{league}|{season}:DUPLICATE_FIXTURE:{fixture_id}")
        seen_fixture_ids.add(fixture_id)
        kickoff = _parse_dt(fixture.get("datetime"), f"{fixture_id}.datetime")
        home_id = _team_id(fixture.get("h"), f"{fixture_id}.home")
        away_id = _team_id(fixture.get("a"), f"{fixture_id}.away")
        if home_id == away_id:
            raise SourcePrecheckError(f"{fixture_id}:HOME_AWAY_COLLISION")
        home_key, away_key = (home_id, kickoff), (away_id, kickoff)
        home = history.get(home_key)
        away = history.get(away_key)
        if home is None or away is None:
            raise SourcePrecheckError(f"{fixture_id}:HISTORY_JOIN_MISSING:home={home is not None}:away={away is not None}")
        if home["h_a"] != "h" or away["h_a"] != "a":
            raise SourcePrecheckError(f"{fixture_id}:H_A_MISMATCH")
        _reciprocal(home, away)
        if home_key in used_history or away_key in used_history:
            raise SourcePrecheckError(f"{fixture_id}:HISTORY_ROW_REUSED")
        used_history.update((home_key, away_key))
        projection.append({
            "fixture_id": fixture_id,
            "league": league,
            "season_start": season,
            "kickoff": kickoff.isoformat().replace("+00:00", "Z"),
            "release_at": (kickoff + timedelta(hours=release_delay_hours)).isoformat().replace("+00:00", "Z"),
            "home_team_id": f"understat-team:{home_id}",
            "away_team_id": f"understat-team:{away_id}",
            "home_team_name": _team_name(fixture.get("h"), f"{fixture_id}.home"),
            "away_team_name": _team_name(fixture.get("a"), f"{fixture_id}.away"),
            "home_ppda": float(home["ppda"]),
            "away_ppda": float(away["ppda"]),
            "home_deep": float(home["deep"]),
            "away_deep": float(away["deep"]),
        })

    projection.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    if len(projection) != expected_matches:
        raise SourcePrecheckError(f"{league}|{season}:PROJECTION_COUNT_MISMATCH")
    return projection


def fetch_json(url: str, *, referer: str, tries: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error: Exception | None = None
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Football3-Nova-N1-Research/1.0; noncommercial-research)",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as response:
                if getattr(response, "status", 200) != 200:
                    raise SourcePrecheckError(f"HTTP_STATUS:{response.status}")
                wire = response.read()
                content_encoding = str(response.headers.get("Content-Encoding") or "").strip().lower()
            body = gzip.decompress(wire) if content_encoding == "gzip" or wire[:2] == b"\x1f\x8b" else wire
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise SourcePrecheckError("PAYLOAD_ROOT_NOT_OBJECT")
            return payload, {"sha256": _sha256(body), "bytes": len(body)}
        except Exception as exc:
            last_error = exc
            if attempt + 1 < tries:
                time.sleep(2.0 * (attempt + 1))
    raise SourcePrecheckError(f"FETCH_FAILED:{url}:{last_error}")


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def run(config_path: pathlib.Path, projection_out: pathlib.Path, receipt_out: pathlib.Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("status") != "PRECHECK_LOCKED_BEFORE_LABEL_READ_OR_FIT":
        raise SourcePrecheckError("CONFIG_STATUS_NOT_PRECHECK_LOCKED")
    all_rows: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for season in config["seasons"]:
        for league, spec in config["leagues"].items():
            slug = spec["slug"]
            url = config["source"]["endpoint_template"].format(slug=slug, season_start=season)
            payload, meta = fetch_json(url, referer=f"https://understat.com/league/{slug}/{season}")
            rows = project_payload(
                payload,
                league=league,
                season=int(season),
                expected_matches=int(spec["expected_matches_per_season"]),
                release_delay_hours=int(config["release_delay_hours"]),
            )
            key = f"{league}|{season}"
            sources[key] = {"url": url, "payload_sha256": meta["sha256"], "payload_bytes": meta["bytes"], "match_count": len(rows)}
            all_rows.extend(rows)
    all_rows.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    if len(all_rows) != int(config["expected_total_match_count"]):
        raise SourcePrecheckError(f"TOTAL_COUNT:{len(all_rows)}!={config['expected_total_match_count']}")
    ids = [row["fixture_id"] for row in all_rows]
    if len(set(ids)) != len(ids):
        raise SourcePrecheckError("GLOBAL_FIXTURE_ID_DUPLICATE")
    projection_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(projection_out, all_rows)
    governance = config["governance"]
    receipt = {
        "schema_version": "football3-nova-n1-deep-ppda-source-precheck-receipt-v1",
        "status": "N1_DEEP_PPDA_SOURCE_PRECHECK_PASS",
        "permission_class": config["source"]["permission_class"],
        "production_eligible": False,
        "match_count": len(all_rows),
        "fixture_id_unique_count": len(set(ids)),
        "fixture_identity_sha256": _sha256(_canon(ids)),
        "state_projection_sha256": _sha256(projection_out.read_bytes()),
        "source_payloads": sources,
        "release_delay_hours": int(config["release_delay_hours"]),
        "same_kickoff_atomic_required": bool(config["same_kickoff_atomic_required"]),
        "safe_features": list(config["projected_features"]),
        "label_values_read": int(governance["label_values_read"]),
        "result_values_used": int(governance["result_values_used"]),
        "score_values_used": int(governance["score_values_used"]),
        "xg_values_used": int(governance["xg_values_used"]),
        "training_performed": bool(governance["training_performed"]),
        "candidate_probabilities_generated": bool(governance["candidate_probabilities_generated"]),
        "candidate_weight": int(governance["candidate_weight"]),
        "matrix_delta": int(governance["matrix_delta"]),
        "formal_v2_changed": bool(governance["formal_v2_changed"]),
        "current_changed": bool(governance["current_changed"]),
        "production_changed": bool(governance["production_changed"]),
    }
    receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--projection-out", type=pathlib.Path, required=True)
    parser.add_argument("--receipt-out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.projection_out, args.receipt_out), sort_keys=True))


if __name__ == "__main__":
    main()