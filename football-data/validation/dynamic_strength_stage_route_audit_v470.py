#!/usr/bin/env python3
"""Audit raw Transfermarkt round labels for special V4.7 competition routes.

Read-only research evidence.  The output is used to decide whether a special
competition can be partitioned without mixing incompatible stages.  No model
weights or probabilities are changed.
"""
from __future__ import annotations

import csv
import gzip
import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "dynamic_strength_public_evidence_v470.json"
OUTPUT_PATH = ROOT / "manifests" / "dynamic_strength_stage_route_audit_v470_status.json"
SPECIAL = ["SUI_SuperLeague", "SCO_Premiership", "KOR_KLeague1", "ARG_Primera", "USA_MLS", "UEFA_ChampionsLeague", "JPN_J1"]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def download(config: dict[str, Any]) -> Path:
    filename = config["source"]["files"]["games"]
    path = Path("/tmp") / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    urls = [
        config["source"]["dataset_delivery_base"].rstrip("/") + "/" + filename,
        "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets/master/data/prep/" + filename,
    ]
    last = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FASHI188-football-analysis/4.7"})
            with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    output.write(chunk)
            if path.stat().st_size > 0: return path
        except Exception as exc:
            last = exc
            if path.exists(): path.unlink()
    raise RuntimeError(f"games download failed: {last}")


def main() -> int:
    config = load_json(CONFIG_PATH)
    external = {config["competition_mapping"][cid]["transfermarkt_competition_id"]: cid for cid in SPECIAL}
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    total = Counter()
    path = download(config)
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            cid = external.get(str(row.get("competition_id") or ""))
            if not cid: continue
            if not str(row.get("home_club_goals") or "").strip() or not str(row.get("away_club_goals") or "").strip(): continue
            season = str(row.get("season") or "").strip(); round_label = str(row.get("round") or "").strip() or "<EMPTY>"
            counts[cid][season][round_label] += 1; total[cid] += 1
    reports = {}
    for cid in SPECIAL:
        route = config["competition_mapping"][cid]["validation_route"]
        seasons = {}
        for season, counter in sorted(counts[cid].items(), key=lambda item: item[0]):
            seasons[season] = {"completed_games": sum(counter.values()), "round_labels": dict(counter.most_common())}
        reports[cid] = {
            "competition_id": cid,
            "validation_route": route,
            "completed_games": total[cid],
            "season_count": len(seasons),
            "seasons": seasons,
            "adapter_status": "RAW_STAGE_LABELS_AUDITED",
            "formal_weight": 0,
            "probability_change": False,
        }
    output = {
        "schema_version": "V4.7.0-dynamic-strength-stage-route-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_weight_change": False,
        "probability_change": False,
        "reports": reports,
        "policy": "Raw round labels only. No special-domain OOF may start until an explicit adapter maps or excludes every observed row label without mixing incompatible stages."
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({cid: {"games": reports[cid]["completed_games"], "seasons": reports[cid]["season_count"]} for cid in SPECIAL}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
