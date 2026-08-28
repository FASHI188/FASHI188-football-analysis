#!/usr/bin/env python3
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
TMP = HERE / "data"
OF = "https://raw.githubusercontent.com/openfootball/champions-league/master"
SEASONS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
FILES = ["cl.txt", "clq.txt", "el.txt", "elq.txt", "conf.txt", "confq.txt"]
TRAIN_SEASONS = {"2020-21", "2021-22", "2022-23"}
VAL_SEASONS = {"2023-24"}
TEST_SEASONS = {"2024-25", "2025-26"}
ELO_K = 20.0
MODEL_C = 0.5
MIN_TEST = 200
MIN_POSITIVE_BLOCKS = 3
MAX_NEGATIVE_BLOCKS = 1
BASE_NAMES = ["elo_diff_400", "gap_days_14", "is_qualification", "is_cl", "is_el", "is_conf"]
STATE_NAMES = BASE_NAMES + ["first_margin_home", "first_abs_margin", "first_total_goals", "first_tied"]


def download(url: str, path: Path, required: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43e6"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as w:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                w.write(b)
        return True
    except Exception:
        if required:
            raise
        return False


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm(s: str) -> str:
    s = re.sub(r"\s*\([A-Z]{3}\)\s*$", "", str(s).strip())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    drop = {"fc", "cf", "afc", "fk", "sk", "nk", "sc", "ac", "as", "ss", "rsc", "tc", "club", "clube", "futbol", "football", "calcio", "fussball"}
    return " ".join(x for x in s.split() if x not in drop)


MONTH = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
DAYWORDS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def parse_score_90(line: str, outer_h: int, outer_a: int) -> tuple[int, int]:
    # OpenFootball writes extra-time/penalty matches as e.g.
    # '3-2 pen. 2-1 a.e.t. (2-1, 1-1)' or '3-1 a.e.t. (2-1, 1-0)'.
    # In those cases the first pair inside the trailing parentheses is the 90-minute score.
    if "a.e.t." in line or " pen." in line:
        tails = re.findall(r"\((\d+)-(\d+)\s*,\s*(\d+)-(\d+)\)", line)
        if tails:
            return int(tails[-1][0]), int(tails[-1][1])
    return int(outer_h), int(outer_a)


def parse_file(text: str, season: str, file: str) -> list[dict]:
    out = []
    stage = "UNKNOWN"
    cur_date = None
    year = None
    season_start = int(season[:4])
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("="):
            continue
        if line.startswith("▪"):
            stage = line.lstrip("▪").strip()
            continue
        bits = line.split()
        if len(bits) >= 3 and bits[0][:3].lower() in DAYWORDS and bits[1][:3].lower() in MONTH:
            mo = MONTH[bits[1][:3].lower()]
            try:
                day = int(re.sub(r"\D", "", bits[2]))
            except Exception:
                day = None
            explicit = int(bits[3]) if len(bits) >= 4 and re.fullmatch(r"20\d{2}", bits[3]) else None
            if day:
                if explicit is not None:
                    year = explicit
                elif year is None:
                    year = season_start if mo >= 7 else season_start + 1
                elif cur_date is not None and mo < cur_date.month - 6:
                    year += 1
                cur_date = datetime(year, mo, day, tzinfo=timezone.utc).date()
                continue
        m = re.match(r"^(?:\d{1,2}:\d{2}\s+)?(.+?)\s+v\s+(.+?)\s+(\d+)-(\d+)(?:\s|$)", line)
        if not m or cur_date is None:
            continue
        h90, a90 = parse_score_90(line, int(m.group(3)), int(m.group(4)))
        out.append({
            "season": season,
            "file": file,
            "date": cur_date.isoformat(),
            "stage": stage,
            "home": m.group(1).strip(),
            "away": m.group(2).strip(),
            "home_n": norm(m.group(1)),
            "away_n": norm(m.group(2)),
            "g90_home": h90,
            "g90_away": a90,
            "had_extra_time_or_penalties": bool("a.e.t." in line or " pen." in line),
            "raw_line": line,
        })
    return out


def result_class(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def build_source() -> tuple[list[dict], dict]:
    TMP.mkdir(parents=True, exist_ok=True)
    matches = []
    meta = {}
    for season in SEASONS:
        for file in FILES:
            p = TMP / f"{season}_{file}"
            ok = download(f"{OF}/{season}/{file}", p, required=False)
            if not ok:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            rows = parse_file(text, season, file)
            matches.extend(rows)
            meta[f"{season}/{file}"] = {"sha256": sha256(p), "parsed_matches": len(rows), "size_bytes": p.stat().st_size}
    return matches, meta


def exact_pairs(matches: list[dict]) -> list[tuple[dict, dict]]:
    groups = defaultdict(list)
    for x in matches:
        key = (x["season"], x["file"], x["stage"], tuple(sorted((x["home_n"], x["away_n"]))))
        groups[key].append(x)
    pairs = []
    for xs in groups.values():
        xs = sorted(xs, key=lambda z: z["date"])
        if len(xs) != 2:
            continue
        a, b = xs
        if a["home_n"] == b["away_n"] and a["away_n"] == b["home_n"] and a["date"] < b["date"]:
            pairs.append((a, b))
    return pairs


def elo_pre_map(matches: list[dict]) -> dict[tuple[str, str, str, str], float]:
    ratings = defaultdict(lambda: 1500.0)
    pre = {}
    rows = sorted(matches, key=lambda x: (x["date"], x["season"], x["file"], x["home_n"], x["away_n"]))
    for x in rows:
        hk, ak = x["home_n"], x["away_n"]
        rh, ra = ratings[hk], ratings[ak]
        key = (x["season"], x["file"], x["date"], hk + "||" + ak)
        pre[key] = rh - ra
        exp_h = 1.0 / (1.0 + 10.0 ** (-(rh - ra) / 400.0))
        y = result_class(x["g90_home"], x["g90_away"])
        score_h = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
        delta = ELO_K * (score_h - exp_h)
        ratings[hk] += delta
        ratings[ak] -= delta
    return pre


def competition_flags(file: str) -> tuple[float, float, float, float]:
    q = 1.0 if file.endswith("q.txt") else 0.0
    if file.startswith("cl"):
        return q, 1.0, 0.0, 0.0
    if file.startswith("el"):
        return q, 0.0, 1.0, 0.0
    return q, 0.0, 0.0, 1.0


def make_dataset(matches: list[dict]) -> list[dict]:
    pre = elo_pre_map(matches)
    rows = []
    for leg1, leg2 in exact_pairs(matches):
        gap = (datetime.fromisoformat(leg2["date"]) - datetime.fromisoformat(leg1["date"])).days
        if gap < 2 or gap > 35:
            continue
        key = (leg2["season"], leg2["file"], leg2["date"], leg2["home_n"] + "||" + leg2["away_n"])
        elo_diff = float(pre.get(key, 0.0))
        # Current home was away in leg 1; positive means current home leads the aggregate before kickoff.
        margin_home = float(leg1["g90_away"] - leg1["g90_home"])
        total = float(leg1["g90_home"] + leg1["g90_away"])
        q, cl, el, conf = competition_flags(leg2["file"])
        base = {
            "elo_diff_400": elo_diff / 400.0,
            "gap_days_14": float(gap) / 14.0,
            "is_qualification": q,
            "is_cl": cl,
            "is_el": el,
            "is_conf": conf,
        }
        state = {
            **base,
            "first_margin_home": margin_home,
            "first_abs_margin": abs(margin_home),
            "first_total_goals": total,
            "first_tied": 1.0 if margin_home == 0 else 0.0,
        }
        rows.append({
            "season": leg2["season"], "date": leg2["date"], "file": leg2["file"], "stage": leg2["stage"],
            "home": leg2["home"], "away": leg2["away"],
            "y": result_class(leg2["g90_home"], leg2["g90_away"]),
            "score90": f"{leg2['g90_home']}-{leg2['g90_away']}",
            "leg1_score90": f"{leg1['g90_home']}-{leg1['g90_away']}",
            "leg1_margin_home": margin_home,
            "base": base, "state": state,
            "target_had_extra_time_or_penalties": leg2["had_extra_time_or_penalties"],
        })
    return sorted(rows, key=lambda x: (x["date"], x["file"], x["home"], x["away"]))


def fit(train: list[dict], key: str, names: list[str]) -> Pipeline:
    X = np.asarray([[float(x[key][n]) for n in names] for x in train], dtype=float)
    y = np.asarray([int(x["y"]) for x in train], dtype=int)
    if len(set(y.tolist())) < 3:
        raise RuntimeError("training split lacks all three classes")
    model = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=MODEL_C, max_iter=3000, solver="lbfgs")),
    ])
    model.fit(X, y)
    return model


