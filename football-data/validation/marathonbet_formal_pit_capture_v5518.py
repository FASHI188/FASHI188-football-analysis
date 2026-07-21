#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from marathonbet_v523_adapter_v5517 import parse_fixture
from prospective_market_snapshot_v523 import canonical_sha256, validate

FORMAL_ROOT = ROOT / "evidence" / "markets_prospective"
MANIFEST = ROOT / "manifests" / "marathonbet_formal_pit_capture_v5518_status.json"

CASES = [
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "canonical_home_team": "Deportivo Alavés",
        "canonical_away_team": "Getafe CF",
        "source_home_team": "Alaves",
        "source_away_team": "Getafe",
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
        "official_kickoff_source": "https://www.laliga.com/en-ES/laliga-easports/results",
        "official_kickoff_basis": "LALIGA 2026/27 Matchday 1: 15 Aug 2026 19:30 Spain local (CEST) = 17:30 UTC",
        "html": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "ESP_LaLiga__2026-07-21T173148+0000__2fa6189d33e8.html",
        "meta": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "ESP_LaLiga__2026-07-21T173148+0000__2fa6189d33e8.json",
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "canonical_home_team": "FC Bayern München",
        "canonical_away_team": "VfB Stuttgart",
        "source_home_team": "Bayern Munich",
        "source_away_team": "Stuttgart",
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
        "official_kickoff_source": "https://products.bundesliga.com/fixtures",
        "official_kickoff_basis": "Bundesliga Matchday 1 official fixture feed: 28 Aug 2026 18:30 UTC / 20:30 CEST",
        "html": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "GER_Bundesliga__2026-07-21T173150+0000__398543dc3b50.html",
        "meta": ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "GER_Bundesliga__2026-07-21T173150+0000__398543dc3b50.json",
    },
]


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _utc(value: str) -> str:
    return _dt(value).replace(microsecond=0).isoformat()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def build_formal_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    raw = case["html"].read_bytes()
    metadata = json.loads(case["meta"].read_text(encoding="utf-8"))
    observed = _utc(str(metadata.get("observed_at_utc") or ""))
    kickoff = _utc(case["kickoff_utc"])
    if not _dt(observed) < _dt(kickoff):
        raise ValueError("Marathonbet formal observation must precede kickoff")
    requested_url = str(metadata.get("requested_url") or "")
    final_url = str(metadata.get("final_url") or "")
    if "/en/" not in requested_url or "/en/" not in final_url:
        raise ValueError("formal capture requires accepted /en/ timezone contract")
    expected_raw_sha = str(metadata.get("raw_html_sha256") or "")
    actual_raw_sha = hashlib.sha256(raw).hexdigest()
    if not expected_raw_sha or expected_raw_sha != actual_raw_sha:
        raise ValueError("raw HTML SHA256 mismatch")

    parsed = parse_fixture(
        raw,
        home_team=case["source_home_team"],
        away_team=case["source_away_team"],
        target_kickoff_utc=kickoff,
    )
    if parsed["kickoff_skew_seconds"] != 0.0:
        raise ValueError("accepted target requires exact kickoff-time conversion match")
    ah = parsed["asian_handicap"]
    ou = parsed["over_under"]
    snapshot: dict[str, Any] = {
        "competition_id": case["competition_id"],
        "season": case["season"],
        "home_team": case["canonical_home_team"],
        "away_team": case["canonical_away_team"],
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
        "source_url": final_url,
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "one_x_two": parsed["one_x_two"],
        "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
        "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
        "source_adapter": {
            "schema_version": "V5.5.18-marathonbet-formal-pit-capture-r1",
            "accepted_parser": "V5.5.17-marathonbet-v523-adapter-r1",
            "parser_acceptance_receipt": "football-data/manifests/marathonbet_v523_adapter_v5517_status.json",
            "parent_raw_html_path": str(case["html"].relative_to(ROOT)),
            "parent_metadata_path": str(case["meta"].relative_to(ROOT)),
            "parent_raw_html_sha256": actual_raw_sha,
            "source_display_names": {
                "home": case["source_home_team"],
                "away": case["source_away_team"],
            },
            "canonical_identity": {
                "home": case["canonical_home_team"],
                "away": case["canonical_away_team"],
            },
            "requested_url": requested_url,
            "final_url": final_url,
            "html_locale": "en",
            "display_timezone": parsed["display_timezone"],
            "displayed_kickoff": parsed["displayed_time"],
            "displayed_kickoff_converted_utc": parsed["page_kickoff_utc"],
            "kickoff_skew_seconds": parsed["kickoff_skew_seconds"],
            "official_kickoff_source": case["official_kickoff_source"],
            "official_kickoff_basis": case["official_kickoff_basis"],
            "handicap_away_line_audit": ah["away_line"],
            "parsing_policy": "Exact source-display fixture header and same-response Match Result, two-sided handicap, Total Goals; source display names are mapped once to canonical project identity after exact kickoff-time verification.",
        },
        "observation_semantics": {
            "source_observed_at_utc": "actual GitHub Runner observation time of the immutable direct first-party Marathonbet HTML response",
            "surface_observed_at_utc": "same timestamp for all three surfaces because all values came from one HTML response",
            "retrospective_backfill": False,
        },
        "promotion_semantics": {
            "single_provider_pit_evidence": True,
            "independent_provider_consensus": False,
            "promotion_sample_eligible": False,
            "reason": "A second independent provider_group within the synchronization window has not been captured.",
        },
    }
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    validation = validate(snapshot)
    if not validation.get("passed") or not validation.get("formal_pit_eligible"):
        raise ValueError(f"V5.2.3 formal snapshot validation failed: {validation.get('errors')}")
    return snapshot


