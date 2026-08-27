# Football3 R43 — Prematch Context Signal Roadmap

Date: 2026-08-28
Status: PLANNING ONLY
Formal evidence weight: 0
Base evidence commit: aa05776c2a354ad6c85f53a7a6e39f7035b4d86a
Baseline to preserve: frozen R42L-R1 = R42E current-season lineup/availability anchor + frozen R42H technical log-ratio transported at fixed alpha=0.5.

## Purpose

Extend the current R42 system from a lineup/player-strength model into a full prematch state model without weakening point-in-time discipline. New variables must not be added as hand-coded win/draw/loss bonuses. Each signal should first explain an intermediate football mechanism, then be allowed to affect the score distribution and 1X2 probabilities only through strict out-of-sample evidence.

The target architecture is:

1. Availability — who can play and for how long.
2. Effective player strength — how much the expected players are worth on the pitch.
3. Fatigue / rotation — how much of that strength is likely to be available in this match.
4. Coach / tactical state — how the team is expected to play.
5. Tactical matchup — how the two styles interact.
6. Motivation / risk preference — whether the match state makes each team press for a win, accept a draw, rotate, or protect a lead.
7. Environment / officiating — travel, weather, altitude, pitch and referee effects.
8. Market residual — information present in the prematch market but not already explained by the model.
9. Joint score distribution / calibration — translate the above into goals, scorelines, 1X2 and uncertainty.

## Non-negotiable governance

- Preserve immutable R42L-R1 lock; do not rewrite or retune it.
- Strict prediction-time data only. No target confirmed XI, target result, postmatch stats, same-day post-kickoff snapshots, or closing odds for an earlier prediction timestamp.
- Chronological train / validation / test only; no random time split.
- Every new signal gets its own ablation and gate before integration.
- No manual rules such as “next match is Champions League => subtract 5% win probability” or “defensive coach => add draw probability.”
- Missing information must be represented explicitly; absence of data is not evidence of fitness/availability.
- Strong shrinkage / hierarchical pooling for sparse coaches, referees, players, leagues and tactical interactions.
- Evaluate Top1 accuracy, Log Loss, Brier, RPS, calibration, draw recall/precision, probability sharpness and uncertainty.
- A signal can survive even if Top1 is flat when proper scores improve robustly; it cannot be promoted if it only increases Top1 by creating worse calibration/overconfidence.
- Final promotion requires fresh forward confirmation after all design choices are frozen.

# Major implementation phases

## R43A — Data contract and historical PIT reconstruction

Goal: build the data spine before fitting any new context model.

Required timestamped inputs where available:
- injury / illness / suspension status and status-change timestamp;
- player training / return information if reliable and timestamped;
- expected and actual historical minutes before each target match;
- fixture calendar, kickoff timestamps and competitions;
- travel origin/destination, distance and time zones;
- coach tenure and coach-change timestamp;
- standings, competition state, aggregate score / qualification state before kickoff;
- odds snapshots at fixed prematch horizons, kept in a separate market track;
- weather forecast observed before the prediction timestamp;
- referee assignment timestamp and historical referee profile;
- player event data and lineup history already used by the technical layer.

Deliverable: one auditable prematch feature snapshot per match and prediction timestamp, with source timestamp and leakage flags.

Gate: PIT audit passes before modelling begins.

## R43B — Availability and probabilistic lineup model

Goal: replace a single deterministic expected XI with player-level probabilities.

For each player estimate:
- P(start);
- P(bench);
- expected minutes;
- P(unavailable);
- P(minutes restriction | recent return);
- role / position probabilities.

Inputs may include:
- recent starts and minutes;
- current-season lineup bridge;
- confirmed out / suspended status;
- doubtful / questionable information with uncertainty rather than deterministic exclusion;
- coach-specific selection history;
- competition-specific rotation tendency;
- days since injury / return when PIT-safe;
- position depth.

Prediction integration:
- sample or integrate over plausible XIs rather than using only one XI;
- propagate lineup uncertainty into final 1X2 uncertainty.

Key derived quantity:
Availability Loss = expected missing minutes weighted by player value minus replacement value.

Gate: improve probabilistic lineup likelihood / Brier plus downstream proper scores OOS.

## R43C — Fatigue, rest, congestion and travel

Goal: learn actual fatigue/rotation effects instead of hard-coded rest-day penalties.

Player-level features:
- minutes in last 3 / 7 / 14 / 21 days;
- weighted minutes decay;
- consecutive starts;
- extra-time minutes;
- days / hours since prior kickoff;
- travel distance and time-zone shifts;
- consecutive away matches;
- international-duty travel;
- age and position interaction;
- squad depth / replacement quality;
- coach rotation propensity.

