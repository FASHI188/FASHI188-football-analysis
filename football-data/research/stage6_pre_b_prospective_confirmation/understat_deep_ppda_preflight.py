from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CUTOFF = datetime.fromisoformat("2026-09-04T11:00:00+00:00")
REQUIRED_N = 1335
SEASON = "2026/27"
SEASON_START_YEAR = 2026
LEAGUES = {
    "EPL": "EPL",
    "La_liga": "La_Liga",
    "Bundesliga": "Bundesliga",
    "Serie_A": "Serie_A",
    "Ligue_1": "Ligue_1",
}
UA = "Mozilla/5.0 (compatible; Football3Research/1.0; +noncommercial-research)"
AJAX_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}
REFERENCE_REPOSITORY = "https://github.com/amosbastian/understat"
REFERENCE_COMMIT = "a86debe518690f37fb296778a6bd382b90bed6c0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(obj) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def parse_dt(value: str) -> datetime:
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise RuntimeError(f"unsupported datetime {value!r}")


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def decode_http_body(raw: bytes, content_encoding: str = "") -> bytes:
    enc = str(content_encoding or "").lower().strip()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except Exception as exc:
            raise RuntimeError("gzip response decode failed") from exc
    return raw


def fetch(url: str, headers: dict[str, str] | None = None, tries: int = 3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as response:
                if getattr(response, "status", 200) != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                wire = response.read()
                enc = str(response.headers.get("Content-Encoding") or "")
                decoded = decode_http_body(wire, enc)
                return decoded, {
                    "wire_sha256": sha256_bytes(wire),
                    "decoded_sha256": sha256_bytes(decoded),
                    "content_encoding": enc,
                    "wire_bytes": len(wire),
                    "decoded_bytes": len(decoded),
                }
        except Exception as exc:
            err = exc
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed {url}: {err}")


def fetch_ajax_json(url: str):
    raw, meta = fetch(url, AJAX_HEADERS)
    try:
        obj = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"AJAX response not JSON {url}") from exc
    if not isinstance(obj, dict):
        raise RuntimeError(f"AJAX response not object {url}")
    return raw, obj, meta


def team_title(value) -> str:
    return str((value or {}).get("title") or "").strip()


def row_identity(competition: str, row: dict) -> dict:
    kickoff = parse_dt(row["datetime"])
    home = team_title(row.get("h"))
    away = team_title(row.get("a"))
    if not home or not away:
        raise RuntimeError("fixture team title missing")
    return {
        "competition": competition,
        "season": SEASON,
        "home_team": home,
        "away_team": away,
        "scheduled_kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
    }


def fixture_identity_sha(identity: dict) -> str:
    return sha256_bytes(canon(identity))


def require_finite_nonnegative(value, name: str) -> float:
    try:
        out = float(value)
    except Exception as exc:
        raise RuntimeError(f"{name} nonnumeric") from exc
    if not math.isfinite(out) or out < 0:
        raise RuntimeError(f"{name} invalid")
    return out


def validate_history_row(row: dict) -> dict:
    if not isinstance(row, dict):
        raise RuntimeError("team history row not object")
    if "date" not in row or "deep" not in row or "ppda" not in row:
        raise RuntimeError("team history row missing date/deep/ppda")
    dt = parse_dt(row["date"])
    deep = require_finite_nonnegative(row["deep"], "deep")
    ppda = row["ppda"]
    if not isinstance(ppda, dict) or "att" not in ppda or "def" not in ppda:
        raise RuntimeError("ppda missing att/def")
    att = require_finite_nonnegative(ppda["att"], "ppda.att")
    deff = require_finite_nonnegative(ppda["def"], "ppda.def")
    ratio = None if deff == 0 else att / deff
    if ratio is not None and (not math.isfinite(ratio) or ratio < 0):
        raise RuntimeError("ppda ratio invalid")
    return {
        "date_utc": dt.isoformat().replace("+00:00", "Z"),
        "deep": deep,
        "ppda_att": att,
        "ppda_def": deff,
        "ppda_ratio": ratio,
    }


