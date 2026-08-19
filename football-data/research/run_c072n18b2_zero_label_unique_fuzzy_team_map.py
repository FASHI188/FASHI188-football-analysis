#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from rapidfuzz import fuzz

BASE = Path(__file__).with_name("build_c072n18b_zero_label_target_market_join.py")
spec = importlib.util.spec_from_file_location("c072n18b_base", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load N18B base builder")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ORIG_BUILD_HISTORY = m.build_history
ORIG_RETRIEVE_ODDS = m.retrieve_footiqo_odds
STATE = {"history": None, "receipt": [], "fuzzy_accepted": 0, "exact_seen": 0}
RECEIPT = m.OUTDIR / "c072n18b2_team_mapping_receipt.json"
SUMMARY = m.OUTDIR / "c072n18b_summary.json"


def patched_build_history(resolved, tdir):
    h = ORIG_BUILD_HISTORY(resolved, tdir)
    STATE["history"] = h
    return h


def tokens(s: str) -> set[str]:
    return {x for x in str(s or "").split() if x}


def fuzzy_resolve(code: str, target_raw: str, target_norm: str):
    h = STATE["history"]
    if h is None:
        raise RuntimeError("N18B2 history state missing")
    source_map = h["unique_name_id"].get(code, {})
    if target_norm in source_map:
        STATE["exact_seen"] += 1
        return {
            "target_provider_name": target_raw,
            "target_normalized": target_norm,
            "resolver_mode": "EXACT",
            "accepted": True,
            "source_team_id": source_map[target_norm],
            "source_normalized": target_norm,
            "best_score": 100.0,
            "second_best_score": None,
            "margin": None,
        }
    if len(target_norm) < 4:
        return {
            "target_provider_name": target_raw,
            "target_normalized": target_norm,
            "resolver_mode": "FUZZY",
            "accepted": False,
            "source_team_id": None,
            "source_normalized": None,
            "best_score": None,
            "second_best_score": None,
            "margin": None,
            "reject_reason": "TARGET_NORMALIZED_LEN_LT4",
        }
    tt = tokens(target_norm)
    scored = []
    for cand_norm, team_id in source_map.items():
        if not (tt & tokens(cand_norm)):
            continue
        score = float(fuzz.token_set_ratio(target_norm, cand_norm))
        scored.append((score, cand_norm, team_id))
    scored.sort(key=lambda z: (-z[0], z[1], z[2]))
    if not scored:
        return {
            "target_provider_name": target_raw,
            "target_normalized": target_norm,
            "resolver_mode": "FUZZY",
            "accepted": False,
            "source_team_id": None,
            "source_normalized": None,
            "best_score": None,
            "second_best_score": None,
            "margin": None,
            "reject_reason": "NO_SHARED_TOKEN_CANDIDATE",
        }
    best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    best_unique = len([x for x in scored if abs(x[0] - best[0]) < 1e-12]) == 1
    margin = best[0] - second_score
    accepted = best[0] >= 90.0 and best_unique and margin >= 10.0
    rec = {
        "target_provider_name": target_raw,
        "target_normalized": target_norm,
        "resolver_mode": "FUZZY",
        "accepted": accepted,
        "source_team_id": best[2] if accepted else None,
        "source_normalized": best[1] if accepted else None,
        "best_score": best[0],
        "second_best_score": second_score,
        "margin": margin,
    }
    if not accepted:
        if best[0] < 90.0:
            rec["reject_reason"] = "BEST_LT90"
        elif not best_unique:
            rec["reject_reason"] = "NONUNIQUE_BEST"
        elif margin < 10.0:
            rec["reject_reason"] = "MARGIN_LT10"
    else:
        STATE["fuzzy_accepted"] += 1
    return rec


def patched_retrieve_odds():
    rows, stats, post_count = ORIG_RETRIEVE_ODDS()
    h = STATE["history"]
    if h is None:
        raise RuntimeError("N18B2 history state missing after odds retrieval")
    seen = set()
    receipt = []
    for r in rows:
        code = r["sourceCode"]
        for raw_name in (r["homeTeam"], r["awayTeam"]):
            norm = m.norm_team(raw_name)
            k = (code, norm)
            if k in seen:
                continue
            seen.add(k)
            rec = fuzzy_resolve(code, raw_name, norm)
            rec["source_code"] = code
            receipt.append(rec)
            if rec["accepted"] and rec["resolver_mode"] == "FUZZY":
                h["unique_name_id"][code][norm] = rec["source_team_id"]
    receipt.sort(key=lambda x: (x["source_code"], x["target_normalized"], x["target_provider_name"]))
    STATE["receipt"] = receipt
    m.OUTDIR.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return rows, stats, post_count


def rewrite_summary():
    if not SUMMARY.exists():
        return
    x = json.loads(SUMMARY.read_text(encoding="utf-8"))
    x["experiment"] = "C072-N18B2"
    x["parent_n18b_terminal"] = "STOP_COVERAGE"
    x["identity_resolver"] = {
        "library": "rapidfuzz",
        "function": "fuzz.token_set_ratio",
        "best_min": 90.0,
        "margin_min": 10.0,
        "same_league_only": True,
        "shared_token_required": True,
        "manual_aliases": 0,
        "fuzzy_accepted_unique_team_names": STATE["fuzzy_accepted"],
        "mapping_receipt_rows": len(STATE["receipt"]),
    }
    if x.get("status") == "PASS_N18B_ZERO_LABEL_TARGET_MARKET_JOIN":
        x["status"] = "PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN"
    else:
        x["status"] = "STOP_COVERAGE"
    if RECEIPT.exists():
        x["n18b2_team_mapping_receipt_sha256"] = m.sha256_file(RECEIPT)
    SUMMARY.write_text(json.dumps(x, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": x.get("status"),
        "eligible_rows": x.get("eligible_rows"),
        "selected_rows": x.get("selected_rows"),
        "fuzzy_accepted_unique_team_names": STATE["fuzzy_accepted"],
        "ineligibility_reasons": x.get("ineligibility_reasons"),
        "source_target_overlap": x.get("source_target_overlap"),
        "target_result_values_materialized": x.get("target_result_values_materialized"),
    }, indent=2, ensure_ascii=False, sort_keys=True))


m.build_history = patched_build_history
m.retrieve_footiqo_odds = patched_retrieve_odds

try:
    m.main()
except SystemExit:
    rewrite_summary()
    raise
else:
    rewrite_summary()
