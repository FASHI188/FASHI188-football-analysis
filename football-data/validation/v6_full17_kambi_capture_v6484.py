#!/usr/bin/env python3
"""V6.48.4 live capture wrapper with a fail-closed M11 provider-alias overlay.

Uses the V6.48.2 synchronized Kambi acquisition engine unchanged, but points its identity
input to the phase-aware V6.48.4 live-capture registry plus a small, competition-scoped
set of deterministic provider aliases recovered prospectively. No fuzzy matching is
introduced and ambiguous/cross-competition names remain unresolved.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_full17_kambi_capture_v6482 as base

SOURCE_REGISTRY = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
OVERLAY_REGISTRY = ROOT / "runtime" / "results" / "m11_safe_capture_identity_overlay.json"

# Every alias below is competition-scoped and names the same club as the frozen
# canonical identity. These are explicit deterministic synonyms, not fuzzy rules.
SAFE_PROVIDER_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "SUI_SuperLeague": {
        "FC Basel": ("Basel",),
        "FC Lugano": ("Lugano",),
        "FC Thun": ("Thun",),
    },
    "FRA_Ligue1": {
        "AJ Auxerre": ("Auxerre",),
        "Le Havre AC": ("Le Havre",),
        "Stade Rennais": ("Rennes",),
    },
    "ARG_Primera": {
        "Belgrano (Córdoba)": ("Belgrano",),
    },
    "SCO_Premiership": {
        "Rangers": ("Rangers FC",),
        "Dundee": ("Dundee FC",),
    },
    "NED_Eredivisie": {
        "Feyenoord Rotterdam": ("Feyenoord",),
        "Heerenveen": ("SC Heerenveen",),
        "Telstar": ("SC Telstar",),
        "Ajax Amsterdam": ("Ajax",),
    },
    "ENG_PremierLeague": {
        "Brighton & Hove Albion": ("Brighton",),
    },
    "POR_PrimeiraLiga": {
        "C.D. Nacional": ("Nacional Madeira",),
        "Estrela": ("CF Estrela",),
    },
    "GER_Bundesliga": {
        "Schalke 04": ("FC Schalke 04",),
    },
    "ESP_LaLiga": {
        "Deportivo La Coruña": ("Deportivo A Coruña",),
    },
}


def build_overlay_registry() -> Path:
    data = json.loads(SOURCE_REGISTRY.read_text(encoding="utf-8"))
    competitions = data.get("competitions") or {}
    applied: list[dict[str, str]] = []

    for cid, by_canonical in SAFE_PROVIDER_ALIASES.items():
        comp = competitions.get(cid)
        if not isinstance(comp, dict):
            raise ValueError(f"M11 safe alias competition missing:{cid}")
        teams = comp.get("teams") or []
        team_by_name = {str(t.get("canonical_name") or ""): t for t in teams if isinstance(t, dict)}
        for canonical, aliases in by_canonical.items():
            team = team_by_name.get(canonical)
            if team is None:
                raise ValueError(f"M11 safe alias canonical missing:{cid}:{canonical}")
            tokens = list(team.get("provider_alias_tokens") or [])
            for alias in aliases:
                token = base.norm(alias)
                if not token:
                    raise ValueError(f"M11 safe alias normalizes empty:{cid}:{canonical}:{alias}")
                if token not in tokens:
                    tokens.append(token)
                applied.append({"competition_id": cid, "canonical_name": canonical, "provider_alias_token": token})
            team["provider_alias_tokens"] = tokens

    # Re-run the same fail-closed collision semantics used by the collector, but on
    # normalized tokens so an overlay cannot silently shadow another canonical club.
    for cid, comp in competitions.items():
        seen: dict[str, str] = {}
        for team in comp.get("teams") or []:
            canonical = str(team.get("canonical_name") or "").strip()
            if not canonical:
                continue
            tokens = [str(team.get("normalized_identity") or base.norm(canonical))]
            tokens.extend(str(x) for x in (team.get("provider_alias_tokens") or []))
            for raw in tokens:
                token = base.norm(raw)
                if not token:
                    continue
                previous = seen.get(token)
                if previous is not None and previous != canonical:
                    raise ValueError(f"M11 safe alias collision:{cid}:{token}:{previous}/{canonical}")
                seen[token] = canonical

    data["m11_safe_provider_alias_overlay"] = {
        "schema_version": "football3-r43gov0-m11-safe-provider-alias-overlay-v1",
        "source_registry": str(SOURCE_REGISTRY.relative_to(ROOT)),
        "fuzzy_matching": False,
        "outcome_data_used": False,
        "competition_scoped": True,
        "applied_alias_count": len(applied),
        "applied": applied,
    }
    OVERLAY_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    OVERLAY_REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return OVERLAY_REGISTRY


base.REGISTRY = build_overlay_registry()

if __name__ == "__main__":
    raise SystemExit(base.main())
