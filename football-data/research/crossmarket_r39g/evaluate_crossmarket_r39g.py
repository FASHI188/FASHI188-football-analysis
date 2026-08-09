#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

IDENTITY_COLS = ["Season", "Div", "Date", "Time", "HomeTeam", "AwayTeam"]
REQ = ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5", "AHh", "AvgAHH", "AvgAHA",
       "AvgCH", "AvgCD", "AvgCA", "AvgC>2.5", "AvgC<2.5", "AHCh", "AvgCAHH", "AvgCAHA"]


def htxt(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hfile(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def clip(p: float, lo: float = 1e-8, hi: float = 1 - 1e-8) -> float:
    return min(hi, max(lo, float(p)))


def logit(p: float) -> float:
    p = clip(p)
    return math.log(p / (1 - p))


def entropy(q) -> float:
    return -sum(float(x) * math.log(max(float(x), 1e-15)) for x in q)


def valid_odd(v) -> bool:
    try:
        x = float(str(v).strip())
    except Exception:
        return False
    return math.isfinite(x) and x > 1.0


def valid_line(v) -> bool:
    try:
        x = float(str(v).strip())
    except Exception:
        return False
    return math.isfinite(x) and -5 <= x <= 5


def complete(row: dict[str, str]) -> bool:
    for c in REQ:
        if c in {"AHh", "AHCh"}:
            if not valid_line(row.get(c, "")):
                return False
        elif not valid_odd(row.get(c, "")):
            return False
    return True


def devig3(a, b, c):
    x = np.array([1 / float(a), 1 / float(b), 1 / float(c)], dtype=float)
    x /= x.sum()
    return x


def devig2(a, b):
    x = np.array([1 / float(a), 1 / float(b)], dtype=float)
    x /= x.sum()
    return x


def identity(row: dict[str, str]) -> str:
    return "|".join(str(row.get(c, "")).strip() for c in IDENTITY_COLS)


def set_sha(ids: list[str]) -> str:
    return htxt("\n".join(sorted(ids)) + "\n")


def parse_dt(date_s: str, time_s: str) -> datetime:
    ds = date_s.strip()
    ts = time_s.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
        try:
            return datetime.strptime(f"{ds} {ts}", fmt)
        except ValueError:
            pass
    raise ValueError(f"bad datetime {ds} {ts}")


def features(row: dict[str, str]):
    qo = devig3(row["AvgH"], row["AvgD"], row["AvgA"])
    qc = devig3(row["AvgCH"], row["AvgCD"], row["AvgCA"])
    ouo = devig2(row["Avg>2.5"], row["Avg<2.5"])
    ouc = devig2(row["AvgC>2.5"], row["AvgC<2.5"])
    aho = devig2(row["AvgAHH"], row["AvgAHA"])
    ahc = devig2(row["AvgCAHH"], row["AvgCAHA"])
    gap_o = abs(float(qo[0] - qo[2]))
    gap_c = abs(float(qc[0] - qc[2]))
    ent_o = entropy(qo)
    ent_c = entropy(qc)
    hda = [
        logit(float(qo[1])),
        logit(float(qc[1])),
        float(qc[1] - qo[1]),
        gap_o,
        gap_c,
        gap_c - gap_o,
        ent_o,
        ent_c,
        ent_c - ent_o,
    ]
    ah_line_o = float(row["AHh"])
    ah_line_c = float(row["AHCh"])
    bal_o = 1.0 - gap_o
    bal_c = 1.0 - gap_c
    under_o = float(ouo[1])
    under_c = float(ouc[1])
    nondraw_share_o = float(qo[0] / max(qo[0] + qo[2], 1e-12))
    nondraw_share_c = float(qc[0] / max(qc[0] + qc[2], 1e-12))
    disc_o = nondraw_share_o - float(aho[0])
    disc_c = nondraw_share_c - float(ahc[0])
    extra = [
        float(ouo[0]),
        float(ouc[0]),
        float(ouc[0] - ouo[0]),
        ah_line_o,
        ah_line_c,
        ah_line_c - ah_line_o,
        float(aho[0]),
        float(ahc[0]),
        float(ahc[0] - aho[0]),
        bal_o * under_o,
        bal_c * under_c,
        abs(ah_line_c) * under_c,
        disc_c,
        disc_c - disc_o,
    ]
    return hda, hda + extra, qc


def load_market_rows(sanitized_dir: Path):
    rows = {}
    for p in sorted(sanitized_dir.glob("*.csv")):
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            header = r.fieldnames or []
            forbidden = {"FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR"}
            if forbidden.intersection(header):
                raise RuntimeError(f"result columns present in market input {p.name}")
            for row in r:
                if not all(row.get(c, "").strip() for c in IDENTITY_COLS):
                    continue
                if not complete(row):
                    continue
                ident = identity(row)
                if ident in rows:
                    raise RuntimeError(f"duplicate identity {ident}")
                hda, cross, qc = features(row)
                rows[ident] = {
                    "identity": ident,
                    "season": row["Season"].strip(),
                    "div": row["Div"].strip(),
                    "dt": parse_dt(row["Date"], row["Time"]),
                    "hda": hda,
                    "cross": cross,
                    "qclose": qc.tolist(),
                }
    return rows


def recompute_sets(rows: dict, pre: dict):
    source = pre["source_binding"]
    hold_season = source["holdout_season"]
    training = [x for x in rows.values() if x["season"] != hold_season]
    hold = [x for x in rows.values() if x["season"] == hold_season]
    if len(training) != int(source["preholdout_rows"]) or len(hold) != int(source["holdout_pool_rows"]):
        raise RuntimeError(f"source count drift training={len(training)} hold={len(hold)}")
    seed = int(source["fixed100_seed"])
    n = int(source["fixed100_rows"])
    fixed = sorted(hold, key=lambda x: htxt(f"{seed}|{x['identity']}"))[:n]
    sha = set_sha([x["identity"] for x in fixed])
    if sha != source["fixed100_identity_sha256"]:
        raise RuntimeError(f"fixed100 identity drift {sha}")
    training = sorted(training, key=lambda x: (x["dt"], x["identity"]))
    fixed = sorted(fixed, key=lambda x: (x["dt"], x["identity"]))
    return training, fixed


def read_training_labels(raw_dir: Path, training_ids: set[str]):
    labels = {}
    accessed = 0
    holdout_access = 0
    for p in sorted(raw_dir.glob("*.csv")):
        season = p.stem.split("_", 1)[0]
        if season == "2425":
            continue
        with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                z = dict(row)
                z["Season"] = season
                ident = identity(z)
                if ident not in training_ids:
                    continue
                val = str(row.get("FTR", "")).strip()
                if val not in {"H", "D", "A"}:
                    raise RuntimeError(f"bad training label {ident}: {val}")
                labels[ident] = {"H": 0, "D": 1, "A": 2}[val]
                accessed += 1
    return labels, accessed, holdout_access


def read_fixed100_labels_after_freeze(raw_dir: Path, fixed_ids: set[str]):
    labels = {}
    accessed = 0
    nonfixed_2425_access = 0
    for p in sorted(raw_dir.glob("2425_*.csv")):
        season = "2425"
        with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                z = dict(row)
                z["Season"] = season
                ident = identity(z)
                if ident not in fixed_ids:
                    continue
                val = str(row.get("FTR", "")).strip()
                if val not in {"H", "D", "A"}:
                    raise RuntimeError(f"bad fixed label {ident}: {val}")
                labels[ident] = {"H": 0, "D": 1, "A": 2}[val]
                accessed += 1
    return labels, accessed, nonfixed_2425_access


def standardize_fit(X):
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-12, 1.0, std)
    return (X - mean) / std, mean, std


def fit_logistic(X, y, l2=1.0, max_iter=60, tol=1e-8):
    Z = np.column_stack([np.ones(len(X)), X])
    beta = np.zeros(Z.shape[1], dtype=float)
    pen = np.zeros_like(beta)
    pen[1:] = float(l2)
    conv = False
    delta_max = None
    for it in range(1, max_iter + 1):
        eta = Z @ beta
        p = 1 / (1 + np.exp(-np.clip(eta, -40, 40)))
        w = np.clip(p * (1 - p), 1e-8, None)
        grad = Z.T @ (p - y) + pen * beta
        H = Z.T @ (Z * w[:, None]) + np.diag(pen) + np.eye(Z.shape[1]) * 1e-10
        delta = np.linalg.solve(H, grad)
        beta -= delta
        delta_max = float(np.max(np.abs(delta)))
        if delta_max < tol:
            conv = True
            break
    return beta, {"iterations": it, "converged": conv, "coefficient_delta_max": delta_max}


def predict_logistic(X, beta):
    Z = np.column_stack([np.ones(len(X)), X])
    return 1 / (1 + np.exp(-np.clip(Z @ beta, -40, 40)))


def threeway(qclose, pd):
    pd = clip(pd)
    h, a = float(qclose[0]), float(qclose[2])
    s = h + a
    return np.array([(1 - pd) * h / s, pd, (1 - pd) * a / s], dtype=float)


def probability_metrics(probs, actual):
    ll = br = rps = 0.0
    for p, y in zip(probs, actual):
        one = np.zeros(3); one[y] = 1.0
        ll -= math.log(max(float(p[y]), 1e-15))
        br += float(((p - one) ** 2).sum())
        rps += 0.5 * ((float(p[0] - one[0])) ** 2 + (float(p[0] + p[1] - one[0] - one[1])) ** 2)
    n = len(actual)
    return {"rows": n, "log_loss": ll / n, "brier": br / n, "rps": rps / n}


def binary_logloss(pd, actual):
    y = np.array([1.0 if z == 1 else 0.0 for z in actual], dtype=float)
    p = np.clip(np.asarray(pd, dtype=float), 1e-15, 1 - 1e-15)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))


