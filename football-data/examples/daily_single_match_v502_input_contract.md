# Input contract summary

Required in all modes: explicit competition, season, teams, kickoff UTC, freeze UTC before kickoff, 90-minute settlement, and identity evidence observed no later than the freeze.

`live_user_supplied` additionally requires verified identity plus caller-supplied data-freshness evidence. `offline_repository_snapshot_demo` derives the history count with the exact formal-engine cutoff and is forced to abstain because the fixture is not schedule-verified.

Target-result and postmatch fields are rejected before the formal engine starts.
