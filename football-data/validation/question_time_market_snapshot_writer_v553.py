#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import canonical_sha256, validate

EVIDENCE_ROOT = ROOT / "evidence" / "markets_prospective"
MAX_RESEARCH_REFERENCE_SKEW_SECONDS = 300


def _dt(value: str) -> datetime:
    token = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(token)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must carry timezone: {value}")
    return dt.astimezone(timezone.utc)


def _utc(value: str) -> str:
    return _dt(value).replace(microsecond=0).isoformat()


def _safe_token(value: str) -> str:
    out = []
    for ch in str(value):
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "unknown"


def _optional_ou25_reference(args, *, source_observed: str, kickoff_utc: str, main_surface_times: list[str]):
    over = getattr(args, "ou25_over_odds", None)
    under = getattr(args, "ou25_under_odds", None)
    observed = getattr(args, "ou25_observed_at_utc", None)
    supplied = [over is not None, under is not None, bool(observed)]
    if not any(supplied):
        return None
    if not all(supplied):
        raise ValueError("OU2.5 research reference requires over, under and observed_at together")
    over = float(over)
    under = float(under)
    if over <= 1.0 or under <= 1.0:
        raise ValueError("OU2.5 research reference decimal odds must be >1.0")
    observed_utc = _utc(str(observed))
    observed_dt = _dt(observed_utc)
    if observed_dt >= _dt(kickoff_utc):
        raise ValueError("OU2.5 research reference must be observed before kickoff")
    comparison_times = [_dt(value) for value in main_surface_times] + [observed_dt]
    skew = (max(comparison_times) - min(comparison_times)).total_seconds()
    if skew > MAX_RESEARCH_REFERENCE_SKEW_SECONDS:
        raise ValueError(f"OU2.5 research reference exceeds {MAX_RESEARCH_REFERENCE_SKEW_SECONDS}s synchronization window")
    return {
        "over_under_2_5": {
            "line": 2.5,
            "over": over,
            "under": under,
            "observed_at_utc": observed_utc,
            "role": "fixed_research_reference_surface",
        }
    }


def build(args) -> dict:
    freeze = _utc(args.freeze_utc)
    accessed = _utc(args.accessed_at_utc or freeze)
    source_observed = _utc(args.source_observed_at_utc or freeze)
    one_seen = _utc(args.one_x_two_observed_at_utc or source_observed)
    ah_seen = _utc(args.asian_handicap_observed_at_utc or source_observed)
    ou_seen = _utc(args.over_under_observed_at_utc or source_observed)
    kickoff_utc = _utc(args.kickoff_utc)
    payload = {
        "competition_id": args.competition_id,
        "season": args.season,
        "home_team": args.home_team,
        "away_team": args.away_team,
        "kickoff_utc": kickoff_utc,
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": freeze,
        "accessed_at_utc": accessed,
        "source_observed_at_utc": source_observed,
        "surface_observed_at_utc": {
            "one_x_two": one_seen,
            "asian_handicap": ah_seen,
            "over_under": ou_seen,
        },
        "source_url": args.source_url,
        "provider_name": args.provider_name,
        "provider_group": args.provider_group,
        "one_x_two": {
            "home": float(args.home_odds),
            "draw": float(args.draw_odds),
            "away": float(args.away_odds),
        },
        "asian_handicap": {
            "line": float(args.ah_line),
            "home": float(args.ah_home_odds),
            "away": float(args.ah_away_odds),
        },
        "over_under": {
            "line": float(args.ou_line),
            "over": float(args.over_odds),
            "under": float(args.under_odds),
        },
        "observation_semantics": {
            "source_observed_at_utc": "timestamp when this system actually observed the quoted market at question time; not a retrospective provider-update timestamp",
            "surface_observed_at_utc": "actual observation timestamps for each captured main market surface",
            "research_reference_surfaces": "optional fixed research references are preserved separately from the actual main market line and are included in the immutable snapshot hash",
            "retrospective_backfill": False,
        },
    }
    research_reference = _optional_ou25_reference(
        args,
        source_observed=source_observed,
        kickoff_utc=kickoff_utc,
        main_surface_times=[one_seen, ah_seen, ou_seen],
    )
    if research_reference:
        payload["research_reference_surfaces"] = research_reference
    payload["raw_snapshot_sha256"] = canonical_sha256(payload)
    result = validate(payload)
    if not result.get("passed"):
        raise ValueError(f"V5.2.3 snapshot hard gate failed: {result.get('errors')}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--home-team", required=True)
    parser.add_argument("--away-team", required=True)
    parser.add_argument("--kickoff-utc", required=True)
    parser.add_argument("--freeze-utc", required=True)
    parser.add_argument("--accessed-at-utc")
    parser.add_argument("--source-observed-at-utc")
    parser.add_argument("--one-x-two-observed-at-utc")
    parser.add_argument("--asian-handicap-observed-at-utc")
    parser.add_argument("--over-under-observed-at-utc")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--provider-name", required=True)
    parser.add_argument("--provider-group", required=True)
    parser.add_argument("--home-odds", required=True, type=float)
    parser.add_argument("--draw-odds", required=True, type=float)
    parser.add_argument("--away-odds", required=True, type=float)
    parser.add_argument("--ah-line", required=True, type=float)
    parser.add_argument("--ah-home-odds", required=True, type=float)
    parser.add_argument("--ah-away-odds", required=True, type=float)
    parser.add_argument("--ou-line", required=True, type=float, help="Actual observed main OU line at question time")
    parser.add_argument("--over-odds", required=True, type=float)
    parser.add_argument("--under-odds", required=True, type=float)
    parser.add_argument("--ou25-over-odds", type=float, help="Optional fixed OU2.5 research-reference over price")
    parser.add_argument("--ou25-under-odds", type=float, help="Optional fixed OU2.5 research-reference under price")
    parser.add_argument("--ou25-observed-at-utc", help="Observation timestamp for optional fixed OU2.5 reference")
    parser.add_argument("--out")
    args = parser.parse_args()

    payload = build(args)
    if args.out:
        out = Path(args.out)
    else:
        freeze_token = payload["freeze_utc"].replace(":", "").replace("+00:00", "Z")
        filename = "__".join([
            _safe_token(payload["competition_id"]),
            _safe_token(payload["home_team"]),
            _safe_token(payload["away_team"]),
            _safe_token(payload["provider_group"]),
            _safe_token(freeze_token),
        ]) + ".json"
        out = EVIDENCE_ROOT / filename
    if out.exists():
        raise FileExistsError(f"immutable snapshot already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "VALID_PIT_SNAPSHOT_WRITTEN",
        "path": str(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out),
        "raw_snapshot_sha256": payload["raw_snapshot_sha256"],
        "has_ou25_research_reference": bool((payload.get("research_reference_surfaces") or {}).get("over_under_2_5")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
