"""Bounded HTTPS client for the single approved API-Football host."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from e3g0d_common import (
    E3Error,
    ENDPOINTS,
    HOST,
    MAX_BACKOFF,
    MAX_BODY,
    MAX_RETRIES,
    MAX_TIMEOUT,
    api_url,
    utc_now,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:
        raise E3Error("VALIDATION_FAILED", "HTTP redirect refused")


Transport = Callable[[str, Mapping[str, Any], int], tuple[int, Mapping[str, str], bytes, str]]


class Client:
    """Request client with bounded retries and an injectable no-network test transport."""

    def __init__(
        self,
        key: str | None,
        timeout: float,
        retries: int,
        backoff: float,
        budget: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        opener: Any | None = None,
        transport: Transport | None = None,
    ) -> None:
        if not 0 < float(timeout) <= MAX_TIMEOUT:
            raise E3Error("VALIDATION_FAILED", "invalid timeout")
        if not 0 <= int(retries) <= MAX_RETRIES:
            raise E3Error("VALIDATION_FAILED", "invalid retry count")
        if not 0 < float(backoff) <= MAX_BACKOFF:
            raise E3Error("VALIDATION_FAILED", "invalid backoff cap")
        normalized_key = str(key or "").strip()
        if transport is None and not normalized_key:
            raise E3Error("VALIDATION_FAILED", "API_FOOTBALL_KEY is required")
        self._key = normalized_key
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.backoff = float(backoff)
        self.budget = budget
        self.sleep = sleep
        self.transport = transport
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.backoff)
            except ValueError:
                pass
        return min(float(2**attempt), self.backoff)

    def _network_transport(
        self, endpoint: str, params: Mapping[str, Any], attempt: int
    ) -> tuple[int, Mapping[str, str], bytes, str]:
        del attempt
        url = api_url(endpoint, params)
        request = urllib.request.Request(
            url,
            headers={
                "x-apisports-key": self._key,
                "Accept": "application/json",
                "User-Agent": "FASHI188-e3g0d/1.2",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return (
                    int(response.status),
                    {str(key): str(value) for key, value in response.headers.items()},
                    response.read(MAX_BODY + 1),
                    str(response.geturl()),
                )
        except urllib.error.HTTPError as exc:
            return (
                int(exc.code),
                {str(key): str(value) for key, value in (exc.headers or {}).items()},
                b"",
                url,
            )

    def _validate_success(
        self,
        status: int,
        headers: Mapping[str, str],
        raw: bytes,
        final_url: str,
    ) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(final_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != HOST
            or parsed.path.strip("/") not in ENDPOINTS
        ):
            raise E3Error("VALIDATION_FAILED", "redirect outside allowlist")
        if len(raw) > MAX_BODY:
            raise E3Error("RESPONSE_TOO_LARGE")
        if self._key and self._key.encode("utf-8") in raw:
            raise E3Error("VALIDATION_FAILED", "response contained credential material")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise E3Error("NON_JSON_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise E3Error("NON_JSON_RESPONSE", "provider returned non-object JSON")
        if payload.get("errors"):
            raise E3Error("PROVIDER_ERROR")
        remaining = None
        for key, value in headers.items():
            if str(key).lower() in {
                "x-ratelimit-requests-remaining",
                "x-ratelimit-remaining",
            }:
                remaining = value
                break
        if remaining is not None:
            try:
                if int(remaining) < 10:
                    raise E3Error("PROVIDER_QUOTA_RESERVE_REACHED")
            except ValueError as exc:
                raise E3Error("VALIDATION_FAILED", "invalid rate-limit header") from exc
        return payload

    def get(
        self, endpoint: str, params: Mapping[str, Any]
    ) -> tuple[bytes, dict[str, Any], Any, Any, int, dict[str, str]]:
        # Construct and validate the URL before any attempt is counted.
        api_url(endpoint, params)
        transport = self.transport or self._network_transport
        for attempt in range(self.retries + 1):
            self.budget.take()
            requested = utc_now()
            try:
                status, headers_raw, raw, final_url = transport(endpoint, params, attempt)
                headers = {str(key): str(value) for key, value in headers_raw.items()}
                if 300 <= int(status) < 400:
                    raise E3Error("VALIDATION_FAILED", "HTTP redirect refused")
                if int(status) == 429:
                    if attempt >= self.retries:
                        raise E3Error("HTTP_429")
                    self.sleep(self.delay(attempt, headers.get("Retry-After")))
                    continue
                if 500 <= int(status) <= 599:
                    if attempt >= self.retries:
                        raise E3Error("HTTP_5XX")
                    self.sleep(self.delay(attempt, headers.get("Retry-After")))
                    continue
                if not 200 <= int(status) <= 299:
                    raise E3Error("VALIDATION_FAILED", f"provider HTTP {int(status)}")
                payload = self._validate_success(int(status), headers, raw, final_url)
                return raw, payload, requested, utc_now(), int(status), headers
            except E3Error:
                raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt >= self.retries:
                    raise E3Error("NETWORK_FAILURE") from None
                self.sleep(self.delay(attempt))
        raise E3Error("NETWORK_FAILURE")