def auc_draw(pd, actual):
    pos = [float(p) for p, y in zip(pd, actual) if y == 1]
    neg = [float(p) for p, y in zip(pd, actual) if y != 1]
    if not pos or not neg:
        return None
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / (len(pos) * len(neg))


def decision_metrics(pred, actual):
    n = len(actual)
    hits = sum(int(p == y) for p, y in zip(pred, actual))
    dp = sum(p == 1 for p in pred)
    ad = sum(y == 1 for y in actual)
    tp = sum(p == 1 and y == 1 for p, y in zip(pred, actual))
    precision = tp / dp if dp else 0.0
    recall = tp / ad if ad else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"rows": n, "hits": hits, "accuracy": hits / n, "predicted_draw_count": dp, "actual_draw_count": ad,
            "draw_true_positive": tp, "draw_precision": precision, "draw_recall": recall, "draw_f1": f1}


def dscore(p):
    return float(p[1] - max(p[0], p[2]))


def market_pred(p):
    return int(np.argmax(np.asarray(p, dtype=float)))


def apply_boundary(p, threshold):
    if dscore(p) >= threshold:
        return 1
    return 0 if float(p[0]) >= float(p[2]) else 2


def choose_model(rows_fit, rows_val, labels, key, l2_candidates):
    Xfit = np.array([x[key] for x in rows_fit], dtype=float)
    Xval = np.array([x[key] for x in rows_val], dtype=float)
    yfit = np.array([1.0 if labels[x["identity"]] == 1 else 0.0 for x in rows_fit], dtype=float)
    actual_val = [labels[x["identity"]] for x in rows_val]
    Xs, mean, std = standardize_fit(Xfit)
    out = []
    for l2 in l2_candidates:
        beta, diag = fit_logistic(Xs, yfit, l2=float(l2))
        if not diag["converged"]:
            raise RuntimeError(f"logistic failed to converge key={key} l2={l2}")
        pd = predict_logistic((Xval - mean) / std, beta)
        probs = [threeway(x["qclose"], p) for x, p in zip(rows_val, pd)]
        pm = probability_metrics(probs, actual_val)
        out.append({"l2": float(l2), "beta": beta, "mean": mean, "std": std, "diag": diag,
                    "pd": pd, "probs": probs, "hda": pm,
                    "binary_draw_log_loss": binary_logloss(pd, actual_val), "draw_auc": auc_draw(pd, actual_val)})
    chosen = sorted(out, key=lambda z: (z["hda"]["log_loss"], z["binary_draw_log_loss"], z["l2"]))[0]
    return chosen, out


