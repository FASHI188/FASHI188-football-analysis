#!/usr/bin/env python3
"""Technical runtime shim for Direct-T expected-loss routing R2.

The frozen R2 scientific design is unchanged. The first run failed because the new
router exposed raw core inputs directly to Ridge, while the frozen Direct-T base model
has always handled missing raw inputs with median imputation before scaling. This shim
reuses that existing deterministic preprocessing rule for the router and changes no
feature family, target, alpha, identity set, label boundary, or reporting gate.
"""
from __future__ import annotations

import json

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_direct_t_expected_loss_routing_r2 as r2
from evaluate_r41a_fixed200_joint_error_decomposition import load_json
from v510_historical_structure_features_r1 import ResearchError


def _raw_block_allow_missing(df, cols, name):
    x = df[cols].to_numpy(dtype=float)
    if x.shape != (len(df), len(cols)):
        raise ResearchError(f"{name}_SHAPE_MISMATCH:{x.shape}")
    if np.isinf(x).any():
        bad = int(np.isinf(x).sum())
        raise ResearchError(f"{name}_INFINITE:{bad}")
    return x


def _compose_features_with_base_imputation_contract(df, probs, family, core, fnames, jnames):
    g = r2.meta_features(probs)
    if family in {"r1_absolute_geometry39", "relative_geometry39"}:
        x = g
    elif family == "relative_geometry_core86":
        x = np.concatenate([g, _raw_block_allow_missing(df, core, "CORE47")], axis=1)
    elif family == "relative_geometry_all122":
        x = np.concatenate(
            [
                g,
                _raw_block_allow_missing(df, core, "CORE47"),
                _raw_block_allow_missing(df, fnames, "R42F18"),
                _raw_block_allow_missing(df, jnames, "R42J18"),
            ],
            axis=1,
        )
    else:
        raise ResearchError(f"UNKNOWN_ROUTER_FAMILY:{family}")
    expected = {
        "r1_absolute_geometry39": 39,
        "relative_geometry39": 39,
        "relative_geometry_core86": 86,
        "relative_geometry_all122": 122,
    }[family]
    if x.shape != (len(df), expected) or np.isinf(x).any():
        raise ResearchError(f"INVALID_ROUTER_MATRIX:{family}:{x.shape}")
    return x


def _ridge_pipeline(alpha):
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        Ridge(alpha=alpha),
    )


def _fit_absolute_with_imputation(x_policy, policy_losses, x_eval, alpha):
    predicted = np.column_stack(
        [_ridge_pipeline(alpha).fit(x_policy, policy_losses[n]).predict(x_eval) for n in r2.EXPERTS]
    )
    return np.argmin(predicted, axis=1), predicted


def _fit_relative_with_imputation(x_policy, policy_losses, x_eval, alpha):
    common = policy_losses["common_baseline"]
    preds = [np.zeros(len(x_eval), dtype=float)]
    for n in ("R42F", "R42J"):
        target = policy_losses[n] - common
        model = _ridge_pipeline(alpha)
        model.fit(x_policy, target)
        preds.append(model.predict(x_eval))
    predicted_delta = np.column_stack(preds)
    return np.argmin(predicted_delta, axis=1), predicted_delta


r2._raw_block = _raw_block_allow_missing
r2._compose_features = _compose_features_with_base_imputation_contract
r2._fit_absolute = _fit_absolute_with_imputation
r2._fit_relative = _fit_relative_with_imputation


if __name__ == "__main__":
    print(json.dumps(r2.run(load_json(r2.CONFIG), r2.OUT_DIR), ensure_ascii=False, indent=2))
