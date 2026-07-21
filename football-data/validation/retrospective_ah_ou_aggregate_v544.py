#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DOMAINS = ["POR_PrimeiraLiga", "SCO_Premiership"]
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "retrospective_ah_ou_por_sco_v544_status.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    src = Path(args.input)
    reports = {}; failures = {}
    for cid in DOMAINS:
        p = src / f"{cid}.json"
        if not p.exists():
            failures[cid] = "MISSING_DOMAIN_RECEIPT"
            continue
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("status") != "PASS":
            failures[cid] = payload.get("error") or payload.get("status")
            continue
        reports[cid] = payload["report"]
    ou_strict = []; ah_strict = []
    for cid, report in reports.items():
        ou = report.get("over_under_2_5") or {}
        ou_boot = ((ou.get("bootstrap_market_minus_formal") or {}).get("brier") or {})
        if ou_boot and float(ou_boot.get("ci95_upper") or 1.0) < 0.0:
            ou_strict.append(cid)
        ah = report.get("asian_handicap_closing_line") or {}
        ah_boot = ((ah.get("bootstrap_market_minus_formal") or {}).get("settlement_score") or {})
        if ah_boot and float(ah_boot.get("ci95_lower") or -1.0) > 0.0:
            ah_strict.append(cid)
    payload = {
        "schema_version": "V5.4.4-retrospective-ah-ou-por-sco-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "reports": reports,
        "failures": failures,
        "ou_market_strict_brier_win_domains": ou_strict,
        "ah_market_strict_settlement_score_win_domains": ah_strict,
        "status": "PASS" if len(reports) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": "Same frozen V5.2.8 AH/OU audit and 1600-draw block-bootstrap gates, executed per domain in parallel. Retrospective prices remain reference only."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
