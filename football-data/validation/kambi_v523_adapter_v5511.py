#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
import sys
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import canonical_sha256, validate

EVIDENCE_ROOT = ROOT / "evidence" / "markets_prospective"


def _dt(value: str) -> datetime:
    token = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(token)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _utc(value: str) -> str:
    return _dt(value).replace(microsecond=0).isoformat()


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"\b(fc|cf|vfb|rc|olympique|deportivo)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _team_match(value: object, target: str) -> bool:
    a, b = _norm(value), _norm(target)
    return bool(a and b and (a == b or a in b or b in a))


def _outcomes(offer: dict[str, Any]) -> list[dict[str, Any]]:
    value = offer.get("outcomes")
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _criterion(offer: dict[str, Any]) -> dict[str, Any]:
    value = offer.get("criterion")
    return value if isinstance(value, dict) else {}


def _offer_type(offer: dict[str, Any]) -> dict[str, Any]:
    value = offer.get("betOfferType")
    return value if isinstance(value, dict) else {}


def _tags(offer: dict[str, Any]) -> set[str]:
    value = offer.get("tags")
    return {str(x) for x in value} if isinstance(value, list) else set()


def _odds(value: object) -> float:
    number = int(value)
    result = number / 1000.0
    if not math.isfinite(result) or result <= 1.0:
        raise ValueError(f"invalid Kambi odds milli value: {value}")
    return result


def _line(value: object) -> float:
    number = int(value) / 1000.0
    if not math.isfinite(number):
        raise ValueError(f"invalid Kambi line milli value: {value}")
    if abs(number * 4.0 - round(number * 4.0)) > 1e-9:
        raise ValueError(f"Kambi line is not quarter increment after /1000: {value}")
    return number


