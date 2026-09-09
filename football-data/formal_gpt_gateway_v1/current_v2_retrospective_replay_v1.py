#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import runtime as rt

SCHEMA = "football3-current-v2-retrospective-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
UCL = "UEFA_ChampionsLeague"
RESEARCH_SCOPE = frozenset({
    "ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA",
    "FRA_Ligue1", UCL, "JPN_J1", "KOR_KLeague1",
})
UCL_REGISTRY = "football-data/config/ucl_2026_27_league_phase_identity_v1.json"
FORMAL_POINTER = "football-data/config/formal_model_pointer_historical_xg_fusion_v2.json"


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise rt.RuntimeGateError(f"retrospective replay JSON invalid: {path}") from exc
    if type(obj) is not dict:
        raise rt.RuntimeGateError(f"retrospective replay JSON object required: {path}")
    return obj


def _formal_binding(repo_root: Path) -> dict[str, Any]:
    """Read the current formal pointer/model semantics; never define replacement weights here."""
    pointer_path = repo_root / FORMAL_POINTER
    pointer = _read_json(pointer_path)
    model = pointer.get("model")
    if type(model) is not dict:
        raise rt.RuntimeGateError("current formal pointer model contract missing")
    try:
        xg_weight = float(model["xg_weight"])
        v1_weight = float(model["frozen_v1_weight"])
        runtime_xg_weight = float(rt.formal_v2.FUSION_WEIGHT)
    except (KeyError, TypeError, ValueError) as exc:
        raise rt.RuntimeGateError("current formal fusion weight contract invalid") from exc
    if abs((xg_weight + v1_weight) - 1.0) > 1e-12:
        raise rt.RuntimeGateError("current formal fusion weights do not conserve probability")
    if abs(runtime_xg_weight - xg_weight) > 1e-12:
        raise rt.RuntimeGateError("current formal pointer/runtime fusion weight mismatch")
    scope = pointer.get("formal_scope")
    if type(scope) is not list or tuple(scope) != tuple(rt.FORMAL_SCOPE):
        raise rt.RuntimeGateError("current formal pointer/runtime scope mismatch")
    return {
        "authority": pointer.get("authority"),
        "model_name": model.get("name"),
        "formula": model.get("formula"),
        "xg_weight": xg_weight,
        "frozen_v1_weight": v1_weight,
        "xg_insufficient": model.get("xg_insufficient"),
        "pointer_sha256": _file_sha(pointer_path),
        "runtime_formal_head": str(rt.FORMAL_HEAD),
        "runtime_current_sha256": str(rt.CURRENT_SHA256),
        "runtime_model_module": str(getattr(rt.formal_v2, "__file__", "")),
        "resolved_from_runtime_and_pointer": True,
    }


def _safe_history_upper(target_kickoff: datetime) -> datetime:
    # Repository/live result transports used by Frozen V1 normalize many fixtures to
    # UTC calendar dates.  Excluding the entire target UTC date is conservative but
    # mechanically prevents target/same-day/post-kickoff labels from entering state.
    t = target_kickoff.astimezone(timezone.utc)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def _ucl_registry(repo_root: Path) -> dict[str, Any]:
    reg = _read_json(repo_root / UCL_REGISTRY)
    if reg.get("competition_id") != UCL or reg.get("team_count") != 36:
        raise rt.RuntimeGateError("UCL 36-team authority contract invalid")
    teams = reg.get("teams")
    if type(teams) is not list or len(teams) != 36:
        raise rt.RuntimeGateError("UCL 36-team authority roster invalid")
    return reg


def _exact_key(value: str) -> str:
    return rt._normalize_team(str(value or ""))


def _resolve_ucl_team(repo_root: Path, raw: str) -> tuple[str, dict[str, Any]]:
    token = _exact_key(raw)
    hits: list[dict[str, Any]] = []
    for row in _ucl_registry(repo_root)["teams"]:
        accepted = row.get("accepted_exact_names") or []
        if token in {_exact_key(x) for x in accepted}:
            hits.append(row)
    if len(hits) != 1:
        raise rt.RuntimeGateError(f"UCL exact identity unresolved or ambiguous: {raw}")
    row = hits[0]
    canonical = str(row["uefa_name"])
    return canonical, {
        "authority": UCL_REGISTRY,
        "canonical_name": canonical,
        "association_code": row.get("association_code"),
        "domestic_formal_competition_id": row.get("domestic_formal_competition_id"),
        "fuzzy_matching_used": False,
        "result_or_score_used": False,
    }


