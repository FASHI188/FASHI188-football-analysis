#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_1xbet_pit_probe_v557_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "1xbet"

TARGETS = [
    {
        "competition_id": "POR_PrimeiraLiga",
        "season": "2026/27",
        "home_team": "Estoril Praia",
        "away_team": "FC Famalicão",
        "kickoff_utc": "2026-08-07T19:15:00+00:00",
        "known_event_id": None,
    },
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "home_team": "Deportivo Alavés",
        "away_team": "Getafe CF",
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
        "known_event_id": 350922834,
    },
    {
        "competition_id": "FRA_Ligue1",
        "season": "2026/27",
        "home_team": "Olympique de Marseille",
        "away_team": "RC Strasbourg Alsace",
        "kickoff_utc": "2026-08-21T18:45:00+00:00",
        "known_event_id": 348835554,
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "home_team": "FC Bayern München",
        "away_team": "VfB Stuttgart",
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
        "known_event_id": 347217272,
    },
]

# Regional bases are included only because those exact 1xBet domains have already
# exposed the target event pages in the project evidence. They remain one provider
# group and can never be counted as independent consensus sources.
DEFAULT_BASES = [
    "https://1xbet.com",
    "https://tw.1xbet.com",
    "https://ir.1xbet.com",
    "https://jo.1xbet.com",
]
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.7; +https://github.com/FASHI188/FASHI188-football-analysis)"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"\b(fc|cf|vfb|rc|olympique|deportivo)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity(a: object, b: object) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def fetch_json(
    base: str,
    path: str,
    params: dict[str, object],
    timeout: int = 25,
) -> tuple[dict, bytes, str, int]:
    """Fetch direct-provider JSON without treating HTTP 203 as an automatic failure.

    HTTP 203 is still a successful 2xx response class. It is not promoted to formal
    evidence here: the caller must additionally verify the expected JSON structure,
    event identity and market mapping. Raw status is preserved for auditability.
    """
    url = f"{base.rstrip('/')}/{path.lstrip('/')}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed bookmaker endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("provider response is not a JSON object")
    return data, raw, url, status


