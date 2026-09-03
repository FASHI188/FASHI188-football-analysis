#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import json
import math
import pathlib
import sys
from typing import Any

EPS = 1e-15
OUTCOME = {"H": 0, "D": 1, "A": 2}


def loadmod(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("contract root must be object")
    return value


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def norm_odds(vals) -> list[float]:
    xs = [1.0 / float(x) for x in vals]
    s = sum(xs)
    if not math.isfinite(s) or s <= 0:
        raise ValueError("invalid odds mass")
    return [x / s for x in xs]


def top1(p) -> int:
    return max(range(3), key=lambda i: (float(p[i]), -i))


def evidence_dominates(open_p, close_p) -> tuple[bool, dict[str, Any]]:
    incumbent = top1(open_p)
    target = top1(close_p)
    if incumbent == target:
        return False, {
            "proposal": False,
            "incumbent": incumbent,
            "target": target,
            "opening_margin": 0.0,
            "closing_reversal_margin": 0.0,
            "dominates": False,
        }
    opening_margin = float(open_p[incumbent]) - float(open_p[target])
    closing_reversal_margin = float(close_p[target]) - float(close_p[incumbent])
    dominates = closing_reversal_margin >= opening_margin
    return dominates, {
        "proposal": True,
        "incumbent": incumbent,
        "target": target,
        "opening_margin": opening_margin,
        "closing_reversal_margin": closing_reversal_margin,
        "dominates": dominates,
    }


def usable_odds(row: dict[str, str], cols: list[str]) -> bool:
    try:
        vals = [float(row[c]) for c in cols]
        return all(math.isfinite(x) and x > 1.0 for x in vals)
    except Exception:
        return False


def parse_date(s: str) -> datetime.date:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


def metric(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0, "logloss": None, "brier": None, "rps": None, "top1_accuracy": None}
    ll = br = rp = acc = 0.0
    for r in rows:
        p = r[key]
        y = int(r["y"])
        ll -= math.log(max(EPS, float(p[y])))
        br += sum((float(p[i]) - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        ycum = [1.0 if y == 0 else 0.0, 1.0 if y <= 1 else 0.0]
        pcum = [float(p[0]), float(p[0]) + float(p[1])]
        rp += 0.5 * sum((pcum[i] - ycum[i]) ** 2 for i in range(2))
        acc += 1.0 if top1(p) == y else 0.0
    return {"n": n, "logloss": ll / n, "brier": br / n, "rps": rp / n, "top1_accuracy": acc / n}


def deltas(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, float]:
    return {k: float(cand[k]) - float(base[k]) for k in ("logloss", "brier", "rps", "top1_accuracy")}


def pair_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    b = metric(rows, "open")
    q = metric(rows, key)
    return {"baseline": b, "candidate": q, "deltas": deltas(b, q)}


def evaluate_slices(rows: list[dict[str, Any]], key: str, contract: dict[str, Any]):
    nfold = int(contract["evaluation"]["chronological_folds"])
    folds = []
    nondeg = 0
    for k in range(nfold):
        lo = (len(rows) * k) // nfold
        hi = (len(rows) * (k + 1)) // nfold
        rr = rows[lo:hi]
        rec = pair_metrics(rr, key)
        ok = rec["deltas"]["logloss"] <= 0.0
        nondeg += int(ok)
        folds.append({
            "fold": k + 1,
            "n": len(rr),
            "min_date": str(rr[0]["date"]),
            "max_date": str(rr[-1]["date"]),
            **rec,
            "ll_nondegrade": ok,
        })
    groups = []
    gnon = 0
    for season in contract["fresh_data"]["seasons"]:
        for lg in contract["fresh_data"]["leagues"]:
            rr = [r for r in rows if r["season"] == season and r["league"] == lg["code"]]
            if not rr:
                raise RuntimeError(f"empty frozen league-season group: {season} {lg['code']}")
            rec = pair_metrics(rr, key)
            ok = rec["deltas"]["logloss"] <= 0.0
            gnon += int(ok)
            groups.append({"season": season, "league": lg["code"], "n": len(rr), **rec, "ll_nondegrade": ok})
    return nondeg, folds, gnon, groups


def classify(global_delta: dict[str, float], fold_n: int, group_n: int) -> str:
    global_ok = (
        global_delta["logloss"] <= 0.0
        and global_delta["brier"] <= 0.0
        and global_delta["rps"] <= 0.0
        and global_delta["top1_accuracy"] >= 0.0
    )
    if global_delta["logloss"] > 0.0 or fold_n <= 6 or group_n <= 6:
        return "EVIDENCE_DOMINANCE_EXTERNALLY_FAILED"
    if global_ok and fold_n >= 10 and group_n >= 10:
        return "EVIDENCE_DOMINANCE_EXTERNALLY_STABLE"
    return "EVIDENCE_DOMINANCE_EXTERNALLY_MIXED"


def validate_contract(c: dict[str, Any]) -> None:
    if c.get("status") != "FROZEN_BEFORE_FRESH_COHORT_DOWNLOAD_OR_LABEL_SCORING":
        raise RuntimeError("contract drift")
    rule = c.get("rule", {})
    expected = {
        "name": "EVIDENCE_DOMINANCE_REVERSAL_MARGIN",
        "evidence_dominance": "closing_reversal_margin >= opening_margin",
        "no_learning": True,
        "no_parameter_fit": True,
        "no_threshold_grid": True,
        "no_post_view_selector": True,
        "prediction_weight": 0,
    }
    for k, v in expected.items():
        if rule.get(k) != v:
            raise RuntimeError(f"frozen rule drift: {k}")
    codes = [x["code"] for x in c["fresh_data"]["leagues"]]
    prior = set(c["fresh_data"]["prior_external_stress_codes"])
    if len(codes) != 6 or len(set(codes)) != 6 or set(codes) & prior:
        raise RuntimeError("fresh cohort identity guard failed")
    if len(c["fresh_data"]["seasons"]) != 2 or int(c["evaluation"]["chronological_folds"]) != 12:
        raise RuntimeError("evaluation inventory drift")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True, type=pathlib.Path)
    ap.add_argument("--data-dir", required=True, type=pathlib.Path)
    ap.add_argument("--v324-runner", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = load_json(a.contract)
    validate_contract(c)
    v324 = loadmod("evidence_dominance_v324", a.v324_runner)

    rows: list[dict[str, Any]] = []
    inventory = []
    invalid_odds_n = 0
    req = c["fresh_data"]["required_columns"]
    odds_cols = ["AvgH", "AvgD", "AvgA", "AvgCH", "AvgCD", "AvgCA"]
    for season in c["fresh_data"]["seasons"]:
        sp = c["fresh_data"]["season_paths"][season]
        for lg in c["fresh_data"]["leagues"]:
            code = lg["code"]
            path = a.data_dir / f"{sp}_{code}.csv"
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                rd = csv.DictReader(f)
                header = rd.fieldnames or []
                missing = [x for x in req if x not in header]
                if missing:
                    raise RuntimeError(f"{path}: missing frozen required columns {missing}")
                raw = list(rd)
            completed = usable = 0
            for r in raw:
                if r.get("Div", "").strip() != code:
                    raise RuntimeError(f"{path}: division mismatch")
                if r.get("FTR", "").strip() not in OUTCOME:
                    continue
                completed += 1
                if not usable_odds(r, odds_cols):
                    invalid_odds_n += 1
                    continue
                op = norm_odds([r["AvgH"], r["AvgD"], r["AvgA"]])
                cp = norm_odds([r["AvgCH"], r["AvgCD"], r["AvgCA"]])
                weak = 0 if op[0] < op[2] else 2
                dominates, gate = evidence_dominates(op, cp)
                proposal = bool(gate["proposal"])
                target = int(gate["target"])

                if proposal:
                    ungated, upr = v324.minimum_boundary_projection(op, target, weak, float(c["rule"]["epsilon"]))
                else:
                    ungated = list(op)
                    upr = {"executed": False, "reason": "same_argmax", "total_variation": 0.0, "l2_sq": 0.0}

                if proposal and dominates:
                    cand, pr = v324.minimum_boundary_projection(op, target, weak, float(c["rule"]["epsilon"]))
                else:
                    cand = list(op)
                    pr = {
                        "executed": False,
                        "reason": "same_argmax" if not proposal else "evidence_not_dominant",
                        "total_variation": 0.0,
                        "l2_sq": 0.0,
                    }
                if cand[weak] < op[weak] - 1e-12:
                    raise RuntimeError("weak-floor invariant violated")
                rows.append({
                    "season": season,
                    "season_path": sp,
                    "league": code,
                    "date": parse_date(r["Date"]),
                    "home": r["HomeTeam"],
                    "away": r["AwayTeam"],
                    "y": OUTCOME[r["FTR"].strip()],
                    "open": op,
                    "close": cp,
                    "ungated": ungated,
                    "candidate": cand,
                    "proposal": proposal,
                    "evidence_dominant": dominates,
                    "candidate_executed": bool(pr["executed"]),
                    "candidate_reason": pr["reason"],
                    "candidate_tv": float(pr.get("total_variation", 0.0)),
                    "ungated_executed": bool(upr["executed"]),
                    "ungated_tv": float(upr.get("total_variation", 0.0)),
                    "opening_margin": float(gate["opening_margin"]),
                    "closing_reversal_margin": float(gate["closing_reversal_margin"]),
                })
                usable += 1
            inventory.append({
                "season": season,
                "season_path": sp,
                "league": code,
                "completed_match_count": completed,
                "usable_pre_match_odds_count": usable,
                "file": path.name,
            })

    rows.sort(key=lambda r: (r["date"], r["league"], r["home"], r["away"], r["season"]))
    if len(rows) <= 1000:
        raise RuntimeError(f"fresh cohort unexpectedly small: {len(rows)}")

    primary = pair_metrics(rows, "candidate")
    ungated = pair_metrics(rows, "ungated")
    fold_n, folds, group_n, groups = evaluate_slices(rows, "candidate", c)
    ufold_n, ufolds, ugroup_n, ugroups = evaluate_slices(rows, "ungated", c)
    cls = classify(primary["deltas"], fold_n, group_n)

    proposals = sum(int(r["proposal"]) for r in rows)
    dominant = sum(int(r["evidence_dominant"]) for r in rows)
    executed = sum(int(r["candidate_executed"]) for r in rows)
    ungated_executed = sum(int(r["ungated_executed"]) for r in rows)
    fallback = sum(int(r["proposal"] and r["evidence_dominant"] and not r["candidate_executed"]) for r in rows)
    tv = [r["candidate_tv"] for r in rows if r["candidate_executed"]]

    result = {
        "schema_version": "football3-v3-evidence-dominance-result-v1",
        "classification": cls,
        "scientific_role": c["scientific_role"],
        "row_count": len(rows),
        "missing_or_invalid_odds_row_count": invalid_odds_n,
        "inventory": inventory,
        "rule": {
            "name": c["rule"]["name"],
            "proposal_n": proposals,
            "evidence_dominant_n": dominant,
            "candidate_executed_n": executed,
            "weak_floor_fallback_n": fallback,
            "ungated_executed_n": ungated_executed,
            "mean_candidate_tv": sum(tv) / len(tv) if tv else 0.0,
            "max_candidate_tv": max(tv) if tv else 0.0,
            "prediction_weight": 0,
        },
        "global": primary,
        "chronological_fold_ll_nondegrade_n": fold_n,
        "chronological_folds": folds,
        "league_season_ll_nondegrade_n": group_n,
        "league_season_groups": groups,
        "ungated_reference": {
            "global": ungated,
            "chronological_fold_ll_nondegrade_n": ufold_n,
            "chronological_folds": ufolds,
            "league_season_ll_nondegrade_n": ugroup_n,
            "league_season_groups": ugroups,
        },
        "formal_confirmation": False,
        "promotion_allowed": False,
        "prediction_weight": 0,
        "formal_v2_unchanged": True,
        "v3_1_1_unchanged": True,
        "v3_2_4_unchanged": True,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_enablement_changed": False,
        "formal_weights_changed": False,
    }
    write_json(a.out / "evidence_dominance_result.json", result)
    print(json.dumps({
        "classification": cls,
        "row_count": len(rows),
        "proposal_n": proposals,
        "evidence_dominant_n": dominant,
        "candidate_executed_n": executed,
        "global_deltas": primary["deltas"],
        "chronological_fold_ll_nondegrade_n": fold_n,
        "league_season_ll_nondegrade_n": group_n,
        "ungated_global_deltas": ungated["deltas"],
        "ungated_fold_ll_nondegrade_n": ufold_n,
        "ungated_group_ll_nondegrade_n": ugroup_n,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
