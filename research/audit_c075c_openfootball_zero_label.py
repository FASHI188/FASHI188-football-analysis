#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PINNED = "ea767ac28cf9a2d737bb3e4ce65aa4b1f4ac9361"
FILES = [
    "2019/br.1.json", "2019/cn.1.json", "2019/jp.1.json",
    "2020/br.1.json", "2020/cn.1.json", "2020/jp.1.json",
    "2025/ar.1.json", "2025/br.1.json", "2025/br.2.json", "2025/cn.1.json",
    "2025/co.1.json", "2025/jp.1.json", "2025/mls.json",
]
CUTOFF_2025 = "2025-08-15"
STR = r'"((?:\\.|[^"\\])*)"'
DATE_RE = re.compile(r'"date"\s*:\s*' + STR)
TEAM1_RE = re.compile(r'"team1"\s*:\s*' + STR)
TEAM2_RE = re.compile(r'"team2"\s*:\s*' + STR)


def decode_json_string(payload: str) -> str:
    return json.loads('"' + payload + '"')


def sha256_text(lines: list[str]) -> str:
    p = "\n".join(lines) + "\n"
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    root = Path(a.source_root)
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    head = git("rev-parse", "HEAD", cwd=root)
    if head != PINNED:
        raise RuntimeError(f"source commit drift {head} != {PINNED}")

    file_rows = []
    identity_rows = []
    family_set = set()
    year_set = set()

    for rel in FILES:
        p = root / rel
        if not p.is_file():
            raise RuntimeError(f"missing fixed source file: {rel}")
        # Deliberately extract ONLY date/team1/team2 literals. No score/result key is
        # searched, selected, parsed, printed, hashed into the identity, or returned.
        text = p.read_text(encoding="utf-8")
        dates = [decode_json_string(x) for x in DATE_RE.findall(text)]
        t1 = [decode_json_string(x) for x in TEAM1_RE.findall(text)]
        t2 = [decode_json_string(x) for x in TEAM2_RE.findall(text)]
        if not (len(dates) == len(t1) == len(t2)):
            raise RuntimeError(f"identity field count mismatch {rel}: date={len(dates)} team1={len(t1)} team2={len(t2)}")
        year = rel.split("/", 1)[0]
        family = rel.split("/", 1)[1].rsplit(".json", 1)[0]
        family_set.add(family); year_set.add(year)
        kept = 0
        keys = []
        for d, h, aw in zip(dates, t1, t2):
            if year == "2025" and d[:10] > CUTOFF_2025:
                continue
            key = f"{rel}|{d}|{h}|{aw}"
            keys.append(key)
            identity_rows.append({"source_file": rel, "date": d, "team1": h, "team2": aw, "identity_key": key})
            kept += 1
        blob = git("rev-parse", f"HEAD:{rel}", cwd=root)
        file_rows.append({
            "source_file": rel,
            "year": year,
            "family": family,
            "git_blob_sha": blob,
            "byte_length": int(p.stat().st_size),
            "raw_identity_count": int(len(dates)),
            "frozen_identity_count": int(kept),
            "frozen_identity_sha256": sha256_text(keys),
        })

    keys = [r["identity_key"] for r in identity_rows]
    duplicate_count = len(keys) - len(set(keys))
    total = len(keys)
    pass_checks = {
        "fixed_file_count_13": len(file_rows) == 13,
        "all_identity_field_counts_valid": True,
        "duplicate_identity_count_zero": duplicate_count == 0,
        "frozen_identity_count_ge_1500": total >= 1500,
        "league_file_families_ge_3": len(family_set) >= 3,
        "calendar_years_ge_2": len(year_set) >= 2,
        "score_fields_selected": False,
        "score_values_materialized": False,
        "model_fit": False,
        "tail_scoring": False,
    }
    passed = all(v is True for k, v in pass_checks.items() if k not in {"score_fields_selected", "score_values_materialized", "model_fit", "tail_scoring"})

    summary = {
        "schema_version": "C075C_OPENFOOTBALL_ZERO_LABEL_AUDIT_V1",
        "status": "PASS_ZERO_LABEL_EXTERNAL_IDENTITY_FREEZE" if passed else "FAIL_SOURCE_GATE",
        "source": {"repository": "openfootball/football.json", "commit": head},
        "identity_horizon": {"2025_cutoff_inclusive": CUTOFF_2025, "2019_2020": "all identities"},
        "fixed_file_count": len(file_rows),
        "frozen_identity_count": total,
        "frozen_identity_sha256": sha256_text(sorted(keys)),
        "duplicate_identity_count": duplicate_count,
        "league_file_families": sorted(family_set),
        "calendar_years": sorted(year_set),
        "files": file_rows,
        "gate": pass_checks,
        "label_boundary": {
            "score_fields_selected": False,
            "score_values_materialized": False,
            "result_or_goal_metrics_computed": False,
            "tail_membership_computed": False,
            "model_fit": False,
            "tail_scoring": False,
        },
        "protected_boundaries": {
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "unified_matrix_generated": False,
            "formal_weight": 0,
        },
        "next_if_pass": "freeze C075-C simple exact-tail-law external confirmation contract before any score-field selection",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "frozen_identity_manifest.jsonl").open("w", encoding="utf-8") as f:
        for row in identity_rows:
            safe = {k: row[k] for k in ("source_file", "date", "team1", "team2", "identity_key")}
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
