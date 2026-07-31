"""Read-only GitHub Artifact, pagination, quota, plan, and local archive primitives."""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from e3g0d_common import E3Error, PLAN_SCHEMA, iso, packed, parse_utc, sha, utc_now, xwrite
from e3g0d_collect import compute_plan_sha256

API = "https://api.github.com"
WORKFLOW = "football-research-e3g0d-api-football-forward-collector.yml"
ARCHIVE_SCHEMA = "E3G0D-LOCAL-ARCHIVE-1.1"
PER_PAGE = 100


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:
        return None


JsonLoader = Callable[[str], Mapping[str, Any]]
DownloadLoader = Callable[[int], bytes]


class GitHubReader:
    """Fail-closed read client with complete page traversal and optional test loaders."""

    def __init__(
        self,
        repo: str,
        token: str | None,
        timeout: float = 20,
        *,
        json_loader: JsonLoader | None = None,
        download_loader: DownloadLoader | None = None,
    ) -> None:
        if "/" not in repo:
            raise E3Error("VALIDATION_FAILED", "repository must be owner/name")
        if json_loader is None and not str(token or "").strip():
            raise E3Error("VALIDATION_FAILED", "GH_TOKEN is required for GitHub reads")
        self.repo = repo
        self.token = str(token or "").strip()
        self.timeout = float(timeout)
        self._json_loader = json_loader
        self._download_loader = download_loader

    def request(self, path: str) -> urllib.request.Request:
        url = f"{API}{path}"
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise E3Error("VALIDATION_FAILED", "GitHub API outside allowlist")
        return urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "FASHI188-e3g0d-archive/1.1",
            },
        )

    def json(self, path: str) -> dict[str, Any]:
        if self._json_loader is not None:
            try:
                value = self._json_loader(path)
            except E3Error:
                raise
            except Exception as exc:
                raise E3Error("PAGINATION_FAILED", "mock GitHub read failed") from exc
        else:
            try:
                with urllib.request.urlopen(self.request(path), timeout=self.timeout) as response:
                    value = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise E3Error("GITHUB_READ_FAILED", "GitHub resource not found") from None
                raise E3Error("GITHUB_READ_FAILED", f"GitHub API HTTP {exc.code}") from None
            except Exception as exc:
                raise E3Error("GITHUB_READ_FAILED", "GitHub API read failed") from exc
        if not isinstance(value, Mapping):
            raise E3Error("PAGINATION_FAILED", "unexpected GitHub payload")
        return dict(value)

    def paged(self, path: str, field: str) -> list[dict[str, Any]]:
        """Read every page, dedupe by id, and fail closed on malformed/incomplete pages."""
        output: list[dict[str, Any]] = []
        seen: set[int] = set()
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            value = self.json(f"{path}{separator}per_page={PER_PAGE}&page={page}")
            rows = value.get(field)
            if not isinstance(rows, list):
                raise E3Error("PAGINATION_FAILED", f"invalid paginated {field} payload")
            for row in rows:
                if not isinstance(row, Mapping) or row.get("id") is None:
                    raise E3Error("PAGINATION_FAILED", f"invalid {field} row")
                identity = int(row["id"])
                if identity not in seen:
                    seen.add(identity)
                    output.append(dict(row))
            if len(rows) < PER_PAGE:
                return output
            page += 1
            if page > 10000:
                raise E3Error("PAGINATION_FAILED", "pagination did not terminate")

    def artifacts(self, prefix: str = "football-e3g0d-") -> list[dict[str, Any]]:
        return [
            row
            for row in self.paged(f"/repos/{self.repo}/actions/artifacts", "artifacts")
            if str(row.get("name", "")).startswith(prefix)
        ]

    def workflow_runs(self, workflow_id: int | str) -> list[dict[str, Any]]:
        return self.paged(
            f"/repos/{self.repo}/actions/workflows/{workflow_id}/runs", "workflow_runs"
        )

    def artifact(self, artifact_id: int) -> dict[str, Any]:
        value = self.json(f"/repos/{self.repo}/actions/artifacts/{int(artifact_id)}")
        if value.get("id") is None:
            raise E3Error("GITHUB_READ_FAILED", "Artifact metadata is malformed")
        return value

    def download(self, artifact_id: int) -> bytes:
        if self._download_loader is not None:
            try:
                raw = self._download_loader(int(artifact_id))
            except E3Error:
                raise
            except Exception as exc:
                raise E3Error("GITHUB_READ_FAILED", "Artifact download failed") from exc
            if not isinstance(raw, bytes):
                raise E3Error("GITHUB_READ_FAILED", "Artifact download was not bytes")
            return raw
        opener = urllib.request.build_opener(
            urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(), NoRedirect()
        )
        try:
            opener.open(
                self.request(f"/repos/{self.repo}/actions/artifacts/{artifact_id}/zip"),
                timeout=self.timeout,
            )
            raise E3Error("GITHUB_READ_FAILED", "missing Artifact download redirect")
        except urllib.error.HTTPError as exc:
            if exc.code not in {302, 307}:
                raise E3Error("GITHUB_READ_FAILED", f"Artifact download HTTP {exc.code}") from None
            location = exc.headers.get("Location")
        parsed = urllib.parse.urlsplit(location or "")
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (
            host.endswith(".githubusercontent.com") or host.endswith(".github.com")
        ):
            raise E3Error("GITHUB_READ_FAILED", "download redirect outside allowlist")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    location, headers={"User-Agent": "FASHI188-e3g0d-archive/1.1"}
                ),
                timeout=self.timeout,
            ) as response:
                return response.read()
        except Exception as exc:
            raise E3Error("GITHUB_READ_FAILED", "Artifact payload download failed") from exc


