from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path

EXPECTED = {
    "matches_England.json": 380,
    "matches_Spain.json": 380,
    "matches_Italy.json": 380,
    "matches_Germany.json": 306,
    "matches_France.json": 380,
}


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0.0 or vy <= 0.0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def read_index(root: Path) -> list[dict]:
    # The match label column is intentionally never captured or parsed. Only ID/date/source are used.
    text = (root / "processed-v2/README.md").read_text(encoding="utf-8")
    pat = re.compile(
        r"^\|\[(\d+)\]\(files/(\d+)\.json\)\|[^|]*\|([^|]+)\|(matches_[A-Za-z_]+\.json)\|$",
        re.M,
    )
    rows = []
    for a, b, date_text, source_file in pat.findall(text):
        if source_file not in EXPECTED:
            continue
        if a != b:
            raise RuntimeError(f"index id mismatch: {a} {b}")
        m = re.search(r"([A-Za-z]+ \d{1,2}, \d{4}) at", date_text)
        if not m:
            raise RuntimeError(f"cannot parse date without label: {a} {date_text}")
        d = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        rows.append({"match_id": int(a), "date": d, "source_file": source_file})
    counts = collections.Counter(r["source_file"] for r in rows)
    if dict(counts) != EXPECTED or len(rows) != 1826:
        raise RuntimeError(f"index scope drift: n={len(rows)} counts={dict(counts)}")
    return rows


def team_match_rows(root: Path, index_rows: list[dict], min_accurate_xy: int) -> tuple[list[dict], dict]:
    out = []
    total_pass = total_accurate = total_accurate_xy = 0
    valid_fixture_n = 0
    leagues = collections.defaultdict(list)
    for meta in index_rows:
        mid = int(meta["match_id"])
        p = root / f"processed-v2/files/{mid}.json"
        if not p.is_file():
            raise RuntimeError(f"missing source match: {mid}")
        d = json.loads(p.read_text(encoding="utf-8"))
        events = d.get("events")
        if not isinstance(events, list) or not events:
            raise RuntimeError(f"empty events: {mid}")
        teams = sorted({int(e.get("teamId", 0) or 0) for e in events if int(e.get("teamId", 0) or 0) > 0})
        if len(teams) != 2:
            raise RuntimeError(f"team inventory drift {mid}: {teams}")
        by = {
            t: {
                "pass_n": 0,
                "accurate_pass_n": 0,
                "accurate_xy_pass_n": 0,
                "forward_gain_sum": 0.0,
                "net_gain_sum": 0.0,
                "counterattack_event_n": 0,
                "event_n": 0,
            }
            for t in teams
        }
        for e in events:
            tid = int(e.get("teamId", 0) or 0)
            if tid not in by:
                continue
            z = by[tid]
            z["event_n"] += 1
            tags = {int(x.get("id")) for x in (e.get("tags") or []) if isinstance(x, dict) and x.get("id") is not None}
            if 1901 in tags:
                z["counterattack_event_n"] += 1
            if e.get("eventName") != "Pass":
                continue
            z["pass_n"] += 1
            total_pass += 1
            if 1801 not in tags:
                continue
            z["accurate_pass_n"] += 1
            total_accurate += 1
            pos = e.get("positions") or []
            good2 = (
                len(pos) >= 2
                and all(isinstance(q, dict) for q in pos[:2])
                and all(isinstance(pos[i].get("x"), (int, float)) and isinstance(pos[i].get("y"), (int, float)) for i in (0, 1))
            )
            if not good2:
                continue
            x0 = float(pos[0]["x"])
            x1 = float(pos[1]["x"])
            if not (0.0 <= x0 <= 100.0 and 0.0 <= x1 <= 100.0):
                raise RuntimeError(f"coordinate domain drift {mid}: {x0},{x1}")
            dx = x1 - x0
            z["accurate_xy_pass_n"] += 1
            z["forward_gain_sum"] += max(dx, 0.0)
            z["net_gain_sum"] += dx
            total_accurate_xy += 1
        fixture_valid = True
        for tid in teams:
            z = by[tid]
            nxy = int(z["accurate_xy_pass_n"])
            if z["pass_n"] <= 0 or z["accurate_pass_n"] <= 0 or nxy < min_accurate_xy:
                fixture_valid = False
            feature = z["forward_gain_sum"] / nxy if nxy else None
            net_gain = z["net_gain_sum"] / nxy if nxy else None
            pass_accuracy = z["accurate_pass_n"] / z["pass_n"] if z["pass_n"] else None
            ca_rate = z["counterattack_event_n"] / z["event_n"] if z["event_n"] else None
            row = {
                "match_id": mid,
                "date": meta["date"],
                "league": meta["source_file"],
                "team_id": tid,
                "source_file_sha256": sha256_path(p),
                "pass_n": int(z["pass_n"]),
                "accurate_pass_n": int(z["accurate_pass_n"]),
                "accurate_xy_pass_n": nxy,
                "forward_x_gain_per_accurate_pass": feature,
                "net_x_gain_per_accurate_pass_audit_only": net_gain,
                "pass_accuracy_proxy": pass_accuracy,
                "pass_volume_proxy": int(z["pass_n"]),
                "counterattack_event_rate_proxy": ca_rate,
            }
            out.append(row)
            if feature is not None:
                leagues[meta["source_file"]].append(float(feature))
        if fixture_valid:
            valid_fixture_n += 1
    if len(out) != 3652:
        raise RuntimeError(f"team-match inventory drift: {len(out)}")
    variances = {k: statistics.pvariance(v) if len(v) > 1 else 0.0 for k, v in leagues.items()}
    if set(variances) != set(EXPECTED):
        raise RuntimeError(f"league variance inventory drift: {variances}")
    audit = {
        "pass_n": total_pass,
        "accurate_pass_n": total_accurate,
        "accurate_xy_pass_n": total_accurate_xy,
        "accurate_xy_rate_within_accurate": total_accurate_xy / total_accurate if total_accurate else 0.0,
        "raw_bilateral_valid_fixture_n": valid_fixture_n,
        "raw_bilateral_valid_fixture_rate": valid_fixture_n / 1826.0,
        "league_raw_feature_variance": variances,
    }
    return out, audit


