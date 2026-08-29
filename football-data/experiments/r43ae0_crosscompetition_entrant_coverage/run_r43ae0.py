#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ae0.json"
R9 = HERE.parent / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
CAT_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/league_catalogue.parquet?download=true"
CURRENT_COMP_LOOKBACK_DAYS = 270
OTHER_LEAGUE_LOOKBACK_DAYS = 365
THRESHOLDS = (1, 3, 5)


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ae0/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def load_catalogue() -> dict[str, dict]:
    tmp = HERE / "league_catalogue.parquet"
    download(CAT_URL, tmp)
    df = pd.read_parquet(tmp, columns=["dataset_league_id", "af_name", "af_country", "af_type", "history_status"])
    df = df[df["dataset_league_id"].notna()].copy()
    df["competition_id"] = df["dataset_league_id"].astype("int64").astype(str)
    df = df.drop_duplicates("competition_id")
    meta = df.set_index("competition_id").to_dict("index")
    tmp.unlink(missing_ok=True)
    return meta


def split_boundaries(rows: list[dict]) -> tuple[int, int, int]:
    def boundary(target: int) -> int:
        i = min(max(1, target), len(rows) - 1)
        while i < len(rows) and rows[i]["date"] == rows[i - 1]["date"]:
            i += 1
        return i
    b1 = boundary(4000)
    b2 = boundary(b1 + 8000)
    b3 = boundary(b2 + 4000)
    return b1, b2, b3


