from __future__ import annotations
import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
SCHEMA = 'football3-v3-c3-score-matrix-development-contract-v1'
EXPECTED_BASE = '6375e7854285f9a40335157dec3cc39da44606c0'
EXPECTED_BRANCH = 'football3/v3-c3-score-matrix-development-v1'
EXPECTED_DB_SHA256 = 'f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681'
BIG5 = ('Bundesliga', 'EPL', 'La liga', 'Ligue 1', 'Serie A')
COMPETITION_MAP = {'EPL': 'ENG_PremierLeague', 'Bundesliga': 'GER_Bundesliga', 'La liga': 'ESP_LaLiga', 'Serie A': 'ITA_SerieA', 'Ligue 1': 'FRA_Ligue1'}
WARMUP_SEASONS = (2015, 2016, 2017)
FIT_SEASONS = (2018, 2019)
EVAL_SEASONS = (2020, 2021, 2022)
EXPECTED_COUNTS = {'warmup': 5477, 'fit': 3551, 'eval': 5478}
EPS = 1e-15

class DevelopmentError(RuntimeError):
    pass

@dataclass(frozen=True)
class SourceRow:
    match_id: int
    league: str
    season: int
    kickoff: datetime
    home_id: int
    away_id: int
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int
    home_xg: float
    away_xg: float

    @property
    def fixture_id(self) -> str:
        return f'understat_match_{self.match_id}'

    @property
    def competition_id(self) -> str:
        return COMPETITION_MAP[self.league]

    @property
    def season_text(self) -> str:
        return f'{self.season}/{str(self.season + 1)[-2:]}'

    @property
    def home_team_id(self) -> str:
        return f'understat_team_{self.home_id}'

    @property
    def away_team_id(self) -> str:
        return f'understat_team_{self.away_id}'

@dataclass(frozen=True)
class FitDatum:
    fixture_id: str
    competition_id: str
    signal: float
    actual_margin: int
    conditional_margin_probs: tuple[tuple[int, float], ...]
    baseline_conditional_nll: float

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()

def load_contract(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding='utf-8'))
    if x.get('schema_version') != SCHEMA or x.get('status') != 'POST_VIEW_DEVELOPMENT_LOCKED':
        raise DevelopmentError('development contract identity/status mismatch')
    if x.get('exact_base') != EXPECTED_BASE or x.get('branch') != EXPECTED_BRANCH:
        raise DevelopmentError('development base/branch drift')
    src = x['source']
    if src['database_sha256'] != EXPECTED_DB_SHA256:
        raise DevelopmentError('source database pin drift')
    if src['classification'] != 'POST_VIEW_DEVELOPMENT_ONLY':
        raise DevelopmentError('source classification drift')
    if src['warmup_seasons'] != list(WARMUP_SEASONS) or src['fit_seasons'] != list(FIT_SEASONS) or src['development_eval_seasons'] != list(EVAL_SEASONS):
        raise DevelopmentError('chronological cohort drift')
    if tuple(src['allowed_leagues']) != BIG5:
        raise DevelopmentError('league scope drift')
    if not src['season_2023_or_later_query_forbidden'] or not src['protected_2023_cohort_forbidden']:
        raise DevelopmentError('protected cohort gate unlocked')
    opt = x['optimizer']
    if opt['candidate_beta_bounds'] != [-1.0, 1.0] or opt['derivative_tolerance'] != 1e-12 or opt['max_iterations'] != 200:
        raise DevelopmentError('optimizer drift')
    if opt['grid_search'] or opt['restarts'] or opt['refit_on_development_eval']:
        raise DevelopmentError('optimizer tuning surface unlocked')
    if x['prereg']['parameter'] != 'beta' or x['prereg']['parameter_count'] != 1:
        raise DevelopmentError('candidate parameter drift')
    if not all(x['forbidden_changes'].values()):
        raise DevelopmentError('forbidden surface unlocked')
    if x['inactive'] != {'status': 'NOT_AVAILABLE', 'weight': 0, 'matrix_delta': 0, 'data_ready': False}:
        raise DevelopmentError('inactive state drift')
    return x

