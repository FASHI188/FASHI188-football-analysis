from __future__ import annotations
import argparse, json, zipfile
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import evaluate_c069_matched_pair_state_dynamics as base


def load_reduced_events_nested(events_zip, wanted):
    """Technical-only parser fix: accept events_*.json at any ZIP directory depth."""
    z = zipfile.ZipFile(events_zip)
    red = defaultdict(list); endm = defaultdict(lambda: 90.0); n_ev = 0
    selected = []
    goal_tag_events = 0; own_goal_tag_events = 0
    for n in z.namelist():
        bn = Path(n).name
        if not bn.startswith('events_') or not bn.endswith('.json'):
            continue
        selected.append(n)
        rr = json.loads(z.read(n))
        if isinstance(rr, dict) and 'events' in rr:
            rr = rr['events']
        n_ev += len(rr)
        for i, e in enumerate(rr):
            mid = int(e.get('matchId', -1))
            tags = {int(t['id']) for t in e.get('tags', []) if 'id' in t}
            if 101 in tags: goal_tag_events += 1
            if 102 in tags: own_goal_tag_events += 1
            if mid not in wanted:
                continue
            m = base.minute_of(e)
            if m is None:
                continue
            endm[mid] = max(endm[mid], min(m, 105.0))
            goal = 101 in tags; own = 102 in tags; shot = (e.get('eventName') == 'Shot')
            if goal or own or shot:
                red[mid].append((float(m), i, int(e.get('teamId') or -1), bool(shot), bool(goal), bool(own)))
    for mid in red:
        red[mid].sort(key=lambda x: (x[0], x[1]))
    print('C069_EVENT_ARCHIVE_DIAGNOSTIC=' + json.dumps({
        'zip_members_total': len(z.namelist()),
        'selected_event_members': selected,
        'events_seen': n_ev,
        'goal_tag_events': goal_tag_events,
        'own_goal_tag_events': own_goal_tag_events,
        'wanted_matches_with_reduced_events': len(red),
    }, sort_keys=True))
    return red, endm, n_ev


def build_panel_zero_floor(M, summaries):
    """Technical-only fix: honor the preregistered 0.2 lambda floor when a competition prefix has 0 total goals."""
    M = M[M.match_id.isin(summaries)].copy().sort_values(['dt', 'match_id']).reset_index(drop=True)
    totals = M.groupby('cid').size().to_dict()
    idx = Counter(); TH = defaultdict(list); CH = defaultdict(list); ST = defaultdict(Counter); GST = Counter(); R = []
    for date, G in M.groupby('date', sort=True):
        for _, r in G.iterrows():
            h, a, c = int(r.home), int(r.away), int(r.cid); hc = CH[c]
            lgh = float(np.mean([x[0] for x in hc])) if hc else 1.4
            lga = float(np.mean([x[1] for x in hc])) if hc else 1.1
            raw_lm = (lgh + lga) / 2.0
            lm = max(raw_lm, 0.2)
            def ga(t):
                x = TH[t]; n = len(x)
                return n, (float(np.mean([q[0] for q in x])) if n else lm), (float(np.mean([q[1] for q in x])) if n else lm)
            hn, hgf, hga = ga(h); an, agf, aga = ga(a)
            hgf = (hgf * hn + base.PRIOR * lm) / (hn + base.PRIOR)
            hga = (hga * hn + base.PRIOR * lm) / (hn + base.PRIOR)
            agf = (agf * an + base.PRIOR * lm) / (an + base.PRIOR)
            aga = (aga * an + base.PRIOR * lm) / (an + base.PRIOR)
            lh = float(np.clip(lgh * (hgf / lm) * (aga / lm), 0.2, 3.5))
            la = float(np.clip(lga * (agf / lm) * (hga / lm), 0.2, 3.5))
            P = base.phda(lh, la)
            fh = base.team_features(ST[h], GST); fa = base.team_features(ST[a], GST)
            f = {**r.to_dict(), 'hn': hn, 'an': an, 'baseline_pdraw': P[1], 'baseline_abs_home_away_prob_gap': abs(P[0] - P[2]), 'baseline_expected_total_goals': lh + la, 'baseline_abs_log_lambda_ratio': abs(np.log(lh / la)), 'competition_scoring_environment': float(np.mean([x[0] + x[1] for x in hc])) if hc else lgh + lga, 'season_stage': idx[c] / max(1, totals[c] - 1)}
            base.add_state_pair(f, fh, fa); R.append(f)
        for _, r in G.iterrows():
            h, a, c = int(r.home), int(r.away), int(r.cid)
            TH[h].append((float(r.hg), float(r.ag))); TH[a].append((float(r.ag), float(r.hg))); CH[c].append((float(r.hg), float(r.ag))); idx[c] += 1
            for team in [h, a]:
                q = summaries[int(r.match_id)][team]; ST[team].update(q); GST.update(q)
    out = pd.DataFrame(R)
    diag = {
        'state_reconciled_input_matches': int(len(M)),
        'panel_rows_before_history_gate': int(len(out)),
        'max_home_prior_matches': int(out.hn.max()) if len(out) else None,
        'max_away_prior_matches': int(out.an.max()) if len(out) else None,
        'rows_hn_an_ge_5': int(((out.hn >= 5) & (out.an >= 5)).sum()) if len(out) else 0,
        'competitions': int(out.cid.nunique()) if len(out) else 0,
        'dates': int(out.date.nunique()) if len(out) else 0,
    }
    print('C069_ZERO_LABEL_COVERAGE_DIAGNOSTIC=' + json.dumps(diag, sort_keys=True))
    if not len(out):
        raise RuntimeError('C069_ZERO_LABEL_COVERAGE_EMPTY_BEFORE_HISTORY_GATE')
    return out.sort_values(['dt', 'match_id']).reset_index(drop=True)


base.load_reduced_events = load_reduced_events_nested
base.build_panel = build_panel_zero_floor

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--matches', required=True); ap.add_argument('--events', required=True); ap.add_argument('--contract', required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args(); base.run(Path(a.matches), Path(a.events), Path(a.contract), Path(a.out))
