#!/usr/bin/env python3
import hashlib
import json
import os
import statistics
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

SOURCE_REPO = "hudl/open-data"
SOURCE_PIN = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW_ROOT = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_PIN}"
EXCLUDED_COMPETITION_IDS = {37}  # R44L4 WSL 已开标签消费
MIN_SEASONS = 4
MIN_TOTAL_IDENTITIES = 720
MIN_LARGE_SEASONS = 4
MIN_IDENTITIES_PER_LARGE_SEASON = 150
MIN_MEDIAN_TEAM_SEASON_MATCHES = 18
MIN_P10_TEAM_SEASON_MATCHES = 10


def fetch_bytes(path: str) -> bytes:
    req = urllib.request.Request(
        f"{RAW_ROOT}/{path}",
        headers={"User-Agent": "football-r44l5-zero-label-screen/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_json(path: str):
    raw = fetch_bytes(path)
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def pct(values, q: float):
    if not values:
        return 0.0
    xs = sorted(values)
    idx = max(0, min(len(xs) - 1, int((len(xs) - 1) * q)))
    return float(xs[idx])


def main():
    out = Path(os.environ.get("R44L5_OUTPUT", "r44l5_output"))
    out.mkdir(parents=True, exist_ok=True)

    competitions, competitions_sha = fetch_json("data/competitions.json")
    rows_by_comp = defaultdict(list)
    for row in competitions:
        rows_by_comp[int(row["competition_id"])].append({
            "competition_id": int(row["competition_id"]),
            "season_id": int(row["season_id"]),
            "country_name": row.get("country_name"),
            "competition_name": row.get("competition_name"),
            "competition_gender": row.get("competition_gender"),
            "competition_international": row.get("competition_international"),
            "season_name": row.get("season_name"),
        })

    domains = []
    source_manifest = []
    for competition_id, season_rows in sorted(rows_by_comp.items()):
        season_summaries = []
        all_team_season_counts = []
        all_match_ids = []
        identity_error = None
        for season_row in sorted(season_rows, key=lambda r: (str(r["season_name"]), r["season_id"])):
            season_id = season_row["season_id"]
            rel = f"data/matches/{competition_id}/{season_id}.json"
            try:
                matches, file_sha = fetch_json(rel)
            except Exception as exc:
                identity_error = f"{type(exc).__name__}:{exc}"
                break
            source_manifest.append({"path": rel, "sha256": file_sha})
            team_counts = defaultdict(int)
            ids = []
            dates = []
            for m in matches:
                mid = int(m["match_id"])
                ids.append(mid)
                dates.append(str(m["match_date"]))
                home = m["home_team"]
                away = m["away_team"]
                team_counts[int(home["home_team_id"])] += 1
                team_counts[int(away["away_team_id"])] += 1
                comp_obj = m["competition"]
                season_obj = m["season"]
                if int(comp_obj["competition_id"]) != competition_id or int(season_obj["season_id"]) != season_id:
                    identity_error = f"identity_mismatch:{competition_id}:{season_id}:{mid}"
                    break
            if identity_error:
                break
            all_match_ids.extend(ids)
            team_values = list(team_counts.values())
            all_team_season_counts.extend(team_values)
            season_summaries.append({
                "season_id": season_id,
                "season_name": season_row["season_name"],
                "identity_count": len(ids),
                "unique_teams": len(team_counts),
                "median_matches_per_team": float(statistics.median(team_values)) if team_values else 0.0,
                "p10_matches_per_team": pct(team_values, 0.10),
                "first_match_date": min(dates) if dates else None,
                "last_match_date": max(dates) if dates else None,
                "source_sha256": file_sha,
            })

        total = sum(x["identity_count"] for x in season_summaries)
        large_seasons = [x for x in season_summaries if x["identity_count"] >= MIN_IDENTITIES_PER_LARGE_SEASON]
        global_unique = len(set(all_match_ids)) == len(all_match_ids)
        median_team = float(statistics.median(all_team_season_counts)) if all_team_season_counts else 0.0
        p10_team = pct(all_team_season_counts, 0.10)
        passed = (
            competition_id not in EXCLUDED_COMPETITION_IDS
            and identity_error is None
            and global_unique
            and len(season_summaries) >= MIN_SEASONS
            and total >= MIN_TOTAL_IDENTITIES
            and len(large_seasons) >= MIN_LARGE_SEASONS
            and median_team >= MIN_MEDIAN_TEAM_SEASON_MATCHES
            and p10_team >= MIN_P10_TEAM_SEASON_MATCHES
        )
        first = season_rows[0]
        domains.append({
            "competition_id": competition_id,
            "competition_name": first["competition_name"],
            "country_name": first["country_name"],
            "competition_gender": first["competition_gender"],
            "competition_international": first["competition_international"],
            "excluded_consumed_domain": competition_id in EXCLUDED_COMPETITION_IDS,
            "identity_error": identity_error,
            "global_match_id_unique": global_unique,
            "season_count": len(season_summaries),
            "total_identity_count": total,
            "large_season_count": len(large_seasons),
            "median_team_season_matches": median_team,
            "p10_team_season_matches": p10_team,
            "passes_stage_a_scale_density_gate": passed,
            "seasons": season_summaries,
        })

    domains.sort(key=lambda x: (-x["total_identity_count"], -x["season_count"], x["competition_id"]))
    candidates = [d for d in domains if d["passes_stage_a_scale_density_gate"]]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    terminal = "PASS_R44L5_STAGE_A_CANDIDATES_FOUND" if candidates else "STOP_R44L5_NO_EXTERNAL_DOMAIN_MEETS_SCALE_DENSITY_GATE"
    receipt = {
        "schema_version": "V520-R44L5-ZERO-LABEL-DOMAIN-SCREEN-1.0",
        "terminal": terminal,
        "research_only": True,
        "formal_weight": 0,
        "current_rule_family": "V5.2.0",
        "source_repo": SOURCE_REPO,
        "source_pin": SOURCE_PIN,
        "competitions_sha256": competitions_sha,
        "gate": {
            "min_seasons": MIN_SEASONS,
            "min_total_identities": MIN_TOTAL_IDENTITIES,
            "min_large_seasons": MIN_LARGE_SEASONS,
            "min_identities_per_large_season": MIN_IDENTITIES_PER_LARGE_SEASON,
            "min_median_team_season_matches": MIN_MEDIAN_TEAM_SEASON_MATCHES,
            "min_p10_team_season_matches": MIN_P10_TEAM_SEASON_MATCHES,
            "excluded_competition_ids": sorted(EXCLUDED_COMPETITION_IDS),
        },
        "domain_count": len(domains),
        "candidate_count": len(candidates),
        "candidate_competition_ids": [d["competition_id"] for d in candidates],
        "hard_boundary_receipt": {
            "target_labels_accessed": 0,
            "settlement_results_accessed": 0,
            "model_fits": 0,
            "candidate_probabilities": 0,
            "fixed_sample_consumed": 0,
            "formal_model_changes": 0,
            "formal_data_changes": 0,
            "formal_config_changes": 0,
            "CURRENT_changes": 0,
        },
        "run_identity": {
            "checked_out_head_sha": head,
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        },
    }

    (out / "domains_r44l5.json").write_text(json.dumps(domains, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "source_manifest_r44l5.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt_raw = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    (out / "receipt_r44l5.json").write_bytes(receipt_raw)
    (out / "receipt_r44l5.sha256").write_text(hashlib.sha256(receipt_raw).hexdigest() + "\n", encoding="ascii")

    lines = [
        "# R44L5 零标签外部赛事域 Stage-A 筛选",
        "",
        f"- terminal: `{terminal}`",
        f"- source: `{SOURCE_REPO}@{SOURCE_PIN}`",
        f"- domains scanned: {len(domains)}",
        f"- candidates: {len(candidates)}",
        "- labels/results/model fits: `0/0/0`",
        "",
        "## Top domains by identity count",
        "",
        "|competition_id|competition|seasons|identities|large seasons|median team-season|p10 team-season|gate|",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for d in domains[:20]:
        lines.append(
            f"|{d['competition_id']}|{d['competition_name']}|{d['season_count']}|{d['total_identity_count']}|"
            f"{d['large_season_count']}|{d['median_team_season_matches']:.1f}|{d['p10_team_season_matches']:.1f}|"
            f"{'PASS' if d['passes_stage_a_scale_density_gate'] else 'FAIL'}|"
        )
    (out / "report_r44l5.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"terminal": terminal, "candidate_count": len(candidates), "candidate_ids": receipt["candidate_competition_ids"]}, sort_keys=True))


if __name__ == "__main__":
    main()
