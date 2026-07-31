"""Read-only GitHub Artifact, pagination, quota, plan, and local archive primitives."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from e3g0d_common import E3Error

API = "https://api.github.com"
WORKFLOW = "football-research-e3g0d-api-football-forward-collector.yml"
ARCHIVE_SCHEMA = "E3G0D-LOCAL-ARCHIVE-1.1"
PER_PAGE = 100
ARTIFACT_REDIRECT_SUFFIXES = (
    "github.com",
    "githubusercontent.com",
    "actions.githubusercontent.com",
    "blob.core.windows.net",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:
        return None


JsonLoader = Callable[[str], Mapping[str, Any]]
DownloadLoader = Callable[[int], bytes]
RedirectLoader = Callable[[int], tuple[int, Mapping[str, str]]]
PayloadLoader = Callable[[urllib.request.Request], bytes]


def _official_artifact_host(host: str) -> bool:
    normalized = str(host or "").rstrip(".").lower()
    return any(normalized == suffix or normalized.endswith("." + suffix) for suffix in ARTIFACT_REDIRECT_SUFFIXES)


def validate_artifact_redirect_url(location: Any) -> str:
    text = str(location or "")
    try:
        parsed = urllib.parse.urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise E3Error("GITHUB_READ_FAILED", "malformed Artifact redirect") from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not _official_artifact_host(host)
    ):
        raise E3Error("GITHUB_READ_FAILED", "download redirect outside allowlist")
    return text


def artifact_redirect_location(status: int, headers: Mapping[str, Any]) -> str:
    if int(status) not in {302, 307}:
        raise E3Error("GITHUB_READ_FAILED", f"Artifact download HTTP {int(status)}")
    location = None
    for key, value in headers.items():
        if str(key).lower() == "location":
            location = value
            break
    if not location:
        raise E3Error("GITHUB_READ_FAILED", "missing Artifact download redirect")
    return validate_artifact_redirect_url(location)


def artifact_payload_request(location: Any) -> urllib.request.Request:
    url = validate_artifact_redirect_url(location)
    request = urllib.request.Request(url, headers={"User-Agent": "FASHI188-e3g0d-archive/1.2"})
    lowered = {str(key).lower(): str(value) for key, value in request.header_items()}
    if "authorization" in lowered or "x-apisports-key" in lowered:
        raise E3Error("GITHUB_READ_FAILED", "credential leaked to Artifact storage request")
    return request


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
        redirect_loader: RedirectLoader | None = None,
        payload_loader: PayloadLoader | None = None,
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
        self._redirect_loader = redirect_loader
        self._payload_loader = payload_loader

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
                "User-Agent": "FASHI188-e3g0d-archive/1.2",
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
        return self.paged(f"/repos/{self.repo}/actions/workflows/{workflow_id}/runs", "workflow_runs")

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

        if self._redirect_loader is not None:
            try:
                status, headers = self._redirect_loader(int(artifact_id))
            except E3Error:
                raise
            except Exception as exc:
                raise E3Error("GITHUB_READ_FAILED", "Artifact redirect lookup failed") from exc
        else:
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
                status, headers = exc.code, exc.headers
        location = artifact_redirect_location(int(status), headers)
        request = artifact_payload_request(location)
        if self._payload_loader is not None:
            try:
                raw = self._payload_loader(request)
            except E3Error:
                raise
            except Exception as exc:
                raise E3Error("GITHUB_READ_FAILED", "Artifact payload download failed") from exc
            if not isinstance(raw, bytes):
                raise E3Error("GITHUB_READ_FAILED", "Artifact payload was not bytes")
            return raw
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except Exception as exc:
            raise E3Error("GITHUB_READ_FAILED", "Artifact payload download failed") from exc

