#!/usr/bin/env python3
"""V6.18.9 freeze a deterministic pre-match Understat state panel.

Governance/data asset only. Hard-depends on the PASS V6.18.8f immutable alias freeze.
The panel contains no match outcome/score labels. It stores only strict-PIT fixture
identity, the exact mapped Understat fixture identity, pre-match home/away Understat
state dictionaries, and immutable source hashes.

The web is used only while creating this one-time frozen panel. Downstream xG models
must read this asset and may not refetch Understat.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
E = ROOT / "engine"
import sys
for p in (V, E):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_shot_residual_v6181 as shotbase
import v6_total_shot_residual_v6181a as shotfix
import v6_strict_daily_pit_rows_v6181c as strict
import v6_understat_xg_residual_v624 as xg
import v6_understat_fixture_alignment_audit_v6186 as a186
import v6_understat_fixture_alignment_audit_v6186r3 as a186r3
import v6_understat_alias_qualification_v6187 as q187

FREEZE_STATUS = ROOT / "manifests" / "v6_understat_alias_freeze_v6188f_status.json"
ALIAS_ASSET = ROOT / "models" / "challengers_v6188" / "understat_aliases_v6188.json"
PANEL = ROOT / "models" / "challengers_v6189" / "understat_xg_prematch_panel_v6189.jsonl"
OUT = ROOT / "manifests" / "v6_understat_xg_feature_panel_freeze_v6189_status.json"
DOMAINS = tuple(sorted(xg.UNDERSTAT_LEAGUES.keys()))
SEASONS = a186.SEASONS
YEAR_BY_SEASON = a186.YEAR_BY_SEASON
MIN_RATE = 0.90


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_safe(value: Any):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return str(value)


def canonical_line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_freeze():
    if not FREEZE_STATUS.exists() or not ALIAS_ASSET.exists():
        raise SystemExit("V6.18.8f freeze status/asset missing")
    status = json.loads(FREEZE_STATUS.read_text(encoding="utf-8"))
    asset_raw = ALIAS_ASSET.read_bytes()
    asset = json.loads(asset_raw.decode("utf-8"))
    if status.get("status") != "PASS" or status.get("xg_model_research_identity_gate") != "PASS":
        raise SystemExit("V6.18.8f identity gate not PASS")
    if status.get("alias_asset_sha256") != sha256_bytes(asset_raw):
        raise SystemExit("V6.18.8f alias asset hash mismatch")
    if asset.get("schema_version") != "V6.18.8f-understat-alias-freeze-r1":
        raise SystemExit("unexpected alias asset schema")
    return status, asset


def aliases_for(asset, cid: str) -> dict[str, str]:
    out = {}
    for ptoken, rec in ((asset.get("domains") or {}).get(cid, {}).get("aliases") or {}).items():
        out[str(ptoken)] = str(rec["understat_token"])
    return out


def strict_rows():
    raw, _ = shotbase.raw_stat_matches()
    lookup, _ = shotfix.lagged_shot_lookup_fixed(raw)
    rows, meta = strict.strict_formal_score_rows(lookup)
    return [r for r in rows if r["competition_id"] in DOMAINS and r["season"] in SEASONS], meta


def source_domain(cid: str, league: str):
    payloads = {}
    teams = {}
    fixtures = []
    audits = {}
    for season in SEASONS:
        payload, audit = a186r3.fetch_understat_payload_transport_safe(league, YEAR_BY_SEASON[season])
        payloads[season] = payload
        teams[season] = payload["teams"]
        audits[season] = audit
        for f in a186.fixture_rows(cid, payload):
            x = dict(f)
            x["season"] = season
            fixtures.append(x)
    state_map, state_stats = xg._build_state_maps(cid, teams, 20.0)
    pair_index = defaultdict(list)
    for f in fixtures:
        pair_index[(f["home_token"], f["away_token"])].append(f)
    return state_map, state_stats, pair_index, audits


def main() -> int:
    freeze_status, asset = load_freeze()
    rows, strict_meta = strict_rows()
    if not rows:
        raise SystemExit("no strict-PIT rows for Understat domains")
    by_domain = defaultdict(list)
    for r in rows:
        by_domain[r["competition_id"]].append(r)

    panel_records = []
    coverage = {}
    fetch_audit = {}
    state_stats = {}
    unresolved_examples = {}
    for cid in DOMAINS:
        amap = aliases_for(asset, cid)
        state_map, sstats, pair_index, audits = source_domain(cid, xg.UNDERSTAT_LEAGUES[cid])
        fetch_audit[cid] = audits
        state_stats[cid] = sstats
        c = Counter()
        examples = []
        for r in by_domain.get(cid, []):
            c["input"] += 1
            pdate = str(r["date"])
            ph0 = xg._understat_team_token(cid, str(r["home_team"]))
            pa0 = xg._understat_team_token(cid, str(r["away_team"]))
            ph = amap.get(ph0, ph0)
            pa = amap.get(pa0, pa0)
            fixture, reason = q187.exact_mapped_fixture(ph, pa, pdate, pair_index)
            c[reason] += 1
            if fixture is None:
                if len(examples) < 20:
                    examples.append({"season": r["season"], "date": pdate, "home": r["home_team"], "away": r["away_team"], "mapped": [ph, pa], "reason": reason})
                continue
            udate = fixture["date"]
            hs = state_map.get((udate, ph))
            astate = state_map.get((udate, pa))
            if hs is None or astate is None:
                c["STATE_MISSING"] += 1
                if len(examples) < 20:
                    examples.append({"season": r["season"], "date": pdate, "home": r["home_team"], "away": r["away_team"], "mapped": [ph, pa], "understat_date": udate, "reason": "STATE_MISSING"})
                continue
            c["attached"] += 1
            panel_records.append({
                "competition_id": cid,
                "season": r["season"],
                "platform_date": pdate,
                "platform_home_team": r["home_team"],
                "platform_away_team": r["away_team"],
                "understat_fixture_id": fixture.get("id"),
                "understat_fixture_date": udate,
                "understat_home_token": ph,
                "understat_away_token": pa,
                "home_prematch_state": json_safe(hs),
                "away_prematch_state": json_safe(astate),
            })
        n = c["input"]
        rate = c["attached"] / n if n else 0.0
        coverage[cid] = {**dict(sorted(c.items())), "attach_rate": rate}
        unresolved_examples[cid] = examples
        if rate < MIN_RATE:
            raise SystemExit(f"feature-panel coverage below gate {cid}: {rate}")

    total_input = sum(int(v["input"]) for v in coverage.values())
    total_attached = sum(int(v["attached"]) for v in coverage.values())
    overall = total_attached / total_input if total_input else 0.0
    if overall < MIN_RATE:
        raise SystemExit(f"aggregate feature-panel coverage below gate: {overall}")

    panel_records.sort(key=lambda r: (r["season"], r["platform_date"], r["competition_id"], r["platform_home_team"], r["platform_away_team"]))
    panel_bytes = b"".join(canonical_line(r) for r in panel_records)
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    if PANEL.exists() and PANEL.read_bytes() != panel_bytes:
        raise SystemExit("existing V6.18.9 panel drift detected; fail closed")
    PANEL.write_bytes(panel_bytes)

    receipt = {
        "schema_version": "V6.18.9-understat-xg-prematch-feature-panel-freeze-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "alias_freeze_status_sha256": sha256_file(FREEZE_STATUS),
        "alias_asset_sha256": sha256_file(ALIAS_ASSET),
        "panel_path": str(PANEL.relative_to(ROOT)),
        "panel_sha256": sha256_bytes(panel_bytes),
        "panel_rows": len(panel_records),
        "input_rows": total_input,
        "attach_rate": overall,
        "coverage": coverage,
        "fetch_audit": fetch_audit,
        "state_stats": state_stats,
        "strict_formal_meta": strict_meta,
        "unresolved_examples": unresolved_examples,
        "labels_in_panel": False,
        "xg_model_research_feature_gate": "PASS",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "downstream_web_refetch_forbidden": True,
            "downstream_must_verify_panel_sha256": True,
            "panel_contains_only_prematch_state": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "panel_rows": len(panel_records), "input_rows": total_input, "attach_rate": overall, "panel_sha256": receipt["panel_sha256"], "coverage": coverage}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
