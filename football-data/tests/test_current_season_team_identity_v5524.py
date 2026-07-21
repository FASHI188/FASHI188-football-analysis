#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "current_season_team_identity_v5524.json"
RECEIPT = ROOT / "manifests" / "current_season_team_identity_v5524_status.json"
EXPECTED_COUNTS = {
    "ESP_LaLiga": 20,
    "GER_Bundesliga": 18,
    "FRA_Ligue1": 18,
    "POR_PrimeiraLiga": 18,
}
CRITICAL_ALIASES = {
    "ESP_LaLiga": {
        "Racing Santander": "Racing Santander",
        "Real Racing Club": "Racing Santander",
        "Deportivo de La Coruña": "Deportivo de la Coruna",
        "Atlético de Madrid": "Ath Madrid",
        "Celta de Vigo": "Celta Vigo",
    },
    "GER_Bundesliga": {
        "SV Elversberg": "Elversberg",
        "SC Paderborn 07": "Paderborn 07",
        "Borussia Mönchengladbach": "M'gladbach",
        "FC Schalke 04": "Schalke 04",
    },
    "FRA_Ligue1": {
        "Le Mans FC": "Le Mans",
        "RC Strasbourg Alsace": "RC Strasbourg Alsace",
    },
    "POR_PrimeiraLiga": {
        "Marítimo M.": "Maritimo",
        "Académico": "Academico",
        "FC Famalicão": "FC Famalicão",
    },
}


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def build_aliases(comp: dict) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for row in comp.get("teams", []):
        canonical = str(row.get("canonical_name") or "")
        if not canonical:
            raise AssertionError("blank canonical_name")
        for candidate in [canonical, row.get("official_name"), *(row.get("aliases") or [])]:
            if not candidate:
                continue
            token = norm(candidate)
            previous = aliases.get(token)
            if previous is not None and previous != canonical:
                raise AssertionError(f"ambiguous alias {candidate!r}: {previous!r} vs {canonical!r}")
            aliases[token] = canonical
    return aliases


def main() -> int:
    raw = REGISTRY.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert data.get("schema_version") == "V5.5.24-current-season-team-identity-r1"
    assert data.get("season") == "2026/27"
    assert data.get("formal_weight_change") is False
    assert data.get("probability_change") is False
    policy = data.get("policy") or {}
    assert policy.get("unregistered_current_season_club") == "FAIL_CLOSED"
    assert policy.get("fuzzy_cross_club_substitution") == "PROHIBITED"
    assert policy.get("historical_team_strength_fallback_for_2026_27_identity") is False

    competitions = data.get("competitions") or {}
    reports = {}
    for cid, expected_count in EXPECTED_COUNTS.items():
        comp = competitions.get(cid)
        assert isinstance(comp, dict), cid
        teams = comp.get("teams") or []
        assert int(comp.get("team_count")) == expected_count
        assert len(teams) == expected_count
        canonical = [str(row.get("canonical_name")) for row in teams]
        assert len(canonical) == len(set(canonical)), f"duplicate canonical in {cid}"
        aliases = build_aliases(comp)
        checks = {}
        for source, expected in CRITICAL_ALIASES[cid].items():
            resolved = aliases.get(norm(source))
            assert resolved == expected, f"{cid}: {source} -> {resolved}, expected {expected}"
            checks[source] = resolved
        reports[cid] = {
            "team_count": expected_count,
            "canonical_unique_count": len(set(canonical)),
            "normalized_alias_count": len(aliases),
            "critical_alias_checks": checks,
            "official_source_count": len(comp.get("official_sources") or []),
        }

    receipt = {
        "schema_version": "V5.5.24-current-season-team-identity-acceptance-r1",
        "status": "PASS",
        "registry_path": str(REGISTRY.relative_to(ROOT)),
        "registry_sha256": hashlib.sha256(raw).hexdigest(),
        "season": data.get("season"),
        "competition_count": len(EXPECTED_COUNTS),
        "expected_team_counts": EXPECTED_COUNTS,
        "reports": reports,
        "alias_ambiguity_found": False,
        "fuzzy_cross_club_substitution_allowed": False,
        "historical_team_strength_identity_fallback_allowed": False,
        "formal_weight_change": False,
        "probability_change": False,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