def run() -> dict:
    if not R9.exists():
        raise RuntimeError(f"missing R9 snapshot: {R9}")
    # Zero-label audit: deliberately exclude scores/xG/result columns.
    df = pd.read_csv(R9, usecols=["date", "game_id", "competition_id", "home_team", "away_team"], dtype=str)
    if len(df) != 20000:
        raise RuntimeError(f"expected 20000 R9 rows, got {len(df)}")
    rows = df.to_dict("records")
    rows.sort(key=lambda r: (r["date"], r["game_id"]))
    meta = load_catalogue()
    missing = sorted({r["competition_id"] for r in rows if r["competition_id"] not in meta})
    if missing:
        raise RuntimeError(f"unmapped competitions: {missing}")

    b1, b2, b3 = split_boundaries(rows)
    split_of_index = {}
    for i in range(len(rows)):
        split_of_index[i] = "burn" if i < b1 else "train" if i < b2 else "validation" if i < b3 else "historical_test"

    # team_history stores only earlier-date membership observations.
    hist: dict[str, deque] = defaultdict(deque)
    by_day: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_day[r["date"]].append((i, r))

    stats = {str(k): {"target_league_rows": 0, "affected_match_rows": 0, "home_entrant_rows": 0, "away_entrant_rows": 0, "both_entrant_rows": 0, "entrant_team_sides": 0, "same_country_entrant_sides": 0, "cross_country_entrant_sides": 0, "split": {s: {"target_league_rows": 0, "affected_match_rows": 0, "entrant_team_sides": 0} for s in ("burn", "train", "validation", "historical_test")}} for k in THRESHOLDS}
    transition_examples = {str(k): [] for k in THRESHOLDS}
    league_target_rows = 0
    prior_other_league_side_counts = Counter()

    for ds in sorted(by_day):
        d = date.fromisoformat(ds)
        pending_updates = []
        for idx, r in sorted(by_day[ds], key=lambda x: x[1]["game_id"]):
            cid = r["competition_id"]
            if str(meta[cid].get("af_type")) != "League":
                # Cup rows may still update generic membership history after all predictions that date.
                pending_updates.append((r["home_team"], cid, d))
                pending_updates.append((r["away_team"], cid, d))
                continue
            league_target_rows += 1
            split = split_of_index[idx]
            side_info = {}
            for side, team in (("home", r["home_team"]), ("away", r["away_team"])):
                dq = hist[team]
                # Retain a little beyond max lookback to keep bounded memory.
                while dq and (d - dq[0][0]).days > OTHER_LEAGUE_LOOKBACK_DAYS:
                    dq.popleft()
                current_recent = [x for x in dq if x[1] == cid and (d - x[0]).days <= CURRENT_COMP_LOOKBACK_DAYS]
                other_league = [x for x in dq if x[1] != cid and (d - x[0]).days <= OTHER_LEAGUE_LOOKBACK_DAYS and str(meta.get(x[1], {}).get("af_type")) == "League"]
                counts = Counter(x[1] for x in other_league)
                prev_cid, prev_n = (counts.most_common(1)[0] if counts else (None, 0))
                prior_other_league_side_counts[min(prev_n, 20)] += 1
                prev_country = meta.get(prev_cid, {}).get("af_country") if prev_cid else None
                curr_country = meta[cid].get("af_country")
                side_info[side] = {
                    "team": team,
                    "no_recent_current_comp": len(current_recent) == 0,
                    "prior_other_league_matches": int(prev_n),
                    "previous_competition_id": prev_cid,
                    "previous_competition_country": prev_country,
                    "current_competition_country": curr_country,
                    "same_country": bool(prev_cid is not None and prev_country is not None and curr_country is not None and str(prev_country) == str(curr_country)),
                }

            for k in THRESHOLDS:
                key = str(k)
                st = stats[key]
                st["target_league_rows"] += 1
                st["split"][split]["target_league_rows"] += 1
                h = side_info["home"]["no_recent_current_comp"] and side_info["home"]["prior_other_league_matches"] >= k
                a = side_info["away"]["no_recent_current_comp"] and side_info["away"]["prior_other_league_matches"] >= k
                if h or a:
                    st["affected_match_rows"] += 1
                    st["split"][split]["affected_match_rows"] += 1
                if h:
                    st["home_entrant_rows"] += 1
                if a:
                    st["away_entrant_rows"] += 1
                if h and a:
                    st["both_entrant_rows"] += 1
                for side, flag in (("home", h), ("away", a)):
                    if flag:
                        st["entrant_team_sides"] += 1
                        st["split"][split]["entrant_team_sides"] += 1
                        if side_info[side]["same_country"]:
                            st["same_country_entrant_sides"] += 1
                        else:
                            st["cross_country_entrant_sides"] += 1
                        if len(transition_examples[key]) < 20:
                            transition_examples[key].append({"date": ds, "split": split, "side": side, "team": side_info[side]["team"], "current_competition_id": cid, "current_competition_name": meta[cid].get("af_name"), "previous_competition_id": side_info[side]["previous_competition_id"], "previous_competition_name": meta.get(side_info[side]["previous_competition_id"], {}).get("af_name") if side_info[side]["previous_competition_id"] else None, "prior_other_league_matches": side_info[side]["prior_other_league_matches"], "same_country": side_info[side]["same_country"]})

            pending_updates.append((r["home_team"], cid, d))
            pending_updates.append((r["away_team"], cid, d))

        # Same-date membership is not visible to any prediction on that date.
        for team, cid, d in pending_updates:
            hist[team].append((d, cid))

    for k in THRESHOLDS:
        st = stats[str(k)]
        denom = st["target_league_rows"]
        st["affected_match_rate"] = st["affected_match_rows"] / denom if denom else 0.0
        for split, q in st["split"].items():
            q["affected_match_rate"] = q["affected_match_rows"] / q["target_league_rows"] if q["target_league_rows"] else 0.0

    out = {
        "schema_version": "football3-r43ae0-crosscompetition-entrant-coverage-v1",
        "status": "COMPLETE",
        "classification": "ZERO_MODEL_ZERO_LABEL_STRICT_PRIOR_MEMBERSHIP_TRANSITION_COVERAGE_AUDIT",
        "formal_weight": 0,
        "governance": {
            "model_fits": 0,
            "candidate_probabilities": 0,
            "result_columns_read": False,
            "xg_columns_read": False,
            "same_date_membership_updates_withheld": True,
            "future_membership_or_schedule_used": False,
            "promotion_or_relegation_claimed": False,
            "promotion_allowed": False,
        },
        "definition": {
            "name": "cross_competition_entrant_state",
            "target_type": "League only",
            "current_competition_lookback_days": CURRENT_COMP_LOOKBACK_DAYS,
            "other_league_lookback_days": OTHER_LEAGUE_LOOKBACK_DAYS,
            "thresholds_audited_without_labels": list(THRESHOLDS),
            "rule": "At current date, a team is an entrant at threshold k if it has zero earlier-date matches in the current League within 270 days and at least k earlier-date matches in one other League within 365 days. This is a membership transition signal, not a proven promotion/relegation label.",
        },
        "source": {"r9_snapshot_rows": len(rows), "league_catalogue_url": CAT_URL, "league_target_rows": league_target_rows},
        "split_boundaries": {"burn_end": b1, "train_end": b2, "validation_end": b3, "historical_test_n": len(rows) - b3},
        "coverage_by_prior_other_league_threshold": stats,
        "prior_other_league_match_count_histogram_capped20": {str(k): int(v) for k, v in sorted(prior_other_league_side_counts.items())},
        "transition_examples": transition_examples,
        "next": "Proceed to one predeclared formal_weight=0 K1 incremental entrant-state screen only if threshold-5 still yields substantial affected validation and historical-test rows; otherwise close without fitting.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "league_target_rows": league_target_rows, "coverage": stats}, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["source"]["r9_snapshot_rows"] == 20000
    assert s["governance"]["model_fits"] == 0
    assert s["governance"]["result_columns_read"] is False
    assert s["governance"]["same_date_membership_updates_withheld"] is True
    assert s["governance"]["promotion_or_relegation_claimed"] is False
    assert s["definition"]["thresholds_audited_without_labels"] == [1, 3, 5]
    print("R43AE0 cross-competition entrant coverage audit verified")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
