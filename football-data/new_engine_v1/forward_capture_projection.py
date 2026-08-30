from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE = ROOT / "manifests" / "v6_full17_kambi_capture_v6482_status.json"
DEFAULT_OUT = Path(__file__).resolve().parent / "forward" / "safe_capture_candidates.json"
VALID_EVENT_STATUSES = {"VALID_KAMBI_FULL17_PIT_WRITTEN", "ALREADY_PRESENT_IDENTICAL"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project(capture: dict[str, Any], capture_sha256: str) -> dict[str, Any]:
    safe_events: list[dict[str, Any]] = []
    for row in capture.get("events") or []:
        if not isinstance(row, dict) or row.get("status") not in VALID_EVENT_STATUSES:
            continue
        safe_events.append({
            "competition_id": str(row.get("competition_id") or ""),
            "provider_event_id": str(row.get("event_id") or ""),
            "canonical_home": str(row.get("canonical_home") or ""),
            "canonical_away": str(row.get("canonical_away") or ""),
            "kickoff_utc": str(row.get("provider_start") or ""),
            "provider_state": str(row.get("provider_state") or ""),
            "observed_at_utc": str(row.get("detail_observed_at_utc") or ""),
            "formal_snapshot_path": str(row.get("formal_snapshot_path") or ""),
            "identity_status": str(row.get("status") or ""),
        })
    safe_events.sort(key=lambda x: (x["kickoff_utc"], x["competition_id"], x["provider_event_id"]))
    return {
        "schema_version": "football3-new-engine-v1-safe-capture-projection-v1",
        "capture_manifest_sha256": capture_sha256,
        "capture_status": capture.get("status"),
        "capture_generated_at_utc": capture.get("generated_at_utc"),
        "identity_registry_path": capture.get("identity_registry_path"),
        "identity_registry_sha256": capture.get("identity_registry_sha256"),
        "group_alias_path": capture.get("group_alias_path"),
        "group_alias_sha256": capture.get("group_alias_sha256"),
        "safe_event_count": len(safe_events),
        "events": safe_events,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default=str(DEFAULT_CAPTURE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    capture_path = Path(args.capture)
    out_path = Path(args.out)
    if not capture_path.exists():
        raise SystemExit(f"capture manifest missing: {capture_path}")
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    payload = project(capture, sha256_file(capture_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"capture_status": payload["capture_status"], "safe_event_count": payload["safe_event_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
