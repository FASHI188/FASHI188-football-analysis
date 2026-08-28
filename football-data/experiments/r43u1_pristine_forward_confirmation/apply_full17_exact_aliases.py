#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'config' / 'v6_full17_identity_registry_v6482.json'
ALIASES = ROOT / 'config' / 'v6_full17_provider_aliases_v6483.json'

TRANSLATE = str.maketrans({
    'ø':'o','Ø':'o','ł':'l','Ł':'l','đ':'d','Đ':'d','ð':'d','Ð':'d',
    'þ':'th','Þ':'th','æ':'ae','Æ':'ae','œ':'oe','Œ':'oe'
})


def norm(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value or '').translate(TRANSLATE)).casefold()
    out=[]
    for ch in text:
        if unicodedata.combining(ch):
            continue
        out.append(ch if ch.isalnum() else ' ')
    return ' '.join(''.join(out).split())


def canonical_digest(reg: dict) -> str:
    rows=[]
    for cid, comp in sorted((reg.get('competitions') or {}).items()):
        for team in comp.get('teams') or []:
            rows.append((cid, str(team.get('canonical_name') or ''), str(team.get('normalized_identity') or '')))
    raw=json.dumps(rows, ensure_ascii=False, separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    reg=json.loads(REGISTRY.read_text(encoding='utf-8'))
    cfg_raw=ALIASES.read_bytes()
    cfg=json.loads(cfg_raw.decode('utf-8'))
    if reg.get('status') != 'PASS_ALL_17' or int(reg.get('available_competition_count') or 0) != 17:
        raise RuntimeError('source frozen registry is not PASS_ALL_17')
    before=canonical_digest(reg)
    added=[]
    already=[]
    for cid, rows in (cfg.get('aliases') or {}).items():
        comp=(reg.get('competitions') or {}).get(cid)
        if not isinstance(comp, dict) or comp.get('status') != 'PASS':
            raise RuntimeError(f'competition not PASS:{cid}')
        teams=comp.get('teams') or []
        by_name={str(t.get('canonical_name') or ''): t for t in teams}
        canonical_tokens={str(t.get('normalized_identity') or norm(t.get('canonical_name'))): str(t.get('canonical_name') or '') for t in teams}
        alias_owner={}
        for t in teams:
            owner=str(t.get('canonical_name') or '')
            for tok in t.get('provider_alias_tokens') or []:
                tok=str(tok)
                prev=alias_owner.get(tok)
                if prev is not None and prev != owner:
                    raise RuntimeError(f'existing alias collision:{cid}:{tok}:{prev}/{owner}')
                alias_owner[tok]=owner
        for source, target in rows.items():
            source=str(source); target=str(target); stok=norm(source)
            if target not in by_name:
                raise RuntimeError(f'alias target absent from frozen current domain:{cid}:{source}->{target}')
            canonical_owner=canonical_tokens.get(stok)
            if canonical_owner is not None and canonical_owner != target:
                raise RuntimeError(f'alias collides with canonical identity:{cid}:{source}:{canonical_owner}/{target}')
            prev=alias_owner.get(stok)
            if prev is not None and prev != target:
                raise RuntimeError(f'alias collision:{cid}:{source}:{prev}/{target}')
            team=by_name[target]
            tokens={str(x) for x in (team.get('provider_alias_tokens') or [])}
            if stok in tokens:
                already.append({'competition_id':cid,'source':source,'target':target,'token':stok})
                continue
            tokens.add(stok)
            team['provider_alias_tokens']=sorted(tokens)
            alias_owner[stok]=target
            added.append({'competition_id':cid,'source':source,'target':target,'token':stok})
        comp['provider_alias_count']=sum(len(t.get('provider_alias_tokens') or []) for t in teams)
        if comp.get('alias_errors'):
            raise RuntimeError(f'preexisting alias errors:{cid}:{comp.get("alias_errors")}')
    after=canonical_digest(reg)
    if before != after:
        raise RuntimeError('canonical team set changed during alias-only patch')
    reg['exact_alias_registry_path']=str(ALIASES.relative_to(ROOT))
    reg['exact_alias_registry_sha256']=hashlib.sha256(cfg_raw).hexdigest()
    reg['alias_patch_applied_at_utc']=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    reg['alias_patch_policy']='Exact same-domain aliases only; canonical team set immutable; no fuzzy matching.'
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({
        'status':'PASS',
        'available_competition_count':reg.get('available_competition_count'),
        'canonical_digest_before':before,
        'canonical_digest_after':after,
        'added_count':len(added),
        'already_count':len(already),
        'added':added,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
