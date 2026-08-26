# Football3 Batch-001 preregistration

status: PREREG_LOCKED
created_at: 2026-08-26 Asia/Taipei
base_main_sha: 8568f0b1652d6d3752ae10e62917eebc5249a0e2
purpose: strict retrospective pseudo-prospective 1X2 replay; target 100 matches

## Selection universe
- Season: 2024/2025.
- Competitions, fixed order: E0 (England Premier League), SP1 (Spain La Liga), I1 (Italy Serie A), D1 (Germany Bundesliga), F1 (France Ligue 1).
- Source family: football-data.co.uk season 2425 CSVs for the five fixed competition codes above.
- Selection is independent of match result, score, xG, in-play events, post-match statistics, post-match reporting, or any model output.

## Zero-label selection rule
1. At selection time read only identity/time columns required to form the match list: Div, Date, Time, HomeTeam, AwayTeam. Do not read/export FTHG, FTAG, FTR or any other target/result field.
2. Keep rows with parseable Date and non-empty HomeTeam/AwayTeam.
3. Preserve each source CSV's original zero-based row position as source_row_index.
4. Sort ascending by: parsed Date; fixed competition order E0, SP1, I1, D1, F1; source_row_index. Time is retained as metadata but is not used to break cross-source ordering, so missing/format differences cannot alter eligibility.
5. Select the first 100 rows exactly. No replacement, no outcome-based filtering, no manual exclusions.
6. Assign batch_index 001..100 after sorting.
7. The immutable lock must contain only identity/time/source fields plus deterministic hashes; no outcome/label fields.

## Prediction boundary
- Default information cutoff for each match: T-24h relative to scheduled kickoff when a reliable kickoff time is recoverable; otherwise a separately logged conservative cutoff must be used before feature retrieval.
- Before all 100 predictions are locked, target-match final scores, result labels, in-play events, post-match xG/statistics and post-match reporting are forbidden from prediction inputs and review outputs.
- Human judgment cannot directly add/subtract probabilities; extra information must enter through a defined feature/model interface.

## Required outputs before reveal
- Immutable 100-match identity lock with SHA256.
- Per-match S60 baseline prediction and candidate predictions where supported.
- H/D/A probabilities, Top1, model/input provenance and information-cutoff evidence.
- Only after all 100 predictions are immutably locked may result labels be joined for scoring.

## Primary evaluation after reveal
Top1 accuracy, H/D/A class accuracy, draw Top1 count/accuracy, LogLoss, Brier, RPS, high-confidence subset accuracy and coverage.
