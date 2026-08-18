from __future__ import annotations

from collections import defaultdict

import numpy as np

import evaluate_c070c_semimarkov_generator as c


def _simulate_q_fast(model, lh: float, la: float, features: list[str]):
    scaler = model.named_steps["standardscaler"]
    lr = model.named_steps["logisticregression"]
    classes = [int(x) for x in lr.classes_]
    if classes != c.ALL_CLASSES:
        raise RuntimeError(f"fast simulator class order mismatch {classes}")
    mean = np.asarray(scaler.mean_, dtype=float)
    scale = np.asarray(scaler.scale_, dtype=float)
    coef = np.asarray(lr.coef_, dtype=float)
    intercept = np.asarray(lr.intercept_, dtype=float)
    if coef.shape[0] != 3:
        raise RuntimeError(f"fast simulator expects 3 multinomial rows, got {coef.shape}")

    states: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for minute in range(90):
        keys = list(states.keys())
        raw = np.asarray(
            [
                [c._minute_feature_dict(lh, la, minute, diff, duration)[f] for f in features]
                for diff, duration in keys
            ],
            dtype=float,
        )
        z = (raw - mean) / scale
        logits = z @ coef.T + intercept
        logits -= np.max(logits, axis=1, keepdims=True)
        exp = np.exp(logits)
        probs = exp / np.sum(exp, axis=1, keepdims=True)

        nxt: dict[tuple[int, int], float] = defaultdict(float)
        for i, (diff, duration) in enumerate(keys):
            mass = states[(diff, duration)]
            p_no, p_home, p_away = map(float, probs[i])
            nxt[(diff, min(duration + 1, 90))] += mass * p_no
            nxt[(min(diff + 1, c.SIM_DIFF_MAX), 0)] += mass * p_home
            nxt[(max(diff - 1, c.SIM_DIFF_MIN), 0)] += mass * p_away
        states = dict(nxt)

    p0 = float(sum(m for (d, _), m in states.items() if d == 0))
    p1 = float(sum(m for (d, _), m in states.items() if abs(d) == 1))
    denom = p0 + p1
    q = p0 / denom if denom > 1e-15 else 0.5
    cap_mass = float(
        sum(m for (d, _), m in states.items() if d in {c.SIM_DIFF_MIN, c.SIM_DIFF_MAX})
    )
    return float(np.clip(q, 1e-9, 1 - 1e-9)), cap_mass


def main() -> None:
    c._simulate_q = _simulate_q_fast
    c.main()


if __name__ == "__main__":
    main()
