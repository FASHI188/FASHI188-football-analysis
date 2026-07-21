#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_bwin_sportsapi_probe_v5513_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "bwin"
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.13; +https://github.com/FASHI188/FASHI188-football-analysis)"

BASES = [
    "https://sportsapi.bwin.com",
    "https://sportsapi.bwin.co",
]
COUNTRIES = ["DE", "GB", "NL"]
SPORT_ID = 4  # official bwin deep-link docs: football/soccer SportID=4


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def fetch_json(url: str, params: dict[str, object], timeout: int = 40) -> tuple[dict, bytes, str, int, str, str]:
    full_url = f"{url}?{urlencode(params)}"
    observed = now_utc()
    req = Request(full_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed official bwin Sports API endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return data, raw, full_url, status, content_type, observed


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


def market_summary(market: dict) -> dict:
    options = market.get("options") if isinstance(market.get("options"), list) else []
    return {
        "id": market.get("id"),
        "name": translations(market.get("name")),
        "marketType": market.get("marketType"),
        "happening": market.get("happening"),
        "period": market.get("period"),
        "subPeriod": market.get("subPeriod"),
        "value": market.get("value"),
        "isBalancedLine": market.get("isBalancedLine"),
        "isDisplayed": market.get("isDisplayed"),
        "isOpenForBetting": market.get("isOpenForBetting"),
        "option_count": len(options),
        "options": [
            {
                "id": option.get("id"),
                "name": translations(option.get("name")),
                "isDisplayed": option.get("isDisplayed"),
                "isOpenForBetting": option.get("isOpenForBetting"),
                "price": option.get("price"),
            }
            for option in options[:6]
            if isinstance(option, dict)
        ],
    }


def fixture_summary(fixture: dict) -> dict:
    participants = fixture.get("participants") if isinstance(fixture.get("participants"), list) else []
    markets = fixture.get("markets") if isinstance(fixture.get("markets"), list) else []
    ids = fixture.get("id") if isinstance(fixture.get("id"), list) else []
    return {
        "id": ids,
        "name": translations(fixture.get("name")),
        "startDateUtc": fixture.get("startDateUtc"),
        "cutOffDateUtc": fixture.get("cutOffDateUtc"),
        "isInPlay": fixture.get("isInPlay"),
        "isDisplayed": fixture.get("isDisplayed"),
        "isOpenForBetting": fixture.get("isOpenForBetting"),
        "state": fixture.get("state"),
        "competition": fixture.get("competition"),
        "participants": [
            {"id": p.get("id"), "name": translations(p.get("name")), "participantTag": p.get("participantTag")}
            for p in participants
            if isinstance(p, dict)
        ],
        "market_count": len(markets),
        "markets": [market_summary(m) for m in markets[:40] if isinstance(m, dict)],
    }


def write_sample(base: str, country: str, observed: str, fixture: dict) -> tuple[str, str]:
    payload = fixture_summary(fixture)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"{safe(country)}__sample__{token}__{digest[:12]}.json"
    envelope = {
        "schema_version": "V5.5.13-bwin-sportsapi-sample-r1",
        "provider_name": "bwin Sports API",
        "provider_group": "entain_bwin",
        "base": base,
        "country": country,
        "observed_at_utc": observed,
        "fixture_summary_sha256": digest,
        "fixture": payload,
        "formal_evidence": False,
        "research_probe_only": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.13-direct-bwin-sportsapi-probe-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "bwin Sports API",
        "provider_group": "entain_bwin",
        "status": "NO_REACHABLE_OFFICIAL_FIXTURE_JSON",
        "attempts": [],
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Official bwin Sports API reachability/capability probe only. Different bwin API hostnames are one provider_group=entain_bwin. No formal PIT snapshot until exact fixture identity and complete synchronized 1X2/AH/OU mapping pass the V5.2.3 hard gate.",
    }

    reachable = 0
    fixture_total = 0
    balanced_market_total = 0
    sample_paths: list[str] = []
    market_types: Counter[str] = Counter()
    periods: Counter[str] = Counter()
    happenings: Counter[str] = Counter()
    option_counts: Counter[int] = Counter()

    for base in BASES:
        for country in COUNTRIES:
            endpoint = f"{base}/offer/api/{SPORT_ID}/{country}/fixtures"
            attempt = {
                "base": base,
                "country": country,
                "observed_at_utc": now_utc(),
                "status": "FAIL",
            }
            try:
                data, raw, url, status, content_type, observed = fetch_json(
                    endpoint,
                    {
                        "language": "en",
                        "isInPlay": "false",
                        "onlyMainMarkets": "true",
                        "marketsFilterCriteria": "Visible",
                        "_": int(time.time() * 1000),
                    },
                )
                items = data.get("items")
                if not isinstance(items, list):
                    raise ValueError("response.items is not a list")
                reachable += 1
                fixture_total += len(items)
                local_balanced = 0
                for fixture in items:
                    if not isinstance(fixture, dict):
                        continue
                    markets = fixture.get("markets") if isinstance(fixture.get("markets"), list) else []
                    for market in markets:
                        if not isinstance(market, dict):
                            continue
                        market_types[str(market.get("marketType"))] += 1
                        periods[str(market.get("period"))] += 1
                        happenings[str(market.get("happening"))] += 1
                        options = market.get("options") if isinstance(market.get("options"), list) else []
                        option_counts[len(options)] += 1
                        if market.get("isBalancedLine") is True:
                            balanced_market_total += 1
                            local_balanced += 1
                sample = None
                if items:
                    sample, _ = write_sample(base, country, observed, items[0])
                    sample_paths.append(sample)
                attempt.update({
                    "status": "PASS_JSON_STRUCTURE",
                    "request_url": url,
                    "http_status": status,
                    "content_type": content_type,
                    "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                    "fixture_count": len(items),
                    "balanced_market_count": local_balanced,
                    "sample_path": sample,
                })
            except Exception as exc:
                attempt["error"] = f"{type(exc).__name__}: {exc}"
            manifest["attempts"].append(attempt)

    manifest.update({
        "reachable_attempt_count": reachable,
        "fixture_count_across_reachable_attempts": fixture_total,
        "balanced_market_count_across_reachable_attempts": balanced_market_total,
        "top_market_types": market_types.most_common(30),
        "top_periods": periods.most_common(20),
        "top_happenings": happenings.most_common(20),
        "option_count_distribution": sorted(option_counts.items()),
        "sample_paths": sample_paths,
    })
    if reachable:
        manifest["status"] = "PASS_OFFICIAL_BWIN_FIXTURE_JSON_REACHABLE"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
