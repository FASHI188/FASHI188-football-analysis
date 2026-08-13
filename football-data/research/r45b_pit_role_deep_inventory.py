#!/usr/bin/env python3
"""R45B zero-label deep inventory for PIT role / process / task inputs.

Research governance:
- reads roster/context evidence and pre-existing readiness manifests only;
- does not read target match results, train, fit, score, tune, or call providers;
- classifies axes PASS / PARTIAL / MISSING and remains fail-closed.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "evidence"
TEAM = EVID / "team_configuration_weekly"
LIVE = EVID / "match_context_live" / "manual_web"
MANIFESTS = ROOT / "manifests"
OUT = ROOT / "research" / "r45b_pit_role_deep_inventory_status.json"

BROAD = {
    "g", "gk", "goalkeeper",
    "d", "df", "defender", "defence", "defense",
    "m", "mf", "midfielder", "midfield",
    "f", "fw", "forward", "attacker", "attack",
}
ROLE_TERMS = (
    "back", "wing", "winger", "centre", "center", "striker", "forward",
    "midfield", "midfielder", "keeper", "goalkeeper", "sweeper", "half",
)
STRONG_TIERS = {"tier_1", "tier_1_identity", "tier_1_official", "tier_2", "tier_2_independent"}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def iter_snapshots(path: Path) -> Iterable[tuple[dict[str, Any], str]]:
    obj = load(path)
    if isinstance(obj, dict) and isinstance(obj.get("snapshots"), list):
        for i, row in enumerate(obj["snapshots"]):
            if isinstance(row, dict):
                yield row, f"{path.name}#{i}"
    elif isinstance(obj, dict):
        yield obj, path.name


def norm_position(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def is_detailed_position(value: Any) -> bool:
    p = norm_position(value)
    if not p or p in BROAD:
        return False
    # A positional side/zone or multiword tactical label is detailed enough for
    # roster-role inventory, but not automatically match-specific tactical role.
    return any(term in p for term in ROLE_TERMS) or len(p.split()) >= 2


def latest_team_snapshots() -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    if not TEAM.exists():
        return []
    for path in sorted(TEAM.glob("*.json")):
        try:
            rows = list(iter_snapshots(path))
        except Exception:
            continue
        for obj, virtual in rows:
            cid = str(obj.get("competition_id") or "")
            team = str(obj.get("team_name") or "").strip()
            obs = parse_ts(obj.get("observed_at_utc"))
            if not cid or not team or obs is None:
                continue
            key = (cid, team)
            prev = latest.get(key)
            if prev is None or obs > prev[0]:
                latest[key] = (obs, obj, virtual)
    return [
        {"competition_id": k[0], "team": k[1], "observed_at": v[0], "obj": v[1], "file": v[2]}
        for k, v in sorted(latest.items())
    ]


def audit_roster_roles() -> dict[str, Any]:
    rows = latest_team_snapshots()
    counters = Counter()
    domains = defaultdict(lambda: Counter())
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        obj = row["obj"]
        cid = row["competition_id"]
        players = obj.get("players") if isinstance(obj.get("players"), list) else []
        depth = obj.get("depth_chart") if isinstance(obj.get("depth_chart"), list) else []
        sources = obj.get("sources") if isinstance(obj.get("sources"), list) else []
        strong = any(
            isinstance(s, dict)
            and str(s.get("source_tier") or "") in STRONG_TIERS
            and s.get("source_reached", True) is not False
            for s in sources
        )
        detailed_players = 0
        position_players = 0
        labels: set[str] = set()
        for player in players:
            if not isinstance(player, dict):
                continue
            positions = player.get("positions") if isinstance(player.get("positions"), list) else []
            cleaned = [norm_position(x) for x in positions if norm_position(x)]
            if cleaned:
                position_players += 1
            detailed = [p for p in cleaned if is_detailed_position(p)]
            if detailed:
                detailed_players += 1
                labels.update(detailed)
        roster18 = len(players) >= 18
        role_rich = roster18 and detailed_players >= 8 and len(labels) >= 4 and strong
        depth_nonempty = roster18 and len(depth) > 0 and strong
        counters["teams"] += 1
        counters["roster_18plus"] += int(roster18)
        counters["position_any"] += int(position_players > 0)
        counters["detailed_position_any"] += int(detailed_players > 0)
        counters["role_rich_candidate"] += int(role_rich)
        counters["depth_chart_nonempty"] += int(depth_nonempty)
        counters["strong_source"] += int(strong)
        domains[cid]["teams"] += 1
        domains[cid]["role_rich_candidate"] += int(role_rich)
        domains[cid]["depth_chart_nonempty"] += int(depth_nonempty)
        if role_rich and len(examples["role_rich"]) < 5:
            examples["role_rich"].append(row["file"])
        if depth_nonempty and len(examples["depth_nonempty"]) < 5:
            examples["depth_nonempty"].append(row["file"])
    return {
        "counts": dict(counters),
        "domain_counts": {k: dict(v) for k, v in sorted(domains.items())},
        "examples": dict(examples),
        "interpretation": "Roster position labels are descriptive team-state context. Even role-rich snapshots do not prove match-specific tactical assignment for a predicted XI.",
    }


def nested_keys(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k).lower())
            out |= nested_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= nested_keys(v)
    return out


def audit_live_match_roles() -> dict[str, Any]:
    c = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    if not LIVE.exists():
        return {"counts": {}, "examples": {}}
    for path in sorted(LIVE.glob("*.json")):
        try:
            obj = load(path)
        except Exception:
            continue
        ident = obj.get("fixture_identity") if isinstance(obj.get("fixture_identity"), dict) else {}
        obs = parse_ts(obj.get("observed_at_utc"))
        ko = parse_ts(ident.get("kickoff_at"))
        if not obs or not ko or obs >= ko:
            continue
        c["pre_kickoff_files"] += 1
        context = obj.get("context") if isinstance(obj.get("context"), dict) else {}
        xi = context.get("predicted_xi") if isinstance(context.get("predicted_xi"), dict) else {}
        home = xi.get("home") if isinstance(xi.get("home"), list) else []
        away = xi.get("away") if isinstance(xi.get("away"), list) else []
        if len(home) >= 11 and len(away) >= 11:
            c["predicted_xi_both"] += 1
        keys = nested_keys(context)
        match_role_keys = {"role", "position", "position_name", "detailed_position", "formation_position", "player_role", "tactical_role", "zone"}
        formation_keys = {"formation", "home_formation", "away_formation", "shape"}
        if keys & match_role_keys:
            c["match_specific_role_fields"] += 1
            if len(examples["match_roles"]) < 5:
                examples["match_roles"].append(path.name)
        if keys & formation_keys:
            c["formation_fields"] += 1
            if len(examples["formations"]) < 5:
                examples["formations"].append(path.name)
    return {"counts": dict(c), "examples": dict(examples)}


def audit_process_manifest() -> dict[str, Any]:
    shot_path = MANIFESTS / "shot_event_data_readiness_v506_status.json"
    understat_path = MANIFESTS / "v6_understat_fixture_alignment_audit_v6186_status.json"
    shot = load(shot_path) if shot_path.exists() else {}
    understat = load(understat_path) if understat_path.exists() else {}
    proxy = list(shot.get("shot_quantity_quality_proxy_ready_domains") or [])
    true_xg = list(shot.get("true_xg_ready_domains") or [])
    design = understat.get("design") if isinstance(understat.get("design"), dict) else {}
    return {
        "shot_sot_proxy_ready_domains": proxy,
        "shot_sot_proxy_ready_domain_count": len(proxy),
        "true_xg_ready_domains": true_xg,
        "true_xg_ready_domain_count": len(true_xg),
        "understat_alignment_domains": list(design.get("domains") or []),
        "understat_classification": understat.get("classification"),
        "understat_formal_current_version": understat.get("formal_current_version"),
        "ruling": "Strict-date historical shots/SOT can be a PIT process proxy in ready domains. It must not be relabelled true xG. Retrospectively captured Understat match xG remains non-formal unless CURRENT PIT provenance is independently proven.",
    }


def audit_task_evidence() -> dict[str, Any]:
    # Restrict search to context-oriented evidence only; do not inspect result datasets.
    roots = [EVID / "match_context_live", EVID / "gdelt_context_v517", EVID / "team_manager_context_weekly"]
    counts = Counter()
    examples = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            counts["context_files_scanned"] += 1
            if '"motivation_task_state"' in text:
                counts["motivation_task_state_records"] += text.count('"motivation_task_state"')
                if len(examples) < 5:
                    examples.append(str(path.relative_to(ROOT)))
            if '"schedule_fatigue"' in text:
                counts["schedule_fatigue_records"] += text.count('"schedule_fatigue"')
    return {"counts": dict(counts), "examples": examples}


def classify() -> dict[str, Any]:
    roles = audit_roster_roles()
    live = audit_live_match_roles()
    process = audit_process_manifest()
    task = audit_task_evidence()
    rc = roles.get("counts", {})
    lc = live.get("counts", {})
    tc = task.get("counts", {})

    role_rich = int(rc.get("role_rich_candidate", 0))
    depth_nonempty = int(rc.get("depth_chart_nonempty", 0))
    match_roles = int(lc.get("match_specific_role_fields", 0))
    predicted_both = int(lc.get("predicted_xi_both", 0))
    proxy_domains = int(process.get("shot_sot_proxy_ready_domain_count", 0))
    true_xg_domains = int(process.get("true_xg_ready_domain_count", 0))
    task_records = int(tc.get("motivation_task_state_records", 0))

    axes: dict[str, dict[str, Any]] = {}
    axes["detailed_roles"] = {
        "status": "PASS" if match_roles > 0 and predicted_both > 0 else "PARTIAL" if role_rich > 0 else "MISSING",
        "reason": "Match-specific predicted-XI tactical roles are required for PASS; roster-level detailed positions alone are partial context.",
    }
    axes["process_capability"] = {
        "status": "PASS" if true_xg_domains > 0 else "PARTIAL" if proxy_domains > 0 else "MISSING",
        "reason": "True xG has no ready domains; shots/SOT strict-date process proxy can support only ready domains and cannot be called xG.",
    }
    axes["task_utility"] = {
        "status": "PASS" if task_records > 0 else "MISSING",
        "reason": "Requires actual contract-conforming pre-match task-state evidence, not merely a schema listing motivation_task_state as an allowed type.",
    }
    axes["replacement_gap"] = {
        "status": "PASS" if (match_roles > 0 and depth_nonempty > 0 and proxy_domains > 0) else "MISSING",
        "reason": "Requires same-freeze unavailable-player identity + reliable role/depth replacement candidate + quality/process baseline; current prerequisites are not jointly satisfied.",
    }
    axes["matchup_interaction"] = {
        "status": "PASS" if match_roles > 0 and predicted_both > 0 else "MISSING",
        "reason": "Requires match-specific role assignments for both expected XIs at the same freeze; name-only XIs are insufficient.",
    }
    blocking = [k for k, v in axes.items() if v["status"] != "PASS"]
    return {
        "schema_version": "R45B-PIT-ROLE-DEEP-INVENTORY-R1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "stage": "ZERO_LABEL_DEEP_INVENTORY",
        "status": "PASS_R45B_DEEP_INPUTS" if not blocking else "STOP_R45B_DEEP_INPUTS_NOT_COMPLETE",
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "axes": axes,
        "blocking_axes": blocking,
        "inventory": {
            "roster_roles": roles,
            "live_match_roles": live,
            "process_capability": process,
            "task_evidence": task,
        },
        "forward_capture_contract": {
            "detailed_roles_required": [
                "fixture_key", "team", "player_id_or_unambiguous_name", "predicted_xi_status",
                "role_or_position_slot", "formation_or_shape_if_available", "source_url",
                "source_published_at_utc_or_collector_first_observed_at_utc", "payload_sha256", "freeze_at_utc",
            ],
            "replacement_gap_required": [
                "unavailable_player", "unavailable_player_role", "replacement_candidate",
                "replacement_candidate_role", "same_freeze_depth_or_selection_evidence",
                "strict_prior_process_or_quality_baseline", "source_and_timestamp_provenance",
            ],
            "matchup_interaction_required": [
                "both_teams_role_assigned_expected_xi", "same_freeze_timestamp",
                "formation_or_zone_mapping", "executable_interaction_definition_fixed_before_labels",
            ],
            "process_required": [
                "strictly_prior_match process statistics", "source identity", "date-before-freeze guard",
                "semantic label distinguishing shots/SOT proxy from true xG",
            ],
            "task_utility_required": [
                "fixture_key", "evidence_type=motivation_task_state_or_schedule_fatigue",
                "source_url", "published_or_independently_observed_at", "accessed_at", "freeze_at",
                "content_sha256", "structured extractor with no free-form probability override",
            ],
        },
        "independent_oos_authorized": not blocking,
        "automatic_promotion": False,
    }


def main() -> int:
    payload = classify()
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"].startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
