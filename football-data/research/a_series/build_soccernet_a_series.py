#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

SEED = "A_SERIES_SOCCERNET_V2_20260818_R1"
START_INDEX = 6
PACKAGE_SIZE = 400


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    labels = sorted(args.source.rglob("Labels-v2.json"))
    games = []
    for label in labels:
        game_dir = label.parent
        rel = game_dir.relative_to(args.source).as_posix()
        game_key = rel
        games.append({
            "game_key": game_key,
            "label_path": label,
            "selection_sha256": hashlib.sha256(f"{SEED}|{game_key}".encode()).hexdigest(),
        })

    if len(games) < PACKAGE_SIZE:
        raise SystemExit(f"STOP_DATA_COVERAGE: only {len(games)} SoccerNet label games")

    games.sort(key=lambda r: (r["selection_sha256"], r["game_key"]))
    index = {
        "series": "A",
        "semantics": "RESEARCH_DEVELOPMENT_ONLY_NOT_PROTECTED",
        "source": "SoccerNet-v2 action spotting labels",
        "sdk": "SoccerNet==0.1.62",
        "seed": SEED,
        "package_size_standard": PACKAGE_SIZE,
        "total_games": len(games),
        "packages": [],
        "warning": "A-series is not eligible as an independent protected confirmation reserve.",
    }

    for offset in range(0, len(games), PACKAGE_SIZE):
        pkg_no = START_INDEX + offset // PACKAGE_SIZE
        pkg_id = f"A{pkg_no:02d}"
        rows = games[offset:offset + PACKAGE_SIZE]
        stage = args.output / f"_{pkg_id}"
        if stage.exists():
            shutil.rmtree(stage)
        (stage / "labels").mkdir(parents=True)

        manifest_path = stage / "MANIFEST.jsonl"
        with manifest_path.open("w", encoding="utf-8") as mf:
            for rank, row in enumerate(rows, 1):
                dest = stage / "labels" / f"{rank:04d}.json"
                shutil.copy2(row["label_path"], dest)
                item = {
                    "package_id": pkg_id,
                    "rank": rank,
                    "game_key": row["game_key"],
                    "selection_sha256": row["selection_sha256"],
                    "file": f"labels/{rank:04d}.json",
                }
                mf.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

        manifest_sha = sha256_file(manifest_path)
        meta = {
            "package_id": pkg_id,
            "series": "A",
            "semantics": "RESEARCH_DEVELOPMENT_ONLY_NOT_PROTECTED",
            "source": "SoccerNet-v2 action spotting labels",
            "sdk": "SoccerNet==0.1.62",
            "match_count": len(rows),
            "standard_package_size": PACKAGE_SIZE,
            "tail_package": len(rows) < PACKAGE_SIZE,
            "seed": SEED,
            "manifest_sha256": manifest_sha,
            "contains_videos": False,
            "nda_video_content_downloaded": False,
            "warning": "Do not use this A-series package as an independent protected confirmation sample.",
        }
        (stage / "PACKAGE.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        zip_path = args.output / f"{pkg_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage))

        index["packages"].append({
            "id": pkg_id,
            "matches": len(rows),
            "tail": len(rows) < PACKAGE_SIZE,
            "file": zip_path.name,
            "sha256": sha256_file(zip_path),
            "manifest_sha256": manifest_sha,
            "size_bytes": zip_path.stat().st_size,
        })
        shutil.rmtree(stage)

    (args.output / "A_SERIES_SOCCERNET_INDEX.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
