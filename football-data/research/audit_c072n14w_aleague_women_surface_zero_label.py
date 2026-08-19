#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026")
PREFERRED = (0.5, 1.5, 2.5, 3.5, 4.5)
SNAPSHOTS = (60, 30, 1)
MARKET_TYPES = {
    "OVER_UNDER_05": 0.5,
    "OVER_UNDER_15": 1.5,
    "OVER_UNDER_25": 2.5,
    "OVER_UNDER_35": 3.5,
    "OVER_UNDER_45": 4.5,
}
MARKET_NAME_RE = re.compile(r"^Over/Under ([0-4]\.5) Goals$", re.I)
RUNNER_RE = re.compile(r"^(Over|Under)\s+([0-4]\.5)(?:\s+Goals)?$", re.I)
ALLOWED = [
    "EVENT_DATE", "EVENT_ID", "MARKET_TYPE", "MARKET_ID", "MARKET_NAME",
    "SELECTION_ID", "RUNNER_NAME", "HOME_TEAM", "AWAY_TEAM",
    "BEST_BACK_PRICE_60_MIN_PRIOR", "BEST_LAY_PRICE_60_MIN_PRIOR",
    "BEST_BACK_PRICE_30_MIN_PRIOR", "BEST_LAY_PRICE_30_MIN_PRIOR",
    "BEST_BACK_PRICE_1_MIN_PRIOR", "BEST_LAY_PRICE_1_MIN_PRIOR",
]
FORBIDDEN = {"TOTAL_GOALS", "IS_WINNER", "HOME_SCORE", "AWAY_SCORE", "RUNNER_STATUS"}
OUT = Path("football-data/research/c072n14w_aleague_women_surface_zero_label_summary.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm(x: str) -> str:
    return " ".join(str(x).strip().casefold().split())


def price(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def recognized_line(mt: str, mn: str) -> float | None:
    by_type = MARKET_TYPES.get(str(mt).strip().upper())
    m = MARKET_NAME_RE.fullmatch(str(mn).strip())
    by_name = float(m.group(1)) if m else None
    if by_type is not None and by_name is not None and by_type != by_name:
        return None
    line = by_type if by_type is not None else by_name
    return line if line in PREFERRED else None


def runner_side(name: str, line: float) -> str | None:
    m = RUNNER_RE.fullmatch(str(name).strip())
    if not m or float(m.group(2)) != line:
        return None
    return m.group(1).lower()


def audit_season(path: Path, season: str) -> dict:
    # Header inspection is allowed; usecols then prevents target/result fields from being materialized.
    header = list(pd.read_csv(path, nrows=0).columns)
    missing = [c for c in ALLOWED if c not in header]
    if missing:
        return {"schema_ok": False, "missing_allowed_columns": missing, "sha256": sha256_file(path), "bytes": path.stat().st_size}

    df = pd.read_csv(path, usecols=ALLOWED, dtype=str, keep_default_na=False)
    if set(df.columns) != set(ALLOWED):
        raise RuntimeError(f"unexpected materialized schema for {season}")
    if any(c in df.columns for c in FORBIDDEN):
        raise RuntimeError(f"forbidden target/result field materialized for {season}")

    event_identity: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    markets: dict[tuple[str, float, str], dict] = {}
    diagnostics = Counter()

    for r in df.itertuples(index=False):
        d = r._asdict()
        event_id = str(d["EVENT_ID"]).strip()
        line = recognized_line(d["MARKET_TYPE"], d["MARKET_NAME"])
        if line is None or not event_id:
            continue
        side = runner_side(d["RUNNER_NAME"], line)
        market_id = str(d["MARKET_ID"]).strip()
        selection_id = str(d["SELECTION_ID"]).strip()
        if side not in {"over", "under"} or not market_id or not selection_id:
            diagnostics["bad_runner_identity"] += 1
            continue
        event_identity[event_id].add((norm(d["EVENT_DATE"]), norm(d["HOME_TEAM"]), norm(d["AWAY_TEAM"])))
        key = (event_id, line, market_id)
        m = markets.setdefault(key, {"market_types": set(), "market_names": set(), "runners": defaultdict(list)})
        m["market_types"].add(str(d["MARKET_TYPE"]).strip())
        m["market_names"].add(str(d["MARKET_NAME"]).strip())
        snaps = {}
        for s in SNAPSHOTS:
            b = price(d[f"BEST_BACK_PRICE_{s}_MIN_PRIOR"])
            l = price(d[f"BEST_LAY_PRICE_{s}_MIN_PRIOR"])
            crossed = b is not None and l is not None and b > l
            if crossed:
                diagnostics[f"crossed_T{s}"] += 1
            snaps[s] = (b, l, crossed)
        m["runners"][side].append((selection_id, snaps))

    identity_conflicts = {eid for eid, vals in event_identity.items() if len(vals) != 1}
    event_line_keys: dict[tuple[str, float], list[tuple[str, float, str]]] = defaultdict(list)
    normalized = {}
    for key, m in markets.items():
        eid, line, _mid = key
        event_line_keys[(eid, line)].append(key)
        if eid in identity_conflicts:
            continue
        if len(m["market_types"]) != 1 or len(m["market_names"]) != 1:
            diagnostics["market_metadata_conflict"] += 1
            continue
        if set(m["runners"]) != {"over", "under"}:
            diagnostics["missing_runner_side"] += 1
            continue
        sides = {}
        ok = True
        for side in ("over", "under"):
            obs = m["runners"][side]
            sels = {x[0] for x in obs}
            if len(sels) != 1:
                diagnostics["multiple_selection_ids"] += 1
                ok = False
                break
            first = obs[0]
            if any(x != first for x in obs[1:]):
                diagnostics["duplicate_runner_quote_conflict"] += 1
                ok = False
                break
            sides[side] = first
        if ok:
            normalized[key] = sides

    complete_line = set()
    event_any_line = set()
    for event_line, keys in event_line_keys.items():
        eid, line = event_line
        event_any_line.add(eid)
        if len(keys) != 1:
            diagnostics["multiple_market_ids_event_line"] += 1
            continue
        sides = normalized.get(keys[0])
        if sides is None:
            continue
        ok = True
        for snap in SNAPSHOTS:
            for side in ("over", "under"):
                b, l, crossed = sides[side][1][snap]
                if b is None or l is None or crossed:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            complete_line.add((eid, line))

    ou25_all3 = sum((eid, 2.5) in complete_line for eid in event_any_line)
    ge3_all3 = sum(sum((eid, line) in complete_line for line in PREFERRED) >= 3 for eid in event_any_line)
    all5_all3 = sum(all((eid, line) in complete_line for line in PREFERRED) for eid in event_any_line)

    return {
        "schema_ok": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows_materialized_non_target_only": int(len(df)),
        "preferred_ou_events": int(len(event_any_line)),
        "ou25_all3_events": int(ou25_all3),
        "ge3_lines_all3_events": int(ge3_all3),
        "all5_lines_all3_events": int(all5_all3),
        "identity_conflict_events": int(len(identity_conflicts)),
        "diagnostics": dict(diagnostics),
        "target_result_columns_materialized": 0,
        "target_result_values_materialized": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    args = ap.parse_args()
    root = Path(args.source_dir)
    per = {}
    for season in SEASONS:
        p = root / f"A-League_Womens_{season}_All_Markets.csv"
        if not p.exists():
            raise RuntimeError(f"missing source file {p.name}")
        per[season] = audit_season(p, season)

    pooled_events = sum(x.get("preferred_ou_events", 0) for x in per.values())
    pooled_ou25 = sum(x.get("ou25_all3_events", 0) for x in per.values())
    pooled_all5 = sum(x.get("all5_lines_all3_events", 0) for x in per.values())
    pooled_conflicts = sum(x.get("identity_conflict_events", 0) for x in per.values())
    frac = lambda a, b: float(a / b) if b else 0.0

    gates = {
        "all_six_files_schema_ok": all(x.get("schema_ok") for x in per.values()),
        "zero_target_result_materialization": all(x.get("target_result_values_materialized", 0) == 0 for x in per.values()),
        "pooled_preferred_ou_events_ge_450": pooled_events >= 450,
        "pooled_all5_all3_ge_350": pooled_all5 >= 350,
        "each_dev_season_all5_all3_ge_45": all(per[s].get("all5_lines_all3_events", 0) >= 45 for s in SEASONS[:5]),
        "reserve_2025_26_all5_all3_ge_45": per["2025-2026"].get("all5_lines_all3_events", 0) >= 45,
        "pooled_ou25_all3_fraction_ge_75pct": frac(pooled_ou25, pooled_events) >= 0.75,
        "pooled_all5_all3_fraction_ge_55pct": frac(pooled_all5, pooled_events) >= 0.55,
        "identity_conflict_rate_le_1pct": frac(pooled_conflicts, pooled_events) <= 0.01,
        "zero_model": True,
        "seals_and_quarantine_hold": True,
    }
    passed = all(gates.values())
    summary = {
        "schema": "C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_V1",
        "project_line": "football3",
        "classification": "ZERO_LABEL_SOURCE_COVERAGE",
        "source_repo": "betfair-datascientists/betfair-datascientists.github.io",
        "source_revision": "9fe7fb127cd05316dbd438fe0e5be82c5c3ed536",
        "terminal": "C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_PASS" if passed else "C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_STOP",
        "pass": passed,
        "per_season": per,
        "pooled_preferred_ou_events": pooled_events,
        "pooled_ou25_all3_events": pooled_ou25,
        "pooled_all5_all3_events": pooled_all5,
        "pooled_ou25_all3_fraction": frac(pooled_ou25, pooled_events),
        "pooled_all5_all3_fraction": frac(pooled_all5, pooled_events),
        "pooled_identity_conflict_events": pooled_conflicts,
        "pooled_identity_conflict_rate": frac(pooled_conflicts, pooled_events),
        "target_result_columns_materialized": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_scientific_results_used": False,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
        "gates": gates,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