Intermediate outputs:
- P(rotation) per player;
- expected fatigue adjustment to player contribution;
- team fatigue asymmetry;
- uncertainty when travel/minutes history is incomplete.

Gate: must add OOS value beyond R43B lineup probabilities alone.

## R43D — Coach tactical fingerprint

Goal: learn a continuous coach/style state, not subjective labels such as “attacking” or “defensive.”

Candidate coach/team tactical dimensions:
- possession and field tilt;
- PPDA / press intensity;
- press height;
- defensive line height;
- directness / verticality;
- transition speed;
- counterattack share;
- width and crossing tendency;
- penalty-box entries;
- shot distance profile;
- set-piece reliance;
- build-up risk;
- leading-state behavior;
- trailing-state behavior;
- substitution timing and attackingness;
- formation / role usage distribution.

Model requirements:
- hierarchical coach prior;
- blend coach history with current team evidence;
- rapid adaptation after manager change without automatic “new manager bounce” bonus;
- decay old-team tactical information when a coach changes clubs/leagues.

Deliverable: timestamped tactical vector with uncertainty for each team before each match.

Gate: tactical vector must improve score distribution / proper scores beyond team strength and player availability.

## R43E — Match importance, next-match importance and rotation incentive

Goal: represent strategic scheduling without subjective motivation scores.

Current-match state features:
- points needed for title / Europe / relegation objectives;
- qualification / elimination state;
- knockout first/second leg;
- aggregate score;
- away-goal rules where historically applicable;
- “draw sufficient” / “must win” / “must improve goal difference” states;
- dead-rubber probability;
- competition priority inferred from historical coach/team selections rather than manually assigned.

Next-match features:
- hours until next fixture;
- next competition;
- next-match qualification / knockout importance;
- opponent strength;
- travel burden;
- current match vs next match importance differential.

Primary mechanism:
importance differential -> P(rotation) / expected minutes / tactical risk appetite -> score distribution.

Do not directly map “important next match” to a fixed win-probability deduction.

Gate: incremental value over R43B/C and stability across competitions/seasons.

## R43F — Tactical matchup interactions

Goal: move from “team A strength minus team B strength” to pairwise football interactions.

Candidate interactions:
- press intensity × opponent build-up resistance;
- high line × opponent transition speed / runner quality;
- possession control × opponent low-block defense;
- crossing / aerial attack × opponent aerial defense;
- set-piece attack × set-piece defense;
- wide overload × fullback/wingback defense;
- central progression × midfield ball-winning;
- dribble progression × opponent one-v-one defense;
- direct play × second-ball ability;
- opponent press × goalkeeper / center-back distribution ability.

Use shrinkage / low-rank interactions; do not explode into thousands of unconstrained crosses.

Gate: positive chronological OOS proper-score contribution, multiple-era stability, no one-league dependence.

## R43G — Specialist football modules

Build and gate separately:

1. Goalkeeper layer
   - PSxG prevented / shot-stopping;
   - cross claiming / sweeping where available;
   - distribution under pressure;
   - strong regression to mean.

2. Finishing layer
   - shot quality vs post-shot quality;
   - player finishing residual with empirical-Bayes shrinkage;
   - distinguish persistent skill from short-run finishing noise.

3. Set-piece layer
   - attacking and defending set-piece xG;
   - aerial personnel availability;
   - delivery quality;
   - matchup interactions.

4. Chemistry / combinations
   - center-back pairing;
   - midfield pair/triangle;
   - fullback-winger combinations;
   - striker-creator combinations;
   - shared minutes and role compatibility;
   - decay after long separation / transfers.

Each module survives only with independent ablation evidence.

## R43H — Environment, referee and venue context

Lower-priority, strongly shrunk contextual signals:
- temperature / heat stress;
- humidity;
- wind;
- rain / snow;
- altitude;
- pitch type/size/condition when reliable;
- travel geography;
- crowd restrictions / unusual attendance;
- referee foul, card, penalty and advantage profiles;
- VAR regime / rule-era indicators;
- venue-specific home advantage.

Primary channels should be tempo, card/penalty hazard, fatigue and goal intensity, not arbitrary direct 1X2 bonuses.

Gate: retain only effects that survive multi-era OOS testing.

## R43I — Market residual track

Keep market data isolated to prevent double counting.

