#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FOOTBALL_DATA = HERE.parents[1]
CONTRACT = HERE / "frozen_research_contract_v1.json"
REGISTRY = HERE / "source_registry_v1.json"

SCHEMA = "football3-jpkr-xg-source-audit-v1"
USER_AGENT = "football3-jpkr-xg-research-audit-v1"


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, application/json, text/plain, */*"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def decode_csv_text(data: bytes) -> tuple[str, str]:
    """Decode pinned public CSV bytes without altering their SHA identity.

    The historical worldfootballR league registry contains legacy Western-European
    bytes and is not guaranteed to be UTF-8.  Decoding is metadata-only: the raw
    byte SHA remains the frozen source identity used by the audit receipt.
    """
    failures: list[str] = []
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}:{exc.start}")
    raise RuntimeError("unable to decode pinned CSV registry: " + ",".join(failures))


def assert_local_production_contract(contract: dict[str, Any]) -> dict[str, Any]:
    runtime = (FOOTBALL_DATA / "formal_fast_runtime_v1" / "runtime.py").read_text(encoding="utf-8")
    fusion = (FOOTBALL_DATA / "new_engine_v1" / "formal_fusion_v2.py").read_text(encoding="utf-8")

    big5_start = runtime.index("BIG5 = {")
    big5_end = runtime.index("\n}\n", big5_start) + 3
    big5_block = runtime[big5_start:big5_end]
    formal_scope_start = runtime.index("FORMAL_SCOPE = (")
    formal_scope_end = runtime.index("\n)\n", formal_scope_start) + 3
    formal_scope_block = runtime[formal_scope_start:formal_scope_end]

    assertions = {
        "JPN_J1_already_in_formal_scope": '"JPN_J1"' in formal_scope_block,
        "KOR_KLeague1_already_in_formal_scope": '"KOR_KLeague1"' in formal_scope_block,
        "formal_head_matches_frozen_contract": str(contract["formal_model"]["head"]) in runtime,
        "current_sha_matches_frozen_contract": str(contract["formal_model"]["current_sha256"]) in runtime,
        "fusion_weight_fixed_0_75": "FUSION_WEIGHT = 0.75" in fusion,
        "exact_v1_fallback_route_present": 'route = "FROZEN_V1_EXACT_FALLBACK"' in fusion,
        "active_fusion_route_present": 'route = "FUSION_V2_ACTIVE"' in fusion,
        "big5_xg_mapping_present": all(x in big5_block for x in (
            '"Bundesliga":"GER_Bundesliga"',
            '"EPL":"ENG_PremierLeague"',
            '"La liga":"ESP_LaLiga"',
            '"Ligue 1":"FRA_Ligue1"',
            '"Serie A":"ITA_SerieA"',
        )),
        "jpn_not_in_big5_xg_mapping": "JPN_J1" not in big5_block,
        "kor_not_in_big5_xg_mapping": "KOR_KLeague1" not in big5_block,
    }
    if not all(assertions.values()):
        bad = [k for k, v in assertions.items() if not v]
        raise RuntimeError("production contract assertion failed: " + ",".join(bad))
    return assertions


def audit_worldfootballr(contract: dict[str, Any]) -> dict[str, Any]:
    ident = contract["frozen_public_source_identities"]["worldfootballR_data"]
    commit = ident["commit"]
    registry_url = f"https://raw.githubusercontent.com/JaseZiv/worldfootballR_data/{commit}/{ident['fotmob_league_registry_path']}"
    release_url = "https://api.github.com/repos/JaseZiv/worldfootballR_data/releases/tags/fotmob_match_details"

    registry_raw = fetch(registry_url)
    release_raw = fetch(release_url)
    release = json.loads(release_raw.decode("utf-8"))
    if int(release.get("id", -1)) != int(ident["fotmob_match_details_release_id"]):
        raise RuntimeError("FotMob static release identity drift")
    if str(release.get("tag_name")) != str(ident["fotmob_match_details_release_tag"]):
        raise RuntimeError("FotMob static release tag drift")

    registry_text, registry_encoding = decode_csv_text(registry_raw)
    rows = list(csv.DictReader(io.StringIO(registry_text)))
    by_id = {int(r["id"]): r for r in rows if str(r.get("id", "")).isdigit()}
    j1_id = int(ident["j1_fotmob_league_id"])
    k1_id = int(ident["k1_fotmob_league_id"])
    if j1_id not in by_id or k1_id not in by_id:
        raise RuntimeError("target FotMob league id missing from pinned league registry")

    assets = sorted(str(a.get("name") or "") for a in release.get("assets", []))
    target_assets = {
        "JPN_J1": [x for x in assets if x.startswith(f"{j1_id}_match_details.")],
        "KOR_KLeague1": [x for x in assets if x.startswith(f"{k1_id}_match_details.")],
    }
    return {
        "source_id": "worldfootballR_fotmob_match_details_static_release",
        "registry_url": registry_url,
        "registry_sha256": sha256_bytes(registry_raw),
        "registry_bytes": len(registry_raw),
        "registry_encoding": registry_encoding,
        "release_api_url": release_url,
        "release_metadata_sha256": sha256_bytes(release_raw),
        "release_id": release["id"],
        "release_tag": release["tag_name"],
        "release_asset_count": len(assets),
        "release_assets_sha256": sha256_bytes(canon(assets)),
        "target_league_registry": {
            "JPN_J1": by_id[j1_id],
            "KOR_KLeague1": by_id[k1_id],
        },
        "target_assets": target_assets,
        "qualified_match_details_assets": {k: len(v) for k, v in target_assets.items()},
        "status": "ZERO_TARGET_ASSETS" if not any(target_assets.values()) else "TARGET_ASSET_PRESENT_REQUIRES_SEPARATE_LICENSE_AND_COVERAGE_AUDIT",
    }


def audit_statsbomb_open_data(contract: dict[str, Any]) -> dict[str, Any]:
    ident = contract["frozen_public_source_identities"]["hudl_statsbomb_open_data"]
    commit = ident["commit"]
    url = f"https://raw.githubusercontent.com/hudl/open-data/{commit}/{ident['competitions_path']}"
    raw = fetch(url)
    rows = json.loads(raw.decode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("StatsBomb competitions registry is not a list")

    target = []
    for r in rows:
        country = str(r.get("country_name") or "").casefold()
        comp = str(r.get("competition_name") or "").casefold()
        if country in {"japan", "south korea", "korea", "korea republic"} or "j1" in comp or "k league" in comp:
            target.append(r)
    return {
        "source_id": "hudl_statsbomb_open_data_repository",
        "url": url,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "competition_season_rows": len(rows),
        "target_rows": target,
        "target_row_count": len(target),
        "status": "ZERO_TARGET_COMPETITIONS" if len(target) == 0 else "TARGET_COMPETITION_PRESENT_REQUIRES_COVERAGE_AUDIT",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    contract = read_json(CONTRACT)
    registry = read_json(REGISTRY)
    production_assertions = assert_local_production_contract(contract)
    wf = audit_worldfootballr(contract)
    sb = audit_statsbomb_open_data(contract)

    # Only mechanically verified no-key/no-login public GitHub sources are fetched.
    # Human-reviewed sources rejected by terms/key requirements remain frozen in source_registry_v1.json
    # and are deliberately not contacted by this workflow.
    mechanical_pass = wf["status"] == "ZERO_TARGET_ASSETS" and sb["status"] == "ZERO_TARGET_COMPETITIONS"
    if not mechanical_pass:
        # A newly appearing asset/competition is not automatically accepted; it needs a fresh
        # pre-label license/coverage audit rather than silently changing this frozen experiment.
        status = "STOP_SOURCE_IDENTITY_DRIFT_REAUDIT_REQUIRED"
    else:
        status = "STOP_DATA_COVERAGE"

    leagues = {}
    for comp in ("JPN_J1", "KOR_KLeague1"):
        frozen = registry["league_gate"][comp]
        if frozen["status"] != "STOP_DATA_COVERAGE":
            raise RuntimeError(f"frozen league gate drift: {comp}")
        leagues[comp] = {
            "current_formal_scope": True,
            "production_xg_evidence": "NO_LEAGUE_LOCAL_MATCH_XG_IN_CURRENT_BIG5_SOURCE_PATH",
            "expected_current_route_when_xg_evidence_insufficient": "FROZEN_V1_EXACT_FALLBACK",
            "qualified_sources": [],
            "qualified_seasons": [],
            "qualified_match_count": 0,
            "home_xg_complete_rate": None,
            "away_xg_complete_rate": None,
            "kickoff_provenance_audit": "NOT_RUN_NO_QUALIFIED_ROWS",
            "observed_available_at_audit": "NOT_RUN_NO_QUALIFIED_ROWS",
            "multilingual_team_identity_audit": "NOT_RUN_NO_QUALIFIED_ROWS",
            "duplicate_reschedule_cancel_audit": "NOT_RUN_NO_QUALIFIED_ROWS",
            "oos_status": "NOT_RUN_ZERO_LABEL_GATE_FAILED",
            "oos_metrics": {
                "FROZEN_V1_FALLBACK": None,
                "JP_KR_XG_CHALLENGER": None,
                "FIXED_75_25_FUSION_V2": None
            },
            "status": "STOP_DATA_COVERAGE"
        }

    receipt = {
        "schema_version": SCHEMA,
        "status": status,
        "zero_label": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "research_branch_expected": "football3/jpkr-xg-source-audit-research-v1",
        "base_integration_head": contract["base_integration"]["head"],
        "formal_model_head": contract["formal_model"]["head"],
        "current_sha256": contract["formal_model"]["current_sha256"],
        "fusion_weights": {"xg": 0.75, "v1": 0.25},
        "contract_sha256": sha256_file(CONTRACT),
        "source_registry_sha256": sha256_file(REGISTRY),
        "production_contract_assertions": production_assertions,
        "mechanical_source_audits": [wf, sb],
        "human_reviewed_rejected_or_partial_sources": [
            {"source_id": x["source_id"], "decision": x["decision"], "reason": x["reason"]}
            for x in registry["sources"]
            if not x.get("mechanical_audit", False)
        ],
        "leagues": leagues,
        "labels_materialized": False,
        "oos_run": False,
        "adapter_implemented": False,
        "formal_or_production_changes": False,
        "independent_acceptance_eligible": False,
        "stop_reason": "No qualified no-key/no-login, provenance-and-license-acceptable multi-season match-level xG source was found for J1/K1. Per frozen contract, labels and OOS evaluation remain unopened."
    }
    receipt["receipt_sha256"] = sha256_bytes(canon(receipt))
    (out / "jpkr_xg_source_audit_v1.json").write_bytes(canon(receipt))
    (out / "frozen_research_contract_v1.json").write_bytes(CONTRACT.read_bytes())
    (out / "source_registry_v1.json").write_bytes(REGISTRY.read_bytes())
    print(json.dumps({
        "status": receipt["status"],
        "receipt_sha256": receipt["receipt_sha256"],
        "JPN_J1": receipt["leagues"]["JPN_J1"]["status"],
        "KOR_KLeague1": receipt["leagues"]["KOR_KLeague1"]["status"],
        "labels_materialized": False,
        "oos_run": False
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
