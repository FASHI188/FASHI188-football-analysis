#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib

import nova_n1_external_archive_header_audit_v1 as base

CANDIDATE = {
    "candidate_id": "KAGGLE_CODYTIPTON_UNDERSTAT_PER_GAME_2014_2025",
    "provider": "Cody Tipton - Player stats per game - Understat",
    "source_page": "https://www.kaggle.com/datasets/codytipton/player-stats-per-game-understat",
    "download_url": "https://www.kaggle.com/api/v1/datasets/download/codytipton/player-stats-per-game-understat",
    "declared_license": "MIT",
    "target_leagues": ["Bundesliga", "Serie A", "Ligue 1"],
    "declared_seasons": "2014/15-2024/25",
    "declared_game_schema": "general_game_stats: date, team_h, team_a, h_deep, a_deep, h_ppda, a_ppda",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = base.audit_candidate_safe(CANDIDATE)
    payload = {
        "schema_version": "football3-nova-n1-cody-understat-header-audit-v1",
        "report": report,
        "source_page_license_evidence": {
            "declared_license": "MIT",
            "scope_claim": "Kaggle dataset page labels the dataset MIT and describes general_game_stats across EPL, La Liga, Bundesliga, Serie A, Ligue 1 and RFPL through 2024/25.",
            "data_rows_opened_for_license_check": 0,
        },
        "test_identity_opened": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "data_rows_opened": 0,
    }
    (args.out / "cody_understat_header_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "candidate": CANDIDATE["candidate_id"],
        "status": report["status"],
        "schema_files": report.get("locked_schema_candidate_files", []),
        "test_labels_read": False,
        "data_rows_opened": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
