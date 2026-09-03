#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live

SCHEMA = "football3-live-extra-schema-patch-v1"


def _pick(raw: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(raw.get(name) or "").strip()
        if value:
            return value
    return ""


def _extra_rows(repo_root: Path, comp: str, source_code: str, leagues: list[str], lower, upper):
    url = f"https://www.football-data.co.uk/new/{source_code}.csv"
    payload, payload_sha = live._fetch(url)
    rows = live._decode_csv(payload)
    selected: list[live.V1Row] = []
    observed_leagues: set[str] = set()
    headers: set[str] = set()
    wanted = {live._norm(x) for x in leagues}

    for raw in rows:
        headers.update(raw)
        league = _pick(raw, "League", "Div", "Competition")
        if league:
            observed_leagues.add(league)
        if wanted and league and live._norm(league) not in wanted:
            continue

        g = live._goals(raw)
        if g is None:
            continue
        d = live._parse_date(_pick(raw, "Date", "date"))
        if not (lower <= d < upper):
            continue

        season = live._extra_season(comp, _pick(raw, "Season", "season"), d)
        if season is None:
            continue

        # football-data.co.uk extra-league files use Home/Away/HG/AG, while
        # main-league files use HomeTeam/AwayTeam/FTHG/FTAG. Accept only these
        # documented schema variants; never infer identity from scores or xG.
        home = _pick(raw, "HomeTeam", "Home", "home_team")
        away = _pick(raw, "AwayTeam", "Away", "away_team")
        if not home or not away:
            raise live.AcquisitionError(
                f"{comp}: completed extra row missing team after documented schema resolution; headers={sorted(headers)}"
            )
        selected.append(
            live._v1row(repo_root, comp, season, d, home, away, g[0], g[1], url, payload_sha)
        )

    source = {
        "competition_id": comp,
        "url": url,
        "sha256": payload_sha,
        "rows_in_window": len(selected),
        "source_class": "football_data_extra",
        "schema_variant": "HomeTeam/AwayTeam or Home/Away; FTHG/FTAG or HG/AG",
        "observed_headers": sorted(headers),
        "observed_leagues": sorted(observed_leagues)[:50],
    }
    return selected, [source]


def install() -> dict[str, Any]:
    live._extra_rows = _extra_rows
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "identity_policy": "documented column aliases only; no result/xG-based identity matching",
        "model_parameters_or_weights_changed": False,
    }