def _resolve_target(repo_root: Path, req: dict[str, Any]) -> tuple[rt.hxg.FixtureRow, dict[str, Any], datetime]:
    m = req.get("match")
    if type(m) is not dict:
        raise rt.RuntimeGateError("retrospective replay requires match object")
    comp = str(m.get("competition_id") or "")
    if comp not in RESEARCH_SCOPE:
        raise rt.RuntimeGateError("competition outside CURRENT V2 retrospective research scope")
    season = str(m.get("season") or "").strip()
    home_raw = str(m.get("home_team_name") or "").strip()
    away_raw = str(m.get("away_team_name") or "").strip()
    kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "target kickoff")
    if not season or not home_raw or not away_raw:
        raise rt.RuntimeGateError("retrospective target identity incomplete")
    if comp == UCL:
        home, home_audit = _resolve_ucl_team(repo_root, home_raw)
        away, away_audit = _resolve_ucl_team(repo_root, away_raw)
    else:
        aliases = rt._read_aliases(repo_root)
        home = rt._canonical_team(comp, home_raw, aliases)
        away = rt._canonical_team(comp, away_raw, aliases)
        home_audit = {"canonical_name": home, "authority": "formal_team_alias_contract", "fuzzy_matching_used": False, "result_or_score_used": False}
        away_audit = {"canonical_name": away, "authority": "formal_team_alias_contract", "fuzzy_matching_used": False, "result_or_score_used": False}
    if not home or not away or _exact_key(home) == _exact_key(away):
        raise rt.RuntimeGateError("retrospective target canonical identity invalid")
    fixture_id = rt._fixture_id(comp, season, kickoff, home, away)
    fixture = rt.hxg.FixtureRow(
        fixture_id, comp, season, kickoff,
        rt._global_team_id(home), rt._global_team_id(away), home, away,
    )
    identity = {
        "status": "PASS",
        "competition_id": comp,
        "season": season,
        "home": home_audit,
        "away": away_audit,
        "target_fixture_id": fixture_id,
        "fixture_time_preserved": True,
        "score_or_result_assisted_identity": False,
    }
    return fixture, identity, kickoff


def _research_v1_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], dict[str, Any]]:
    """Acquire only labels strictly earlier than a conservative pre-target boundary.

    This intentionally does not call acquire_verified_delta: current observation may
    be later than the historical target in research-only mode.  The current source
    content is used only to reconstruct pre-target event state.
    """
    rows, report = live.acquire_v1(repo_root, lower, upper)
    checked = [r for r in rows if r.kickoff < upper]
    if len(checked) != len(rows):
        raise rt.RuntimeGateError("research V1 source returned row at/after history boundary")
    return checked, {
        "status": "COMPLETE",
        "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION",
        "strict_pit_claimed": False,
        "from": lower.isoformat(),
        "to_exclusive": upper.isoformat(),
        "rows": len(checked),
        "acquisition": report,
    }


def _history_fixture(r: live.V1Row) -> rt.HistoryFixture:
    return rt.HistoryFixture(
        r.fixture_id, r.competition_id, r.season, r.kickoff,
        r.home_team_id, r.away_team_id, r.home_team_name, r.away_team_name,
        r.home_goals, r.away_goals, r.source, r.source_sha256,
    )


def _current_xg_labels(rows: list[live.V1Row], lower: datetime, upper: datetime,
                       base_state: Any) -> tuple[dict[str, rt.XGLabel], dict[str, Any]]:
    if not rows:
        return {}, {"status": "COMPLETE", "joined_results": 0, "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION"}
    xg_map, _freezes, report = live.acquire_xg(rows, lower, upper, datetime.now(timezone.utc), base_state)
    by_id = {r.fixture_id: r for r in rows}
    out: dict[str, rt.XGLabel] = {}
    for fid, x in xg_map.items():
        r = by_id.get(fid)
        if r is None:
            raise rt.RuntimeGateError("research XG/V1 identity mismatch")
        # This is explicitly a research replay release adapter.  Observation now is
        # disclosed separately and is never claimed to be contemporaneous PIT time.
        release = r.kickoff + timedelta(hours=3)
        source_sha = str(x.get("source_sha256") or "")
        if not source_sha:
            raise rt.RuntimeGateError("research XG source identity missing")
        label = rt.hxg.ReleasedLabel(r.home_goals, r.away_goals, float(x["home_xg"]), float(x["away_xg"]), release)
        out[fid] = rt.XGLabel(label, fid, source_sha, r.kickoff.isoformat())
    return out, {
        "status": "COMPLETE",
        "joined_results": len(out),
        "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION",
        "strict_pit_claimed": False,
        "research_release_adapter": "kickoff_plus_3h",
        "acquisition": report,
    }


