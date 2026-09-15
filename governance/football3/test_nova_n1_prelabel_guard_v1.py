#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import nova_n1_prelabel_guard_v1 as g

def check(name, fn):
    fn()
    print(f"PASS {name}")

def fail_if(condition):
    if condition:
        raise AssertionError

def main():
    here = Path(__file__).resolve().parent
    contract = here / "nova_n1_preregistration_v1.json"
    c = g.validate_contract(contract)
    check("status_locked", lambda: fail_if(c["status"] != "DESIGN_LOCKED_PRELABEL"))
    check("legacy_half_life_not_inherited", lambda: fail_if(c["legacy_signal"]["inherit_old_half_life_16"]))
    check("old_signal_domain_excluded", lambda: fail_if(c["chronology"]["legacy_signal_season_keys_excluded"] != [2020, 2021, 2022]))
    check("fit_2014_2017", lambda: fail_if(c["chronology"]["fit_season_keys"] != [2014, 2015, 2016, 2017]))
    check("dev_2018_2019", lambda: fail_if(c["chronology"]["development_selection_season_keys"] != [2018, 2019]))
    check("test_2024_single_use", lambda: fail_if(not c["chronology"]["independent_test_single_use"]))
    check("max_12_configs", lambda: fail_if(c["max_candidate_configurations"] != 12))
    check("l2_fixed", lambda: fail_if(c["model"]["regularization_strength"] != 1.0))
    check("single_matrix_projection", lambda: fail_if("p_v2(h,a)" not in c["model"]["unified_matrix_projection"]))
    check("j1_k1_inactive", lambda: fail_if(set(c["coverage"]["unsupported"]) != {"JPN_J1", "KOR_KLeague1"}))
    check("promotion_not_authorized", lambda: fail_if(not c["promotion_not_authorized"]))
    check("test_labels_locked", lambda: fail_if(not c["data_lock"]["independent_test_identity"]["test_labels_locked_until_predictions_frozen"]))
    print("N1_PRELABEL_TESTS_PASS=12/12")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
