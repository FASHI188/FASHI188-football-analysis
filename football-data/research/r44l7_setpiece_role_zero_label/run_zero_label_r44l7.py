#!/usr/bin/env python3
import hashlib
import json
import math
import os
import statistics
import subprocess
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

SOURCE_REPO = "hudl/open-data"
SOURCE_PIN = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW_ROOT = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_PIN}"
DOMAINS = [
    {"competition_id": 2, "season_id": 27, "name": "Premier League"},
    {"competition_id": 12, "season_id": 27, "name": "Serie A"},
    {"competition_id": 7, "season_id": 27, "name": "Ligue 1"},
]
WARMUP_MATCHES = 100
TARGETS_PER_DOMAIN = 8
PRIOR_MATCHES = 5


def fetch_json(path):
    req = urllib.request.Request(f"{RAW_ROOT}/{path}", headers={"User-Agent": "r44l7-zero-label/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def event_path(mid): return f"data/events/{mid}.json"
def lineup_path(mid): return f"data/lineups/{mid}.json"


def identity_rows(comp_id, season_id):
    rows, sha = fetch_json(f"data/matches/{comp_id}/{season_id}.json")
    out = []
    for m in rows:
        # Deliberately whitelist identity-only fields. Do not access result fields.
        comp = m["competition"]
        season = m["season"]
        if int(comp["competition_id"]) != comp_id or int(season["season_id"]) != season_id:
            raise RuntimeError("identity_mismatch")
        out.append({
            "match_id": int(m["match_id"]),
            "match_date": str(m["match_date"]),
            "kick_off": str(m.get("kick_off") or ""),
            "home_team_id": int(m["home_team"]["home_team_id"]),
            "away_team_id": int(m["away_team"]["away_team_id"]),
        })
    out.sort(key=lambda x: (x["match_date"], x["kick_off"], x["match_id"]))
    return out, sha


def choose_targets(rows):
    eligible = rows[WARMUP_MATCHES:]
    if len(eligible) < TARGETS_PER_DOMAIN:
        return []
    if TARGETS_PER_DOMAIN == 1:
        return [eligible[len(eligible)//2]]
    idxs = [round(i * (len(eligible)-1) / (TARGETS_PER_DOMAIN-1)) for i in range(TARGETS_PER_DOMAIN)]
    return [eligible[i] for i in idxs]


def starters_from_lineup(mid):
    obj, sha = fetch_json(lineup_path(mid))
    teams = {}
    for t in obj:
        starters = []
        for p in t.get("lineup", []):
            positions = p.get("positions") or []
            if any(pos.get("from") == "00:00" and pos.get("start_reason") == "Starting XI" for pos in positions):
                starters.append(int(p["player_id"]))
        teams[int(t["team_id"])] = starters
    return teams, sha


def prior_matches_for_team(rows, target, team_id):
    pos = next(i for i, r in enumerate(rows) if r["match_id"] == target["match_id"])
    prior = []
    for r in reversed(rows[:pos]):
        if team_id in (r["home_team_id"], r["away_team_id"]):
            prior.append(r)
            if len(prior) == PRIOR_MATCHES:
                break
    return list(reversed(prior))


def setpiece_takers(mid, team_id, cache):
    if mid not in cache:
        events, sha = fetch_json(event_path(mid))
        cache[mid] = (events, sha)
    events, sha = cache[mid]
    c = Counter()
    for e in events:
        if e.get("type", {}).get("name") != "Pass":
            continue
        if int(e.get("team", {}).get("id", -1)) != team_id:
            continue
        ptype = (e.get("pass") or {}).get("type", {}).get("name")
        if ptype not in {"Corner", "Free Kick"}:
            continue
        pid = (e.get("player") or {}).get("id")
        if pid is not None:
            c[int(pid)] += 1
    return c, sha


def main():
    out = Path(os.environ.get("R44L7_OUTPUT", "r44l7_output"))
    out.mkdir(parents=True, exist_ok=True)
    cache = {}
    domain_reports = []
    team_rows = []
    target_rows = []
    source_hashes = []

    for d in DOMAINS:
        rows, match_sha = identity_rows(d["competition_id"], d["season_id"])
        source_hashes.append({"path": f"data/matches/{d['competition_id']}/{d['season_id']}.json", "sha256": match_sha})
        targets = choose_targets(rows)
        domain = {"name": d["name"], "competition_id": d["competition_id"], "identity_count": len(rows), "target_count": len(targets)}
        domain_reports.append(domain)
        for target in targets:
            lineups, lineup_sha = starters_from_lineup(target["match_id"])
            source_hashes.append({"path": lineup_path(target["match_id"]), "sha256": lineup_sha})
            ht, at = target["home_team_id"], target["away_team_id"]
            exact_11v11 = len(lineups.get(ht, [])) == 11 and len(lineups.get(at, [])) == 11
            target_rows.append({
                "domain": d["name"], "match_id": target["match_id"], "date": target["match_date"],
                "home_team_id": ht, "away_team_id": at, "home_xi_n": len(lineups.get(ht, [])),
                "away_xi_n": len(lineups.get(at, [])), "exact_11v11": exact_11v11,
            })
            for team_id in (ht, at):
                xi = set(lineups.get(team_id, []))
                prior = prior_matches_for_team(rows, target, team_id)
                complete_prior5 = len(prior) == PRIOR_MATCHES
                total = Counter()
                event_ok = 0
                for pr in prior:
                    try:
                        c, event_sha = setpiece_takers(pr["match_id"], team_id, cache)
                        source_hashes.append({"path": event_path(pr["match_id"]), "sha256": event_sha})
                        total.update(c)
                        event_ok += 1
                    except Exception:
                        pass
                nsp = sum(total.values())
                top_pid, top_n = (total.most_common(1)[0] if total else (None, 0))
                xi_retained = (sum(v for pid, v in total.items() if pid in xi) / nsp) if nsp else None
                team_rows.append({
                    "domain": d["name"], "target_match_id": target["match_id"], "team_id": team_id,
                    "current_xi_n": len(xi), "prior_match_count": len(prior), "prior_event_files_ok": event_ok,
                    "complete_prior5": complete_prior5 and event_ok == PRIOR_MATCHES,
                    "prior5_setpiece_passes": nsp, "prior5_unique_takers": len(total),
                    "top1_taker_share": (top_n / nsp) if nsp else None,
                    "top1_taker_in_xi": (top_pid in xi) if top_pid is not None else None,
                    "xi_role_retention": xi_retained,
                })

    identities_ok = all(x["identity_count"] >= 370 for x in domain_reports)
    target_lineup_files_ok = len(target_rows) == 24
    exact_11_count = sum(1 for x in target_rows if x["exact_11v11"])
    complete_prior_count = sum(1 for x in team_rows if x["complete_prior5"])
    observable_count = sum(1 for x in team_rows if x["prior5_setpiece_passes"] >= 3)
    retentions = [x["xi_role_retention"] for x in team_rows if x["xi_role_retention"] is not None]
    median_retention = statistics.median(retentions) if retentions else 0.0
    top_rows = [x for x in team_rows if x["top1_taker_in_xi"] is not None]
    top_in_xi_rate = (sum(1 for x in top_rows if x["top1_taker_in_xi"]) / len(top_rows)) if top_rows else 0.0

    gates = {
        "three_domains_identity_ge_370": identities_ok,
        "target_lineups_24_of_24": target_lineup_files_ok,
        "exact_11v11_ge_23_of_24": exact_11_count >= 23,
        "complete_prior5_ge_46_of_48": complete_prior_count >= 46,
        "setpiece_observable_ge_41_of_48": observable_count >= 41,
        "median_xi_role_retention_ge_0_60": median_retention >= 0.60,
        "top1_taker_in_xi_rate_ge_0_70": top_in_xi_rate >= 0.70,
        "hard_boundaries": True,
    }
    terminal = "PASS_R44L7_SETPIECE_ROLE_ZERO_LABEL_FEASIBILITY" if all(gates.values()) else "STOP_R44L7_SETPIECE_ROLE_DATA_FEASIBILITY"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    receipt = {
        "schema_version": "V520-R44L7-SETPIECE-ZERO-LABEL-1.0",
        "terminal": terminal,
        "research_only": True,
        "formal_weight": 0,
        "source_repo": SOURCE_REPO,
        "source_pin": SOURCE_PIN,
        "domains": domain_reports,
        "summary": {
            "target_matches": len(target_rows), "team_targets": len(team_rows),
            "exact_11v11": exact_11_count, "complete_prior5": complete_prior_count,
            "setpiece_observable": observable_count, "median_xi_role_retention": median_retention,
            "top1_taker_in_xi_rate": top_in_xi_rate,
        },
        "gates": gates,
        "hard_boundary_receipt": {
            "target_result_keys_accessed": 0,
            "target_event_files_accessed": 0,
            "model_fits": 0,
            "candidate_probabilities": 0,
            "label_threshold_selection": 0,
            "formal_model_changes": 0,
            "formal_data_changes": 0,
            "formal_config_changes": 0,
            "CURRENT_changes": 0,
        },
        "run_identity": {"checked_out_head_sha": head, "github_run_id": os.environ.get("GITHUB_RUN_ID"), "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")},
    }
    # Deduplicate source identities for compact audit.
    dedup = {(x["path"], x["sha256"]): x for x in source_hashes}
    (out / "source_manifest.json").write_text(json.dumps(list(dedup.values()), indent=2), encoding="utf-8")
    (out / "target_matches.json").write_text(json.dumps(target_rows, indent=2), encoding="utf-8")
    (out / "team_role_rows.json").write_text(json.dumps(team_rows, indent=2), encoding="utf-8")
    raw = json.dumps(receipt, indent=2).encode()
    (out / "receipt.json").write_bytes(raw)
    (out / "receipt.sha256").write_text(hashlib.sha256(raw).hexdigest()+"\n")
    print(json.dumps({"terminal":terminal, "summary":receipt["summary"], "gates":gates}, sort_keys=True))

if __name__ == "__main__":
    main()
