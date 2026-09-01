from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import pathlib
import random
import re
import statistics
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

ROOT = pathlib.Path(".").resolve()
CTX = ROOT / "football-data/research/context_translator_v1"
sys.path.insert(0, str(CTX))

import candidate_b_diagnostic as cbd
import candidate_c_diagnostic as ccd
import source_ingest as si
from candidate_b import build_probability_mass_scenarios, capability_residual
from candidate_c import (
    UNCERTAINTY_BY_GRADE,
    c1_availability_replacement,
    c2_possible_xi,
    c3_confirmed_xi,
    c4_bench,
    combine_effects,
    evidence_grade,
    probability_mass_supported,
    zero_effect,
)
from candidate_c_historical import contract as historical_uncertainty_contract
from candidate_c_historical import monotonic_contract_holds, uncertainty_only_effect
from player_strength import DIMS, estimate_player_vectors

BASE_HEAD = "6880c1ac8db1e4a56e83e129188dec85b13bb5db"
V2_ARTIFACT_ID = 9743018815
V2_RUN_ID = 33348991436
V2_HEAD = "ef830299e8ee37749ac083e007b4947f8e72d7b7"
V2_PRED_SHA = "92dc38866e6e46b167ed6bf0bcfc6f6e0e8b85e57e68cb3a571d3c44fc9461a7"
LEAGUE = "ENG1"
SEASON = "2023-24"
FULL_SEASON_N = 380
COHORT_N = 300
T15_MINUTES = 15
RELEASE_HOURS = 3
SPORTSMOLE_SITEMAPS = (
    "https://www.sportsmole.co.uk/sitemap-articles-2023.xml",
    "https://www.sportsmole.co.uk/sitemap-articles-2024.xml",
)
UNDERSTAT_LEAGUE_URL = "https://understat.com/league/EPL/2023"
UNDERSTAT_MATCH_URL = "https://understat.com/match/{match_id}"
UA = {"User-Agent": "football3-historical-pit-replay/1.0 (+public reproducible research)"}
GRADE_ORDER = (
    "CONFIRMED_LINEUP_PIT",
    "POSSIBLE_XI_PIT",
    "TEAM_NEWS_AVAILABILITY_PIT",
    "NO_USABLE_ROSTER_EVIDENCE",
)
MODELS = (
    "protected_v2",
    "old_l1_l2",
    "candidate_b",
    "candidate_c_original",
    "candidate_c_historical",
)
OUTCOMES = ("home", "draw", "away")
BOOTSTRAP_SEED = 20260901
BOOTSTRAP_N = 2000
PROMOTION_MIN_ACTIVE_N = 60
PROMOTION_MIN_LL_IMPROVEMENT = 0.002

TEAM_SLUGS = {
    "bournemouth": ("bournemouth",),
    "arsenal": ("arsenal",),
    "aston villa": ("aston-villa",),
    "brentford": ("brentford",),
    "brighton and hove albion": ("brighton", "brighton-and-hove-albion"),
    "burnley": ("burnley",),
    "chelsea": ("chelsea",),
    "crystal palace": ("crystal-palace",),
    "everton": ("everton",),
    "fulham": ("fulham",),
    "liverpool": ("liverpool",),
    "luton town": ("luton-town", "luton"),
    "manchester city": ("manchester-city", "man-city"),
    "manchester united": ("manchester-united", "man-utd"),
    "newcastle united": ("newcastle-united", "newcastle"),
    "nottingham forest": ("nottingham-forest",),
    "sheffield united": ("sheffield-united",),
    "tottenham hotspur": ("tottenham-hotspur", "tottenham"),
    "west ham united": ("west-ham-united", "west-ham"),
    "wolverhampton wanderers": ("wolverhampton-wanderers", "wolves"),
}


class ReplayError(RuntimeError):
    pass


def dt(value: str) -> datetime:
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise ReplayError(f"timezone required: {value!r}")
    return d.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def canon(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def readjl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def dump(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def writejl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for x in rows), encoding="utf-8")


def norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    toks = [x for x in re.findall(r"[a-z0-9]+", s) if x not in {"fc", "afc"}]
    return " ".join(toks)


def fetch(url: str, *, timeout: int = 60, attempts: int = 4) -> tuple[bytes, dict[str, str]]:
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), dict(r.headers)
        except Exception as exc:
            last = exc
            time.sleep(min(2 ** i, 8))
    raise ReplayError(f"public source fetch failed: {url}: {last}")


def js_json(text: str, key: str) -> Any:
    m = re.search(rf"{re.escape(key)}\s*=\s*JSON\.parse\('([^']*)'\)", text, re.S)
    if not m:
        raise ReplayError(f"Understat JS payload missing: {key}")
    decoded = bytes(m.group(1), "utf-8").decode("unicode_escape")
    return json.loads(decoded)


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</p>|</div>|</li>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    lines = [re.sub(r"\s+", " ", html.unescape(x)).strip() for x in fragment.splitlines()]
    return "\n".join(x for x in lines if x)


def page_time(page: str, key: str) -> datetime | None:
    patterns = {
        "published": (
            r'(?is)"datePublished"\s*:\s*"([^"]+)"',
            r'(?is)<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        ),
        "modified": (
            r'(?is)"dateModified"\s*:\s*"([^"]+)"',
            r'(?is)<meta[^>]+property=["\']article:modified_time["\'][^>]+content=["\']([^"\']+)',
        ),
    }[key]
    for p in patterns:
        for value in re.findall(p, page):
            try:
                return dt(value)
            except Exception:
                continue
    return None


def team_news_section(page: str) -> tuple[str, str] | None:
    m = re.search(r"(?is)<h2[^>]*>\s*Team News\s*</h2>(.*?)(?=<h2[^>]*>)", page)
    if not m:
        return None
    raw = m.group(1)
    text = strip_tags(raw)
    return (raw, text) if text else None


def possible_lineups(text: str) -> tuple[list[str], list[str]] | None:
    matches = list(re.finditer(r"(?i)([A-ZÀ-ÖØ-öø-ÿ0-9 .&'’\-]{2,60})\s+possible starting lineup:\s*", text))
    if len(matches) < 2:
        return None
    out = []
    for i, m in enumerate(matches[:2]):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end():end]
        chunk = re.split(r"(?i)\bWe say:|\bData Analysis\b|\bWhat is your prediction\b", chunk)[0]
        names = [re.sub(r"\s+", " ", x).strip(" .") for x in re.split(r"[;,]", chunk)]
        names = [x for x in names if x]
        if len(names) < 11:
            return None
        out.append(names[:11])
    return out[0], out[1]


