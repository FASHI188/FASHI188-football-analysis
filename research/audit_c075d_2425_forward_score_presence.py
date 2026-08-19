#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

SOURCE_COMMIT = "ea767ac28cf9a2d737bb3e4ce65aa4b1f4ac9361"
FILES = [
    "2024-25/at.1.json", "2024-25/at.2.json", "2024-25/au.1.json",
    "2024-25/de.2.json", "2024-25/dz.1.json", "2024-25/eg.1.json",
    "2024-25/en.2.json", "2024-25/en.3.json", "2024-25/en.4.json",
    "2024-25/es.2.json", "2024-25/fr.2.json", "2024-25/gr.1.json",
    "2024-25/it.2.json", "2024-25/ma.1.json", "2024-25/mx.1.json",
    "2024-25/pt.1.json", "2024-25/sco.1.json", "2024-25/tr.1.json",
]
BANNED_TOP = {
    "2024-25/en.1.json", "2024-25/es.1.json", "2024-25/it.1.json",
    "2024-25/de.1.json", "2024-25/fr.1.json", "2024-25/nl.1.json", "2024-25/be.1.json",
}
CONSUMED_C075C = {
    "2019/br.1.json", "2019/cn.1.json", "2019/jp.1.json",
    "2020/br.1.json", "2020/cn.1.json", "2020/jp.1.json",
    "2025/ar.1.json", "2025/br.1.json", "2025/br.2.json", "2025/cn.1.json",
    "2025/co.1.json", "2025/jp.1.json", "2025/mls.json",
}
STR = r'"((?:\\.|[^"\\])*)"'
DATE_RE = re.compile(r'"date"\s*:\s*' + STR)
TEAM1_RE = re.compile(r'"team1"\s*:\s*' + STR)
TEAM2_RE = re.compile(r'"team2"\s*:\s*' + STR)
FT_ARRAY_TOKEN_RE = re.compile(r'"ft"\s*:\s*\[')


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def decode_string(payload: str) -> str:
    return json.loads('"' + payload + '"')


def sha_keys(keys: list[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(keys)) + "\n").encode("utf-8")).hexdigest()


def iter_match_object_text(text: str):
    m = re.search(r'"matches"\s*:\s*\[', text)
    if not m:
        raise RuntimeError("matches array not found")
    i = m.end(); n = len(text); in_string = False; escape = False; arr = 1
    while i < n and arr > 0:
        c = text[i]
        if in_string:
            if escape: escape = False
            elif c == "\\": escape = True
            elif c == '"': in_string = False
            i += 1; continue
        if c == '"': in_string = True; i += 1; continue
        if c == '[': arr += 1; i += 1; continue
        if c == ']': arr -= 1; i += 1; continue
        if c != '{' or arr != 1: i += 1; continue
        start = i; brace = 0; s = False; esc = False
        while i < n:
            ch = text[i]
            if s:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': s = False
            else:
                if ch == '"': s = True
                elif ch == '{': brace += 1
                elif ch == '}':
                    brace -= 1
                    if brace == 0:
                        i += 1
                        yield text[start:i]
                        break
            i += 1
        else:
            raise RuntimeError("unterminated match object")


def identity(obj: str):
    d = DATE_RE.search(obj); h = TEAM1_RE.search(obj); a = TEAM2_RE.search(obj)
    if not (d and h and a):
        return None
    return decode_string(d.group(1)), decode_string(h.group(1)), decode_string(a.group(1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(); root = Path(a.source_root); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    head = git("rev-parse", "HEAD", cwd=root)
    if head != SOURCE_COMMIT:
        raise RuntimeError(f"source commit drift {head}")
    if set(FILES) & BANNED_TOP:
        raise RuntimeError("candidate includes C074-G viewed top league")
    if set(FILES) & CONSUMED_C075C:
        raise RuntimeError("candidate overlaps C075-C consumed files")

    keys = []
    file_report = {}
    presence_total = 0
    for rel in FILES:
        p = root / rel
        if not p.is_file():
            raise RuntimeError(f"missing fixed file {rel}")
        text = p.read_text(encoding="utf-8")
        n = present = 0
        file_keys = []
        for obj in iter_match_object_text(text):
            ident = identity(obj)
            if ident is None:
                raise RuntimeError(f"identity parse failure {rel}")
            date, team1, team2 = ident
            key = f"{rel}|{date}|{team1}|{team2}"
            file_keys.append(key); keys.append(key); n += 1
            # Presence-only audit: the regex ends at the opening '[' token and never captures,
            # converts, returns, hashes or stores either score number.
            if FT_ARRAY_TOKEN_RE.search(obj) is not None:
                present += 1; presence_total += 1
        frac = float(present / n) if n else 0.0
        file_report[rel] = {
            "identity_count": int(n),
            "score_ft_array_token_present_count": int(present),
            "score_ft_presence_fraction": frac,
            "git_blob_sha": git("rev-parse", f"HEAD:{rel}", cwd=root),
            "byte_length": int(p.stat().st_size),
            "identity_sha256": sha_keys(file_keys),
        }

    duplicate_count = len(keys) - len(set(keys))
    overall_frac = float(presence_total / len(keys)) if keys else 0.0
    min_file_frac = min(v["score_ft_presence_fraction"] for v in file_report.values()) if file_report else 0.0
    gate = {
        "fixed_file_count_18": len(FILES) == 18,
        "identity_count_ge_5000": len(keys) >= 5000,
        "duplicate_identity_count_zero": duplicate_count == 0,
        "overall_score_ft_presence_fraction_ge_0_98": overall_frac >= 0.98,
        "each_file_score_ft_presence_fraction_ge_0_95": min_file_frac >= 0.95,
        "C075C_consumed_file_overlap_zero": len(set(FILES) & CONSUMED_C075C) == 0,
        "C074G_viewed_top_file_overlap_zero": len(set(FILES) & BANNED_TOP) == 0,
    }
    passed = all(gate.values())
    summary = {
        "schema_version": "C075D_2425_FORWARD_SCORE_PRESENCE_AUDIT_V1",
        "status": "PASS_ZERO_VALUE_FORWARD_SOURCE_GATE" if passed else "FAIL_SOURCE_GATE",
        "source": {"repository": "openfootball/football.json", "commit": head},
        "fixed_file_count": len(FILES),
        "identity_count": int(len(keys)),
        "identity_sha256": sha_keys(keys),
        "duplicate_identity_count": int(duplicate_count),
        "score_ft_presence_count": int(presence_total),
        "score_ft_presence_fraction": overall_frac,
        "minimum_per_file_presence_fraction": min_file_frac,
        "files": file_report,
        "gate": gate,
        "label_boundary": {
            "score_numbers_captured": False,
            "score_numbers_converted": False,
            "score_numbers_stored": False,
            "score_numbers_hashed": False,
            "goal_totals_computed": False,
            "tail_membership_computed": False,
            "model_fit": False,
            "only_score_ft_array_token_presence_inspected": True,
        },
        "protected_boundaries": {
            "C075C_consumed_tail_labels_reused": False,
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "unified_matrix_generated": False,
            "formal_weight": 0,
        },
        "next_if_pass": "freeze same r=.3158284023668639 and one-shot C075-D confirmation gates against this exact identity pool before any score value extraction",
    }
    (out/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    with (out/"identity_manifest.jsonl").open("w", encoding="utf-8") as f:
        for key in sorted(keys):
            f.write(json.dumps({"identity_key": key}, ensure_ascii=False)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