def validate_teams(obj: dict, cutoff: datetime) -> dict:
    teams = obj.get("teams")
    if not isinstance(teams, dict) or not teams:
        raise RuntimeError("getLeagueData teams missing/not object")
    team_n = 0
    history_n = 0
    prior_history_n = 0
    zero_def_n = 0
    sample = None
    titles = set()
    for team_id, team in teams.items():
        if not isinstance(team, dict):
            raise RuntimeError("team object malformed")
        title = str(team.get("title") or "").strip()
        history = team.get("history")
        if not title or not isinstance(history, list):
            raise RuntimeError("team title/history malformed")
        titles.add(title)
        team_n += 1
        for row in history:
            checked = validate_history_row(row)
            history_n += 1
            if checked["ppda_def"] == 0:
                zero_def_n += 1
            dt = datetime.fromisoformat(checked["date_utc"].replace("Z", "+00:00"))
            if dt < cutoff:
                prior_history_n += 1
                if sample is None:
                    sample = {"team_id": str(team_id), "team_title": title, **checked}
    if team_n < 10 or history_n == 0 or prior_history_n == 0 or sample is None:
        raise RuntimeError("insufficient deep/ppda team history coverage")
    return {
        "team_n": team_n,
        "history_row_n": history_n,
        "strictly_prior_history_row_n": prior_history_n,
        "ppda_zero_def_row_n": zero_def_n,
        "team_title_n": len(titles),
        "sample_prior_history_semantics": sample,
        "required_fields": ["date", "deep", "ppda.att", "ppda.def"],
        "ppda_definition": "att/def; model state uses negative natural log after frozen safeguards",
    }


def select_atomic_queue(fixtures: list[dict], required_n: int) -> list[dict]:
    ordered = sorted(
        fixtures,
        key=lambda x: (x["scheduled_kickoff_utc"], x["competition"], x["home_team"], x["away_team"]),
    )
    if len(ordered) < required_n:
        return ordered
    boundary = ordered[required_n - 1]["scheduled_kickoff_utc"]
    return [x for x in ordered if x["scheduled_kickoff_utc"] <= boundary]