def understat_identity(v2_rows: list[dict[str, Any]], out: pathlib.Path) -> tuple[dict[str, str], dict[str, Any]]:
    raw, _ = fetch(UNDERSTAT_LEAGUE_URL)
    text = raw.decode("utf-8", "replace")
    payload = js_json(text, "datesData")
    dates = payload.get("dates", payload) if isinstance(payload, dict) else payload
    identities = []
    for x in dates:
        h = x.get("h") or {}
        a = x.get("a") or {}
        mid = str(x.get("id") or "")
        date = str(x.get("datetime") or "")[:10]
        ht = str(h.get("title") or "")
        at = str(a.get("title") or "")
        if mid and date and ht and at:
            identities.append({"understat_match_id": mid, "date": date, "home": ht, "away": at})
    idx = defaultdict(list)
    for x in identities:
        idx[(x["date"], norm(x["home"]), norm(x["away"]))].append(x)
    mapping = {}
    rows = []
    for r in v2_rows:
        key = (str(r["cutoff"])[:10], norm(r["home_team"]), norm(r["away_team"]))
        cand = idx.get(key, [])
        if len(cand) != 1:
            raise ReplayError(f"Understat identity collision/miss for {key}: {len(cand)}")
        mapping[str(r["fixture_id"])] = cand[0]["understat_match_id"]
        rows.append({"fixture_id": str(r["fixture_id"]), **cand[0]})
    if len(mapping) != FULL_SEASON_N or len(set(mapping.values())) != FULL_SEASON_N:
        raise ReplayError("Understat full-season identity is not one-to-one 380")
    rec = {
        "schema_version": "football3-understat-epl-2023-24-identity-v1",
        "source_url": UNDERSTAT_LEAGUE_URL,
        "source_page_sha256": sha_bytes(raw),
        "source_page_bytes": len(raw),
        "collected_at": iso(datetime.now(timezone.utc)),
        "identity_rule": "date+canonical_home+canonical_away; result/xG fields not retained or supplied to predictor",
        "mapped_n": len(mapping),
        "result_fields_retained": False,
        "rows_sha256": canon(rows),
    }
    dump(out / "understat_identity_receipt.json", rec)
    return mapping, rec


def v2_t15_equivalence(v2: pathlib.Path, season_rows: list[dict[str, Any]], cohort: list[dict[str, Any]]) -> dict[str, Any]:
    all_eval = [x for x in readjl(v2 / "dataset/evaluation_features.jsonl") if str(x.get("competition_id")) == LEAGUE]
    release_times = [dt(str(x["cutoff"])) + timedelta(hours=RELEASE_HOURS) for x in all_eval]
    dev = [x for x in readjl(v2 / "dataset/development.jsonl") if str(x.get("competition_id")) == LEAGUE]
    for x in dev:
        if x.get("result_available_at"):
            release_times.append(dt(str(x["result_available_at"])))
    violations = []
    for r in cohort:
        ko = dt(str(r["cutoff"]))
        t15 = ko - timedelta(minutes=T15_MINUTES)
        hits = [x for x in release_times if t15 < x <= ko]
        if hits:
            violations.append({"fixture_id": r["fixture_id"], "kickoff": iso(ko), "release_times": [iso(x) for x in hits]})
    return {
        "schema_version": "football3-protected-v2-t15-equivalence-v1",
        "passed": not violations,
        "rule": "sealed V2 kickoff prediction equals T-15 state iff no ENG1 competition-state result-release event occurs in (T-15,kickoff]",
        "release_contract": "development result_available_at; evaluation kickoff+3h; no result values read",
        "checked_n": len(cohort),
        "violation_n": len(violations),
        "violations": violations,
    }


def select_cohort(v2: pathlib.Path, out: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    am = json.load(open(v2 / "artifact_manifest.json"))
    if am["run_id"] != V2_RUN_ID or am["head"] != V2_HEAD or am["prediction_sha256"] != V2_PRED_SHA:
        raise ReplayError("sealed protected V2 identity mismatch")
    if (v2 / "dataset/evaluation_label_vault.jsonl").exists():
        raise ReplayError("evaluation labels physically present during cohort/prediction")
    ev = readjl(v2 / "dataset/evaluation_features.jsonl")
    full = [x for x in ev if str(x["competition_id"]) == LEAGUE and str(x["season"]) == SEASON]
    full.sort(key=lambda x: (dt(str(x["cutoff"])), str(x["fixture_id"])))
    if len(full) != FULL_SEASON_N:
        raise ReplayError(f"expected complete EPL 2023/24 380, got {len(full)}")
    cohort = full[:COHORT_N]
    pred = {str(x["fixture_id"]): x for x in readjl(v2 / "replay/predictions.jsonl")}
    if any(str(x["fixture_id"]) not in pred for x in cohort):
        raise ReplayError("sealed V2 replay missing cohort prediction")
    eq = v2_t15_equivalence(v2, full, cohort)
    dump(out / "protected_v2_t15_equivalence.json", eq)
    if not eq["passed"]:
        raise ReplayError("protected V2 T-15 equivalence failed")
    rows = [{
        "fixture_id": str(r["fixture_id"]),
        "competition_id": str(r["competition_id"]),
        "season": str(r["season"]),
        "kickoff_utc": iso(dt(str(r["cutoff"]))),
        "prediction_cutoff_utc": iso(dt(str(r["cutoff"])) - timedelta(minutes=T15_MINUTES)),
        "home_team_id": str(r["home_team_id"]),
        "away_team_id": str(r["away_team_id"]),
        "home_team": str(r["home_team"]),
        "away_team": str(r["away_team"]),
        "round_index": r.get("round_index"),
        "shared_cold_start_bucket": pred[str(r["fixture_id"])].get("shared_cold_start_bucket"),
    } for r in cohort]
    identity_sha = canon(rows)
    manifest = {
        "schema_version": "football3-historical-pit-replay-cohort-v1",
        "status": "HISTORICAL_PIT_REPLAY",
        "selection_rule": "COMPLETE_ENG1_2023_24_SORT_KICKOFF_ASC_FIXTURE_ID_ASC_FIRST_300",
        "full_season_n": FULL_SEASON_N,
        "n": COHORT_N,
        "prediction_cutoff": "kickoff_minus_15_minutes",
        "rows": rows,
        "cohort_identity_sha256": identity_sha,
        "result_or_model_performance_selection": False,
        "protected_v2_t15_equivalence_sha256": sha_file(out / "protected_v2_t15_equivalence.json"),
    }
    dump(out / "cohort_manifest.json", manifest)
    return cohort, manifest, pred


def sportsmole_urls() -> tuple[list[str], list[dict[str, Any]]]:
    urls = []
    receipts = []
    for u in SPORTSMOLE_SITEMAPS:
        raw, _ = fetch(u)
        root = ET.fromstring(raw)
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        candidates = [x for x in locs if "/football/" in x and ("/preview/" in x or "/injury-news/" in x) and x.endswith(".html")]
        urls.extend(candidates)
        receipts.append({"url": u, "sha256": sha_bytes(raw), "bytes": len(raw), "candidate_urls": len(candidates)})
    return sorted(set(urls)), receipts


def sm_candidates(urls: list[str], home: str, away: str) -> list[str]:
    hs = TEAM_SLUGS.get(norm(home), ())
    ass = TEAM_SLUGS.get(norm(away), ())
    if not hs or not ass:
        raise ReplayError(f"Sports Mole team alias missing: {home} / {away}")
    out = []
    for u in urls:
        b = u.rsplit("/", 1)[-1].lower()
        if any(x in b for x in hs) and any(x in b for x in ass):
            out.append(u)
    return sorted(out, key=lambda u: (0 if "/injury-news/" in u else 1, u))


def wayback_snapshot(url: str, cutoff: datetime) -> tuple[str, bytes, str] | None:
    to = cutoff.strftime("%Y%m%d%H%M%S")
    q = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200",
        "to": to,
        "limit": "20",
        "collapse": "digest",
    })
    try:
        raw, _ = fetch(q, timeout=45, attempts=2)
        data = json.loads(raw)
        rows = data[1:] if isinstance(data, list) and data else []
        valid = [x for x in rows if len(x) >= 3 and x[0].isdigit() and x[0] <= to]
        if not valid:
            return None
        ts = max(x[0] for x in valid)
        snap = f"https://web.archive.org/web/{ts}id_/{url}"
        body, _ = fetch(snap, timeout=60, attempts=2)
        return ts, body, q
    except Exception:
        return None


