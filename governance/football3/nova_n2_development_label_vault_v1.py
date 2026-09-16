from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

BIG5 = {
    "EPL": "EPL",
    "Bundesliga": "Bundesliga",
    "La liga": "La_liga",
    "La_liga": "La_liga",
    "Ligue 1": "Ligue_1",
    "Ligue_1": "Ligue_1",
    "Serie A": "Serie_A",
    "Serie_A": "Serie_A",
}
EXPECTED_N = 1826


class LabelVaultError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LabelVaultError(message)


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_db_datetime(value: str) -> str:
    text = str(value).replace(" ", "T").replace("+00:00", "Z")
    return text if text.endswith("Z") else text + "Z"


def read_development_projection(path: Path) -> list[dict[str, Any]]:
    """Read exactly the 2022 development prefix and never parse the following isolated row."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index in range(EXPECTED_N):
            line = handle.readline()
            require(bool(line), f"PROJECTION_SHORT:{index}")
            row = json.loads(line)
            require(int(row["season_start"]) == 2022, f"NON_DEVELOPMENT_ROW:{index}:{row.get('season_start')}")
            rows.append(row)
    return rows


def projection_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["kickoff"]),
        str(row["home_team_id"]),
        str(row["away_team_id"]),
        str(row["league"]),
    )


def database_key(row: tuple[Any, ...]) -> tuple[str, str, str, str]:
    return (
        normalize_db_datetime(str(row[0])),
        f"understat-team:{int(row[1])}",
        f"understat-team:{int(row[2])}",
        BIG5[str(row[3])],
    )


def run(db_path: Path, projection_path: Path, out_dir: Path) -> dict[str, Any]:
    targets = read_development_projection(projection_path)
    require(len(targets) == EXPECTED_N, "TARGET_COUNT")

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    query = """
        SELECT date,h_id,a_id,league,h_goals,a_goals
        FROM general_game_stats
        WHERE season=2022
          AND league IN ('EPL','Bundesliga','La liga','La_liga','Ligue 1','Ligue_1','Serie A','Serie_A')
        ORDER BY date,h_id,a_id
    """
    rows = connection.execute(query).fetchall()
    connection.close()
    require(len(rows) == EXPECTED_N, f"DATABASE_COUNT:{len(rows)}")

    index: dict[tuple[str, str, str, str], tuple[Any, ...]] = {}
    for row in rows:
        key = database_key(row)
        require(key not in index, f"DATABASE_DUPLICATE:{key}")
        index[key] = row

    labels: list[dict[str, Any]] = []
    binding_keys: list[list[str]] = []
    for target in targets:
        key = projection_key(target)
        require(key in index, f"UNMATCHED_TARGET:{key}")
        raw = index[key]
        home_goals, away_goals = int(raw[4]), int(raw[5])
        outcome = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
        labels.append(
            {
                "fixture_id": str(target["fixture_id"]),
                "league": str(target["league"]),
                "season_start": 2022,
                "kickoff": str(target["kickoff"]),
                "outcome": outcome,
            }
        )
        binding_keys.append(list(key))

    require(len(labels) == EXPECTED_N, "LABEL_COUNT")
    require(len({row["fixture_id"] for row in labels}) == EXPECTED_N, "LABEL_FIXTURE_DUPLICATE")

    out_dir.mkdir(parents=True, exist_ok=True)
    labels_path = out_dir / "development_2022_labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as handle:
        for row in labels:
            handle.write(canon(row).decode("utf-8") + "\n")

    receipt = {
        "schema_version": "football3-nova-n2-development-label-vault-v1",
        "status": "N2_DEVELOPMENT_2022_LABEL_VAULT_PASS",
        "development_n": EXPECTED_N,
        "development_season": 2022,
        "queried_season_predicate": 2022,
        "isolated_2023_labels_read": 0,
        "label_sha256": sha256_bytes(labels_path.read_bytes()),
        "binding_sha256": sha256_bytes(canon(binding_keys)),
        "score_fields_emitted": False,
        "raw_goals_emitted": False,
        "training_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    (out_dir / "development_label_receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.projection, args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
