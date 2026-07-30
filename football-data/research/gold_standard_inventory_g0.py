#!/usr/bin/env python3
"""Research-only G0 inventory for sealed gold-standard football matches.

The scanner is intentionally fail-closed. A processed match, validation record,
research manifest, or historical context is never promoted to gold standard by
inference. Acceptance requires an explicit candidate flag, complete evidence,
and a sealed parent manifest whose record digest is reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_OUT = REPO / "artifacts" / "research" / "gold_standard_inventory_g0"
MAX_FILE_BYTES = 5_000_000
NAME_HINTS = ("gold", "context", "snapshot", "freeze", "receipt", "match", "fixture", "audit", "manifest")
SKIP_PARTS = {
    ".git", "artifacts", "node_modules", "__pycache__", "models", "processed",
    "validation", "archive", "archives", "research/contracts",
}
HEX40 = set("0123456789abcdef")
HEX64 = HEX40
SCOPE = "90_minutes_including_stoppage"


def git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def valid_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value.lower()) <= (HEX40 if length == 40 else HEX64)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def price(value: Any) -> bool:
    return number(value) and float(value) > 1.0


def likely_evidence_object(value: dict[str, Any]) -> bool:
    if isinstance(value.get("match_identity"), dict):
        return True
    identity_keys = {"competition_id", "home_team", "away_team"}
    return identity_keys.issubset(value) and any(
        key in value for key in ("freeze_time_utc", "result_90m", "market_snapshot", "original_market_snapshot")
    )


def iter_candidate_files() -> Iterable[Path]:
    root = REPO / "football-data"
    for suffix in ("*.json", "*.jsonl"):
        for path in root.rglob(suffix):
            rel = path.relative_to(REPO)
            rel_text = rel.as_posix().lower()
            if any(part in rel.parts for part in SKIP_PARTS):
                continue
            if any(token in rel_text for token in ("/models/", "/processed/", "/validation/", "/archive/", "/archives/")):
                continue
            if not any(hint in path.name.lower() for hint in NAME_HINTS):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def read_documents(path: Path) -> Iterable[tuple[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return
    if path.suffix.lower() == ".jsonl":
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                yield f"line:{index}", json.loads(line)
            except json.JSONDecodeError:
                continue
    else:
        try:
            yield "$", json.loads(text)
        except json.JSONDecodeError:
            return


def manifest_meta(value: dict[str, Any]) -> dict[str, Any] | None:
    records = value.get("records")
    if not isinstance(records, list):
        return None
    return {
        "schema_version": value.get("schema_version"),
        "manifest_version": value.get("manifest_version"),
        "created_at_utc": value.get("created_at_utc"),
        "selection_cutoff_utc": value.get("selection_cutoff_utc"),
        "sealed": value.get("sealed"),
        "repository_commit": value.get("repository_commit"),
        "declared_content_sha256": value.get("content_sha256"),
        "actual_content_sha256": canonical_digest(records),
        "record_count": len(records),
    }


def walk(value: Any, pointer: str, inherited_manifest: dict[str, Any] | None = None) -> Iterable[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    if isinstance(value, dict):
        local_manifest = manifest_meta(value) or inherited_manifest
        if likely_evidence_object(value):
            yield pointer, value, local_manifest
        for key, child in value.items():
            child_pointer = f"{pointer}/{key}" if pointer != "$" else f"$/{key}"
            yield from walk(child, child_pointer, local_manifest)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{pointer}/{index}", inherited_manifest)


def validate_manifest(meta: dict[str, Any] | None) -> list[str]:
    if meta is None:
        return ["not_inside_manifest"]
    reasons: list[str] = []
    if meta.get("schema_version") != "1.0": reasons.append("manifest_schema_version_invalid")
    if not nonempty(meta.get("manifest_version")): reasons.append("manifest_version_missing")
    if parse_timestamp(meta.get("created_at_utc")) is None: reasons.append("manifest_created_at_invalid")
    if parse_timestamp(meta.get("selection_cutoff_utc")) is None: reasons.append("manifest_selection_cutoff_invalid")
    if meta.get("sealed") is not True: reasons.append("manifest_not_sealed")
    if not valid_hex(meta.get("repository_commit"), 40): reasons.append("manifest_repository_commit_invalid")
    if not valid_hex(meta.get("declared_content_sha256"), 64): reasons.append("manifest_digest_invalid")
    elif meta.get("declared_content_sha256") != meta.get("actual_content_sha256"):
        reasons.append("manifest_digest_mismatch")
    return reasons


def validate_identity(record: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    identity = record.get("match_identity")
    if not isinstance(identity, dict):
        return None, ["match_identity_missing"]
    reasons: list[str] = []
    for key in ("match_id", "competition_id", "round_or_stage", "home_team", "away_team", "match_status", "venue_or_neutral_status", "first_leg_status"):
        if not nonempty(identity.get(key)): reasons.append(f"identity_{key}_missing")
    kickoff = parse_timestamp(identity.get("kickoff_time_utc"))
    freeze = parse_timestamp(identity.get("freeze_time_utc"))
    if kickoff is None: reasons.append("kickoff_time_invalid")
    if freeze is None: reasons.append("freeze_time_invalid")
    if kickoff is not None and freeze is not None and freeze > kickoff: reasons.append("freeze_after_kickoff")
    if identity.get("settlement_scope") != SCOPE: reasons.append("settlement_scope_not_90m")
    if not isinstance(identity.get("is_two_legged"), bool): reasons.append("two_leg_status_not_explicit")
    if identity.get("home_team") == identity.get("away_team") and nonempty(identity.get("home_team")):
        reasons.append("home_away_identity_conflict")
    return identity, reasons


def validate_result(record: dict[str, Any]) -> list[str]:
    result = record.get("result_90m")
    if not isinstance(result, dict): return ["result_90m_missing"]
    reasons: list[str] = []
    for key in ("home_goals", "away_goals"):
        value = result.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            reasons.append(f"result_{key}_invalid")
    if result.get("scope") != SCOPE: reasons.append("result_scope_not_90m")
    if result.get("extra_time_included") is not False: reasons.append("extra_time_exclusion_not_explicit")
    if result.get("penalties_included") is not False: reasons.append("penalty_exclusion_not_explicit")
    return reasons


def validate_sources(record: dict[str, Any]) -> list[str]:
    sources = record.get("source_evidence")
    if not isinstance(sources, list) or not sources: return ["source_evidence_missing"]
    reasons: list[str] = []
    for item in sources:
        if not isinstance(item, dict) or not nonempty(item.get("source")) or parse_timestamp(item.get("captured_at_utc")) is None:
            reasons.append("source_evidence_item_invalid")
            break
    return reasons


def validate_freeze(record: dict[str, Any]) -> list[str]:
    audit = record.get("freeze_audit")
    if not isinstance(audit, dict): return ["freeze_audit_missing"]
    reasons = []
    if audit.get("post_freeze_data_excluded") is not True: reasons.append("post_freeze_exclusion_not_proven")
    if audit.get("freeze_not_after_kickoff") is not True: reasons.append("freeze_order_audit_missing")
    return reasons


def validate_market(record: dict[str, Any], freeze: datetime | None) -> list[str]:
    market = record.get("market_snapshot")
    if not isinstance(market, dict): return ["market_snapshot_missing"]
    reasons: list[str] = []
    captured = parse_timestamp(market.get("captured_at_utc"))
    if captured is None: reasons.append("market_timestamp_invalid")
    elif freeze is not None and captured > freeze: reasons.append("market_after_freeze")
    window = market.get("synchronization_window_seconds")
    if not isinstance(window, int) or isinstance(window, bool) or not 0 <= window <= 3600:
        reasons.append("market_sync_window_invalid")
    if market.get("source_independence_status") != "verified": reasons.append("market_source_independence_unverified")
    one = market.get("one_x_two")
    if not isinstance(one, dict) or not all(price(one.get(k)) for k in ("home_price", "draw_price", "away_price")):
        reasons.append("complete_1x2_prices_missing")
    for section_name in ("asian_handicap", "total_goals"):
        section = market.get(section_name)
        if not isinstance(section, dict) or not number(section.get("line")) or not price(section.get("side_a_price")) or not price(section.get("side_b_price")):
            reasons.append(f"complete_{section_name}_prices_missing")
    return reasons


def validate_lineup(record: dict[str, Any], freeze: datetime | None) -> list[str]:
    lineup = record.get("lineup_assessment")
    if not isinstance(lineup, dict): return ["lineup_assessment_missing"]
    reasons: list[str] = []
    if lineup.get("status") != "通过": reasons.append("lineup_status_not_passed")
    lineup_type = lineup.get("lineup_type")
    if not isinstance(lineup_type, str) or lineup_type not in {"official", "predicted"}:
        reasons.append("lineup_type_invalid")
    captured = parse_timestamp(lineup.get("captured_at_utc"))
    if captured is None: reasons.append("lineup_timestamp_invalid")
    elif freeze is not None and captured > freeze: reasons.append("lineup_after_freeze")
    if not nonempty(lineup.get("source")): reasons.append("lineup_source_missing")
    return reasons


def evaluate(record: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    if record.get("gold_standard_candidate") is not True: reasons.append("explicit_candidate_flag_missing")
    tier = record.get("gold_standard_tier")
    if not isinstance(tier, str) or tier not in {"GS-CORE", "GS-FULL"}:
        reasons.append("gold_standard_tier_invalid")
    identity, identity_reasons = validate_identity(record)
    reasons.extend(identity_reasons)
    reasons.extend(validate_result(record))
    reasons.extend(validate_sources(record))
    reasons.extend(validate_freeze(record))
    if not valid_hex(record.get("context_sha256"), 64): reasons.append("context_sha256_invalid")
    reasons.extend(validate_manifest(meta))
    freeze = parse_timestamp(identity.get("freeze_time_utc")) if identity else None
    core_reasons = list(dict.fromkeys(reasons))
    full_reasons = list(core_reasons)
    if tier == "GS-FULL":
        full_reasons.extend(validate_market(record, freeze))
        full_reasons.extend(validate_lineup(record, freeze))
    else:
        full_reasons.append("record_not_declared_gs_full")
    full_reasons = list(dict.fromkeys(full_reasons))
    match_id = identity.get("match_id") if identity else None
    return {
        "match_id": match_id,
        "declared_tier": tier if isinstance(tier, str) else None,
        "core_eligible": not core_reasons,
        "full_eligible": not full_reasons,
        "core_reasons": core_reasons,
        "full_reasons": full_reasons,
    }


def markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# G0 Gold-Standard Inventory",
        "",
        "Research-only; no formal mutation.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Scanned files: {counts['scanned_files']}",
        f"- Evidence-like objects: {counts['evidence_objects']}",
        f"- Explicit candidates: {counts['explicit_candidates']}",
        f"- Sealed GS-CORE eligible: {counts['sealed_gs_core']}",
        f"- Sealed GS-FULL eligible: {counts['sealed_gs_full']}",
        f"- Inventory status: `{report['status']}`",
        "",
        "## Leading rejection reasons",
        "",
    ]
    if report["rejection_reasons"]:
        for reason, count in report["rejection_reasons"][:20]:
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- None")
    lines += [
        "",
        "Processed or historical OOS matches are not promoted by inference. Zero eligible records is a valid fail-closed result.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scanned_files = 0
    evidence_objects = 0
    explicit_candidates = 0
    accepted_core: list[dict[str, Any]] = []
    accepted_full: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for path in sorted(set(iter_candidate_files())):
        scanned_files += 1
        rel = path.relative_to(REPO).as_posix()
        for root_pointer, document in read_documents(path):
            for pointer, record, meta in walk(document, root_pointer):
                evidence_objects += 1
                if record.get("gold_standard_candidate") is True:
                    explicit_candidates += 1
                result = evaluate(record, meta)
                item = {
                    "file": rel,
                    "pointer": pointer,
                    "match_id": result["match_id"],
                    "declared_tier": result["declared_tier"],
                }
                if result["core_eligible"]:
                    accepted_core.append(item)
                else:
                    reason_counts.update(result["core_reasons"])
                    rejected.append({**item, "reasons": result["core_reasons"]})
                if result["full_eligible"]:
                    accepted_full.append(item)

    if accepted_full:
        status = "SEALED_GS_FULL_AVAILABLE"
    elif accepted_core:
        status = "SEALED_GS_CORE_AVAILABLE_GS_FULL_MISSING"
    else:
        status = "NO_SEALED_GOLD_STANDARD_SET"

    report = {
        "schema_version": "1.0",
        "research_stage": "G0_GOLD_STANDARD_INVENTORY",
        "repository_head": git_head(),
        "status": status,
        "counts": {
            "scanned_files": scanned_files,
            "evidence_objects": evidence_objects,
            "explicit_candidates": explicit_candidates,
            "sealed_gs_core": len(accepted_core),
            "sealed_gs_full": len(accepted_full),
        },
        "accepted_gs_core": accepted_core,
        "accepted_gs_full": accepted_full,
        "rejection_reasons": reason_counts.most_common(),
        "rejected_candidates": rejected,
        "controls": {
            "automatic_promotion": False,
            "formal_weight": 0,
            "development_oos_is_gold_standard": False,
            "zero_is_valid_fail_closed_result": True,
        },
    }
    (output_dir / "gold_standard_inventory_g0.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "gold_standard_inventory_g0.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({"status": status, **report["counts"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
