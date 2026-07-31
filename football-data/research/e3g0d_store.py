"""Append-only PIT evidence store."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from e3g0d_common import (
    SAFE_HEADERS,
    SCHEMA,
    STATUS,
    clean_params,
    iso,
    kickoff_id,
    packed,
    parse_utc,
    raw_write,
    sha,
    slug,
    xwrite,
)


def response_ids(payload: Mapping[str, Any]) -> set[int]:
    output: set[int] = set()
    rows = payload.get("response")
    if not isinstance(rows, list):
        return output
    for row in rows:
        if not isinstance(row, dict):
            continue
        fixture = row.get("fixture")
        if isinstance(fixture, dict) and fixture.get("id") is not None:
            output.add(int(fixture["id"]))
    return output


class Store:
    def __init__(
        self,
        root: Path | str,
        head: str,
        run_id: str,
        retention: int,
        expires: str,
        request_day: str,
        target_date: str,
        selected_plan: Mapping[str, Any] | None = None,
    ) -> None:
        self.root = Path(root)
        self.head = head or "LOCAL_UNCOMMITTED"
        self.run_id = run_id or "LOCAL"
        self.retention = int(retention)
        self.expires = expires
        self.request_day = request_day
        self.target_date = target_date
        self.selected_plan = dict(selected_plan or {})
        self.sequence = 0

    def event_id(self, observed: Any, endpoint: str, digest: str) -> str:
        self.sequence += 1
        return (
            f"{observed.strftime('%Y%m%dT%H%M%S%fZ')}__{slug(endpoint)}__"
            f"{self.run_id}__{self.sequence:04d}__{digest[:16]}"
        )

    def save(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        raw: bytes,
        payload: Mapping[str, Any],
        requested: Any,
        observed: Any,
        status: int,
        headers: Mapping[str, str],
        role: str,
        fixtures: Sequence[Mapping[str, Any]] = (),
        labels: Sequence[str] = (),
        final_candidate: bool = False,
    ) -> dict[str, Any]:
        digest = sha(raw)
        day_path = observed.strftime("%Y/%m/%d")
        endpoint_slug = slug(endpoint)
        raw_rel = Path("raw") / day_path / endpoint_slug / f"sha256_{digest}.json"
        newly_written = raw_write(self.root / raw_rel, raw)
        event = self.event_id(observed, endpoint, digest)
        ids = response_ids(payload)
        records: list[dict[str, Any]] = []

        for fixture in fixtures:
            fixture_id = int(fixture["fixture_id"])
            kickoff = parse_utc(fixture["scheduled_kickoff_utc"])
            pre_kickoff = observed < kickoff
            if endpoint == "fixtures/lineups" and len(fixtures) == 1:
                present = bool(payload.get("response"))
            else:
                present = fixture_id in ids
            if present:
                data_status, missing_reason = "PRESENT", None
            elif payload.get("response") == []:
                data_status = "MISSING_UNINTERPRETED"
                missing_reason = "provider_empty_response_not_equivalent_to_no_injury_or_no_lineup"
            else:
                data_status = "MISSING_UNMAPPED"
                missing_reason = "fixture_not_present_in_provider_response"

            record = {
                "schema_version": SCHEMA,
                "deployment_status": STATUS,
                "provider": "API-Football",
                "competition_id": int(fixture["competition_id"]),
                "season_id": int(fixture["season_id"]),
                "fixture_id": fixture_id,
                "home_team_id": int(fixture["home_team_id"]),
                "away_team_id": int(fixture["away_team_id"]),
                "scheduled_kickoff_utc": iso(kickoff),
                "kickoff_version_id": kickoff_id(fixture),
                "provider_updated_at": fixture.get("provider_updated_at"),
                "requested_at_utc": iso(requested),
                "observed_at_utc": iso(observed),
                "request_day_utc": self.request_day,
                "target_date_utc": self.target_date,
                "request_endpoint_type": endpoint,
                "raw_response_sha256": digest,
                "raw_response_path": raw_rel.as_posix(),
                "run_head": self.head,
                "workflow_run_id": self.run_id,
                "data_status": data_status,
                "missing_reason": missing_reason,
                "is_pre_kickoff": pre_kickoff,
                "is_final_pre_kickoff_candidate": bool(final_candidate and pre_kickoff),
                "is_final_pre_kickoff_freeze_version": False,
                "final_freeze_rule": (
                    "post-kickoff local finalizer selects latest observation strictly before same kickoff version"
                ),
                "target_labels": sorted(set(labels)),
                "selected_plan_sha256": self.selected_plan.get("plan_sha256"),
                "selected_plan_artifact_id": self.selected_plan.get("plan_artifact_id"),
                "selected_plan_source_raw_sha256": self.selected_plan.get("source_raw_response_sha256"),
                "append_only": True,
                "formal_weight": 0,
            }
            record_rel = (
                Path("records") / str(fixture_id) / endpoint_slug / f"{event}.json"
            )
            xwrite(self.root / record_rel, packed(record) + b"\n")
            record["record_path"] = record_rel.as_posix()
            records.append(record)

        manifest = {
            "schema_version": SCHEMA,
            "deployment_status": STATUS,
            "provider": "API-Football",
            "request_endpoint_type": endpoint,
            "role": role,
            "request": {"params": clean_params(params)},
            "request_day_utc": self.request_day,
            "target_date_utc": self.target_date,
            "requested_at_utc": iso(requested),
            "observed_at_utc": iso(observed),
            "http_status": int(status),
            "safe_response_headers": {
                str(key).lower(): str(value)
                for key, value in headers.items()
                if str(key).lower() in SAFE_HEADERS
            },
            "raw_response_sha256": digest,
            "raw_response_path": raw_rel.as_posix(),
            "raw_blob_newly_written": newly_written,
            "provider_results": payload.get("results")
            if isinstance(payload.get("results"), int)
            else None,
            "records": records,
            "run_head": self.head,
            "workflow_run_id": self.run_id,
            "selected_plan_sha256": self.selected_plan.get("plan_sha256"),
            "selected_plan_artifact_id": self.selected_plan.get("plan_artifact_id"),
            "selected_plan_source_raw_sha256": self.selected_plan.get("source_raw_response_sha256"),
            "artifact_retention_days": self.retention,
            "artifact_expires_at_utc": self.expires,
            "append_only": True,
            "formal_weight": 0,
        }
        manifest_rel = Path("manifests") / day_path / f"{event}.manifest.json"
        xwrite(self.root / manifest_rel, packed(manifest) + b"\n")
        return {
            "endpoint": endpoint,
            "observed_at_utc": iso(observed),
            "sha256": digest,
            "raw_path": raw_rel.as_posix(),
            "manifest_path": manifest_rel.as_posix(),
            "record_count": len(records),
        }