def score(model: Pipeline, rows: list[dict], key: str, names: list[str]) -> list[dict]:
    X = np.asarray([[float(x[key][n]) for n in names] for x in rows], dtype=float)
    P0 = model.predict_proba(X)
    classes = list(model.named_steps["clf"].classes_)
    P = np.zeros((len(rows), 3), dtype=float)
    for j, c in enumerate(classes):
        P[:, int(c)] = P0[:, j]
    out = []
    for x, p in zip(rows, P):
        q = p / p.sum()
        out.append({"date": x["date"], "season": x["season"], "y": int(x["y"]), "P": q, "source": x})
    return out


def metrics(scored: list[dict]) -> dict:
    if not scored:
        return {"n": 0}
    y = np.asarray([x["y"] for x in scored], dtype=int)
    P = np.asarray([x["P"] for x in scored], dtype=float)
    ptrue = np.clip(P[np.arange(len(y)), y], 1e-12, 1.0)
    one = np.eye(3)[y]
    hits = int(np.sum(np.argmax(P, axis=1) == y))
    # Ranked probability score on ordered H-D-A classes.
    cP = np.cumsum(P, axis=1)[:, :2]
    cY = np.cumsum(one, axis=1)[:, :2]
    return {
        "n": len(y),
        "hits": hits,
        "top1_accuracy": float(hits / len(y)),
        "logloss": float(-np.mean(np.log(ptrue))),
        "brier": float(np.mean(np.sum((P - one) ** 2, axis=1))),
        "rps": float(np.mean(np.sum((cP - cY) ** 2, axis=1) / 2.0)),
        "actual_home": int(np.sum(y == 0)), "actual_draw": int(np.sum(y == 1)), "actual_away": int(np.sum(y == 2)),
        "pick_home": int(np.sum(np.argmax(P, axis=1) == 0)), "pick_draw": int(np.sum(np.argmax(P, axis=1) == 1)), "pick_away": int(np.sum(np.argmax(P, axis=1) == 2)),
    }