def build_strict_pit(rows: list[dict], lookback: int, min_prior: int) -> tuple[list[dict], dict]:
    by_match = collections.defaultdict(list)
    for r in rows:
        by_match[(r["date"], r["match_id"], r["league"])].append(r)
    dates = sorted({r["date"] for r in rows})
    history: dict[tuple[str, int], list[float]] = collections.defaultdict(list)
    pit = []
    bilateral = 0
    match_feature_values = []
    for dt in dates:
        day_keys = sorted((k for k in by_match if k[0] == dt), key=lambda x: (x[2], x[1]))
        pending_updates = []
        for key in day_keys:
            team_rows = by_match[key]
            if len(team_rows) != 2:
                raise RuntimeError(f"PIT match team count drift: {key}")
            available = []
            for r in sorted(team_rows, key=lambda x: x["team_id"]):
                h = history[(r["league"], int(r["team_id"]))]
                f = statistics.fmean(h[-lookback:]) if len(h) >= min_prior else None
                pit_row = {
                    "match_id": int(r["match_id"]),
                    "date": r["date"],
                    "league": r["league"],
                    "team_id": int(r["team_id"]),
                    "prior_n": len(h),
                    "lookback_n": min(len(h), lookback),
                    "strict_pit_forward_x_gain_per_accurate_pass": f,
                }
                pit.append(pit_row)
                available.append(f is not None)
                raw = r["forward_x_gain_per_accurate_pass"]
                if raw is not None:
                    pending_updates.append((r["league"], int(r["team_id"]), float(raw)))
            if all(available):
                bilateral += 1
                vals = [x["strict_pit_forward_x_gain_per_accurate_pass"] for x in pit[-2:]]
                match_feature_values.append(statistics.fmean(float(x) for x in vals if x is not None))
        # Same-calendar-date isolation: update all team states only after all predictions/features for this date exist.
        for league, tid, raw in pending_updates:
            history[(league, tid)].append(raw)
    if len(pit) != 3652:
        raise RuntimeError(f"PIT row drift: {len(pit)}")
    return pit, {
        "strict_pit_bilateral_feature_fixture_n": bilateral,
        "strict_pit_bilateral_feature_coverage": bilateral / 1826.0,
        "strict_pit_match_feature_variance": statistics.pvariance(match_feature_values) if len(match_feature_values) > 1 else 0.0,
        "same_calendar_date_isolation": True,
        "lookback": lookback,
        "min_prior": min_prior,
    }


