#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path

H = Path(__file__).resolve().parent
S = importlib.util.spec_from_file_location("v", H / "validate_v3_schedule_importance_sanitized_schema_v1.py")
v = importlib.util.module_from_spec(S)
S.loader.exec_module(v)
C = json.loads((H / "v3_schedule_importance_sanitized_schema_contract_v1.json").read_text())

def payload(match_fragment: str):
    return ('{"name":"Synthetic League","matches":[' + match_fragment + ']}').encode()

def safe_match(score='"score":{"ht":[9,8],"ft":[7,6]}', extra=""):
    return (
        '{"round":"Matchday 1","date":"2020-01-02","time":"20:45",'
        '"team1":"Alpha FC","team2":"Beta FC",' + score + extra + '}'
    )

def expect_stop(raw, code):
    try:
        v.sanitize(raw, "2020-21/en.1.json", C["sanitizer_contract"]["allowed_output_keys"])
        assert False, f"expected {code}"
    except v.Stop as e:
        assert str(e) == code, (str(e), code)

def t_score_is_opaque_and_absent():
    raw = payload(safe_match())
    rows, st = v.sanitize(raw, "2020-21/en.1.json", C["sanitizer_contract"]["allowed_output_keys"])
    assert len(rows) == 1
    assert rows[0]["team1"] == "Alpha FC"
    assert st["score"] == 1 and st["opaque"] == 1
    assert True
    out = json.dumps(rows)
    assert '"score"' not in out and '"ht"' not in out and '"ft"' not in out
    assert "9" not in out and "8" not in out and "7" not in out and "6" not in out

def t_escaped_score_key_is_stripped():
    raw = payload(safe_match(score='"sc\\u006fre":{"ft":[987654321,123456789]}'))
    rows, st = v.sanitize(raw, "2020-21/en.1.json", C["sanitizer_contract"]["allowed_output_keys"])
    assert len(rows) == 1 and st["score"] == 1
    out = json.dumps(rows)
    assert "987654321" not in out and "123456789" not in out

def t_null_score_is_not_completed():
    raw = payload(safe_match(score='"score":null'))
    rows, st = v.sanitize(raw, "2020-21/en.1.json", C["sanitizer_contract"]["allowed_output_keys"])
    assert rows == []
    assert st["drop"] == 1 and st["done"] == 0

def t_unknown_result_like_fields_never_emit():
    raw = payload(safe_match(extra=',"winner":"Alpha FC","goals":[99,88],"penalties":{"ft":[5,4]}'))
    rows, _ = v.sanitize(raw, "2020-21/en.1.json", C["sanitizer_contract"]["allowed_output_keys"])
    assert len(rows) == 1
    assert set(rows[0]) == set(C["sanitizer_contract"]["allowed_output_keys"])
    text = json.dumps(rows)
    for k in C["sanitizer_contract"]["forbidden_output_keys"]:
        assert f'"{k}"' not in text

def t_required_date_missing_stops():
    raw = payload('{"round":"R1","time":"20:00","team1":"A","team2":"B","score":{"ft":[1,0]}}')
    expect_stop(raw, "STOP_SAFE_FIELD")

def t_round_missing_stops():
    raw = payload('{"date":"2020-01-02","time":"20:00","team1":"A","team2":"B","score":{"ft":[1,0]}}')
    expect_stop(raw, "STOP_ROUND")

def t_bad_time_stops():
    raw = payload('{"round":"R1","date":"2020-01-02","time":"25:00","team1":"A","team2":"B","score":{"ft":[1,0]}}')
    expect_stop(raw, "STOP_TIME")

def t_duplicate_fixture_stops():
    m = safe_match()
    raw = payload(m + "," + m)
    expect_stop(raw, "STOP_DUP_FIXTURE")

def t_normalized_team_collision_stops():
    m1 = safe_match()
    m2 = (
        '{"round":"Matchday 2","date":"2020-01-03","time":"20:45",'
        '"team1":"Alpha-FC","team2":"Gamma FC","score":{"ft":[1,1]}}'
    )
    raw = payload(m1 + "," + m2)
    expect_stop(raw, "STOP_IDENTITY_COLLISION")

def t_contract_parent_and_completed_only_guards():
    assert C["parent_zero_label_evidence"]["selected_file_count"] == 94
    assert C["parent_zero_label_evidence"]["inventory_sha256"] == "7449306437db6f974f2209ce2389ba235a293f5334139390c4e2714929e0b72f"
    assert C["inventory_rules"]["excluded_path"] == "2023-24/en.1.json"
    assert C["hard_zero_guards"]["future_matches_allowed"] is False
    assert C["hard_zero_guards"]["training"] is False and C["hard_zero_guards"]["tuning"] is False
    assert C["pit_contract"]["standings_pressure"].startswith("DEFERRED")
    assert C["pit_contract"]["travel_pressure"] == "NOT_IN_THIS_BATCH"
    assert C["pit_contract"]["rotation_pressure"] == "NOT_IN_THIS_BATCH"

T = [
    t_score_is_opaque_and_absent,
    t_escaped_score_key_is_stripped,
    t_null_score_is_not_completed,
    t_unknown_result_like_fields_never_emit,
    t_required_date_missing_stops,
    t_round_missing_stops,
    t_bad_time_stops,
    t_duplicate_fixture_stops,
    t_normalized_team_collision_stops,
    t_contract_parent_and_completed_only_guards,
]

if __name__ == "__main__":
    for f in T:
        f()
        print("PASS", f.__name__)
    print(f"{len(T)}/{len(T)} PASS")
