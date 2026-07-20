#!/usr/bin/env python3
"""Audit all 17 competition domains for market-LOMO data readiness.

This receipt distinguishes:
- a real production LOMO promotion receipt;
- timestamped complete 1X2/AH/OU historical surfaces that are acquired but still
  require chronological LOMO validation;
- research-only or partial market evidence;
- a mapped but credential-gated historical acquisition route.

It never creates or promotes a production LOMO receipt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "config" / "global_evidence_routes_v475.json"
EVIDENCE_ROOT = ROOT / "evidence" / "markets"
RECEIPT_ROOT = ROOT / "manifests" / "market_lomo"
RESEARCH_ROOT = ROOT / "manifests" / "market_lomo_research"
OUT = ROOT / "manifests" / "market_lomo_data_readiness_v470_status.json"
EXPECTED_STATUS = "LOMO_FORMAL_EV_VALIDATED"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _complete_surface(row: dict[str, Any]) -> bool:
    one = row.get("one_x_two")
    ah = row.get("asian_handicap")
    ou = row.get("over_under") or row.get("total_goals")
    if not isinstance(one, dict) or not isinstance(ah, dict) or not isinstance(ou, dict):
        return False
    return (
        all(key in one for key in ("home", "draw", "away"))
        and all(key in ah for key in ("line", "home", "away"))
        and all(key in ou for key in ("line", "over", "under"))
    )


def _timestamped(row: dict[str, Any]) -> bool:
    return bool(
        row.get("observed_at_utc")
        or row.get("quote_timestamp_utc")
        or row.get("provider_snapshot_time_utc")
    )


def _scan_jsonl(path: Path) -> dict[str, int]:
    total = 0
    complete = 0
    timestamped_complete = 0
    formally_eligible = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        if _complete_surface(row):
            complete += 1
            if _timestamped(row):
                timestamped_complete += 1
                if row.get("formal_lomo_eligible") is not False:
                    formally_eligible += 1
    return {
        "rows": total,
        "complete_surfaces": complete,
        "timestamped_complete_surfaces": timestamped_complete,
        "formally_eligible_timestamped_complete_surfaces": formally_eligible,
    }


def main() -> int:
    routes = _load(ROUTES)
    reports: dict[str, Any] = {}
    production_count = 0
    acquired_review_count = 0
    research_partial_count = 0

    for cid, route in sorted((routes.get("competitions") or {}).items()):
        receipt_path = RECEIPT_ROOT / f"{cid}.json"
        receipt_valid = False
        receipt_summary = None
        if receipt_path.exists():
            try:
                receipt = _load(receipt_path)
                receipt_valid = (
                    receipt.get("status") == EXPECTED_STATUS
                    and receipt.get("competition_id") == cid
                    and receipt.get("formal_ev_enabled") is True
                )
                receipt_summary = {
                    "path": str(receipt_path.relative_to(ROOT)),
                    "status": receipt.get("status"),
                    "target_season": receipt.get("target_season"),
                    "formal_ev_enabled": receipt.get("formal_ev_enabled"),
                    "market_coordination_enabled": receipt.get("market_coordination_enabled"),
                }
            except Exception as exc:
                receipt_summary = {"path": str(receipt_path.relative_to(ROOT)), "error": str(exc)}

        files = []
        totals = {
            "rows": 0,
            "complete_surfaces": 0,
            "timestamped_complete_surfaces": 0,
            "formally_eligible_timestamped_complete_surfaces": 0,
        }
        domain_dir = EVIDENCE_ROOT / cid
        if domain_dir.exists():
            for path in sorted(domain_dir.glob("*.jsonl")):
                counts = _scan_jsonl(path)
                files.append({"path": str(path.relative_to(ROOT)), **counts})
                for key in totals:
                    totals[key] += counts[key]

        research_manifests = []
        if RESEARCH_ROOT.exists():
            for path in sorted(RESEARCH_ROOT.glob(f"{cid}*.json")):
                try:
                    item = _load(path)
                    research_manifests.append({
                        "path": str(path.relative_to(ROOT)),
                        "status": item.get("status"),
                        "normalized_complete_surface_count": item.get("normalized_complete_surface_count"),
                        "formal_lomo_eligible": item.get("formal_lomo_eligible"),
                        "blocker": item.get("blocker"),
                    })
                except Exception as exc:
                    research_manifests.append({"path": str(path.relative_to(ROOT)), "error": str(exc)})

        if receipt_valid:
            status = "FORMAL_LOMO_VALIDATED"
            production_count += 1
        elif totals["formally_eligible_timestamped_complete_surfaces"] >= 200:
            status = "TIMESTAMPED_COMPLETE_SURFACES_ACQUIRED_REVIEW_REQUIRED"
            acquired_review_count += 1
        elif totals["rows"] > 0 or research_manifests:
            status = "RESEARCH_ONLY_OR_PARTIAL_MARKET_EVIDENCE"
            research_partial_count += 1
        else:
            status = "CREDENTIAL_GATED_BACKFILL_REQUIRED"

        reports[cid] = {
            "competition_id": cid,
            "status": status,
            "production_lomo_receipt": receipt_summary,
            "evidence_files": files,
            "evidence_totals": totals,
            "research_manifests": research_manifests,
            "historical_route": {
                "market_route": route.get("market_route"),
                "the_odds_api_sport_key": route.get("the_odds_api_sport_key"),
                "historical_odds_start_utc": route.get("historical_odds_start_utc"),
                "credential_env": routes.get("global_policies", {}).get("historical_market", {}).get("credential_env"),
                "access": routes.get("global_policies", {}).get("historical_market", {}).get("access"),
            },
            "formal_ev_available": receipt_valid,
            "formal_market_coordination_available": bool(receipt_valid and (receipt_summary or {}).get("market_coordination_enabled")),
        }

    out = {
        "schema_version": "V4.7.0-market-lomo-data-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count": len(reports),
        "production_lomo_validated_count": production_count,
        "timestamped_complete_surface_review_count": acquired_review_count,
        "research_only_or_partial_count": research_partial_count,
        "formal_ev_available_count": production_count,
        "reports": reports,
        "policy": (
            "Route mapping is not acquired data. Research-only or partial odds never create a production LOMO receipt. "
            "Formal EV remains fail-closed until a competition/season chronological LOMO validation receipt is explicitly produced."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": out["status"],
        "production_lomo_validated_count": production_count,
        "timestamped_complete_surface_review_count": acquired_review_count,
        "research_only_or_partial_count": research_partial_count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