def delta(a: list[dict], b: list[dict]) -> dict:
    ma, mb = metrics(a), metrics(b)
    return {
        "hits": mb["hits"] - ma["hits"],
        "accuracy_pp": 100.0 * (mb["top1_accuracy"] - ma["top1_accuracy"]),
        "logloss": mb["logloss"] - ma["logloss"],
        "brier": mb["brier"] - ma["brier"],
        "rps": mb["rps"] - ma["rps"],
    }


def time_blocks(base: list[dict], cand: list[dict], n: int = 4) -> list[dict]:
    idxs = np.array_split(np.arange(len(base)), n)
    out = []
    for idx in idxs:
        a = [base[int(i)] for i in idx]
        b = [cand[int(i)] for i in idx]
        d = delta(a, b)
        out.append({"first_date": a[0]["date"], "last_date": a[-1]["date"], "n": len(a), **d})
    return out


def margin_table(rows: list[dict]) -> dict:
    def band(m: float) -> str:
        if m <= -2: return "trail_2plus"
        if m == -1: return "trail_1"
        if m == 0: return "tied"
        if m == 1: return "lead_1"
        return "lead_2plus"
    g = defaultdict(list)
    for x in rows:
        g[band(float(x["leg1_margin_home"]))].append(int(x["y"]))
    out = {}
    for k, ys in g.items():
        a = np.asarray(ys, dtype=int)
        out[k] = {"n": len(ys), "home": float(np.mean(a == 0)), "draw": float(np.mean(a == 1)), "away": float(np.mean(a == 2))}
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    matches, source_meta = build_source()
    data = make_dataset(matches)
    train = [x for x in data if x["season"] in TRAIN_SEASONS]
    val = [x for x in data if x["season"] in VAL_SEASONS]
    test = [x for x in data if x["season"] in TEST_SEASONS]
    if len(test) < MIN_TEST:
        raise RuntimeError(f"undersized test cohort {len(test)} < {MIN_TEST}")
    mb = fit(train, "base", BASE_NAMES)
    ms = fit(train, "state", STATE_NAMES)
    vb, vs = score(mb, val, "base", BASE_NAMES), score(ms, val, "state", STATE_NAMES)
    tb, ts = score(mb, test, "base", BASE_NAMES), score(ms, test, "state", STATE_NAMES)
    vd, td = delta(vb, vs), delta(tb, ts)
    blocks = time_blocks(tb, ts, 4)
    pos = sum(x["logloss"] < 0 for x in blocks)
    neg = sum(x["logloss"] > 0 for x in blocks)
    passed = bool(
        vd["logloss"] < 0 and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
        and pos >= MIN_POSITIVE_BLOCKS and neg <= MAX_NEGATIVE_BLOCKS
    )
    result = {
        "schema_version": "football3-r43e6-exact-two-leg-state-mechanism-v1",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_EXACT_UEFA_SECOND_LEG_STATE_CHRONOLOGICAL_OOS",
        "formal_weight": 0,
        "question": "Does exact first-leg aggregate state improve 90-minute second-leg 1X2 prediction beyond strict-prior rolling team strength and competition context?",
        "governance": {
            "source_r43e5_run": 33152737678,
            "external_source": "openfootball/champions-league",
            "external_license": "CC0/public domain",
            "exact_two_leg_identity": "same season/file/stage/team pair, exactly two matches, reversed home-away",
            "target_90min_result_used_as_feature": False,
            "first_leg_90min_score_used_as_feature": True,
            "first_leg_is_completed_before_target": True,
            "rolling_elo_updates_after_each_match": True,
            "parameter_search": False,
            "feature_search_after_test": False,
            "model_C": MODEL_C,
            "elo_K": ELO_K,
            "train_seasons": sorted(TRAIN_SEASONS), "validation_seasons": sorted(VAL_SEASONS), "test_seasons": sorted(TEST_SEASONS),
            "no_manual_importance_bonus": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "unified_three_class_argmax_unchanged": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline_features": BASE_NAMES,
            "candidate_features": STATE_NAMES,
            "new_information": ["first_margin_home", "first_abs_margin", "first_total_goals", "first_tied"],
            "baseline_strength": "strict-prior rolling Elo over all parsed UEFA matches, updated only after each match",
            "model": "StandardScaler + multinomial LogisticRegression, fixed C=0.5",
            "gate": "validation LogLoss improves; test LogLoss/Brier/RPS all improve; >=3/4 chronological test LogLoss blocks improve",
        },
        "source": {"parsed_matches": len(matches), "exact_second_leg_rows": len(data), "files": source_meta},
        "split": {
            "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]] if train else None,
            "val_dates": [val[0]["date"], val[-1]["date"]] if val else None,
            "test_dates": [test[0]["date"], test[-1]["date"]] if test else None,
        },
        "validation": {"baseline": metrics(vb), "candidate": metrics(vs), "candidate_minus_baseline": vd},
        "test": {
            "baseline": metrics(tb), "candidate": metrics(ts), "candidate_minus_baseline": td,
            "time_blocks": blocks, "positive_logloss_blocks": pos, "negative_logloss_blocks": neg,
            "actual_outcome_by_first_leg_margin": margin_table(test),
            "extra_time_or_penalty_targets": int(sum(x["target_had_extra_time_or_penalties"] for x in test)),
        },
        "gate": {"passed": passed, "action": "PROCEED_TO_R43E7_R43F5_STATE_TRANSPORT_REPLICATION" if passed else "DO_NOT_PROMOTE_R43E6_STATE_MECHANISM"},
        "limitations": [
            "This is a mechanism test on UEFA second legs, not a formal global model promotion.",
            "Rolling Elo is an internal strength control, not the full R43F5 baseline; a passing result must still be transported and replicated on R43F5 before promotion.",
            "OpenFootball naming is normalized within the source; R43E5 separately audited provider identity mapping for production integration.",
        ],
    }
    p = OUT / "summary_r43e6_exact_two_leg_state_mechanism.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "split": result["split"], "validation_delta": vd, "test_delta": td, "gate": result["gate"]}, indent=2))
    return result


if __name__ == "__main__":
    run()