def refit(rows, labels, key, l2):
    X = np.array([x[key] for x in rows], dtype=float)
    y = np.array([1.0 if labels[x["identity"]] == 1 else 0.0 for x in rows], dtype=float)
    Xs, mean, std = standardize_fit(X)
    beta, diag = fit_logistic(Xs, y, l2=float(l2))
    if not diag["converged"]:
        raise RuntimeError("refit did not converge")
    return {"l2": float(l2), "beta": beta, "mean": mean, "std": std, "diag": diag}


def predict_model(rows, model, key):
    X = np.array([x[key] for x in rows], dtype=float)
    pd = predict_logistic((X - model["mean"]) / model["std"], model["beta"])
    probs = [threeway(x["qclose"], p) for x, p in zip(rows, pd)]
    return pd, probs


def per_div_logloss(rows, probs, actual):
    by = defaultdict(list)
    for i, row in enumerate(rows):
        by[row["div"]].append(i)
    out = {}
    for div, idx in sorted(by.items()):
        pp = [probs[i] for i in idx]
        yy = [actual[i] for i in idx]
        out[div] = probability_metrics(pp, yy)["log_loss"]
    return out


def select_policy(rows, probs, actual, market_preds, pre):
    base = decision_metrics(market_preds, actual)
    prevalence = sum(y == 1 for y in actual) / len(actual)
    scores = [dscore(p) for p in probs]
    ranked = sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i]["identity"]))
    cands = []
    for cov in pre["policy"]["coverages"]:
        k = max(1, int(math.ceil(float(cov) * len(rows))))
        selected = set(ranked[:k])
        threshold = min(scores[i] for i in selected)
        pred = [1 if i in selected else (0 if float(p[0]) >= float(p[2]) else 2) for i, p in enumerate(probs)]
        m = decision_metrics(pred, actual)
        ok = (m["accuracy"] > base["accuracy"] and
              m["draw_precision"] >= prevalence + float(pre["policy"]["draw_precision_at_least_policy_draw_prevalence_plus"]) and
              m["draw_f1"] >= float(pre["policy"]["draw_f1_min"]))
        cands.append({"coverage": float(cov), "selected_rows": k, "threshold": threshold, "metrics": m, "eligible": ok})
    good = [c for c in cands if c["eligible"]]
    chosen = sorted(good, key=lambda c: (-c["metrics"]["accuracy"], -c["metrics"]["draw_f1"], -c["metrics"]["draw_precision"], c["coverage"]))[0] if good else None
    return chosen, cands, base, prevalence


