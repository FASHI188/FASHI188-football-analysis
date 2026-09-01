from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import historical_pit_replay as core
from player_strength import DIMS


class UnderstatCompatError(RuntimeError):
    pass


def _ajax(url: str) -> tuple[Any, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            **core.UA,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8")), raw
    except Exception as exc:
        raise UnderstatCompatError(f"Understat AJAX response is not JSON: {url}") from exc


def _league_dates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("dates")
        if isinstance(data, list):
            return data
    if isinstance(payload, list):
        return payload
    raise UnderstatCompatError("Understat league JSON missing dates list")


def _rosters(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("rosters", payload)
        if isinstance(data, dict) and ("h" in data or "a" in data):
            return data
    raise UnderstatCompatError("Understat match JSON missing rosters")


def understat_identity(v2_rows, out):
    payload, raw = _ajax(core.UNDERSTAT_LEAGUE_URL)
    dates = _league_dates(payload)
    identities = []
    for x in dates:
        h = x.get("h") or {}
        a = x.get("a") or {}
        mid = str(x.get("id") or "")
        date = str(x.get("datetime") or x.get("date") or "")[:10]
        ht = str(h.get("title") or h.get("name") or "")
        at = str(a.get("title") or a.get("name") or "")
        if mid and date and ht and at:
            identities.append({"understat_match_id": mid, "date": date, "home": ht, "away": at})
    idx = defaultdict(list)
    for x in identities:
        idx[(x["date"], core.norm(x["home"]), core.norm(x["away"]))].append(x)
    mapping = {}
    rows = []
    for r in v2_rows:
        key = (str(r["cutoff"])[:10], core.norm(r["home_team"]), core.norm(r["away_team"]))
        cand = idx.get(key, [])
        if len(cand) != 1:
            raise UnderstatCompatError(f"Understat identity collision/miss for {key}: {len(cand)}")
        mapping[str(r["fixture_id"])] = cand[0]["understat_match_id"]
        rows.append({"fixture_id": str(r["fixture_id"]), **cand[0]})
    if len(mapping) != core.FULL_SEASON_N or len(set(mapping.values())) != core.FULL_SEASON_N:
        raise UnderstatCompatError("Understat full-season identity is not one-to-one 380")
    rec = {
        "schema_version": "football3-understat-epl-2023-24-identity-v2",
        "transport": "PUBLIC_AJAX_JSON_X_REQUESTED_WITH_XMLHTTPREQUEST",
        "source_url": core.UNDERSTAT_LEAGUE_URL,
        "source_page_sha256": core.sha_bytes(raw),
        "source_page_bytes": len(raw),
        "collected_at": core.iso(datetime.now(timezone.utc)),
        "identity_rule": "date+canonical_home+canonical_away; result/xG values not retained or supplied to target predictor",
        "mapped_n": len(mapping),
        "result_fields_retained": False,
        "rows_sha256": core.canon(rows),
    }
    core.dump(out / "understat_identity_receipt.json", rec)
    return mapping, rec


def _source_player_id(tid: str, row: dict[str, Any], name: str) -> str:
    raw = row.get("id") or row.get("player_id") or row.get("playerId")
    if raw is not None and str(raw).strip():
        return "understat_player_" + str(raw).strip()
    # Prior-match history may use a deterministic source-name surrogate only when
    # Understat omitted its source id. Target prematch names never get this fallback.
    return core.player_pid(tid, name)


def understat_roster(match_id: str, home_tid: str, away_tid: str, release_at: str) -> dict[str, Any]:
    url = core.UNDERSTAT_MATCH_URL.format(match_id=match_id)
    payload, raw = _ajax(url)
    data = _rosters(payload)
    out_usage = defaultdict(list)
    events = []
    aliases = defaultdict(list)
    for side, tid in (("h", str(home_tid)), ("a", str(away_tid))):
        vals = data.get(side) or {}
        rows = list(vals.values()) if isinstance(vals, dict) else list(vals)
        for x in rows:
            if not isinstance(x, dict):
                continue
            name = str(x.get("player") or x.get("player_name") or x.get("playerName") or "").strip()
            if not name:
                continue
            pid = _source_player_id(tid, x, name)
            position = str(x.get("position") or "")
            minutes = float(x.get("time") or x.get("minutes") or 0.0)
            started = position.lower() not in {"sub", "substitute"} and minutes > 0
            role = core.role_from_position(position)
            rec = {
                "player_id": pid,
                "started": started,
                "appeared": minutes > 0,
                "minutes": minutes,
                "role": role,
                "known_at": release_at,
                "player_name": name,
            }
            out_usage[tid].append(rec)
            aliases[tid].append({"player_id": pid, "player_name": name})
            if minutes > 0:
                values = {
                    "shot_generation": float(x.get("xG") or 0.0),
                    "finishing": 0.0,
                    "chance_creation": float(x.get("xA") or 0.0),
                    "passing_progression": float(x.get("xGBuildup") or 0.0),
                    "carrying_progression": 0.0,
                    "possession_retention_risk": 0.0,
                    "pressing": 0.0,
                    "tackling_interception": 0.0,
                    "defensive_position_protection": 0.0,
                    "aerial": 0.0,
                    "set_piece": 0.0,
                    "goalkeeper_shot_stopping": 0.0,
                    "goalkeeper_sweeping": 0.0,
                    "goalkeeper_cross_claiming": 0.0,
                    "goalkeeper_distribution": 0.0,
                    "on_ball_contribution": float(x.get("xGChain") or 0.0),
                    "off_ball_contribution": 0.0,
                    "current_form": float(x.get("xG") or 0.0) + float(x.get("xA") or 0.0),
                }
                if set(values) - set(DIMS):
                    raise UnderstatCompatError("Understat adapter produced unknown player dimension")
                events.append({
                    "player_id": pid,
                    "team_id": tid,
                    "league_id": core.LEAGUE,
                    "role": role,
                    "known_at": release_at,
                    "minutes_exposure": minutes,
                    "possession_opportunity": 1.0,
                    "values": values,
                    "source_sha256": core.sha_bytes(raw),
                })
    for tid, rows in out_usage.items():
        if len([x for x in rows if x["started"]]) != 11:
            for x in rows:
                x["started"] = False
    return {
        "source_url": url,
        "source_sha256": core.sha_bytes(raw),
        "source_bytes": len(raw),
        "release_at": release_at,
        "usage": dict(out_usage),
        "events": events,
        "aliases": dict(aliases),
        "prohibited_target_fields_retained": False,
        "rating_field_used": False,
        "transport": "PUBLIC_AJAX_JSON_X_REQUESTED_WITH_XMLHTTPREQUEST",
    }


def strict_resolve_source_name(name: str, tid: str, registry: dict[str, dict[str, str]]):
    n = core.norm(name)
    if not n:
        return None, "EMPTY"
    exact = registry.get(str(tid), {}).get(n)
    if exact:
        return exact, "PRIOR_HISTORY_EXACT"
    surname = n.split()[-1]
    candidates = {pid for nm, pid in registry.get(str(tid), {}).items() if nm.split() and nm.split()[-1] == surname}
    if len(candidates) == 1:
        return next(iter(candidates)), "PRIOR_HISTORY_UNIQUE_SURNAME"
    if len(candidates) > 1:
        raise UnderstatCompatError(f"ambiguous player identity {tid} {name}: {sorted(candidates)}")
    return None, "NO_PRIOR_PERMANENT_IDENTITY_FAIL_CLOSED"


def install() -> None:
    core.understat_identity = understat_identity
    core.understat_roster = understat_roster
    core.resolve_source_name = strict_resolve_source_name


def main() -> int:
    install()
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("predict"); p.add_argument("--v2", required=True); p.add_argument("--out", required=True)
    s = sp.add_parser("score"); s.add_argument("--v2", required=True); s.add_argument("--label-vault", required=True); s.add_argument("--out", required=True)
    a = ap.parse_args()
    import pathlib
    if a.cmd == "predict":
        result = core.run_prediction(pathlib.Path(a.v2), pathlib.Path(a.out))
    else:
        result = core.score(pathlib.Path(a.v2), pathlib.Path(a.label_vault), pathlib.Path(a.out))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
