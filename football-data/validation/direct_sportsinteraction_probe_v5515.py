#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_sportsinteraction_probe_v5515_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "sportsinteraction"
BASE = "https://sportsapi.sportsinteraction.com"
COUNTRY = "CA"
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.15; +https://github.com/FASHI188/FASHI188-football-analysis)"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_json(path: str, params: dict[str, object] | None = None, timeout: int = 40) -> tuple[object, bytes, str, int, str, str]:
    params = params or {}
    url = f"{BASE.rstrip('/')}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urlencode(params)}"
    observed = now_utc()
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed first-party Sports Interaction API only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    return data, raw, url, status, content_type, observed


def translations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        for key in ("text", "shortText"):
            token = item.get(key)
            if token and str(token) not in out:
                out.append(str(token))
    return out


def entity_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        for key in ("entityId", "id"):
            try:
                if value.get(key) is not None:
                    return int(value[key])
            except (TypeError, ValueError):
                pass
    if isinstance(value, list):
        for item in value:
            found = entity_id(item)
            if found is not None:
                return found
    return None


def sport_records(data: object) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "sports", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def sport_name(record: dict) -> str:
    names = translations(record.get("name"))
    if names:
        return names[0]
    for key in ("name", "sportName", "text"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    return ""


def choose_soccer(sports: list[dict]) -> tuple[int | None, dict | None]:
    ranked = []
    for record in sports:
        name = sport_name(record)
        token = name.lower()
        score = 2 if "soccer" in token else 1 if "football" in token and "american" not in token else 0
        sid = entity_id(record.get("id") if "id" in record else record)
        if score and sid is not None:
            ranked.append((score, sid, record))
    if not ranked:
        return None, None
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked[0][1], ranked[0][2]


def fixtures(data: object) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [x for x in data["items"] if isinstance(x, dict)]
    return []


def market_summary(market: dict) -> dict:
    options = market.get("options") if isinstance(market.get("options"), list) else []
    return {
        "id": market.get("id"),
        "name": translations(market.get("name")),
        "marketType": market.get("marketType"),
        "marketTemplateId": market.get("marketTemplateId"),
        "happening": market.get("happening"),
        "period": market.get("period"),
        "subPeriod": market.get("subPeriod"),
        "value": market.get("value"),
        "isBalancedLine": market.get("isBalancedLine"),
        "isDisplayed": market.get("isDisplayed"),
        "isOpenForBetting": market.get("isOpenForBetting"),
        "options": [
            {
                "id": option.get("id"),
                "name": translations(option.get("name")),
                "isDisplayed": option.get("isDisplayed"),
                "isOpenForBetting": option.get("isOpenForBetting"),
                "price": option.get("price"),
            }
            for option in options[:8]
            if isinstance(option, dict)
        ],
    }


def fixture_summary(fixture: dict) -> dict:
    markets = fixture.get("markets") if isinstance(fixture.get("markets"), list) else []
    participants = fixture.get("participants") if isinstance(fixture.get("participants"), list) else []
    return {
        "id": fixture.get("id"),
        "name": translations(fixture.get("name")),
        "startDateUtc": fixture.get("startDateUtc"),
        "cutOffDateUtc": fixture.get("cutOffDateUtc"),
        "isInPlay": fixture.get("isInPlay"),
        "isDisplayed": fixture.get("isDisplayed"),
        "isOpenForBetting": fixture.get("isOpenForBetting"),
        "state": fixture.get("state"),
        "competition": fixture.get("competition"),
        "region": fixture.get("region"),
        "participants": [
            {
                "id": p.get("id"),
                "name": translations(p.get("name")),
                "participantTag": p.get("participantTag"),
            }
            for p in participants if isinstance(p, dict)
        ],
        "markets": [market_summary(m) for m in markets if isinstance(m, dict)],
    }


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def write_sample(kind: str, observed: str, payload: object) -> tuple[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"{safe(kind)}__{token}__{digest[:12]}.json"
    envelope = {
        "schema_version": "V5.5.15-sportsinteraction-probe-sample-r1",
        "provider_name": "Sports Interaction",
        "provider_group": "entain_sportsinteraction",
        "observed_at_utc": observed,
        "kind": kind,
        "payload_sha256": digest,
        "payload": payload,
        "formal_evidence": False,
        "research_probe_only": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.15-direct-sportsinteraction-probe-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Sports Interaction",
        "provider_group": "entain_sportsinteraction",
        "status": "BLOCKED",
        "country": COUNTRY,
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "consensus_change": False,
        "policy": "First-party anonymous read-only capability probe. Sports Interaction is one Entain-family provider group and must never be counted independently from another Entain skin. No formal PIT snapshot until exact fixture identity and complete synchronized Full Time 1X2/AH/OU mapping pass V5.2.3.",
    }

    try:
        sports_data, sports_raw, sports_url, sports_status, sports_content_type, sports_observed = fetch_json(
            f"offer/api/{COUNTRY}/sports", {"language": "en"}
        )
        sports = sport_records(sports_data)
        soccer_id, soccer_record = choose_soccer(sports)
        sports_sample, sports_digest = write_sample("sports", sports_observed, sports_data)
        manifest["sports_probe"] = {
            "request_url": sports_url,
            "http_status": sports_status,
            "content_type": sports_content_type,
            "raw_response_sha256": hashlib.sha256(sports_raw).hexdigest(),
            "sample_path": sports_sample,
            "payload_sha256": sports_digest,
            "sport_count": len(sports),
            "soccer_sport_id": soccer_id,
            "soccer_record": soccer_record,
        }
        if soccer_id is None:
            raise ValueError("soccer/football sport id not found from official /sports response")

        fixture_data, fixture_raw, fixture_url, fixture_status, fixture_content_type, fixture_observed = fetch_json(
            f"offer/api/{soccer_id}/{COUNTRY}/fixtures",
            {
                "language": "en",
                "isInPlay": "false",
                "onlyMainMarkets": "true",
                "marketsFilterCriteria": "Visible",
            },
        )
        items = fixtures(fixture_data)
        if not isinstance(fixture_data, dict):
            raise ValueError("fixtures response is not an object")
        market_types: Counter[str] = Counter()
        periods: Counter[str] = Counter()
        happenings: Counter[str] = Counter()
        balanced = 0
        open_markets = 0
        option_counts: Counter[int] = Counter()
        for fixture in items:
            markets = fixture.get("markets") if isinstance(fixture.get("markets"), list) else []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                market_types[str(market.get("marketType"))] += 1
                periods[str(market.get("period"))] += 1
                happenings[str(market.get("happening"))] += 1
                options = market.get("options") if isinstance(market.get("options"), list) else []
                option_counts[len(options)] += 1
                balanced += int(market.get("isBalancedLine") is True)
                open_markets += int(market.get("isOpenForBetting") is True)

        sample_payload = fixture_summary(items[0]) if items else fixture_data
        fixture_sample, fixture_digest = write_sample("fixture", fixture_observed, sample_payload)
        manifest.update({
            "status": "PASS_OFFICIAL_SPORTSINTERACTION_FIXTURE_JSON",
            "fixture_probe": {
                "request_url": fixture_url,
                "http_status": fixture_status,
                "content_type": fixture_content_type,
                "raw_response_sha256": hashlib.sha256(fixture_raw).hexdigest(),
                "observed_at_utc": fixture_observed,
                "fixture_count": len(items),
                "balanced_market_count": balanced,
                "open_market_count": open_markets,
                "top_market_types": market_types.most_common(40),
                "top_periods": periods.most_common(30),
                "top_happenings": happenings.most_common(30),
                "option_count_distribution": sorted(option_counts.items()),
                "sample_path": fixture_sample,
                "sample_payload_sha256": fixture_digest,
            },
        })
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