def bootstrap_accuracy(base_pred, cand_pred, actual, samples, seed):
    rnd = random.Random(seed)
    N = len(actual)
    vals = []
    for _ in range(samples):
        s = 0
        for _j in range(N):
            i = rnd.randrange(N)
            s += int(cand_pred[i] == actual[i]) - int(base_pred[i] == actual[i])
        vals.append(s / N)
    vals.sort()
    def q(x):
        return vals[min(len(vals) - 1, max(0, int(x * (len(vals) - 1))))]
    return {"samples": samples, "seed": seed, "mean": sum(vals) / samples, "p05": q(.05), "p50": q(.50), "p95": q(.95)}


def serialize_model(m):
    return {"l2": m["l2"], "beta": m["beta"].tolist(), "mean": m["mean"].tolist(), "std": m["std"].tolist(), "diag": m["diag"]}


def self_test():
    q = devig3(2, 3.5, 4)
    assert abs(float(q.sum()) - 1) < 1e-12
    x = np.linspace(-2, 2, 30)[:, None]
    y = (x[:, 0] > 0).astype(float)
    xs, mean, std = standardize_fit(x)
    b, d = fit_logistic(xs, y, l2=1.0)
    p = predict_logistic((x - mean) / std, b)
    assert d["converged"] and p[0] < p[-1]
    print("PASS_R39G_SYNTHETIC_SELF_TEST")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path)
    ap.add_argument("--sanitized-dir", type=Path)
    ap.add_argument("--raw-dir", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test(); return
    if not all((a.prereg, a.sanitized_dir, a.raw_dir, a.out_dir)):
        raise SystemExit("missing required args")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    pre = json.loads(a.prereg.read_text(encoding="utf-8"))
    rows = load_market_rows(a.sanitized_dir)
    training, fixed = recompute_sets(rows, pre)
    training_ids = {x["identity"] for x in training}
    labels, training_access, holdout_before = read_training_labels(a.raw_dir, training_ids)
    if holdout_before != 0 or len(labels) != len(training):
        raise RuntimeError(f"label count mismatch {len(labels)} vs {len(training)}")

    n = len(training)
    i1 = int(math.floor(n * float(pre["chronological_design"]["fit_fraction"])))
    i2 = int(math.floor(n * (float(pre["chronological_design"]["fit_fraction"]) + float(pre["chronological_design"]["validation_fraction"]))))
    fit_rows, val_rows, policy_rows = training[:i1], training[i1:i2], training[i2:]
    actual_val = [labels[x["identity"]] for x in val_rows]
    market_val_probs = [np.asarray(x["qclose"], dtype=float) for x in val_rows]
    market_val = probability_metrics(market_val_probs, actual_val)
    l2s = pre["model"]["l2_candidates_for_each_representation"]
    hda_chosen, hda_candidates = choose_model(fit_rows, val_rows, labels, "hda", l2s)
    cross_chosen, cross_candidates = choose_model(fit_rows, val_rows, labels, "cross", l2s)
    hda_div = per_div_logloss(val_rows, hda_chosen["probs"], actual_val)
    cross_div = per_div_logloss(val_rows, cross_chosen["probs"], actual_val)
    division_wins = sum(cross_div[d] < hda_div[d] for d in hda_div if d in cross_div)
    vg = pre["validation_gate_all_required"]
    validation_gate = {
        "cross_HDA_LogLoss_better_than_market": cross_chosen["hda"]["log_loss"] < market_val["log_loss"],
        "cross_HDA_LogLoss_better_than_HDA_only": cross_chosen["hda"]["log_loss"] < hda_chosen["hda"]["log_loss"],
        "cross_binary_Draw_LogLoss_better_than_HDA_only": cross_chosen["binary_draw_log_loss"] < hda_chosen["binary_draw_log_loss"],
        "cross_Draw_AUC_better_than_HDA_only": cross_chosen["draw_auc"] > hda_chosen["draw_auc"],
        "cross_HDA_LogLoss_division_wins": division_wins,
        "division_win_gate": division_wins >= int(vg["cross_HDA_LogLoss_beats_HDA_only_in_at_least_divisions"]),
    }
    validation_pass = all(v if isinstance(v, bool) else True for k, v in validation_gate.items() if k != "cross_HDA_LogLoss_division_wins")
    candidate_summary = {
        "hda_only": [{"l2": z["l2"], "HDA_LogLoss": z["hda"]["log_loss"], "binary_Draw_LogLoss": z["binary_draw_log_loss"], "Draw_AUC": z["draw_auc"]} for z in hda_candidates],
        "cross_market": [{"l2": z["l2"], "HDA_LogLoss": z["hda"]["log_loss"], "binary_Draw_LogLoss": z["binary_draw_log_loss"], "Draw_AUC": z["draw_auc"]} for z in cross_candidates],
    }
    base_result = {
        "schema_version": pre["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_rows": len(rows),
        "training_rows": len(training),
        "fixed100_rows": len(fixed),
        "fixed100_identity_sha256": set_sha([x["identity"] for x in fixed]),
        "fit_rows": len(fit_rows), "validation_rows": len(val_rows), "policy_rows": len(policy_rows),
        "training_labels_accessed": training_access,
        "holdout_labels_before_freeze": 0,
        "candidate_summary": candidate_summary,
        "validation": {
            "market": market_val,
            "selected_HDA_only": {"l2": hda_chosen["l2"], "HDA": hda_chosen["hda"], "binary_Draw_LogLoss": hda_chosen["binary_draw_log_loss"], "Draw_AUC": hda_chosen["draw_auc"], "per_division_HDA_LogLoss": hda_div},
            "selected_cross_market": {"l2": cross_chosen["l2"], "HDA": cross_chosen["hda"], "binary_Draw_LogLoss": cross_chosen["binary_draw_log_loss"], "Draw_AUC": cross_chosen["draw_auc"], "per_division_HDA_LogLoss": cross_div},
            "gate": validation_gate,
            "passed": validation_pass,
        },
        "prior_beat_the_bookie_blind_labels_accessed": 0,
        "hard_limits": pre["hard_limits"],
    }
    if not validation_pass:
        base_result["status"] = pre["validation_gate_all_required"]["if_fail"]
        base_result["fixed100_labels_accessed"] = 0
        (a.out_dir / "r39g_result.json").write_text(json.dumps(base_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(base_result, ensure_ascii=False, indent=2)); return

    fitval = training[:i2]
    hda_final = refit(fitval, labels, "hda", hda_chosen["l2"])
    cross_final = refit(fitval, labels, "cross", cross_chosen["l2"])
    cross_policy_pd, cross_policy_probs = predict_model(policy_rows, cross_final, "cross")
    actual_policy = [labels[x["identity"]] for x in policy_rows]
    market_policy_preds = [market_pred(x["qclose"]) for x in policy_rows]
    chosen_policy, policy_candidates, policy_market, policy_prevalence = select_policy(policy_rows, cross_policy_probs, actual_policy, market_policy_preds, pre)
    base_result["policy"] = {"draw_prevalence": policy_prevalence, "market": policy_market, "candidates": policy_candidates, "selected": chosen_policy}
    if chosen_policy is None:
        base_result["status"] = pre["policy"]["if_no_lane"]
        base_result["fixed100_labels_accessed"] = 0
        (a.out_dir / "r39g_result.json").write_text(json.dumps(base_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(base_result, ensure_ascii=False, indent=2)); return

    freeze = {
        "schema_version": pre["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prereg_file_sha256": hfile(a.prereg),
        "fixed100_identity_sha256": pre["source_binding"]["fixed100_identity_sha256"],
        "HDA_only_model": serialize_model(hda_final),
        "cross_market_model": serialize_model(cross_final),
        "policy": chosen_policy,
        "holdout_labels_accessed_at_freeze": 0,
    }
    freeze_path = a.out_dir / "model_policy_freeze_receipt_r39g.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    freeze_sha = hfile(freeze_path)

    fixed_ids = {x["identity"] for x in fixed}
    fixed_labels, fixed_access, nonfixed_access = read_fixed100_labels_after_freeze(a.raw_dir, fixed_ids)
    if fixed_access != 100 or nonfixed_access != 0 or len(fixed_labels) != 100:
        raise RuntimeError(f"fixed label access mismatch {fixed_access} {nonfixed_access} {len(fixed_labels)}")
    actual = [fixed_labels[x["identity"]] for x in fixed]
    market_probs = [np.asarray(x["qclose"], dtype=float) for x in fixed]
    market_preds = [market_pred(p) for p in market_probs]
    hda_pd, hda_probs = predict_model(fixed, hda_final, "hda")
    cross_pd, cross_probs = predict_model(fixed, cross_final, "cross")
    decision_pred = [apply_boundary(p, float(chosen_policy["threshold"])) for p in cross_probs]
    market_pm = probability_metrics(market_probs, actual)
    hda_pm = probability_metrics(hda_probs, actual)
    cross_pm = probability_metrics(cross_probs, actual)
    market_dm = decision_metrics(market_preds, actual)
    decision_dm = decision_metrics(decision_pred, actual)
    hda_bin = binary_logloss(hda_pd, actual)
    cross_bin = binary_logloss(cross_pd, actual)
    hda_auc = auc_draw(hda_pd, actual)
    cross_auc = auc_draw(cross_pd, actual)
    hg = pre["holdout"]["gate_all_required"]
    hold_gate = {
        "decision_accuracy_better_than_market": decision_dm["accuracy"] > market_dm["accuracy"],
        "draw_precision": decision_dm["draw_precision"] >= float(hg["draw_precision_min"]),
        "draw_recall": decision_dm["draw_recall"] >= float(hg["draw_recall_min"]),
        "draw_f1": decision_dm["draw_f1"] >= float(hg["draw_f1_min"]),
        "predicted_draw_count_min": decision_dm["predicted_draw_count"] >= int(hg["predicted_draw_count_min"]),
        "predicted_draw_count_max": decision_dm["predicted_draw_count"] <= int(hg["predicted_draw_count_max"]),
        "cross_HDA_LogLoss_nonworse_than_market": cross_pm["log_loss"] <= market_pm["log_loss"],
        "cross_HDA_LogLoss_better_than_HDA_only": cross_pm["log_loss"] < hda_pm["log_loss"],
        "cross_binary_Draw_LogLoss_better_than_HDA_only": cross_bin < hda_bin,
        "cross_Draw_AUC_better_than_HDA_only": cross_auc > hda_auc,
    }
    passed = all(hold_gate.values())
    base_result.update({
        "status": pre["holdout"]["pass_status"] if passed else pre["holdout"]["fail_status"],
        "model_policy_freeze_receipt_sha256": freeze_sha,
        "fixed100_labels_accessed": fixed_access,
        "fixed100_nonselected_2425_labels_accessed": nonfixed_access,
        "holdout": {
            "actual_H_D_A": {"H": sum(y == 0 for y in actual), "D": sum(y == 1 for y in actual), "A": sum(y == 2 for y in actual)},
            "market_probability": market_pm,
            "HDA_only_probability": {**hda_pm, "binary_Draw_LogLoss": hda_bin, "Draw_AUC": hda_auc},
            "cross_market_probability": {**cross_pm, "binary_Draw_LogLoss": cross_bin, "Draw_AUC": cross_auc},
            "market_decision": market_dm,
            "cross_market_decision": decision_dm,
            "paired_bootstrap_accuracy_delta": bootstrap_accuracy(market_preds, decision_pred, actual, int(pre["holdout"]["bootstrap_samples"]), int(pre["holdout"]["bootstrap_seed"])),
            "gate": hold_gate,
            "passed": passed,
        }
    })
    (a.out_dir / "r39g_result.json").write_text(json.dumps(base_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(base_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
