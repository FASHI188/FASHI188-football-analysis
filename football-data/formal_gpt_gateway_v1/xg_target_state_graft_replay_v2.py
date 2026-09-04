#!/usr/bin/env python3
from __future__ import annotations

import xg_trigger_diagnostic_fix_v2 as diagnostic_fix
import xg_target_state_graft_replay_v1 as replay


# Target-only replay path: install the metadata-bearing XG trigger diagnostic here,
# never through the generic production gateway entry.
diagnostic_fix.install()


class _ReceiptAlias(dict):
    def __missing__(self, key):
        if key == "formal_current_sha256":
            return self["current_sha256"]
        raise KeyError(key)


_original_predict_match = replay.rt.predict_match


def _predict_match_with_summary_alias(*args, **kwargs):
    # Keep the serialized prediction receipt byte/schema identical to the frozen runtime
    # contract. The alias exists only for the repair script's in-memory summary lookup.
    return _ReceiptAlias(_original_predict_match(*args, **kwargs))


replay.rt.predict_match = _predict_match_with_summary_alias

if __name__ == "__main__":
    raise SystemExit(replay.main())
