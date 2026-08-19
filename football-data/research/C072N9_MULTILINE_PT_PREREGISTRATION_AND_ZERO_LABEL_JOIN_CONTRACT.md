# C072-N9 — Multi-line P(T) preregistration + zero-label outcome-source identity join

## Lineage / purpose
- football3 only.
- Parent C072-N8 authoritative dataset is original run `32244931845`, CSV SHA256 `e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082`, ordered identity SHA256 `95ff10827e5097158c2bf20838e317c106d0b53c8ad6088a50fecae99b6ad0f4`, 18,768 rows.
- C072-N8 was adjudicated PASS offline after adding the actual Footiqo date representation `%d-%m-%y %H:%M`; all non-date audit values remained frozen.
- N8 multi-line quality: O/U2.5 coverage 99.9947%, joint 1.5/2.5/3.5 99.8881%, all-five-line coverage 95.4923%, all-five monotonicity 99.9944%, 11 seasons, duplicate rate 0.
- The unintended later N8 auto-rerun is quarantined and cannot be used.
- C073-C077 remain quarantined and are not ancestry/evidence/design input.

## Scientific question frozen BEFORE outcome labels
Does the **cross-threshold shape** of closing total-goals odds at O/U 0.5,1.5,2.5,3.5,4.5 improve complete match-level `P(T=0,1,2,3,4,5,6,7+)` beyond an otherwise identical model using only closing O/U2.5 information?

This directly targets the C072-L2 remaining bottleneck: P(T) resolution and the excessive concentration of total-goal Top1 calls around T=2. Proper scoring remains primary; no artificial T=2/Draw/1-1 suppression is allowed.

## Fixed zero-label market dataset
Use only the immutable N8 CSV from original run 32244931845. The five fixed leagues/source codes are EPL/LL/BL/SA/L1.

Market features are constructed only from two-sided valid Over/Under odds:
`q_L = (1/O_L)/((1/O_L)+(1/U_L))`, with clipping only for numerical logit safety to `[1e-6,1-1e-6]`.
`market_logit_L = log(q_L/(1-q_L))`.

### Baseline numeric market features
- `market_logit_25` only.

### Candidate numeric market features
- `market_logit_05`
- `market_logit_15`
- `market_logit_25`
- `market_logit_35`
- `market_logit_45`

No H/D/A, BTTS, team-name encoding, score-history feature, manual total-mode feature, handcrafted curvature term, or market movement is included in N9/N10. This experiment isolates the incremental value of the multi-line total-goal curve.

### Shared controls
- `sourceCode` league one-hot only.

Rows must have valid two-sided prices for all five lines for paired baseline/candidate evaluation. The same exact rows are used by both models.

## Frozen outcome-label source (labels CLOSED in N9)
Pinned public source: `nm2890/football-data` at revision `279978313f9c16a210fa80e8986fa22f0f866fba`.
Fixed files:
- `data/england/premier-league.csv`
- `data/spain/laliga.csv`
- `data/germany/bundesliga.csv`
- `data/italy/serie-a.csv`
- `data/france/ligue-1.csv`

N9 may materialize ONLY identity columns from these files:
- `Date`, `Season`, `HomeTeam`, `AwayTeam`, plus source file/code.

Forbidden in N9 identity audit:
- `FTHG`, `FTAG`, `FTR`, half-time scores/results, derived total goals, outcome/model score.

## Frozen identity join algorithm
### Canonicalization
Footiqo:
- `matchDate`: parse `%d-%m-%y %H:%M` or `%d-%m-%Y %H:%M`, reduce to calendar `YYYY-MM-DD`.
- `Season`: replace `/` with `-`.

Outcome source:
- `Date`: parse `%Y-%m-%d %H:%M:%S` or `%Y-%m-%d`, reduce to calendar `YYYY-MM-DD`.
- `Season`: unchanged.

Team canonical string for BOTH sources:
1. Unicode NFKD;
2. remove accents by ASCII transliteration;
3. lowercase;
4. remove every non `[a-z0-9]` character.

### Stage 1 exact-normalized join
Key: `(sourceCode, date, season, normalized_home, normalized_away)`.
Require exactly one outcome-source identity for an exact match.

### Stage 2 conservative same-day alias fallback
Only for Stage-1 unmatched rows, candidate outcome-source identities are restricted to the exact same:
- `sourceCode`
- calendar date
- season
- home/away orientation (no swapping).

String similarity = Python `difflib.SequenceMatcher(None, canonical_name_A, canonical_name_B).ratio()`.
For each candidate compute:
- `home_ratio`
- `away_ratio`
- `mean_ratio=(home_ratio+away_ratio)/2`
- `min_side=min(home_ratio,away_ratio)`.

Accept fallback only if ALL:
- `min_side >= 0.60`
- `mean_ratio >= 0.78`
- best mean-ratio minus second-best mean-ratio on that same date/season >= `0.12`
- exactly one candidate satisfies the best-score rule.

No manual alias dictionary may be added after seeing N9 results. No date +/-1 fallback, no home/away swap, no score/result assistance.

