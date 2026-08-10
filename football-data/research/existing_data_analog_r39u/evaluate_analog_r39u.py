#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

LABELS = ("H", "D", "A")
TIE_PRIORITY = {"A": 0, "H": 1, "D": 2}
EPS = 1e-6


@dataclass(frozen=True)
class Row:
    competition: str
    season: str
    dt: date
    home: str
    away: str
    label: str
    x: tuple[float, ...]

    @property
    def key(self) -> str:
        return f"{self.competition}|{self.season}|{self.dt.isoformat()}|{self.home}|{self.away}"


def num(v: str) -> float:
    try:
        x = float(str(v).strip())
    except Exception:
        return math.nan
    return x if math.isfinite(x) else math.nan


def safe_rate(total: float, n: float) -> float:
    if not (math.isfinite(total) and math.isfinite(n)) or n <= 0:
        return math.nan
    return total / n


def feature_vector(r: dict[str, str]) -> tuple[float, ...]:
    hh = num(r.get("home_history_matches", ""))
    ah = num(r.get("away_history_matches", ""))
    hgf = num(r.get("home_history_gf", ""))
    agf = num(r.get("away_history_gf", ""))
    hga = num(r.get("home_history_ga", ""))
    aga = num(r.get("away_history_ga", ""))
    hppg = num(r.get("home_history_ppg", ""))
    appg = num(r.get("away_history_ppg", ""))
    h5ppg = num(r.get("home_last5_ppg", ""))
    a5ppg = num(r.get("away_last5_ppg", ""))
    h5gf = num(r.get("home_last5_gf", ""))
    a5gf = num(r.get("away_last5_gf", ""))
    h5ga = num(r.get("home_last5_ga", ""))
    a5ga = num(r.get("away_last5_ga", ""))
    elo = num(r.get("elo_difference_with_home_advantage", ""))
    return (
        elo,
        h5ppg - a5ppg,
        h5gf - a5gf,
        h5ga - a5ga,
        h5gf + a5gf,
        h5ga + a5ga,
        hppg - appg,
        safe_rate(hgf, hh) - safe_rate(agf, ah),
        safe_rate(hga, hh) - safe_rate(aga, ah),
    )


def finite_vec(x: Iterable[float]) -> bool:
    return all(math.isfinite(v) for v in x)


def load_rows(root: Path) -> list[Row]:
    rows: list[Row] = []
    files = sorted(root.glob("football-data/training_datasets/*/point_in_time.csv"))
    if not files:
        raise RuntimeError("NO_EXISTING_POINT_IN_TIME_DATASETS")
    for p in files:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            for raw in rd:
                label = str(raw.get("label_result", "")).strip().upper()
                if label not in LABELS:
                    continue
                try:
                    dt = date.fromisoformat(str(raw.get("date", "")).strip())
                except Exception:
                    continue
                comp = str(raw.get("competition_id", "")).strip()
                season = str(raw.get("season", "")).strip()
                home = str(raw.get("home_team", "")).strip()
                away = str(raw.get("away_team", "")).strip()
                if not all((comp, season, home, away)):
                    continue
                x = feature_vector(raw)
                rows.append(Row(comp, season, dt, home, away, label, x))
    keys = [r.key for r in rows]
    if len(keys) != len(set(keys)):
        raise RuntimeError("DUPLICATE_MATCH_KEYS")
    return rows


def quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        raise ValueError("empty quantile")
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    w = pos - lo
    return s[lo] * (1.0 - w) + s[hi] * w


def robust_params(pool: list[Row]) -> tuple[list[float], list[float]]:
    med: list[float] = []
    scale: list[float] = []
    for j in range(len(pool[0].x)):
        vals = [r.x[j] for r in pool]
        m = quantile(vals, 0.5)
        iqr = quantile(vals, 0.75) - quantile(vals, 0.25)
        if not math.isfinite(iqr) or iqr < 1e-9:
            iqr = 1.0
        med.append(m)
        scale.append(iqr)
    return med, scale


def dist(a: tuple[float, ...], b: tuple[float, ...], med: list[float], scale: list[float]) -> float:
    total = 0.0
    for j in range(len(a)):
        za = (a[j] - med[j]) / scale[j]
        zb = (b[j] - med[j]) / scale[j]
        total += (za - zb) ** 2
    return math.sqrt(total)


def probs_for(target: Row, prior: list[Row], k: int) -> dict[str, float]:
    med, scale = robust_params(prior)
    ranked = sorted(((dist(target.x, r.x, med, scale), r) for r in prior), key=lambda z: (z[0], z[1].key))[:k]
    votes = {lab: 0.0 for lab in LABELS}
    for d, r in ranked:
        votes[r.label] += 1.0 / (d + EPS)
    total = sum(votes.values())
    if total <= 0:
        raise RuntimeError("ZERO_KNN_VOTE")
    return {lab: votes[lab] / total for lab in LABELS}