def _changed_times(offer: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in _outcomes(offer):
        value = item.get("changedDate")
        if value:
            out.append(_utc(str(value)))
    return sorted(set(out))


def _is_full_time(offer: dict[str, Any]) -> bool:
    criterion = _criterion(offer)
    lifetime = str(criterion.get("lifetime") or "").upper()
    english = str(criterion.get("englishLabel") or "").strip().lower()
    if "1st half" in english or "2nd half" in english or "half time" in english:
        return False
    return lifetime in {"", "FULL_TIME"}


def _open_two_sided(offer: dict[str, Any]) -> bool:
    outs = _outcomes(offer)
    return len(outs) == 2 and all(str(x.get("status") or "").upper() == "OPEN" for x in outs)


def _balanced_score(price_a: float, price_b: float) -> tuple[float, float]:
    ia, ib = 1.0 / price_a, 1.0 / price_b
    total = ia + ib
    if total <= 0:
        raise ValueError("non-positive implied total")
    p_a = ia / total
    return abs(p_a - 0.5), abs(total - 1.0)


def _select_center(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ValueError("no eligible market candidates")
    main = [x for x in candidates if x.get("main_line_tag")]
    pool = main if main else candidates
    return sorted(
        pool,
        key=lambda x: (
            float(x["balance_distance"]),
            float(x["overround_abs"]),
            abs(float(x["line"])),
            int(x["offer_id"]),
        ),
    )[0]


def extract_full_time_1x2(payload: dict[str, Any], home_team: str, away_team: str) -> dict[str, Any]:
    candidates = []
    for offer in payload.get("betOffers", []):
        if not isinstance(offer, dict) or not _is_full_time(offer):
            continue
        criterion = _criterion(offer)
        offer_type = _offer_type(offer)
        english = str(criterion.get("englishLabel") or "").strip().lower()
        if int(offer_type.get("id") or -1) != 2 or english != "full time":
            continue
        outs = _outcomes(offer)
        if len(outs) != 3 or any(str(x.get("status") or "").upper() != "OPEN" for x in outs):
            continue
        by_type = {str(x.get("type") or ""): x for x in outs}
        if set(by_type) != {"OT_ONE", "OT_CROSS", "OT_TWO"}:
            continue
        if not _team_match(by_type["OT_ONE"].get("participant") or home_team, home_team):
            continue
        if not _team_match(by_type["OT_TWO"].get("participant") or away_team, away_team):
            continue
        candidates.append({
            "offer_id": int(offer.get("id")),
            "home": _odds(by_type["OT_ONE"].get("odds")),
            "draw": _odds(by_type["OT_CROSS"].get("odds")),
            "away": _odds(by_type["OT_TWO"].get("odds")),
            "provider_changed_at_utc": _changed_times(offer),
            "main_line_tag": "MAIN_LINE" in _tags(offer),
        })
    if not candidates:
        raise ValueError("no exact Full Time 1X2 offer")
    main = [x for x in candidates if x["main_line_tag"]]
    return sorted(main or candidates, key=lambda x: int(x["offer_id"]))[0]


def extract_full_time_ah(payload: dict[str, Any], home_team: str, away_team: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for offer in payload.get("betOffers", []):
        if not isinstance(offer, dict) or not _is_full_time(offer) or not _open_two_sided(offer):
            continue
        criterion = _criterion(offer)
        offer_type = _offer_type(offer)
        if int(offer_type.get("id") or -1) != 7:
            continue
        if str(criterion.get("englishLabel") or "").strip().lower() != "asian handicap":
            continue
        outs = _outcomes(offer)
        home = next((x for x in outs if _team_match(x.get("participant") or x.get("englishLabel"), home_team)), None)
        away = next((x for x in outs if _team_match(x.get("participant") or x.get("englishLabel"), away_team)), None)
        if home is None or away is None:
            continue
        home_line, away_line = _line(home.get("line")), _line(away.get("line"))
        if abs(home_line + away_line) > 1e-9:
            continue
        home_odds, away_odds = _odds(home.get("odds")), _odds(away.get("odds"))
        balance, overround_abs = _balanced_score(home_odds, away_odds)
        candidates.append({
            "offer_id": int(offer.get("id")),
            "line": home_line,
            "away_line": away_line,
            "home": home_odds,
            "away": away_odds,
            "balance_distance": balance,
            "overround_abs": overround_abs,
            "main_line_tag": "MAIN_LINE" in _tags(offer),
            "provider_changed_at_utc": _changed_times(offer),
        })
    return _select_center(candidates), candidates


def extract_full_time_ou(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for offer in payload.get("betOffers", []):
        if not isinstance(offer, dict) or not _is_full_time(offer) or not _open_two_sided(offer):
            continue
        criterion = _criterion(offer)
        offer_type = _offer_type(offer)
        if int(offer_type.get("id") or -1) != 6:
            continue
        if str(criterion.get("englishLabel") or "").strip().lower() != "total goals":
            continue
        by_type = {str(x.get("type") or ""): x for x in _outcomes(offer)}
        if set(by_type) != {"OT_OVER", "OT_UNDER"}:
            continue
        over_line, under_line = _line(by_type["OT_OVER"].get("line")), _line(by_type["OT_UNDER"].get("line"))
        if abs(over_line - under_line) > 1e-9:
            continue
        over_odds, under_odds = _odds(by_type["OT_OVER"].get("odds")), _odds(by_type["OT_UNDER"].get("odds"))
        balance, overround_abs = _balanced_score(over_odds, under_odds)
        candidates.append({
            "offer_id": int(offer.get("id")),
            "line": over_line,
            "over": over_odds,
            "under": under_odds,
            "balance_distance": balance,
            "overround_abs": overround_abs,
            "main_line_tag": "MAIN_LINE" in _tags(offer),
            "provider_changed_at_utc": _changed_times(offer),
        })
    return _select_center(candidates), candidates


def _event_identity(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0]
    event = payload.get("event")
    return event if isinstance(event, dict) else {}


def extract(envelope: dict[str, Any], *, home_team: str, away_team: str) -> dict[str, Any]:
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
    if not isinstance(payload, dict):
        raise ValueError("Kambi payload missing")
    one = extract_full_time_1x2(payload, home_team, away_team)
    ah, ah_candidates = extract_full_time_ah(payload, home_team, away_team)
    ou, ou_candidates = extract_full_time_ou(payload)
    return {
        "one_x_two": one,
        "asian_handicap": ah,
        "over_under": ou,
        "candidate_counts": {
            "one_x_two": 1,
            "asian_handicap": len(ah_candidates),
            "over_under": len(ou_candidates),
        },
        "asian_handicap_candidates": ah_candidates,
        "over_under_candidates": ou_candidates,
        "event_identity": _event_identity(payload),
    }


def build_snapshot(
    envelope: dict[str, Any],
    *,
    competition_id: str,
    season: str,
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    observed_at_utc: str | None = None,
) -> dict[str, Any]:
    extracted = extract(envelope, home_team=home_team, away_team=away_team)
    observed = _utc(observed_at_utc or str(envelope.get("observed_at_utc") or ""))
    kickoff = _utc(kickoff_utc)
    if not _dt(observed) < _dt(kickoff):
        raise ValueError("Kambi observation must precede kickoff")
    event_id = int(envelope.get("event_id") or 0)
    source_url = f"https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event/{event_id}.json"
    one, ah, ou = extracted["one_x_two"], extracted["asian_handicap"], extracted["over_under"]
    payload: dict[str, Any] = {
        "competition_id": competition_id,
        "season": season,
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff,
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": observed,
        "accessed_at_utc": observed,
        "source_observed_at_utc": observed,
        "surface_observed_at_utc": {
            "one_x_two": observed,
            "asian_handicap": observed,
            "over_under": observed,
        },
        "source_url": source_url,
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "one_x_two": {"home": one["home"], "draw": one["draw"], "away": one["away"]},
        "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
        "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
        "source_adapter": {
            "schema_version": "V5.5.11-kambi-v523-adapter-r1",
            "raw_envelope_sha256": str(envelope.get("payload_sha256") or ""),
            "kambi_integer_scaling": {"odds_divisor": 1000, "line_divisor": 1000},
            "one_x_two_offer_id": one["offer_id"],
            "asian_handicap_offer_id": ah["offer_id"],
            "over_under_offer_id": ou["offer_id"],
            "asian_handicap_selection": "MAIN_LINE tag when available, otherwise minimum devig balance distance to 50/50; tie break lowest overround error, absolute home line, offer id",
            "over_under_selection": "MAIN_LINE tag when available, otherwise minimum devig balance distance to 50/50; tie break lowest overround error, line magnitude, offer id",
            "asian_handicap_candidate_count": extracted["candidate_counts"]["asian_handicap"],
            "over_under_candidate_count": extracted["candidate_counts"]["over_under"],
            "provider_changed_at_utc": {
                "one_x_two": one["provider_changed_at_utc"],
                "asian_handicap": ah["provider_changed_at_utc"],
                "over_under": ou["provider_changed_at_utc"],
            },
            "provider_changed_timestamp_role": "audit_only_not_observation_time",
        },
        "observation_semantics": {
            "source_observed_at_utc": "actual system observation time for the direct Kambi event-detail response",
            "surface_observed_at_utc": "same HTTP observation timestamp because all three surfaces came from one event-detail response",
            "retrospective_backfill": False,
        },
    }
    payload["raw_snapshot_sha256"] = canonical_sha256(payload)
    result = validate(payload)
    if not result.get("passed"):
        raise ValueError(f"V5.2.3 snapshot hard gate failed: {result.get('errors')}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_envelope")
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--home-team", required=True)
    parser.add_argument("--away-team", required=True)
    parser.add_argument("--kickoff-utc", required=True)
    parser.add_argument("--observed-at-utc")
    parser.add_argument("--write-snapshot", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()

    envelope = json.loads(Path(args.raw_envelope).read_text(encoding="utf-8"))
    snapshot = build_snapshot(
        envelope,
        competition_id=args.competition_id,
        season=args.season,
        home_team=args.home_team,
        away_team=args.away_team,
        kickoff_utc=args.kickoff_utc,
        observed_at_utc=args.observed_at_utc,
    )
    result = {
        "status": "V523_KAMBI_ADAPTER_PASS",
        "formal_pit_eligible": validate(snapshot)["formal_pit_eligible"],
        "one_x_two": snapshot["one_x_two"],
        "asian_handicap": snapshot["asian_handicap"],
        "over_under": snapshot["over_under"],
        "source_adapter": snapshot["source_adapter"],
        "raw_snapshot_sha256": snapshot["raw_snapshot_sha256"],
        "snapshot_written": False,
    }
    if args.write_snapshot:
        out = Path(args.out) if args.out else EVIDENCE_ROOT / (
            f"{competition_id_safe(args.competition_id)}__{competition_id_safe(args.home_team)}__"
            f"{competition_id_safe(args.away_team)}__kambi__{snapshot['freeze_utc'].replace(':', '').replace('+00:00', 'Z')}.json"
        )
        if out.exists():
            raise FileExistsError(f"immutable snapshot already exists: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        result["snapshot_written"] = True
        result["path"] = str(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def competition_id_safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
