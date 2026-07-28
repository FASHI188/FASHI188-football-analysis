#!/usr/bin/env python3
"""V6.48.5 acquire exact-match pre-kickoff context from FotMob preview pages.

Input discovery comes from the open-source Golazo JSON list command, which exposes the
FotMob match page URL for today's upcoming matches.  The page itself is then fetched
directly and its Next.js __NEXT_DATA__ payload is preserved as raw evidence.

Conservative semantics:
* injury/suspension/unavailable evidence is accepted only from subtrees whose key/path
  explicitly contains an availability marker;
* a starting XI is accepted as PREDICTED only when the same subtree contains an
  explicit predicted/expected/probable marker and yields exactly 11 unique names;
* "last starting XI" or an unlabeled lineup is never promoted to predicted XI;
* no fuzzy match identity is used. Team names must resolve through the current exact
  identity surface and kickoff must be within a small tolerance.

This creates evidence for a NEW context-enriched forward freeze. It does not backfill
older V6.5.1 market-first freezes whose event timestamp may precede this observation.
Research only; formal_weight=0.
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
MARKETS = ROOT / "evidence" / "markets_prospective"
OUT_ROOT = ROOT / "evidence" / "match_context_live" / "fotmob"
STATUS = ROOT / "manifests" / "v6_fotmob_match_context_acquire_v6485_status.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MATCH_TOLERANCE = timedelta(minutes=20)
TRANSLATE = str.maketrans({"ø":"o","Ø":"o","ł":"l","Ł":"l","đ":"d","Đ":"d","ð":"d","Ð":"d","þ":"th","Þ":"th","æ":"ae","Æ":"ae","œ":"oe","Œ":"oe"})
AVAIL_MARKERS = ("injur", "suspend", "unavail", "absence", "absent", "doubt")
PREDICT_MARKERS = ("predict", "expected", "probable", "projected")
LINEUP_MARKERS = ("lineup", "starting", "starter", "formation")
NEGATIVE_LINEUP_MARKERS = ("last starting", "previous starting", "last lineup", "recent lineup")
NAME_KEYS = ("name", "playerName", "fullName", "displayName", "shortName")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").translate(TRANSLATE)).casefold()
    chars = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        chars.append(ch if ch.isalnum() else " ")
    return " ".join("".join(chars).split())


def parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        x = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def identity_maps() -> tuple[dict[str, dict[str, str]], str]:
    raw = IDENTITY.read_bytes(); data = json.loads(raw.decode("utf-8"))
    maps: dict[str, dict[str, str]] = {}
    for cid, comp in (data.get("competitions") or {}).items():
        aliases: dict[str, str] = {}
        for team in comp.get("teams") or []:
            if not isinstance(team, dict):
                continue
            canonical = str(team.get("canonical_name") or "").strip()
            if not canonical:
                continue
            for token in [team.get("normalized_identity"), norm(canonical), *(team.get("provider_alias_tokens") or [])]:
                token = str(token or "").strip()
                if not token:
                    continue
                previous = aliases.get(token)
                if previous is not None and previous != canonical:
                    raise RuntimeError(f"identity alias collision {cid}:{token}:{previous}/{canonical}")
                aliases[token] = canonical
        maps[str(cid)] = aliases
    return maps, sha_bytes(raw)


def market_fixtures(now: datetime) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str, str, str], tuple[datetime, Path, dict[str, Any]]] = {}
    if not MARKETS.exists():
        return []
    for path in MARKETS.glob("*.json"):
        raw = load_json(path)
        kickoff = parse_dt(raw.get("kickoff_utc")); observed = parse_dt(raw.get("source_observed_at_utc") or raw.get("freeze_utc"))
        if kickoff is None or observed is None or kickoff <= now or observed >= kickoff:
            continue
        if kickoff > now + timedelta(hours=36):
            continue
        cid = str(raw.get("competition_id") or "").strip(); home = str(raw.get("home_team") or "").strip(); away = str(raw.get("away_team") or "").strip()
        if not cid or not home or not away:
            continue
        key = (cid, kickoff.isoformat(), norm(home), norm(away))
        prev = latest.get(key)
        if prev is None or observed > prev[0]:
            latest[key] = (observed, path, raw)
    out = []
    for key, (observed, path, raw) in latest.items():
        out.append({
            "competition_id": key[0], "kickoff_at": key[1], "home_team": str(raw["home_team"]), "away_team": str(raw["away_team"]),
            "market_observed_at": observed.isoformat(), "market_path": str(path.relative_to(ROOT)), "market_sha256": sha_bytes(path.read_bytes()),
        })
    return sorted(out, key=lambda x: (x["kickoff_at"], x["competition_id"], x["home_team"], x["away_team"]))


def load_golazo(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    return [x for x in (data.get("data") or []) if isinstance(x, dict) and str(x.get("status") or "") == "not_started"]


def canonical_name(cid: str, name: object, aliases: dict[str, dict[str, str]]) -> str | None:
    return (aliases.get(cid) or {}).get(norm(name))


def match_fotmob(fixture: dict[str, Any], rows: list[dict[str, Any]], aliases: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    cid = fixture["competition_id"]; ko = parse_dt(fixture["kickoff_at"])
    if ko is None:
        return None
    candidates = []
    for row in rows:
        rko = parse_dt(row.get("match_time"))
        if rko is None or abs(rko - ko) > MATCH_TOLERANCE:
            continue
        home = ((row.get("home_team") or {}).get("name") if isinstance(row.get("home_team"), dict) else "")
        away = ((row.get("away_team") or {}).get("name") if isinstance(row.get("away_team"), dict) else "")
        ch = canonical_name(cid, home, aliases); ca = canonical_name(cid, away, aliases)
        if ch == fixture["home_team"] and ca == fixture["away_team"]:
            candidates.append(row)
    return candidates[0] if len(candidates) == 1 else None


def fetch_page(page_url: str) -> tuple[bytes, str, str]:
    url = page_url if page_url.startswith("http") else "https://www.fotmob.com" + page_url
    observed = utc_now().isoformat()
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    with urlopen(req, timeout=30) as resp:  # nosec - FotMob public page URL discovered from Golazo/FotMob list
        raw = resp.read(); status = int(getattr(resp, "status", 200))
    if status != 200:
        raise RuntimeError(f"FotMob page HTTP {status}")
    return raw, url, observed


def extract_page_props(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    marker = text.find("__NEXT_DATA__")
    if marker < 0:
        raise ValueError("__NEXT_DATA__ not found")
    start = text.find(">", marker)
    end = text.find("</script>", start + 1)
    if start < 0 or end < 0:
        raise ValueError("Next.js script bounds missing")
    wrapper = json.loads(html_lib.unescape(text[start + 1:end]))
    props = (((wrapper or {}).get("props") or {}).get("pageProps") or {})
    if not isinstance(props, dict):
        raise ValueError("pageProps is not object")
    return props


def iter_nodes(node: Any, path: str = "root") -> Iterable[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_nodes(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_nodes(v, f"{path}[{i}]")


def collect_names(node: Any) -> list[str]:
    out = []
    for _, item in iter_nodes(node):
        if not isinstance(item, dict):
            continue
        for key in NAME_KEYS:
            value = item.get(key)
            if isinstance(value, str):
                name = value.strip()
                # Conservative player-like name filter; team/competition labels are later deduped and capped.
                if 2 <= len(name) <= 80 and not any(x in name.casefold() for x in ("lineup", "formation", "injury", "suspension")):
                    out.append(name)
                    break
    dedup = []
    seen = set()
    for name in out:
        token = norm(name)
        if token and token not in seen:
            seen.add(token); dedup.append(name)
    return dedup


def subtree_text(node: Any, limit: int = 12000) -> str:
    try:
        text = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return ""
    return text[:limit].casefold()


def team_assignment(node: Any, home_names: list[str], away_names: list[str]) -> str | None:
    text = norm(subtree_text(node))
    home_hit = any(norm(x) and norm(x) in text for x in home_names)
    away_hit = any(norm(x) and norm(x) in text for x in away_names)
    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return None


def extract_context(props: dict[str, Any], fixture: dict[str, Any], fotmob: dict[str, Any]) -> dict[str, Any]:
    fhome = (fotmob.get("home_team") or {}) if isinstance(fotmob.get("home_team"), dict) else {}
    faway = (fotmob.get("away_team") or {}) if isinstance(fotmob.get("away_team"), dict) else {}
    home_names = [fixture["home_team"], fhome.get("name"), fhome.get("short_name")]
    away_names = [fixture["away_team"], faway.get("name"), faway.get("short_name")]
    availability = {"home": [], "away": [], "unassigned": []}
    lineup_candidates: list[dict[str, Any]] = []
    availability_paths = []

    for path, node in iter_nodes(props):
        plow = path.casefold(); text = subtree_text(node)
        if any(m in plow for m in AVAIL_MARKERS):
            names = collect_names(node)
            if names:
                side = team_assignment(node, home_names, away_names) or "unassigned"
                availability[side].extend(names[:30]); availability_paths.append(path)
        if any(m in plow for m in LINEUP_MARKERS):
            names = collect_names(node)
            if not names:
                continue
            combined = f"{plow} {text[:4000]}"
            predicted_marker = any(m in combined for m in PREDICT_MARKERS)
            negative = any(m in combined for m in NEGATIVE_LINEUP_MARKERS)
            side = team_assignment(node, home_names, away_names)
            if predicted_marker and not negative and side in {"home", "away"}:
                lineup_candidates.append({"path": path, "side": side, "names": names[:25], "predicted_marker": True})

    for side in availability:
        dedup = []; seen = set()
        for name in availability[side]:
            token = norm(name)
            if token and token not in seen:
                seen.add(token); dedup.append(name)
        availability[side] = dedup

    predicted = {"home": [], "away": []}
    predicted_paths = {"home": [], "away": []}
    for item in lineup_candidates:
        side = item["side"]
        # Accept only exactly 11 unique names. Larger subtrees often include substitutes or both teams.
        names = [] ; seen = set()
        for name in item["names"]:
            token = norm(name)
            if token and token not in seen:
                seen.add(token); names.append(name)
        if len(names) == 11 and not predicted[side]:
            predicted[side] = names; predicted_paths[side].append(item["path"])

    return {
        "availability": availability,
        "availability_paths": availability_paths[:40],
        "predicted_xi": predicted,
        "predicted_xi_paths": predicted_paths,
        "predicted_xi_verified_by_explicit_marker": {"home": len(predicted["home"]) == 11, "away": len(predicted["away"]) == 11},
    }


def safe(s: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_") or "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--golazo-json", required=True); args = ap.parse_args()
    now = utc_now(); aliases, identity_sha = identity_maps(); fixtures = market_fixtures(now); golazo_rows = load_golazo(Path(args.golazo_json))
    stats = Counter(); records = []
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    for fixture in fixtures:
        stats["market_fixtures_considered"] += 1
        match = match_fotmob(fixture, golazo_rows, aliases)
        if match is None:
            stats["fotmob_identity_not_uniquely_matched"] += 1
            continue
        stats["fotmob_identity_matched"] += 1
        page_url = str(match.get("page_url") or "")
        if not page_url:
            stats["missing_page_url"] += 1; continue
        try:
            raw, url, observed = fetch_page(page_url); props = extract_page_props(raw); context = extract_context(props, fixture, match)
            observed_dt = parse_dt(observed); kickoff = parse_dt(fixture["kickoff_at"])
            if observed_dt is None or kickoff is None or observed_dt >= kickoff:
                stats["post_kickoff_rejected"] += 1; continue
            token = observed.replace(":", "").replace("+00:00", "Z")
            out = OUT_ROOT / f"{safe(fixture['competition_id'])}__{safe(fixture['home_team'])}__{safe(fixture['away_team'])}__{safe(match.get('id'))}__{token}.json"
            payload = {
                "schema_version": "V6.48.5-fotmob-prekickoff-context-evidence-r1",
                "observed_at_utc": observed,
                "fixture_identity": {k: fixture[k] for k in ("competition_id","kickoff_at","home_team","away_team")},
                "market_reference": {"market_observed_at": fixture["market_observed_at"], "market_path": fixture["market_path"], "market_sha256": fixture["market_sha256"]},
                "fotmob_identity": {"match_id": match.get("id"), "page_url": page_url, "home_team": match.get("home_team"), "away_team": match.get("away_team"), "match_time": match.get("match_time"), "league": match.get("league")},
                "source": {"source_name": "FotMob match preview page", "source_url": url, "source_tier": "tier_2_structured_public", "provider_group": "fotmob", "source_observed_at_utc": observed, "raw_html_sha256": sha_bytes(raw), "identity_registry_sha256": identity_sha},
                "context": context,
                "eligibility": {
                    "pre_kickoff": True,
                    "availability_candidate_present": bool(context["availability"]["home"] or context["availability"]["away"]),
                    "predicted_xi_home_eligible": len(context["predicted_xi"]["home"]) == 11,
                    "predicted_xi_away_eligible": len(context["predicted_xi"]["away"]) == 11,
                    "legacy_market_first_freeze_backfill_allowed": False,
                    "new_context_enriched_freeze_eligible": True,
                },
                "governance": {"last_starting_xi_not_treated_as_predicted": True, "fuzzy_identity_matching": False, "post_kickoff_evidence_rejected": True, "formal_probability_change": False, "formal_weight": 0, "current_rule_change": False},
            }
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["evidence_written"] += 1
            stats["availability_present"] += int(payload["eligibility"]["availability_candidate_present"])
            stats["predicted_xi_home"] += int(payload["eligibility"]["predicted_xi_home_eligible"])
            stats["predicted_xi_away"] += int(payload["eligibility"]["predicted_xi_away_eligible"])
            records.append({"path": str(out.relative_to(ROOT)), "fixture_identity": payload["fixture_identity"], "eligibility": payload["eligibility"], "source_url": url})
            time.sleep(0.15)
        except Exception as exc:
            stats["page_or_parse_fail"] += 1
            records.append({"fixture_identity": {k: fixture[k] for k in ("competition_id","kickoff_at","home_team","away_team")}, "status": "PAGE_OR_PARSE_FAIL", "error": f"{type(exc).__name__}: {exc}", "page_url": page_url})

    payload = {
        "schema_version": "V6.48.5-fotmob-match-context-acquisition-status-r1",
        "generated_at_utc": now.isoformat(), "formal_current_version": "V5.0.1",
        "status": "PASS" if stats["evidence_written"] > 0 else "PASS_NO_ELIGIBLE_CONTEXT_EVIDENCE",
        "golazo_input_path": str(args.golazo_json), "identity_registry_path": str(IDENTITY.relative_to(ROOT)), "identity_registry_sha256": identity_sha,
        "counts": dict(sorted(stats.items())), "records": records,
        "interpretation": "Context observations are pre-kickoff but are NOT retroactively attached to older market-first freezes. They are inputs for a new decision-freeze sidecar.",
        "governance": {"research_only": True, "no_legacy_freeze_backfill": True, "no_fuzzy_identity": True, "formal_weight": 0, "runtime_probability_change": False, "current_rule_change": False},
    }
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "counts": payload["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
