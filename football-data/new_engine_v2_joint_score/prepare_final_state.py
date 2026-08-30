from __future__ import annotations

import json
from pathlib import Path

from engine import EngineState, Fixture, Parameters
from research import batch_ranges, make_fixture, read_jsonl
from strict import GovernanceError, sha256_file, strict_nonnegative_int

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"


def main() -> int:
    manifest = json.loads((EVIDENCE / "universe_manifest.json").read_text(encoding="utf-8"))
    lock = json.loads((EVIDENCE / "final_model_lock.json").read_text(encoding="utf-8"))
    rows_path = EVIDENCE / "research_rows.jsonl"
    if sha256_file(rows_path) != manifest["research_rows_sha256"]:
        raise GovernanceError("research rows digest drift")
    rows = read_jsonl(rows_path)
    if len(rows) != int(manifest["research_n"]):
        raise GovernanceError("research count drift")
    engine = EngineState(Parameters(**lock["selected_dynamic_parameters"]))
    for start, end in batch_ranges(rows):
        fixtures = [make_fixture(r) for r in rows[start:end]]
        labels = {
            f.fixture_id: (
                strict_nonnegative_int(r["home_goals"], "research.home_goals"),
                strict_nonnegative_int(r["away_goals"], "research.away_goals"),
            )
            for f, r in zip(fixtures, rows[start:end])
        }
        engine.apply_batch(fixtures, labels)
    state_path = EVIDENCE / "final_state_initial.json"
    state_path.write_text(json.dumps(engine.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": "football3-v2-final-initial-state-v1",
        "research_rows_sha256": manifest["research_rows_sha256"],
        "research_n": len(rows),
        "model_lock_sha256": sha256_file(EVIDENCE / "final_model_lock.json"),
        "state_sha256": sha256_file(state_path),
        "final_holdout_labels_read": False,
    }
    (EVIDENCE / "final_state_initial_manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
