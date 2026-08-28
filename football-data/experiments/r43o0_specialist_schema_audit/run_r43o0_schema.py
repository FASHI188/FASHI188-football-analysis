#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
R42H_DIR = ROOT / "football-data" / "experiments" / "r42h_player_technical_translation"
if str(R42H_DIR) not in sys.path:
    sys.path.insert(0, str(R42H_DIR))

import run_r42h_player_technical_translation as r42h  # noqa: E402

TOKENS = ("goal", "save", "penalt", "shot", "minute", "fixture", "game", "team", "player", "position")


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stats_path = Path(r42h.download_stats())
    pf = pq.ParquetFile(stats_path)
    schema = pf.schema_arrow
    cols = [f.name for f in schema]
    dtypes = {f.name: str(f.type) for f in schema}
    relevant = [c for c in cols if any(t in c.lower() for t in TOKENS)]
    result = {
        "schema_version": "football3-r43o0-specialist-schema-audit-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "zero_label": True,
        "governance": {
            "outcome_labels_accessed": False,
            "model_fit": False,
            "parameter_search": False,
            "feature_effect_search": False,
            "r42l_lock_modified": False,
        },
        "stats_path_name": stats_path.name,
        "stats_row_groups": pf.num_row_groups,
        "stats_columns": cols,
        "stats_dtypes": dtypes,
        "specialist_relevant_columns": relevant,
        "r42h_rate_fields": list(r42h.RATE_FIELDS),
        "load_technical_rows_source": inspect.getsource(r42h.load_technical_rows),
        "technical_ledger_update_row_source": inspect.getsource(r42h.TechnicalLedger.update_row),
        "download_stats_source": inspect.getsource(r42h.download_stats),
    }
    p = OUT / "summary_r43o0_specialist_schema_audit.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "relevant": relevant,
        "rate_fields": result["r42h_rate_fields"],
    }, indent=2))
    return result


def verify() -> None:
    p = OUT / "summary_r43o0_specialist_schema_audit.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["zero_label"] is True
    assert d["governance"]["outcome_labels_accessed"] is False
    assert d["governance"]["model_fit"] is False
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["r42l_lock_modified"] is False
    print("R43O0 zero-label schema audit contract verified")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "run":
        run()
    elif mode == "verify":
        verify()
    else:
        raise SystemExit(f"unknown mode: {mode}")