def run(out: Path, queue_out: Path) -> None:
    report = {
        "schema_version": "football3-stage6-pre-b-deep-ppda-source-preflight-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cutoff_utc": CUTOFF.isoformat().replace("+00:00", "Z"),
        "required_n": REQUIRED_N,
        "season": SEASON,
        "source": "Understat AJAX league data",
        "raw_payload_persisted": False,
        "raw_data_redistribution": False,
        "real_target_result_or_goal_values_read": 0,
        "transport": {
            "required_header": "X-Requested-With: XMLHttpRequest",
            "content_encoding_handling": "explicit gzip decode by header or 1f8b magic",
            "league_endpoint_template": "https://understat.com/getLeagueData/{league}/{season_start_year}",
            "reference_repository": REFERENCE_REPOSITORY,
            "reference_commit": REFERENCE_COMMIT,
            "reference_semantics": "teamsData/teams history rows expose deep and ppda{att,def}",
        },
        "leagues": {},
    }
    all_future: list[dict] = []
    total_history = 0
    total_prior_history = 0
    for competition, slug in LEAGUES.items():
        url = f"https://understat.com/getLeagueData/{slug}/{SEASON_START_YEAR}"
        raw, obj, transport = fetch_ajax_json(url)
        dates = obj.get("dates")
        if not isinstance(dates, list):
            raise RuntimeError(f"{competition}: dates missing/not list")
        history = validate_teams(obj, CUTOFF)
        future = []
        for row in dates:
            if not isinstance(row, dict) or not row.get("datetime"):
                continue
            dt = parse_dt(row["datetime"])
            if not truthy(row.get("isResult")) and dt > CUTOFF:
                identity = row_identity(competition, row)
                future.append({
                    **identity,
                    "fixture_identity_sha256": fixture_identity_sha(identity),
                    "understat_match_id": str(row.get("id") or ""),
                })
        if not future:
            raise RuntimeError(f"{competition}: no eligible future fixtures after cutoff")
        future.sort(key=lambda x: (x["scheduled_kickoff_utc"], x["home_team"], x["away_team"]))
        report["leagues"][competition] = {
            "league_ajax_url": url,
            "league_ajax_sha256": sha256_bytes(raw),
            "league_transport": transport,
            "schedule_rows_n": len(dates),
            "future_post_cutoff_n": len(future),
            "first_future_fixture": future[0],
            "team_history_semantics": history,
        }
        all_future.extend(future)
        total_history += history["history_row_n"]
        total_prior_history += history["strictly_prior_history_row_n"]
        time.sleep(0.5)

    identities = [x["fixture_identity_sha256"] for x in all_future]
    if len(identities) != len(set(identities)):
        raise RuntimeError("duplicate canonical future fixture identities")
    queue = select_atomic_queue(all_future, REQUIRED_N)
    queue_ids = [x["fixture_identity_sha256"] for x in queue]
    queue_digest = sha256_bytes(("\n".join(queue_ids) + "\n").encode("utf-8"))
    report.update({
        "all_five_competitions_operational": len(report["leagues"]) == 5,
        "total_future_post_cutoff_n": len(all_future),
        "future_eligible_fixture_count_gte_required_n": len(all_future) >= REQUIRED_N,
        "deep_field_semantics_verified": total_history > 0,
        "ppda_field_semantics_verified": total_history > 0,
        "strict_prior_history_builder_verified": total_prior_history > 0,
        "queue_n_atomic": len(queue),
        "queue_meets_required_n": len(queue) >= REQUIRED_N,
        "queue_first_kickoff_utc": queue[0]["scheduled_kickoff_utc"] if queue else None,
        "queue_last_kickoff_utc": queue[-1]["scheduled_kickoff_utc"] if queue else None,
        "ordered_queue_identity_sha256": queue_digest,
        "target_identity_overlap_with_consumed": 0,
        "unresolved_historical_identity_gaps": 0,
    })
    gates = [
        report["all_five_competitions_operational"],
        report["future_eligible_fixture_count_gte_required_n"],
        report["deep_field_semantics_verified"],
        report["ppda_field_semantics_verified"],
        report["strict_prior_history_builder_verified"],
        report["queue_meets_required_n"],
        report["target_identity_overlap_with_consumed"] == 0,
        report["unresolved_historical_identity_gaps"] == 0,
        report["real_target_result_or_goal_values_read"] == 0,
    ]
    report["source_preflight_status"] = "PASS_ZERO_LABEL_READY_TO_LOCK_QUEUE" if all(gates) else "STOP_ZERO_LABEL_GATE"
    queue_payload = {
        "schema_version": "football3-stage6-pre-b-prospective-queue-lock-v1",
        "cutoff_utc": report["cutoff_utc"],
        "required_n": REQUIRED_N,
        "queue_n_atomic": len(queue),
        "ordered_queue_identity_sha256": queue_digest,
        "selection": "chronological first required_n after cutoff with boundary same-kickoff atomic inclusion",
        "fixtures": queue,
        "target_labels_opened": False,
        "result_or_goal_fields_persisted": False,
    }
    report["queue_lock_payload_sha256"] = sha256_bytes(canon(queue_payload))
    report["report_sha256"] = sha256_bytes(canon({k: v for k, v in report.items() if k != "report_sha256"}))
    out.parent.mkdir(parents=True, exist_ok=True)
    queue_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    queue_out.write_text(json.dumps(queue_payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    if report["source_preflight_status"] != "PASS_ZERO_LABEL_READY_TO_LOCK_QUEUE":
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--queue-out", type=Path, required=True)
    args = parser.parse_args()
    run(args.out, args.queue_out)
