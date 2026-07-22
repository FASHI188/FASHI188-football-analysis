#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import kambi_active_domain_capture_v5532 as kambi
import marathonbet_active_domain_capture_v5532 as base
import marathonbet_active_domain_direct_v5532 as direct

ROOT = base.ROOT
MANIFEST = base.MANIFEST
SCHEDULE_RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "active_domains"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_tags(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def td_cell(block: str, mutable_id: str) -> str | None:
    match = re.search(
        rf'<td\b[^>]*data-mutable-id="{re.escape(mutable_id)}"[^>]*>(.*?)</td>',
        block,
        re.I | re.S,
    )
    return match.group(1) if match else None


def cell_price(cell: str | None) -> float:
    if not cell:
        raise ValueError("market cell missing")
    match = re.search(r'data-selection-price="([0-9.]+)"', cell, re.I)
    if not match:
        raise ValueError("selection price missing")
    return base.price(match.group(1))


def cell_line(cell: str | None) -> float:
    if not cell:
        raise ValueError("line cell missing")
    match = re.search(r'<span[^>]*class="[^"]*middle-simple[^"]*"[^>]*>\s*\(?([+-]?[0-9.]+|0)\)?\s*</span>', cell, re.I | re.S)
    if not match:
        raise ValueError("quarter line missing")
    return base.qline(match.group(1))


def event_blocks(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    starts = [match.start() for match in re.finditer(r'<div\s+class="bg coupon-row"', text, re.I)]
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        blocks.append(text[start:end])
    return blocks


def parse_event_block(block: str) -> dict[str, Any]:
    event_match = re.search(r'data-event-eventId="([0-9]+)"[^>]*data-event-treeId="([0-9]+)"[^>]*\s*data-event-name="([^"]+)"', block, re.I | re.S)
    if not event_match:
        raise ValueError("event identity attributes missing")
    event_id, tree_id, event_name = event_match.groups()
    event_name = html.unescape(event_name)
    if " vs " not in event_name:
        raise ValueError(f"event name has no exact vs delimiter: {event_name}")
    home, away = [part.strip() for part in event_name.split(" vs ", 1)]

    time_match = re.search(r'data-mutable-id="prematch-time".*?<span>\s*([^<]+?)\s*</span>', block, re.I | re.S)
    if not time_match:
        raise ValueError("prematch displayed time missing")
    displayed_time = " ".join(time_match.group(1).split())

    result_home = td_cell(block, "S_0_1_european")
    result_draw = td_cell(block, "S_0_2_european")
    result_away = td_cell(block, "S_0_3_european")
    handicap_home = td_cell(block, "S_2_1_european")
    handicap_away = td_cell(block, "S_2_3_european")
    total_under = td_cell(block, "S_3_1_european")
    total_over = td_cell(block, "S_3_3_european")

    home_line = cell_line(handicap_home)
    away_line = cell_line(handicap_away)
    under_line = cell_line(total_under)
    over_line = cell_line(total_over)
    if abs(home_line + away_line) > 1e-9:
        raise ValueError(f"handicap line symmetry failure: {home_line}/{away_line}")
    if abs(under_line - over_line) > 1e-9:
        raise ValueError(f"total line symmetry failure: {under_line}/{over_line}")

    return {
        "marathon_event_id": int(event_id),
        "marathon_tree_id": int(tree_id),
        "source_home": home,
        "source_away": away,
        "displayed_time": displayed_time,
        "one_x_two": {
            "home": cell_price(result_home),
            "draw": cell_price(result_draw),
            "away": cell_price(result_away),
        },
        "asian_handicap": {
            "line": home_line,
            "home": cell_price(handicap_home),
            "away": cell_price(handicap_away),
            "away_line": away_line,
        },
        "over_under": {
            "line": under_line,
            "under": cell_price(total_under),
            "over": cell_price(total_over),
        },
        "parse_status": "COMPLETE_MAIN_SURFACES_HTML",
    }


def write_schedule_envelope(payload: dict[str, Any], raw: bytes, url: str, observed: str) -> tuple[Path, str]:
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = SCHEDULE_RAW_ROOT / f"kambi_schedule_crosscheck__{token}__{digest[:12]}.json"
    envelope = {
        "schema_version": "V5.5.32-kambi-schedule-crosscheck-envelope-r1",
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "observed_at_utc": observed,
        "request_url": url,
        "payload_sha256": digest,
        "payload": payload,
        "allowed_use": "fixture_identity_and_kickoff_only",
        "market_values_copied": False,
        "formal_weight_change": False,
        "probability_change": False,
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("payload_sha256") != digest:
            raise FileExistsError(f"immutable Kambi schedule envelope collision: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, digest


def schedule_index(alias_maps: dict[str, dict[str, str]]) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], dict[str, Any]]:
    payload, raw, url, status, content_type, observed = kambi.fetch_json(kambi.LIST_URL, {**kambi.PARAMS, "useCombinedLive": "true"})
    raw_path, digest = write_schedule_envelope(payload, raw, url, observed)
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    unresolved = []
    accepted = 0
    for wrapper in payload.get("events", []):
        if not isinstance(wrapper, dict):
            continue
        event = kambi.event_payload(wrapper)
        mapped = kambi.GROUP_MAP.get(kambi.group_name(event))
        if mapped is None or str(event.get("state") or "") != "NOT_STARTED":
            continue
        cid, season = mapped
        aliases = alias_maps.get(cid) or {}
        source_home = str(event.get("homeName") or "")
        source_away = str(event.get("awayName") or "")
        home = aliases.get(base.norm(source_home))
        away = aliases.get(base.norm(source_away))
        if home is None or away is None:
            unresolved.append({"competition_id": cid, "source_home": source_home, "source_away": source_away})
            continue
        kickoff = kambi.dt(str(event.get("start"))).replace(microsecond=0)
        local = kickoff.astimezone(base.LONDON)
        index[(cid, home, away)].append({
            "competition_id": cid,
            "season": season,
            "canonical_home": home,
            "canonical_away": away,
            "source_home": source_home,
            "source_away": source_away,
            "kickoff_utc": kickoff.isoformat(),
            "kickoff_london_date_time": local.strftime("%d %b %H:%M"),
            "kickoff_london_time": local.strftime("%H:%M"),
            "event_id": event.get("id"),
        })
        accepted += 1
    audit = {
        "provider_group": "kambi",
        "observed_at_utc": observed,
        "request_url": url,
        "http_status": status,
        "content_type": content_type,
        "raw_response_sha256": digest,
        "raw_envelope_path": str(raw_path.relative_to(ROOT)),
        "accepted_exact_schedule_count": accepted,
        "unresolved_identity_count": len(unresolved),
        "unresolved_identities": unresolved[:100],
        "market_values_copied": False,
    }
    return index, audit


def resolve_kickoff(
    *,
    cid: str,
    home: str,
    away: str,
    displayed_time: str,
    schedule: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = schedule.get((cid, home, away), [])
    if re.fullmatch(r"\d{1,2} [A-Za-z]{3} \d{1,2}:\d{2}", displayed_time):
        matches = [row for row in candidates if row["kickoff_london_date_time"].lstrip("0") == displayed_time.lstrip("0")]
    elif re.fullmatch(r"\d{1,2}:\d{2}", displayed_time):
        matches = [row for row in candidates if row["kickoff_london_time"] == displayed_time]
    else:
        raise ValueError(f"unsupported Marathonbet displayed time: {displayed_time}")
    if len(matches) != 1:
        raise ValueError(f"Kambi exact schedule crosscheck count={len(matches)} for {cid}:{home}/{away}:{displayed_time}")
    return matches[0]


def main() -> int:
    _, registry_sha, alias_maps = base.load_registry()
    schedule, schedule_audit = schedule_index(alias_maps)
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.32-marathonbet-active-domain-html-status-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_ACTIVE_DOMAIN_SNAPSHOTS",
        "identity_registry_path": str(base.REGISTRY.relative_to(ROOT)),
        "identity_registry_sha256": registry_sha,
        "kickoff_crosscheck": schedule_audit,
        "target_competition_count": len(base.TARGETS),
        "leagues": [],
        "formal_snapshot_count_written": 0,
        "formal_snapshot_count_available": 0,
        "complete_surface_fixture_count": 0,
        "unresolved_identity_count": 0,
        "kickoff_crosscheck_fail_count": 0,
        "parse_fail_count": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "promotion_sample_count_change": 0,
    }

    index_raw, index_url, index_status, index_headers, index_observed = base.fetch(direct.ALL_EVENTS_URL)
    selected, candidates = direct.discover_category_urls(index_raw)
    receipt["category_discovery"] = {
        "observed_at_utc": index_observed,
        "source_url": index_url,
        "http_status": index_status,
        "response_headers": index_headers,
        "raw_html_sha256": hashlib.sha256(index_raw).hexdigest(),
        "matched_candidates": candidates,
        "selected_competition_count": len(selected),
    }

    for cid, cfg in base.TARGETS.items():
        aliases = alias_maps.get(cid) or {}
        chosen = selected.get(cid)
        league: dict[str, Any] = {
            "competition_id": cid,
            "season": cfg["season"],
            "status": "DIRECT_CATEGORY_URL_NOT_DISCOVERED",
            "fixtures": [],
            "discovered_category": chosen,
        }
        if not aliases:
            league["status"] = "IDENTITY_DOMAIN_UNAVAILABLE_FAIL_CLOSED"
            receipt["leagues"].append(league)
            continue
        if chosen is None:
            receipt["leagues"].append(league)
            continue

        try:
            raw, final_url, http_status, headers, observed = base.fetch(chosen["url"])
            digest = hashlib.sha256(raw).hexdigest()
            page_path = direct.raw_path(cid, observed, digest)
            page_path.parent.mkdir(parents=True, exist_ok=True)
            if page_path.exists():
                if hashlib.sha256(page_path.read_bytes()).hexdigest() != digest:
                    raise FileExistsError(f"immutable direct Marathonbet page collision: {page_path}")
            else:
                page_path.write_bytes(raw)
            blocks = event_blocks(raw)
            league["direct_page"] = {
                "observed_at_utc": observed,
                "final_url": final_url,
                "http_status": http_status,
                "response_headers": headers,
                "raw_html_sha256": digest,
                "raw_html_path": str(page_path.relative_to(ROOT)),
                "event_block_count": len(blocks),
                "matched_category": chosen["label"],
            }
        except Exception as exc:
            league["status"] = "DIRECT_CATEGORY_FETCH_FAIL_CLOSED"
            league["error"] = f"{type(exc).__name__}: {exc}"
            receipt["leagues"].append(league)
            continue

        written = available = complete = 0
        for block in blocks:
            try:
                fixture = parse_event_block(block)
            except Exception as exc:
                receipt["parse_fail_count"] += 1
                league["fixtures"].append({"parse_status": "HTML_MARKET_PARSE_FAIL_CLOSED", "error": f"{type(exc).__name__}: {exc}"})
                continue
            complete += 1
            receipt["complete_surface_fixture_count"] += 1
            home = aliases.get(base.norm(fixture["source_home"]))
            away = aliases.get(base.norm(fixture["source_away"]))
            fixture["identity_resolution"] = {
                "home": {"canonical": home, "source": "CURRENT_SEASON_EXACT_ALIAS" if home else "UNRESOLVED"},
                "away": {"canonical": away, "source": "CURRENT_SEASON_EXACT_ALIAS" if away else "UNRESOLVED"},
                "fuzzy_matching_used": False,
                "registry_sha256": registry_sha,
            }
            if home is None or away is None:
                receipt["unresolved_identity_count"] += 1
                fixture["formal_status"] = "IDENTITY_UNRESOLVED_FAIL_CLOSED"
                league["fixtures"].append(fixture)
                continue
            try:
                cross = resolve_kickoff(
                    cid=cid,
                    home=home,
                    away=away,
                    displayed_time=fixture["displayed_time"],
                    schedule=schedule,
                )
            except Exception as exc:
                receipt["kickoff_crosscheck_fail_count"] += 1
                fixture["formal_status"] = "KICKOFF_CROSSCHECK_FAIL_CLOSED"
                fixture["error"] = f"{type(exc).__name__}: {exc}"
                league["fixtures"].append(fixture)
                continue

            fixture["kickoff_utc"] = cross["kickoff_utc"]
            snapshot = direct.build_snapshot(
                cid=cid,
                season=cfg["season"],
                fixture=fixture,
                home=home,
                away=away,
                observed=observed,
                final_url=final_url,
                page_path=page_path,
                page_sha=digest,
                registry_sha=registry_sha,
                matched_category=chosen["label"],
            )
            snapshot["source_adapter"]["schema_version"] = "V5.5.32-marathonbet-active-domain-html-r1"
            snapshot["source_adapter"]["kickoff_crosscheck"] = {
                "provider_group": "kambi",
                "raw_envelope_path": schedule_audit["raw_envelope_path"],
                "raw_response_sha256": schedule_audit["raw_response_sha256"],
                "event_id": cross["event_id"],
                "source_home": cross["source_home"],
                "source_away": cross["source_away"],
                "kickoff_utc": cross["kickoff_utc"],
                "displayed_london_time": fixture["displayed_time"],
                "exact_canonical_pair_match": True,
                "market_values_copied": False,
            }
            snapshot.pop("raw_snapshot_sha256", None)
            snapshot["raw_snapshot_sha256"] = base.canonical_sha256(snapshot)
            validation = base.validate(snapshot)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                fixture["formal_status"] = "V523_FAIL_CLOSED"
                fixture["v523_errors"] = validation.get("errors")
                league["fixtures"].append(fixture)
                continue
            out = base.formal_path(snapshot)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable Marathonbet PIT collision: {out}")
                fixture["formal_status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                fixture["formal_status"] = "VALID_PIT_SNAPSHOT_WRITTEN"
                written += 1
                receipt["formal_snapshot_count_written"] += 1
            available += 1
            receipt["formal_snapshot_count_available"] += 1
            fixture["formal_snapshot_path"] = str(out.relative_to(ROOT))
            fixture["kickoff_crosscheck"] = snapshot["source_adapter"]["kickoff_crosscheck"]
            league["fixtures"].append(fixture)

        league.update({
            "status": "PASS_ACTIVE_DOMAIN_HTML_CAPTURE",
            "complete_surface_fixture_count": complete,
            "formal_snapshot_count_written": written,
            "formal_snapshot_count_available": available,
        })
        receipt["leagues"].append(league)

    if receipt["formal_snapshot_count_available"]:
        receipt["status"] = "PASS_ACTIVE_DOMAIN_MARATHONBET_PIT"
    receipt["policy"] = (
        "Marathonbet 1X2/AH/OU prices are parsed solely from one immutable direct league-page HTML response. "
        "Kambi listView is used only to supply an exact canonical fixture-pair kickoff date when Marathonbet displays time-only. "
        "No Kambi price, line or probability is copied. Fuzzy identity, historical fallback and synthetic markets are prohibited."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "formal_snapshot_count_available": receipt["formal_snapshot_count_available"],
        "unresolved_identity_count": receipt["unresolved_identity_count"],
        "kickoff_crosscheck_fail_count": receipt["kickoff_crosscheck_fail_count"],
        "parse_fail_count": receipt["parse_fail_count"],
        "league_statuses": {row["competition_id"]: row["status"] for row in receipt["leagues"]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
