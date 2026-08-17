#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path

from dateutil import parser

OUT = Path("football-data/research/b05_eladsil_timeseries_direct_t_r1/freeze")
ZIP = Path("/tmp/elad/source.zip")
ODDS_SHA = "bfa14f07a11581ffd793b1f44d3e7c9203c631d26ac1c692fe71a77f66579298"
EXPECTED_BATCH_SHA = "094edba4ae1e4955ef174f790011a4f49dc7013d3c51a842f64f70a7590f2f4e"
SEED = "ELADSIL-PIT6H-RESERVE-20260817-R1"
PREREG = Path("football-data/research/b05_eladsil_timeseries_direct_t_r1/PREREG.md")


def parse_dt(s):
    try:
        return parser.parse(str(s))
    except Exception:
        return None


def devig(vals):
    inv = [1.0 / v for v in vals]
    z = sum(inv)
    return [v / z for v in inv]


def entropy(p):
    return -sum(v * math.log(max(v, 1e-15)) for v in p)


def slope(xs, ys):
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        raise RuntimeError("ZERO_TIME_VARIANCE")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source_zip_sha = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        odds_members = [n for n in names if n.endswith("Matches_Odds.csv")]
        if len(odds_members) != 1:
            raise RuntimeError(f"ODDS_MEMBER_COUNT:{odds_members}")
        odds_bytes = z.read(odds_members[0])
        if hashlib.sha256(odds_bytes).hexdigest() != ODDS_SHA:
            raise RuntimeError("ODDS_SHA_MISMATCH")
        Path("/tmp/elad/Matches_Odds.csv").write_bytes(odds_bytes)

        # Header-only inspection. For every non-odds CSV, exactly one header line is read.
        headers = []
        for name in names:
            if not name.lower().endswith(".csv"):
                continue
            with z.open(name) as fh:
                line = fh.readline().decode("utf-8-sig", errors="replace").rstrip("\r\n")
            headers.append({"member": name, "header": next(csv.reader([line])) if line else []})

    (OUT / "source_headers.json").write_text(
        json.dumps(
            {
                "schema_version": "B05-HEADER-ONLY-R1",
                "source_zip_sha256": source_zip_sha,
                "headers": headers,
                "non_odds_data_rows_read": 0,
                "outcome_values_dereferenced": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    reserve_state = {}
    trajectory_rows = defaultdict(list)
    raw_rows = 0
    valid_rows = 0
    with Path("/tmp/elad/Matches_Odds.csv").open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        need = {
            "match_id",
            "date_start",
            "competition_name",
            "date_created",
            "home_team_name",
            "away_team_name",
            "home_team_odd",
            "away_team_odd",
            "tie_odd",
        }
        if not need.issubset(r.fieldnames or []):
            raise RuntimeError("ODDS_HEADER_MISMATCH")
        for x in r:
            raw_rows += 1
            try:
                mid = str(x["match_id"]).strip()
                ks = parse_dt(x["date_start"])
                obs = parse_dt(x["date_created"])
                vals = [float(x["home_team_odd"]), float(x["tie_odd"]), float(x["away_team_odd"])]
                if not mid or ks is None or obs is None or not all(math.isfinite(v) and v > 1 for v in vals):
                    raise ValueError
            except Exception:
                continue
            valid_rows += 1
            lead = (ks - obs).total_seconds() / 3600.0
            z = reserve_state.setdefault(
                mid,
                {
                    "match_id": mid,
                    "kickoff": ks,
                    "competition": x["competition_name"],
                    "home": x["home_team_name"],
                    "away": x["away_team_name"],
                    "pit6h_obs": set(),
                },
            )
            if lead >= 6:
                z["pit6h_obs"].add(obs)
                ph, pd, pa = devig(vals)
                trajectory_rows[mid].append((obs, ks, ph, pd, pa))

    reserve = []
    for z in reserve_state.values():
        ss = sorted(z["pit6h_obs"])
        if len(ss) < 2:
            continue
        reserve.append(
            {
                "match_id": z["match_id"],
                "competition": z["competition"],
                "home": z["home"],
                "away": z["away"],
                "kickoff": z["kickoff"].isoformat(),
                "distinct_pit_timestamps": len(ss),
                "first_quote_hours_before": (z["kickoff"] - ss[0]).total_seconds() / 3600.0,
                "last_quote_hours_before": (z["kickoff"] - ss[-1]).total_seconds() / 3600.0,
            }
        )
    reserve = sorted(reserve, key=lambda m: hashlib.sha256(f"{SEED}|{m['match_id']}".encode()).hexdigest())
    b = reserve[:400]
    if len(b) != 400:
        raise RuntimeError(f"B05_SIZE:{len(b)}")

    payload = {
        "schema_version": "ELADSIL-PIT6H-BATCH-R1",
        "batch_id": "ELAD-PIT6H-B001",
        "status": "SEALED_UNOPENED",
        "batch_size": len(b),
        "source_odds_sha256": ODDS_SHA,
        "pit_gate": "Matches_Odds only; >=2 valid full-1X2 timestamps each >=6h before date_start",
        "selection_uses_outcomes": False,
        "outcome_values_dereferenced": 0,
        "matches": b,
    }
    batch_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    batch_sha = hashlib.sha256(batch_text.encode("utf-8")).hexdigest()
    if batch_sha != EXPECTED_BATCH_SHA:
        raise RuntimeError(f"B05_MANIFEST_SHA_MISMATCH:{batch_sha}")
    (OUT / "B05_manifest.json").write_text(batch_text, encoding="utf-8")

    features = []
    for m in b:
        mid = m["match_id"]
        by_t = defaultdict(list)
        for obs, ks, ph, pd, pa in trajectory_rows[mid]:
            by_t[obs].append((ph, pd, pa, ks))
        seq = []
        for obs in sorted(by_t):
            rr = by_t[obs]
            ph = statistics.median(v[0] for v in rr)
            pd = statistics.median(v[1] for v in rr)
            pa = statistics.median(v[2] for v in rr)
            zz = ph + pd + pa
            ph, pd, pa = ph / zz, pd / zz, pa / zz
            seq.append((obs, rr[0][3], ph, pd, pa))
        if len(seq) < 2:
            raise RuntimeError(f"B05_LT2:{mid}")
        first, last = seq[0], seq[-1]
        vals_h = [q[2] for q in seq]
        vals_d = [q[3] for q in seq]
        vals_a = [q[4] for q in seq]
        lhd = [math.log(q[2] / q[3]) for q in seq]
        lad = [math.log(q[4] / q[3]) for q in seq]
        xs = [(q[0] - first[0]).total_seconds() / 3600.0 for q in seq]
        span = xs[-1]
        if span <= 0:
            raise RuntimeError(f"B05_NONPOSITIVE_SPAN:{mid}")
        rec = {
            "match_id": mid,
            "kickoff": m["kickoff"],
            "competition": m["competition"],
            "home": m["home"],
            "away": m["away"],
            "last_log_H_over_D": lhd[-1],
            "last_log_A_over_D": lad[-1],
            "last_pD": vals_d[-1],
            "last_entropy": entropy([vals_h[-1], vals_d[-1], vals_a[-1]]),
            "last_quote_hours_before_kickoff": (last[1] - last[0]).total_seconds() / 3600.0,
            "first_log_H_over_D": lhd[0],
            "first_log_A_over_D": lad[0],
            "delta_log_H_over_D": lhd[-1] - lhd[0],
            "delta_log_A_over_D": lad[-1] - lad[0],
            "range_pH": max(vals_h) - min(vals_h),
            "range_pD": max(vals_d) - min(vals_d),
            "range_pA": max(vals_a) - min(vals_a),
            "std_pH": statistics.pstdev(vals_h),
            "std_pD": statistics.pstdev(vals_d),
            "std_pA": statistics.pstdev(vals_a),
            "slope_log_H_over_D_per_hour": slope(xs, lhd),
            "slope_log_A_over_D_per_hour": slope(xs, lad),
            "log1p_distinct_timestamps": math.log1p(len(seq)),
            "trajectory_span_hours": span,
        }
        if not all(math.isfinite(v) for k, v in rec.items() if k not in {"match_id", "kickoff", "competition", "home", "away"}):
            raise RuntimeError(f"NONFINITE_FEATURE:{mid}")
        features.append(rec)

    features = sorted(features, key=lambda x: (x["kickoff"], x["match_id"]))
    if len(features) != 400 or len({x["match_id"] for x in features}) != 400:
        raise RuntimeError("FEATURE_IDENTITY_FAIL")
    cols = list(features[0])
    with (OUT / "B05_features.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(features)

    feature_sha = hashlib.sha256((OUT / "B05_features.csv").read_bytes()).hexdigest()
    prereg_sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    freeze = {
        "schema_version": "B05-ELADSIL-TIMESERIES-FREEZE-R1",
        "global_alias": "B05",
        "source_batch_id": "ELAD-PIT6H-B001",
        "rows": 400,
        "source_zip_sha256": source_zip_sha,
        "source_odds_sha256": ODDS_SHA,
        "source_batch_manifest_sha256": batch_sha,
        "feature_packet_sha256": feature_sha,
        "prereg_sha256": prereg_sha,
        "baseline_feature_count": 5,
        "challenger_feature_count": 19,
        "raw_odds_rows": raw_rows,
        "valid_odds_rows": valid_rows,
        "selection_uses_outcomes": False,
        "result_data_rows_read": 0,
        "outcome_values_dereferenced": 0,
        "status": "B05_ZERO_LABEL_FEATURE_PACKET_FROZEN",
    }
    (OUT / "freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(freeze, ensure_ascii=False, indent=2))
    print(json.dumps({"headers": headers}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
