# C070-F NEW-DATA / FRESH-4000 SOURCE CONTRACT

Status: FROZEN_BEFORE_MATCH_EVENT_TARGET_ACCESS
Date: 2026-08-18 Asia/Taipei
Formal weight: 0
Parent scientific mechanism: C070-C score-state-duration / Semi-Markov transition signal
Parent fresh attempt: C070-E A04 STOP_CALIBRATION_COVERAGE (13 matched calibration pairs < frozen 25; alpha not fit; confirmation not scored)

## Purpose
Acquire a substantially larger, genuinely new retrospective data universe for a future C070-F warmup -> calibration -> confirmation test. This step is DATA ACQUISITION / ZERO-LABEL IDENTITY COVERAGE ONLY. It does not fit alpha, score confirmation, train a prediction model, or change any formal asset.

## Fixed source
Hugging Face dataset: `julien-c/kaggle-hugomathien-soccer`
Pinned revision: `80e14cc7aa624cc266470f43a626652dabdfb80a`
Primary file: `database.sqlite`
Dataset license metadata: ODbL.
Source describes >25,000 matches and detailed match-event fields for >10,000 matches.

## 4000-match identity rule
1. Download the pinned `database.sqlite` without querying target outcomes first.
2. Query only Match identity/metadata columns needed for identity and time ordering: `id`, `match_api_id`, `country_id`, `league_id`, `season`, `stage`, `date`, `home_team_api_id`, `away_team_api_id` plus event-payload PRESENCE flags only (NULL/non-NULL), never event contents or score/result columns.
3. Eligible identity rows must have a non-null goal-event payload and valid date/home/away identity. Payload content must remain unopened at this stage.
4. Sort eligible rows by SHA-256 of canonical identity string `source|match_api_id|date|home_team_api_id|away_team_api_id|C070F_4000_20260818` and select the first exactly 4000 rows.
5. Selection may not use goals, winner, score, goal count, draw status, one-goal-win status, market value, C070 feature values, or any model output.
6. Persist exact 4000 identity manifest and its SHA-256 before event-target parsing.

## Future split rule (not executed in this download step)
After the 4000 identities are frozen, sort the frozen identities chronologically using metadata-only date. The future C070-F scientific contract must reserve:
- Warmup: first 1200 matches
- Calibration: next 1200 matches
- Confirmation: final 1600 matches

Exact date boundaries and identity SHA for all three blocks must be persisted before any C070-F event-target/model-effect evaluation. If chronological ties cross a boundary, move the entire UTC calendar-day tie group to the earlier block and reduce the later block; never split a calendar day across blocks. If this makes calibration <1000 or confirmation <1400 raw matches, STOP_DATA_COVERAGE and do not open event targets.

## Hard prohibitions
- Do not use A04 labels to tune C070-F.
- Do not lower the prior 25 matched-pair floor after seeing outcomes.
- Do not alter C070-C duration mechanism based on these 4000 outcomes before confirmation.
- Do not fit alpha or any transport parameter in warmup or confirmation.
- Do not use confirmation for feature/threshold/model selection.
- Do not touch A05/protected packages.
- Do not modify main, CURRENT, formal model/data/config, or formal_weight.

## This step passes only if
- pinned source download succeeds and SHA/size are recorded;
- >=4000 eligible identity rows exist using metadata + payload-presence only;
- exactly 4000 identities are selected by the frozen hash rule;
- exact identity manifest + hashes are persisted;
- target/event payload contents accessed = 0;
- model fits/scoring/tuning = 0.

Any failure is DATA/COVERAGE/ENGINEERING only, not an efficacy result for C070-C/D/E/F.
