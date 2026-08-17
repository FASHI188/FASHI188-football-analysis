#!/usr/bin/env python3
from pathlib import Path

# Mechanical B02 -> B03 adaptation only.  The B02 settlement implementation is the
# already-settled scientific contract; B03 changes only sealed-batch identity/hashes
# and the target-only governance field for the still-unqueried B04 batch.
source_path = Path(__file__).with_name('settle_fabulous_ou25_b02_market_direct_t_r1.py')
src = source_path.read_text(encoding='utf-8')

required = [
    "EXPECTED_B02_MANIFEST_SHA = '5f642afc51175ae693d45cb0393a78b3b2d5bd8bc8fb5b1a67567f21a3a906e2'",
    "EXPECTED_PACKET_SHA = 'b5c8019a06fa0b75ba9b205a552beabc1e84e7877e1095d86c2e8125bfc2d6ed'",
    "EXPECTED_PRED_SHA = '9b771c9466842e97fe8984945cc9b2c9f26d6531bab27f7f871305b6e2c8c008'",
    "EXPECTED_LOCK_SHA = '0dd9d39157578b2f3c0d78b66df6bfff5896c6d1a729609bc2ab34cd0d5e45ab'",
    "BOOTSTRAP_SEED = 20260817",
    "BOOTSTRAP_REPS = 20000",
    "assert receipt['B03_B04_fixture_ids_requested'] == 0",
    "'B03_B04_fixture_ids_requested': 0,",
    "'B03_B04_state_after_run': 'UNOPENED_BY_TARGET_ONLY_QUERY',",
]
for needle in required:
    if needle not in src:
        raise SystemExit(f'B02 contract drift; missing expected source fragment: {needle}')

out = src.replace('B02', 'B03').replace('b02', 'b03')
out = out.replace(
    "EXPECTED_B03_MANIFEST_SHA = '5f642afc51175ae693d45cb0393a78b3b2d5bd8bc8fb5b1a67567f21a3a906e2'",
    "EXPECTED_B03_MANIFEST_SHA = '9c82bdc7ea139021f0bef444a2b7cf9c55d9793704ff5d0eeaa3008737295cf9'",
)
out = out.replace(
    "EXPECTED_PACKET_SHA = 'b5c8019a06fa0b75ba9b205a552beabc1e84e7877e1095d86c2e8125bfc2d6ed'",
    "EXPECTED_PACKET_SHA = '7c1437b3a20df9ecb9dc0422988558d2cda93c167113c552cd7e4c760bed0ffc'",
)
out = out.replace(
    "EXPECTED_PRED_SHA = '9b771c9466842e97fe8984945cc9b2c9f26d6531bab27f7f871305b6e2c8c008'",
    "EXPECTED_PRED_SHA = 'f6dd9831ff0d70f6cd56e84a0d643f56422e67bb963fb1bb51b1d06308c50655'",
)
out = out.replace(
    "EXPECTED_LOCK_SHA = '0dd9d39157578b2f3c0d78b66df6bfff5896c6d1a729609bc2ab34cd0d5e45ab'",
    "EXPECTED_LOCK_SHA = '8bf9af77c4877a2dacc3403d413dc2723ba96183f0c33d012fb79f9916107c3a'",
)
# B03 itself is now the target, so the untouched-target field refers only to B04.
out = out.replace("receipt['B03_B04_fixture_ids_requested']", "receipt['B04_fixture_ids_requested']")
out = out.replace("'B03_B04_fixture_ids_requested': 0,", "'B04_fixture_ids_requested': 0,")
out = out.replace("'B03_B04_state_after_run': 'UNOPENED_BY_TARGET_ONLY_QUERY',", "'B04_state_after_run': 'UNOPENED_BY_TARGET_ONLY_QUERY',")
out = out.replace('max 40 requested IDs per query', 'max 10 requested IDs per query')

# Guard the scientific contract after adaptation.
assert "BOOTSTRAP_SEED = 20260817" in out
assert "BOOTSTRAP_REPS = 20000" in out
assert "assert lock['model']['selected_C'] == 0.1" in out
assert "assert lock['science_gate']['PASS'] == 'point delta < 0 AND calendar-date/bootstrap90 upper bound < 0'" in out
assert "'locked_rule': 'PASS iff LL point delta < 0 AND calendar-date block-bootstrap90 upper < 0 AND Brier point delta <= 0 AND RPS point delta <= 0'" in out

exec(compile(out, str(Path(__file__).resolve()), 'exec'), {'__name__': '__main__', '__file__': str(Path(__file__).resolve())})
