from __future__ import annotations

import hashlib
from source_ingest import fact_from_mapping, PITViolation
from identity_registry import IdentityRegistry, IdentityError


def _prov(known_at:str)->dict:
    return {"source_url":"https://example.invalid/fact","raw_sha256":hashlib.sha256(b"x").hexdigest(),"published_at":known_at,"observed_at":known_at,
            "retrieved_at":"2026-08-31T00:00:00+00:00","known_at":known_at,"source_tier":"TIER_1_OFFICIAL","extraction_confidence":1.0,
            "provider_license":"public","immutable_source_ref":"fixture-test"}

def run_probes()->dict:
    cutoff="2026-08-31T12:00:00+00:00"; passed=[]
    attacks=[
        {"predicate":"injury","entity_type":"player","entity_id":"p1","value":{"status":"out","__import__":"os.system('id')"},"provenance":_prov("2026-08-31T10:00:00+00:00")},
        {"predicate":"injury","entity_type":"player","entity_id":"p1","value":{"home_goals":9},"provenance":_prov("2026-08-31T10:00:00+00:00")},
        {"predicate":"injury","entity_type":"player","entity_id":"p1","value":{"status":"out"},"provenance":_prov("2026-08-31T13:00:00+00:00")},
        {"predicate":"unapproved_numeric_bonus","entity_type":"team","entity_id":"t1","value":1.5,"provenance":_prov("2026-08-31T10:00:00+00:00")},
    ]
    fact=fact_from_mapping(attacks[0],cutoff); passed.append(fact.value["__import__"]=="os.system('id')")
    for bad in attacks[1:]:
        try: fact_from_mapping(bad,cutoff); passed.append(False)
        except PITViolation: passed.append(True)
    reg=IdentityRegistry(); reg.register("player","p1","Alex Smith"); reg.register("player","p2","Alex Smith")
    try: reg.resolve("player","Alex Smith"); passed.append(False)
    except IdentityError: passed.append(True)
    return {"passed":all(passed),"checks":len(passed),"results":passed}

if __name__=="__main__":
    import json; print(json.dumps(run_probes(),indent=2))
