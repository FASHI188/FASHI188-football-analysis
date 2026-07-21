#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import fpl_context_challenger_v519 as core

FPL_TEAM_CODE_TO_NAME: dict[str, str] = {}
_ORIGINAL_DOWNLOAD = core._download_gameweeks
_ORIGINAL_TEAM_FEATURES = core._team_features
_ORIGINAL_PAIR = core._pair_processed_match


def _numeric_token(value) -> str:
    token = str(value or "").strip()
    try:
        number = float(token)
        if math.isfinite(number) and abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
    except Exception:
        pass
    return token


def _token_variants(value) -> set[str]:
    raw = str(value or "").strip()
    token = _numeric_token(value)
    variants = {item for item in (raw, token) if item}
    try:
        number = float(token)
        if math.isfinite(number) and abs(number - round(number)) < 1e-9:
            variants.add(f"{int(round(number))}.0")
    except Exception:
        pass
    return variants


def _patched_download_gameweeks(source_sha: str):
    bundles, hashes = _ORIGINAL_DOWNLOAD(source_sha)

    # Preserve the provider's original per-GW feature snapshots before mutating
    # the target bundles. The README states that By Gameweek is an end-of-GW
    # snapshot, while only a subset of fields is explicitly deadline-safe.
    # Therefore target GW g uses feature files from GW g-1; GW1 is excluded.
    original_feature_files = {
        gw: {
            filename: bundles[gw][filename]
            for filename in ("players.csv", "player_gameweek_stats.csv", "teams.csv")
        }
        for gw in bundles
    }

    for gw, bundle in bundles.items():
        fixture_bundle = bundle["fixtures.csv"]
        rows = list(fixture_bundle["rows"])
        premier = [
            row for row in rows
            if str(row.get("tournament") or "").strip().lower() == "prem"
        ]
        if gw == 1:
            # No earlier snapshot exists, so GW1 cannot be a clean pre-match
            # context observation under the one-GW lag contract.
            premier = []
            feature_snapshot_gw = None
        else:
            feature_snapshot_gw = gw - 1
            for filename in ("players.csv", "player_gameweek_stats.csv", "teams.csv"):
                bundle[filename] = original_feature_files[gw - 1][filename]

        bundle["_fixture_filter_audit"] = {
            "raw_fixture_rows": len(rows),
            "premier_league_fixture_rows_before_lag_gate": sum(
                1 for row in rows if str(row.get("tournament") or "").strip().lower() == "prem"
            ),
            "target_fixture_rows_after_lag_gate": len(premier),
            "non_premier_rows_excluded": len(rows) - sum(
                1 for row in rows if str(row.get("tournament") or "").strip().lower() == "prem"
            ),
            "allowed_tournament": "prem",
            "target_gameweek": gw,
            "feature_snapshot_gameweek": feature_snapshot_gw,
            "feature_snapshot_policy": "STRICT_PREVIOUS_GAMEWEEK_ONLY",
        }
        fixture_bundle["rows"] = premier
    return bundles, hashes


def _patched_team_features(bundle):
    features, audit = _ORIGINAL_TEAM_FEATURES(bundle)
    for row in bundle["teams.csv"]["rows"]:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        # fixtures.csv is keyed by team *code*, so only code may drive fixture identity.
        code_token = _numeric_token(row.get("code"))
        if code_token:
            FPL_TEAM_CODE_TO_NAME[code_token] = name
        # Feature lookup may encounter code/id serialized as integer-like or .0 strings.
        for raw_token in (row.get("code"), row.get("id")):
            for variant in _token_variants(raw_token):
                if name in features:
                    features[variant] = features[name]
    audit = dict(audit)
    audit["fixture_team_code_bridge_count"] = len(FPL_TEAM_CODE_TO_NAME)
    audit["feature_alias_key_count"] = sum(1 for key in features if str(key).replace(".", "", 1).isdigit())
    audit["fixture_filter"] = dict(bundle.get("_fixture_filter_audit") or {})
    return features, audit


def _patched_pair_processed_match(lookup, date: str, home: str, away: str):
    home_name = FPL_TEAM_CODE_TO_NAME.get(_numeric_token(home), str(home))
    away_name = FPL_TEAM_CODE_TO_NAME.get(_numeric_token(away), str(away))
    return _ORIGINAL_PAIR(lookup, date, home_name, away_name)


def _patched_project(matrix, residual: float, scale: float):
    """Conditional KL tilt that preserves zero-mass total-goal slices exactly."""
    grouped = defaultdict(list)
    original = defaultdict(float)
    for h, a, p in core.score_matrix_rows(matrix):
        grouped[h + a].append((h, a, p))
        original[h + a] += p

    out = []
    zero_mass_slices = 0
    for total, cells in grouped.items():
        mass = sum(p for _, _, p in cells)
        if mass <= 0.0:
            zero_mass_slices += 1
            for h, a, _ in cells:
                out.append({"home_goals": h, "away_goals": a, "probability": 0.0})
            continue

        weighted = []
        for h, a, p in cells:
            exponent = max(-40.0, min(40.0, float(scale) * float(residual) * float(h - a)))
            weighted.append((h, a, p * math.exp(exponent)))
        denom = sum(item[2] for item in weighted)
        if denom <= 0.0:
            raise core.PlatformError(f"FPL conditional KL normalization failed positive_mass_total={total}")
        for h, a, weight in weighted:
            out.append({"home_goals": h, "away_goals": a, "probability": mass * weight / denom})

    total_prob = sum(float(cell["probability"]) for cell in out)
    if total_prob <= 0.0:
        raise core.PlatformError("FPL conditional KL projection produced non-positive total probability")
    out = [{**cell, "probability": float(cell["probability"]) / total_prob} for cell in out]

    new = defaultdict(float)
    for h, a, p in core.score_matrix_rows(out):
        new[h + a] += p
    return out, {
        "probability_sum_residual": abs(sum(float(cell["probability"]) for cell in out) - 1.0),
        "max_total_marginal_residual": max(abs(float(new[t]) - float(original[t])) for t in original),
        "zero_mass_total_slice_count": zero_mass_slices,
    }


core._download_gameweeks = _patched_download_gameweeks
core._team_features = _patched_team_features
core._pair_processed_match = _patched_pair_processed_match
core._project = _patched_project


def main() -> int:
    try:
        return int(core.main())
    except Exception as exc:
        payload = {
            "schema_version": "V5.1.9-fpl-context-challenger-execution-r8",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": "ENG_PremierLeague",
            "season": "2025/26",
            "status": "EXECUTION_FAILURE_KEEP_FORMAL_WEIGHT_0",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "fixture_team_code_bridge_count": len(FPL_TEAM_CODE_TO_NAME),
            "formal_weight": 0,
            "probability_change": False,
            "automatic_promotion": False,
        }
        core.OUT.parent.mkdir(parents=True, exist_ok=True)
        core.OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