### Duplicate/ambiguity rule
Any Footiqo identity that maps to 0 or >1 outcome identities after the frozen join remains unmatched. No replacement row is pulled from another season/date.

## Label time boundaries frozen before labels
Outcome labels are not opened in N9.

If the zero-label join PASSes, N10 is permitted to materialize `FTHG` and `FTAG` only for the exact frozen joined identities under these time boundaries:
- Development/training history allowed: seasons 2015/16 through 2023/24.
- Five rolling OOS development test seasons: 2019/20, 2020/21, 2021/22, 2022/23, 2023/24; each fold trains only strictly earlier seasons.
- One-shot forward confirmation season: **2024/25**, completely label-sealed until the N10 development gate passes.
- Footiqo 2025/26 identities remain zero-label reserve and are excluded from N10 because the pinned outcome source does not provide that season.

## Frozen model family
Target: `T=min(FTHG+FTAG,7)` with 8 classes `[0,1,2,3,4,5,6,7+]`.

Both baseline and candidate use the identical pipeline:
- numeric market features: median imputation then StandardScaler;
- categorical `sourceCode`: OneHotEncoder(handle_unknown='ignore');
- ColumnTransformer;
- multinomial LogisticRegression, `C=0.1`, `max_iter=3000`, `class_weight=None`, `random_state=0`, default lbfgs multinomial behavior of installed sklearn;
- no hyperparameter/model/feature/subset search.

Baseline differs from candidate ONLY by having q2.5 logit instead of all five line logits.

## Frozen development metrics/gates
Primary: multiclass LogLoss candidate minus baseline on pooled rolling OOS.
Secondary proper scores: multiclass Brier and RPS.
Secondary decision metrics: Top1 accuracy, Top3 accuracy.
Diagnostics only (NOT a standalone PASS override):
- fraction of matches where Top1 total = 2 for baseline and candidate;
- entropy/top1 margin distributions;
- T-specific LogLoss and recall;
- per-league metrics;
- calibration/ECE diagnostics.

Paired bootstrap:
- unit = match;
- 3000 reps;
- seed = 72009;
- 90% CI for candidate-minus-baseline LogLoss.

`C072N10_MULTILINE_PT_DEVELOPMENT_PASS` requires ALL:
1. five frozen rolling folds satisfy coverage;
2. pooled dLogLoss < 0;
3. paired bootstrap90 upper dLogLoss < 0;
4. at least 4/5 chronological folds improve LogLoss;
5. pooled dBrier <= 0;
6. pooled dRPS <= 0;
7. pooled Top1 accuracy delta >= 0;
8. pooled Top3 accuracy delta >= 0;
9. at least 4/5 source leagues improve pooled LogLoss;
10. probability-conservation residual <=1e-12.

The T=2 Top1-call fraction is explicitly diagnostic: a development PASS does NOT require lowering it, and a lower fraction cannot rescue a proper-score failure.

If development FAILs: PARK this fixed multi-line representation. Do not tune C, add H/D/A/BTTS, create shape interactions, change folds, alter join thresholds, or subset leagues on viewed labels and call it the same experiment.

## Frozen 2024/25 one-shot confirmation gate
Only after development PASS may N10/N11 refit the exact frozen baseline/candidate on all joined 2015/16-2023/24 labels and open joined 2024/25 `FTHG/FTAG` once.

Confirmation PASS requires ALL:
1. joined confirmation rows >=1200 and >=200 per source league;
2. candidate dLogLoss < 0;
3. paired bootstrap5000 seed 72010 90% upper dLogLoss < 0;
4. dBrier <=0;
5. dRPS <=0;
6. Top1 accuracy delta >=0;
7. Top3 accuracy delta >=0;
8. at least 4/5 source leagues improve LogLoss;
9. probability conservation residual <=1e-12.

Even confirmation PASS remains research-component evidence, formal_weight=0. Unified exact-score promotion is separately gated; high-tail exact score remains separately unresolved.

## N9 zero-label join PASS gate
Before any score label may be read:
1. N8 CSV SHA exactly equals the frozen hash above;
2. all five pinned label-source files/identity columns parse;
3. no score/result column is materialized;
4. among N8 rows in seasons 2015/16-2024/25, unique frozen join coverage >=97%;
5. each source league join coverage >=95%;
6. each development OOS season 2019/20-2023/24 join coverage >=95%;
7. 2024/25 join coverage >=95%;
8. no joined Footiqo identity maps to >1 label-source identity;
9. no label-source identity is assigned to >1 Footiqo row;
10. 2025/26 rows remain unmatched-by-design and label-unread;
11. target/result values materialized=0;
12. model_fit=0 and model_score=0.

Terminal `C072N9_ZERO_LABEL_JOIN_PASS` only if all pass. Otherwise `C072N9_ZERO_LABEL_JOIN_STOP`; any join refinement must be a separately frozen zero-label experiment.

## Hard boundaries
- No football outcome label in N9.
- No use of K2/L2 consumed labels for model/threshold choice.
- No Draw/0-0/1-1/T=2 manual boost or penalty.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
