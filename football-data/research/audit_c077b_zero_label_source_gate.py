#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

CONTRACT_PATH = Path("football-data/research/c077b_candidate_source_freeze_v1.json")
WEEKDAYS = r"Mon|Tue|Wed|Thu|Fri|Sat|Sun"
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
DATE_LINE = re.compile(
    rf"^\s*(?:{WEEKDAYS})\s+(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{{1,2}})(?:\s+(?P<year>\d{{4}}))?\s*$"
)
# Presence-only. The score token is never captured, converted, stored, hashed, summed or compared.
SCORE_TOKEN_PRESENT = re.compile(r"\s+\d+\s*-\s*\d+(?=\s|\(|$)")
LEADING_TIME = re.compile(r"^\s*(?:\d{1,2}:\d{2}\s+)?")


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def sha_lines(lines: list[str]) -> str:
    payload = "\n".join(sorted(lines)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().casefold()
    s = re.sub(r"\s+", " ", s)
    return s


def infer_base_year(rel: str) -> int:
    m = re.search(r"(?:^|/)(20\d{2})(?:-|_)", rel)
    if not m:
        raise RuntimeError(f"cannot infer base year from {rel}")
    return int(m.group(1))


def parse_file(path: Path, rel: str) -> tuple[list[dict], dict]:
    base_year = infer_base_year(rel)
    current_year = base_year
    current_month: int | None = None
    current_date: date | None = None

    fixture_like = 0
    score_present = 0
    completed = []
    date_header_count = 0
    missing_date_for_completed = 0
    malformed_completed = 0

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        dm = DATE_LINE.match(raw)
        if dm:
            mon = MONTHS[dm.group("mon")]
            day = int(dm.group("day"))
            explicit_year = dm.group("year")
            if explicit_year is not None:
                current_year = int(explicit_year)
            elif current_month is not None and current_month >= 10 and mon <= 3:
                current_year += 1
            current_month = mon
            current_date = date(current_year, mon, day)
            date_header_count += 1
            continue

        if " v " not in raw:
            continue
        fixture_like += 1
        sm = SCORE_TOKEN_PRESENT.search(raw)
        if sm is None:
            continue
        score_present += 1
        if current_date is None:
            missing_date_for_completed += 1
            continue

        prefix = raw[: sm.start()]
        prefix = LEADING_TIME.sub("", prefix, count=1).strip()
        if " v " not in prefix:
            malformed_completed += 1
            continue
        home, away = prefix.rsplit(" v ", 1)
        home = home.strip()
        away = away.strip()
        if not home or not away:
            malformed_completed += 1
            continue

        completed.append(
            {
                "date": current_date.isoformat(),
                "calendar_year": current_date.year,
                "home": home,
                "away": away,
                "home_norm": norm_team(home),
                "away_norm": norm_team(away),
                "competition_family": rel.split("/", 1)[0],
                "source_file": rel,
            }
        )

    report = {
        "fixture_like_line_count": fixture_like,
        "score_token_present_count": score_present,
        "score_token_presence_fraction": score_present / fixture_like if fixture_like else 0.0,
        "completed_identity_count_before_year_filter": len(completed),
        "date_header_count": date_header_count,
        "missing_date_for_completed": missing_date_for_completed,
        "malformed_completed": malformed_completed,
    }
    return completed, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(args.source_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    pinned = contract["candidate_repo_commit"]
    actual = git("rev-parse", "HEAD", cwd=root)
    if actual != pinned:
        raise RuntimeError(f"source drift: expected {pinned}, got {actual}")

    files = contract["frozen_candidate_files"]
    families = set(contract["frozen_competition_families"])
    gate_spec = contract["source_gate"]
    allowed_years = set(gate_spec["accepted_fixture_calendar_years"])

    prior_family_union = set(contract["explicit_prior_exclusions"]["C075C_consumed_competition_families"])
    prior_family_union |= set(contract["explicit_prior_exclusions"]["C075E_consumed_competition_families"])
    # C074-G/C076-D use provider codes; V3 candidate country families were frozen to avoid those domains entirely.
    forbidden_candidate_families = {
        "england", "spain", "italy", "germany", "france", "netherlands", "belgium",
        "scotland", "portugal", "greece", "turkey", "austria", "australia", "morocco", "mexico",
        "argentina", "brazil", "china", "colombia", "japan", "mls"
    }

    file_reports = {}
    accepted = []
    all_fixture_like = 0
    all_score_present = 0
    source_blob_lines = []

    for rel in files:
        p = root / rel
        if not p.is_file():
            raise RuntimeError(f"missing frozen source file: {rel}")
        rows, rep = parse_file(p, rel)
        blob = git("rev-parse", f"HEAD:{rel}", cwd=root)
        rep["git_blob_sha"] = blob
        rep["byte_length"] = p.stat().st_size
        source_blob_lines.append(f"{rel}|{blob}|{p.stat().st_size}")
        all_fixture_like += rep["fixture_like_line_count"]
        all_score_present += rep["score_token_present_count"]

        kept = [r for r in rows if r["calendar_year"] in allowed_years]
        rep["accepted_completed_identity_count"] = len(kept)
        rep["rejected_outside_frozen_calendar_years"] = len(rows) - len(kept)
        file_reports[rel] = rep
        accepted.extend(kept)

    keys = [f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in accepted]
    duplicate_identity_count = len(keys) - len(set(keys))
    by_year = Counter(r["calendar_year"] for r in accepted)
    by_family = Counter(r["competition_family"] for r in accepted)
    parsed_families = set(by_family)
    date_values = [r["date"] for r in accepted]

    score_presence_fraction = all_score_present / all_fixture_like if all_fixture_like else 0.0
    family_overlap = sorted(parsed_families & forbidden_candidate_families)
    prior_named_overlap = sorted(parsed_families & prior_family_union)

    gate = {
        "source_commit_exact": actual == pinned,
        "required_file_count_exact": len(files) == gate_spec["required_file_count"],
        "all_frozen_files_present": len(file_reports) == len(files),
        "completed_identity_count_ge_min": len(accepted) >= gate_spec["minimum_completed_identity_count"],
        "duplicate_identity_count_zero": duplicate_identity_count == gate_spec["duplicate_identity_count"],
        "early_2024_completed_ge_min": by_year.get(2024, 0) >= gate_spec["minimum_completed_identities_each_time_block"],
        "late_2025_completed_ge_min": by_year.get(2025, 0) >= gate_spec["minimum_completed_identities_each_time_block"],
        "competition_family_count_ge_min": len(parsed_families) >= gate_spec["minimum_competition_families"],
        "score_token_presence_fraction_ge_min": score_presence_fraction >= gate_spec["score_token_presence_fraction_min"],
        "only_frozen_calendar_years_accepted": set(by_year).issubset(allowed_years),
        "competition_family_overlap_zero": not family_overlap and not prior_named_overlap,
        "all_contract_families_accounted_for": parsed_families.issubset(families),
        "completed_rows_have_date_and_team_identity": all(r["date"] and r["home"] and r["away"] for r in accepted),
    }
    passed = all(gate.values())

    summary = {
        "schema_version": "C077B_ZERO_LABEL_SOURCE_GATE_V1",
        "status": "PASS_ZERO_LABEL_SOURCE_GATE" if passed else "FAIL_ZERO_LABEL_SOURCE_GATE",
        "source": {"repository": contract["candidate_repo"], "commit": actual},
        "frozen_file_count": len(files),
        "completed_identity_count": len(accepted),
        "identity_sha256": sha_lines(keys),
        "source_blob_inventory_sha256": sha_lines(source_blob_lines),
        "duplicate_identity_count": duplicate_identity_count,
        "time_block_completed_counts": {"2024_early": by_year.get(2024, 0), "2025_late": by_year.get(2025, 0)},
        "competition_family_count": len(parsed_families),
        "competition_family_counts": dict(sorted(by_family.items())),
        "score_token_presence_fraction": score_presence_fraction,
        "fixture_like_line_count": all_fixture_like,
        "score_token_present_count": all_score_present,
        "date_min": min(date_values) if date_values else None,
        "date_max": max(date_values) if date_values else None,
        "competition_family_overlap_with_prior_exclusions": family_overlap,
        "prior_named_family_overlap": prior_named_overlap,
        "gate": gate,
        "files": file_reports,
        "label_boundary": {
            "numeric_score_values_captured": False,
            "numeric_score_values_converted": False,
            "numeric_score_values_stored": False,
            "numeric_score_values_hashed": False,
            "goal_totals_computed": False,
            "goal_difference_computed": False,
            "tail_membership_computed": False,
            "model_fit": False,
            "candidate_or_baseline_loss_computed": False,
            "only_score_token_presence_inspected": True,
        },
        "protected_boundaries": {
            "C076D_fresh_score_values_opened": False,
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "unified_matrix_generated": False,
            "formal_weight": 0,
            "CURRENT_change": False,
        },
        "next_if_pass": "Freeze the exact identity manifest SHA and one-shot confirmation execution spec, then and only then open numeric scores once. Realized T>=7 coverage remains unknown until that label-open step.",
    }

    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "identity_manifest.jsonl").open("w", encoding="utf-8") as fh:
        for r in sorted(accepted, key=lambda x: (x["date"], x["competition_family"], x["home_norm"], x["away_norm"], x["source_file"])):
            fh.write(json.dumps({
                "date": r["date"],
                "competition_family": r["competition_family"],
                "home": r["home"],
                "away": r["away"],
                "source_file": r["source_file"],
            }, ensure_ascii=False) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