def _parse_utc(text: str) -> datetime:
    dt = datetime.fromisoformat(str(text).strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def load_rows(db_path: Path) -> tuple[list[SourceRow], dict[str, int]]:
    if sha256_file(db_path) != EXPECTED_DB_SHA256:
        raise DevelopmentError('source database SHA256 mismatch')
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        placeholders = ','.join(('?' for _ in BIG5))
        sql = f'\n            SELECT id, league, season, date, h_id, a_id, team_h, team_a,\n                   h_goals, a_goals, h_xg, a_xg\n            FROM general_game_stats\n            WHERE season >= 2015 AND season <= 2022\n              AND league IN ({placeholders})\n            ORDER BY date ASC, id ASC\n        '
        raw = con.execute(sql, BIG5).fetchall()
    finally:
        con.close()
    rows: list[SourceRow] = []
    seen: set[int] = set()
    for values in raw:
        if len(values) != 12:
            raise DevelopmentError('source row shape mismatch')
        mid, league, season, date, hid, aid, home, away, hg, ag, hxg, axg = values
        if int(season) >= 2023:
            raise DevelopmentError('protected season leakage')
        if league not in BIG5 or league not in COMPETITION_MAP:
            raise DevelopmentError('unexpected competition')
        if mid in seen:
            raise DevelopmentError('duplicate source match id')
        seen.add(int(mid))
        row = SourceRow(int(mid), str(league), int(season), _parse_utc(str(date)), int(hid), int(aid), str(home), str(away), int(hg), int(ag), float(hxg), float(axg))
        if row.home_id == row.away_id or row.home_goals < 0 or row.away_goals < 0 or (row.home_goals > 14) or (row.away_goals > 14):
            raise DevelopmentError('invalid identity/score support')
        if not (math.isfinite(row.home_xg) and math.isfinite(row.away_xg) and (row.home_xg >= 0) and (row.away_xg >= 0)):
            raise DevelopmentError('invalid xG label')
        rows.append(row)
    counts = {'warmup': sum((r.season in WARMUP_SEASONS for r in rows)), 'fit': sum((r.season in FIT_SEASONS for r in rows)), 'eval': sum((r.season in EVAL_SEASONS for r in rows))}
    if counts != EXPECTED_COUNTS:
        raise DevelopmentError(f'cohort count drift: {counts}')
    if len(rows) != sum(EXPECTED_COUNTS.values()):
        raise DevelopmentError('unexpected cohort rows')
    if any((r.season not in WARMUP_SEASONS + FIT_SEASONS + EVAL_SEASONS for r in rows)):
        raise DevelopmentError('season scope leakage')
    return (rows, counts)

def kickoff_batches(rows: Iterable[SourceRow]) -> list[list[SourceRow]]:
    ordered = sorted(rows, key=lambda r: (r.kickoff, r.match_id))
    out: list[list[SourceRow]] = []
    cur: list[SourceRow] = []
    t: datetime | None = None
    for r in ordered:
        if t is None or r.kickoff == t:
            cur.append(r)
            t = r.kickoff
        else:
            out.append(cur)
            cur = [r]
            t = r.kickoff
    if cur:
        out.append(cur)
    return out

def _normalized_map(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for c in matrix:
        k = (int(c['home_goals']), int(c['away_goals']))
        p = float(c['probability'])
        if k in out or p < 0 or (not math.isfinite(p)):
            raise DevelopmentError('invalid matrix')
        out[k] = p
    z = math.fsum(out.values())
    if z <= 0 or not math.isfinite(z):
        raise DevelopmentError('invalid matrix mass')
    return {k: v / z for k, v in out.items()}

def _mix(v1_matrix: list[dict[str, Any]], xg_matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    v1 = _normalized_map(v1_matrix)
    xg = _normalized_map(xg_matrix)
    if set(v1) != set(xg):
        raise DevelopmentError('component support mismatch')
    q = {k: 0.25 * v1[k] + 0.75 * xg[k] for k in v1}
    z = math.fsum(q.values())
    return {k: v / z for k, v in q.items()}

def _signals(v1_matrix: list[dict[str, Any]], xg_matrix: list[dict[str, Any]]) -> dict[int, float | None]:
    v1 = _normalized_map(v1_matrix)
    xg = _normalized_map(xg_matrix)
    if set(v1) != set(xg):
        raise DevelopmentError('component support mismatch')
    totals = sorted({h + a for h, a in v1})
    out: dict[int, float | None] = {}
    for t in totals:
        keys = [k for k in v1 if sum(k) == t]
        mv1 = math.fsum((v1[k] for k in keys))
        mxg = math.fsum((xg[k] for k in keys))
        if mv1 <= 0 or mxg <= 0:
            out[t] = None
            continue
        ev1 = math.fsum(((h - a) * v1[h, a] for h, a in keys)) / mv1
        exg = math.fsum(((h - a) * xg[h, a] for h, a in keys)) / mxg
        s = exg - ev1
        out[t] = s if math.isfinite(s) else None
    return out

def tilt(v1_matrix: list[dict[str, Any]], xg_matrix: list[dict[str, Any]], beta: float, *, sign_only: bool=False) -> dict[tuple[int, int], float]:
    if not math.isfinite(beta):
        raise DevelopmentError('nonfinite beta')
    q = _mix(v1_matrix, xg_matrix)
    if beta == 0:
        return dict(q)
    sigs = _signals(v1_matrix, xg_matrix)
    totals: dict[int, float] = {}
    for k, p in q.items():
        totals[sum(k)] = totals.get(sum(k), 0.0) + p
    out: dict[tuple[int, int], float] = {}
    for t, mass in totals.items():
        keys = [k for k in q if sum(k) == t]
        s = sigs[t]
        if s is None or abs(s) <= 0 or len(keys) <= 1 or (mass <= 0):
            for k in keys:
                out[k] = q[k]
            continue
        if sign_only:
            s = 1.0 if s > 0 else -1.0
        cond = {k: q[k] / mass for k in keys}
        vals = {k: beta * s * (k[0] - k[1]) for k in keys}
        shift = max(vals.values())
        raw = {k: cond[k] * math.exp(vals[k] - shift) for k in keys}
        z = math.fsum(raw.values())
        if z <= 0 or not math.isfinite(z):
            raise DevelopmentError('tilt normalization failed')
        for k in keys:
            out[k] = mass * raw[k] / z
    if set(out) != set(q):
        raise DevelopmentError('support drift')
    for t, mass in totals.items():
        got = math.fsum((p for k, p in out.items() if sum(k) == t))
        if abs(got - mass) > 5e-12:
            raise DevelopmentError('P(T) invariant failed')
    return out

def fit_datum(v1_matrix: list[dict[str, Any]], xg_matrix: list[dict[str, Any]], hg: int, ag: int, fixture_id: str, competition_id: str) -> FitDatum:
    q = _mix(v1_matrix, xg_matrix)
    t = hg + ag
    actual = (hg, ag)
    if actual not in q:
        raise DevelopmentError('actual score outside support')
    keys = [k for k in q if sum(k) == t]
    mass = math.fsum((q[k] for k in keys))
    if mass <= 0:
        raise DevelopmentError('actual total has zero mass')
    sig = _signals(v1_matrix, xg_matrix).get(t)
    s = 0.0 if sig is None else float(sig)
    cond = tuple(sorted(((h - a, q[h, a] / mass) for h, a in keys), key=lambda x: x[0]))
    cp = q[actual] / mass
    return FitDatum(fixture_id, competition_id, s, hg - ag, cond, -math.log(max(EPS, cp)))

def _signal(d: FitDatum, sign_only: bool) -> float:
    if not sign_only:
        return d.signal
    return 1.0 if d.signal > 0 else -1.0 if d.signal < 0 else 0.0

def datum_nll(d: FitDatum, beta: float, *, sign_only: bool=False) -> float:
    s = _signal(d, sign_only)
    if s == 0:
        return d.baseline_conditional_nll
    logs = [math.log(max(EPS, p)) + beta * s * m for m, p in d.conditional_margin_probs]
    shift = max(logs)
    lz = shift + math.log(math.fsum((math.exp(v - shift) for v in logs)))
    actual_base = None
    for m, p in d.conditional_margin_probs:
        if m == d.actual_margin:
            actual_base = math.log(max(EPS, p)) + beta * s * m
            break
    if actual_base is None:
        raise DevelopmentError('actual margin missing from conditional')
    return -(actual_base - lz)

def mean_derivative(data: list[FitDatum], beta: float, *, sign_only: bool=False) -> float:
    if not data:
        raise DevelopmentError('empty fit data')
    acc = 0.0
    for d in data:
        s = _signal(d, sign_only)
        if s == 0:
            continue
        exps = []
        shift = max((beta * s * m for m, _ in d.conditional_margin_probs))
        for m, p in d.conditional_margin_probs:
            exps.append((m, p * math.exp(beta * s * m - shift)))
        z = math.fsum((w for _, w in exps))
        mean_m = math.fsum((m * w for m, w in exps)) / z
        acc += s * (mean_m - d.actual_margin)
    return acc / len(data)

def fit_beta(data: list[FitDatum], *, sign_only: bool=False, lo: float=-1.0, hi: float=1.0, tol: float=1e-12, max_iter: int=200, boundary_eps: float=1e-09) -> dict[str, Any]:
    if not data or not lo < hi or tol <= 0 or (max_iter <= 0):
        raise DevelopmentError('invalid optimizer input')
    dlo = mean_derivative(data, lo, sign_only=sign_only)
    dhi = mean_derivative(data, hi, sign_only=sign_only)
    if dlo >= 0:
        beta = lo
        boundary = True
        it = 0
    elif dhi <= 0:
        beta = hi
        boundary = True
        it = 0
    else:
        boundary = False
        beta = (lo + hi) / 2
        it = 0
        for it in range(1, max_iter + 1):
            beta = (lo + hi) / 2
            d = mean_derivative(data, beta, sign_only=sign_only)
            if abs(d) <= tol or hi - lo <= tol:
                break
            if d < 0:
                lo = beta
            else:
                hi = beta
        boundary = abs(beta + 1.0) <= boundary_eps or abs(beta - 1.0) <= boundary_eps
    base = math.fsum((d.baseline_conditional_nll for d in data)) / len(data)
    cand = math.fsum((datum_nll(d, beta, sign_only=sign_only) for d in data)) / len(data)
    return {'beta': beta, 'boundary_hit': boundary, 'iterations': it, 'derivative_lo': dlo, 'derivative_hi': dhi, 'baseline_mean_nll': base, 'candidate_mean_nll': cand, 'delta': cand - base}

def matrix_metrics(matrix: dict[tuple[int, int], float], hg: int, ag: int) -> dict[str, float]:
    actual = (hg, ag)
    p_actual = matrix.get(actual)
    if p_actual is None or p_actual <= 0:
        raise DevelopmentError('actual score probability unavailable')
    t = hg + ag
    pt = math.fsum((p for k, p in matrix.items() if sum(k) == t))
    cond = p_actual / pt
    ph = math.fsum((p for (h, a), p in matrix.items() if h > a))
    pd = math.fsum((p for (h, a), p in matrix.items() if h == a))
    pa = math.fsum((p for (h, a), p in matrix.items() if h < a))
    probs = (ph, pd, pa)
    y = 0 if hg > ag else 1 if hg == ag else 2
    ll = -math.log(max(EPS, probs[y]))
    brier = sum(((p - (1.0 if i == y else 0.0)) ** 2 for i, p in enumerate(probs)))
    rps = ((probs[0] - (1.0 if y == 0 else 0.0)) ** 2 + (probs[0] + probs[1] - (1.0 if y <= 1 else 0.0)) ** 2) / 2
    yd = 1.0 if y == 1 else 0.0
    draw_ll = -(yd * math.log(max(EPS, pd)) + (1 - yd) * math.log(max(EPS, 1 - pd)))
    entropy = -math.fsum((p * math.log(max(EPS, p)) for p in matrix.values()))
    return {'conditional_nll': -math.log(max(EPS, cond)), 'joint_nll': -math.log(max(EPS, p_actual)), '1x2_logloss': ll, '1x2_brier': brier, 'rps': rps, 'draw_logloss': draw_ll, 'entropy': entropy, 'top_cell': max(matrix.values()), 'p_home': ph, 'p_draw': pd, 'p_away': pa, 'pt': pt}

def aggregate_metric(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    keys = ('conditional_nll', 'joint_nll', '1x2_logloss', '1x2_brier', 'rps', 'draw_logloss', 'entropy', 'top_cell')
    return {k: math.fsum((float(r[prefix][k]) for r in rows)) / len(rows) for k in keys}

def run_replay(db_path: Path) -> dict[str, Any]:
    from historical_xg_challenger_v1 import historical_xg_challenger as hxg
    from new_engine_v1 import formal_fusion_v2 as ff
    rows, counts = load_rows(db_path)
    state = ff.new_candidate_state()
    fit_rows: list[FitDatum] = []

    def fixtures(batch: list[SourceRow]):
        return [hxg.FixtureRow(r.fixture_id, r.competition_id, r.season_text, r.kickoff, r.home_team_id, r.away_team_id, r.home_name, r.away_name) for r in batch]

    def predict_components(batch: list[SourceRow]):
        fs = fixtures(batch)
        xg_preds, v1_preds = state.predict_batch(fs, include_matrix=True)
        if len(xg_preds) != len(batch) or len(v1_preds) != len(batch):
            raise DevelopmentError('component prediction length mismatch')
        return (fs, xg_preds, v1_preds)

    def release(batch: list[SourceRow], fs):
        labels = {r.fixture_id: hxg.ReleasedLabel(r.home_goals, r.away_goals, r.home_xg, r.away_xg, r.kickoff + timedelta(hours=3)) for r in batch}
        ff.apply_completed_xg_batch(state, fs, labels, batch[0].kickoff + timedelta(hours=3))
    pre = [r for r in rows if r.season in WARMUP_SEASONS + FIT_SEASONS]
    for batch in kickoff_batches(pre):
        fs, xgs, v1s = predict_components(batch)
        if batch[0].season in FIT_SEASONS:
            for r, x, v in zip(batch, xgs, v1s):
                if bool(x.get('dynamic', {}).get('fallback_exact_v1')):
                    if _normalized_map(x['score_matrix']) != _normalized_map(v['score_matrix']):
                        raise DevelopmentError('fallback component matrix not exact V1')
                fit_rows.append(fit_datum(v['score_matrix'], x['score_matrix'], r.home_goals, r.away_goals, r.fixture_id, r.competition_id))
        release(batch, fs)
    if len(fit_rows) != EXPECTED_COUNTS['fit']:
        raise DevelopmentError('fit row count mismatch')
    full_fit = fit_beta(fit_rows, sign_only=False)
    sign_fit = fit_beta(fit_rows, sign_only=True)
    beta = float(full_fit['beta'])
    beta_sign = float(sign_fit['beta'])
    scored: list[dict[str, Any]] = []
    max_pt_err = 0.0
    beta_zero_err = 0.0
    for batch in kickoff_batches([r for r in rows if r.season in EVAL_SEASONS]):
        fs, xgs, v1s = predict_components(batch)
        for r, x, v in zip(batch, xgs, v1s):
            base = _mix(v['score_matrix'], x['score_matrix'])
            c0 = tilt(v['score_matrix'], x['score_matrix'], 0.0)
            beta_zero_err = max(beta_zero_err, max((abs(base[k] - c0[k]) for k in base)))
            cand = tilt(v['score_matrix'], x['score_matrix'], beta)
            sign = tilt(v['score_matrix'], x['score_matrix'], beta_sign, sign_only=True)
            for t in {sum(k) for k in base}:
                b = math.fsum((p for k, p in base.items() if sum(k) == t))
                c = math.fsum((p for k, p in cand.items() if sum(k) == t))
                max_pt_err = max(max_pt_err, abs(b - c))
            bm = matrix_metrics(base, r.home_goals, r.away_goals)
            cm = matrix_metrics(cand, r.home_goals, r.away_goals)
            sm = matrix_metrics(sign, r.home_goals, r.away_goals)
            scored.append({'fixture_id': r.fixture_id, 'competition_id': r.competition_id, 'baseline': bm, 'candidate': cm, 'sign_only': sm, 'primary_delta': cm['conditional_nll'] - bm['conditional_nll']})
        release(batch, fs)
    if len(scored) != EXPECTED_COUNTS['eval']:
        raise DevelopmentError('development eval count mismatch')
    if max_pt_err > 5e-12 or beta_zero_err > 1e-15:
        raise DevelopmentError('matrix engineering invariant failure')
    base_agg = aggregate_metric(scored, 'baseline')
    cand_agg = aggregate_metric(scored, 'candidate')
    sign_agg = aggregate_metric(scored, 'sign_only')
    deltas = {k: cand_agg[k] - base_agg[k] for k in base_agg}
    sign_deltas = {k: sign_agg[k] - base_agg[k] for k in base_agg}
    primary = [float(r['primary_delta']) for r in scored]
    sigma = statistics.stdev(primary)
    required_n = max(1000, math.ceil(((1.959964 + 0.841621) * sigma / 0.003) ** 2))
    by_comp = {}
    for cid in sorted({r['competition_id'] for r in scored}):
        x = [r for r in scored if r['competition_id'] == cid]
        ba = aggregate_metric(x, 'baseline')
        ca = aggregate_metric(x, 'candidate')
        by_comp[cid] = {'n': len(x), 'conditional_nll_delta': ca['conditional_nll'] - ba['conditional_nll']}
    guard = deltas['1x2_logloss'] <= 0.001 and deltas['1x2_brier'] <= 0.001 and (deltas['rps'] <= 0.001)
    if bool(full_fit['boundary_hit']):
        status = 'STOP_BETA_BOUNDARY_HIT'
    elif deltas['conditional_nll'] >= 0:
        status = 'STOP_DEVELOPMENT_NO_GAIN'
    elif not guard:
        status = 'STOP_DEVELOPMENT_GUARDRAIL'
    else:
        status = 'DEVELOPMENT_PASS_FREEZE_REQUIRED_N'
    identities = [(r.fixture_id, r.competition_id, r.season_text, r.kickoff.isoformat(), r.home_team_id, r.away_team_id) for r in rows]
    return {'schema_version': 'football3-v3-c3-score-matrix-development-receipt-v1', 'status': status, 'source': {'database_sha256': EXPECTED_DB_SHA256, 'classification': 'POST_VIEW_DEVELOPMENT_ONLY', 'warmup_n': counts['warmup'], 'fit_n': counts['fit'], 'development_eval_n': counts['eval'], 'max_source_season': max((r.season for r in rows)), 'identity_sha256': canonical_sha256(identities)}, 'optimizer': {'full_magnitude': full_fit, 'sign_only_ablation': sign_fit, 'bounds': [-1.0, 1.0], 'tolerance': 1e-12, 'max_iterations': 200, 'refit_on_eval': False}, 'development_eval': {'baseline': base_agg, 'candidate': cand_agg, 'candidate_delta': deltas, 'sign_only': sign_agg, 'sign_only_delta': sign_deltas, 'paired_primary_sigma_dev': sigma, 'required_n': required_n, 'by_competition': by_comp}, 'engineering': {'max_pt_abs_error': max_pt_err, 'beta_zero_max_abs_error': beta_zero_err, 'support_unchanged': True, 'fresh_confirmation_consumed': False, 'formal_changed': False}, 'inactive': {'candidate_status': 'NOT_AVAILABLE', 'weight': 0, 'matrix_delta': 0, 'data_ready': False}, 'authorization': {'post_view_labels_read': True, 'fresh_labels_read': False, 'fresh_confirmation_enrolled': False, 'ready_or_merge': False}}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', required=True)
    ap.add_argument('--source-db', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    contract = load_contract(Path(args.contract))
    receipt = run_replay(Path(args.source_db))
    receipt['contract_sha256'] = sha256_file(Path(args.contract))
    receipt['exact_base'] = contract['exact_base']
    receipt['branch'] = contract['branch']
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'development_receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    summary = {'status': receipt['status'], 'beta': receipt['optimizer']['full_magnitude']['beta'], 'primary_delta': receipt['development_eval']['candidate_delta']['conditional_nll'], 'sigma_dev': receipt['development_eval']['paired_primary_sigma_dev'], 'required_n': receipt['development_eval']['required_n']}
    (out / 'development_summary.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, sort_keys=True))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
