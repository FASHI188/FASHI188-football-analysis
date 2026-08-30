# Football3 New Engine V1 — Data / Label / PIT Contract

Status: PREREGISTERED_BEFORE_IMPLEMENTATION
Anchor: `7c1815c47102412e88f72189e2b8f837d9b73a42`

## Authorized data
Only already-ended historical matches and already-authorized public/raw sources or collectors present at the anchor may be used. No paid provider, Secret, credential, or newly introduced external API is permitted.

Primary historical source for V1 is the existing public `eatpizzanot/soccer-dataset` collector lineage already present in the repository. Raw fixture/result and match-stat observations may be regenerated from that existing collector. Source hashes and row/time ranges must be recorded.

## Point-in-time rules
For target fixture kickoff K, the prediction snapshot may consume only observations whose information-known timestamp is strictly earlier than K. Final goals become usable only after the corresponding match is finished. Post-match xG may update future state only when its `known_at` timestamp is earlier than the future target cutoff.

Fixtures with identical kickoff timestamps form one atomic batch: predict all, serialize/hash all predictions, then apply eligible state updates. Same-kickoff labels may never influence each other.

Any timestamp ambiguity, future-known observation, duplicate fixture identity, unresolved team/competition identity, corrupt numeric value or ordering conflict is fail-closed.

## Label isolation
Development/tuning and final holdout are chronological and disjoint. No random split is permitted.

For final holdout evaluation the workflow must:
1. freeze the holdout identity rule and thresholds;
2. build and hash label-blind predictions;
3. persist the prediction/manifest hash;
4. only then attach ended-match labels for scoring.

The prediction-generation path must not read target goals/outcomes for the holdout. The scoring path must verify the frozen prediction hash before labels are joined.

## Forward data
Unfinished forward fixtures may be captured/predicted but their labels must not be read. Historical ended matches may never be relabeled as prospective evidence. Forward checkpoints are 30 for operational stability, 100 for trend observation, and 300 for stability confirmation.

## Odds
Pure-football evaluation is odds-free. Market-assisted evaluation requires a separately verified prematch PIT snapshot. Closing odds without a provable prematch timestamp are not admissible as PIT evidence.