def output_path(snapshot: dict[str, Any]) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return FORMAL_ROOT / (
        f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__marathonbet__{token}.json"
    )


def main() -> int:
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.18-marathonbet-formal-pit-capture-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_SNAPSHOTS_WRITTEN",
        "cases": [],
        "formal_snapshot_count_written": 0,
        "independent_provider_consensus_count_change": 0,
        "promotion_sample_count_change": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Persist valid direct single-provider PIT evidence only. Do not create promotion consensus or change formal probabilities/weights until a second independent provider_group is captured within the configured synchronization window.",
    }

    for case in CASES:
        row = {
            "competition_id": case["competition_id"],
            "canonical_home_team": case["canonical_home_team"],
            "canonical_away_team": case["canonical_away_team"],
            "status": "FAIL_CLOSED",
        }
        try:
            snapshot = build_formal_snapshot(case)
            validation = validate(snapshot)
            out = output_path(snapshot)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable formal PIT path collision with different payload: {out}")
                row["status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["formal_snapshot_count_written"] += 1
                row["status"] = "VALID_PIT_SNAPSHOT_WRITTEN"
            row.update({
                "formal_snapshot_path": str(out.relative_to(ROOT)),
                "freeze_utc": snapshot["freeze_utc"],
                "kickoff_utc": snapshot["kickoff_utc"],
                "one_x_two": snapshot["one_x_two"],
                "asian_handicap": snapshot["asian_handicap"],
                "over_under": snapshot["over_under"],
                "source_display_names": snapshot["source_adapter"]["source_display_names"],
                "v523_validation": validation,
                "promotion_sample_eligible": False,
            })
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["cases"].append(row)

    valid_statuses = {"VALID_PIT_SNAPSHOT_WRITTEN", "ALREADY_PRESENT_IDENTICAL"}
    valid_count = sum(1 for row in receipt["cases"] if row.get("status") in valid_statuses)
    if valid_count == len(CASES):
        receipt["status"] = "PASS_SINGLE_PROVIDER_PIT_EVIDENCE"
    elif valid_count:
        receipt["status"] = "PARTIAL_SINGLE_PROVIDER_PIT_EVIDENCE"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
