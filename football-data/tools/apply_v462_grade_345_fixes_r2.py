#!/usr/bin/env python3
"""Second idempotent patch for A-grade gates 3/4/5 integration."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def update_auditor() -> None:
    path = ROOT / "validation" / "a_grade_auditor.py"
    text = path.read_text(encoding="utf-8")
    replay_root = 'REPLAY_REPORT_ROOT = ROOT / "validation" / "reports" / "replay_v462"\n'
    if replay_root not in text:
        raise RuntimeError("replay root must be installed by grade345 r1 before r2")
    if "LINEUP_REPORT_ROOT" not in text:
        text = text.replace(
            replay_root,
            replay_root + 'LINEUP_REPORT_ROOT = ROOT / "validation" / "reports" / "probable_lineup_v462"\n',
            1,
        )
    replay_path = '    replay_path = REPLAY_REPORT_ROOT / f"{competition_id}.json"\n'
    if "lineup_path = LINEUP_REPORT_ROOT" not in text:
        text = text.replace(
            replay_path,
            replay_path + '    lineup_path = LINEUP_REPORT_ROOT / f"{competition_id}.json"\n',
            1,
        )
    replay_load = '    replay = load_json(replay_path) if replay_path.exists() else None\n'
    if "lineup = load_json(lineup_path)" not in text:
        text = text.replace(
            replay_load,
            replay_load + '    lineup = load_json(lineup_path) if lineup_path.exists() else None\n',
            1,
        )
    old = '        "lineup_route": bool(core_checks.get("lineup_route")),\n'
    new = '''        "lineup_route": bool(
            lineup
            and lineup.get("status") == "PROBABLE_LINEUP_ROUTE_VALIDATED"
            and lineup.get("validated_for_a_grade") is True
            and int(lineup.get("prediction_count", 0)) >= int(lineup.get("minimum_validation_predictions", 10**9))
        ),
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif 'lineup.get("status") == "PROBABLE_LINEUP_ROUTE_VALIDATED"' not in text:
        raise RuntimeError("lineup auditor patch anchor not found")
    path.write_text(text, encoding="utf-8")


def update_bootstrap() -> None:
    path = ROOT / "manifests" / "runtime_bootstrap.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = "1.7"
    data.setdefault("formal_core", {})["implementation_revision"] = "V4.6.2-core-2-grade345"
    data["a_grade_support"] = {
        "probable_lineup_route": "football-data/validation/probable_lineup_route_v462.py",
        "probable_lineup_status_manifest": "football-data/manifests/probable_lineup_v462_status.json",
        "independent_replay_builder": "football-data/validation/competition_replay_receipts_v462.py",
        "independent_replay_status_manifest": "football-data/manifests/replay_v462_status.json",
        "total_goals_rps_policy": "Nested time-ordered candidate selection may shrink the venue-pair direct-total signal toward the competition total. Promotion remains fail-closed unless the paired block-bootstrap total-goals RPS CI upper bound is <= 0.",
        "outer_time_fold_policy": "Each completely unseen outer season is split into two disjoint chronological evaluation folds after hyperparameters are selected only from prior seasons; OOF calibration remains season-routed and deduplicated by outer season.",
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    update_auditor()
    update_bootstrap()
    print("Applied V4.6.2 grade345 r2 integration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