def _ucl_history(repo_root: Path, upper: datetime) -> tuple[list[rt.HistoryFixture], dict[str, Any]]:
    """Read UCL repository history with the date gate evaluated before score fields."""
    directory = repo_root / "football-data" / "processed" / UCL
    if not directory.is_dir():
        raise rt.RuntimeGateError("UCL processed history directory missing")
    registry = _ucl_registry(repo_root)
    accepted: dict[str, str] = {}
    for row in registry["teams"]:
        canonical = str(row["uefa_name"])
        for name in row.get("accepted_exact_names") or []:
            key = _exact_key(str(name))
            prior = accepted.get(key)
            if prior is not None and prior != canonical:
                raise rt.RuntimeGateError("UCL accepted identity collision")
            accepted[key] = canonical
    rows: list[rt.HistoryFixture] = []
    files: dict[str, Any] = {}
    seen: set[str] = set()
    for path in sorted(directory.glob("*.csv")):
        source_sha = _file_sha(path)
        used = 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                if not season or not date_raw:
                    continue
                kickoff = rt._parse_match_date(date_raw, season)
                # Critical trust boundary: target-date and later rows are rejected
                # before FTHG/FTAG are accessed.
                if kickoff >= upper:
                    continue
                home_raw = str(raw.get("HomeTeam") or raw.get("home_team") or "").strip()
                away_raw = str(raw.get("AwayTeam") or raw.get("away_team") or "").strip()
                home = accepted.get(_exact_key(home_raw), home_raw)
                away = accepted.get(_exact_key(away_raw), away_raw)
                if not home or not away or _exact_key(home) == _exact_key(away):
                    raise rt.RuntimeGateError("UCL historical identity invalid")
                try:
                    hg = int(str(raw.get("FTHG") or raw.get("home_goals") or "").strip())
                    ag = int(str(raw.get("FTAG") or raw.get("away_goals") or "").strip())
                except ValueError as exc:
                    raise rt.RuntimeGateError("UCL historical score invalid") from exc
                if hg < 0 or ag < 0 or hg > 30 or ag > 30:
                    raise rt.RuntimeGateError("UCL historical score outside formal bounds")
                fid = rt._fixture_id(UCL, season, kickoff, home, away)
                if fid in seen:
                    raise rt.RuntimeGateError("duplicate UCL retrospective history fixture")
                seen.add(fid)
                rows.append(rt.HistoryFixture(
                    fid, UCL, season, kickoff, rt._global_team_id(home), rt._global_team_id(away),
                    home, away, hg, ag, str(path.relative_to(repo_root)), source_sha,
                ))
                used += 1
        if used:
            files[str(path.relative_to(repo_root))] = {"sha256": source_sha, "used_rows": used}
    rows.sort(key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    if not rows:
        raise rt.RuntimeGateError("UCL retrospective history unavailable")
    return rows, {"status": "COMPLETE", "rows": len(rows), "files": files, "to_exclusive": upper.isoformat()}


def _build_research_state(repo_root: Path, understat_db: Path, confirmation_dir: Path,
                          target: rt.hxg.FixtureRow, target_kickoff: datetime) -> tuple[Any, dict[str, Any]]:
    frozen, frozen_source = rt.load_frozen_v1_history(repo_root)
    frozen_xg, frozen_xg_source = rt.load_xg_labels(frozen, understat_db, confirmation_dir)
    base_cutoff = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff")
    history_upper = _safe_history_upper(target_kickoff)
    if history_upper <= base_cutoff:
        state, replay = rt.replay_history_state(frozen, frozen_xg, history_upper)
        return state, {
            "frozen": frozen_source, "frozen_xg": frozen_xg_source, "current": None,
            "ucl": None, "replay": replay, "history_upper_exclusive": history_upper.isoformat(),
            "target_fixture_excluded": True, "result_excluded": True, "post_kickoff_events_excluded": True,
        }

    base_state, _ = rt.replay_history_state(frozen, frozen_xg, base_cutoff)
    current_rows, current_report = _research_v1_rows(repo_root, base_cutoff, history_upper)
    current_xg, current_xg_report = _current_xg_labels(current_rows, base_cutoff, history_upper, base_state)
    current_history = [_history_fixture(r) for r in current_rows]

    ucl_rows: list[rt.HistoryFixture] = []
    ucl_report = None
    if target.competition_id == UCL:
        ucl_rows, ucl_report = _ucl_history(repo_root, history_upper)

    combined = list(frozen) + current_history + ucl_rows
    by_id: dict[str, rt.HistoryFixture] = {}
    for row in combined:
        prior = by_id.get(row.fixture_id)
        if prior is not None and prior != row:
            raise rt.RuntimeGateError("retrospective history fixture identity collision")
        by_id[row.fixture_id] = row
    combined = sorted(by_id.values(), key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    if any(r.kickoff >= history_upper for r in combined):
        raise rt.RuntimeGateError("retrospective history contains target-date/post-target fixture")
    if target.fixture_id in by_id:
        raise rt.RuntimeGateError("target fixture entered retrospective history")

    labels = dict(frozen_xg)
    for fid, label in current_xg.items():
        if fid in labels:
            raise rt.RuntimeGateError("retrospective XG label identity collision")
        labels[fid] = label
    state, replay = rt.replay_history_state(combined, labels, target_kickoff)
    return state, {
        "frozen": frozen_source,
        "frozen_xg": frozen_xg_source,
        "current": current_report,
        "current_xg": current_xg_report,
        "ucl": ucl_report,
        "replay": replay,
        "history_fixture_n": len(combined),
        "history_upper_exclusive": history_upper.isoformat(),
        "target_kickoff": target_kickoff.isoformat(),
        "conservative_target_utc_date_exclusion": True,
        "target_fixture_excluded": True,
        "result_excluded": True,
        "post_kickoff_events_excluded": True,
        "same_kickoff_unupdated": True,
        "strict_pit_claimed": False,
    }


def _state_integrity(state: Any, target: rt.hxg.FixtureRow, identity: dict[str, Any]) -> dict[str, Any]:
    local_home = state.base.teams_local.get((target.competition_id, target.home_team_id))
    local_away = state.base.teams_local.get((target.competition_id, target.away_team_id))
    global_home = state.base.teams_global.get(target.home_team_id)
    global_away = state.base.teams_global.get(target.away_team_id)
    evidence = {
        "home_local": bool(local_home is not None and float(local_home.weight) > 0),
        "away_local": bool(local_away is not None and float(local_away.weight) > 0),
        "home_global": bool(global_home is not None and float(global_home.weight) > 0),
        "away_global": bool(global_away is not None and float(global_away.weight) > 0),
    }
    # Cold starts remain a legal model condition.  Integrity fails only on identity,
    # target-leakage, or model execution invariants; it does not invent evidence.
    status = "PASS" if identity.get("status") == "PASS" else "DATA_STATE_ANOMALY"
    return {
        "status": status,
        "identity_status": identity.get("status"),
        "evidence": evidence,
        "cold_start_is_not_reclassified_as_anomaly": True,
        "target_result_used": False,
        "post_kickoff_state_used": False,
    }


def _top1(p_home: float, p_draw: float, p_away: float) -> str:
    return max(((p_home, "1"), (p_draw, "X"), (p_away, "2")), key=lambda x: (x[0], x[1]))[1]


def _over25(total_goals: dict[str, Any]) -> float:
    total = 0.0
    for key, value in total_goals.items():
        try:
            goals = int(key)
        except ValueError:
            continue
        if goals >= 3:
            total += float(value)
    return total


def run(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
        understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    target, identity, kickoff = _resolve_target(repo_root, req)
    formal_binding = _formal_binding(repo_root)
    state, reconstruction = _build_research_state(repo_root, understat_db, confirmation_dir, target, kickoff)
    predicted = rt._prediction_from_state(state, target)
    row = predicted["row"]
    pred = row["prediction"]
    audit = row["audit"]
    marg = predicted["marginals"]
    p_home = float(pred["p_home"])
    p_draw = float(pred["p_draw"])
    p_away = float(pred["p_away"])
    if abs((p_home + p_draw + p_away) - 1.0) > 1e-12:
        raise rt.RuntimeGateError("retrospective 1X2 probability conservation failed")
    matrix = pred["score_matrix"]
    matrix_sum = sum(float(c["probability"]) for c in matrix)
    if abs(matrix_sum - 1.0) > 1e-12:
        raise rt.RuntimeGateError("retrospective matrix conservation failed")
    integrity = _state_integrity(state, target, identity)
    if integrity["status"] != "PASS":
        raise rt.RuntimeGateError("DATA_STATE_ANOMALY")
    route = str(audit.get("route") or "")
    fallback = bool(audit.get("fallback_exact_v1"))
    if route not in {"FUSION_V2_ACTIVE", "FROZEN_V1_EXACT_FALLBACK"}:
        raise rt.RuntimeGateError(f"retrospective formal route invalid: {route}")
    if fallback != (route == "FROZEN_V1_EXACT_FALLBACK"):
        raise rt.RuntimeGateError("retrospective formal fallback/route mismatch")

    prediction_core = {
        "schema_version": SCHEMA,
        "mode": MODE,
        "classification": ["RETROSPECTIVE", "RESEARCH_ONLY", "NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE", "NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS"],
        "strict_pit_claimed": False,
        "fixture_identity": {
            "fixture_id": target.fixture_id,
            "competition_id": target.competition_id,
            "season": target.season,
            "kickoff": target.kickoff.isoformat(),
            "home_team_id": target.home_team_id,
            "away_team_id": target.away_team_id,
            "home_team_name": target.home_team_name,
            "away_team_name": target.away_team_name,
        },
        "model_route": route,
        "fallback_exact_v1": fallback,
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "top1": _top1(p_home, p_draw, p_away),
        "score_matrix": matrix,
        "top_scores": list(marg["top_scores"][:3]),
        "over_2_5": _over25(marg["total_goals"]),
        "btts": marg["btts"],
        "matrix_probability_sum": matrix_sum,
        "matrix_conservation": abs(matrix_sum - 1.0) <= 1e-12,
        "mu_home": pred.get("mu_home", "FORMAL_RECEIPT_NOT_DEFINED"),
        "mu_away": pred.get("mu_away", "FORMAL_RECEIPT_NOT_DEFINED"),
        "state_integrity_guard": integrity,
        "result_excluded": True,
        "target_fixture_excluded": True,
        "post_kickoff_events_excluded": True,
    }
    prediction_sha = _sha(prediction_core)
    receipt = dict(prediction_core)
    receipt.update({
        "prediction_sha": prediction_sha,
        "identity_audit": identity,
        "reconstruction_audit": reconstruction,
        "formal_binding": formal_binding,
        "fusion_weights": {"xg": formal_binding["xg_weight"], "v1": formal_binding["frozen_v1_weight"]},
        "source_observation_after_target_allowed_only_because_research_only": True,
        "manual_or_auxiliary_fallback_used": False,
        "formal_scope_widened": False,
        "ucl_research_only_scope_exception": target.competition_id == UCL,
    })
    receipt_sha = _sha(receipt)
    receipt["receipt_sha"] = receipt_sha
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction_receipt.json").write_bytes(_canon(receipt))
    (out / "current_v2_retrospective_replay_audit.json").write_bytes(_canon({
        "schema_version": SCHEMA,
        "status": "PASS",
        "prediction_sha": prediction_sha,
        "receipt_sha": receipt_sha,
        "identity": identity,
        "reconstruction": reconstruction,
        "formal_binding": formal_binding,
    }))
    return {
        "status": "PASS",
        "probe": MODE,
        "fixture": prediction_core["fixture_identity"],
        "cutoff": kickoff.isoformat(),
        "calculation_path": "CURRENT_V2_RETROSPECTIVE_RESEARCH_REBUILD",
        "model_route": route,
        "fallback_exact_v1": fallback,
        "prediction_sha": prediction_sha,
        "receipt_sha": receipt_sha,
        "strict_pit_claimed": False,
        "research_only": True,
        "result_excluded": True,
        "target_fixture_excluded": True,
        "post_kickoff_events_excluded": True,
        "matrix_conservation": True,
        "state_integrity_status": integrity["status"],
    }


def install(gateway_module) -> dict[str, Any]:
    """Install an outermost, mode-scoped research replay without changing formal paths."""
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        if str(req.get("request_mode") or "") != MODE:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        return run(req, state_root, out, repo_root, understat_db, confirmation_dir)

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "scope": sorted(RESEARCH_SCOPE),
        "classification": "RETROSPECTIVE_RESEARCH_ONLY",
        "strict_pit_claimed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
        "formal_scope_changed": False,
        "ucl_exception": "research-only entry only; 36-team authority reused",
        "current_pointer_or_model_or_weights_changed": False,
    }
