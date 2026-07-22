#!/usr/bin/env python3
"""V6.4.5 r3: retry-safe low-concurrency StatsBomb transport with guaranteed receipt.

Model/features/splits are unchanged from r2. This version exists only to eliminate transport
ambiguity: every exit writes a PASS or FAIL_DATA receipt with download diagnostics.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import v6_prelineup_stability_v645 as r1

OUT = ROOT / "manifests" / "v6_prelineup_stability_v645_r3_status.json"


def write(payload):
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def retry_json(url: str, attempts: int = 5):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-v6.4-lineup-research/3.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            time.sleep(min(8.0, 0.75 * (2 ** i)))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        matches = sorted(retry_json(r1.SB_MATCHES), key=lambda m: (m["match_date"], m["match_id"]))
        fd = r1.get_csv(r1.FD)
        byfd = defaultdict(list)
        for row in fd:
            try:
                byfd[r1.parse_date(row["Date"])].append(row)
            except Exception:
                pass

        payloads, failures = {}, {}

        def fetch(m):
            mid = int(m["match_id"])
            return mid, retry_json(r1.SB_LINEUP.format(match_id=mid))

        # Intentionally conservative concurrency to avoid raw.githubusercontent throttling.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch, m): int(m["match_id"]) for m in matches}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    k, value = fut.result()
                    payloads[k] = value
                except Exception as exc:
                    failures[mid] = f"{type(exc).__name__}: {exc}"

        if len(payloads) < max(200, int(0.80 * len(matches))):
            write({
                "schema_version": "V6.4.5-prelineup-stability-pilot-r3-retry-safe",
                "generated_at_utc": generated.isoformat(),
                "status": "FAIL_DATA",
                "reason": "insufficient lineup downloads",
                "data_audit": {
                    "statsbomb_matches": len(matches),
                    "lineups_downloaded": len(payloads),
                    "lineup_download_failures": len(failures),
                    "failure_examples": dict(list(sorted(failures.items()))[:10]),
                },
                "governance": {"model_logic_changed": False, "current_rule_change": False},
            })
            return 1

        xi_hist = defaultdict(lambda: deque(maxlen=5))
        last_mgr, tenure = {}, defaultdict(int)
        rows, starter_fail, identity_unmatched = [], 0, 0

        for m in matches:
            date = m["match_date"]
            hn = m["home_team"]["home_team_name"]
            an = m["away_team"]["away_team_name"]
            candidates = byfd.get(date, [])
            raw = next((x for x in candidates if r1.norm(x.get("HomeTeam")) == r1.norm(hn) and r1.norm(x.get("AwayTeam")) == r1.norm(an)), None)
            if raw is None:
                # Require both home and away tolerant prefix matches, unlike the overly broad r1 fallback.
                raw = next((x for x in candidates if (
                    r1.norm(hn)[:6]
                    and (r1.norm(hn)[:6] in r1.norm(x.get("HomeTeam")) or r1.norm(x.get("HomeTeam"))[:6] in r1.norm(hn))
                    and r1.norm(an)[:6]
                    and (r1.norm(an)[:6] in r1.norm(x.get("AwayTeam")) or r1.norm(x.get("AwayTeam"))[:6] in r1.norm(an))
                )), None)
            if raw is None:
                identity_unmatched += 1
            mk = r1.market(raw) if raw else None

            hid = int(m["home_team"]["home_team_id"])
            aid = int(m["away_team"]["away_team_id"])
            hmgr = ((m["home_team"].get("managers") or [{}])[0].get("id"))
            amgr = ((m["away_team"].get("managers") or [{}])[0].get("id"))
            hchg = hid in last_mgr and hmgr is not None and last_mgr[hid] != hmgr
            achg = aid in last_mgr and amgr is not None and last_mgr[aid] != amgr

            # IMPORTANT: feature is built BEFORE current match lineup enters history.
            if mk and len(xi_hist[hid]) >= 2 and len(xi_hist[aid]) >= 2:
                x = r1.row_features(mk, list(xi_hist[hid]), list(xi_hist[aid]), hchg, achg, tenure[hid], tenure[aid])
                truth = "home" if m["home_score"] > m["away_score"] else "away" if m["home_score"] < m["away_score"] else "draw"
                rows.append({"date": date, "match_id": m["match_id"], "x": x, "market": mk, "truth": truth})

            lp = payloads.get(int(m["match_id"]))
            if lp is not None:
                hxi, axi = r1.starter_ids(lp, hid), r1.starter_ids(lp, aid)
                if len(hxi) >= 9:
                    xi_hist[hid].append(hxi)
                else:
                    starter_fail += 1
                if len(axi) >= 9:
                    xi_hist[aid].append(axi)
                else:
                    starter_fail += 1
            else:
                starter_fail += 2

            if hmgr is not None:
                tenure[hid] = 1 if hchg or hid not in last_mgr else tenure[hid] + 1
                last_mgr[hid] = hmgr
            if amgr is not None:
                tenure[aid] = 1 if achg or aid not in last_mgr else tenure[aid] + 1
                last_mgr[aid] = amgr

        rows = sorted(rows, key=lambda x: (x["date"], x["match_id"]))
        n = len(rows)
        if n < 180:
            write({
                "schema_version": "V6.4.5-prelineup-stability-pilot-r3-retry-safe",
                "generated_at_utc": generated.isoformat(),
                "status": "FAIL_DATA",
                "reason": "insufficient usable leakage-safe rows",
                "data_audit": {
                    "statsbomb_matches": len(matches), "lineups_downloaded": len(payloads),
                    "lineup_download_failures": len(failures), "starter_parse_failures": starter_fail,
                    "identity_unmatched": identity_unmatched, "usable_rows": n,
                },
                "governance": {"model_logic_changed": False, "current_rule_change": False},
            })
            return 1

        a, b = int(.60 * n), int(.80 * n)
        tr, va, ho = rows[:a], rows[a:b], rows[b:]
        bv, bh = r1.score(va), r1.score(ho)
        candidates = []
        for l2 in r1.L2_GRID:
            decisive = [x for x in tr if x["truth"] != "draw"]
            model = r1.fit([x["x"] for x in decisive], [1 if x["truth"] == "home" else 0 for x in decisive], l2)
            mv = r1.score(va, model)
            proper = mv["mean_brier"] <= bv["mean_brier"] + 1e-12 and mv["mean_log_loss"] <= bv["mean_log_loss"] + 1e-12
            candidates.append({"l2": l2, "proper_nonworse": proper, "validation": mv})
        eligible = [c for c in candidates if c["proper_nonworse"]] or candidates
        eligible.sort(key=lambda c: (-c["validation"]["accuracy"], c["validation"]["mean_log_loss"]))
        selected = eligible[0]
        decisive = [x for x in tr + va if x["truth"] != "draw"]
        refit = r1.fit([x["x"] for x in decisive], [1 if x["truth"] == "home" else 0 for x in decisive], selected["l2"])
        mh = r1.score(ho, refit)
        guard = {
            "brier_nonworse": mh["mean_brier"] <= bh["mean_brier"] + 1e-12,
            "log_loss_nonworse": mh["mean_log_loss"] <= bh["mean_log_loss"] + 1e-12,
        }
        write({
            "schema_version": "V6.4.5-prelineup-stability-pilot-r3-retry-safe",
            "generated_at_utc": generated.isoformat(),
            "status": "PASS",
            "scope": {
                "competition": "Bundesliga", "season": "2023/24",
                "current_match_actual_xi_used_as_feature": False,
                "features_use_only_prior_lineups": True,
            },
            "data_audit": {
                "statsbomb_matches": len(matches), "lineups_downloaded": len(payloads),
                "lineup_download_failures": len(failures), "starter_parse_failures": starter_fail,
                "identity_unmatched": identity_unmatched, "usable_rows": n,
                "train": len(tr), "validation": len(va), "holdout": len(ho),
            },
            "baseline_validation": bv,
            "selected_candidate": selected,
            "baseline_holdout": bh,
            "challenger_holdout": mh,
            "accuracy_gain_pp": 100.0 * (mh["accuracy"] - bh["accuracy"]),
            "proper_score_guard": guard,
            "pilot_gate_passed": bool(mh["accuracy"] > bh["accuracy"] and all(guard.values())),
            "governance": {
                "r1_feature_logic_unchanged": True,
                "transport_retry_safe": True,
                "pilot_only": True,
                "single_season_not_promotion_evidence": True,
                "current_rule_change": False,
                "formal_weight_change": False,
                "runtime_probability_change": False,
            },
        })
        return 0
    except Exception as exc:
        write({
            "schema_version": "V6.4.5-prelineup-stability-pilot-r3-retry-safe",
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_DATA",
            "reason": f"{type(exc).__name__}: {exc}",
            "governance": {"model_logic_changed": False, "current_rule_change": False},
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
