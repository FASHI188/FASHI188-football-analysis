#!/usr/bin/env python3
"""Entrypoint for R3 with unambiguous price-candidate parsing.

The frozen config uses three strings for a 1X2 price triplet and [over, under, line]
for OU. The base evaluator intentionally delegates candidate parsing through a helper;
this entrypoint makes the distinction explicit without changing any scientific field,
threshold, sample, target, or estimator.
"""
from __future__ import annotations

import json
from typing import Any

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
import evaluate_direct_t_market_state_routing_r3 as r3


def _first_complete_values(
    row: dict[str, Any], headers: list[str], candidates: list[list[Any]]
) -> tuple[list[float], list[str], Any] | None:
    for candidate in candidates:
        # OU candidates have a numeric/null third element representing the line.
        # 1X2 candidates have three string aliases and therefore three prices.
        has_line_payload = len(candidate) == 3 and not isinstance(candidate[2], str)
        price_aliases = candidate[:2] if has_line_payload else candidate
        fields = [field_name(headers, [str(alias)]) for alias in price_aliases]
        if not all(fields):
            continue
        values = [float_value(row, field) for field in fields]
        if all(valid_price(value) for value in values):
            extra = candidate[2] if has_line_payload else None
            return [float(value) for value in values], [str(field) for field in fields], extra
    return None


r3._first_complete_values = _first_complete_values

if __name__ == "__main__":
    print(json.dumps(r3.run(r3.load_json(r3.CONFIG), r3.OUT_DIR), ensure_ascii=False, indent=2))