def safe_zip_members(raw: bytes) -> tuple[zipfile.ZipFile, set[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise E3Error("VALIDATION_FAILED", "invalid Artifact ZIP") from exc
    bad = archive.testzip()
    if bad:
        raise E3Error("VALIDATION_FAILED", "Artifact ZIP CRC failed")
    names = set(archive.namelist())
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise E3Error("VALIDATION_FAILED", "unsafe Artifact member path")
    return archive, names


def verify_zip(raw: bytes) -> dict[str, Any]:
    archive, names = safe_zip_members(raw)
    checked = 0
    for name in names:
        if not name.endswith(".manifest.json"):
            continue
        try:
            manifest = json.loads(archive.read(name))
        except Exception as exc:
            raise E3Error("VALIDATION_FAILED", "invalid snapshot manifest") from exc
        path = manifest.get("raw_response_path") or manifest.get("raw_payload_path")
        digest = manifest.get("raw_response_sha256") or manifest.get("raw_payload_sha256")
        if path and digest:
            matching = [candidate for candidate in names if candidate.endswith(str(path))]
            if len(matching) != 1 or sha(archive.read(matching[0])) != str(digest):
                raise E3Error("VALIDATION_FAILED", "raw SHA-256 link failed")
            checked += 1
    return {"zip_crc": "PASS", "members": len(names), "raw_sha256_links_checked": checked}


def extract_verified(raw: bytes, destination: Path) -> None:
    archive, names = safe_zip_members(raw)
    destination.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        if name.endswith("/"):
            continue
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        xwrite(target, archive.read(name))


def artifact_digest(meta: Mapping[str, Any], raw: bytes) -> str:
    actual = sha(raw)
    expected = str(meta.get("digest") or "").removeprefix("sha256:")
    if not expected or expected != actual:
        raise E3Error("VALIDATION_FAILED", "Artifact SHA-256 mismatch or unavailable")
    return actual


def read_single_json_from_zip(raw: bytes, suffix: str) -> dict[str, Any]:
    archive, names = safe_zip_members(raw)
    matching = [name for name in names if name.endswith(suffix)]
    if len(matching) != 1:
        raise E3Error("VALIDATION_FAILED", f"Artifact must contain one {suffix}")
    try:
        value = json.loads(archive.read(matching[0]))
    except Exception as exc:
        raise E3Error("VALIDATION_FAILED", f"invalid {suffix}") from exc
    if not isinstance(value, dict):
        raise E3Error("VALIDATION_FAILED", f"{suffix} is not an object")
    return value


def quota_used(reader: GitHubReader, request_day: str) -> dict[str, Any]:
    total = 0
    artifacts: list[dict[str, Any]] = []
    prefix = f"football-e3g0d-quota-{request_day}-"
    for meta in reader.artifacts(prefix):
        if meta.get("expired"):
            raise E3Error("QUOTA_STATE_UNTRUSTED", "same-day quota Artifact expired")
        raw = reader.download(int(meta["id"]))
        if meta.get("digest"):
            artifact_digest(meta, raw)
        receipt = read_single_json_from_zip(raw, "quota_receipt.json")
        if receipt.get("request_day_utc") != request_day:
            raise E3Error("QUOTA_STATE_UNTRUSTED", "quota receipt request day mismatch")
        attempts = receipt.get("request_attempts")
        if not isinstance(attempts, int) or attempts < 0:
            raise E3Error("QUOTA_STATE_UNTRUSTED", "quota receipt attempts invalid")
        total += attempts
        artifacts.append({"artifact_id": int(meta["id"]), "request_attempts": attempts})
    return {
        "request_day_utc": request_day,
        "requests_used_today": total,
        "quota_artifact_count": len(artifacts),
        "quota_artifacts": artifacts,
        "all_pages_read": True,
    }


def plan_artifact_name(target_date: str, league: int, season: int, plan_sha256: str) -> str:
    return (
        f"football-e3g0d-plan-{target_date}-league-{league}-season-{season}-"
        f"sha256-{plan_sha256}"
    )


def resolve_plan(
    reader: GitHubReader,
    destination: Path,
    target_date: str,
    league: int,
    season: int,
    *,
    artifact_id: int | None = None,
    plan_sha256: str | None = None,
) -> dict[str, Any]:
    prefix = f"football-e3g0d-plan-{target_date}-league-{league}-season-{season}-sha256-"
    candidates = [row for row in reader.artifacts(prefix) if not row.get("expired")]
    if artifact_id is not None:
        candidates = [row for row in candidates if int(row["id"]) == int(artifact_id)]
    if plan_sha256:
        candidates = [row for row in candidates if str(row.get("name", "")).endswith(plan_sha256)]
    if len(candidates) != 1:
        raise E3Error(
            "PLAN_IDENTITY_AMBIGUOUS",
            "exactly one explicitly pinned immutable plan Artifact is required",
        )
    meta = candidates[0]
    raw = reader.download(int(meta["id"]))
    if meta.get("digest"):
        artifact_digest(meta, raw)
    verify_zip(raw)
    root = destination / f"artifact_{int(meta['id'])}"
    extract_verified(raw, root)
    plan_files = list(root.rglob("plans/*.json"))
    if len(plan_files) != 1:
        raise E3Error("VALIDATION_FAILED", "plan Artifact must contain one plan")
    try:
        plan = json.loads(plan_files[0].read_text(encoding="utf-8"))
    except Exception as exc:
        raise E3Error("VALIDATION_FAILED", "plan file is invalid") from exc
    if not isinstance(plan, dict):
        raise E3Error("VALIDATION_FAILED", "plan file is not an object")
    actual_sha = compute_plan_sha256(plan)
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or plan.get("plan_sha256") != actual_sha
        or not str(meta.get("name", "")).endswith(actual_sha)
        or (plan_sha256 and actual_sha != plan_sha256)
        or int(plan.get("competition_id", -1)) != int(league)
        or int(plan.get("season_id", -1)) != int(season)
        or plan.get("target_date_utc") != target_date
    ):
        raise E3Error("IDENTITY_MAPPING_FAILED", "plan Artifact identity mismatch")
    manifest_rel = plan.get("source_manifest_path")
    source_sha = plan.get("source_raw_response_sha256")
    if not manifest_rel or not source_sha:
        raise E3Error("VALIDATION_FAILED", "plan source identity is incomplete")
    manifest_matches = list(root.rglob(str(manifest_rel)))
    if len(manifest_matches) != 1:
        raise E3Error("VALIDATION_FAILED", "plan source manifest unavailable")
    manifest = json.loads(manifest_matches[0].read_text(encoding="utf-8"))
    if manifest.get("raw_response_sha256") != source_sha:
        raise E3Error("VALIDATION_FAILED", "plan source manifest SHA mismatch")
    raw_rel = manifest.get("raw_response_path")
    raw_matches = list(root.rglob(str(raw_rel))) if raw_rel else []
    if len(raw_matches) != 1 or sha(raw_matches[0].read_bytes()) != source_sha:
        raise E3Error("VALIDATION_FAILED", "plan source raw SHA chain failed")
    return {
        "selected_plan_artifact_id": int(meta["id"]),
        "selected_plan_sha256": actual_sha,
        "selected_plan_source_raw_sha256": source_sha,
        "selected_plan_run_head": plan.get("run_head"),
        "selected_plan_path": plan_files[0].as_posix(),
        "plan_artifact_name": meta.get("name"),
        "all_pages_read": True,
    }


def manifest_path(root: Path | str) -> Path:
    return Path(root) / "local_archive_manifest.jsonl"


def archive_rows(root: Path | str) -> list[dict[str, Any]]:
    path = manifest_path(root)
    if not path.exists():
        return []
    try:
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except Exception as exc:
        raise E3Error("VALIDATION_FAILED", "local archive manifest damaged") from exc
    if any(not isinstance(value, dict) for value in values):
        raise E3Error("VALIDATION_FAILED", "local archive manifest malformed")
    return values


def archived_ids(root: Path | str) -> set[int]:
    return {int(row["artifact_id"]) for row in archive_rows(root)}


def append_manifest(root: Path | str, row: Mapping[str, Any]) -> None:
    if int(row["artifact_id"]) in archived_ids(root):
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "Artifact already archived")
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("ab") as handle:
            handle.write(packed(dict(row)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "archive manifest append failed") from exc


def safe_name(value: Any) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in str(value))[:160]


def archive_one(reader: GitHubReader, root: Path | str, artifact_id: int) -> dict[str, Any]:
    if int(artifact_id) in archived_ids(root):
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "Artifact already archived")
    meta = reader.artifact(int(artifact_id))
    if meta.get("expired"):
        raise E3Error("GITHUB_READ_FAILED", "Artifact expired")
    raw = reader.download(int(artifact_id))
    actual = artifact_digest(meta, raw)
    check = verify_zip(raw)
    relative = Path("artifacts") / (
        f"{artifact_id}__{safe_name(meta.get('name'))}__sha256_{actual}.zip"
    )
    xwrite(Path(root) / relative, raw)
    row = {
        "schema_version": ARCHIVE_SCHEMA,
        "artifact_id": int(artifact_id),
        "artifact_name": meta.get("name"),
        "artifact_created_at": meta.get("created_at"),
        "artifact_expires_at": meta.get("expires_at"),
        "github_digest": meta.get("digest"),
        "downloaded_sha256": actual,
        "archived_at_utc": iso(utc_now()),
        "local_path": relative.as_posix(),
        "content_verification": check,
        "append_only": True,
        "github_artifact_deleted": False,
        "repository_modified": False,
    }
    append_manifest(root, row)
    return row
