#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import audit_c071_opportunity_source as audit_c071_source
import audit_c077b_zero_label_source_gate as source_gate
import evaluate_c071b_opportunity_pt_v2 as c071
import evaluate_c077a_high_tail_shared_dgiven_t as c077a

SPEC_PATH = Path("football-data/research/c077b_independent_confirmation_execution_spec.json")
SOURCE_CONTRACT_PATH = Path("football-data/research/c077b_candidate_source_freeze_v1.json")
ELIG_CONTRACT_PATH = Path("football-data/research/c077b_prelabel_target_eligibility_contract.json")

EXPECTED_SOURCE_COUNT = 9376
EXPECTED_SOURCE_SHA = "f1e70a3f783be235136060a117645d3a1b42b400dfcef7b4be19d8f11233b8b2"
EXPECTED_ELIGIBLE_COUNT = 6943
EXPECTED_ELIGIBLE_SHA = "4b607eca3a9c2f5589e811fd90b986503ceece87d3ff947f155e99c7e9623149"
EXPECTED_OPENFOOTBALL_COMMIT = "e27eb01726f394ddf9fa68b15d37b900487b5903"

BOOT_REPS = 5000
BOOT_SEED = 77002
DOMAIN_MIN_TAIL = 5
COVERAGE_POOLED = 150
COVERAGE_BLOCK = 50
COVERAGE_DOMAINS_ANY_TAIL = 3
DOMAIN_VOTE_MIN_ELIGIBLE = 3

