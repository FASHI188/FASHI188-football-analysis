#!/usr/bin/env python3
"""Strict 20k pure-results experiment: same-competition baseline vs cross-competition latent strength + veto.

Research only. No odds, market prices, lineups, future results, or manual match-level probability edits.
All matches on a calendar date are predicted before any result from that date updates state.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

SOURCE_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULT_DIR = ROOT / "results"
N_SNAPSHOT = 20_000
PART_SIZE = 5_000
GLOBAL_HOME_PRIOR = 1.45
GLOBAL_AWAY_PRIOR = 1.20
COMP_PRIOR_MATCHES = 30.0
TEAM_PRIOR_MATCHES = 6.0
LATENT_HALF_LIFE_DAYS = 240.0
LATENT_LR = 0.06
LATENT_BLEND = 0.50
MAX_GOALS = 10

SNAP_FIELDS = [
    "date", "game_id", "competition_id", "season", "competition_type",
    "home_club_id", "away_club_id", "home_club_name", "away_club_name",
    "home_goals", "away_goals",
]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _download() -> bytes:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "football3-research/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response:
        return response.read()


def _pick(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def _goal(value: str) -> int | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or abs(number - round(number)) > 1e-9:
        return None
    return int(round(number))


def _club_id(value: str) -> str:
    token = str(value or "").strip()
    if not token or token.lower() in {"nan", "none", "null"}:
        return ""
    if token.endswith(".0") and token[:-2].isdigit():
        token = token[:-2]
    return token


def freeze_snapshot() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_gz = _download()
    source_sha = _sha256_bytes(raw_gz)
    text = gzip.decompress(raw_gz).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    source_columns = list(reader.fieldnames or [])
    valid: list[dict[str, str | int]] = []
    bad = Counter()
    source_rows = 0
    seen_game_ids: set[str] = set()
    for row in reader:
        source_rows += 1
        ds = _pick(row, "date", "Date", "game_date")[:10]
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            bad["date"] += 1
            continue
        gid = _pick(row, "game_id", "match_id", "id")
        comp = _pick(row, "competition_id", "competition_code", "league_id")
        ctype = _pick(row, "competition_type", "type")
        hid = _club_id(_pick(row, "home_club_id", "home_team_id", "home_id"))
        aid = _club_id(_pick(row, "away_club_id", "away_team_id", "away_id"))
        hg = _goal(_pick(row, "home_club_goals", "home_goals", "FTHG"))
        ag = _goal(_pick(row, "away_club_goals", "away_goals", "FTAG"))
        if not gid or not comp or not hid or not aid or hid == aid:
            bad["identity"] += 1
            continue
        if gid in seen_game_ids:
            bad["duplicate_game_id"] += 1
            continue
        if hg is None or ag is None:
            bad["score"] += 1
            continue
        if ctype == "national_team_competition":
            bad["national_team"] += 1
            continue
        seen_game_ids.add(gid)
        valid.append({
            "date": d.isoformat(),
            "game_id": gid,
            "competition_id": comp,
            "season": _pick(row, "season", "season_name"),
            "competition_type": ctype,
            "home_club_id": hid,
            "away_club_id": aid,
            "home_club_name": _pick(row, "home_club_name", "home_team_name", "home_team"),
            "away_club_name": _pick(row, "away_club_name", "away_team_name", "away_team"),
            "home_goals": hg,
            "away_goals": ag,
        })
    valid.sort(key=lambda r: (str(r["date"]), str(r["game_id"])))
    if len(valid) < N_SNAPSHOT:
        raise RuntimeError(f"only {len(valid)} valid club matches; need {N_SNAPSHOT}")
    chosen = valid[-N_SNAPSHOT:]
    assert len(chosen) == N_SNAPSHOT
    part_meta = []
    for index, start in enumerate(range(0, N_SNAPSHOT, PART_SIZE), 1):
        part = chosen[start:start + PART_SIZE]
        path = DATA_DIR / f"matches_20000_part{index:02d}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SNAP_FIELDS)
            writer.writeheader()
            writer.writerows(part)
        part_meta.append({"path": str(path.relative_to(ROOT)), "rows": len(part), "sha256": _sha256_file(path)})
    manifest = {
        "schema_version": "football3-top1-20k-r1",
        "status": "FROZEN_20000",
        "source_url": SOURCE_URL,
        "source_compressed_sha256": source_sha,
        "source_compressed_bytes": len(raw_gz),
        "source_rows": source_rows,
        "source_columns": source_columns,
        "valid_club_rows": len(valid),
        "selection": "latest 20000 valid completed club matches sorted by (date, game_id)",
        "snapshot_rows": len(chosen),
        "first_date": chosen[0]["date"],
        "last_date": chosen[-1]["date"],
        "parts": part_meta,
        "excluded_counts": dict(bad),
        "market_or_odds_columns_persisted": False,
        "target_result_same_date_update_allowed": False,
    }
    (DATA_DIR / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("status", "source_rows", "valid_club_rows", "snapshot_rows", "first_date", "last_date")}, indent=2))
    return manifest


@dataclass
class VenueStat:
    n: int = 0
    gf: float = 0.0
    ga: float = 0.0


@dataclass
class LatentTeam:
    attack: float = 0.0
    defence: float = 0.0  # positive means more goals conceded than average
    matches: int = 0
    last_date: date | None = None


class OnlineState:
    def __init__(self) -> None:
        self.comp_n: Counter[str] = Counter()
        self.comp_hg: defaultdict[str, float] = defaultdict(float)
        self.comp_ag: defaultdict[str, float] = defaultdict(float)
        self.venue: defaultdict[tuple[str, str, str], VenueStat] = defaultdict(VenueStat)
        self.latent: defaultdict[str, LatentTeam] = defaultdict(LatentTeam)

    def comp_means(self, comp: str) -> tuple[float, float]:
        n = float(self.comp_n[comp])
        h = (self.comp_hg[comp] + COMP_PRIOR_MATCHES * GLOBAL_HOME_PRIOR) / (n + COMP_PRIOR_MATCHES)
        a = (self.comp_ag[comp] + COMP_PRIOR_MATCHES * GLOBAL_AWAY_PRIOR) / (n + COMP_PRIOR_MATCHES)
        return max(0.2, h), max(0.2, a)

    def _rate(self, stat: VenueStat, attr: str, prior: float) -> float:
        value = stat.gf if attr == "gf" else stat.ga
        return (value + TEAM_PRIOR_MATCHES * prior) / (stat.n + TEAM_PRIOR_MATCHES)

    def predict_a(self, row: dict) -> dict:
        comp = row["competition_id"]
        hid, aid = row["home_club_id"], row["away_club_id"]
        lh, la = self.comp_means(comp)
        hs = self.venue[(comp, hid, "H")]
        av = self.venue[(comp, aid, "A")]
        home_attack = self._rate(hs, "gf", lh) / lh
        home_def = self._rate(hs, "ga", la) / la
        away_attack = self._rate(av, "gf", la) / la
        away_def = self._rate(av, "ga", lh) / lh
        mu_h = _clamp(lh * home_attack * away_def, 0.15, 4.5)
        mu_a = _clamp(la * away_attack * home_def, 0.15, 4.5)
        return _distribution(mu_h, mu_a, {
            "home_history": hs.n, "away_history": av.n, "comp_history": self.comp_n[comp]
        })

    def _decayed(self, club: str, d: date) -> tuple[float, float, int]:
        state = self.latent[club]
        if state.last_date is None:
            return state.attack, state.defence, state.matches
        days = max(0, (d - state.last_date).days)
        factor = math.exp(-math.log(2.0) * days / LATENT_HALF_LIFE_DAYS)
        return state.attack * factor, state.defence * factor, state.matches

    def predict_b(self, row: dict, a_pred: dict) -> dict:
        d = date.fromisoformat(row["date"])
        comp = row["competition_id"]
        hid, aid = row["home_club_id"], row["away_club_id"]
        lh, la = self.comp_means(comp)
        hatt, hdef, hn = self._decayed(hid, d)
        aatt, adef, an = self._decayed(aid, d)
        latent_h = _clamp(lh * math.exp(hatt + adef), 0.15, 4.5)
        latent_a = _clamp(la * math.exp(aatt + hdef), 0.15, 4.5)
        # Fixed geometric blend: A supplies venue context; latent track supplies opponent-adjusted all-competition strength.
        mu_h = math.exp((1.0 - LATENT_BLEND) * math.log(a_pred["mu_home"]) + LATENT_BLEND * math.log(latent_h))
        mu_a = math.exp((1.0 - LATENT_BLEND) * math.log(a_pred["mu_away"]) + LATENT_BLEND * math.log(latent_a))
        return _distribution(mu_h, mu_a, {
            "home_history": hn, "away_history": an, "comp_history": self.comp_n[comp]
        })

    def _touch_latent(self, club: str, d: date) -> LatentTeam:
        state = self.latent[club]
        if state.last_date is not None:
            days = max(0, (d - state.last_date).days)
            factor = math.exp(-math.log(2.0) * days / LATENT_HALF_LIFE_DAYS)
            state.attack *= factor
            state.defence *= factor
        state.last_date = d
        return state

    def update(self, row: dict, b_pred: dict) -> None:
        d = date.fromisoformat(row["date"])
        comp = row["competition_id"]
        hid, aid = row["home_club_id"], row["away_club_id"]
        hg, ag = int(row["home_goals"]), int(row["away_goals"])
        h = self.venue[(comp, hid, "H")]
        a = self.venue[(comp, aid, "A")]
        h.n += 1; h.gf += hg; h.ga += ag
        a.n += 1; a.gf += ag; a.ga += hg
        self.comp_n[comp] += 1; self.comp_hg[comp] += hg; self.comp_ag[comp] += ag
        hs = self._touch_latent(hid, d)
        as_ = self._touch_latent(aid, d)
        # Poisson-score gradient residual, bounded for robustness to 7-0 type outliers.
        eh = _clamp((hg - b_pred["latent_mu_home"]) / (1.0 + b_pred["latent_mu_home"]), -2.0, 2.0)
        ea = _clamp((ag - b_pred["latent_mu_away"]) / (1.0 + b_pred["latent_mu_away"]), -2.0, 2.0)
        hs.attack = _clamp(hs.attack + LATENT_LR * eh, -1.2, 1.2)
        as_.defence = _clamp(as_.defence + LATENT_LR * eh, -1.2, 1.2)
        as_.attack = _clamp(as_.attack + LATENT_LR * ea, -1.2, 1.2)
        hs.defence = _clamp(hs.defence + LATENT_LR * ea, -1.2, 1.2)
        hs.matches += 1; as_.matches += 1


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def _poisson(mu: float) -> list[float]:
    out = [math.exp(-mu)]
    for k in range(1, MAX_GOALS + 1):
        out.append(out[-1] * mu / k)
    return out


def _distribution(mu_h: float, mu_a: float, meta: dict) -> dict:
    hp, ap = _poisson(mu_h), _poisson(mu_a)
    h = d = a = total = 0.0
    best = (-1.0, 0, 0)
    for i, pi in enumerate(hp):
        for j, pj in enumerate(ap):
            p = pi * pj
            total += p
            if i > j: h += p
            elif i == j: d += p
            else: a += p
            if p > best[0]: best = (p, i, j)
    h /= total; d /= total; a /= total
    probs = [h, d, a]
    order = sorted(range(3), key=lambda i: probs[i], reverse=True)
    entropy = -sum(p * math.log(max(p, 1e-15)) for p in probs)
    return {
        "p_home": h, "p_draw": d, "p_away": a,
        "top1": order[0], "margin": probs[order[0]] - probs[order[1]], "entropy": entropy,
        "mu_home": mu_h, "mu_away": mu_a, "mu_total": mu_h + mu_a,
        "score_top1": f"{best[1]}-{best[2]}", "score_top1_p": best[0] / total,
        **meta,
    }


def _load_snapshot() -> list[dict]:
    rows = []
    for path in sorted(DATA_DIR.glob("matches_20000_part*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                r["home_goals"] = int(r["home_goals"]); r["away_goals"] = int(r["away_goals"])
                rows.append(r)
    rows.sort(key=lambda r: (r["date"], r["game_id"]))
    if len(rows) != N_SNAPSHOT:
        raise RuntimeError(f"snapshot rows={len(rows)} expected={N_SNAPSHOT}")
    return rows


def _actual(row: dict) -> int:
    return 0 if row["home_goals"] > row["away_goals"] else 1 if row["home_goals"] == row["away_goals"] else 2


def _metrics(preds: list[dict], key: str) -> dict:
    hits = 0; ll = br = rps = 0.0; draws = draw_hits = actual_draws = 0
    score_counts = Counter(); score_hits = 0
    for x in preds:
        p = x[key]; y = x["actual"]
        vec = [p["p_home"], p["p_draw"], p["p_away"]]
        hits += p["top1"] == y
        ll += -math.log(max(vec[y], 1e-15))
        br += sum((vec[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        rps += ((vec[0] - (1.0 if y == 0 else 0.0)) ** 2 + ((vec[0]+vec[1]) - (1.0 if y <= 1 else 0.0)) ** 2) / 2.0
        draws += p["top1"] == 1; draw_hits += p["top1"] == 1 and y == 1; actual_draws += y == 1
        score_counts[p["score_top1"]] += 1
        score_hits += p["score_top1"] == x["actual_score"]
    n = len(preds)
    return {
        "count": n, "hits": hits, "top1_accuracy": hits/n, "logloss": ll/n, "brier": br/n, "rps": rps/n,
        "draw_top1_picks": draws, "actual_draws": actual_draws,
        "draw_top1_precision": draw_hits/draws if draws else None,
        "draw_recall": draw_hits/actual_draws if actual_draws else None,
        "exact_score_top1_accuracy": score_hits/n,
        "score_top1_most_common": score_counts.most_common(10),
        "score_top1_1_1_fraction": score_counts["1-1"]/n,
    }


def _gate_features(p: dict) -> list[float]:
    probs = sorted([p["p_home"], p["p_draw"], p["p_away"]], reverse=True)
    return [
        probs[0], probs[0]-probs[1], p["entropy"], p["p_draw"], abs(p["mu_home"]-p["mu_away"]),
        p["mu_total"], math.log1p(p["home_history"]), math.log1p(p["away_history"]), math.log1p(p["comp_history"]),
    ]


def _selected_stat(rows: list[dict], scores: list[float], threshold: float) -> dict:
    chosen = [(r, s) for r, s in zip(rows, scores) if s >= threshold]
    hits = sum(r["b"]["top1"] == r["actual"] for r, _ in chosen)
    return {"count": len(chosen), "coverage": len(chosen)/len(rows), "hits": hits, "accuracy": hits/len(chosen) if chosen else None, "threshold": threshold}


def run_experiment() -> dict:
    rows = _load_snapshot()
    state = OnlineState()
    preds: list[dict] = []
    by_date: defaultdict[str, list[dict]] = defaultdict(list)
    for row in rows: by_date[row["date"]].append(row)
    for ds in sorted(by_date):
        pending = []
        for row in sorted(by_date[ds], key=lambda r: r["game_id"]):
            pa = state.predict_a(row)
            # Keep unblended latent means separately for its online gradient update.
            d = date.fromisoformat(row["date"]); comp=row["competition_id"]; hid=row["home_club_id"]; aid=row["away_club_id"]
            lh,la=state.comp_means(comp); hatt,hdef,_=state._decayed(hid,d); aatt,adef,_=state._decayed(aid,d)
            latent_h=_clamp(lh*math.exp(hatt+adef),0.15,4.5); latent_a=_clamp(la*math.exp(aatt+hdef),0.15,4.5)
            pb = state.predict_b(row, pa); pb["latent_mu_home"]=latent_h; pb["latent_mu_away"]=latent_a
            record = {"date": ds, "game_id": row["game_id"], "actual": _actual(row), "actual_score": f"{row['home_goals']}-{row['away_goals']}", "a": pa, "b": pb}
            preds.append(record); pending.append((row,pb))
        # PIT rule: only now are this date's results visible.
        for row,pb in pending: state.update(row,pb)

    burn_end, train_end, val_end = 4_000, 12_000, 16_000
    gate_train = preds[burn_end:train_end]
    validation = preds[train_end:val_end]
    test = preds[val_end:]
    assert (len(gate_train),len(validation),len(test)) == (8000,4000,4000)

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    X = [_gate_features(r["b"]) for r in gate_train]
    y = [int(r["b"]["top1"] == r["actual"]) for r in gate_train]
    gate = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000, random_state=0))
    gate.fit(X,y)
    val_scores = gate.predict_proba([_gate_features(r["b"]) for r in validation])[:,1].tolist()
    test_scores = gate.predict_proba([_gate_features(r["b"]) for r in test])[:,1].tolist()
    thresholds = {}
    gates = {}
    sorted_val = sorted(val_scores)
    for target in (0.90,0.80,0.70):
        q = max(0, min(len(sorted_val)-1, int((1.0-target)*len(sorted_val))))
        th = sorted_val[q]
        thresholds[str(target)] = th
        gates[str(target)] = {
            "validation": _selected_stat(validation,val_scores,th),
            "test": _selected_stat(test,test_scores,th),
        }

    a_test = _metrics(test,"a"); b_test = _metrics(test,"b")
    summary = {
        "schema_version": "football3-top1-20k-opponent-veto-r1",
        "status": "COMPLETE",
        "classification": "RESEARCH_ONLY_NOT_VALIDATED_NOT_PROMOTED",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": N_SNAPSHOT, "same_date_results_withheld": True, "chronological_split": True,
            "burn_in_rows": 4000, "gate_train_rows": 8000, "validation_rows": 4000, "untouched_test_rows": 4000,
            "odds_used": False, "market_prices_used": False, "manual_match_probability_adjustment": False,
            "target_test_labels_used_for_training_or_threshold": False,
        },
        "models": {
            "A": "same-competition venue GF/GA Bayesian pure-results baseline",
            "B": "A plus fixed 50% log-space blend with recency-decayed cross-competition opponent-adjusted club attack/defence latent state",
            "C": "B plus train-only logistic correctness gate; validation fixes coverage thresholds; test untouched",
        },
        "fixed_hyperparameters": {
            "competition_prior_matches": COMP_PRIOR_MATCHES, "team_prior_matches": TEAM_PRIOR_MATCHES,
            "latent_half_life_days": LATENT_HALF_LIFE_DAYS, "latent_lr": LATENT_LR, "latent_blend": LATENT_BLEND,
        },
        "test": {
            "A": a_test, "B": b_test,
            "delta_B_minus_A": {
                "top1_pp": 100*(b_test["top1_accuracy"]-a_test["top1_accuracy"]),
                "logloss": b_test["logloss"]-a_test["logloss"], "brier": b_test["brier"]-a_test["brier"], "rps": b_test["rps"]-a_test["rps"],
            },
            "C_selective": gates,
        },
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary["test"], ensure_ascii=False, indent=2))
    return summary


def verify() -> None:
    manifest=json.loads((DATA_DIR/"source_manifest.json").read_text(encoding="utf-8"))
    summary=json.loads((RESULT_DIR/"summary.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_rows"]==20_000
    assert sum(x["rows"] for x in manifest["parts"])==20_000
    assert len(manifest["parts"])==4 and all(x["rows"]==5000 for x in manifest["parts"])
    assert manifest["market_or_odds_columns_persisted"] is False
    assert summary["governance"]["odds_used"] is False and summary["governance"]["market_prices_used"] is False
    assert summary["governance"]["untouched_test_rows"]==4000
    assert summary["formal_weight"]==0
    print("VERIFY_OK 20000 rows / 4 parts / pure-results / untouched test=4000")


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=["freeze","run","verify"]); args=parser.parse_args()
    if args.command=="freeze": freeze_snapshot()
    elif args.command=="run": run_experiment()
    else: verify()
    return 0

if __name__=="__main__": raise SystemExit(main())
