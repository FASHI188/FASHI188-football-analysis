#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ac0.json"
R9 = HERE.parent / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
CAT_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/league_catalogue.parquet?download=true"
CYCLE_GAP_DAYS = 75


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ac0/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def run() -> dict:
    if not R9.exists():
        raise RuntimeError(f"missing R9 snapshot {R9}")
    # Deliberately do not read score/xG/result columns in this zero-label audit.
    rows = pd.read_csv(R9, usecols=["date", "game_id", "competition_id", "home_team", "away_team"], dtype=str)
    if len(rows) != 20000:
        raise RuntimeError(f"expected 20000 R9 rows, got {len(rows)}")
    rows["d"] = pd.to_datetime(rows["date"]).dt.date

    tmp = HERE / "league_catalogue.parquet"
    download(CAT_URL, tmp)
    cat = pd.read_parquet(tmp, columns=["dataset_league_id", "af_name", "af_country", "af_type", "history_status"])
    cat = cat[cat["dataset_league_id"].notna()].copy()
    cat["competition_id"] = cat["dataset_league_id"].astype("int64").astype(str)
    cat = cat.drop_duplicates("competition_id")
    meta = cat.set_index("competition_id").to_dict("index")
    tmp.unlink(missing_ok=True)

    type_rows = Counter()
    mapped_rows = 0
    for cid in rows["competition_id"]:
        m = meta.get(str(cid))
        if m is None:
            type_rows["UNMAPPED"] += 1
        else:
            mapped_rows += 1
            type_rows[str(m.get("af_type") or "UNKNOWN")] += 1

    by_day = defaultdict(list)
    for r in rows.itertuples(index=False):
        by_day[r.d].append(r)

    last_comp_date: dict[str, date] = {}
    team_games = defaultdict(int)
    cycle_id = defaultdict(int)
    reset_events = []
    league_rows = mature1 = mature3 = mature5 = 0
    active_team_counts = []
    seen_teams = defaultdict(set)
    comp_cycle_rows = Counter()

    for d in sorted(by_day):
        # Detect cycle resets from information available at the current fixture date only.
        comps_today = sorted({str(r.competition_id) for r in by_day[d]})
        for cid in comps_today:
            m = meta.get(cid)
            if not m or str(m.get("af_type")) != "League":
                continue
            prev = last_comp_date.get(cid)
            if prev is not None:
                gap = (d - prev).days
                if gap >= CYCLE_GAP_DAYS:
                    old_cycle = cycle_id[cid]
                    reset_events.append({"competition_id": cid, "date": d.isoformat(), "prior_date": prev.isoformat(), "gap_days": gap, "new_cycle": old_cycle + 1})
                    cycle_id[cid] += 1
                    for key in [k for k in list(team_games.keys()) if k[0] == cid]:
                        del team_games[key]
                    seen_teams[(cid, cycle_id[cid])] = set()

        pending = []
        for r in sorted(by_day[d], key=lambda z: z.game_id):
            cid = str(r.competition_id)
            m = meta.get(cid)
            if not m or str(m.get("af_type")) != "League":
                continue
            league_rows += 1
            cyc = cycle_id[cid]
            hg = team_games[(cid, str(r.home_team))]
            ag = team_games[(cid, str(r.away_team))]
            mature1 += int(hg >= 1 and ag >= 1)
            mature3 += int(hg >= 3 and ag >= 3)
            mature5 += int(hg >= 5 and ag >= 5)
            active_team_counts.append(len(seen_teams[(cid, cyc)]))
            comp_cycle_rows[(cid, cyc)] += 1
            pending.append((cid, str(r.home_team), str(r.away_team), cyc))
        for cid, h, a, cyc in pending:
            team_games[(cid, h)] += 1
            team_games[(cid, a)] += 1
            seen_teams[(cid, cyc)].update([h, a])
        for cid in comps_today:
            m = meta.get(cid)
            if m and str(m.get("af_type")) == "League":
                last_comp_date[cid] = d

    league_comp_ids = sorted({str(c) for c in rows["competition_id"] if meta.get(str(c), {}).get("af_type") == "League"})
    reset_by_comp = Counter(x["competition_id"] for x in reset_events)
    cycle_sizes = list(comp_cycle_rows.values())

    out = {
        "schema_version": "football3-r43ac0-table-state-coverage-audit-v1",
        "status": "COMPLETE",
        "classification": "ZERO_MODEL_ZERO_LABEL_CAUSAL_TABLE_STATE_COVERAGE_AUDIT",
        "formal_weight": 0,
        "governance": {
            "model_fits": 0,
            "candidate_probabilities": 0,
            "result_columns_read": False,
            "xg_columns_read": False,
            "future_dates_used_to_detect_cycle_boundary": False,
            "cycle_reset_rule_predeclared_days": CYCLE_GAP_DAYS,
            "promotion_allowed": False,
        },
        "source": {"r9_snapshot_rows": 20000, "league_catalogue_url": CAT_URL},
        "mapping": {
            "mapped_rows": mapped_rows,
            "mapped_rate": mapped_rows / 20000.0,
            "rows_by_af_type": dict(type_rows),
            "league_competition_count": len(league_comp_ids),
        },
        "causal_cycle_rule": f"For af_type=League only, reset that competition's table state when current match date minus its previously observed match date is >= {CYCLE_GAP_DAYS} days. No future schedule/date is consulted.",
        "coverage": {
            "league_rows": league_rows,
            "league_row_rate": league_rows / 20000.0,
            "both_teams_prior_cycle_games_ge1": mature1,
            "both_teams_prior_cycle_games_ge1_rate_within_league": mature1 / league_rows if league_rows else 0.0,
            "both_teams_prior_cycle_games_ge3": mature3,
            "both_teams_prior_cycle_games_ge3_rate_within_league": mature3 / league_rows if league_rows else 0.0,
            "both_teams_prior_cycle_games_ge5": mature5,
            "both_teams_prior_cycle_games_ge5_rate_within_league": mature5 / league_rows if league_rows else 0.0,
            "median_active_teams_before_match": float(pd.Series(active_team_counts).median()) if active_team_counts else 0.0,
        },
        "cycle_diagnostics": {
            "reset_event_count": len(reset_events),
            "competitions_with_reset": len(reset_by_comp),
            "reset_count_by_competition": dict(reset_by_comp),
            "min_reset_gap_days": min((x["gap_days"] for x in reset_events), default=None),
            "max_reset_gap_days": max((x["gap_days"] for x in reset_events), default=None),
            "competition_cycle_count": len(comp_cycle_rows),
            "median_rows_per_competition_cycle": float(pd.Series(cycle_sizes).median()) if cycle_sizes else 0.0,
            "reset_examples": reset_events[:20],
        },
        "next": "Proceed to a formal_weight=0 K1 incremental dynamic-table-state screen only if league mapping and mature-cycle coverage are substantial; otherwise close without fitting.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "mapping": out["mapping"], "coverage": out["coverage"], "cycle_diagnostics": out["cycle_diagnostics"]}, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["source"]["r9_snapshot_rows"] == 20000
    assert s["governance"]["model_fits"] == 0
    assert s["governance"]["result_columns_read"] is False
    assert s["governance"]["future_dates_used_to_detect_cycle_boundary"] is False
    assert s["governance"]["cycle_reset_rule_predeclared_days"] == 75
    print("R43AC0 table-state coverage audit verified")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
