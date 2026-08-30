from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from engine import CLASSES, joint_matrix, matrix_1x2, poisson_pmf
from research import evaluate_predictions
from strict import GovernanceError, sha256_file, strict_nonnegative_int, validate_probability_vector

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"
FINAL_RUN = EVIDENCE / "final_protocol"
EPS = 1e-15


def cells_to_matrix(cells: list[dict[str, Any]]) -> list[list[float]]:
    if not isinstance(cells, list) or not cells:
        raise GovernanceError("matrix cells missing")
    max_h = max(strict_nonnegative_int(c["home_goals"], "matrix.home_goals") for c in cells)
    max_a = max(strict_nonnegative_int(c["away_goals"], "matrix.away_goals") for c in cells)
    matrix = [[0.0] * (max_a + 1) for _ in range(max_h + 1)]
    seen = set()
    for c in cells:
        h = strict_nonnegative_int(c["home_goals"], "matrix.home_goals")
        a = strict_nonnegative_int(c["away_goals"], "matrix.away_goals")
        if (h, a) in seen:
            raise GovernanceError("duplicate matrix cell")
        seen.add((h, a))
        p = float(c["probability"])
        if not math.isfinite(p) or p < 0:
            raise GovernanceError("invalid matrix probability")
        matrix[h][a] = p
    total = sum(sum(row) for row in matrix)
    if abs(total - 1.0) > 1e-8:
        raise GovernanceError(f"matrix sum invalid {total}")
    return matrix


def v1_poisson_matrix(mu_h: float, mu_a: float, max_goals: int = 14) -> list[list[float]]:
    hp = poisson_pmf(float(mu_h), max_goals)
    ap = poisson_pmf(float(mu_a), max_goals)
    raw = [[h * a for a in ap] for h in hp]
    total = sum(sum(r) for r in raw)
    return [[p / total for p in r] for r in raw]


