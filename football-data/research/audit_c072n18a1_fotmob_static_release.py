#!/usr/bin/env python3
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/JaseZiv/worldfootballR_data/releases/tags/fotmob_match_details"
OUT = Path("football-data/research/_n18a1_fotmob_release_discovery")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "football3-n18a1-release-metadata-audit/1.0",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        obj = json.load(resp)

    assets = []
    for a in obj.get("assets", []):
        assets.append({
            "id": a.get("id"),
            "name": a.get("name"),
            "size": a.get("size"),
            "content_type": a.get("content_type"),
            "updated_at": a.get("updated_at"),
            "browser_download_url": a.get("browser_download_url"),
        })
    assets.sort(key=lambda x: str(x.get("name") or ""))
    match_assets = [a for a in assets if str(a.get("name") or "").endswith("_match_details.csv")]
    total_bytes = sum(int(a.get("size") or 0) for a in assets)

    gates = {
        "release_resolved": bool(obj.get("id")),
        "match_detail_assets_ge_5": len(match_assets) >= 5,
        "premier_league_47_present": any(a.get("name") == "47_match_details.csv" for a in assets),
        "declared_asset_bytes_gt_1mb": total_bytes > 1_000_000,
        "match_data_downloaded": False,
    }
    passed = (
        gates["release_resolved"]
        and gates["match_detail_assets_ge_5"]
        and gates["premier_league_47_present"]
        and gates["declared_asset_bytes_gt_1mb"]
        and not gates["match_data_downloaded"]
    )
    summary = {
        "project": "football3",
        "experiment": "C072-N18A1",
        "status": "PASS_STATIC_RELEASE_METADATA_DISCOVERY" if passed else "STOP_SOURCE_DISCOVERY",
        "observed_at_utc": utc_now(),
        "source_api": API,
        "release_id": obj.get("id"),
        "tag_name": obj.get("tag_name"),
        "name": obj.get("name"),
        "target_commitish": obj.get("target_commitish"),
        "published_at": obj.get("published_at"),
        "asset_count": len(assets),
        "match_detail_asset_count": len(match_assets),
        "declared_asset_bytes": total_bytes,
        "asset_names": [a.get("name") for a in assets],
        "gates": gates,
        "labels_accessed": 0,
        "match_data_bytes_downloaded": 0,
        "model_fits": 0,
        "target_scores": 0,
    }
    (OUT / "fotmob_release_assets.json").write_text(
        json.dumps(assets, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "fotmob_release_discovery_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    if not passed:
        raise SystemExit(summary["status"])


if __name__ == "__main__":
    main()
