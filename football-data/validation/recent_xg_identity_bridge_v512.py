#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "recent_xg_shadow_v511.json"
XG_ROOT = ROOT / "evidence" / "xg" / "understat_2025_26"
OUT_ROOT = ROOT / "evidence" / "xg" / "understat_2025_26_linked"
MANIFEST = ROOT / "manifests" / "recent_xg_identity_bridge_v512_status.json"


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    replacements = {
        "fc": " ", "cf": " ", "afc": " ", "calcio": " ", "club": " ",
        "deportivo": " ", "football": " ", "fussball": " ", "saint": "st"
    }
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [replacements.get(token, token) for token in text.split()]
    return " ".join(token for token in tokens if token).strip()


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _load_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _parse_date(value: str) -> str:
    return str(value).split(" ", 1)[0]


def _read_processed(path: Path):
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("HomeTeam") or not row.get("AwayTeam"):
                continue
            try:
                dt = datetime.strptime(row["Date"], "%d/%m/%Y")
                hg, ag = int(float(row["FTHG"])), int(float(row["FTAG"]))
            except Exception:
                continue
            rows.append({
                "date": dt.date().isoformat(),
                "home_team": row["HomeTeam"],
                "away_team": row["AwayTeam"],
                "home_goals": hg,
                "away_goals": ag,
                "source_code": row.get("source_code") or "",
                "stage": row.get("stage") or ""
            })
    return rows


def _link_domain(competition_id: str):
    xg_path = XG_ROOT / f"{competition_id}.jsonl"
    processed_path = ROOT / "processed" / competition_id / "2025-26.csv"
    if not xg_path.exists():
        raise RuntimeError(f"missing xG file: {xg_path}")
    if not processed_path.exists():
        raise RuntimeError(f"missing processed season: {processed_path}")

    xg_rows = _load_jsonl(xg_path)
    official = _read_processed(processed_path)
    by_date_score = defaultdict(list)
    for idx, row in enumerate(official):
        by_date_score[(row["date"], row["home_goals"], row["away_goals"])].append((idx, row))

    linked, unmatched, ambiguous = [], [], []
    used_official = set()
    max_score_conflict = 0

    for xg in xg_rows:
        key = (_parse_date(xg["match_datetime_source"]), int(xg["home_goals"]), int(xg["away_goals"]))
        candidates = by_date_score.get(key, [])
        ranked = []
        for idx, row in candidates:
            if idx in used_official:
                continue
            hs = _sim(xg["home_team_source"], row["home_team"])
            aas = _sim(xg["away_team_source"], row["away_team"])
            score = (hs + aas) / 2.0
            ranked.append((score, hs, aas, idx, row))
        ranked.sort(reverse=True, key=lambda item: item[0])
        if not ranked:
            unmatched.append({"reason": "no_same_date_score_candidate", "xg": xg})
            continue
        best = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -1.0
        if best[0] < 0.72 or best[1] < 0.65 or best[2] < 0.65:
            unmatched.append({"reason": "team_similarity_below_gate", "best_score": best[0], "xg": xg, "candidate": best[4]})
            continue
        if len(ranked) > 1 and best[0] - second_score < 0.10:
            ambiguous.append({"reason": "best_candidate_margin_below_gate", "best": best[0], "second": second_score, "xg": xg})
            continue
        used_official.add(best[3])
        row = best[4]
        linked.append({
            **xg,
            "official_date": row["date"],
            "official_home_team": row["home_team"],
            "official_away_team": row["away_team"],
            "official_source_code": row["source_code"],
            "official_stage": row["stage"],
            "home_name_similarity": best[1],
            "away_name_similarity": best[2],
            "identity_score": best[0],
            "identity_bridge_status": "PASS_UNIQUE_DATE_SCORE_TEAM_MATCH"
        })

    coverage = len(linked) / max(1, len(xg_rows))
    out_path = OUT_ROOT / f"{competition_id}.jsonl"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in linked), encoding="utf-8")
    status = "PASS" if coverage >= 0.98 and not ambiguous else "FAIL"
    return {
        "competition_id": competition_id,
        "status": status,
        "xg_row_count": len(xg_rows),
        "official_match_count": len(official),
        "linked_count": len(linked),
        "coverage": coverage,
        "unmatched_count": len(unmatched),
        "ambiguous_count": len(ambiguous),
        "unused_official_count": len(official) - len(used_official),
        "minimum_identity_score": min((float(row["identity_score"]) for row in linked), default=None),
        "output_path": str(out_path.relative_to(ROOT)),
        "unmatched_examples": unmatched[:5],
        "ambiguous_examples": ambiguous[:5]
    }


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    reports, failures = {}, {}
    for competition_id in cfg["domains"]:
        try:
            reports[competition_id] = _link_domain(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"
    passed = [k for k, v in reports.items() if v["status"] == "PASS"]
    payload = {
        "schema_version": "V5.1.2-recent-xg-identity-bridge-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "requested_domains": list(cfg["domains"].keys()),
        "passed_domains": passed,
        "reports": reports,
        "failures": failures,
        "status": "PASS" if len(passed) == len(cfg["domains"]) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Only unique date+score+team-identity links may enter recent-season xG shadow evaluation."
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
