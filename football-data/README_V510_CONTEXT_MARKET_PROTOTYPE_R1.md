# V5.1 Context-Market Prototype R1

This research branch starts the V5.1 implementation with two executable stages.

1. `audit_v510_pit_context_readiness_r1.py`
   - aligns existing immutable context decision freezes to official settled results;
   - verifies market/context/freeze chronology;
   - evaluates the synchronized market baseline;
   - counts draw outcomes including 0-0;
   - refuses to fit context effects unless the frozen minimum-sample gate passes.

2. `evaluate_v510_market_residual_1x2_r1.py`
   - uses the frozen neutral 1000 benchmark;
   - predicts every match before updating on its result;
   - updates all matches from the same day only after every prediction for that day is frozen;
   - starts from market probabilities and learns hierarchical residual frequencies;
   - models draw as a direct class and contains no manual draw bonus or penalty.

The fixed1000 market data are retrospective closing references without original tradable timestamps. Therefore this branch is diagnostic only, has formal weight 0, cannot calculate EV, and cannot promote a model. Context coefficients remain weight 0 until the prospective settled-context gate passes.
