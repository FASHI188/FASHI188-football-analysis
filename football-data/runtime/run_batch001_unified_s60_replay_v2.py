#!/usr/bin/env python3
"""Metadata-only identity refinement for the unified Batch-001 replay.

The underlying numerical runner remains run_batch001_unified_s60_replay.py. This
shim changes only target identity resolution: exact aliases that conflict inside a
competition may be disambiguated by league + date + already-resolved opponent in
safe fixture metadata. No outcome/status/stat field or fuzzy name matching is used.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_batch001_unified_s60_replay as base
from identity.team_identity import RESOLVED, TeamIdentityResolver


def build_context(teams, fixtures, cmap: dict[str, str], provenance: str):
    name_cols = [c for c in base.text_columns(teams) if c in {"name", "fd_name"} or "name" in c.casefold()]
    team_meta: dict[str, set[str]] = {}
    for row in teams.itertuples(index=False):
        d = row._asdict()
        tid = str(int(d["id"]))
        aliases = set()
        for col in name_cols:
            value = d.get(col)
            if value is not None and str(value).casefold() != "nan":
                key = base.alias_key(value)
                if key:
                    aliases.add(key)
        team_meta[tid] = aliases

    records = []
    alias_ids: dict[str, dict[str, set[str]]] = {}
    league_text = fixtures["league_id"].astype(str)
    for division, cid in cmap.items():
        q = fixtures[league_text == str(cid)]
        allowed = set(q["home_team_id"].astype(str)) | set(q["away_team_id"].astype(str))
        ns = f"fd:{division.casefold()}"
        local: dict[str, set[str]] = {}
        for tid in sorted(allowed):
            aliases = team_meta.get(tid, set())
            records.append({
                "source_namespace": ns,
                "source_team_id": tid,
                "canonical_team_id": tid,
                "mapping_method": "provider_team_id_competition_scoped",
                "provenance_hash": provenance,
            })
            for alias in sorted(aliases):
                local.setdefault(alias, set()).add(tid)
                records.append({
                    "source_namespace": ns,
                    "approved_name_alias": alias,
                    "canonical_team_id": tid,
                    "mapping_method": "provider_metadata_exact_alias_competition_scoped",
                    "provenance_hash": provenance,
                })
        alias_ids[division] = local
    return TeamIdentityResolver(records), alias_ids


def _candidate_id_from_fixture(
    fixtures,
    *,
    cid: str,
    date: str,
    side: str,
    alias_candidates: set[str],
    opponent_id: str | None,
    opponent_candidates: set[str] | None = None,
):
    if not alias_candidates:
        return None, []
    d0 = pd.Timestamp(date, tz="UTC")
    q = fixtures[(fixtures["league_id"].astype(str) == str(cid))
                 & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=1))
                 & (fixtures["date_utc"] < d0 + pd.Timedelta(days=2))].copy()
    q["_h"] = q["home_team_id"].astype(str)
    q["_a"] = q["away_team_id"].astype(str)
    if side == "home":
        q = q[q["_h"].isin(alias_candidates)]
        if opponent_id is not None:
            q = q[q["_a"] == str(opponent_id)]
        elif opponent_candidates:
            q = q[q["_a"].isin(opponent_candidates)]
        ids = sorted(set(q["_h"]))
    else:
        q = q[q["_a"].isin(alias_candidates)]
        if opponent_id is not None:
            q = q[q["_h"] == str(opponent_id)]
        elif opponent_candidates:
            q = q[q["_h"].isin(opponent_candidates)]
        ids = sorted(set(q["_a"]))
    fixtures_found = [
        {"id": str(int(row["id"])), "date_utc": row["date_utc"].isoformat(),
         "home_team_id": str(row["_h"]), "away_team_id": str(row["_a"])}
        for _, row in q.iterrows()
    ]
    return (ids[0] if len(ids) == 1 else None), fixtures_found


def resolve_targets(lock: dict, work: Path):
    tp, lp, fp = work / "teams.parquet", work / "leagues.parquet", work / "fixtures_safe.parquet"
    base.download(f"{base.HF}/teams.parquet?download=true", tp)
    base.download(f"{base.HF}/leagues.parquet?download=true", lp)
    base.download(f"{base.HF}/fixtures.parquet?download=true", fp)
    teams, leagues = pd.read_parquet(tp), pd.read_parquet(lp)
    fixtures = pd.read_parquet(fp, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id"])
    fixtures["date_utc"] = pd.to_datetime(fixtures["date_utc"], utc=True)
    divisions = {str(x["division"]) for x in lock["rows"]}
    cmap = base.competition_map(leagues, divisions)
    resolver, alias_ids = build_context(teams, fixtures, cmap, base.sha256(tp))

    mapped, audit = [], []
    for z in lock["rows"]:
        div = str(z["division"])
        ns = f"fd:{div.casefold()}"
        home_alias, away_alias = base.alias_key(z["home"]), base.alias_key(z["away"])
        hid_override = base.TEAM_ID_OVERRIDES.get((div, str(z["home"])))
        aid_override = base.TEAM_ID_OVERRIDES.get((div, str(z["away"])))
        hr = resolver.resolve(ns, hid_override, home_alias) if hid_override else resolver.resolve(ns, None, home_alias)
        ar = resolver.resolve(ns, aid_override, away_alias) if aid_override else resolver.resolve(ns, None, away_alias)
        contextual = []

        hcand = set(alias_ids.get(div, {}).get(home_alias, set()))
        acand = set(alias_ids.get(div, {}).get(away_alias, set()))
        if hr.status != RESOLVED:
            hid, found = _candidate_id_from_fixture(
                fixtures, cid=cmap[div], date=z["date"], side="home",
                alias_candidates=hcand,
                opponent_id=ar.canonical_team_id if ar.status == RESOLVED else None,
                opponent_candidates=acand if ar.status != RESOLVED else None,
            )
            if hid is not None:
                hr = resolver.resolve(ns, hid, home_alias)
                contextual.append({"side": "home", "source_team_id": hid, "fixture_candidates": found})
        if ar.status != RESOLVED:
            aid, found = _candidate_id_from_fixture(
                fixtures, cid=cmap[div], date=z["date"], side="away",
                alias_candidates=acand,
                opponent_id=hr.canonical_team_id if hr.status == RESOLVED else None,
                opponent_candidates=hcand if hr.status != RESOLVED else None,
            )
            if aid is not None:
                ar = resolver.resolve(ns, aid, away_alias)
                contextual.append({"side": "away", "source_team_id": aid, "fixture_candidates": found})

        rec = {
            "batch_index": z["batch_index"], "division": div, "date": z["date"],
            "home": z["home"], "away": z["away"],
            "home_resolution": hr.to_dict(), "away_resolution": ar.to_dict(),
            "home_override_used": hid_override is not None, "away_override_used": aid_override is not None,
            "contextual_exact_fixture_disambiguation": contextual,
        }
        if hr.status != RESOLVED or ar.status != RESOLVED:
            audit.append(rec)
            continue
        d0 = pd.Timestamp(z["date"], tz="UTC")
        match = fixtures[(fixtures["league_id"].astype(str) == cmap[div])
                         & (fixtures["home_team_id"].astype(str) == hr.canonical_team_id)
                         & (fixtures["away_team_id"].astype(str) == ar.canonical_team_id)
                         & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=1))
                         & (fixtures["date_utc"] < d0 + pd.Timedelta(days=2))]
        rec["fixture_candidates"] = [{"id": str(int(x.id)), "date_utc": x.date_utc.isoformat()} for x in match.itertuples(index=False)]
        audit.append(rec)
        if len(match) != 1:
            continue
        x = next(match.itertuples(index=False))
        mapped.append({
            **z,
            "fixture_id": str(int(x.id)), "kickoff_utc": x.date_utc.isoformat(),
            "competition_id": cmap[div], "home_team": hr.canonical_team_id,
            "away_team": ar.canonical_team_id,
            "nominal_cutoff_utc": (x.date_utc - pd.Timedelta(hours=24)).isoformat(),
        })

    for p in (tp, lp, fp):
        p.unlink(missing_ok=True)
    if len(mapped) != 100:
        unresolved = [r for r in audit if r["home_resolution"]["status"] != RESOLVED
                      or r["away_resolution"]["status"] != RESOLVED
                      or len(r.get("fixture_candidates", [])) != 1]
        diagnostic = {
            "schema_version": "football3-batch001-unified-mapping-diagnostic-v3",
            "status": "FAIL_MAPPING_INCOMPLETE",
            "mapped": len(mapped), "expected": 100, "unresolved_count": len(unresolved),
            "competition_map": cmap, "unresolved": unresolved, "all_audit": audit,
            "identity_scope": "competition_scoped_provider_ids_plus_exact_fixture_context_for_alias_conflicts",
            "context_fields": ["league_id", "date_utc", "home_team_id", "away_team_id"],
            "outcome_columns_read": False, "status_columns_read": False,
            "fuzzy_matching_enabled": False,
        }
        base.OUT.mkdir(parents=True, exist_ok=True)
        (base.OUT / "mapping_diagnostic.json").write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise RuntimeError(f"unified target mapping incomplete {len(mapped)}/100")
    return mapped, audit, cmap, resolver


base.resolve_targets = resolve_targets

if __name__ == "__main__":
    raise SystemExit(base.main())
