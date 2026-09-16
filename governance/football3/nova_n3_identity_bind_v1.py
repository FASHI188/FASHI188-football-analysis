from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

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


class IdentityBindError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IdentityBindError(message)


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_kickoff(value: Any) -> str:
    text = str(value).strip().replace("Z", "+00:00")
    if text.endswith("+00:00"):
        return text
    return text


def identity_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    league = LEAGUE_CANON.get(str(row.get("league")))
    require(league is not None, f"UNKNOWN_LEAGUE:{row.get('league')}")
    return (
        normalize_kickoff(row.get("kickoff")),
        str(row.get("home_team_id")),
        str(row.get("away_team_id")),
        league,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                require(isinstance(row, dict), f"ROW_NOT_OBJECT:{path}")
                out.append(row)
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canon(row).decode("utf-8") + "\n")


def bind_source(source_path: Path, baseline_path: Path, out_dir: Path) -> dict[str, Any]:
    source = read_jsonl(source_path)
    baseline = read_jsonl(baseline_path)
    require(len(source) == len(baseline), f"COUNT_MISMATCH:{len(source)}:{len(baseline)}")

    baseline_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in baseline:
        key = identity_key(row)
        require(key not in baseline_by_key, f"BASELINE_IDENTITY_AMBIGUOUS:{key}")
        baseline_by_key[key] = row

    source_keys: set[tuple[str, str, str, str]] = set()
    source_ids: set[str] = set()
    baseline_ids: set[str] = set()
    mapping: dict[str, str] = {}
    bound: list[dict[str, Any]] = []
    direct_fixture_id_match_n = 0
    for row in source:
        source_id = str(row.get("fixture_id"))
        require(source_id and source_id not in source_ids, f"SOURCE_FIXTURE_AMBIGUOUS:{source_id}")
        source_ids.add(source_id)
        key = identity_key(row)
        require(key not in source_keys, f"SOURCE_IDENTITY_AMBIGUOUS:{key}")
        source_keys.add(key)
        target = baseline_by_key.get(key)
        require(target is not None, f"IDENTITY_JOIN_MISSING:{source_id}:{key}")
        target_id = str(target.get("n3_fixture_id"))
        require(target_id and target_id not in baseline_ids, f"BASELINE_ROW_REUSED:{target_id}")
        baseline_ids.add(target_id)
        mapping[source_id] = target_id
        if source_id == target_id:
            direct_fixture_id_match_n += 1
        copied = dict(row)
        copied["source_fixture_id"] = source_id
        copied["fixture_id"] = target_id
        copied["identity_binding"] = "kickoff+home_team_id+away_team_id+league"
        bound.append(copied)

    require(len(bound) == len(source), "BOUND_COUNT")
    require(len(baseline_ids) == len(baseline), "BASELINE_BINDING_NOT_BIJECTIVE")
    out_dir.mkdir(parents=True, exist_ok=True)
    bound_path = out_dir / "source_projection_bound.jsonl"
    map_path = out_dir / "source_to_baseline_fixture_map.json"
    write_jsonl(bound_path, sorted(bound, key=lambda row: (row["kickoff"], row["fixture_id"])))
    map_path.write_text(json.dumps(mapping, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": "football3-nova-n3-zero-label-identity-bind-v1",
        "status": "N3_ZERO_LABEL_IDENTITY_BIND_PASS",
        "bound_n": len(bound),
        "source_fixture_unique_n": len(source_ids),
        "baseline_fixture_unique_n": len(baseline_ids),
        "identity_unique_n": len(source_keys),
        "direct_fixture_id_match_n": direct_fixture_id_match_n,
        "fallback_identity_match_n": len(bound) - direct_fixture_id_match_n,
        "identity_key": ["kickoff", "home_team_id", "away_team_id", "league"],
        "source_sha256": sha256_file(source_path),
        "baseline_sha256": sha256_file(baseline_path),
        "bound_source_sha256": sha256_file(bound_path),
        "map_sha256": sha256_file(map_path),
        "target_label_read": False,
        "score_value_read": False,
        "result_value_read": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    (out_dir / "identity_bind_receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def bind_labels(labels_path: Path, map_path: Path, out_dir: Path) -> dict[str, Any]:
    labels = read_jsonl(labels_path)
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    require(isinstance(mapping, dict), "MAP_NOT_OBJECT")
    require(len(labels) == len(mapping), f"LABEL_MAP_COUNT_MISMATCH:{len(labels)}:{len(mapping)}")
    seen_source: set[str] = set()
    seen_target: set[str] = set()
    bound: list[dict[str, Any]] = []
    for row in labels:
        source_id = str(row.get("fixture_id"))
        require(source_id not in seen_source, f"LABEL_SOURCE_DUPLICATE:{source_id}")
        seen_source.add(source_id)
        target_id = mapping.get(source_id)
        require(target_id is not None, f"LABEL_BIND_MISSING:{source_id}")
        target_id = str(target_id)
        require(target_id not in seen_target, f"LABEL_TARGET_DUPLICATE:{target_id}")
        seen_target.add(target_id)
        copied = dict(row)
        copied["source_fixture_id"] = source_id
        copied["fixture_id"] = target_id
        bound.append(copied)
    require(len(seen_target) == len(mapping), "LABEL_BINDING_NOT_BIJECTIVE")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "labels_bound.jsonl"
    write_jsonl(path, sorted(bound, key=lambda row: row["fixture_id"]))
    receipt = {
        "schema_version": "football3-nova-n3-label-identity-bind-v1",
        "status": "N3_LABEL_IDENTITY_BIND_PASS",
        "label_n": len(bound),
        "source_fixture_unique_n": len(seen_source),
        "baseline_fixture_unique_n": len(seen_target),
        "labels_sha256": sha256_file(labels_path),
        "map_sha256": sha256_file(map_path),
        "bound_labels_sha256": sha256_file(path),
        "binding_map_preexisted_before_label_open": True,
        "binding_uses_outcome": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    (out_dir / "label_bind_receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def self_test() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "source.jsonl"
        baseline = root / "baseline.jsonl"
        labels = root / "labels.jsonl"
        write_jsonl(source, [{"fixture_id":"understat:14086","kickoff":"2020-09-12T11:30:00Z","home_team_id":"understat-team:228","away_team_id":"understat-team:83","league":"EPL"}])
        write_jsonl(baseline, [{"n3_fixture_id":"understat:1485187","kickoff":"2020-09-12T11:30:00+00:00","home_team_id":"understat-team:228","away_team_id":"understat-team:83","league":"EPL","formal_v2_1x2":[0.2,0.3,0.5]}])
        write_jsonl(labels, [{"fixture_id":"understat:14086","outcome":"away"}])
        receipt = bind_source(source, baseline, root / "bind")
        require(receipt["bound_n"] == 1 and receipt["fallback_identity_match_n"] == 1, "SELFTEST_BIND_SOURCE")
        label_receipt = bind_labels(labels, root / "bind" / "source_to_baseline_fixture_map.json", root / "labels_bound")
        require(label_receipt["label_n"] == 1, "SELFTEST_BIND_LABEL")
        row = read_jsonl(root / "labels_bound" / "labels_bound.jsonl")[0]
        require(row["fixture_id"] == "understat:1485187" and row["source_fixture_id"] == "understat:14086", "SELFTEST_LABEL_ID")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    source = sub.add_parser("bind-source")
    source.add_argument("--source", type=Path, required=True)
    source.add_argument("--baseline", type=Path, required=True)
    source.add_argument("--out", type=Path, required=True)
    labels = sub.add_parser("bind-labels")
    labels.add_argument("--labels", type=Path, required=True)
    labels.add_argument("--map", type=Path, required=True)
    labels.add_argument("--out", type=Path, required=True)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "bind-source":
        result = bind_source(args.source, args.baseline, args.out)
        print(json.dumps(result, sort_keys=True))
    elif args.command == "bind-labels":
        result = bind_labels(args.labels, args.map, args.out)
        print(json.dumps(result, sort_keys=True))
    else:
        self_test()
        print(json.dumps({"status":"N3_IDENTITY_BIND_SELFTEST_PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
