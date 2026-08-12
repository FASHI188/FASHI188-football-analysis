#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

SOURCE_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW = f"https://raw.githubusercontent.com/hudl/open-data/{SOURCE_COMMIT}/data"
COMPETITION_ID = 37
SEASONS = {
    "2018/2019": 4,
    "2019/2020": 42,
    "2020/2021": 90,
    "2023/2024": 281,
}
ALLOWED_MATCH_KEYS = {
    "match_id", "match_date", "kick_off", "competition", "season", "home_team", "away_team"
}
OUT = Path(os.environ.get("R44L4_OUT", "r44l4_zero_label_output"))
OUT.mkdir(parents=True, exist_ok=True)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r44l4-zero-label/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon_zero(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parts = text.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return False
    return all(abs(x) < 1e-12 for x in nums)


def restricted_match_rows(data: bytes, expected_season_id: int) -> tuple[list[dict], dict]:
    raw_rows = json.loads(data.decode("utf-8"))
    out = []
    identity_errors = []
    for src in raw_rows:
        row = {k: src.get(k) for k in ALLOWED_MATCH_KEYS}
        comp_id = int((row.get("competition") or {}).get("competition_id", -1))
        season_id = int((row.get("season") or {}).get("season_id", -1))
        if comp_id != COMPETITION_ID or season_id != expected_season_id:
            identity_errors.append({"match_id": row.get("match_id"), "competition_id": comp_id, "season_id": season_id})
        out.append({
            "match_id": int(row["match_id"]),
            "match_date": str(row.get("match_date") or ""),
            "kick_off": str(row.get("kick_off") or ""),
            "home_team_id": int((row.get("home_team") or {}).get("home_team_id", -1)),
            "away_team_id": int((row.get("away_team") or {}).get("away_team_id", -1)),
        })
    return out, {"identity_errors": identity_errors}


def lineup_audit(match_id: int) -> dict:
    url = f"{RAW}/lineups/{match_id}.json"
    try:
        data = fetch(url)
        teams = json.loads(data.decode("utf-8"))
    except Exception as exc:
        return {"match_id": match_id, "ok": False, "error": type(exc).__name__, "sha256": None, "team_count": 0, "starter_counts": []}
    starter_counts = []
    for team in teams:
        starters = 0
        for player in team.get("lineup", []):
            positions = player.get("positions") or []
            if any(canon_zero(pos.get("from")) for pos in positions):
                starters += 1
        starter_counts.append(starters)
    return {
        "match_id": match_id,
        "ok": True,
        "sha256": digest(data),
        "team_count": len(teams),
        "starter_counts": starter_counts,
    }


def event_schema_audit(match_id: int) -> dict:
    url = f"{RAW}/events/{match_id}.json"
    try:
        data = fetch(url)
        events = json.loads(data.decode("utf-8"))
    except Exception as exc:
        return {"match_id": match_id, "ok": False, "error": type(exc).__name__, "sha256": None}
    xg_events = 0
    shot_link_passes = 0
    pass_events = 0
    for event in events:
        etype = str((event.get("type") or {}).get("name") or "")
        if etype == "Shot":
            shot = event.get("shot") or {}
            if isinstance(shot.get("statsbomb_xg"), (int, float)):
                xg_events += 1
        elif etype == "Pass":
            pass_events += 1
            p = event.get("pass") or {}
            if p.get("assisted_shot_id"):
                shot_link_passes += 1
    return {
        "match_id": match_id,
        "ok": True,
        "sha256": digest(data),
        "event_count": len(events),
        "pass_events": pass_events,
        "xg_events": xg_events,
        "shot_link_passes": shot_link_passes,
    }


def stable_sample(ids: list[int], n: int = 10) -> list[int]:
    ranked = sorted(ids, key=lambda mid: hashlib.sha256(f"R44L4_SCHEMA_20260813|{mid}".encode()).hexdigest())
    return ranked[: min(n, len(ranked))]


def main() -> int:
    ledger = []
    season_rows: dict[str, list[dict]] = {}
    all_ids: list[int] = []
    identity_errors = []

    for season_name, season_id in SEASONS.items():
        url = f"{RAW}/matches/{COMPETITION_ID}/{season_id}.json"
        data = fetch(url)
        rows, audit = restricted_match_rows(data, season_id)
        season_rows[season_name] = rows
        all_ids.extend(r["match_id"] for r in rows)
        identity_errors.extend(audit["identity_errors"])
        ledger.append({
            "kind": "matches_container",
            "season": season_name,
            "season_id": season_id,
            "url": url,
            "sha256": digest(data),
            "bytes": len(data),
            "identity_rows": len(rows),
        })

    duplicate_ids = [mid for mid, count in Counter(all_ids).items() if count != 1]

    lineup_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(lineup_audit, mid): mid for mid in all_ids}
        for fut in concurrent.futures.as_completed(futures):
            lineup_results.append(fut.result())
    lineup_results.sort(key=lambda r: r["match_id"])
    lineup_ok = [r for r in lineup_results if r["ok"]]
    xi_ok = [r for r in lineup_ok if r["team_count"] == 2 and sorted(r["starter_counts"]) == [11, 11]]

    schema_results = []
    schema_by_season = {}
    for season_name, rows in season_rows.items():
        sample = stable_sample([r["match_id"] for r in rows], 10)
        season_audits = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for result in ex.map(event_schema_audit, sample):
                season_audits.append(result)
                schema_results.append({"season": season_name, **result})
        schema_by_season[season_name] = season_audits

    season_counts = {name: len(rows) for name, rows in season_rows.items()}
    schema_checks = {}
    for season_name, audits in schema_by_season.items():
        good = [r for r in audits if r.get("ok")]
        schema_checks[season_name] = {
            "sample_n": len(audits),
            "download_ok": len(good),
            "matches_with_xg": sum(int(r.get("xg_events", 0) > 0) for r in good),
            "matches_with_shot_link_pass": sum(int(r.get("shot_link_passes", 0) > 0) for r in good),
            "total_xg_events": sum(int(r.get("xg_events", 0)) for r in good),
            "total_shot_link_passes": sum(int(r.get("shot_link_passes", 0)) for r in good),
        }

    gates = {
        "total_identity_ge_400": len(all_ids) >= 400,
        "each_season_identity_ge_70": all(v >= 70 for v in season_counts.values()),
        "global_match_id_unique": len(duplicate_ids) == 0,
        "identity_exact": len(identity_errors) == 0,
        "lineup_file_coverage_ge_98pct": len(lineup_ok) / max(len(all_ids), 1) >= 0.98,
        "xi_11v11_coverage_ge_95pct": len(xi_ok) / max(len(all_ids), 1) >= 0.95,
        "event_schema_four_of_four": all(
            c["download_ok"] == c["sample_n"]
            and c["matches_with_xg"] >= 9
            and c["matches_with_shot_link_pass"] >= 5
            for c in schema_checks.values()
        ),
    }
    passed = all(gates.values())
    result = {
        "study_id": "r44l4_wsl_attack_balance_external_domain",
        "phase": "ZERO_LABEL_COVERAGE",
        "source_commit": SOURCE_COMMIT,
        "competition_id": COMPETITION_ID,
        "season_ids": SEASONS,
        "label_fields_accessed": 0,
        "model_fits": 0,
        "thresholds_selected": 0,
        "formal_weight": 0,
        "season_identity_counts": season_counts,
        "total_identity_count": len(all_ids),
        "duplicate_match_ids": duplicate_ids,
        "identity_error_count": len(identity_errors),
        "lineup_download_ok": len(lineup_ok),
        "lineup_coverage": len(lineup_ok) / max(len(all_ids), 1),
        "xi_11v11_ok": len(xi_ok),
        "xi_11v11_coverage": len(xi_ok) / max(len(all_ids), 1),
        "schema_checks": schema_checks,
        "gates": gates,
        "status": "PASS_R44L4_ZERO_LABEL_COVERAGE" if passed else "STOP_DATA_COVERAGE_R44L4",
    }
    (OUT / "zero_label_result_r44l4.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "source_ledger_r44l4.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "lineup_coverage_r44l4.json").write_text(json.dumps(lineup_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "event_schema_sample_r44l4.json").write_text(json.dumps(schema_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "total": len(all_ids), "season_counts": season_counts, "xi_coverage": result["xi_11v11_coverage"], "gates": gates}, ensure_ascii=False))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