def admit_sm(url: str, cutoff: datetime, kickoff: datetime) -> dict[str, Any] | None:
    collected = datetime.now(timezone.utc)
    try:
        current_raw, _ = fetch(url, attempts=2)
    except Exception:
        return None
    page = current_raw.decode("utf-8", "replace")
    pub = page_time(page, "published")
    mod = page_time(page, "modified")
    if pub is None or not pub < cutoff or (kickoff - pub).total_seconds() > 10 * 86400:
        return None
    proof_type = None
    proof_at = None
    proof_url = url
    raw = current_raw
    if mod is not None and mod <= cutoff:
        proof_type = "SOURCE_DECLARED_MODIFIED_AT_PRE_CUTOFF"
        proof_at = mod
    else:
        wb = wayback_snapshot(url, cutoff)
        if wb is None:
            return None
        ts, raw, _ = wb
        proof_at = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if proof_at > cutoff:
            return None
        proof_type = "WAYBACK_SNAPSHOT_PRE_CUTOFF"
        proof_url = f"https://web.archive.org/web/{ts}id_/{url}"
        page = raw.decode("utf-8", "replace")
        p2 = page_time(page, "published")
        m2 = page_time(page, "modified")
        if p2 is None or p2 >= cutoff or (m2 is not None and m2 > proof_at):
            return None
        pub = p2
        mod = m2
    sec = team_news_section(page)
    if sec is None:
        return None
    raw_sec, text = sec
    return {
        "source_url": url,
        "proof_url": proof_url,
        "proof_type": proof_type,
        "published_at": iso(pub),
        "source_proof_at": iso(proof_at),
        "collected_at": iso(collected),
        "page_sha256": sha_bytes(raw),
        "page_bytes": len(raw),
        "raw_content_scope": "EXACT_H2_TEAM_NEWS_SECTION_ONLY",
        "raw_content_sha256": sha_bytes(raw_sec.encode()),
        "text": text,
        "possible": possible_lineups(text),
        "modified_at": None if mod is None else iso(mod),
    }


