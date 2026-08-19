#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

SCHEMA = "C076B_HIGH_TAIL_DGIVENT_SUPPORT_AUDIT_V1"
FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM = 72180


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


def legal_d(total: int) -> list[int]:
    return list(range(-total, total + 1, 2))


def support_row(g: pd.DataFrame, total: int) -> dict:
    legal = legal_d(total)
    counts = Counter(int(x) for x in g["D"].tolist())
    observed = sorted(counts)
    missing = [d for d in legal if d not in counts]
    class_counts = {str(d): int(counts.get(d, 0)) for d in legal}
    vals = np.asarray([counts.get(d, 0) for d in legal], dtype=float)
    return {
        "T": int(total),
        "n": int(len(g)),
        "legal_D_class_count": int(len(legal)),
        "legal_D": legal,
        "observed_D": observed,
        "observed_legal_D_class_count": int(sum(d in counts for d in legal)),
        "missing_legal_D": missing,
        "support_complete": len(missing) == 0,
        "class_counts": class_counts,
        "class_count_min_including_missing": int(vals.min()) if len(vals) else 0,
        "class_count_median": float(np.median(vals)) if len(vals) else 0.0,
        "class_count_max": int(vals.max()) if len(vals) else 0,
        "unique_leagues": int(g.league_id.nunique()),
        "unique_calendar_years": int(g.calendar_year.nunique()),
        "calendar_year_min": int(g.calendar_year.min()),
        "calendar_year_max": int(g.calendar_year.max()),
    }


