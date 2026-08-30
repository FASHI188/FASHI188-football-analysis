from research_metrics import *

def comp_dispersion(rows: list[dict[str, Any]], train_end: int) -> dict[str, tuple[float, float]]:
    by: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in rows[:train_end]:
        by[str(row["competition_id"])].append((int(row["home_goals"]), int(row["away_goals"])))
    out = {}
    for cid, vals in by.items():
        def est(xs: list[int]) -> float:
            if len(xs) < 20:
                return 20.0
            mean = statistics.fmean(xs)
            var = statistics.pvariance(xs)
            if var <= mean + 1e-6:
                return 50.0
            return max(0.5, min(50.0, mean * mean / (var - mean)))
        out[cid] = (est([x for x, _ in vals]), est([y for _, y in vals]))
    return out


def deterministic_sample(indices: Iterable[int], limit: int) -> list[int]:
    seq = list(indices)
    if len(seq) <= limit:
        return seq
    return [seq[(i * len(seq)) // limit] for i in range(limit)]


def fit_dependence(family: str, rows: list[dict[str, Any]], features: list[dict[str, Any]], train_end: int,
                   dispersion: dict[str, tuple[float, float]], fitness: tuple[float, float] = (0.0, 0.0)) -> float:
    sample = deterministic_sample(range(train_end), MAX_FIT_ROWS)
    best = None
    for dep in DEP_GRIDS[family]:
        total = 0.0
        valid = True
        for i in sample:
            row = rows[i]
            feat = apply_fitness(features[i], *fitness) if fitness != (0.0, 0.0) else features[i]
            dh, da = dispersion.get(str(row["competition_id"]), (20.0, 20.0))
            try:
                matrix = joint_matrix(family, feat, dispersion_home=dh, dispersion_away=da, dependence=dep)
                total += -math.log(max(EPS, exact_score_probability(matrix, int(row["home_goals"]), int(row["away_goals"]))))
            except GovernanceError:
                valid = False
                break
        if valid:
            score = total / max(1, len(sample))
            if best is None or score < best[0]:
                best = (score, dep)
    if best is None:
        raise GovernanceError(f"no valid dependence value for {family}")
    return float(best[1])


def build_items(rows: list[dict[str, Any]], features: list[dict[str, Any]], start: int, end: int,
                family: str, dispersion: dict[str, tuple[float, float]], dep: float,
                fitness: tuple[float, float] = (0.0, 0.0), head_weights: list[list[float]] | None = None) -> list[dict[str, Any]]:
    items = []
    for i in range(start, end):
        row = rows[i]
        feat = apply_fitness(features[i], *fitness) if fitness != (0.0, 0.0) else features[i]
        dh, da = dispersion.get(str(row["competition_id"]), (20.0, 20.0))
        matrix = joint_matrix(family, feat, dispersion_home=dh, dispersion_away=da, dependence=dep)
        if head_weights is not None:
            target = head_predict(feat, head_weights)
            matrix = kl_project_to_1x2(matrix, target)
        probs = matrix_1x2(matrix)
        items.append({
            "probs": probs,
            "matrix": matrix,
            "actual": actual_class(row),
            "home_goals": int(row["home_goals"]),
            "away_goals": int(row["away_goals"]),
            "competition_id": row["competition_id"],
            "season": row["season"],
            "round_index": row["round_index"],
            "cold_start_bucket": feat["cold_start_bucket"],
        })
    return items


def fit_head(rows: list[dict[str, Any]], features: list[dict[str, Any]], train_end: int,
             fitness: tuple[float, float]) -> list[list[float]]:
    from engine import head_features
    sample = deterministic_sample(range(train_end), MAX_HEAD_ROWS)
    weights = [[0.0] * 5 for _ in range(3)]
    lr = 0.035
    l2 = 0.015
    for step in range(260):
        grad = [[0.0] * 5 for _ in range(3)]
        for i in sample:
            feat = apply_fitness(features[i], *fitness) if fitness != (0.0, 0.0) else features[i]
            x = head_features(feat)
            logits = [sum(w * v for w, v in zip(roww, x)) for roww in weights]
            m = max(logits)
            ex = [math.exp(z - m) for z in logits]
            s = sum(ex)
            probs = [z / s for z in ex]
            actual = actual_class(rows[i])
            yi = CLASSES.index(actual)
            for c in range(3):
                err = probs[c] - (1.0 if c == yi else 0.0)
                for j in range(5):
                    grad[c][j] += err * x[j]
        scale = 1.0 / max(1, len(sample))
        eta = lr / math.sqrt(1.0 + step / 40.0)
        for c in range(3):
            for j in range(5):
                reg = 0.0 if j == 0 else l2 * weights[c][j]
                weights[c][j] -= eta * (grad[c][j] * scale + reg)
        for j in range(5):
            mean = sum(weights[c][j] for c in range(3)) / 3.0
            for c in range(3):
                weights[c][j] -= mean
    return weights


def select_dynamic(rows: list[dict[str, Any]], folds: list[tuple[int, int, int]]) -> tuple[Parameters, list[dict[str, Any]], list[dict[str, Any]]]:
    trials = []
    best = None
    best_features = None
    for params in PARAM_GRID:
        feats = prequential_features(rows, params)
        all_items = []
        fold_ll = []
        for train_end, start, end in folds:
            disp = {cid: (50.0, 50.0) for cid in {str(r["competition_id"]) for r in rows}}
            items = build_items(rows, feats, start, end, "INDEPENDENT_POISSON_FROZEN", disp, 0.0)
            metric = evaluate_predictions(items)
            fold_ll.append(metric["logloss"])
            all_items.extend(items)
        agg = evaluate_predictions(all_items)
        trial = {"parameters": params.__dict__, "fold_logloss": fold_ll, "aggregate": agg}
        trials.append(trial)
        key = (agg["logloss"], agg["brier"], agg["rps"])
        if best is None or key < best:
            best = key
            best_features = feats
            chosen = params
    assert best_features is not None
    return chosen, best_features, trials


def fold_gain_status(candidate_fold_ll: list[float], baseline_fold_ll: list[float]) -> dict[str, Any]:
    gains = [b - c for b, c in zip(baseline_fold_ll, candidate_fold_ll)]
    return {
        "gains": gains,
        "median_gain": statistics.median(gains),
        "non_material_regression_folds": sum(1 for g in gains if g >= -0.003),
        "retention_stability": statistics.median(gains) > 0.0 and sum(1 for g in gains if g >= -0.003) >= 6,
    }

