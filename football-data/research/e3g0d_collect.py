"""Fixture identity, immutable plan, and collection operations."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from e3g0d_common import (
    E3Error,
    PLAN_SCHEMA,
    STATUS,
    TARGETS,
    iso,
    packed,
    parse_utc,
    sha,
    utc_now,
    xwrite,
)


def provider_updated_at(row: Mapping[str, Any]) -> str | None:
    candidates = (
        row.get("update"),
        row.get("updated_at"),
        (row.get("fixture") or {}).get("updated_at"),
    )
    for value in candidates:
        if value:
            try:
                return iso(parse_utc(value))
            except E3Error:
                return str(value)
    return None


def fixture_rows(
    payload: Mapping[str, Any],
    league: int,
    season: int,
    target_date: str,
    *,
    clock=utc_now,
) -> list[dict[str, Any]]:
    rows = payload.get("response")
    if not isinstance(rows, list):
        raise E3Error("IDENTITY_MAPPING_FAILED", "fixtures response is not a list")
    output: list[dict[str, Any]] = []
    current = clock()
    for row in rows:
        if not isinstance(row, dict):
            raise E3Error("IDENTITY_MAPPING_FAILED", "invalid fixture row")
        fixture = row.get("fixture") or {}
        league_row = row.get("league") or {}
        teams = row.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        required = (
            fixture.get("id"),
            fixture.get("date"),
            league_row.get("id"),
            league_row.get("season"),
            home.get("id"),
            away.get("id"),
        )
        if any(value is None for value in required):
            raise E3Error("IDENTITY_MAPPING_FAILED", "fixture identity mapping failed")
        if int(league_row["id"]) != league or int(league_row["season"]) != season:
            raise E3Error("IDENTITY_MAPPING_FAILED", "competition or season mismatch")
        kickoff = parse_utc(fixture["date"])
        if (
            kickoff.date().isoformat() != target_date
            or kickoff < current - timedelta(hours=2)
            or kickoff > current + timedelta(days=400)
        ):
            raise E3Error("VALIDATION_FAILED", "abnormal kickoff time")
        output.append(
            {
                "provider": "API-Football",
                "competition_id": league,
                "season_id": season,
                "fixture_id": int(fixture["id"]),
                "home_team_id": int(home["id"]),
                "away_team_id": int(away["id"]),
                "home_team_name": home.get("name"),
                "away_team_name": away.get("name"),
                "scheduled_kickoff_utc": iso(kickoff),
                "provider_updated_at": provider_updated_at(row),
                "status_short": (fixture.get("status") or {}).get("short"),
            }
        )
    return sorted(output, key=lambda item: (item["scheduled_kickoff_utc"], item["fixture_id"]))


def plan_hash_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in plan.items()
        if key not in {"plan_sha256", "plan_artifact_id"}
    }


def compute_plan_sha256(plan: Mapping[str, Any]) -> str:
    return sha(packed(plan_hash_payload(plan)))


def api_save(
    client: Any,
    store: Any,
    endpoint: str,
    params: Mapping[str, Any],
    role: str,
    fixtures: Sequence[Mapping[str, Any]] = (),
    labels: Sequence[str] = (),
    final_candidate: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, payload, requested, observed, status, headers = client.get(endpoint, params)
    return (
        store.save(
            endpoint,
            params,
            raw,
            payload,
            requested,
            observed,
            status,
            headers,
            role,
            fixtures,
            labels,
            final_candidate,
        ),
        payload,
    )


def build_plan(
    client: Any,
    store: Any,
    league: int,
    season: int,
    target_date: str,
    timezone_name: str,
    fixture_limit: int,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    params = {
        "league": league,
        "season": season,
        "date": target_date,
        "timezone": timezone_name,
    }
    raw, payload, requested, observed, status, headers = client.get("fixtures", params)
    fixtures = fixture_rows(payload, league, season, target_date)[:fixture_limit]
    snapshot = store.save(
        "fixtures",
        params,
        raw,
        payload,
        requested,
        observed,
        status,
        headers,
        "daily_fixture_plan_source",
        fixtures,
    )
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "deployment_status": STATUS,
        "provider": "API-Football",
        "competition_id": league,
        "season_id": season,
        "target_date_utc": target_date,
        "request_day_utc": store.request_day,
        "timezone": timezone_name,
        "created_at_utc": snapshot["observed_at_utc"],
        "fixtures": fixtures,
        "fixture_count": len(fixtures),
        "source_raw_response_sha256": snapshot["sha256"],
        "source_manifest_path": snapshot["manifest_path"],
        "run_head": store.head,
        "workflow_run_id": store.run_id,
        "plan_artifact_id": None,
        "append_only": True,
        "formal_weight": 0,
    }
    plan["plan_sha256"] = compute_plan_sha256(plan)
    plan_path = (
        store.root
        / "plans"
        / (
            f"{target_date}__league_{league}__season_{season}__"
            f"sha256_{plan['plan_sha256']}.json"
        )
    )
    xwrite(plan_path, packed(plan) + b"\n")
    return plan, snapshot, plan_path


def _bundle_root(plan_path: Path) -> Path:
    parent = plan_path.parent
    if parent.name != "plans":
        raise E3Error("VALIDATION_FAILED", "plan is not located in a plans directory")
    return parent.parent


def load_plan(
    plan_path: str | Path,
    league: int,
    season: int,
    target_date: str,
    fixture_limit: int,
    *,
    expected_plan_sha256: str,
    expected_plan_artifact_id: int,
    expected_source_raw_sha256: str,
    expected_run_head: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    path = Path(plan_path)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise E3Error("VALIDATION_FAILED", "unable to load fixture plan") from exc
    if not isinstance(plan, dict):
        raise E3Error("VALIDATION_FAILED", "fixture plan is not an object")
    actual_sha = compute_plan_sha256(plan)
    recorded_sha = str(plan.get("plan_sha256") or "")
    if not recorded_sha or actual_sha != recorded_sha or actual_sha != expected_plan_sha256:
        raise E3Error("VALIDATION_FAILED", "fixture plan SHA-256 mismatch")
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or int(plan.get("competition_id", -1)) != league
        or int(plan.get("season_id", -1)) != season
        or plan.get("target_date_utc") != target_date
        or plan.get("run_head") != expected_run_head
        or plan.get("source_raw_response_sha256") != expected_source_raw_sha256
    ):
        raise E3Error("IDENTITY_MAPPING_FAILED", "fixture plan identity mismatch")
    fixtures = plan.get("fixtures")
    if not isinstance(fixtures, list):
        raise E3Error("IDENTITY_MAPPING_FAILED", "invalid fixture plan")
    selected = fixtures[:fixture_limit]
    required = {
        "competition_id",
        "season_id",
        "fixture_id",
        "home_team_id",
        "away_team_id",
        "scheduled_kickoff_utc",
    }
    if any(not isinstance(item, dict) or not required.issubset(item) for item in selected):
        raise E3Error("IDENTITY_MAPPING_FAILED", "plan mapping failed")

    root = _bundle_root(path)
    manifest_rel = plan.get("source_manifest_path")
    if not manifest_rel:
        raise E3Error("VALIDATION_FAILED", "plan source manifest path missing")
    manifest_path = root / str(manifest_rel)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise E3Error("VALIDATION_FAILED", "plan source manifest unavailable") from exc
    raw_rel = manifest.get("raw_response_path")
    manifest_sha = manifest.get("raw_response_sha256")
    if manifest_sha != expected_source_raw_sha256 or not raw_rel:
        raise E3Error("VALIDATION_FAILED", "plan source manifest SHA chain mismatch")
    try:
        raw = (root / str(raw_rel)).read_bytes()
    except OSError as exc:
        raise E3Error("VALIDATION_FAILED", "plan source raw response unavailable") from exc
    if sha(raw) != expected_source_raw_sha256:
        raise E3Error("VALIDATION_FAILED", "plan source raw SHA-256 mismatch")

    selected_identity = {
        "plan_sha256": actual_sha,
        "plan_artifact_id": int(expected_plan_artifact_id),
        "source_raw_response_sha256": expected_source_raw_sha256,
        "plan_run_head": expected_run_head,
    }
    return plan, [dict(item) for item in selected], selected_identity


def due(
    fixtures: Sequence[Mapping[str, Any]], observed: Any, tolerance: int
) -> dict[str, list[dict[str, Any]]]:
    if not 0 <= int(tolerance) <= 15:
        raise E3Error("VALIDATION_FAILED", "invalid due-window tolerance")
    output = {key: [] for key in TARGETS}
    for fixture in fixtures:
        minutes = (
            parse_utc(fixture["scheduled_kickoff_utc"]) - observed
        ).total_seconds() / 60
        for label, target in TARGETS.items():
            if target - tolerance <= minutes <= target + tolerance:
                output[label].append(dict(fixture))
    return output


def collect_odds(
    client: Any,
    store: Any,
    league: int,
    season: int,
    target_date: str,
    timezone_name: str,
    fixtures: Sequence[Mapping[str, Any]],
    role: str,
    labels: Sequence[str] = (),
    final_candidate: bool = False,
) -> list[dict[str, Any]]:
    snapshot, _ = api_save(
        client,
        store,
        "odds",
        {
            "league": league,
            "season": season,
            "date": target_date,
            "timezone": timezone_name,
            "page": 1,
        },
        role,
        fixtures,
        labels,
        final_candidate,
    )
    return [snapshot]


def collect_injuries(
    client: Any,
    store: Any,
    fixtures: Sequence[Mapping[str, Any]],
    role: str,
    labels: Sequence[str] = (),
    final_candidate: bool = False,
) -> list[dict[str, Any]]:
    fixture_ids = [int(item["fixture_id"]) for item in fixtures]
    if not fixture_ids:
        return []
    if len(fixture_ids) > 20:
        raise E3Error("VALIDATION_FAILED", "injury batch exceeds 20 fixtures")
    snapshot, _ = api_save(
        client,
        store,
        "injuries",
        {"ids": "-".join(map(str, fixture_ids)), "timezone": "UTC"},
        role,
        fixtures,
        labels,
        final_candidate,
    )
    return [snapshot]


def collect_lineups(
    client: Any,
    store: Any,
    fixtures: Sequence[Mapping[str, Any]],
    label: str,
) -> list[dict[str, Any]]:
    output = []
    for fixture in fixtures:
        snapshot, _ = api_save(
            client,
            store,
            "fixtures/lineups",
            {"fixture": int(fixture["fixture_id"])},
            "pre_kickoff_lineup_poll",
            [fixture],
            [label],
            label == "T-15m",
        )
        output.append(snapshot)
    return output
