#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "football-data" / "formal_gpt_gateway_v1"
RUNTIME_DIR = ROOT / "football-data" / "formal_fast_runtime_v1"
for p in (str(ROOT / "football-data"), str(RUNTIME_DIR), str(GATEWAY_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import current_v2_retrospective_exact_history_v1 as exact_history
import current_v2_retrospective_replay_v1 as replay
import entry  # installs the actual entrypoint adapter chain
import gateway
import live_delta_acquisition_v1 as live
import request_contract_v1 as request_contract
import runtime as rt
from current_v2_retrospective_candidate_head_v1 import resolve_candidate_exact_head

SCHEMA = "football3-current-v2-retrospective-replay-acceptance-v1"
MODE = request_contract.CURRENT_V2_RETROSPECTIVE_REPLAY
START = datetime(2026, 9, 1, tzinfo=timezone.utc)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_ROUTES = {"FUSION_V2_ACTIVE", "FROZEN_V1_EXACT_FALLBACK"}
DOMAINS = (
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
    "UEFA_ChampionsLeague",
    "JPN_J1",
    "KOR_KLeague1",
)
FOUR = (
    ("udinese-lazio", "ITA_SerieA", ("Udinese",), ("Lazio",)),
    ("hamburg-mainz", "GER_Bundesliga", ("Hamburg", "Hamburger SV"), ("Mainz", "Mainz 05")),
    ("schalke-bayern", "GER_Bundesliga", ("Schalke 04", "Schalke"), ("Bayern Munich", "Bayern München")),
    ("nfo-tottenham", "ENG_PremierLeague", ("Nottingham Forest", "Nott'm Forest", "NFO"), ("Tottenham", "Tottenham Hotspur")),
)
UCL_FIXTURE_AUTHORITY = "https://www.uefa.com/uefachampionsleague/news/02a8-2174c9e9019d-f909a77bd77a-1000--2026-27-champions-league-all-the-league-phase-fixtures/"


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> datetime:
    raw = os.environ.get("FOOTBALL3_ACCEPTANCE_NOW_UTC", "").strip()
    return rt._parse_dt(raw, "acceptance now") if raw else datetime.now(timezone.utc)


def _norms(comp: str, names: tuple[str, ...]) -> set[str]:
    aliases = rt._read_aliases(ROOT)
    out: set[str] = set()
    for name in names:
        out.add(rt._normalize_team(name))
        try:
            out.add(rt._normalize_team(rt._canonical_team(comp, name, aliases)))
        except Exception:
            pass
    return {x for x in out if x}


def _metadata(comp: str, season: str, kickoff: datetime, home_raw: str, away_raw: str,
              source: str, source_sha: str, time_authority: str) -> dict[str, Any]:
    home = live._canonical(ROOT, comp, home_raw) if comp != replay.UCL else replay._resolve_ucl_team(ROOT, home_raw)[0]
    away = live._canonical(ROOT, comp, away_raw) if comp != replay.UCL else replay._resolve_ucl_team(ROOT, away_raw)[0]
    if not home or not away or rt._normalize_team(home) == rt._normalize_team(away):
        raise rt.RuntimeGateError(f"acceptance identity invalid: {comp} {home_raw} v {away_raw}")
    fixture_id = rt._fixture_id(comp, season, kickoff, home, away)
    return {
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
        "home_team_name": home,
        "away_team_name": away,
        "fixture_id": fixture_id,
        "source": source,
        "source_sha256": source_sha,
        "time_authority": time_authority,
        "score_or_result_fields_accessed_for_resolution": False,
    }


def _main_candidates(comp: str, upper: datetime) -> list[dict[str, Any]]:
    code = live.MAIN_EUROPE[comp]
    start_year = 2026
    url = f"https://www.football-data.co.uk/mmz4281/{live._season_code(start_year)}/{code}.csv"
    payload, source_sha = live._fetch(url)
    out: list[dict[str, Any]] = []
    for raw in live._decode_csv(payload):
        # Resolution trust boundary: only identity/date/time fields are accessed here.
        date_raw = str(raw.get("Date") or "").strip()
        time_raw = str(raw.get("Time") or "").strip()
        home_raw = str(raw.get("HomeTeam") or "").strip()
        away_raw = str(raw.get("AwayTeam") or "").strip()
        if not date_raw or not time_raw or not home_raw or not away_raw:
            continue
        kickoff = exact_history._main_kickoff({"Date": date_raw, "Time": time_raw}, start_year)
        if kickoff is None or not (START <= kickoff < upper):
            continue
        out.append(_metadata(comp, live._season_label_cross(start_year), kickoff, home_raw, away_raw,
                             url, source_sha, "FOOTBALL_DATA_DATE_TIME_EUROPE_LONDON"))
    out.sort(key=lambda x: (x["kickoff"], x["fixture_id"]))
    return out


def _j1_candidates(upper: datetime) -> list[dict[str, Any]]:
    url = "https://www.football-data.co.uk/new/JPN.csv"
    payload, source_sha = live._fetch(url)
    out: list[dict[str, Any]] = []
    accepted_leagues = {live._norm(x) for x in live.EXTRA_ARCHIVE["JPN_J1"][1]}
    for raw in live._decode_csv(payload):
        league = str(raw.get("League") or "").strip()
        if league and live._norm(league) not in accepted_leagues:
            continue
        date_raw = str(raw.get("Date") or "").strip()
        time_raw = str(raw.get("Time") or "").strip()
        home_raw = str(raw.get("HomeTeam") or "").strip()
        away_raw = str(raw.get("AwayTeam") or "").strip()
        if not date_raw or not time_raw or not home_raw or not away_raw:
            continue
        date = live._parse_date(date_raw)
        kickoff, semantics = exact_history._extra_kickoff("JPN_J1", {"Time": time_raw}, date, upper)
        if kickoff is None or semantics != "ASIA_TOKYO_SOURCE_TIME" or not (START <= kickoff < upper):
            continue
        season = live._extra_season("JPN_J1", str(raw.get("Season") or ""), date)
        if season != "2026/27":
            continue
        out.append(_metadata("JPN_J1", season, kickoff, home_raw, away_raw, url, source_sha, semantics))
    out.sort(key=lambda x: (x["kickoff"], x["fixture_id"]))
    return out


def _k1_candidates(upper: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for month in sorted({START.month, upper.month}):
        body = json.dumps({"year": "2026", "month": f"{month:02d}", "leagueId": 1}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload, source_sha = live._fetch(live.KOR_URL, data=body, headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json, text/plain, */*"})
        obj = json.loads(payload.decode("utf-8-sig"))
        data = obj.get("data", obj) if isinstance(obj, dict) else {}
        schedule = data.get("scheduleList", []) if isinstance(data, dict) else []
        for item in schedule or []:
            if not isinstance(item, dict):
                continue
            meet = str(item.get("meetName") or "")
            if "승강" in meet or "플레이오프" in meet:
                continue
            date_raw = str(item.get("gameDate") or "").replace(".", "-")
            home_raw = str(item.get("homeTeamName") or item.get("homeTeam") or "").strip()
            away_raw = str(item.get("awayTeamName") or item.get("awayTeam") or "").strip()
            if not date_raw or not home_raw or not away_raw:
                continue
            date = live._parse_date(date_raw)
            kickoff, semantics = exact_history._kor_time(item, date, upper)
            if kickoff is None or semantics != "OFFICIAL_KLEAGUE_TIME" or not (START <= kickoff < upper):
                continue
            row = _metadata("KOR_KLeague1", "2026", kickoff, home_raw, away_raw, live.KOR_URL, source_sha, semantics)
            if row["fixture_id"] not in seen:
                seen.add(row["fixture_id"])
                out.append(row)
    out.sort(key=lambda x: (x["kickoff"], x["fixture_id"]))
    return out


def _ucl_candidate(upper: datetime) -> dict[str, Any]:
    # Score-blind immutable schedule fact from the governed UEFA league-phase authority.
    local = datetime(2026, 9, 8, 18, 45, tzinfo=ZoneInfo("Europe/Zurich"))
    kickoff = local.astimezone(timezone.utc)
    if not (START <= kickoff < upper):
        raise rt.RuntimeGateError("UCL post-2026-09-01 acceptance fixture not yet eligible")
    reg = replay._ucl_registry(ROOT)
    urls = set(((reg.get("authority") or {}).get("source_urls") or []))
    if UCL_FIXTURE_AUTHORITY not in urls:
        raise rt.RuntimeGateError("UCL acceptance schedule authority not governed by 36-team registry")
    source_sha = file_sha(ROOT / replay.UCL_REGISTRY)
    return _metadata(replay.UCL, "2026/27", kickoff, "AEK Athens", "LASK", UCL_FIXTURE_AUTHORITY,
                     source_sha, "UEFA_PUBLISHED_18_45_EUROPE_ZURICH")


def discover(upper: datetime) -> dict[str, Any]:
    pools: dict[str, list[dict[str, Any]]] = {}
    for comp in live.BIG5:
        pools[comp] = _main_candidates(comp, upper)
    pools["JPN_J1"] = _j1_candidates(upper)
    pools["KOR_KLeague1"] = _k1_candidates(upper)
    pools[replay.UCL] = [_ucl_candidate(upper)]

    four: list[dict[str, Any]] = []
    for label, comp, home_names, away_names in FOUR:
        hn = _norms(comp, home_names)
        an = _norms(comp, away_names)
        hits = [x for x in pools[comp]
                if rt._normalize_team(x["home_team_name"]) in hn and rt._normalize_team(x["away_team_name"]) in an]
        if len(hits) != 1:
            raise rt.RuntimeGateError(f"FOUR_FIXTURE_IDENTITY_RESOLUTION_FAILED:{label}:hits={len(hits)}")
        row = dict(hits[0]); row["acceptance_label"] = label; four.append(row)

    batch: list[dict[str, Any]] = []
    for comp in DOMAINS:
        candidates = pools.get(comp) or []
        if not candidates:
            raise rt.RuntimeGateError(f"EIGHT_DOMAIN_FIXTURE_RESOLUTION_FAILED:{comp}")
        row = dict(candidates[0]); row["acceptance_label"] = f"eight-domain-{comp}"; batch.append(row)

    all_rows = four + batch
    if any(x.get("score_or_result_fields_accessed_for_resolution") is not False for x in all_rows):
        raise rt.RuntimeGateError("score-blind fixture resolution contract failed")
    return {
        "schema_version": SCHEMA,
        "status": "PASS",
        "mode": MODE,
        "window": {"from_inclusive": START.isoformat(), "to_exclusive": upper.isoformat()},
        "four_fixture_resolution": four,
        "eight_domain_resolution": batch,
        "fixture_resolved_percent": 100.0,
        "score_or_result_fields_accessed_for_resolution": False,
        "actual_scores_emitted": False,
        "ucl_authority_registry": replay.UCL_REGISTRY,
        "ucl_formal_scope_widened": False,
    }


def _assert_receipt(receipt: dict[str, Any], result: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "PASS":
        raise rt.RuntimeGateError(f"acceptance replay failed: {expected['acceptance_label']}:{result}")
    if receipt.get("mode") != MODE or receipt.get("request_mode") != MODE:
        raise rt.RuntimeGateError("acceptance receipt mode mismatch")
    labels = set(receipt.get("classification") or [])
    if not {"RETROSPECTIVE", "RESEARCH_ONLY"}.issubset(labels):
        raise rt.RuntimeGateError("acceptance classification mismatch")
    if receipt.get("strict_pit_claimed") is not False:
        raise rt.RuntimeGateError("acceptance strict PIT claim forbidden")
    for key in ("result_excluded", "target_fixture_excluded", "post_kickoff_events_excluded", "matrix_conservation"):
        if receipt.get(key) is not True:
            raise rt.RuntimeGateError(f"acceptance receipt gate failed:{key}")
    if (receipt.get("state_integrity_guard") or {}).get("status") != "PASS":
        raise rt.RuntimeGateError("acceptance state integrity failed")
    prediction_sha = str(receipt.get("prediction_sha") or "")
    if not HEX64.fullmatch(prediction_sha):
        raise rt.RuntimeGateError("acceptance prediction SHA missing")
    route = str(receipt.get("model_route") or "")
    if route not in ALLOWED_ROUTES:
        raise rt.RuntimeGateError(f"acceptance route illegal:{route}")
    fallback = receipt.get("fallback_exact_v1") is True
    if fallback != (route == "FROZEN_V1_EXACT_FALLBACK"):
        raise rt.RuntimeGateError("acceptance route/fallback inconsistency")
    fixture = receipt.get("fixture_identity") or {}
    for key in ("competition_id", "season", "home_team_name", "away_team_name", "kickoff"):
        if str(fixture.get(key)) != str(expected.get(key)):
            raise rt.RuntimeGateError(f"acceptance fixture mismatch:{key}")
    matrix_sum = float(receipt.get("matrix_probability_sum"))
    if abs(matrix_sum - 1.0) > 1e-12:
        raise rt.RuntimeGateError("acceptance unified score matrix conservation failed")
    identity = receipt.get("identity_audit") or {}
    if identity.get("status") != "PASS" or identity.get("score_or_result_assisted_identity") is not False:
        raise rt.RuntimeGateError("acceptance identity anomaly")
    reconstruction = receipt.get("reconstruction_audit") or {}
    if any(reconstruction.get(k) is not True for k in ("target_fixture_excluded", "result_excluded", "post_kickoff_events_excluded")):
        raise rt.RuntimeGateError("acceptance reconstruction exclusion failed")
    current = reconstruction.get("current") or {}
    current_xg = reconstruction.get("current_xg") or {}
    if current.get("target_score_fields_read_before_eligibility_gate") not in (False, None):
        raise rt.RuntimeGateError("acceptance target score read sentinel failed")
    if current_xg.get("target_score_fields_read_before_eligibility_gate") not in (False, None):
        raise rt.RuntimeGateError("acceptance target xG/score read sentinel failed")
    weights = receipt.get("fusion_weights") or {}
    binding = receipt.get("formal_binding") or {}
    if type(weights) is not dict or type(binding) is not dict:
        raise rt.RuntimeGateError("acceptance current/formal binding missing")
    if float(weights.get("xg")) != float(binding.get("xg_weight")) or float(weights.get("v1")) != float(binding.get("frozen_v1_weight")):
        raise rt.RuntimeGateError("acceptance weight report/binding mismatch")
    if abs(float(weights["xg"]) + float(weights["v1"]) - 1.0) > 1e-12:
        raise rt.RuntimeGateError("acceptance weight conservation failed")
    return {
        "acceptance_label": expected["acceptance_label"],
        "competition_id": fixture["competition_id"],
        "fixture_id": fixture["fixture_id"],
        "kickoff": fixture["kickoff"],
        "fixture_resolved": True,
        "valid_receipt": True,
        "prediction_sha": prediction_sha,
        "model_route": route,
        "fallback_exact_v1": fallback,
        "result_excluded": True,
        "target_fixture_excluded": True,
        "post_kickoff_events_excluded": True,
        "strict_pit_claimed": False,
        "identity_status": "PASS",
        "state_integrity_status": "PASS",
        "matrix_conservation": True,
        "fusion_weights": {"xg": float(weights["xg"]), "v1": float(weights["v1"])},
        "runtime_current_sha256": binding.get("runtime_current_sha256"),
        "runtime_formal_head": binding.get("runtime_formal_head"),
        "formal_model_name": binding.get("model_name"),
        "receipt_sha": receipt.get("receipt_sha"),
        "receipt_artifact_binding_required": receipt.get("receipt_artifact_binding_required"),
        "actual_scores_emitted": False,
    }


def run_manifest(manifest: dict[str, Any], understat_db: Path, confirmation_dir: Path, work: Path) -> dict[str, Any]:
    # Cache byte-identical acquisition responses across the batch; this changes transport cost only.
    original_fetch = live._fetch
    cache: dict[tuple[str, bytes, tuple[tuple[str, str], ...]], tuple[bytes, str]] = {}
    def cached_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 60):
        key = (url, data or b"", tuple(sorted((headers or {}).items())))
        if key not in cache:
            cache[key] = original_fetch(url, data=data, headers=headers, timeout=timeout)
        return cache[key]
    live._fetch = cached_fetch

    rows = list(manifest["four_fixture_resolution"]) + list(manifest["eight_domain_resolution"])
    results: list[dict[str, Any]] = []
    try:
        for i, target in enumerate(rows):
            kickoff = rt._parse_dt(target["kickoff"], "acceptance target kickoff")
            req = {
                "schema_version": request_contract.SCHEMA,
                "mode": MODE,
                "request_id": f"current-v2-retrospective-acceptance-{i:02d}-{target['fixture_id'][:16]}",
                "match": {
                    "competition_id": target["competition_id"],
                    "season": target["season"],
                    "home_team_name": target["home_team_name"],
                    "away_team_name": target["away_team_name"],
                    "kickoff": kickoff.isoformat(),
                    "cutoff": (kickoff - timedelta(minutes=60)).isoformat(),
                },
            }
            validated = request_contract.validate_request(req, carrier_request=False)
            execution = request_contract.execution_request(validated)
            case = work / f"{i:02d}-{target['acceptance_label']}"
            out = case / "out"; state = case / "state"
            out.mkdir(parents=True, exist_ok=True); state.mkdir(parents=True, exist_ok=True)
            result = gateway.normal_request(execution, state, out, ROOT, understat_db, confirmation_dir)
            receipt_path = out / "prediction_receipt.json"
            if not receipt_path.exists():
                raise rt.RuntimeGateError(f"acceptance receipt missing:{target['acceptance_label']}")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            checked = _assert_receipt(receipt, result, target)
            checked["receipt_path"] = str(receipt_path.relative_to(work))
            results.append(checked)
    finally:
        live._fetch = original_fetch

    four_results = results[:4]
    batch_results = results[4:]
    by_domain = {x["competition_id"]: x for x in batch_results}
    if set(by_domain) != set(DOMAINS):
        raise rt.RuntimeGateError("eight-domain acceptance coverage mismatch")
    if len(four_results) != 4:
        raise rt.RuntimeGateError("four-fixture acceptance coverage mismatch")

    current_shas = {str(x["runtime_current_sha256"]) for x in results}
    formal_heads = {str(x["runtime_formal_head"]) for x in results}
    weights = {json.dumps(x["fusion_weights"], sort_keys=True) for x in results}
    if len(current_shas) != 1 or len(formal_heads) != 1 or len(weights) != 1:
        raise rt.RuntimeGateError("acceptance CURRENT/formal/weight batch drift")

    summary = {
        "schema_version": SCHEMA,
        "status": "PASS",
        "mode": MODE,
        "candidate_exact_head": resolve_candidate_exact_head(),
        "four_fixture": four_results,
        "eight_domain": batch_results,
        "gates": {
            "four_fixture_count": len(four_results),
            "eight_domain_count": len(batch_results),
            "fixture_resolved_percent": 100.0,
            "valid_receipt_percent": 100.0,
            "nonempty_prediction_sha_percent": 100.0,
            "result_target_post_kickoff_exclusion_percent": 100.0,
            "identity_anomaly_count": 0,
            "state_anomaly_count": 0,
            "matrix_inconsistency_count": 0,
            "actual_scores_emitted": False,
        },
        "actual_current_sha256": next(iter(current_shas)),
        "actual_formal_model_head": next(iter(formal_heads)),
        "actual_fusion_weights": json.loads(next(iter(weights))),
        "source_fetch_cache_entries": len(cache),
        "ucl_research_only_scope_exception_only": True,
        "formal_scope_widened": False,
    }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    work = Path(args.work).resolve(); work.mkdir(parents=True, exist_ok=True)
    upper = _now()
    manifest = discover(upper)
    (work / "fixture_manifest.json").write_bytes(canon(manifest))
    summary = run_manifest(manifest, Path(args.understat_db).resolve(), Path(args.confirmation_dir).resolve(), work)
    summary["fixture_manifest_sha256"] = sha(manifest)
    (work / "acceptance_summary.json").write_bytes(canon(summary))
    print(json.dumps({"status": summary["status"], "candidate_exact_head": summary["candidate_exact_head"], "gates": summary["gates"], "actual_fusion_weights": summary["actual_fusion_weights"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