SCORE_CAPTURE = re.compile(r"\s+(?P<h>\d+)\s*-\s*(?P<a>\d+)(?=\s|\(|$)")
SEASON_PREFIX = re.compile(r"^(?:20\d{2}(?:-\d{2})?|20\d{2})_")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha_lines(lines: list[str]) -> str:
    payload = "\n".join(sorted(lines)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def league_key(source_file: str) -> str:
    family, filename = source_file.split("/", 1)
    stem = Path(filename).stem
    token = SEASON_PREFIX.sub("", stem)
    if not token or token == stem:
        raise RuntimeError(f"league-series token parse failed for {source_file}")
    return f"{family}/{token}"


def reconstruct_zero_label_population(source_root: Path) -> tuple[list[dict], list[dict]]:
    src = load_json(SOURCE_CONTRACT_PATH)
    ec = load_json(ELIG_CONTRACT_PATH)
    actual = git("rev-parse", "HEAD", cwd=source_root)
    if actual != EXPECTED_OPENFOOTBALL_COMMIT or actual != src["candidate_repo_commit"]:
        raise RuntimeError("openfootball source commit drift")

    allowed_years = set(src["source_gate"]["accepted_fixture_calendar_years"])
    rows: list[dict] = []
    for rel in src["frozen_candidate_files"]:
        parsed, _ = source_gate.parse_file(source_root / rel, rel)
        rows.extend(r for r in parsed if r["calendar_year"] in allowed_years)

    source_keys = [f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in rows]
    source_sha = sha_lines(source_keys)
    if len(rows) != EXPECTED_SOURCE_COUNT or source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError(f"source identity drift {len(rows)}/{source_sha}")
    if len(source_keys) != len(set(source_keys)):
        raise RuntimeError("source duplicate identities")

    min_home = int(ec["eligibility"]["minimum_prior_completed_identity_count_home"])
    min_away = int(ec["eligibility"]["minimum_prior_completed_identity_count_away"])
    if min_home != 8 or min_away != 8:
        raise RuntimeError("eligibility threshold drift")

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    hist = Counter()
    eligible: list[dict] = []
    for d in sorted(by_date):
        day = sorted(by_date[d], key=lambda r: (r["competition_family"], r["home_norm"], r["away_norm"], r["source_file"]))
        for r in day:
            hk = f"{r['competition_family']}|{r['home_norm']}"
            ak = f"{r['competition_family']}|{r['away_norm']}"
            if hist[hk] >= min_home and hist[ak] >= min_away:
                z = dict(r)
                z["home_prior_completed_identity_n"] = int(hist[hk])
                z["away_prior_completed_identity_n"] = int(hist[ak])
                eligible.append(z)
        for r in day:
            hist[f"{r['competition_family']}|{r['home_norm']}"] += 1
            hist[f"{r['competition_family']}|{r['away_norm']}"] += 1

    eligible_keys = [f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in eligible]
    eligible_sha = sha_lines(eligible_keys)
    if len(eligible) != EXPECTED_ELIGIBLE_COUNT or eligible_sha != EXPECTED_ELIGIBLE_SHA:
        raise RuntimeError(f"eligible identity drift {len(eligible)}/{eligible_sha}")
    if len(eligible_keys) != len(set(eligible_keys)):
        raise RuntimeError("eligible duplicate identities")
    return rows, eligible


def train_frozen_parent(fixtures_path: Path, stats_path: Path):
    if sha256(fixtures_path) != c077a.FIX_SHA or sha256(stats_path) != c077a.STAT_SHA:
        raise RuntimeError("frozen C077-A training source SHA mismatch")

    c071.utc = c077a.utc_ns
    fixtures = pd.read_parquet(fixtures_path, columns=audit_c071_source.FIXTURE_COLS)
    fixtures["date_utc"] = c077a.utc_ns(fixtures["date_utc"])
    fixtures = fixtures.dropna(subset=audit_c071_source.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures.id.astype("int64")
    stats = pd.read_parquet(stats_path, columns=audit_c071_source.STAT_COLS)
    stats["known_at"] = c077a.utc_ns(stats["known_at"])
    stats = stats.dropna(subset=["fixture_id", "known_at"])

    eligible, _ = c071.eligible_identities(fixtures, stats)
    labels = c071.read_dev_labels(fixtures_path)
    if len(labels) and not (labels.date_utc < c077a.DEV_CUTOFF).all():
        raise RuntimeError("training label horizon breach")
    dev_id = eligible[eligible.date_utc < c077a.DEV_CUTOFF].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    dev = dev_id.merge(labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one")
    dev = dev.dropna(subset=["goals_home", "goals_away"]).copy().reset_index(drop=True)
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["exact_total"] = dev.goals_home + dev.goals_away

    rteam, rleague, _ = c071.result_events(dev_id, labels)
    feat = c071.build_features(dev, rteam, rleague, c077a.empty_opportunity_events())
    feat["goals_home"] = dev.goals_home.to_numpy(int)
    feat["goals_away"] = dev.goals_away.to_numpy(int)
    feat["exact_total"] = dev.exact_total.to_numpy(int)
    tail = feat[feat.exact_total >= 7].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    if len(tail) < 1000:
        raise RuntimeError(f"unexpectedly small frozen training tail {len(tail)}")

    x_trials, y_trials = c077a.expand_goal_trials(tail)
    if len(np.unique(y_trials)) != 2:
        raise RuntimeError("goal-allocation training trial classes collapsed")
    model = c077a.pipeline()
    model.fit(x_trials, y_trials)
    lr = model.named_steps["logisticregression"]
    if list(lr.classes_.astype(int)) != [0, 1]:
        raise RuntimeError(f"unexpected candidate classes {lr.classes_}")
    baseline_tables = c077a.empirical_tables(tail)
    return model, baseline_tables, {
        "eligible_pre2024_rows": int(len(dev)),
        "training_tail_rows": int(len(tail)),
        "training_goal_allocation_trials": int(tail.exact_total.sum()),
        "training_exact_T_values": [int(x) for x in sorted(tail.exact_total.unique())],
        "candidate_classes": [int(x) for x in lr.classes_],
        "candidate_feature_count": int(len(c077a.FEATURES)),
        "baseline_exact_T_tables": [int(x) for x in sorted(baseline_tables)],
    }


def score_parser_self_test() -> dict:
    cases = [
        ("Alpha v Beta  7-1", (7, 1)),
        ("20:30 Alpha v Beta  4-3 (2-1)", (4, 3)),
        ("Alpha v Beta  10-2 (5-0)", (10, 2)),
    ]
    for line, expected in cases:
        m = SCORE_CAPTURE.search(line)
        if m is None:
            raise RuntimeError(f"synthetic score parser miss: {line}")
        got = (int(m.group("h")), int(m.group("a")))
        if got != expected:
            raise RuntimeError(f"synthetic score parser mismatch {got} != {expected}")
    return {"synthetic_cases": len(cases), "status": "PASS"}


def parse_numeric_scores_for_frozen_eligible(source_root: Path, eligible: list[dict]) -> pd.DataFrame:
    src = load_json(SOURCE_CONTRACT_PATH)
    eligible_key_set = {f"{r['date']}|{r['home_norm']}|{r['away_norm']}" for r in eligible}
    out: list[dict] = []

    for rel in src["frozen_candidate_files"]:
        p = source_root / rel
        base_year = source_gate.infer_base_year(rel)
        current_year = base_year
        current_month: int | None = None
        current_date: date | None = None
        for raw in p.read_text(encoding="utf-8-sig").splitlines():
            dm = source_gate.DATE_LINE.match(raw)
            if dm:
                mon = source_gate.MONTHS[dm.group("mon")]
                day = int(dm.group("day"))
                explicit_year = dm.group("year")
                if explicit_year is not None:
                    current_year = int(explicit_year)
                elif current_month is not None and current_month >= 10 and mon <= 3:
                    current_year += 1
                current_month = mon
                current_date = date(current_year, mon, day)
                continue
            if current_date is None or current_date.year not in {2024, 2025} or " v " not in raw:
                continue
            presence = source_gate.SCORE_TOKEN_PRESENT.search(raw)
            if presence is None:
                continue
            prefix = raw[: presence.start()]
            prefix = source_gate.LEADING_TIME.sub("", prefix, count=1).strip()
            if " v " not in prefix:
                continue
            home, away = prefix.rsplit(" v ", 1)
            home = home.strip(); away = away.strip()
            hn = source_gate.norm_team(home); an = source_gate.norm_team(away)
            key = f"{current_date.isoformat()}|{hn}|{an}"
            # Critical no-overread boundary: score integers are not captured unless the identity is in the frozen 6943 set.
            if key not in eligible_key_set:
                continue
            m = SCORE_CAPTURE.search(raw)
            if m is None:
                raise RuntimeError(f"eligible score capture failed: {key}")
            gh = int(m.group("h")); ga = int(m.group("a"))
            out.append({
                "date": current_date.isoformat(),
                "calendar_year": current_date.year,
                "competition_family": rel.split("/", 1)[0],
                "league_key": league_key(rel),
                "home": home,
                "away": away,
                "home_norm": hn,
                "away_norm": an,
                "source_file": rel,
                "goals_home": gh,
                "goals_away": ga,
            })

    df = pd.DataFrame(out)
    if len(df) != EXPECTED_ELIGIBLE_COUNT:
        raise RuntimeError(f"numeric eligible count drift {len(df)} != {EXPECTED_ELIGIBLE_COUNT}")
    keys = [f"{r.date}|{r.home_norm}|{r.away_norm}" for r in df.itertuples(index=False)]
    if sha_lines(keys) != EXPECTED_ELIGIBLE_SHA:
        raise RuntimeError("numeric eligible identity SHA drift")
    if len(keys) != len(set(keys)):
        raise RuntimeError("numeric eligible duplicates")
    df["exact_total"] = df.goals_home.astype(int) + df.goals_away.astype(int)
    df["D"] = df.goals_home.astype(int) - df.goals_away.astype(int)
    return df.sort_values(["date", "competition_family", "home_norm", "away_norm", "source_file"]).reset_index(drop=True)


@dataclass
class Agg:
    n: int = 0
    s1: float = 0.0
    s2: float = 0.0
    def add(self, x: float) -> None:
        self.n += 1; self.s1 += float(x); self.s2 += float(x) * float(x)
    def mean(self) -> float:
        return self.s1 / self.n if self.n else float("nan")
    def sd(self) -> float:
        if not self.n:
            return float("nan")
        mu = self.s1 / self.n
        return math.sqrt(max(self.s2 / self.n - mu * mu, 0.0))


def build_external_features(numeric: pd.DataFrame) -> pd.DataFrame:
    team_gf: dict[str, Agg] = defaultdict(Agg)
    team_ga: dict[str, Agg] = defaultdict(Agg)
    league_tot: dict[str, Agg] = defaultdict(Agg)
    rows: list[dict] = []

    for d, day in numeric.groupby("date", sort=True):
        day = day.sort_values(["competition_family", "home_norm", "away_norm", "source_file"])
        day_features = []
        for r in day.itertuples(index=False):
            hk = f"{r.competition_family}|{r.home_norm}"
            ak = f"{r.competition_family}|{r.away_norm}"
            lg = str(r.league_key)
            hgf, hga, agf, aga, lt = team_gf[hk], team_ga[hk], team_gf[ak], team_ga[ak], league_tot[lg]
            day_features.append({
                "date": r.date,
                "calendar_year": int(r.calendar_year),
                "competition_family": r.competition_family,
                "league_key": lg,
                "home": r.home,
                "away": r.away,
                "source_file": r.source_file,
                "goals_home": int(r.goals_home),
                "goals_away": int(r.goals_away),
                "exact_total": int(r.exact_total),
                "D": int(r.D),
                "league_total_mean": lt.mean(),
                "league_total_sd": lt.sd(),
                "home_goals_for_mean": hgf.mean(),
                "home_goals_for_sd": hgf.sd(),
                "home_goals_against_mean": hga.mean(),
                "home_goals_against_sd": hga.sd(),
                "away_goals_for_mean": agf.mean(),
                "away_goals_for_sd": agf.sd(),
                "away_goals_against_mean": aga.mean(),
                "away_goals_against_sd": aga.sd(),
                "log1p_home_result_history_n": math.log1p(hgf.n),
                "log1p_away_result_history_n": math.log1p(agf.n),
            })
        rows.extend(day_features)
        # Strict same-date predict-before-update.
        for r in day.itertuples(index=False):
            hk = f"{r.competition_family}|{r.home_norm}"
            ak = f"{r.competition_family}|{r.away_norm}"
            team_gf[hk].add(int(r.goals_home)); team_ga[hk].add(int(r.goals_away))
            team_gf[ak].add(int(r.goals_away)); team_ga[ak].add(int(r.goals_home))
            league_tot[str(r.league_key)].add(int(r.exact_total))
    return pd.DataFrame(rows)


def metric_summary(rows: pd.DataFrame) -> dict:
    if len(rows) == 0:
        return {"n": 0, "baseline": None, "candidate": None, "delta": None}
    base = {k: float(rows[f"baseline_{k}"].mean()) for k in ["logloss", "brier", "rps", "top1"]}
    cand = {k: float(rows[f"candidate_{k}"].mean()) for k in ["logloss", "brier", "rps", "top1"]}
    delta = {k: float(cand[k] - base[k]) for k in base}
    return {"n": int(len(rows)), "baseline": base, "candidate": cand, "delta": delta}


def paired_bootstrap(delta: np.ndarray) -> dict:
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    for i in range(BOOT_REPS):
        idx = rng.integers(0, len(d), size=len(d))
        sims[i] = float(d[idx].mean())
    return {
        "n": int(len(d)), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0)),
    }


def run_confirmation(model, baseline_tables, numeric: pd.DataFrame, training_summary: dict, out_dir: Path) -> dict:
    tail_count = int((numeric.exact_total >= 7).sum())
    block_tail = {str(y): int(((numeric.calendar_year == y) & (numeric.exact_total >= 7)).sum()) for y in [2024, 2025]}
    family_tail_counts = numeric[numeric.exact_total >= 7].groupby("competition_family").size().to_dict()
    domains_any_tail = int(sum(int(v) > 0 for v in family_tail_counts.values()))
    coverage = {
        "pooled_tail": tail_count,
        "2024_tail": block_tail["2024"],
        "2025_tail": block_tail["2025"],
        "domains_with_any_tail": domains_any_tail,
        "pass_pooled_ge_150": tail_count >= COVERAGE_POOLED,
        "pass_2024_ge_50": block_tail["2024"] >= COVERAGE_BLOCK,
        "pass_2025_ge_50": block_tail["2025"] >= COVERAGE_BLOCK,
        "pass_domains_ge_3": domains_any_tail >= COVERAGE_DOMAINS_ANY_TAIL,
    }
    coverage_pass = all(v for k, v in coverage.items() if k.startswith("pass_"))
    if not coverage_pass:
        summary = {
            "schema_version": "C077B_INDEPENDENT_CONFIRMATION_V1",
            "status": "STOP_COVERAGE_CONFIRMATION_LABELS_CONSUMED",
            "training": training_summary,
            "confirmation_labels_consumed": EXPECTED_ELIGIBLE_COUNT,
            "coverage": coverage,
            "candidate_scored_against_baseline": False,
            "no_rescue_after_view": True,
            "formal_weight": 0,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary

    feat = build_external_features(numeric)
    tail = feat[feat.exact_total >= 7].copy().reset_index(drop=True)
    row_metrics = []
    mapping_failures = 0
    max_prob_resid = 0.0
    for r in tail.itertuples(index=False):
        x = pd.DataFrame([{f: getattr(r, f) for f in c077a.FEATURES}])
        pp = model.predict_proba(x)[0]
        cls = model.named_steps["logisticregression"].classes_.astype(int)
        p1 = float(pp[np.where(cls == 1)[0][0]])
        cand = c077a.binomial_vector(int(r.exact_total), p1)
        base = c077a.baseline_vector(int(r.exact_total), baseline_tables)
        cs = c077a.score_row(int(r.goals_home), cand)
        bs = c077a.score_row(int(r.goals_home), base)
        mf_c, pr_c = c077a.structural_audit(int(r.exact_total), cand)
        mf_b, pr_b = c077a.structural_audit(int(r.exact_total), base)
        mapping_failures += int(mf_c + mf_b)
        max_prob_resid = max(max_prob_resid, float(pr_c), float(pr_b))
        row_metrics.append({
            "date": r.date,
            "calendar_year": int(r.calendar_year),
            "competition_family": r.competition_family,
            "league_key": r.league_key,
            "home": r.home,
            "away": r.away,
            "T": int(r.exact_total),
            "H": int(r.goals_home),
            "D": int(r.D),
            **{f"baseline_{k}": float(v) for k, v in bs.items()},
            **{f"candidate_{k}": float(v) for k, v in cs.items()},
            "delta_logloss": float(cs["logloss"] - bs["logloss"]),
        })
    rm = pd.DataFrame(row_metrics)
    rm.to_csv(out_dir / "row_metrics.csv", index=False)

    pooled = metric_summary(rm)
    blocks = {str(y): metric_summary(rm[rm.calendar_year == y]) for y in [2024, 2025]}
    boot = paired_bootstrap(rm.delta_logloss.to_numpy(float))

    domain_reports = {}
    eligible_domain_names = []
    domain_wins = 0
    for dom, g in rm.groupby("competition_family", sort=True):
        rep = metric_summary(g)
        n = int(len(g))
        vote_eligible = n >= DOMAIN_MIN_TAIL
        d_ll = float(rep["delta"]["logloss"])
        if vote_eligible:
            eligible_domain_names.append(dom)
            domain_wins += int(d_ll < 0)
        domain_reports[dom] = {
            **rep,
            "vote_eligible": vote_eligible,
            "vote_win": bool(vote_eligible and d_ll < 0),
        }
    eligible_domains = len(eligible_domain_names)
    strict_majority = eligible_domains >= DOMAIN_VOTE_MIN_ELIGIBLE and domain_wins > eligible_domains / 2

    gates = {
        "coverage": True,
        "pooled_dlogloss_lt_zero": float(pooled["delta"]["logloss"]) < 0,
        "bootstrap90_upper_lt_zero": float(boot["ci90_high"]) < 0,
        "pooled_brier_delta_le_zero": float(pooled["delta"]["brier"]) <= 0,
        "pooled_rps_delta_le_zero": float(pooled["delta"]["rps"]) <= 0,
        "2024_dlogloss_lt_zero": float(blocks["2024"]["delta"]["logloss"]) < 0,
        "2025_dlogloss_lt_zero": float(blocks["2025"]["delta"]["logloss"]) < 0,
        "eligible_domains_ge_3": eligible_domains >= DOMAIN_VOTE_MIN_ELIGIBLE,
        "strict_majority_eligible_domains_win": bool(strict_majority),
        "probability_sum_residual_le_1e_10": max_prob_resid <= 1e-10,
        "mapping_failures_zero": mapping_failures == 0,
        "source_identity_sha_exact": True,
        "eligible_identity_sha_exact": True,
        "prior_exclusion_overlap_zero": True,
    }
    status = "CONFIRMATION_PASS" if all(gates.values()) else "CONFIRMATION_FAIL_PARK"
    summary = {
        "schema_version": "C077B_INDEPENDENT_CONFIRMATION_V1",
        "status": status,
        "training": training_summary,
        "confirmation_labels_consumed": EXPECTED_ELIGIBLE_COUNT,
        "coverage": coverage,
        "pooled": pooled,
        "bootstrap": boot,
        "time_blocks": blocks,
        "domain_vote": {
            "minimum_tail_for_vote": DOMAIN_MIN_TAIL,
            "eligible_domain_count": eligible_domains,
            "eligible_domains": eligible_domain_names,
            "domain_wins": int(domain_wins),
            "strict_majority_pass": bool(strict_majority),
            "all_domains": domain_reports,
        },
        "structural_audit": {
            "mapping_failures": int(mapping_failures),
            "max_probability_sum_abs_residual": float(max_prob_resid),
        },
        "gates": gates,
        "no_rescue_after_view": True,
        "exact_tail_created": False,
        "unified_matrix_generated": False,
        "formal_weight": 0,
        "automatic_formal_promotion": False,
        "CURRENT_change": False,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["preflight", "confirm"])
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--confirmation-source", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()

    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    spec = load_json(SPEC_PATH)
    if not spec.get("frozen_before_numeric_confirmation_label_access"):
        raise RuntimeError("execution spec is not frozen prelabel")
    source_rows, eligible = reconstruct_zero_label_population(Path(a.confirmation_source))
    model, baseline_tables, training_summary = train_frozen_parent(Path(a.fixtures), Path(a.stats))
    parser_test = score_parser_self_test()

    if a.mode == "preflight":
        summary = {
            "schema_version": "C077B_INDEPENDENT_CONFIRMATION_PREFLIGHT_V1",
            "status": "PASS_PREFLIGHT_NO_CONFIRMATION_LABELS",
            "source_identity_count": len(source_rows),
            "source_identity_sha256": EXPECTED_SOURCE_SHA,
            "eligible_identity_count": len(eligible),
            "eligible_identity_sha256": EXPECTED_ELIGIBLE_SHA,
            "training": training_summary,
            "score_parser_synthetic_test": parser_test,
            "confirmation_numeric_score_values_captured": False,
            "confirmation_total_goals_computed": False,
            "confirmation_tail_membership_computed": False,
            "confirmation_candidate_scored": False,
            "formal_weight": 0,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    numeric = parse_numeric_scores_for_frozen_eligible(Path(a.confirmation_source), eligible)
    summary = run_confirmation(model, baseline_tables, numeric, training_summary, out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