def actual_class(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def load_v2() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    receipt = json.loads((FINAL_RUN / "protocol_receipt.json").read_text(encoding="utf-8"))
    items = []
    meta = {}
    for batch in receipt["batches"]:
        idx = int(batch["batch_index"])
        blind_path = FINAL_RUN / "batches" / f"{idx:04d}_blind.json"
        scored_path = FINAL_RUN / "batches" / f"{idx:04d}_scored.json"
        if sha256_file(blind_path) != batch["blind_sha256"] or sha256_file(scored_path) != batch["scored_sha256"]:
            raise GovernanceError("final protocol batch digest drift")
        blind = json.loads(blind_path.read_text(encoding="utf-8"))
        scored = json.loads(scored_path.read_text(encoding="utf-8"))
        labels = {str(x["fixture_id"]): (strict_nonnegative_int(x["home_goals"], "hg"), strict_nonnegative_int(x["away_goals"], "ag")) for x in scored["labels"]}
        for pred in blind["predictions"]:
            fid = str(pred["fixture_id"])
            if fid not in labels:
                raise GovernanceError("scored label missing for blind prediction")
            hg, ag = labels[fid]
            matrix = cells_to_matrix(pred["matrix"])
            probs = validate_probability_vector(pred["final_1x2"], "v2.final_1x2")
            item = {
                "fixture_id": fid,
                "probs": probs,
                "matrix": matrix,
                "actual": actual_class(hg, ag),
                "home_goals": hg,
                "away_goals": ag,
                "competition_id": pred["competition_id"],
                "season": pred["season"],
                "round_index": pred["round_index"],
                "cold_start_bucket": pred["cold_start_bucket"],
                "model_features": pred["model_features"],
            }
            items.append(item)
            meta[fid] = item
    if len(items) != int(receipt["final_fixture_n"]) or len(meta) != len(items):
        raise GovernanceError("V2 final prediction coverage/count mismatch")
    return items, meta


def load_v1_reference(v1_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    blind_path = v1_dir / "evidence" / "holdout_predictions_blind.jsonl"
    if not blind_path.exists():
        raise GovernanceError("V1 reference blind file missing")
    pure: dict[str, dict[str, Any]] = {}
    legacy: dict[str, dict[str, Any]] = {}
    with blind_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            batch = json.loads(line)
            for fid, pred in (batch.get("pure") or {}).items():
                pure[str(fid)] = pred
            for fid, pred in (batch.get("legacy") or {}).items():
                legacy[str(fid)] = pred
    return pure, legacy


def paired_v1_items(v2_meta: dict[str, dict[str, Any]], v1_pure: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    v2 = []
    v1 = []
    for fid in sorted(set(v2_meta) & set(v1_pure)):
        a = v2_meta[fid]
        p = v1_pure[fid]
        v2.append(a)
        probs = validate_probability_vector(
            {"home": p["p_home"], "draw": p["p_draw"], "away": p["p_away"]},
            "v1.probs",
        )
        matrix = v1_poisson_matrix(float(p["mu_home"]), float(p["mu_away"]))
        v1.append({
            "fixture_id": fid,
            "probs": probs,
            "matrix": matrix,
            "actual": a["actual"],
            "home_goals": a["home_goals"],
            "away_goals": a["away_goals"],
            "competition_id": a["competition_id"],
            "season": a["season"],
            "round_index": a["round_index"],
            "cold_start_bucket": a["cold_start_bucket"],
        })
    return v2, v1


def evaluate_1x2(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"n": 0}
    n = len(items)
    ll = brier = rps = 0.0
    top = 0
    bins = {c: [[] for _ in range(10)] for c in CLASSES}
    for x in items:
        probs = x["probs"]
        actual = x["actual"]
        ll += -math.log(max(EPS, probs[actual]))
        brier += sum((probs[c] - (1 if c == actual else 0)) ** 2 for c in CLASSES)
        order = list(CLASSES); ai = order.index(actual)
        cp = co = rr = 0.0
        for i in range(2):
            cp += probs[order[i]]
            co += 1.0 if ai == i else 0.0
            rr += (cp - co) ** 2
        rps += rr / 2.0
        top += int(max(CLASSES, key=lambda c: probs[c]) == actual)
        for c in CLASSES:
            p = probs[c]; bins[c][min(9, int(p * 10))].append((p, int(c == actual)))
    eces = {}
    for c in CLASSES:
        ece = 0.0
        for bucket in bins[c]:
            if bucket:
                ece += len(bucket) / n * abs(sum(p for p, _ in bucket)/len(bucket) - sum(y for _, y in bucket)/len(bucket))
        eces[c] = ece
    return {"n": n, "top1": top/n, "logloss": ll/n, "brier": brier/n, "rps": rps/n, "macro_ece": sum(eces.values())/3, "class_ece": eces}


def v500_items(v2_meta: dict[str, dict[str, Any]], legacy: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for fid in sorted(set(v2_meta) & set(legacy)):
        a = v2_meta[fid]; p = legacy[fid]
        out.append({
            "fixture_id": fid,
            "probs": validate_probability_vector({"home": p["p_home"], "draw": p["p_draw"], "away": p["p_away"]}, "v500.probs"),
            "actual": a["actual"],
        })
    return out


def group_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in items:
        groups[f"competition:{x['competition_id']}"] .append(x)
        groups[f"season:{x['competition_id']}|{x['season']}"] .append(x)
        groups[f"cold:{x['cold_start_bucket']}"] .append(x)
        if x["round_index"] in (1, 2, 3, 29, 30):
            groups[f"round:{x['round_index']}"] .append(x)
    return {k: evaluate_predictions(v) for k, v in sorted(groups.items()) if v}


def worst_group_delta(v2: list[dict[str, Any]], v1: list[dict[str, Any]]) -> dict[str, Any]:
    by2: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by1: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a, b in zip(v2, v1):
        keys = [
            f"competition:{a['competition_id']}",
            f"season:{a['competition_id']}|{a['season']}",
            f"cold:{a['cold_start_bucket']}",
        ]
        if a["round_index"] in (1, 2, 3, 29, 30):
            keys.append(f"round:{a['round_index']}")
        for k in keys:
            by2[k].append(a); by1[k].append(b)
    rows = []
    for k in sorted(by2):
        if len(by2[k]) < 100:
            continue
        m2 = evaluate_predictions(by2[k])
        m1 = evaluate_predictions(by1[k])
        rows.append({"group": k, "n": len(by2[k]), "v2_logloss": m2["logloss"], "v1_logloss": m1["logloss"], "delta": m2["logloss"] - m1["logloss"]})
    if not rows:
        return {"status": "NO_N_GE_100_GROUP"}
    worst = max(rows, key=lambda x: x["delta"])
    return {"worst": worst, "groups": rows}