def proxy_correlations(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["forward_x_gain_per_accurate_pass"] is not None]
    target = [float(r["forward_x_gain_per_accurate_pass"]) for r in valid]
    proxies = {
        "pass_accuracy": [float(r["pass_accuracy_proxy"]) for r in valid],
        "pass_volume": [float(r["pass_volume_proxy"]) for r in valid],
        "counterattack_event_rate": [float(r["counterattack_event_rate_proxy"]) for r in valid],
    }
    result = {name: pearson(target, vals) for name, vals in proxies.items()}
    finite = [abs(float(x)) for x in result.values() if x is not None]
    return {"pearson": result, "max_abs": max(finite) if finite else None, "n": len(valid)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--wys-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    if contract["status"] != "FROZEN_ZERO_LABEL_SOURCE_AUDIT":
        raise RuntimeError("contract not frozen")
    root = Path(args.wys_root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index_rows = read_index(root)
    rows, raw_audit = team_match_rows(root, index_rows, int(contract["audit_gates"]["minimum_accurate_xy_passes_per_team_match"]))
    pit, pit_audit = build_strict_pit(
        rows,
        int(contract["mechanism"]["strict_pit_lookback_completed_same_league_matches"]),
        int(contract["mechanism"]["strict_pit_min_prior_matches"]),
    )
    corr = proxy_correlations(rows)
    gates = contract["audit_gates"]
    checks = {
        "fixture_n": len(index_rows) == int(gates["expected_fixture_n"]),
        "team_match_n": len(rows) == int(gates["expected_team_match_n"]),
        "raw_bilateral_valid_fixture_rate": raw_audit["raw_bilateral_valid_fixture_rate"] >= float(gates["raw_bilateral_valid_fixture_rate_min"]),
        "strict_pit_bilateral_feature_coverage": pit_audit["strict_pit_bilateral_feature_coverage"] >= float(gates["strict_pit_bilateral_feature_coverage_min"]),
        "all_five_leagues_positive_variance": len(raw_audit["league_raw_feature_variance"]) == 5 and all(float(v) > 0 for v in raw_audit["league_raw_feature_variance"].values()),
        "strict_pit_positive_variance": float(pit_audit["strict_pit_match_feature_variance"]) > 0.0,
        "same_source_proxy_redundancy": corr["max_abs"] is not None and float(corr["max_abs"]) < float(gates["max_abs_same_source_proxy_correlation"]),
    }
    passed = all(checks.values())
    status = contract["terminal"]["pass"] if passed else contract["terminal"]["fail"]
    result = {
        "schema_version": "football3-wyscout-pass-progression-source-audit-result-v1",
        "status": status,
        "source_gate_pass": passed,
        "fixture_n": len(index_rows),
        "team_match_n": len(rows),
        "mechanism": contract["mechanism"],
        "raw_audit": raw_audit,
        "strict_pit_audit": pit_audit,
        "proxy_redundancy": corr,
        "checks": checks,
        "candidate_fit_performed": False,
        "outcome_fields_used": False,
        "index_match_label_parsed": False,
        "historical_confirmation_2023_opened": False,
        "prospective_1335_touched": False,
        "formal_model_changed": False,
        "CURRENT_changed": False,
        "formal_weight": 0,
    }
    (out / "SOURCE_AUDIT_RESULT.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "TEAM_MATCH_FEATURES.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows), encoding="utf-8")
    (out / "STRICT_PIT_FEATURES.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in pit), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
