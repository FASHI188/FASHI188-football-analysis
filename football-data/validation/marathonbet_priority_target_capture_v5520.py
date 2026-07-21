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

from direct_marathonbet_html_probe_v5516 import fetch
from marathonbet_v523_adapter_v5517 import parse_fixture
from prospective_market_snapshot_v523 import canonical_sha256, validate

RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "priority_targets"
FORMAL_ROOT = ROOT / "evidence" / "markets_prospective"
MANIFEST = ROOT / "manifests" / "marathonbet_priority_target_capture_v5520_status.json"

TARGETS = [
    {
        "competition_id": "POR_PrimeiraLiga",
        "season": "2026/27",
        "canonical_home_team": "Estoril Praia",
        "canonical_away_team": "FC Famalicão",
        "source_alias_pairs": [
            ["Estoril Praia", "FC Famalicao"],
            ["Estoril Praia", "Famalicao"],
            ["Estoril", "Famalicao"],
        ],
        "kickoff_utc": "2026-08-07T19:15:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Portugal/Primeira%2BLiga%2B-%2B43058",
        "official_source": "https://www.ligaportugal.pt/news/28234/estoril-praia-fc-famalicao-abre-liga-betclic-202627",
        "official_basis": "Liga Portugal first-party notice identifies Estoril Praia-FC Famalicao as the 2026/27 Liga Betclic opener and states first-two-round dates/times were published; capture still requires exact bookmaker displayed time to convert to the registered UTC kickoff.",
    },
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "canonical_home_team": "Deportivo Alavés",
        "canonical_away_team": "Getafe CF",
        "source_alias_pairs": [["Alaves", "Getafe"]],
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Spain%2B-%2B8727",
        "official_source": "https://www.laliga.com/en-ES/laliga-easports/results",
        "official_basis": "LALIGA 2026/27 Matchday 1 official schedule: 15 Aug 2026 19:30 Spain local (CEST) = 17:30 UTC.",
    },
    {
        "competition_id": "FRA_Ligue1",
        "season": "2026/27",
        "canonical_home_team": "Olympique de Marseille",
        "canonical_away_team": "RC Strasbourg Alsace",
        "source_alias_pairs": [["Marseille", "Strasbourg"]],
        "kickoff_utc": "2026-08-21T18:45:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/France%2B-%2B21532",
        "official_source": "https://ligue1.com/",
        "official_basis": "Ligue 1 official schedule: Marseille-Strasbourg Fri 21 Aug 2026 20:45 CEST = 18:45 UTC.",
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "canonical_home_team": "FC Bayern München",
        "canonical_away_team": "VfB Stuttgart",
        "source_alias_pairs": [["Bayern Munich", "Stuttgart"]],
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Germany/Bundesliga%2B-%2B22436",
        "official_source": "https://products.bundesliga.com/fixtures",
        "official_basis": "Bundesliga official fixture feed: 28 Aug 2026 18:30 UTC / 20:30 CEST.",
    },
]


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def iso_utc(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value}")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_target(raw: bytes, target: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    errors = []
    for home_alias, away_alias in target["source_alias_pairs"]:
        try:
            parsed = parse_fixture(
                raw,
                home_team=home_alias,
                away_team=away_alias,
                target_kickoff_utc=target["kickoff_utc"],
            )
            return parsed, home_alias, away_alias
        except Exception as exc:
            errors.append(f"{home_alias} vs {away_alias}: {type(exc).__name__}: {exc}")
    raise ValueError("; ".join(errors))


def raw_paths(target: dict[str, Any], observed: str, digest: str) -> tuple[Path, Path]:
    token = observed.replace(":", "").replace("+00:00", "Z")
    stem = f"{safe(target['competition_id'])}__{token}__{digest[:12]}"
    return RAW_ROOT / f"{stem}.html", RAW_ROOT / f"{stem}.json"


def write_raw(target: dict[str, Any], raw: bytes, final_url: str, status: int, headers: dict[str, str], observed: str, source_home: str, source_away: str) -> tuple[Path, Path, str]:
    digest = hashlib.sha256(raw).hexdigest()
    html_path, meta_path = raw_paths(target, observed, digest)
    if not html_path.exists():
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_bytes(raw)
    metadata = {
        "schema_version": "V5.5.20-marathonbet-priority-raw-envelope-r1",
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "observed_at_utc": observed,
        "requested_url": target["url"],
        "final_url": final_url,
        "http_status": status,
        "response_headers": headers,
        "raw_html_sha256": digest,
        "competition_id": target["competition_id"],
        "canonical_home_team": target["canonical_home_team"],
        "canonical_away_team": target["canonical_away_team"],
        "source_home_team": source_home,
        "source_away_team": source_away,
        "kickoff_utc": target["kickoff_utc"],
        "formal_evidence_parent": True,
        "research_probe_only": False,
    }
    if not meta_path.exists():
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path, meta_path, digest


