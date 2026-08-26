#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

import pandas as pd

LEAGUES = [
    ("E0", "England Premier League"),
    ("SP1", "Spain La Liga"),
    ("I1", "Italy Serie A"),
    ("D1", "Germany Bundesliga"),
    ("F1", "France Ligue 1"),
]
BASE_URL = "https://www.football-data.co.uk/mmz4281/2425/{code}.csv"
ALLOWED = ["Div", "Date", "Time", "HomeTeam", "AwayTeam"]
FORBIDDEN_LABEL_NAMES = {
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
    "RESULT", "SCORE", "HOMEGOALS", "AWAYGOALS",
}
RULE_ID = "BATCH001_FIVE_LEAGUE_2425_FIRST100_DATE_LEAGUE_SOURCEINDEX_V1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-batch001-lock/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, path.open("wb") as w:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            w.write(chunk)


def main() -> int:
    out_dir = Path(os.environ.get("BATCH001_OUT", "batch001_out"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    source_meta = []

    with tempfile.TemporaryDirectory(prefix="football3-batch001-") as td:
        td_path = Path(td)
        for league_rank, (code, league_name) in enumerate(LEAGUES):
            url = BASE_URL.format(code=code)
            raw_path = td_path / f"{code}.csv"
            download(url, raw_path)

            # Header inspection only; target/result values are never selected into memory.
            header = pd.read_csv(raw_path, nrows=0).columns.tolist()
            missing = [c for c in ALLOWED if c not in header]
            if missing:
                raise RuntimeError(f"{code}: missing required identity columns: {missing}")

            frame = pd.read_csv(raw_path, usecols=ALLOWED, dtype=str, keep_default_na=False)
            if any(str(c).upper() in FORBIDDEN_LABEL_NAMES for c in frame.columns):
                raise RuntimeError(f"{code}: forbidden label column entered zero-label frame")

            frame = frame.reset_index(names="source_row_index")
            frame["parsed_date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
            frame["HomeTeam"] = frame["HomeTeam"].astype(str).str.strip()
            frame["AwayTeam"] = frame["AwayTeam"].astype(str).str.strip()
            frame = frame[
                frame["parsed_date"].notna()
                & frame["HomeTeam"].ne("")
                & frame["AwayTeam"].ne("")
            ].copy()
            frame["league_rank"] = league_rank
            frame["league_name"] = league_name
            frame["source_url"] = url

            for rec in frame.to_dict(orient="records"):
                rows.append(rec)

            source_meta.append(
                {
                    "code": code,
                    "league_name": league_name,
                    "url": url,
                    "raw_sha256": sha256_file(raw_path),
                    "eligible_identity_rows": int(len(frame)),
                }
            )

    if len(rows) < 100:
        raise RuntimeError(f"eligible identity rows <100: {len(rows)}")

    all_df = pd.DataFrame(rows)
    all_df = all_df.sort_values(
        by=["parsed_date", "league_rank", "source_row_index"],
        ascending=[True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)

    lock = all_df.iloc[:100].copy()
    lock.insert(0, "batch_index", range(1, 101))
    lock["match_date_iso"] = lock["parsed_date"].dt.strftime("%Y-%m-%d")

    def make_key(r) -> str:
        payload = "|".join(
            [
                RULE_ID,
                str(r["Div"]),
                str(r["match_date_iso"]),
                str(r["Time"]),
                str(r["HomeTeam"]),
                str(r["AwayTeam"]),
                str(int(r["source_row_index"])),
            ]
        ).encode("utf-8")
        return sha256_bytes(payload)

    lock["match_key_sha256"] = lock.apply(make_key, axis=1)
    out_cols = [
        "batch_index",
        "Div",
        "league_name",
        "match_date_iso",
        "Time",
        "HomeTeam",
        "AwayTeam",
        "source_row_index",
        "source_url",
        "match_key_sha256",
    ]
    lock = lock[out_cols]

    forbidden_present = [c for c in lock.columns if str(c).upper() in FORBIDDEN_LABEL_NAMES]
    if forbidden_present:
        raise RuntimeError(f"forbidden fields in output: {forbidden_present}")

    csv_path = out_dir / "batch001_zero_label_lock.csv"
    lock.to_csv(csv_path, index=False, lineterminator="\n")
    csv_sha = sha256_file(csv_path)

    counts = {str(k): int(v) for k, v in lock["Div"].value_counts().sort_index().items()}
    rule_payload = {
        "rule_id": RULE_ID,
        "season": "2024/2025",
        "league_order": [code for code, _ in LEAGUES],
        "sort": ["parsed_date asc", "league_rank asc", "source_row_index asc"],
        "take": 100,
        "allowed_selection_columns": ALLOWED,
    }
    rule_sha = sha256_bytes(json.dumps(rule_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    meta = {
        "status": "ZERO_LABEL_LOCK_COMPLETE",
        "rule": rule_payload,
        "rule_sha256": rule_sha,
        "selected_rows": 100,
        "selected_date_min": str(lock["match_date_iso"].min()),
        "selected_date_max": str(lock["match_date_iso"].max()),
        "competition_counts": counts,
        "source_files": source_meta,
        "lock_csv": csv_path.name,
        "lock_csv_sha256": csv_sha,
        "forbidden_result_fields_exported": False,
        "labels_opened_for_selection": False,
    }
    (out_dir / "batch001_zero_label_lock.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
