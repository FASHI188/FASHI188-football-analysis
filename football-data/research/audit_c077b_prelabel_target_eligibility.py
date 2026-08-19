#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import audit_c077b_zero_label_source_gate as source_gate

SOURCE_CONTRACT = Path("football-data/research/c077b_candidate_source_freeze_v1.json")
ELIG_CONTRACT = Path("football-data/research/c077b_prelabel_target_eligibility_contract.json")
EXPECTED_SOURCE_COUNT = 9376
EXPECTED_SOURCE_SHA = "f1e70a3f783be235136060a117645d3a1b42b400dfcef7b4be19d8f11233b8b2"


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def sha_lines(lines: list[str]) -> str:
    payload = "\n".join(sorted(lines)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(args.source_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    src = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))
    ec = json.loads(ELIG_CONTRACT.read_text(encoding="utf-8"))
    pinned = src["candidate_repo_commit"]
    actual = git("rev-parse", "HEAD", cwd=root)
    if actual != pinned:
        raise RuntimeError(f"source drift: {actual} != {pinned}")

    allowed_years = set(src["source_gate"]["accepted_fixture_calendar_years"])
    rows = []
    for rel in src["frozen_candidate_files"]:
        parsed, _ = source_gate.parse_file(root / rel, rel)
        rows.extend(r for r in parsed if r["calendar_year"] in allowed_years)

    source_keys = [f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in rows]
    source_sha = sha_lines(source_keys)
    if len(rows) != EXPECTED_SOURCE_COUNT or source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError(
            f"source identity drift count={len(rows)} sha={source_sha}; "
            f"expected {EXPECTED_SOURCE_COUNT}/{EXPECTED_SOURCE_SHA}"
        )

    min_home = int(ec["eligibility"]["minimum_prior_completed_identity_count_home"])
    min_away = int(ec["eligibility"]["minimum_prior_completed_identity_count_away"])
    if min_home != 8 or min_away != 8:
        raise RuntimeError("frozen threshold drift")

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    history = Counter()
    eligible = []
    all_audit = []
    for d in sorted(by_date):
        day = sorted(
            by_date[d],
            key=lambda r: (r["competition_family"], r["home_norm"], r["away_norm"], r["source_file"]),
        )
        # All eligibility decisions are made before any same-date history update.
        for r in day:
            hk = f"{r['competition_family']}|{r['home_norm']}"
            ak = f"{r['competition_family']}|{r['away_norm']}"
            hn = int(history[hk])
            an = int(history[ak])
            ok = hn >= min_home and an >= min_away
            rec = {
                "date": r["date"],
                "calendar_year": r["calendar_year"],
                "competition_family": r["competition_family"],
                "home": r["home"],
                "away": r["away"],
                "source_file": r["source_file"],
                "home_prior_completed_identity_n": hn,
                "away_prior_completed_identity_n": an,
                "eligible": bool(ok),
                "home_norm": r["home_norm"],
                "away_norm": r["away_norm"],
            }
            all_audit.append(rec)
            if ok:
                eligible.append(rec)
        for r in day:
            history[f"{r['competition_family']}|{r['home_norm']}"] += 1
            history[f"{r['competition_family']}|{r['away_norm']}"] += 1

    eligible_keys = [f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in eligible]
    eligible_sha = sha_lines(eligible_keys)
    by_year = Counter(int(r["calendar_year"]) for r in eligible)
    by_family = Counter(r["competition_family"] for r in eligible)
    duplicate_count = len(eligible_keys) - len(set(eligible_keys))

    summary = {
        "schema_version": "C077B_PRELABEL_TARGET_ELIGIBILITY_AUDIT_V1",
        "status": "PASS_PRELABEL_ELIGIBILITY_AUDIT",
        "source": {"repository": src["candidate_repo"], "commit": actual},
        "source_identity_count": len(rows),
        "source_identity_sha256": source_sha,
        "eligibility_rule": {
            "minimum_prior_home": min_home,
            "minimum_prior_away": min_away,
            "team_key": "competition_family + normalized team name",
            "strict_earlier_calendar_date_only": True,
            "same_day_predict_before_update": True,
        },
        "eligible_identity_count": len(eligible),
        "eligible_identity_sha256": eligible_sha,
        "eligible_duplicate_identity_count": duplicate_count,
        "eligible_fraction": len(eligible) / len(rows) if rows else 0.0,
        "eligible_time_block_counts": {
            "2024_early": int(by_year.get(2024, 0)),
            "2025_late": int(by_year.get(2025, 0)),
        },
        "eligible_competition_family_count": len(by_family),
        "eligible_competition_family_counts": dict(sorted(by_family.items())),
        "label_boundary": {
            "numeric_score_values_captured": False,
            "numeric_score_values_converted": False,
            "numeric_score_values_stored": False,
            "numeric_score_values_hashed": False,
            "goal_totals_computed": False,
            "goal_difference_computed": False,
            "tail_membership_computed": False,
            "model_fit_on_confirmation": False,
            "confirmation_loss_computed": False,
        },
        "scientific_confirmation_coverage_not_yet_known": {
            "realized_T_ge_7_count": None,
            "realized_T_ge_7_2024": None,
            "realized_T_ge_7_2025": None,
            "realized_T_ge_7_domains": None,
        },
    }
    if duplicate_count != 0:
        summary["status"] = "FAIL_PRELABEL_ELIGIBILITY_AUDIT"

    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "eligible_identity_manifest.jsonl").open("w", encoding="utf-8") as fh:
        for r in sorted(eligible, key=lambda x: (x["date"], x["competition_family"], x["home_norm"], x["away_norm"], x["source_file"])):
            fh.write(json.dumps({
                "date": r["date"],
                "competition_family": r["competition_family"],
                "home": r["home"],
                "away": r["away"],
                "source_file": r["source_file"],
                "home_prior_completed_identity_n": r["home_prior_completed_identity_n"],
                "away_prior_completed_identity_n": r["away_prior_completed_identity_n"],
            }, ensure_ascii=False) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS_PRELABEL_ELIGIBILITY_AUDIT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