def aggregate_group(tail: pd.DataFrame, mask, name: str) -> dict:
    g = tail.loc[mask].copy()
    counts = g.groupby("exact_total").size().to_dict()
    return {
        "group": name,
        "n": int(len(g)),
        "exact_total_counts": {str(int(k)): int(v) for k, v in sorted(counts.items())},
        "unique_leagues": int(g.league_id.nunique()) if len(g) else 0,
        "unique_calendar_years": int(g.calendar_year.nunique()) if len(g) else 0,
        "D_min": int(g.D.min()) if len(g) else None,
        "D_max": int(g.D.max()) if len(g) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    fp = Path(a.fixtures); sp = Path(a.stats); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != FIX_SHA or sha256(sp) != STAT_SHA:
        raise RuntimeError("source SHA mismatch")

    c071.utc = utc_ns
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc_ns(fixtures.date_utc)
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures.id.astype("int64")
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc_ns(stats.known_at)
    stats = stats.dropna(subset=["fixture_id", "known_at"])
    eligible, _ = c071.eligible_identities(fixtures, stats)
    sealed = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)]
    if len(sealed) != EXPECTED_CONFIRM:
        raise RuntimeError(f"sealed identity drift {len(sealed)}")

    # Hard file-read horizon: helper selects goal labels only before 2024-01-01.
    labels = c071.read_dev_labels(fp)
    if len(labels) and not (labels.date_utc < DEV_CUTOFF).all():
        raise RuntimeError("goal-label horizon breach")
    dev_id = eligible[eligible.date_utc < DEV_CUTOFF].copy()
    dev = dev_id.merge(labels[["id","goals_home","goals_away"]], on="id", how="left", validate="one_to_one")
    dev = dev.dropna(subset=["goals_home","goals_away"]).copy()
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["exact_total"] = dev.goals_home + dev.goals_away
    dev["D"] = dev.goals_home - dev.goals_away
    dev["calendar_year"] = dev.date_utc.dt.year.astype(int)
    tail = dev[dev.exact_total >= 7].copy().sort_values(["exact_total","date_utc","id"]).reset_index(drop=True)

    # Structural score mapping audit.
    h_rebuilt = (tail.exact_total.to_numpy(int) + tail.D.to_numpy(int)) / 2.0
    a_rebuilt = (tail.exact_total.to_numpy(int) - tail.D.to_numpy(int)) / 2.0
    parity_ok = ((tail.exact_total.to_numpy(int) - tail.D.to_numpy(int)) % 2 == 0)
    mapping_ok = (
        parity_ok
        & (h_rebuilt >= 0) & (a_rebuilt >= 0)
        & (h_rebuilt == np.floor(h_rebuilt)) & (a_rebuilt == np.floor(a_rebuilt))
        & (h_rebuilt.astype(int) == tail.goals_home.to_numpy(int))
        & (a_rebuilt.astype(int) == tail.goals_away.to_numpy(int))
    )
    if not bool(np.all(mapping_ok)):
        raise RuntimeError("T,D score mapping audit failed")

    per_total = {}
    for total, g in tail.groupby("exact_total", sort=True):
        per_total[str(int(total))] = support_row(g, int(total))

    exact_counts = {str(int(k)): int(v) for k, v in tail.groupby("exact_total").size().sort_index().items()}
    cumulative = {f"T_ge_{k}": int((tail.exact_total >= k).sum()) for k in range(7, 13)}
    aggregate = {
        "T7": aggregate_group(tail, tail.exact_total == 7, "T=7"),
        "T8": aggregate_group(tail, tail.exact_total == 8, "T=8"),
        "T9": aggregate_group(tail, tail.exact_total == 9, "T=9"),
        "T10plus": aggregate_group(tail, tail.exact_total >= 10, "T>=10"),
    }

    # Flat class table for downstream human/model-family feasibility review.
    class_rows = []
    for total_str, rec in per_total.items():
        for d_str, n in rec["class_counts"].items():
            class_rows.append({
                "T": int(total_str), "D": int(d_str), "n": int(n),
                "observed": int(n) > 0,
                "home_goals": (int(total_str)+int(d_str))//2,
                "away_goals": (int(total_str)-int(d_str))//2,
            })
    pd.DataFrame(class_rows).to_csv(out / "high_tail_d_support_cells.csv", index=False)

    summary = {
        "schema_version": SCHEMA,
        "status": "SUPPORT_AUDIT_COMPLETE",
        "formal_weight": 0,
        "predictive_model_fit": False,
        "population": {
            "eligible_pre2024_completed": int(len(dev)),
            "tail_T_ge_7": int(len(tail)),
            "unique_leagues": int(tail.league_id.nunique()),
            "calendar_years": [int(x) for x in sorted(tail.calendar_year.unique())],
            "exact_total_min": int(tail.exact_total.min()),
            "exact_total_max": int(tail.exact_total.max()),
        },
        "exact_total_counts": exact_counts,
        "cumulative_counts": cumulative,
        "per_exact_total_support": per_total,
        "aggregate_support": aggregate,
        "mapping_audit": {
            "rows": int(len(tail)),
            "parity_legal_rows": int(parity_ok.sum()),
            "exact_score_reconstruction_rows": int(mapping_ok.sum()),
            "max_abs_home_rebuild_error": float(np.max(np.abs(h_rebuilt-tail.goals_home.to_numpy(int)))) if len(tail) else 0.0,
            "max_abs_away_rebuild_error": float(np.max(np.abs(a_rebuilt-tail.goals_away.to_numpy(int)))) if len(tail) else 0.0,
        },
        "interpretation_boundary": {
            "automatic_sample_sufficiency_claim": False,
            "D_given_T_model_created": False,
            "exact_tail_created": False,
            "unified_matrix_generated": False,
            "note": "raw support only; future model family and validation plan must define its own frozen sample/class sufficiency gate",
        },
        "boundaries": {
            "C075C_consumed_tail_labels_used": False,
            "C075E_consumed_tail_labels_used": False,
            "C071_post2024_goal_labels_opened": False,
            "C071_sealed_identity_count": int(len(sealed)),
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "feature_search": False,
            "model_fit": False,
            "CURRENT_changed": False,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "tail_n": len(tail),
        "exact_total_counts": exact_counts,
        "cumulative_counts": cumulative,
        "T7_support": per_total.get("7"),
        "T8_support": per_total.get("8"),
        "T9_support": per_total.get("9"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
