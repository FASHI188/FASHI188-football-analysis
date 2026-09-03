from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import runtime as rt
import source_compat as base
import source_compat_v3 as v3

ADAPTER_SCHEMA = "football3-frozen-source-compat-v4"
MAX_IDENTITY_DATE_DRIFT_DAYS = 21


def _load_xg_labels(history, understat_db, confirmation_dir):
    source_rows, _ = v3._source_rows(understat_db, confirmation_dir)
    team_map, _ = v3._learn_team_map(history, source_rows)

    by_teams = {}
    exact = set()
    for f in history:
        if f.competition_id not in set(rt.BIG5.values()) or f.season not in ("2022/23", "2023/24", "2024/25"):
            continue
        h = rt._normalize_team(f.home_team_name); a = rt._normalize_team(f.away_team_name)
        exact.add((f.competition_id, f.kickoff.date().isoformat(), h, a))
        by_teams.setdefault((f.competition_id, h, a), []).append(f)

    replacements = {}
    evidence = []
    for s in source_rows:
        comp = str(s["competition_id"])
        mh = team_map[(comp, str(s["home_team"]))]; ma = team_map[(comp, str(s["away_team"]))]
        h = rt._normalize_team(mh); a = rt._normalize_team(ma); source_date = str(s["source_date"])
        if (comp, source_date, h, a) in exact:
            continue
        sd = datetime.fromisoformat(source_date).date()
        candidates = [f for f in by_teams.get((comp, h, a), []) if abs((f.kickoff.date() - sd).days) <= MAX_IDENTITY_DATE_DRIFT_DAYS]
        if len(candidates) != 1:
            raise rt.RuntimeGateError(
                f"postponed/resumed fixture identity ambiguous: {comp} {source_date} {mh} v {ma} candidates={len(candidates)}"
            )
        f = candidates[0]
        if f.fixture_id in replacements:
            raise rt.RuntimeGateError(f"duplicate date reconciliation target: {f.fixture_id}")
        offset = (f.kickoff.date() - sd).days
        shifted = f.kickoff.replace(year=sd.year, month=sd.month, day=sd.day)
        replacements[f.fixture_id] = replace(f, kickoff=shifted)
        evidence.append({
            "competition_id": comp, "fixture_id": f.fixture_id, "home_team": f.home_team_name, "away_team": f.away_team_name,
            "source_date": source_date, "v1_date": f.kickoff.date().isoformat(), "offset_days": offset,
            "rule": f"same_competition_and_schedule_mapped_teams_unique_within_plus_minus_{MAX_IDENTITY_DATE_DRIFT_DAYS}_calendar_days",
            "label_free": True,
        })
    if len(evidence) > 5:
        raise rt.RuntimeGateError(f"too many postponed/resumed date reconciliations: {len(evidence)}")

    adjusted = [replacements.get(f.fixture_id, f) for f in history]
    labels, meta = v3._load_xg_labels(adjusted, understat_db, confirmation_dir)
    evidence.sort(key=lambda r: (r["source_date"], r["competition_id"], r["fixture_id"]))
    meta = dict(meta)
    meta["adapter_schema"] = ADAPTER_SCHEMA
    meta["prejoin_date_reconciliation_n"] = len(evidence)
    meta["prejoin_date_reconciliation_sha256"] = rt._sha_bytes(rt._canon_bytes(evidence))
    meta["prejoin_date_reconciliation"] = evidence
    meta["date_identity_semantics"] = "schedule/team identity only; no score/xG/odds used for date reconciliation"
    return labels, meta


def install() -> dict[str, Any]:
    meta = base.install()
    rt.load_xg_labels = _load_xg_labels
    meta = dict(meta)
    meta["adapter_schema"] = ADAPTER_SCHEMA
    meta["team_identity_mapping"] = "label-free schedule cooccurrence, unique one-to-one, >=80% support"
    meta["date_identity_reconciliation"] = f"label-free exact mapped teams, unique candidate within +/-{MAX_IDENTITY_DATE_DRIFT_DAYS} calendar days"
    return meta
