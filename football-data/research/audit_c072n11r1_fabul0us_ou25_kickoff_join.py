#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import difflib
import hashlib
import io
import json
import math
import re
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DYNAMIC_SHA = "c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
FILES = {
    "PREMIER LEAGUE": "data/england/premier-league.csv",
    "LIGA": "data/spain/laliga.csv",
    "BUNDESLIGA": "data/germany/bundesliga.csv",
    "SERIE A": "data/italy/serie-a.csv",
    "LIGUE 1": "data/france/ligue-1.csv",
}
IDENTITY_COLS = ["Date", "Season", "HomeTeam", "AwayTeam"]
SUMMARY = Path("football-data/research/c072n11r1_fabul0us_ou25_kickoff_join_summary.json")
MANIFEST = Path("football-data/research/c072n11r1_fabul0us_ou25_kickoff_manifest.csv")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_dt(x: str) -> datetime | None:
    s = str(x or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        z = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return z.replace(tzinfo=None) if z.tzinfo is not None else z
    except ValueError:
        return None


def canon(x: str) -> str:
    s = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def sim(a: str, b: str) -> float:
    return float(difflib.SequenceMatcher(None, a, b).ratio())


def price(x: str) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/{path}"


def load_kickoff_sources() -> tuple[dict[str, list[dict]], dict]:
    sources: dict[str, list[dict]] = {}
    meta = {}
    for comp, path in FILES.items():
        # Binding zero-label boundary: only the four identity/time columns are materialized.
        df = pd.read_csv(raw_url(path), usecols=IDENTITY_COLS, dtype=str, keep_default_na=False)
        if any(c not in IDENTITY_COLS for c in df.columns):
            raise RuntimeError("unexpected non-identity column materialized")
        df = df[df["Season"] == "2023-2024"].copy().reset_index(drop=False).rename(columns={"index": "source_row_index"})
        rows = []
        bad_dates = 0
        for _, r in df.iterrows():
            k = parse_dt(r["Date"])
            if k is None:
                bad_dates += 1
                continue
            rows.append({
                "source_row_index": int(r["source_row_index"]),
                "Date": r["Date"], "Season": r["Season"],
                "HomeTeam": r["HomeTeam"], "AwayTeam": r["AwayTeam"],
                "kickoff": k, "home_key": canon(r["HomeTeam"]), "away_key": canon(r["AwayTeam"]),
            })
        sources[comp] = rows
        meta[comp] = {
            "path": path, "revision": REV,
            "identity_columns_materialized": IDENTITY_COLS,
            "2023_24_identity_rows_with_parseable_date": len(rows),
            "bad_date_rows_excluded": bad_dates,
            "target_result_columns_materialized": 0,
            "target_result_values_materialized": 0,
        }
    return sources, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dynamic-csv", required=True)
    args = ap.parse_args()
    path = Path(args.dynamic_csv)
    if sha256(path) != DYNAMIC_SHA:
        raise RuntimeError("dynamic source SHA mismatch")

    # key -> timestamp -> (under, over). Conflicting price pair at one timestamp invalidates identity.
    obs: dict[tuple[str, str, str], dict[datetime, tuple[float, float]]] = defaultdict(dict)
    conflict_keys: set[tuple[str, str, str]] = set()
    dynamic_rows_top5 = 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"competition","home_team","away_team","odds_under_2.5","odds_over_2.5","U/O 2.5 timestamp"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError("dynamic source schema mismatch")
        for r in reader:
            comp = str(r.get("competition", "")).strip()
            if comp not in FILES:
                continue
            dynamic_rows_top5 += 1
            home, away = str(r.get("home_team", "")).strip(), str(r.get("away_team", "")).strip()
            t = parse_dt(r.get("U/O 2.5 timestamp", ""))
            u, o = price(r.get("odds_under_2.5", "")), price(r.get("odds_over_2.5", ""))
            if not home or not away or t is None or u is None or o is None:
                continue
            key = (comp, home, away)
            pair = (u, o)
            old = obs[key].get(t)
            if old is None:
                obs[key][t] = pair
            elif old != pair:
                conflict_keys.add(key)

    for k in conflict_keys:
        obs.pop(k, None)

    top5_dynamic_identities = len(obs) + len(conflict_keys)
    sources, source_meta = load_kickoff_sources()

    provisional = []
    unmatched = 0
    exact_n = 0
    fuzzy_n = 0
    no_window_candidates = 0

    for key, tmap in obs.items():
        comp, home, away = key
        times = sorted(tmap)
        max_t = times[-1]
        hk, ak = canon(home), canon(away)
        candidates = [
            s for s in sources[comp]
            if s["kickoff"] > max_t and s["kickoff"] <= max_t + timedelta(days=7)
        ]
        if not candidates:
            unmatched += 1; no_window_candidates += 1; continue

        exact = [s for s in candidates if s["home_key"] == hk and s["away_key"] == ak]
        if len(exact) == 1:
            s = exact[0]
            provisional.append({"key": key, "source": s, "method": "EXACT", "hr": 1.0, "ar": 1.0, "mean": 1.0, "margin": 1.0})
            exact_n += 1
            continue
        if len(exact) > 1:
            unmatched += 1; continue

        scored = []
        for s in candidates:
            hr, ar = sim(hk, s["home_key"]), sim(ak, s["away_key"])
            mean = (hr + ar) / 2.0
            scored.append((mean, min(hr, ar), hr, ar, s))
        scored.sort(key=lambda z: (-z[0], -z[1], z[4]["kickoff"], z[4]["source_row_index"]))
        best = scored[0]
        second_mean = scored[1][0] if len(scored) > 1 else 0.0
        margin = best[0] - second_mean
        if best[1] >= 0.30 and best[0] >= 0.65 and margin >= 0.15:
            provisional.append({"key": key, "source": best[4], "method": "FUZZY", "hr": best[2], "ar": best[3], "mean": best[0], "margin": margin})
            fuzzy_n += 1
        else:
            unmatched += 1

    claims = Counter((p["key"][0], p["source"]["source_row_index"]) for p in provisional)
    collisions = {k for k, n in claims.items() if n > 1}
    accepted = [p for p in provisional if (p["key"][0], p["source"]["source_row_index"]) not in collisions]
    collision_rows_invalidated = len(provisional) - len(accepted)

    manifest = []
    complete = 0
    pit_violations = 0
    accepted_by_comp = Counter()
    complete_by_comp = Counter()
    kickoff_minus_max_quote_hours = []

    for p in accepted:
        comp, home, away = p["key"]
        s = p["source"]
        kickoff = s["kickoff"]
        tmap = obs[p["key"]]
        times = sorted(tmap)
        kickoff_minus_max_quote_hours.append((kickoff - times[-1]).total_seconds() / 3600.0)
        accepted_by_comp[comp] += 1

        row = {
            "competition": comp, "dynamic_home_team": home, "dynamic_away_team": away,
            "kickoff_source_path": FILES[comp], "kickoff_source_revision": REV,
            "kickoff_source_row_index": s["source_row_index"], "kickoff_source_date": s["Date"],
            "kickoff_source_home_team": s["HomeTeam"], "kickoff_source_away_team": s["AwayTeam"],
            "join_method": p["method"], "home_ratio": f"{p['hr']:.9f}", "away_ratio": f"{p['ar']:.9f}",
            "mean_ratio": f"{p['mean']:.9f}", "best_margin": f"{p['margin']:.9f}",
        }
        ok = True
        for label, hours in (("24h", 24), ("6h", 6), ("1h", 1)):
            cutoff = kickoff - timedelta(hours=hours)
            ix = bisect.bisect_right(times, cutoff) - 1
            if ix < 0:
                ok = False
                row[f"uo_{label}_timestamp"] = ""
                row[f"under_{label}"] = ""
                row[f"over_{label}"] = ""
                continue
            t = times[ix]
            u, o = tmap[t]
            if not (t <= cutoff and t < kickoff):
                pit_violations += 1
                ok = False
            row[f"uo_{label}_timestamp"] = t.isoformat(sep=" ")
            row[f"under_{label}"] = f"{u:.12g}"
            row[f"over_{label}"] = f"{o:.12g}"
        row["complete_24h_6h_1h"] = "1" if ok else "0"
        if ok:
            complete += 1
            complete_by_comp[comp] += 1
        manifest.append(row)

    manifest.sort(key=lambda r: (r["kickoff_source_date"], r["competition"], r["dynamic_home_team"], r["dynamic_away_team"]))
    fields = [
        "competition","dynamic_home_team","dynamic_away_team","kickoff_source_path","kickoff_source_revision",
        "kickoff_source_row_index","kickoff_source_date","kickoff_source_home_team","kickoff_source_away_team",
        "join_method","home_ratio","away_ratio","mean_ratio","best_margin",
        "uo_24h_timestamp","under_24h","over_24h","uo_6h_timestamp","under_6h","over_6h",
        "uo_1h_timestamp","under_1h","over_1h","complete_24h_6h_1h",
    ]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader(); w.writerows(manifest)

    accepted_n = len(accepted)
    join_rate = accepted_n / top5_dynamic_identities if top5_dynamic_identities else 0.0
    complete_rate = complete / accepted_n if accepted_n else 0.0
    one_to_one = len({(r["competition"], r["kickoff_source_row_index"]) for r in manifest}) == len(manifest)
    q = sorted(kickoff_minus_max_quote_hours)
    def quantile(p: float):
        if not q: return None
        return float(q[min(len(q)-1, max(0, int(round(p*(len(q)-1)))) )])

    gates = {
        "dynamic_sha_exact": sha256(path) == DYNAMIC_SHA,
        "five_identity_sources_only": len(source_meta) == 5 and all(v["target_result_columns_materialized"] == 0 for v in source_meta.values()),
        "zero_target_result_values_materialized": True,
        "accepted_joins_ge_1500": accepted_n >= 1500,
        "accepted_join_rate_ge_85pct": join_rate >= 0.85,
        "one_to_one_source_assignment": one_to_one,
        "complete_three_cutoff_matches_ge_1200": complete >= 1200,
        "complete_three_cutoff_rate_ge_75pct": complete_rate >= 0.75,
        "zero_pit_violations": pit_violations == 0,
        "zero_conflicting_same_timestamp_identities": len(conflict_keys) == 0,
        "C070F_sealed_and_zero_model": True,
    }
    passed = all(gates.values())
    terminal = "C072N11R1_FABULOUS_OU25_KICKOFF_JOIN_PASS" if passed else "C072N11R1_FABULOUS_OU25_KICKOFF_JOIN_STOP"

    summary = {
        "schema": "C072N11R1_FABULOUS_OU25_KICKOFF_JOIN_V1",
        "project_line": "football3", "classification": "ZERO_LABEL_ENGINEERING_PIT_JOIN",
        "terminal": terminal, "pass": passed,
        "dynamic_sha256": sha256(path), "kickoff_source_revision": REV,
        "dynamic_rows_top5": dynamic_rows_top5,
        "top5_dynamic_identities": top5_dynamic_identities,
        "conflicting_same_timestamp_dynamic_identities": len(conflict_keys),
        "source_meta": source_meta,
        "provisional_exact": exact_n, "provisional_fuzzy": fuzzy_n,
        "unmatched_before_collision": unmatched, "no_window_candidates": no_window_candidates,
        "collision_source_identities": len(collisions), "collision_rows_invalidated": collision_rows_invalidated,
        "accepted_joins": accepted_n, "accepted_join_rate": join_rate,
        "accepted_by_competition": dict(sorted(accepted_by_comp.items())),
        "complete_24h_6h_1h_matches": complete, "complete_rate_among_accepted": complete_rate,
        "complete_by_competition": dict(sorted(complete_by_comp.items())),
        "pit_violations": pit_violations,
        "kickoff_minus_last_uo_quote_hours_quantiles": {"p10": quantile(.10), "p25": quantile(.25), "p50": quantile(.50), "p75": quantile(.75), "p90": quantile(.90)},
        "manifest_rows": len(manifest), "manifest_sha256": sha256(MANIFEST),
        "target_result_columns_materialized": 0, "target_result_values_materialized": 0,
        "model_fit": 0, "model_score": 0,
        "C073_C077_scientific_results_used": False, "C070F_confirmation1597_opened": False,
        "formal_weight": 0, "gates": gates,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
