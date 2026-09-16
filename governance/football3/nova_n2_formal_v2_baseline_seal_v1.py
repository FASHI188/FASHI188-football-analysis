from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

FORMAL_V2_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
FORMAL_V2_WEIGHT = 0.75
EXPECTED_N = {2022: 1826, 2023: 1752}
LEAGUE_CANON = {
    "EPL": "EPL",
    "Bundesliga": "Bundesliga",
    "La_liga": "La_liga",
    "La liga": "La_liga",
    "Ligue_1": "Ligue_1",
    "Ligue 1": "Ligue_1",
    "Serie_A": "Serie_A",
    "Serie A": "Serie_A",
}
FORBIDDEN_TARGET_FIELDS = {
    "result", "winner", "outcome", "home_goals", "away_goals", "score", "final_score",
    "points", "pts", "xpts", "label", "target"
}


class SealError(RuntimeError):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise SealError(msg)


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(obj, dict), f"NOT_OBJECT:{path}")
    return obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            require(isinstance(row, dict), f"ROW_NOT_OBJECT:{path}:{i}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(canon(row).decode("utf-8") + "\n")


def normalize_kickoff(value: str) -> str:
    return str(value).replace("Z", "+00:00")


def join_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    league = LEAGUE_CANON.get(str(row["league"]))
    require(league is not None, f"UNKNOWN_LEAGUE:{row.get('league')}")
    return (
        normalize_kickoff(str(row["kickoff"])),
        str(row["home_team_id"]),
        str(row["away_team_id"]),
        league,
    )


def validate_target_row(row: dict[str, Any]) -> None:
    lowered = {str(k).lower() for k in row}
    bad = sorted(lowered & FORBIDDEN_TARGET_FIELDS)
    require(not bad, f"TARGET_FORBIDDEN_FIELDS:{bad}")
    require(int(row["season_start"]) in EXPECTED_N, "TARGET_BAD_SEASON")


def mix(v1: dict[str, Any], xg: dict[str, Any]) -> dict[str, float]:
    keys = ("p_home", "p_draw", "p_away")
    fallback = bool((xg.get("dynamic") or {}).get("fallback_exact_v1", False))
    if fallback:
        for k in keys:
            require(abs(float(v1[k]) - float(xg[k])) <= 1e-15, f"FALLBACK_NOT_EXACT:{k}")
        return {k: float(v1[k]) for k in keys}
    q = {k: (1.0 - FORMAL_V2_WEIGHT) * float(v1[k]) + FORMAL_V2_WEIGHT * float(xg[k]) for k in keys}
    z = sum(q.values())
    require(z > 0.0, "BAD_NORMALIZER")
    out = {k: q[k] / z for k in keys}
    require(abs(sum(out.values()) - 1.0) <= 1e-12, "PROB_SUM")
    return out


def build_index(rows: list[dict[str, Any]], season_field: str, season: int) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if int(row[season_field]) != season:
            continue
        key = join_key(row)
        require(key not in out, f"AMBIGUOUS_BASELINE_KEY:{season}:{key}")
        out[key] = row
    require(len(out) == EXPECTED_N[season], f"BASELINE_COUNT:{season}:{len(out)}")
    return out


def run(args: argparse.Namespace) -> dict[str, Any]:
    prereg = read_json(args.prereg)
    require(prereg.get("status") == "PRECHECK_LOCKED_NO_LABEL_FIT", "PREREG_STATUS")
    require(prereg["formal_v2"]["head"] == FORMAL_V2_HEAD, "FORMAL_HEAD_DRIFT")
    source_receipt = read_json(args.source_receipt)
    require(source_receipt.get("status") == "N2_NPXG_SOURCE_PRECHECK_PASS", "SOURCE_NOT_PASS")
    require(int(source_receipt.get("result_labels_read", -1)) == 0, "SOURCE_LABEL_READ_NONZERO")
    require(int(source_receipt.get("score_values_used", -1)) == 0, "SOURCE_SCORE_USE_NONZERO")

    v2_final = read_json(args.v2_final)
    require(float(v2_final.get("selected_weight")) == FORMAL_V2_WEIGHT, "FORMAL_WEIGHT_DRIFT")
    require(v2_final.get("development_pass") is True and v2_final.get("confirmation_pass") is True, "FORMAL_V2_NOT_FROZEN_PASS")

    targets = read_jsonl(args.target_projection)
    require(len(targets) == sum(EXPECTED_N.values()), f"TARGET_COUNT:{len(targets)}")
    for row in targets:
        validate_target_row(row)

    v2_dev = read_jsonl(args.v2_development_predictions)
    xg_all = read_jsonl(args.xg_frozen_predictions)
    v2_2022 = build_index(v2_dev, "season", 2022)
    xg_2022 = build_index(xg_all, "season", 2022)
    xg_2023 = build_index(xg_all, "season", 2023)

    max_repro = 0.0
    for key, row in v2_2022.items():
        parent = xg_2022.get(key)
        require(parent is not None, f"FORMAL_PARENT_MISSING_2022:{key}")
        recomputed = mix(parent["v1"], parent["challenger"])
        frozen = row["fusion"]
        for k in ("p_home", "p_draw", "p_away"):
            max_repro = max(max_repro, abs(float(frozen[k]) - recomputed[k]))
    require(max_repro <= 1e-12, f"FORMAL_2022_REPRO_DRIFT:{max_repro}")

    target_by_season: dict[int, list[dict[str, Any]]] = {2022: [], 2023: []}
    for row in targets:
        target_by_season[int(row["season_start"])].append(row)
    for season in EXPECTED_N:
        require(len(target_by_season[season]) == EXPECTED_N[season], f"TARGET_SEASON_COUNT:{season}")

    outputs: dict[int, list[dict[str, Any]]] = {2022: [], 2023: []}
    binding_keys: dict[int, list[list[str]]] = {2022: [], 2023: []}
    for season in (2022, 2023):
        baseline = v2_2022 if season == 2022 else xg_2023
        seen: set[tuple[str, str, str, str]] = set()
        for target in sorted(target_by_season[season], key=lambda r: (normalize_kickoff(r["kickoff"]), r["home_team_id"], r["away_team_id"])):
            key = join_key(target)
            require(key not in seen, f"TARGET_AMBIGUOUS:{season}:{key}")
            seen.add(key)
            base = baseline.get(key)
            require(base is not None, f"TARGET_UNMATCHED:{season}:{key}")
            if season == 2022:
                probs = {k: float(base["fusion"][k]) for k in ("p_home", "p_draw", "p_away")}
                formal_fixture_id = str(base["fixture_id"])
                fallback = bool(base["fallback_exact_v1"])
            else:
                probs = mix(base["v1"], base["challenger"])
                formal_fixture_id = str(base["fixture_id"])
                fallback = bool(base["challenger"]["dynamic"]["fallback_exact_v1"])
            rec = {
                "n2_fixture_id": str(target["fixture_id"]),
                "formal_fixture_id": formal_fixture_id,
                "league": LEAGUE_CANON[str(target["league"])],
                "season_start": season,
                "kickoff": normalize_kickoff(target["kickoff"]),
                "home_team_id": str(target["home_team_id"]),
                "away_team_id": str(target["away_team_id"]),
                "formal_v2_1x2": [probs["p_home"], probs["p_draw"], probs["p_away"]],
                "fallback_exact_v1": fallback,
                "model_head": FORMAL_V2_HEAD,
                "target_label_read": False,
            }
            outputs[season].append(rec)
            binding_keys[season].append(list(key))
        require(len(outputs[season]) == EXPECTED_N[season], f"OUTPUT_COUNT:{season}")

    args.out.mkdir(parents=True, exist_ok=True)
    dev_path = args.out / "formal_v2_baseline_2022_label_free.jsonl"
    iso_path = args.out / "formal_v2_baseline_2023_isolated_label_free.jsonl"
    write_jsonl(dev_path, outputs[2022])
    write_jsonl(iso_path, outputs[2023])
    receipt = {
        "schema_version": "football3-nova-n2-formal-v2-baseline-seal-v1",
        "status": "N2_FORMAL_V2_BASELINE_LABEL_FREE_SEAL_PASS",
        "formal_v2_head": FORMAL_V2_HEAD,
        "formal_v2_weight": FORMAL_V2_WEIGHT,
        "development_season": 2022,
        "development_n": len(outputs[2022]),
        "isolated_season": 2023,
        "isolated_n": len(outputs[2023]),
        "join_identity": ["kickoff", "home_team_id", "away_team_id", "league"],
        "fixture_id_namespace_rebound": True,
        "development_binding_sha256": sha256_bytes(canon(binding_keys[2022])),
        "isolated_binding_sha256": sha256_bytes(canon(binding_keys[2023])),
        "development_prediction_sha256": sha256_file(dev_path),
        "isolated_prediction_sha256": sha256_file(iso_path),
        "target_projection_sha256": sha256_file(args.target_projection),
        "v2_development_input_sha256": sha256_file(args.v2_development_predictions),
        "xg_frozen_input_sha256": sha256_file(args.xg_frozen_predictions),
        "max_abs_2022_formal_reproduction_diff": max_repro,
        "result_labels_read": 0,
        "score_values_read": 0,
        "isolated_labels_read": 0,
        "training_performed": False,
        "candidate_probabilities_generated": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    (args.out / "baseline_seal_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prereg", type=Path, required=True)
    p.add_argument("--source-receipt", type=Path, required=True)
    p.add_argument("--target-projection", type=Path, required=True)
    p.add_argument("--v2-final", type=Path, required=True)
    p.add_argument("--v2-development-predictions", type=Path, required=True)
    p.add_argument("--xg-frozen-predictions", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
