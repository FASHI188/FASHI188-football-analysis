#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from adaptive_latent_stage4_target_personnel_compact_ledger_guard_v6 import (
    HOSTS,
    LedgerError,
    validate as validate_ledger,
)

ROOT = Path(__file__).resolve().parent
INDEX_NAME = "adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json"
USER_AGENT = "football3-weekly-personnel-scan/1.0 (+research-only; zero-label)"
MAX_BYTES_DEFAULT = 4_000_000
NAME_HINTS = ("player", "squad", "team", "member", "person", "name", "roster")
INTERESTING_TAGS = {"a", "strong", "b", "h1", "h2", "h3", "h4", "h5", "h6"}
STOP_TEXT = {
    "home", "news", "team", "teams", "squad", "players", "player", "staff", "club",
    "clubs", "football", "fixtures", "results", "tickets", "shop", "more", "menu",
    "search", "sign in", "login", "privacy policy", "cookie policy", "contact us",
    "read more", "view more", "premier league", "bundesliga", "serie a", "la liga",
    "ligue 1", "uefa", "goalkeepers", "defenders", "midfielders", "forwards",
}


class ScanError(RuntimeError):
    pass


class HTMLTextProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.candidate_parts: list[str] = []
        self._stack: list[tuple[bool, bool]] = []
        self._suppress_depth = 0
        self._interesting_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attrs_dict = {k.casefold(): (v or "") for k, v in attrs}
        attr_blob = " ".join((attrs_dict.get("class", ""), attrs_dict.get("id", ""))).casefold()
        suppress = tag in {"script", "style", "noscript", "svg"}
        interesting = tag in INTERESTING_TAGS or any(h in attr_blob for h in NAME_HINTS)
        self._stack.append((suppress, interesting))
        if suppress:
            self._suppress_depth += 1
        if interesting:
            self._interesting_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        suppress, interesting = self._stack.pop()
        if suppress:
            self._suppress_depth = max(0, self._suppress_depth - 1)
        if interesting:
            self._interesting_depth = max(0, self._interesting_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._suppress_depth:
            return
        text = clean_text(data)
        if not text:
            return
        self.visible_parts.append(text)
        if self._interesting_depth:
            self.candidate_parts.append(text)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def looks_like_person_name(value: str) -> bool:
    value = clean_text(value)
    if not value or len(value) > 80 or any(ch.isdigit() for ch in value):
        return False
    if normalize_text(value) in STOP_TEXT:
        return False
    if any(token in value.casefold() for token in ("http", "cookie", "privacy", "copyright")):
        return False
    if re.search(r"[,:;!?=/\\@#%$+<>]", value):
        return False
    words = value.replace("’", "'").split()
    if not (1 <= len(words) <= 5):
        return False
    if len(words) == 1 and len(words[0]) < 5:
        return False
    alpha_count = sum(ch.isalpha() for ch in value)
    return alpha_count >= max(4, len(value) // 2)


def parse_html_document(document: str) -> tuple[str, list[str]]:
    probe = HTMLTextProbe()
    probe.feed(document)
    visible = " ".join(probe.visible_parts)
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in probe.candidate_parts:
        raw = clean_text(raw)
        key = normalize_text(raw)
        if key and key not in seen and looks_like_person_name(raw):
            seen.add(key)
            candidates.append(raw)
    return visible, candidates


def _source_host_ok(expected_host: str, actual_host: str | None) -> bool:
    if not actual_host:
        return False
    expected_host = expected_host.casefold()
    actual_host = actual_host.casefold()
    if actual_host == expected_host:
        return True
    expected_bare = expected_host[4:] if expected_host.startswith("www.") else expected_host
    actual_bare = actual_host[4:] if actual_host.startswith("www.") else actual_host
    return actual_bare == expected_bare


def validate_source_url(url: str, competition_id: str) -> str:
    if competition_id not in HOSTS:
        raise ScanError(f"unknown competition_id: {competition_id}")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS[competition_id]
        or parsed.netloc != parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ScanError(f"source URL denied: {url}")
    return parsed.hostname


def fetch_source(
    url: str,
    competition_id: str,
    *,
    timeout: float,
    max_bytes: int,
) -> dict[str, object]:
    expected_host = validate_source_url(url, competition_id)
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            final_url = resp.geturl()
            final = urlsplit(final_url)
            if final.scheme != "https" or not _source_host_ok(expected_host, final.hostname):
                raise ScanError(f"redirect escaped source family: {url} -> {final_url}")
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ScanError(f"source too large: {url}")
            content_type = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return {
                "ok": True,
                "http_status": status,
                "final_url": final_url,
                "content_type": content_type,
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "document": text,
                "error": None,
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "http_status": int(exc.code),
            "final_url": url,
            "content_type": None,
            "byte_count": 0,
            "sha256": None,
            "document": "",
            "error": f"HTTPError:{exc.code}",
        }
    except (URLError, TimeoutError, OSError, ScanError) as exc:
        return {
            "ok": False,
            "http_status": None,
            "final_url": url,
            "content_type": None,
            "byte_count": 0,
            "sha256": None,
            "document": "",
            "error": f"{type(exc).__name__}:{exc}",
        }


def load_baseline(root: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    validation = validate_ledger(root)
    idx = json.loads((root / INDEX_NAME).read_text(encoding="utf-8"))
    teams: list[dict[str, object]] = []
    for competition_id in sorted(idx["files"]):
        for metadata in idx["files"][competition_id]:
            path = root / metadata["file"]
            current: dict[str, object] | None = None
            for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                fields = line.split("\t")
                if fields[0] == "T":
                    (
                        _,
                        team_id,
                        team_name,
                        authority,
                        source_url,
                        source_observed_at,
                        source_published_at,
                        publication_precision,
                        evidence_type,
                        evidence_scope,
                    ) = fields
                    current = {
                        "competition_id": competition_id,
                        "team_id": team_id,
                        "team_name": team_name,
                        "authority": authority,
                        "source_url": source_url,
                        "source_observed_at": source_observed_at,
                        "source_published_at": source_published_at,
                        "publication_precision": publication_precision,
                        "evidence_type": evidence_type,
                        "evidence_scope": evidence_scope,
                        "baseline_players": [],
                    }
                    teams.append(current)
                elif fields[0] == "P":
                    if current is None:
                        raise ScanError(f"person row before team in {path.name}")
                    current["baseline_players"].append(fields[2])
    if len(teams) != 38:
        raise ScanError(f"baseline team count drift: {len(teams)}")
    if sum(len(t["baseline_players"]) for t in teams) != 1097:
        raise ScanError("baseline person count drift")
    return validation, teams


def adjudicate_team(
    team: dict[str, object],
    source: dict[str, object],
    *,
    shared_source_scope: bool,
) -> dict[str, object]:
    baseline = list(team["baseline_players"])
    result = {k: v for k, v in team.items() if k != "baseline_players"}
    result["baseline_player_count"] = len(baseline)
    result["shared_source_scope"] = shared_source_scope
    result["source_fetch_ok"] = bool(source["ok"])
    result["http_status"] = source["http_status"]
    result["final_url"] = source["final_url"]
    result["source_content_type"] = source["content_type"]
    result["source_byte_count"] = source["byte_count"]
    result["source_sha256"] = source["sha256"]
    result["fetch_error"] = source["error"]

    if not source["ok"]:
        result.update(
            {
                "status": "SOURCE_UNAVAILABLE",
                "baseline_visible_count": 0,
                "baseline_hit_rate": 0.0,
                "baseline_missing_names": baseline,
                "possible_departure_candidates": [],
                "unverified_addition_candidates": [],
                "needs_review": True,
            }
        )
        return result

    visible, candidate_texts = parse_html_document(str(source["document"]))
    visible_norm = f" {normalize_text(visible)} "
    baseline_norm = {name: normalize_text(name) for name in baseline}
    visible_names = [
        name for name, key in baseline_norm.items() if key and f" {key} " in visible_norm
    ]
    missing = [name for name in baseline if name not in visible_names]
    hit_rate = len(visible_names) / len(baseline) if baseline else 0.0

    additions: list[str] = []
    if not shared_source_scope and hit_rate >= 0.5:
        baseline_keys = set(baseline_norm.values())
        team_key = normalize_text(str(team["team_name"]))
        seen: set[str] = set()
        for candidate in candidate_texts:
            key = normalize_text(candidate)
            if (
                not key
                or key in baseline_keys
                or key == team_key
                or key in seen
                or key in STOP_TEXT
            ):
                continue
            seen.add(key)
            additions.append(candidate)
            if len(additions) >= 50:
                break

    if hit_rate >= 0.95:
        status = "BASELINE_HIGH_VISIBILITY"
    elif hit_rate >= 0.5:
        status = "BASELINE_PARTIAL_VISIBILITY"
    else:
        status = "SOURCE_NOT_MACHINE_READABLE_OR_ROSTER_DRIFT"

    departures = missing if hit_rate >= 0.75 else []
    result.update(
        {
            "status": status,
            "baseline_visible_count": len(visible_names),
            "baseline_hit_rate": round(hit_rate, 6),
            "baseline_missing_names": missing,
            "possible_departure_candidates": departures,
            "unverified_addition_candidates": additions,
            "needs_review": bool(departures or additions or hit_rate < 0.95),
        }
    )
    return result


def render_markdown(report: dict[str, object]) -> str:
    summary = report["summary"]
    lines = [
        "# football3 weekly personnel scan",
        "",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- source_head: `{report['source_head']}`",
        f"- status: `{report['status']}`",
        f"- target teams: `{summary['team_count']}`",
        f"- baseline persons: `{summary['baseline_person_count']}`",
        f"- source URLs: `{summary['source_count']}`",
        f"- source fetch ok/failed: `{summary['source_fetch_ok']}/{summary['source_fetch_failed']}`",
        f"- teams needing review: `{summary['team_review_count']}`",
        "",
        "## Review queue",
        "",
    ]
    review = [team for team in report["teams"] if team["needs_review"]]
    if not review:
        lines.append("- none")
    else:
        for team in review:
            dep = ", ".join(team["possible_departure_candidates"][:8]) or "-"
            add = ", ".join(team["unverified_addition_candidates"][:8]) or "-"
            lines.append(
                f"- **{team['competition_id']} / {team['team_name']}** — "
                f"`{team['status']}`; visible={team['baseline_visible_count']}/"
                f"{team['baseline_player_count']}; possible departures={dep}; "
                f"unverified additions={add}; source={team['source_url']}"
            )
    lines.extend(
        [
            "",
            "## Scientific boundary",
            "",
            "- real labels read: `0`",
            "- training/tuning/real scoring: `false/false/false`",
            "- Provider/API secrets: `0`",
            "- CURRENT/formal_weight/merge changes: `0`",
            "- This scan is transfer/roster monitoring only. Missing or candidate names are review signals, not formal PIT facts.",
            "",
        ]
    )
    return "\n".join(lines)


def run_scan(root: Path, output_dir: Path, timeout: float, max_bytes: int) -> dict[str, object]:
    validation, teams = load_baseline(root)
    source_groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for team in teams:
        key = (str(team["competition_id"]), str(team["source_url"]))
        source_groups.setdefault(key, []).append(team)

    fetched: dict[tuple[str, str], dict[str, object]] = {}
    for key in sorted(source_groups):
        competition_id, source_url = key
        fetched[key] = fetch_source(
            source_url,
            competition_id,
            timeout=timeout,
            max_bytes=max_bytes,
        )

    team_results: list[dict[str, object]] = []
    for key in sorted(source_groups):
        group = source_groups[key]
        source = fetched[key]
        shared = len(group) > 1
        for team in sorted(group, key=lambda x: str(x["team_name"])):
            team_results.append(adjudicate_team(team, source, shared_source_scope=shared))

    fetch_ok = sum(1 for source in fetched.values() if source["ok"])
    review_count = sum(1 for team in team_results if team["needs_review"])
    if fetch_ok == 0:
        status = "SOURCE_ACCESS_FAILED_ALL"
    elif review_count:
        status = "SCAN_COMPLETE_REVIEW_REQUIRED"
    else:
        status = "SCAN_COMPLETE_BASELINE_STABLE"

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report: dict[str, object] = {
        "schema": "football3_weekly_target_personnel_scan_v1",
        "project_id": "football3",
        "generated_at_utc": generated_at,
        "source_head": os.environ.get("GITHUB_SHA", "LOCAL_OR_UNKNOWN"),
        "status": status,
        "baseline_validation": validation,
        "baseline_identity_lock_sha256": json.loads((root / INDEX_NAME).read_text())[
            "identity_lock_sha256"
        ],
        "formal_pit_eligible": False,
        "formal_weight": 0.0,
        "real_labels_read": 0,
        "real_match_rows_read": 0,
        "training": False,
        "tuning": False,
        "real_scoring": False,
        "provider_api": False,
        "secret_access": False,
        "CURRENT_change": False,
        "merge": False,
        "summary": {
            "team_count": len(team_results),
            "baseline_person_count": sum(t["baseline_player_count"] for t in team_results),
            "source_count": len(fetched),
            "source_fetch_ok": fetch_ok,
            "source_fetch_failed": len(fetched) - fetch_ok,
            "team_review_count": review_count,
            "competition_team_counts": {
                cid: sum(1 for t in team_results if t["competition_id"] == cid)
                for cid in sorted(HOSTS)
            },
        },
        "teams": sorted(
            team_results,
            key=lambda t: (str(t["competition_id"]), str(t["team_name"])),
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weekly_personnel_scan.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "weekly_personnel_scan.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly zero-label personnel source scan for football3.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evidence" / "weekly_personnel_scan_v1",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES_DEFAULT)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_bytes < 1024:
        raise SystemExit("invalid timeout/max-bytes")
    try:
        report = run_scan(args.root, args.output_dir, args.timeout, args.max_bytes)
    except (LedgerError, ScanError, ValueError, KeyError, OSError) as exc:
        print(f"FATAL weekly personnel scan invariant failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], sort_keys=True))
    print(report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
