# V5.1 Context-Market Prototype R1

This research branch implements the executable V5.1 input and identifiability layers.

1. `audit_v510_pit_context_readiness_r1.py`
   - aligns existing immutable context decision freezes to official settled results;
   - verifies market/context/freeze chronology;
   - evaluates the synchronized market baseline;
   - counts draw outcomes including 0-0;
   - refuses to fit context effects unless the frozen minimum-sample gate passes.

2. `materialize_v510_context_feature_packets_r1.py`
   - converts all existing frozen web evidence into deterministic model-input packets;
   - preserves source timing, missing semantics, synchronized market features and SHA-256 identity;
   - applies no context coefficient and generates no probabilities.

3. `evaluate_v510_market_residual_1x2_r1.py` and `evaluate_v510_direct_draw_residual_r2.py`
   - run retrospective prequential market-residual diagnostics;
   - both challengers failed the proper-score gates and remain formal weight 0.

4. `materialize_v510_total_goal_difference_contract_r1.py`
   - creates auditable inputs for direct `P(T=0..7+)` and conditional `P(D=d|T=t,X)`;
   - verifies parity and reversible mapping through `H=(T+D)/2`, `A=(T-D)/2` for exact totals 0 through 6;
   - refuses exact-score mapping for the aggregate 7+ bucket without an executable `P(T=t|T>=7)` tail decomposition;
   - fitted total-goals, conditional goal-difference and unified score-matrix modules remain unavailable because only four settled context scores exist.

The fixed1000 market data are retrospective closing references without original tradable timestamps. All work in this branch is diagnostic only, has formal weight 0, cannot calculate EV, and cannot promote a model. Current fixed outputs are `总进球分布不可用。` and `精确比分不可用。`