def choose_base() -> tuple[str | None, dict | None, bytes | None, str | None, int | None, list[dict]]:
    configured = os.environ.get("ONE_X_BET_BASES", "").strip()
    bases = [x.strip() for x in configured.split(",") if x.strip()] if configured else DEFAULT_BASES
    attempts: list[dict] = []
    params = {
        "sports": 1,
        "count": 1000,
        "lng": "en",
        "tf": 3000000,
        "mode": 4,
        "country": 1,
        "getEmpty": "true",
    }
    for base in bases:
        observed = now_utc()
        try:
            data, raw, url, http_status = fetch_json(base, "LineFeed/Get1x2_VZip", params)
            values = data.get("Value")
            if not isinstance(values, list):
                raise ValueError(f"response.Value is not a list (HTTP {http_status})")
            attempts.append({
                "base": base,
                "observed_at_utc": observed,
                "status": "PASS_JSON_STRUCTURE",
                "http_status": http_status,
                "event_count": len(values),
            })
            return base, data, raw, url, http_status, attempts
        except Exception as exc:  # fail closed; diagnostics only
            attempts.append({"base": base, "observed_at_utc": observed, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    return None, None, None, None, None, attempts


def event_identity(event: dict) -> tuple[str, str, object, object]:
    return str(event.get("O1") or ""), str(event.get("O2") or ""), event.get("I"), event.get("S")


def find_event(events: list[dict], target: dict) -> tuple[dict | None, dict]:
    best = None
    best_score = -1.0
    for event in events:
        if not isinstance(event, dict):
            continue
        home, away, _, _ = event_identity(event)
        score = (similarity(home, target["home_team"]) + similarity(away, target["away_team"])) / 2.0
        if score > best_score:
            best = event
            best_score = score
    meta = {"best_similarity": round(max(best_score, 0.0), 4)}
    if best is not None:
        home, away, event_id, start = event_identity(best)
        meta.update({"matched_home": home, "matched_away": away, "event_id": event_id, "provider_start": start})
    return (best if best_score >= 0.72 else None), meta


def flatten_odds(value: object, out: list[dict]) -> None:
    if isinstance(value, dict):
        if "T" in value and "C" in value:
            item = {k: value.get(k) for k in ("T", "G", "C", "B", "P") if k in value}
            out.append(item)
        for child in value.values():
            flatten_odds(child, out)
    elif isinstance(value, list):
        for child in value:
            flatten_odds(child, out)


def group_summary(game_value: dict) -> list[dict]:
    groups = game_value.get("GE")
    if not isinstance(groups, list):
        return []
    result: list[dict] = []
    for idx, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        odds: list[dict] = []
        flatten_odds(group.get("E"), odds)
        scalars = {k: v for k, v in group.items() if k != "E" and isinstance(v, (str, int, float, bool, type(None)))}
        result.append({
            "index": idx,
            "metadata": scalars,
            "odd_count": len(odds),
            "unique_T": sorted({str(x.get("T")) for x in odds if x.get("T") is not None}),
            "unique_G": sorted({str(x.get("G")) for x in odds if x.get("G") is not None}),
            "sample": odds[:12],
        })
    return result


def write_evidence(
    target: dict,
    base: str,
    url: str,
    observed: str,
    http_status: int,
    raw: bytes,
    data: dict,
) -> tuple[str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"{target['competition_id']}__{token}.json"
    payload = {
        "schema_version": "V5.5.7-direct-1xbet-probe-raw-envelope-r2",
        "provider_name": "1xBet",
        "provider_group": "1xbet",
        "provider_base": base,
        "request_url": url,
        "http_status": http_status,
        "observed_at_utc": observed,
        "target": target,
        "raw_response_sha256": digest,
        "raw_response": data,
        "formal_evidence": False,
        "research_probe_only": True,
        "http_203_policy": "2xx JSON may be frozen for research diagnostics only; HTTP status never substitutes for V5.2.3 market/identity/time hard gates",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    generated = now_utc()
    base, listing, listing_raw, listing_url, listing_http_status, base_attempts = choose_base()
    manifest: dict = {
        "schema_version": "V5.5.7-direct-1xbet-pit-probe-r2",
        "generated_at_utc": generated,
        "provider_name": "1xBet",
        "provider_group": "1xbet",
        "status": "BLOCKED_PROVIDER_UNREACHABLE_OR_INVALID_JSON",
        "base_attempts": base_attempts,
        "targets": [],
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Research probe only. A valid 2xx JSON body, including HTTP 203, may be frozen for diagnostics but is not a formal PIT snapshot. V5.2.3 remains fail-closed until complete synchronized 1X2 + two-sided Asian Handicap + two-sided Over/Under are mapped and validated from the same direct provider observation window.",
    }
    if base is None or listing is None or listing_raw is None or listing_url is None or listing_http_status is None:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    events = [x for x in listing.get("Value", []) if isinstance(x, dict)]
    manifest["status"] = "DIRECT_LISTING_JSON_REACHABLE"
    manifest["provider_base"] = base
    manifest["listing_http_status"] = listing_http_status
    manifest["listing_event_count"] = len(events)
    manifest["listing_response_sha256"] = hashlib.sha256(listing_raw).hexdigest()

    matched_count = 0
    detailed_count = 0
    for target in TARGETS:
        event, match_meta = find_event(events, target)
        candidate_id = (event or {}).get("I") if event else target.get("known_event_id")
        target_result: dict = {
            "target": target,
            "listing_match": bool(event),
            "match_meta": match_meta,
            "candidate_event_id": candidate_id,
            "game_fetch_status": "NOT_ATTEMPTED_NO_EVENT_ID",
            "formal_snapshot_eligible": False,
        }
        if event:
            matched_count += 1
        if candidate_id:
            observed = now_utc()
            try:
                game, raw, url, http_status = fetch_json(base, "LineFeed/GetGameZip", {
                    "id": candidate_id,
                    "lng": "en",
                    "cfview": 0,
                    "isSubGames": "true",
                    "GroupEvents": "true",
                    "allEventsGroupSubGames": "true",
                    "countevents": 500,
                })
                value = game.get("Value")
                if not isinstance(value, dict):
                    raise ValueError(f"GetGameZip.Value is not an object (HTTP {http_status})")
                provider_home = str(value.get("O1") or "")
                provider_away = str(value.get("O2") or "")
                home_score = similarity(provider_home, target["home_team"])
                away_score = similarity(provider_away, target["away_team"])
                identity_ok = home_score >= 0.72 and away_score >= 0.72
                target_result.update({
                    "game_fetch_status": "PASS" if identity_ok else "IDENTITY_MISMATCH",
                    "http_status": http_status,
                    "observed_at_utc": observed,
                    "provider_home": provider_home,
                    "provider_away": provider_away,
                    "identity_scores": {"home": round(home_score, 4), "away": round(away_score, 4)},
                    "provider_league": value.get("L") or value.get("LE"),
                    "provider_start": value.get("S"),
                })
                if identity_ok:
                    detailed_count += 1
                    evidence_path, digest = write_evidence(target, base, url, observed, http_status, raw, game)
                    odds: list[dict] = []
                    flatten_odds(value.get("E"), odds)
                    flatten_odds(value.get("GE"), odds)
                    target_result.update({
                        "raw_evidence_path": evidence_path,
                        "raw_response_sha256": digest,
                        "flat_odd_count": len(odds),
                        "unique_T": sorted({str(x.get("T")) for x in odds if x.get("T") is not None}),
                        "unique_G": sorted({str(x.get("G")) for x in odds if x.get("G") is not None}),
                        "market_group_summary": group_summary(value),
                        "market_mapping_status": "UNMAPPED_DIRECT_PROVIDER_TYPE_CODES",
                        "formal_snapshot_eligible": False,
                        "reason": "Raw direct-provider JSON frozen successfully. Market type codes must be mapped and regression-tested before any 1X2/AH/OU values can enter the formal V5.2.3 writer.",
                    })
            except Exception as exc:
                target_result.update({"game_fetch_status": "FAIL", "observed_at_utc": observed, "error": f"{type(exc).__name__}: {exc}"})
        manifest["targets"].append(target_result)

    manifest["listing_matched_target_count"] = matched_count
    manifest["identity_verified_detailed_target_count"] = detailed_count
    if detailed_count:
        manifest["status"] = "PASS_DIRECT_GAME_JSON_FROZEN_MAPPING_REQUIRED"
    elif matched_count:
        manifest["status"] = "PARTIAL_LISTING_MATCH_NO_VERIFIED_GAME_JSON"
    else:
        manifest["status"] = "DIRECT_LISTING_JSON_REACHABLE_TARGETS_NOT_MATCHED"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
