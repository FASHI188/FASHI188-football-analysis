#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC = dt.timezone.utc


class SkyGhostarchiveError(RuntimeError):
    pass


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyGhostarchiveError(msg)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_z(value: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=UTC)
    return x.astimezone(UTC)


def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def ghost_host_ok(url: str, allowed_host: str) -> bool:
    h = host(url)
    a = allowed_host.lower()
    return h == a or h == "www." + a


def build_search_url(endpoint: str, target: str) -> str:
    return endpoint + "?" + urllib.parse.urlencode({"term": target})


def fetch_search(url: str, source: dict[str, Any]) -> tuple[int, bytes, str, dict[str, str]]:
    req(ghost_host_ok(url, source["allowed_host"]), "REQUEST_HOST")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": source["user_agent"],
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
    )
    limit = int(source["max_response_bytes"])
    try:
        with urllib.request.urlopen(
            request,
            timeout=int(source["request_timeout_seconds"]),
            context=ssl.create_default_context(),
        ) as response:
            final = response.geturl()
            req(ghost_host_ok(final, source["allowed_host"]), "REDIRECT_OUTSIDE_GHOSTARCHIVE")
            req(urllib.parse.urlparse(final).path.rstrip("/") == "/search", "REDIRECT_OUTSIDE_SEARCH")
            raw = response.read(limit + 1)
            req(len(raw) <= limit, "RESPONSE_TOO_LARGE")
            return (
                int(getattr(response, "status", 200)),
                raw,
                final,
                {k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        final = exc.geturl()
        req(ghost_host_ok(final, source["allowed_host"]), "HTTP_ERROR_OUTSIDE_GHOSTARCHIVE")
        return int(exc.code), b"", final, {k.lower(): v for k, v in exc.headers.items()}


def anti_bot_marker(raw: bytes) -> str | None:
    s = raw.decode("utf-8", "replace").casefold()
    markers = [
        "cf-chl-",
        "captcha",
        "verify you are human",
        "checking your browser",
        "access denied",
        "attention required",
    ]
    for marker in markers:
        if marker in s:
            return marker
    return None


class RowParser(HTMLParser):
    IGNORE = {"script", "style", "noscript", "template", "svg"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.ignore_depth = 0
        self.in_row = False
        self.row_depth = 0
        self.row_text: list[str] = []
        self.row_hrefs: list[str] = []
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self.IGNORE:
            self.ignore_depth += 1
            return
        if self.ignore_depth:
            return
        if t == "tr":
            if not self.in_row:
                self.in_row = True
                self.row_depth = 1
                self.row_text = []
                self.row_hrefs = []
            else:
                self.row_depth += 1
            return
        if not self.in_row:
            return
        if t == "a":
            d = {str(k).lower(): (v or "") for k, v in attrs}
            href = d.get("href", "").strip()
            if href:
                self.row_hrefs.append(urllib.parse.urljoin(self.base_url, href))
        if t in {"td", "th", "br", "p", "div"}:
            self.row_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.IGNORE and self.ignore_depth:
            self.ignore_depth -= 1
            return
        if self.ignore_depth:
            return
        if t == "tr" and self.in_row:
            self.row_depth -= 1
            if self.row_depth <= 0:
                text = re.sub(r"\s+", " ", html.unescape("".join(self.row_text))).strip()
                self.rows.append({"text": text, "hrefs": list(dict.fromkeys(self.row_hrefs))})
                self.in_row = False
                self.row_depth = 0
                self.row_text = []
                self.row_hrefs = []

    def handle_data(self, data: str) -> None:
        if self.ignore_depth or not self.in_row:
            return
        if data:
            self.row_text.append(data)


def parse_rows(raw: bytes, final_url: str) -> list[dict[str, Any]]:
    parser = RowParser(final_url)
    parser.feed(raw.decode("utf-8", "replace"))
    return parser.rows


_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)
_ISO_TS_RE = re.compile(
    r"\b20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?"
    r"(?:\s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2}))\b",
    re.I,
)
_DMY_TS_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
    r"20\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+(?:UTC|GMT|[+-]\d{2}:?\d{2})\b",
    re.I,
)
_DMY_NO_WEEKDAY_RE = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
    r"20\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+(?:UTC|GMT|[+-]\d{2}:?\d{2})\b",
    re.I,
)


def clean_url_token(value: str) -> str:
    return value.rstrip(").,;]}>")