def build_snapshot(target: dict[str, Any], parsed: dict[str, Any], observed: str, final_url: str, raw_html_path: Path, raw_meta_path: Path, raw_sha: str, source_home: str, source_away: str) -> dict[str, Any]:
    observed = iso_utc(observed)
    kickoff = iso_utc(target["kickoff_utc"])
    if datetime.fromisoformat(observed) >= datetime.fromisoformat(kickoff):
        raise ValueError("observation must precede kickoff")
    ah = parsed["asian_handicap"]
    ou = parsed["over_under"]
    snapshot: dict[str, Any] = {
        "competition_id": target["competition_id"],
        "season": target["season"],
        "home_team": target["canonical_home_team"],
        "away_team": target["canonical_away_team"],
        "kickoff_utc": kickoff,
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": observed,
        "accessed_at_utc": observed,
        "source_observed_at_utc": observed,
        "surface_observed_at_utc": {"one_x_two": observed, "asian_handicap": observed, "over_under": observed},
        "source_url": final_url,
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "one_x_two": parsed["one_x_two"],
        "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
        "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
        "source_adapter": {
            "schema_version": "V5.5.20-marathonbet-priority-target-capture-r1",
            "accepted_parser": "V5.5.17-marathonbet-v523-adapter-r1",
            "parent_raw_html_path": str(raw_html_path.relative_to(ROOT)),
            "parent_metadata_path": str(raw_meta_path.relative_to(ROOT)),
            "parent_raw_html_sha256": raw_sha,
            "source_display_names": {"home": source_home, "away": source_away},
            "canonical_identity": {"home": target["canonical_home_team"], "away": target["canonical_away_team"]},
            "display_timezone": parsed["display_timezone"],
            "displayed_kickoff": parsed["displayed_time"],
            "displayed_kickoff_converted_utc": parsed["page_kickoff_utc"],
            "kickoff_skew_seconds": parsed["kickoff_skew_seconds"],
            "official_kickoff_source": target["official_source"],
            "official_kickoff_basis": target["official_basis"],
            "handicap_away_line_audit": ah["away_line"],
        },
        "observation_semantics": {
            "source_observed_at_utc": "fresh direct first-party HTML response observation time",
            "surface_observed_at_utc": "same response/time for 1X2, AH and OU",
            "retrospective_backfill": False,
        },
        "promotion_semantics": {
            "single_provider_pit_evidence": True,
            "independent_provider_consensus": False,
            "promotion_sample_eligible": False,
        },
    }
    snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
    result = validate(snapshot)
    if not result.get("passed") or not result.get("formal_pit_eligible"):
        raise ValueError(f"V5.2.3 validation failed: {result.get('errors')}")
    return snapshot


def formal_path(snapshot: dict[str, Any]) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return FORMAL_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__marathonbet__{token}.json"


def main() -> int:
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.20-marathonbet-priority-target-capture-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_TARGET_SNAPSHOTS_WRITTEN",
        "targets": [],
        "formal_snapshot_count_written": 0,
        "raw_target_count_written": 0,
        "independent_provider_consensus_count_change": 0,
        "promotion_sample_count_change": 0,
        "formal_weight_change": False,
        "probability_change": False,
    }
    for target in TARGETS:
        row: dict[str, Any] = {"competition_id": target["competition_id"], "canonical_home_team": target["canonical_home_team"], "canonical_away_team": target["canonical_away_team"], "status": "TARGET_NOT_AVAILABLE_OR_GATE_FAILED"}
        try:
            raw, final_url, status, headers, observed = fetch(target["url"])
            parsed, source_home, source_away = parse_target(raw, target)
            raw_html_path, raw_meta_path, raw_sha = write_raw(target, raw, final_url, status, headers, observed, source_home, source_away)
            receipt["raw_target_count_written"] += 1
            snapshot = build_snapshot(target, parsed, observed, final_url, raw_html_path, raw_meta_path, raw_sha, source_home, source_away)
            out = formal_path(snapshot)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable PIT path collision: {out}")
                status_label = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["formal_snapshot_count_written"] += 1
                status_label = "VALID_PIT_SNAPSHOT_WRITTEN"
            row.update({
                "status": status_label,
                "observed_at_utc": snapshot["freeze_utc"],
                "formal_snapshot_path": str(out.relative_to(ROOT)),
                "raw_html_path": str(raw_html_path.relative_to(ROOT)),
                "source_display_names": snapshot["source_adapter"]["source_display_names"],
                "one_x_two": snapshot["one_x_two"],
                "asian_handicap": snapshot["asian_handicap"],
                "over_under": snapshot["over_under"],
                "v523_validation": validate(snapshot),
                "promotion_sample_eligible": False,
            })
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["targets"].append(row)
    valid = [x for x in receipt["targets"] if x.get("status") in {"VALID_PIT_SNAPSHOT_WRITTEN", "ALREADY_PRESENT_IDENTICAL"}]
    if valid:
        receipt["status"] = "PASS_FRESH_SINGLE_PROVIDER_PIT_EVIDENCE"
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