def choose(p: dict[str, float]) -> str:
    return max(LABELS, key=lambda lab: (p[lab], TIE_PRIORITY[lab]))


def sample_sha(keys: list[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(keys)) + "\n").encode()).hexdigest()


def metrics(records: list[dict]) -> dict:
    n = len(records)
    hits = sum(r["prediction"] == r["actual"] for r in records)
    pred = Counter(r["prediction"] for r in records)
    actual = Counter(r["actual"] for r in records)
    dp = sum(r["prediction"] == "D" for r in records)
    dh = sum(r["prediction"] == "D" and r["actual"] == "D" for r in records)
    ad = sum(r["actual"] == "D" for r in records)
    precision = dh / dp if dp else None
    recall = dh / ad if ad else None
    f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and precision + recall > 0 else None
    ll = 0.0
    brier = 0.0
    for r in records:
        p = r["probabilities"]
        ll += -math.log(max(p[r["actual"]], 1e-15))
        brier += sum((p[lab] - (1.0 if r["actual"] == lab else 0.0)) ** 2 for lab in LABELS)
    return {
        "n": n,
        "hits": hits,
        "accuracy": hits / n,
        "predicted_counts": {lab: pred.get(lab, 0) for lab in LABELS},
        "actual_counts": {lab: actual.get(lab, 0) for lab in LABELS},
        "draw_predictions": dp,
        "draw_hits": dh,
        "draw_precision": precision,
        "draw_recall": recall,
        "draw_f1": f1,
        "log_loss": ll / n,
        "brier": brier / n,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R39U-EXISTING-DATA-ANALOG-1.0"
    assert pre["hard_boundaries"]["external_network_requests"] == 0
    assert pre["hard_boundaries"]["new_data_collection"] is False
    ks = [int(x) for x in pre["algorithm"]["candidate_k"]]
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    seed = str(pre["sample"]["seed"])
    sample_n = int(pre["sample"]["size"])

    rows = load_rows(args.root)
    by_comp: dict[str, list[Row]] = {}
    for r in rows:
        by_comp.setdefault(r.competition, []).append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))

    eligible: list[tuple[Row, int]] = []
    for comp, rs in sorted(by_comp.items()):
        valid_prior: list[Row] = []
        i = 0
        while i < len(rs):
            d = rs[i].dt
            j = i
            while j < len(rs) and rs[j].dt == d:
                j += 1
            for t in rs[i:j]:
                if finite_vec(t.x) and len(valid_prior) >= min_prior:
                    eligible.append((t, len(valid_prior)))
            valid_prior.extend(r for r in rs[i:j] if finite_vec(r.x))
            i = j

    ranked = sorted(eligible, key=lambda z: (hashlib.sha256(f"{seed}|{z[0].key}".encode()).hexdigest(), z[0].key))
    sample = ranked[:sample_n]
    if len(sample) != sample_n:
        raise RuntimeError(f"INSUFFICIENT_ELIGIBLE_SAMPLE:{len(sample)}")

    keys = [t.key for t, _ in sample]
    results: dict[str, list[dict]] = {str(k): [] for k in ks}
    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and finite_vec(r.x)]
        if len(prior) < max(max(ks), min_prior):
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")
        for k in ks:
            p = probs_for(t, prior, k)
            pred = choose(p)
            results[str(k)].append({
                "key": t.key,
                "competition": t.competition,
                "season": t.season,
                "date": t.dt.isoformat(),
                "home": t.home,
                "away": t.away,
                "actual": t.label,
                "prediction": pred,
                "probabilities": {lab: round(p[lab], 12) for lab in LABELS},
                "strictly_prior_pool_n": len(prior),
            })

    out = {
        "schema_version": pre["schema_version"],
        "status": "PASS_R39U_EXISTING_DATA_FIXED100_EXPLORATORY",
        "sample_status": pre["sample"]["status"],
        "source_dataset_files": len(by_comp),
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "fixed100_identity_sha256": sample_sha(keys),
        "sample_keys": keys,
        "candidate_results": {k: {"metrics": metrics(v), "predictions": v} for k, v in results.items()},
        "hard_boundaries": pre["hard_boundaries"],
        "interpretation_boundary": "Exploratory retrospective research only. All target labels already exist in repository data; this is not an untouched blind confirmation and cannot promote formal_weight above 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r39u_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r39u_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    summary = {k: v["metrics"] for k, v in out["candidate_results"].items()}
    print(json.dumps({"status": out["status"], "source_rows": len(rows), "eligible_rows": len(eligible), "fixed100_identity_sha256": out["fixed100_identity_sha256"], "metrics": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
