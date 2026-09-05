#!/usr/bin/env python3
from __future__ import annotations

from typing import Any
import runtime as rt

SCHEMA = "football3-formal-receipt-distribution-contract-v1"
_INSTALLED = False


def _distribution_fields(receipt: dict[str, Any]) -> dict[str, Any]:
    totals = receipt.get("total_goals")
    btts = receipt.get("btts")
    top = receipt.get("top_scores")
    if type(totals) is not dict or type(btts) is not dict or type(top) is not list:
        raise rt.RuntimeGateError("formal receipt distribution inputs missing")
    over = 0.0
    for key, value in totals.items():
        try:
            goals = int(key)
            p = float(value)
        except (TypeError, ValueError) as exc:
            raise rt.RuntimeGateError("formal total-goals distribution invalid") from exc
        if goals >= 3:
            over += p
    probs = [("HOME", float(receipt["p_home"])), ("DRAW", float(receipt["p_draw"])), ("AWAY", float(receipt["p_away"]))]
    top1 = max(probs, key=lambda x: (x[1], {"HOME": 2, "DRAW": 1, "AWAY": 0}[x[0]]))
    return {
        "mu_home": None,
        "mu_away": None,
        "mu_semantics": "NOT_DEFINED_FOR_GLOBAL_MIXTURE_SINGLE_POISSON_MU_NOT_CLAIMED",
        "over_2_5": over,
        "btts_yes": float(btts["yes"]),
        "top_3_exact_scores": top[:3],
        "one_x_two_top1": {"selection": top1[0], "probability": top1[1]},
        "distribution_contract_schema": SCHEMA,
        "distribution_fields_derived_from_existing_formal_distribution_only": True,
    }


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent_reuse": True, "additive_receipt_contract_only": True, "prediction_probability_object_changed": False, "single_poisson_mu_invented": False}
    original = rt.predict_match

    def predict_match(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if type(receipt) is not dict:
            raise rt.RuntimeGateError("formal predict_match receipt object required")
        prediction_sha_before = receipt.get("prediction_sha")
        receipt.update(_distribution_fields(receipt))
        receipt["prediction_sha_before_distribution_contract"] = prediction_sha_before
        receipt["receipt_sha"] = rt._sha_bytes(rt._canon_bytes({k: v for k, v in receipt.items() if k != "receipt_sha"}))
        return receipt

    rt.predict_match = predict_match
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "additive_receipt_contract_only": True,
        "prediction_probability_object_changed": False,
        "single_poisson_mu_invented": False,
        "over_2_5_from_formal_total_goals_distribution": True,
        "btts_yes_from_formal_btts_distribution": True,
        "top3_from_formal_score_ranking": True,
        "top1_from_formal_1x2": True,
    }