def source_packets(cohort: list[dict[str, Any]], out: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    urls, sitemap = sportsmole_urls()
    packets = []
    missing = Counter()
    for r in cohort:
        kickoff = dt(str(r["cutoff"]))
        cutoff = kickoff - timedelta(minutes=T15_MINUTES)
        cands = sm_candidates(urls, str(r["home_team"]), str(r["away_team"]))
        admitted = []
        for u in cands[:6]:
            z = admit_sm(u, cutoff, kickoff)
            if z is not None:
                admitted.append(z)
        if not admitted:
            packets.append({
                "fixture_id": str(r["fixture_id"]),
                "kickoff_utc": iso(kickoff),
                "cutoff_utc": iso(cutoff),
                "home_team_id": str(r["home_team_id"]),
                "away_team_id": str(r["away_team_id"]),
                "home_team": str(r["home_team"]),
                "away_team": str(r["away_team"]),
                "pit_legal": False,
                "missing_reason": "NO_SOURCE_CONTENT_WITH_PRE_T15_PUBLICATION_PROOF",
                "candidate_url_n": len(cands),
            })
            missing["NO_SOURCE_CONTENT_WITH_PRE_T15_PUBLICATION_PROOF"] += 1
            continue
        admitted.sort(key=lambda x: (dt(x["source_proof_at"]), dt(x["published_at"]), x["source_url"]), reverse=True)
        z = admitted[0]
        poss = z.pop("possible")
        text = z.pop("text")
        packets.append({
            "fixture_id": str(r["fixture_id"]),
            "kickoff_utc": iso(kickoff),
            "cutoff_utc": iso(cutoff),
            "home_team_id": str(r["home_team_id"]),
            "away_team_id": str(r["away_team_id"]),
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "pit_legal": True,
            "source": z,
            "source_text_for_identity_only": text,
            "possible_lineup_source_names": None if poss is None else {"home": poss[0], "away": poss[1]},
            "confirmed_lineups": None,
            "bench": None,
            "confirmed_xi_policy": "NO_HISTORICAL_CONFIRMED_XI_ADMITTED_WITHOUT_INDEPENDENT_PRE_T15_OBSERVATION",
        })
    writejl(out / "pit_roster_source_packets.jsonl", [{k: v for k, v in x.items() if k != "source_text_for_identity_only"} for x in packets])
    audit = {
        "schema_version": "football3-historical-pit-source-coverage-v1",
        "n": len(packets),
        "pit_legal_team_news_n": sum(bool(x.get("pit_legal")) for x in packets),
        "possible_xi_source_n": sum(bool(x.get("possible_lineup_source_names")) for x in packets),
        "confirmed_xi_source_n": 0,
        "bench_source_n": 0,
        "missing_reasons": dict(sorted(missing.items())),
        "sitemap_receipts": sitemap,
        "packet_source_sha256": sha_file(out / "pit_roster_source_packets.jsonl"),
        "current_postmatch_or_late_modified_content_admitted": False,
    }
    dump(out / "source_coverage_audit.json", audit)
    return packets, audit


def player_pid(team_id: str, name: str) -> str:
    return "pit_player_" + hashlib.sha256((str(team_id) + "|" + norm(name)).encode()).hexdigest()[:20]


def role_from_position(pos: str) -> str:
    p = str(pos).upper()
    if "GK" in p:
        return "GK"
    if any(x in p for x in ("DC", "DL", "DR", "DMC", "DEF")):
        return "DEF"
    if any(x in p for x in ("MC", "ML", "MR", "AMC", "MID")):
        return "MID"
    if any(x in p for x in ("FW", "FWD", "ST", "CF", "ATT")):
        return "FWD"
    return "UNK"


def understat_roster(match_id: str, home_tid: str, away_tid: str, release_at: str) -> dict[str, Any]:
    url = UNDERSTAT_MATCH_URL.format(match_id=match_id)
    raw, _ = fetch(url)
    text = raw.decode("utf-8", "replace")
    data = js_json(text, "rostersData")
    if isinstance(data, dict) and "rosters" in data:
        data = data["rosters"]
    out_usage = defaultdict(list)
    events = []
    aliases = defaultdict(list)
    for side, tid in (("h", str(home_tid)), ("a", str(away_tid))):
        vals = data.get(side) or {}
        rows = list(vals.values()) if isinstance(vals, dict) else list(vals)
        for x in rows:
            name = str(x.get("player") or x.get("player_name") or "").strip()
            if not name:
                continue
            pid = player_pid(tid, name)
            position = str(x.get("position") or "")
            minutes = float(x.get("time") or x.get("minutes") or 0.0)
            started = position.lower() not in {"sub", "substitute"} and minutes > 0
            role = role_from_position(position)
            rec = {"player_id": pid, "started": started, "appeared": minutes > 0, "minutes": minutes, "role": role, "known_at": release_at, "player_name": name}
            out_usage[tid].append(rec)
            aliases[tid].append({"player_id": pid, "player_name": name})
            if minutes > 0:
                values = {
                    "shot_generation": float(x.get("xG") or 0.0),
                    "finishing": 0.0,
                    "chance_creation": float(x.get("xA") or 0.0),
                    "passing_progression": float(x.get("xGBuildup") or 0.0),
                    "carrying_progression": 0.0,
                    "possession_retention_risk": 0.0,
                    "pressing": 0.0,
                    "tackling_interception": 0.0,
                    "defensive_position_protection": 0.0,
                    "aerial": 0.0,
                    "set_piece": 0.0,
                    "goalkeeper_shot_stopping": 0.0,
                    "goalkeeper_sweeping": 0.0,
                    "goalkeeper_cross_claiming": 0.0,
                    "goalkeeper_distribution": 0.0,
                    "on_ball_contribution": float(x.get("xGChain") or 0.0),
                    "off_ball_contribution": 0.0,
                    "current_form": float(x.get("xG") or 0.0) + float(x.get("xA") or 0.0),
                }
                if set(values) - set(DIMS):
                    raise ReplayError("Understat adapter produced unknown player dimension")
                events.append({
                    "player_id": pid,
                    "team_id": tid,
                    "league_id": LEAGUE,
                    "role": role,
                    "known_at": release_at,
                    "minutes_exposure": minutes,
                    "possession_opportunity": 1.0,
                    "values": values,
                    "source_sha256": sha_bytes(raw),
                })
    for tid, rows in out_usage.items():
        starters = [x for x in rows if x["started"]]
        if len(starters) != 11:
            for x in rows:
                x["started"] = False
    return {
        "source_url": url,
        "source_sha256": sha_bytes(raw),
        "source_bytes": len(raw),
        "release_at": release_at,
        "usage": dict(out_usage),
        "events": events,
        "aliases": dict(aliases),
        "prohibited_target_fields_retained": False,
        "rating_field_used": False,
    }


def resolve_source_name(name: str, tid: str, registry: dict[str, dict[str, str]]) -> tuple[str | None, str]:
    n = norm(name)
    if not n:
        return None, "EMPTY"
    exact = registry.get(str(tid), {}).get(n)
    if exact:
        return exact, "PRIOR_HISTORY_EXACT"
    surname = n.split()[-1]
    candidates = {pid for nm, pid in registry.get(str(tid), {}).items() if nm.split() and nm.split()[-1] == surname}
    if len(candidates) == 1:
        return next(iter(candidates)), "PRIOR_HISTORY_UNIQUE_SURNAME"
    if len(candidates) > 1:
        raise ReplayError(f"ambiguous player identity {tid} {name}: {sorted(candidates)}")
    if len(n.split()) >= 2:
        return player_pid(str(tid), name), "SOURCE_FULL_NAME_STABLE_ID_NO_PRIOR_CAPABILITY"
    return None, "UNRESOLVED_SURNAME_WITHOUT_PRIOR_IDENTITY"


def build_status_records(text: str, home_tid: str, away_tid: str, registry: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    records = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        low = sent.lower()
        if not any(k in low for k in ("suspend", "injur", "ruled out", "sidelined", "will miss", "doubt")):
            continue
        stype = "SUSPENSION" if "suspend" in low else "INJURY_OR_AVAILABILITY"
        sn = norm(sent)
        for tid in (str(home_tid), str(away_tid)):
            hits = []
            for nm, pid in registry.get(tid, {}).items():
                sur = nm.split()[-1] if nm.split() else ""
                if len(sur) >= 4 and re.search(rf"\b{re.escape(sur)}\b", sn):
                    hits.append((pid, nm))
            for pid, nm in set(hits):
                records.append({"player_id": pid, "player_name": nm, "team_id": tid, "status_type": stype, "source_semantics": "SPORTSMOLE_PRE_T15_TEAM_NEWS"})
    return list({(x["player_id"], x["status_type"]): x for x in records}.values())


def bind_packet(src: dict[str, Any], registry: dict[str, dict[str, str]]) -> dict[str, Any]:
    base = {k: src[k] for k in ("fixture_id", "kickoff_utc", "home_team_id", "away_team_id", "home_team", "away_team", "pit_legal")}
    if not src.get("pit_legal"):
        return {**base, "source": None, "predicted_lineups": None, "confirmed_lineups": None, "bench": None, "status_records": [], "identity_attempt_n": 0, "identity_matched_n": 0, "missing_reason": src.get("missing_reason")}
    poss = src.get("possible_lineup_source_names")
    pred = None
    attempts = matched = 0
    if isinstance(poss, dict):
        pred = {"home": [], "away": []}
        for side, tid in (("home", str(src["home_team_id"])), ("away", str(src["away_team_id"]))):
            for name in poss.get(side) or []:
                attempts += 1
                pid, state = resolve_source_name(str(name), tid, registry)
                matched += int(pid is not None)
                pred[side].append({"source_name": name, "player_id": pid, "identity_status": state, "lineup_type": "POSSIBLE_XI_POINT_PREDICTION", "starting_probability": None, "expected_minutes": None, "position": None})
    text = str(src.get("source_text_for_identity_only") or "")
    statuses = build_status_records(text, str(src["home_team_id"]), str(src["away_team_id"]), registry)
    source = dict(src["source"])
    source["source_observed_at"] = source["source_proof_at"]
    source["available_at"] = source["published_at"]
    packet = {**base, "source": source, "predicted_lineups": pred, "confirmed_lineups": None, "bench": None, "status_records": statuses, "probability_contract": "NO_SOURCE_PLAYER_START_PROBABILITIES_DO_NOT_INVENT", "identity_attempt_n": attempts, "identity_matched_n": matched}
    packet["packet_sha256"] = canon(packet)
    return packet


def b_effect_pred(base: dict[str, Any], effect: Any, lock: dict[str, Any], eng: Any) -> dict[str, Any]:
    return cbd.effect_prediction(base["score_matrix"], effect, lock, eng)


def c_effect_pred(base: dict[str, Any], effect: Any, lock: dict[str, Any], eng: Any) -> dict[str, Any]:
    return ccd.effect_prediction(base["score_matrix"], effect, lock, eng)


def hist_comparators(base: dict[str, Any], vectors: dict[str, Any], usage: dict[str, list[dict[str, Any]]], home_tid: str, away_tid: str, cutoff: str, lock: dict[str, Any], eng: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    baseline = cbd.pred(base["score_matrix"], eng)
    he = si._expected(str(home_tid), usage, cutoff)
    ae = si._expected(str(away_tid), usage, cutoff)
    if not vectors or not he or not ae:
        return baseline, baseline, {"old_active": False, "b_active": False, "reason": "INSUFFICIENT_PRIOR_PLAYER_HISTORY"}
    scenarios = build_probability_mass_scenarios(he, ae, cutoff=cutoff)
    if not scenarios:
        return baseline, baseline, {"old_active": False, "b_active": False, "reason": "INSUFFICIENT_PRIOR_LINEUP_MASS"}
    modal = scenarios[0]
    e0 = capability_residual(vectors=vectors, usage=usage, home_team_id=str(home_tid), away_team_id=str(away_tid), home_player_ids=modal.home_player_ids, away_player_ids=modal.away_player_ids, cutoff=cutoff)
    if not e0.active:
        return baseline, baseline, {"old_active": False, "b_active": False, "reason": e0.reason}
    l1 = b_effect_pred(base, e0, lock, eng)
    if len(scenarios) >= 2:
        ok = True
        items_b = []
        items_old = []
        for sc in scenarios:
            e = capability_residual(vectors=vectors, usage=usage, home_team_id=str(home_tid), away_team_id=str(away_tid), home_player_ids=sc.home_player_ids, away_player_ids=sc.away_player_ids, cutoff=cutoff)
            if not e.active:
                ok = False
                break
            items_b.append((b_effect_pred(base, e, lock, eng), sc.probability))
            items_old.append((cbd.effect_prediction(l1["score_matrix"], e, lock, eng), sc.probability))
        if ok:
            return cbd.mix(items_old, eng), cbd.mix(items_b, eng), {"old_active": True, "b_active": True, "scenario_n": len(scenarios), "reason": "ACTIVE"}
    return l1, l1, {"old_active": True, "b_active": True, "scenario_n": 1, "reason": "ACTIVE_MODAL_ONLY"}


def run_prediction(v2: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    if (v2 / "dataset/evaluation_label_vault.jsonl").exists():
        raise ReplayError("labels present during historical PIT predictor")
    cohort_raw, cm, v2pred = select_cohort(v2, out)
    under_map, _ = understat_identity(
        [x for x in readjl(v2 / "dataset/evaluation_features.jsonl") if str(x["competition_id"]) == LEAGUE and str(x["season"]) == SEASON], out
    )
    packets_src, _ = source_packets(cohort_raw, out)
    smap = {str(x["fixture_id"]): x for x in packets_src}
    cohort = cm["rows"]
    lock = json.load(open(v2 / "locks/v2_lock.json"))
    eng = cbd.engine()
    events: list[dict[str, Any]] = []
    usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    registry: dict[str, dict[str, str]] = defaultdict(dict)
    pending: list[dict[str, Any]] = []
    ledger = []
    predictions = []
    bound_packets = []
    history_evidence = []
    ability_attempt = ability_available = 0
    identity_attempt = identity_match = 0
    component_n = Counter()
    grade_n = Counter()
    model_active = Counter()
    groups = defaultdict(list)
    for r in cohort:
        groups[str(r["kickoff_utc"])].append(r)

    def release_ready(cutoff: datetime) -> None:
        nonlocal events, usage, registry
        ready = [x for x in pending if dt(x["release_at"]) < cutoff]
        pending[:] = [x for x in pending if dt(x["release_at"]) >= cutoff]
        for x in sorted(ready, key=lambda z: (z["release_at"], z["fixture_id"])):
            z = understat_roster(x["understat_match_id"], x["home_team_id"], x["away_team_id"], x["release_at"])
            events.extend(z["events"])
            for tid, players in z["usage"].items():
                usage[str(tid)].append({"players": players, "known_at": x["release_at"], "match_id": x["understat_match_id"]})
            for tid, aliases in z["aliases"].items():
                for p in aliases:
                    nn = norm(p["player_name"])
                    old = registry[str(tid)].get(nn)
                    if old is not None and old != p["player_id"]:
                        raise ReplayError(f"prior-history identity conflict {tid} {nn}")
                    registry[str(tid)][nn] = p["player_id"]
            history_evidence.append({"fixture_id": x["fixture_id"], "understat_match_id": x["understat_match_id"], "source_url": z["source_url"], "source_sha256": z["source_sha256"], "source_bytes": z["source_bytes"], "release_at": x["release_at"], "player_event_n": len(z["events"])})
            ledger.append({"event": "UPDATE_RELEASED_PRIOR_MATCH", "fixture_id": x["fixture_id"], "release_at": x["release_at"], "before_cutoff": iso(cutoff), "source_sha256": z["source_sha256"]})

    for kickoff_text in sorted(groups, key=dt):
        kickoff = dt(kickoff_text)
        cutoff = kickoff - timedelta(minutes=T15_MINUTES)
        release_ready(cutoff)
        batch_rows = []
        batch_packet_shas = []
        for r in sorted(groups[kickoff_text], key=lambda x: x["fixture_id"]):
            fid = str(r["fixture_id"])
            packet = bind_packet(smap[fid], registry)
            bound_packets.append(packet)
            batch_packet_shas.append(packet.get("packet_sha256") or canon(packet))
            identity_attempt += int(packet.get("identity_attempt_n", 0))
            identity_match += int(packet.get("identity_matched_n", 0))
            grade = evidence_grade(packet, iso(cutoff))
            grade_n[grade] += 1
            vectors = estimate_player_vectors(events, [], as_of=iso(cutoff)) if events else {}
            relevant_ids = set()
            for side in ("home", "away"):
                for x in ((packet.get("predicted_lineups") or {}).get(side) or []):
                    if x.get("player_id"):
                        relevant_ids.add(str(x["player_id"]))
            relevant_ids |= {str(x["player_id"]) for x in packet.get("status_records") or [] if x.get("player_id")}
            ability_attempt += len(relevant_ids)
            ability_available += sum(pid in vectors for pid in relevant_ids)
            vp = v2pred[fid]
            base = cbd.pred(vp["v2_joint"]["score_matrix"], eng)
            old, bpred, cmpdiag = hist_comparators(base, vectors, usage, str(r["home_team_id"]), str(r["away_team_id"]), iso(cutoff), lock, eng)
            model_active["old_l1_l2"] += int(cmpdiag["old_active"])
            model_active["candidate_b"] += int(cmpdiag["b_active"])
            ev_unc = UNCERTAINTY_BY_GRADE[grade]
            if not packet.get("pit_legal") or not vectors:
                c1 = zero_effect("C1", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
                c2 = zero_effect("C2", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
                c3 = zero_effect("C3", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
                c4 = zero_effect("C4", "NO_USABLE_PIT_OR_PRIOR_CAPABILITY", uncertainty=ev_unc)
            else:
                c1 = c1_availability_replacement(vectors=vectors, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]), status_records=packet.get("status_records") or [], evidence_uncertainty=ev_unc)
                c2 = c2_possible_xi(vectors=vectors, usage=usage, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]), predicted_lineups=packet.get("predicted_lineups") or {}, cutoff=iso(cutoff)) if grade == "POSSIBLE_XI_PIT" else zero_effect("C2", "EVIDENCE_GRADE_NOT_POSSIBLE_XI", uncertainty=ev_unc)
                c3 = c3_confirmed_xi(vectors=vectors, usage=usage, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]), confirmed_lineups=packet.get("confirmed_lineups"), cutoff=iso(cutoff)) if grade == "CONFIRMED_LINEUP_PIT" else zero_effect("C3", "EVIDENCE_GRADE_NOT_CONFIRMED_XI", uncertainty=ev_unc)
                c4 = c4_bench(vectors=vectors, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]), bench=packet.get("bench"), evidence_uncertainty=ev_unc)
            for e in (c1, c2, c3, c4):
                component_n[e.component] += int(e.active)
            lineup = c3 if c3.active else c2
            if lineup.active:
                full = combine_effects([lineup] + ([c4] if c4.active else []), grade=grade)
            else:
                full = combine_effects(([c1] if c1.active else []) + ([c4] if c4.active else []), grade=grade)
            fixed_full = uncertainty_only_effect(full, grade)
            c_orig = c_effect_pred(base, full, lock, eng)
            c_hist = c_effect_pred(base, fixed_full, lock, eng)
            active = full.active
            model_active["candidate_c_original"] += int(active)
            model_active["candidate_c_historical"] += int(active)
            batch_rows.append({
                "fixture_id": fid,
                "kickoff_utc": r["kickoff_utc"],
                "cutoff_utc": iso(cutoff),
                "home_team_id": r["home_team_id"],
                "away_team_id": r["away_team_id"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "round_index": r["round_index"],
                "shared_cold_start_bucket": r["shared_cold_start_bucket"],
                "research_status": "HISTORICAL_PIT_REPLAY",
                "evidence_grade": grade,
                "protected_v2": base,
                "old_l1_l2": old,
                "candidate_b": bpred,
                "candidate_c_original": c_orig,
                "candidate_c_historical": c_hist,
                "components": {"C1": c1.to_dict(), "C2": c2.to_dict(), "C3": c3.to_dict(), "C4": c4.to_dict()},
                "candidate_c_full_effect": full.to_dict(),
                "candidate_c_historical_effect": fixed_full.to_dict(),
                "uncertainty_original": max(float(full.home.uncertainty), float(full.away.uncertainty)),
                "uncertainty_historical": max(float(fixed_full.home.uncertainty), float(fixed_full.away.uncertainty)),
                "probability_mass_supported": probability_mass_supported(packet),
                "probability_mass_redistribution_active": False,
                "packet_sha256": packet.get("packet_sha256") or canon(packet),
                "understat_match_id": under_map[fid],
                "comparator_diagnostic": cmpdiag,
            })
        batch_sha = canon([{"fixture_id": x["fixture_id"], "packet_sha256": x["packet_sha256"], "predictions": {m: canon(x[m]) for m in MODELS}} for x in batch_rows])
        ledger.append({"event": "PREDICT_BATCH_FREEZE", "kickoff_utc": kickoff_text, "cutoff_utc": iso(cutoff), "fixture_ids": [x["fixture_id"] for x in batch_rows], "packet_shas": batch_packet_shas, "prediction_batch_sha256": batch_sha})
        predictions.extend(batch_rows)
        for r in sorted(groups[kickoff_text], key=lambda x: x["fixture_id"]):
            release_at = iso(kickoff + timedelta(hours=RELEASE_HOURS))
            pending.append({"fixture_id": r["fixture_id"], "understat_match_id": under_map[r["fixture_id"]], "home_team_id": r["home_team_id"], "away_team_id": r["away_team_id"], "release_at": release_at})
            ledger.append({"event": "ENQUEUE_AFTER_BATCH_FREEZE", "fixture_id": r["fixture_id"], "understat_match_id": under_map[r["fixture_id"]], "release_at": release_at, "prediction_batch_sha256": batch_sha})

    order = {x["fixture_id"]: i for i, x in enumerate(cohort)}
    predictions.sort(key=lambda x: order[x["fixture_id"]])
    bound_packets.sort(key=lambda x: order[x["fixture_id"]])
    writejl(out / "pit_roster_packets.jsonl", bound_packets)
    writejl(out / "historical_pit_predictions.jsonl", predictions)
    writejl(out / "historical_pit_ledger.jsonl", ledger)
    dump(out / "released_history_evidence.json", {"schema_version": "football3-historical-pit-released-history-v1", "rows": history_evidence, "rows_sha256": canon(history_evidence), "target_match_postmatch_data_used_in_own_prediction": False})
    dump(out / "candidate_c_historical_contract.json", historical_uncertainty_contract())
    means = {}
    for grade in GRADE_ORDER:
        xs = [x["uncertainty_historical"] for x in predictions if x["evidence_grade"] == grade]
        means[grade] = None if not xs else statistics.fmean(xs)
    nonempty = [(g, means[g]) for g in GRADE_ORDER if means[g] is not None]
    mono = all(a <= b for (_, a), (_, b) in zip(nonempty, nonempty[1:]))
    pre = {
        "schema_version": "football3-historical-pit-replay-pre-score-v1",
        "status": "HISTORICAL_PIT_REPLAY_PREDICTIONS_FROZEN",
        "labels_read_in_prediction_phase": False,
        "n": len(predictions),
        "cohort_identity_sha256": cm["cohort_identity_sha256"],
        "cohort_manifest_sha256": sha_file(out / "cohort_manifest.json"),
        "pit_roster_source_packet_sha256": sha_file(out / "pit_roster_source_packets.jsonl"),
        "pit_roster_packet_sha256": sha_file(out / "pit_roster_packets.jsonl"),
        "prediction_sha256": sha_file(out / "historical_pit_predictions.jsonl"),
        "pit_ledger_sha256": sha_file(out / "historical_pit_ledger.jsonl"),
        "released_history_sha256": sha_file(out / "released_history_evidence.json"),
        "evidence_grade_counts": {g: grade_n[g] for g in GRADE_ORDER},
        "component_activation_n": {k: component_n[k] for k in ("C1", "C2", "C3", "C4")},
        "model_activation_n": dict(model_active),
        "candidate_c_fallback_n": len(predictions) - model_active["candidate_c_historical"],
        "identity_attempt_n": identity_attempt,
        "identity_matched_n": identity_match,
        "identity_match_rate": None if not identity_attempt else identity_match / identity_attempt,
        "historical_capability_attempt_n": ability_attempt,
        "historical_capability_available_n": ability_available,
        "historical_capability_coverage_rate": None if not ability_attempt else ability_available / ability_attempt,
        "uncertainty_mean_by_grade": means,
        "uncertainty_monotonicity_observed": mono,
        "uncertainty_contract_monotonic": monotonic_contract_holds(),
        "real_probability_mass_supported_n": sum(bool(x["probability_mass_supported"]) for x in predictions),
        "probability_mass_redistribution_active_n": 0,
        "understat_identity_receipt_sha256": sha_file(out / "understat_identity_receipt.json"),
        "source_coverage_audit_sha256": sha_file(out / "source_coverage_audit.json"),
        "new_or_target_labels_read_n": 0,
        "api_football_requests": 0,
        "api_keys_or_secrets_used": 0,
        "eightbo_used_in_translator": False,
        "coach_feature_used": False,
        "formal_weight": 0,
        "formal_promotion_eligible": False,
    }
    dump(out / "pre_score_manifest.json", pre)
    dump(out / "historical_pit_gate.json", {"schema_version": "football3-historical-pit-replay-gate-v1", "pipeline_integrity": "PASS", "status": "HISTORICAL_PIT_REPLAY_PREDICTIONS_FROZEN", "prediction_sha256": pre["prediction_sha256"], "cohort_identity_sha256": pre["cohort_identity_sha256"], "labels_read_in_prediction_phase": False, "formal_weight": 0})
    return pre


def outcome(label: dict[str, Any]) -> str:
    h, a = int(label["home_goals"]), int(label["away_goals"])
    return "home" if h > a else "draw" if h == a else "away"


def probs(p: dict[str, Any]) -> dict[str, float]:
    z = {"home": float(p["p_home"]), "draw": float(p["p_draw"]), "away": float(p["p_away"])}
    if min(z.values()) < 0 or abs(sum(z.values()) - 1) > 1e-8:
        raise ReplayError("invalid probability vector")
    return z


def metric_rows(rows: list[dict[str, Any]], labels: dict[str, Any], model: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "logloss": None, "brier": None, "rps": None, "top1": None}
    ll = br = rp = top = 0.0
    for r in rows:
        p = probs(r[model]); y = outcome(labels[r["fixture_id"]])
        ll += -math.log(max(1e-15, p[y]))
        br += sum((p[k] - (1.0 if y == k else 0.0)) ** 2 for k in OUTCOMES)
        rp += ((p["home"] - (1.0 if y == "home" else 0.0)) ** 2 + ((p["home"] + p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0
        top += max(OUTCOMES, key=lambda k: p[k]) == y
    n = len(rows)
    return {"n": n, "logloss": ll / n, "brier": br / n, "rps": rp / n, "top1": top / n}


def allowed_labels(path: pathlib.Path, allowed: set[str]) -> dict[str, Any]:
    rx = re.compile(r'"fixture_id"\s*:\s*"([^"]+)"')
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            m = rx.search(line)
            if not m:
                raise ReplayError("label row missing fixture_id")
            fid = m.group(1)
            if fid not in allowed:
                continue
            z = json.loads(line)
            out[fid] = {"fixture_id": fid, "cutoff": z.get("cutoff"), "home_goals": z["home_goals"], "away_goals": z["away_goals"]}
    if set(out) != allowed:
        raise ReplayError(f"historical scorer label whitelist incomplete {len(out)}/{len(allowed)}")
    return out


def percentile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] * (hi - pos) + ys[hi] * (pos - lo)


def loss_vector(rows: list[dict[str, Any]], labels: dict[str, Any], model: str, metric: str) -> list[float]:
    out = []
    for r in rows:
        p = probs(r[model]); y = outcome(labels[r["fixture_id"]])
        if metric == "logloss":
            out.append(-math.log(max(1e-15, p[y])))
        elif metric == "brier":
            out.append(sum((p[k] - (1.0 if y == k else 0.0)) ** 2 for k in OUTCOMES))
        elif metric == "rps":
            out.append(((p["home"] - (1.0 if y == "home" else 0.0)) ** 2 + ((p["home"] + p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0)
        else:
            raise ReplayError(metric)
    return out


def bootstrap_delta(rows: list[dict[str, Any]], labels: dict[str, Any], model: str, metric: str) -> dict[str, float]:
    a = loss_vector(rows, labels, model, metric); b = loss_vector(rows, labels, "protected_v2", metric)
    d = [x - y for x, y in zip(a, b)]
    rng = random.Random(BOOTSTRAP_SEED + {"logloss": 11, "brier": 23, "rps": 37}[metric])
    vals = []
    n = len(d)
    for _ in range(BOOTSTRAP_N):
        vals.append(sum(d[rng.randrange(n)] for _ in range(n)) / n)
    return {"mean_delta": statistics.fmean(d), "ci95_low": percentile(vals, 0.025), "ci95_high": percentile(vals, 0.975), "bootstrap_n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED}


def score(v2: pathlib.Path, label_vault: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    pre = json.load(open(out / "pre_score_manifest.json"))
    pred_path = out / "historical_pit_predictions.jsonl"
    if pre["labels_read_in_prediction_phase"] or sha_file(pred_path) != pre["prediction_sha256"]:
        raise ReplayError("predictor/scorer separation SHA gate failed")
    rows = readjl(pred_path)
    if len(rows) != COHORT_N:
        raise ReplayError("scorer cohort n mismatch")
    ids = {str(x["fixture_id"]) for x in rows}
    labels = allowed_labels(label_vault, ids)
    overall = {m: metric_rows(rows, labels, m) for m in MODELS}
    def subgroup(name: str, subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {"group": name, "n": len(subset), "models": {m: metric_rows(subset, labels, m) for m in MODELS}}
    groups = {
        "actual_draw": subgroup("actual_draw", [r for r in rows if outcome(labels[r["fixture_id"]]) == "draw"]),
        "actual_home": subgroup("actual_home", [r for r in rows if outcome(labels[r["fixture_id"]]) == "home"]),
        "actual_away": subgroup("actual_away", [r for r in rows if outcome(labels[r["fixture_id"]]) == "away"]),
        "weak_team_win": subgroup("weak_team_win", [r for r in rows if outcome(labels[r["fixture_id"]]) in {"home", "away"} and outcome(labels[r["fixture_id"]]) == ("home" if r["protected_v2"]["p_home"] < r["protected_v2"]["p_away"] else "away")]),
    }
    for bucket in sorted({str(r.get("shared_cold_start_bucket")) for r in rows}):
        groups["cold_start_" + bucket] = subgroup("cold_start_" + bucket, [r for r in rows if str(r.get("shared_cold_start_bucket")) == bucket])
    team_counts = Counter()
    for r in rows:
        team_counts[str(r["home_team"])] += 1; team_counts[str(r["away_team"])] += 1
    for team, n in sorted(team_counts.items(), key=lambda x: (-x[1], x[0])):
        if n >= 20:
            groups["team_" + norm(team).replace(" ", "_")] = subgroup(team, [r for r in rows if team in {r["home_team"], r["away_team"]}])
    active = [r for r in rows if r["candidate_c_historical_effect"]["active"]]
    groups["candidate_c_active"] = subgroup("candidate_c_active", active)
    groups["candidate_c_fallback"] = subgroup("candidate_c_fallback", [r for r in rows if not r["candidate_c_historical_effect"]["active"]])
    bootstrap = {metric: bootstrap_delta(rows, labels, "candidate_c_historical", metric) for metric in ("logloss", "brier", "rps")}
    base = overall["protected_v2"]; cand = overall["candidate_c_historical"]
    top_delta = cand["top1"] - base["top1"]
    passed = (
        len(active) >= PROMOTION_MIN_ACTIVE_N
        and bootstrap["logloss"]["mean_delta"] <= -PROMOTION_MIN_LL_IMPROVEMENT
        and bootstrap["logloss"]["ci95_high"] < 0
        and bootstrap["brier"]["ci95_high"] < 0
        and bootstrap["rps"]["mean_delta"] <= 0
        and top_delta >= -0.005
        and bool(pre["uncertainty_monotonicity_observed"])
    )
    status = "HISTORICAL_PIT_CANDIDATE_PASSED" if passed else "HISTORICAL_PIT_NOT_PROMOTED"
    result = {
        "schema_version": "football3-historical-pit-replay-score-v1",
        "status": status,
        "scientific_claim": "HISTORICAL_PIT_REPLAY_ONLY_NOT_FUTURE_PROSPECTIVE_NOT_NEW_BLIND_TEST",
        "n": len(rows),
        "cohort_identity_sha256": pre["cohort_identity_sha256"],
        "models": overall,
        "subgroups": groups,
        "bootstrap_candidate_c_historical_vs_protected_v2": bootstrap,
        "promotion_screen_preregistered": {
            "min_active_n": PROMOTION_MIN_ACTIVE_N,
            "min_mean_logloss_improvement": PROMOTION_MIN_LL_IMPROVEMENT,
            "logloss_ci95_high_lt_zero": True,
            "brier_ci95_high_lt_zero": True,
            "rps_mean_delta_lte_zero": True,
            "top1_delta_gte": -0.005,
            "uncertainty_monotonicity_required": True,
        },
        "candidate_c_active_n": len(active),
        "candidate_c_fallback_n": len(rows) - len(active),
        "uncertainty_mean_by_grade": pre["uncertainty_mean_by_grade"],
        "uncertainty_monotonicity": pre["uncertainty_monotonicity_observed"],
        "stable_non_micro_improvement": passed,
        "new_fixture_labels_read_n": 0,
        "labels_read_only_after_prediction_sha_freeze": True,
        "formal_weight": 0,
        "formal_promotion_eligible": False,
        "formal_enablement": False,
    }
    dump(out / "historical_pit_score.json", result)
    gate = json.load(open(out / "historical_pit_gate.json"))
    gate.update({"status": status, "score_sha256": sha_file(out / "historical_pit_score.json"), "terminal": "NO_FORMAL_ENABLEMENT_NO_MERGE_NO_WEIGHT_CHANGE"})
    dump(out / "historical_pit_gate.json", gate)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("predict"); p.add_argument("--v2", type=pathlib.Path, required=True); p.add_argument("--out", type=pathlib.Path, required=True)
    s = sp.add_parser("score"); s.add_argument("--v2", type=pathlib.Path, required=True); s.add_argument("--label-vault", type=pathlib.Path, required=True); s.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(); result = run_prediction(a.v2, a.out) if a.cmd == "predict" else score(a.v2, a.label_vault, a.out)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