At fixed prematch horizons, recover de-vigged market probabilities and model:
- model probability vs market probability residual;
- movement since prior fixed horizon;
- cross-book dispersion / disagreement if data support it;
- Asian handicap / totals information separately from 1X2;
- whether movement is explainable by known lineup/injury news;
- league / liquidity reliability.

Never use closing odds to train a prediction meant for an earlier time.

Candidate integration:
- blind track: football information only;
- anchored track: market prior + independent football residual;
- fused track: calibrated ensemble after strict OOS selection.

Gate: market layer must add information beyond current football layer without duplicated weighting.

## R43J — Latent prematch state and joint-score model

Goal: integrate surviving modules through intermediate latent states, not one giant flat feature matrix.

Proposed latent state vector:
- expected XI strength distribution;
- attacking strength;
- defensive strength;
- goalkeeper state;
- fatigue asymmetry;
- tactical tempo;
- transition threat;
- set-piece advantage;
- rotation uncertainty;
- risk appetite / draw acceptance;
- environment/referee modifiers;
- market residual, only on market/fused tracks.

Translate latent state to:
- home scoring intensity;
- away scoring intensity;
- correlation / low-score dependence;
- score matrix;
- 1X2;
- totals / BTTS for diagnostics;
- uncertainty intervals.

Draws must emerge from the score distribution. No manual draw boost.

## R43K — Calibration, ablation and signal pruning

For every module and combination:
- chronological rolling OOS;
- Top1 accuracy;
- draw Top1 count / hits / precision / recall;
- Log Loss;
- Brier;
- RPS;
- calibration by probability bin;
- ECE / reliability slope/intercept where appropriate;
- paired bootstrap;
- season / league / era blocks;
- high/low coverage strata;
- signal-off ablation;
- uncertainty / missingness sensitivity.

Prune signals that:
- only work in one era/league;
- merely increase confidence without information;
- overlap almost completely with a stronger signal;
- fail when information availability is matched to real prediction time.

## R43L — Frozen forward confirmation

After all architecture and hyperparameters are frozen:
- lock future fixtures before kickoff;
- append-only reveal;
- score unchanged predictions;
- no post-lock parameter search;
- accumulate enough matches for stable Top1 and proper-score estimates;
- explicitly measure matches where the new system changes Top1 relative to R42L so forward comparison has statistical power.

Only fresh forward evidence can promote the complete R43 stack.

# Suggested batching / expected number of major steps

A useful first-generation context model can be reached in about 6 major modelling steps after the data spine:

1. PIT data spine.
2. Probabilistic availability / lineup.
3. Fatigue + rotation + travel.
4. Coach tactical fingerprint.
5. Match/next-match importance + rotation incentive.
6. Tactical matchup.
7. Integrate and calibrate into the score distribution.

A substantially comprehensive version is about 11–12 major gated steps:

A. data/PIT,
B. availability,
C. fatigue/rest/travel,
D. coach style,
E. current/next-match importance,
F. tactical interactions,
G. goalkeeper/finishing/set pieces/chemistry,
H. environment/referee/venue,
I. market residual,
J. latent-state integration,
K. calibration/pruning,
L. frozen forward confirmation.

In practice each major step should contain 2–5 small experiments, so expect roughly 25–40 controlled experiments rather than one giant training run.

# Priority order

Tier S — do first:
1. probabilistic lineup / availability;
2. fatigue + rotation + rest;
3. coach tactical fingerprint;
4. current vs next-match importance and rotation incentive;
5. tactical matchup interactions.

Tier A — after the above survives OOS:
6. goalkeeper / finishing;
7. set pieces;
8. chemistry / pairings;
9. market residual track.

Tier B — add only if independently useful:
10. weather / altitude / travel refinements;
11. referee / VAR / venue context.

# Expected learning progression

The model should not be considered to have “learned” a concept merely because the feature exists.

- Stage 1: knows who is likely to play.
- Stage 2: knows whether those players are rested, limited or likely to rotate.
- Stage 3: knows how the coach tends to deploy them.
- Stage 4: knows why this specific match and the next match may change selection/risk behavior.
- Stage 5: knows how this tactical setup interacts with the opponent.
- Stage 6: knows specialist effects such as goalkeeper, finishing, set pieces and chemistry.
- Stage 7: knows smaller environmental/contextual modifiers.
- Stage 8: can compare its football view with independent market information.
- Stage 9: combines all surviving signals into a calibrated score distribution.
- Stage 10: proves the frozen system on future matches.

Thus the first meaningful improvement should be testable after R43B–F; the full “prematch state model” requires the complete R43A–L sequence.
