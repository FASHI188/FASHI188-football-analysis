#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
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


def _date_delta_days(a: str, b: str) -> int:
    da = datetime.fromisoformat(a).date()
    db = datetime.fromisoformat(b).date()
    return (db - da).days


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


def _infer_stable_aliases(xg_rows, official, by_date_score):
    """Infer provider-name -> official-name only from unique exact date+score fingerprints."""
    counts = defaultdict(Counter)
    for xg in xg_rows:
        key = (_parse_date(xg["match_datetime_source"]), int(xg["home_goals"]), int(xg["away_goals"]))
        candidates = by_date_score.get(key, [])
        if len(candidates) != 1:
            continue
        _, row = candidates[0]
        counts[xg["home_team_source"]][row["home_team"]] += 1
        counts[xg["away_team_source"]][row["away_team"]] += 1

    aliases = {}
    audit = {}
    for source_name, counter in counts.items():
        ranked = counter.most_common()
        if not ranked:
            continue
        best_name, best_count = ranked[0]
        total = sum(counter.values())
        second_count = ranked[1][1] if len(ranked) > 1 else 0
        share = best_count / max(1, total)
        # Stable season alias: at least two independent unique fingerprints and
        # no material conflicting mapping.
        if best_count >= 2 and share >= 0.90 and second_count <= 1:
            aliases[source_name] = best_name
        audit[source_name] = {
            "selected": best_name if source_name in aliases else None,
            "best_count": best_count,
            "total_evidence": total,
            "best_share": share,
            "alternatives": ranked[:3]
        }
    return aliases, audit


def _alias_exact(source_name: str, official_name: str, aliases: dict[str, str]) -> bool:
    mapped = aliases.get(source_name)
    return mapped == official_name if mapped is not None else _norm(source_name) == _norm(official_name)


def _rank_same_date_candidates(xg, candidates, aliases):
    ranked = []
    for idx, row in candidates:
        home_exact = _alias_exact(xg["home_team_source"], row["home_team"], aliases)
        away_exact = _alias_exact(xg["away_team_source"], row["away_team"], aliases)
        hs = 1.0 if home_exact else _sim(aliases.get(xg["home_team_source"], xg["home_team_source"]), row["home_team"])
        aas = 1.0 if away_exact else _sim(aliases.get(xg["away_team_source"], xg["away_team_source"]), row["away_team"])
        score = (hs + aas) / 2.0
        ranked.append((score, hs, aas, home_exact and away_exact, idx, row))
    ranked.sort(reverse=True, key=lambda item: (item[3], item[0]))
    return ranked


def _bounded_date_offset_candidate(xg, official, used_official, aliases):
    source_date = _parse_date(xg["match_datetime_source"])
    matches = []
    for idx, row in enumerate(official):
        if idx in used_official:
            continue
        if int(row["home_goals"]) != int(xg["home_goals"]) or int(row["away_goals"]) != int(xg["away_goals"]):
            continue
        offset = _date_delta_days(source_date, row["date"])
        if abs(offset) > 2:
            continue
        if not _alias_exact(xg["home_team_source"], row["home_team"], aliases):
            continue
        if not _alias_exact(xg["away_team_source"], row["away_team"], aliases):
            continue
        matches.append((idx, row, offset))
    return matches


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

    aliases, alias_audit = _infer_stable_aliases(xg_rows, official, by_date_score)
    linked, unmatched, ambiguous = [], [], []
    used_official = set()
    counters = Counter()

    for xg in xg_rows:
        source_date = _parse_date(xg["match_datetime_source"])
        key = (source_date, int(xg["home_goals"]), int(xg["away_goals"]))
        candidates = [(idx, row) for idx, row in by_date_score.get(key, []) if idx not in used_official]
        chosen = None
        method = None
        date_offset_days = 0
        hs = aas = identity_score = None

        if candidates:
            ranked = _rank_same_date_candidates(xg, candidates, aliases)
            if len(ranked) == 1:
                best = ranked[0]
                chosen = (best[4], best[5])
                hs, aas, identity_score = best[1], best[2], best[0]
                method = "PASS_UNIQUE_DATE_SCORE_FINGERPRINT"
                counters["unique_date_score"] += 1
            else:
                exact = [item for item in ranked if item[3]]
                if len(exact) == 1:
                    best = exact[0]
                    chosen = (best[4], best[5])
                    hs, aas, identity_score = best[1], best[2], best[0]
                    method = "PASS_STABLE_ALIAS_DATE_SCORE_DISAMBIGUATION"
                    counters["stable_alias_disambiguation"] += 1
                else:
                    best = ranked[0]
                    second_score = ranked[1][0]
                    if best[0] >= 0.72 and best[1] >= 0.65 and best[2] >= 0.65 and best[0] - second_score >= 0.10:
                        chosen = (best[4], best[5])
                        hs, aas, identity_score = best[1], best[2], best[0]
                        method = "PASS_MULTI_CANDIDATE_TEAM_DISAMBIGUATION"
                        counters["fuzzy_disambiguation"] += 1

        if chosen is None:
            offset_matches = _bounded_date_offset_candidate(xg, official, used_official, aliases)
            if len(offset_matches) == 1:
                idx, row, date_offset_days = offset_matches[0]
                chosen = (idx, row)
                hs = aas = identity_score = 1.0
                method = "PASS_STABLE_ALIAS_EXACT_SCORE_BOUNDED_DATE_OFFSET"
                counters["bounded_date_offset"] += 1
            elif len(offset_matches) > 1:
                ambiguous.append({
                    "reason": "multiple_alias_exact_score_candidates_within_2_days",
                    "candidate_count": len(offset_matches),
                    "xg": xg
                })
                continue

        if chosen is None:
            unmatched.append({
                "reason": "no_unique_identity_after_alias_and_bounded_date_audit",
                "xg": xg,
                "same_date_candidate_count": len(candidates)
            })
            continue

        idx, row = chosen
        used_official.add(idx)
        linked.append({
            **xg,
            "official_date": row["date"],
            "official_home_team": row["home_team"],
            "official_away_team": row["away_team"],
            "official_source_code": row["source_code"],
            "official_stage": row["stage"],
            "home_name_similarity": hs,
            "away_name_similarity": aas,
            "identity_score": identity_score,
            "date_offset_days": date_offset_days,
            "identity_bridge_status": method
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
        "link_method_counts": dict(counters),
        "stable_alias_count": len(aliases),
        "stable_aliases": aliases,
        "stable_alias_evidence": alias_audit,
        "max_abs_date_offset_days": max((abs(int(row["date_offset_days"])) for row in linked), default=0),
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
        "schema_version": "V5.1.2-recent-xg-identity-bridge-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "requested_domains": list(cfg["domains"].keys()),
        "passed_domains": passed,
        "reports": reports,
        "failures": failures,
        "status": "PASS" if len(passed) == len(cfg["domains"]) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Aliases are learned only from unique exact date+score fingerprints. Date offsets up to two days are accepted only when stable home/away aliases plus exact final score identify exactly one official match. Any ambiguity remains fail-closed."
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