def row_exact_identity(row: dict[str, Any], target: str) -> bool:
    wanted = normalize_sky_identity(target)
    for href in row.get("hrefs", []):
        if ghost_host_ok(href, "ghostarchive.org"):
            continue
        try:
            if normalize_sky_identity(href) == wanted:
                return True
        except Exception:
            pass
    text = str(row.get("text") or "")
    if target in html.unescape(text):
        return True
    for m in _URL_RE.finditer(text):
        candidate = clean_url_token(m.group(0))
        try:
            if normalize_sky_identity(candidate) == wanted:
                return True
        except Exception:
            continue
    return False


def archive_hrefs(row: dict[str, Any]) -> list[str]:
    out = []
    for href in row.get("hrefs", []):
        p = urllib.parse.urlparse(href)
        if ghost_host_ok(href, "ghostarchive.org") and p.path.startswith("/archive/"):
            out.append(href)
    return sorted(set(out))


def _parse_timestamp_text(value: str) -> dt.datetime | None:
    s = value.strip()
    if not s:
        return None
    normalized = re.sub(r"\s+", " ", s)
    z = normalized
    if re.search(r"\bUTC\b", z, flags=re.I):
        z = re.sub(r"\bUTC\b", "+00:00", z, flags=re.I)
    elif re.search(r"\bGMT\b", z, flags=re.I):
        z = re.sub(r"\bGMT\b", "+00:00", z, flags=re.I)
    try:
        x = dt.datetime.fromisoformat(z.replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(UTC)
    except Exception:
        pass
    try:
        x = email.utils.parsedate_to_datetime(normalized)
        if x.tzinfo is None:
            return None
        return x.astimezone(UTC)
    except Exception:
        pass
    for fmt in (
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M %z",
        "%d %B %Y %H:%M:%S %z",
        "%d %B %Y %H:%M %z",
    ):
        try:
            return dt.datetime.strptime(normalized, fmt).astimezone(UTC)
        except Exception:
            continue
    return None


def timestamp_candidates(row_text: str) -> list[dict[str, str]]:
    candidates: list[str] = []
    for pattern in (_ISO_TS_RE, _DMY_TS_RE, _DMY_NO_WEEKDAY_RE):
        candidates.extend(m.group(0) for m in pattern.finditer(row_text))
    out = []
    seen = set()
    for raw in candidates:
        when = _parse_timestamp_text(raw)
        if when is None:
            continue
        iso = when.isoformat().replace("+00:00", "Z")
        if iso in seen:
            continue
        seen.add(iso)
        out.append({"visible_timestamp": raw, "capture_utc": iso})
    return out


def audit_rows(
    rows: list[dict[str, Any]],
    target: str,
    lower_utc: str,
    upper_utc: str,
) -> dict[str, Any]:
    lo = parse_z(lower_utc)
    hi = parse_z(upper_utc)
    req(lo < hi, "INVALID_PIT_WINDOW")
    sanitized = []
    identity_archive_row_n = 0
    metadata_insufficient_row_n = 0
    witnesses = []

    for idx, row in enumerate(rows):
        archives = archive_hrefs(row)
        if not archives:
            continue
        exact = row_exact_identity(row, target)
        if not exact:
            sanitized.append({
                "row_index": idx,
                "archive_href_n": len(archives),
                "exact_identity": False,
                "timestamp_n": 0,
                "witness_pass_n": 0,
            })
            continue

        identity_archive_row_n += 1
        ts = timestamp_candidates(str(row.get("text") or ""))
        passed = []
        for item in ts:
            when = parse_z(item["capture_utc"])
            if lo <= when < hi:
                passed.append({
                    "archive_href": archives[0],
                    "visible_timestamp": item["visible_timestamp"],
                    "capture_utc": item["capture_utc"],
                    "pit_time_ok": True,
                })
        if not ts:
            metadata_insufficient_row_n += 1
        sanitized.append({
            "row_index": idx,
            "archive_href_n": len(archives),
            "archive_hrefs": archives,
            "exact_identity": True,
            "timestamp_n": len(ts),
            "timestamps": ts,
            "witness_pass_n": len(passed),
            "witnesses": passed,
        })
        witnesses.extend(passed)

    return {
        "result_row_n": len(sanitized),
        "identity_archive_row_n": identity_archive_row_n,
        "metadata_insufficient_row_n": metadata_insufficient_row_n,
        "witness_pass_n": len(witnesses),
        "witnesses": witnesses,
        "rows": sanitized,
    }


def classify(
    witness_n: int,
    error_n: int,
    metadata_insufficient_n: int,
    p: dict[str, Any],
) -> tuple[str, str]:
    if witness_n > 0:
        return (
            p["decision_contract"]["positive_classification"],
            p["reasonable_subroutes"]["if_positive"],
        )
    if error_n > 0:
        return (
            p["decision_contract"]["fail_classification"],
            p["reasonable_subroutes"]["if_external_error"],
        )
    if metadata_insufficient_n > 0:
        return (
            p["decision_contract"]["fail_classification"],
            p["reasonable_subroutes"]["if_metadata_insufficient"],
        )
    return (
        p["decision_contract"]["fail_classification"],
        p["reasonable_subroutes"]["if_clean_zero"],
    )


def run(registry: Path, out: Path, token: str) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(p["exact_base"] == "3f3cfc227641ca7a5dcd90a9123697f11f9e7e97", "EXACT_BASE")

    sources = p["search_contract_sources"]
    req(len(sources) == 2, "SEARCH_CONTRACT_SOURCE_N")
    req(sources[0]["repository"] == "wabarc/ghostarchive", "SEARCH_SOURCE_1_REPO")
    req(sources[0]["commit"] == "55fdc132c9ca5979c8e4ea3f84eb750913793ba3", "SEARCH_SOURCE_1_COMMIT")
    req(sources[0]["git_blob_sha"] == "bc98c9e8c18b3892af7fabab8e7951897d1af037", "SEARCH_SOURCE_1_BLOB")
    req(sources[1]["repository"] == "dessant/web-archives", "SEARCH_SOURCE_2_REPO")
    req(sources[1]["commit"] == "3cc1989d4d714ccecef4914895f9255bcddcd36e", "SEARCH_SOURCE_2_COMMIT")
    req(sources[1]["git_blob_sha"] == "21d02d5025c2351fe4dfeb473399125cc5e774c8", "SEARCH_SOURCE_2_BLOB")

    h = p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False, "NO_PREVIOUS_REQUERY")
    req(h["archive_creation_allowed"] is False, "NO_ARCHIVE_CREATE")
    req(h["archive_snapshot_fetch_allowed"] is False and h["warc_fetch_allowed"] is False, "NO_ARCHIVE_BODY")
    req(h["search_result_link_follow_allowed"] is False, "NO_RESULT_FOLLOW")
    req(h["search_html_persisted"] is False, "NO_HTML_PERSIST")
    req(h["date_only_timestamp_allowed"] is False and h["timezone_less_timestamp_allowed"] is False, "STRICT_TIME")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False, "NO_BODY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False, "NO_RESULT_PARSE")
    req(h["match_result_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False, "NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    pit, sky, parent_prov = acquire_parents(p, token)
    pit_rows = {int(x["round"]): x for x in pit["rows"]}
    sky_rows = {int(x["round"]): x for x in sky["rows"]}
    samples = [int(x["round"]) for x in p["samples"]]
    req(samples == [8, 24, 34], "FROZEN_SAMPLES")

    src = p["source"]
    reports = []
    errors = []
    witnesses = []
    metadata_insufficient_rounds = []

    for rnd in samples:
        prow = pit_rows[rnd]
        srow = sky_rows[rnd]
        req(prow["binding_status"] == "FAIL", f"SAMPLE_ALREADY_PASS:R{rnd}")
        target = srow.get("sky_url")
        req(isinstance(target, str) and target.startswith("https://sport.sky.it/"), f"SKY_URL:R{rnd}")
        lower = prow["sky_visible_published_utc"]
        upper = prow["first_fixture_cutoff_utc"]
        q = build_search_url(src["endpoint"], target)
        report = {
            "round": rnd,
            "target_url": target,
            "lower_utc": lower,
            "upper_utc": upper,
            "query_url": q,
            "status": "UNSET",
            "http_status": None,
            "search_html_persisted": False,
            "archive_result_link_followed": False,
            "archive_snapshot_fetch_performed": False,
            "archive_creation_performed": False,
            "warc_fetch_performed": False,
            "witness_pass_n": 0,
        }
        try:
            status, raw, final, headers = fetch_search(q, src)
            report.update({
                "http_status": status,
                "final_url": final,
                "content_type": headers.get("content-type"),
            })
            if not (200 <= status < 300):
                report["status"] = "HTTP_ERROR"
                report["error"] = f"HTTP_{status}"
                errors.append({"round": rnd, "error": report["error"]})
            else:
                marker = anti_bot_marker(raw)
                if marker is not None:
                    report["status"] = "EXTERNAL_ANTI_BOT_BLOCK"
                    report["error"] = f"ANTI_BOT:{marker}"
                    errors.append({"round": rnd, "error": report["error"]})
                else:
                    rows = parse_rows(raw, final)
                    audit = audit_rows(rows, target, lower, upper)
                    report.update({
                        "status": "SUCCESS",
                        "response_bytes": len(raw),
                        "response_sha256": sha256_bytes(raw),
                        "html_row_n": len(rows),
                        **audit,
                        "witness_pass_n": audit["witness_pass_n"],
                    })
                    if audit["metadata_insufficient_row_n"] > 0:
                        metadata_insufficient_rounds.append(rnd)
                    for witness in audit["witnesses"]:
                        witnesses.append({"round": rnd, **witness})
        except Exception as exc:
            report["status"] = "EXTERNAL_OR_CONTRACT_ERROR"
            report["error"] = f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round": rnd, "error": report["error"]})
        reports.append(report)

    positive_rounds = sorted({int(x["round"]) for x in witnesses})
    metadata_insufficient_rounds = sorted(set(metadata_insufficient_rounds))
    classification, next_step = classify(
        len(witnesses), len(errors), len(metadata_insufficient_rounds), p
    )

    matrix = {
        "schema_version": "football3-nova-n10-referee-sky-ghostarchive-feasibility-matrix-v1",
        "sample_rounds": samples,
        "query_n": len(reports),
        "error_n": len(errors),
        "errors": errors,
        "metadata_insufficient_rounds": metadata_insufficient_rounds,
        "metadata_insufficient_round_n": len(metadata_insufficient_rounds),
        "witness_pass_n": len(witnesses),
        "positive_rounds": positive_rounds,
        "witnesses": witnesses,
        "reports": reports,
        "search_html_persisted": False,
        "archive_result_link_followed": False,
        "archive_snapshot_fetch_performed": False,
        "archive_creation_performed": False,
        "warc_fetch_performed": False,
    }
    out.mkdir(parents=True, exist_ok=True)
    matrix_bytes = (
        json.dumps(matrix, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    (out / "sky_ghostarchive_feasibility_matrix.json").write_bytes(matrix_bytes)

    receipt = {
        "schema_version": "football3-nova-n10-referee-sky-ghostarchive-feasibility-receipt-v1",
        "status": "N10_REFEREE_SKY_GHOSTARCHIVE_FEASIBILITY_COMPLETE",
        "classification": classification,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "search_contract_1_commit": sources[0]["commit"],
        "search_contract_1_blob_sha": sources[0]["git_blob_sha"],
        "search_contract_2_commit": sources[1]["commit"],
        "search_contract_2_blob_sha": sources[1]["git_blob_sha"],
        "parent_provenance": parent_prov,
        "sample_rounds": samples,
        "query_n": len(reports),
        "error_n": len(errors),
        "errors": errors,
        "metadata_insufficient_rounds": metadata_insufficient_rounds,
        "metadata_insufficient_round_n": len(metadata_insufficient_rounds),
        "witness_pass_n": len(witnesses),
        "positive_rounds": positive_rounds,
        "matrix_sha256": sha256_bytes(matrix_bytes),
        "previous_source_requery_n": 0,
        "search_html_persisted": False,
        "archive_result_link_followed": False,
        "archive_snapshot_fetch_performed": False,
        "archive_creation_performed": False,
        "warc_fetch_performed": False,
        "article_body_read": False,
        "referee_assignment_body_parsed": False,
        "match_result_payload_read": False,
        "standings_payload_read": False,
        "player_stats_payload_read": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_available_at_proven": False,
        "fixture_level_binding_complete": False,
        "referee_oof_allowed": False,
        "next_step": next_step,
    }
    (out / "sky_ghostarchive_feasibility_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    x = a.parse_args()
    run(x.registry, x.out, os.environ.get("GITHUB_TOKEN", ""))


if __name__ == "__main__":
    main()
