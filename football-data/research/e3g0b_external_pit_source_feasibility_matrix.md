# E3g-0B External PIT Source Feasibility Matrix

Reviewed: 2026-07-31 UTC

## Execution receipt

- fixed sample: 6,251 matches;
- fixed B100: 100 matches;
- external records downloaded: 0;
- credentialed API calls: 0;
- subscriptions purchased: 0;
- candidate model fits: 0;
- candidate probabilities created: 0;
- second source ingested: 0;
- Actions permissions changed: 0;
- `formal_weight=0`;
- formal model/data/config/CURRENT/formal-weight mutations: 0.

## Controlled research grade

`PIT_RECONSTRUCTED_RESEARCH_ONLY` is allowed only when a source has complete and continuous historical data for the selected league and seasons but lacks provable original publication timestamps.

Required restrictions:

- target-match own data never enters its own input;
- only matches completed before target kickoff may be used;
- historical revision risk must be stated;
- results cannot support promotion;
- `formal_weight=0`;
- true future forward data must validate any signal;
- training requires separate user authorization.

## Source matrix

| Source | Category | Cost / login | Coverage and estimated fixed-sample overlap | observed_at / available_at | Snapshot and licence | PIT status | Decision |
|---|---|---|---|---|---|---|---|
| Betfair Exchange Historical Data | Historical market trajectory | Registered Betfair.com account. Paid. Official published Soccer ADVANCED price: £69/month or £699/year; PRO £230/month or £2,299/year. BASIC exists but reviewed support pages do not publish its price. | Exchange data from April 2015. Expected raw overlap 5,500–6,251, but exact count is unverified before portal filtering. Competition names are not embedded and mapping support is incomplete after 2022. | Exchange Stream `pt` publish time is embedded in ordered market-change messages. Historical files are delivered after settlement but retain original stream publication times. | Purchased files can be stored and hashed locally. General Betfair terms restrict reuse and redistribution; jurisdiction restrictions apply. | `FORMAL_PIT_PILOT_CANDIDATE` | Strongest timestamp fidelity and Back/Lay depth. Higher cost and mapping burden. Recommend only a one-competition-month count/size preflight after user approval. |
| The Odds API historical snapshots (`the-odds-api.com`) | Multi-bookmaker historical market trajectory | API account required. Historical access starts at US$30/month for 20K credits. | Featured markets from 2020-06-06 at 10-minute snapshots; 5-minute snapshots from 2022-09. Official examples include EPL 2021 and Bundesliga 2022. Estimated raw overlap 4,500–6,251; exact sport/bookmaker continuity must be measured. | Each response contains snapshot `timestamp`, previous/next timestamps and bookmaker `last_update`; endpoint returns the closest snapshot at or before requested ISO8601 time. | JSON responses can be hashed and versioned. Analytical applications are permitted; raw standalone resale or redistribution is prohibited. | `FORMAL_PIT_PILOT_CANDIDATE` | Lowest-cost paid formal-PIT preflight. Recommended first choice for one league/one season, sparse synchronized h2h/AH/OU snapshots. |
| Sportmonks Odds API | Historical and current bookmaker odds | Token required. Starter from €29/month; historical add-on from €29 one-time; odds add-ons vary. | Selected leagues and historical seasons depend on subscription. Public docs warn historical migration is not complete. Estimated raw overlap 4,000–6,251, verified overlap 0. | Public docs reviewed do not prove immutable original timestamps for each historical odds snapshot. Local retrieval time is possible, but not equivalent to original availability. | API responses can be hashed locally; contractual storage and redistribution terms require review. | `PIT_UNVERIFIED_RESEARCH_ONLY` | Do not purchase before a timestamp and coverage preflight. Not currently eligible for formal PIT evidence. |
| API-Football pre-match odds | Forward market collection | Login/API key. Free 100 requests/day; Pro US$19/month. | 1,200+ competitions. Odds are available 1–14 days before kickoff, updated every 3 hours, and retained only seven days. Historical fixed-sample trajectory overlap: 0. | Response includes provider `update`; true `observed_at` must be recorded locally at collection time. | Raw responses can be stored and hashed prospectively. Provider terms must be reviewed. | `FORWARD_COLLECTION_ONLY` | Best free forward collection option. Cannot reconstruct the fixed 6,251 historical trajectories. |
| StatsBomb Open Data | xG and event data | Free; no login. | Competition-specific releases only. E3g-0 verified 29 fixed-sample matches in Bundesliga 2023/24, with severe Leverkusen-only selection bias. | Event times exist within matches, but historical per-match first-publication time was not proven; season availability was after the target season. | Git blobs and local files can be hashed. Attribution required. | `PIT_IDENTITY_OR_TIMESTAMP_FAILED` | Reject for the fixed sample. This does not show xG is ineffective; it shows the open sample cannot support the test. |
| Sportmonks xG / fixture statistics | Historical xG and events | Token required. Starter from €29/month plus xG add-on from approximately €24/month; older history may require a one-time add-on from €29. | Historical seasons and `xGFixture` exist where covered, but public docs state historical integration may be incomplete. Estimated raw overlap 3,000–6,000; verified overlap 0. | Reviewed docs do not prove original first-publication timestamps for historical xG values or preserved prior versions. | API responses can be stored and hashed under provider terms. | `PIT_RECONSTRUCTED_RESEARCH_ONLY_CANDIDATE` | Secondary option only. May support low-cost reconstructed screening if one league has complete continuous coverage, but cannot support promotion without forward validation. |
| API-Football lineups and injuries | Historical/current availability data | Login/API key. Free 100 requests/day; Pro US$19/month. | Injuries available from April 2021. Lineups generally appear 20–40 minutes before kickoff where covered, but provider states some are only added after the match. Exact continuous Big5 coverage is unverified. | No per-record first-observed timestamp is exposed in the reviewed historical response schema. Prospective local collection can create valid `observed_at`. | Raw responses can be stored and hashed prospectively. | `FORWARD_COLLECTION_ONLY` | Recommended for future capture, not historical backfill. Current pages may include later corrections or post-match lineup additions. |
| Sportmonks lineups / sidelined / expected lineups | Historical/current availability data | Paid token and selected league subscription. | Broad fixture includes exist, but exact historical continuity and first-version retention are unverified. | Public docs reviewed do not prove original first availability for each lineup or sideline record. | Local response hashing possible under provider contract. | `PIT_RECONSTRUCTED_RESEARCH_ONLY_CANDIDATE` | Only a reconstructed research candidate after a coverage audit; not a formal-PIT source today. |

## Official documentation reviewed

### Betfair

- https://support.developer.betfair.com/hc/en-us/articles/360002407732-What-data-is-provided-by-the-Historical-Data-service
- https://support.developer.betfair.com/hc/en-us/articles/360018468438-Advanced-Historical-Data-How-do-I-interpret-updates
- https://support.developer.betfair.com/hc/en-us/articles/360000402211-How-do-I-download-view-Betfair-Historical-Data
- https://support.developer.betfair.com/hc/en-us/articles/360019984158-Are-bulk-purchase-discounts-available
- https://support.developer.betfair.com/hc/en-us/articles/9863189066781-Does-Betfair-Historical-Data-include-the-competitionId-and-competition-name

### The Odds API

- https://the-odds-api.com/historical-odds-data/
- https://the-odds-api.com/liveapi/guides/v4/
- https://the-odds-api.com/
- https://the-odds-api.com/terms-and-conditions.html

### Sportmonks

- https://www.sportmonks.com/football-api/plans-pricing/
- https://docs.sportmonks.com/v3/odds-api
- https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures
- https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/expected/includes
- https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/seasons

### API-Football

- https://www.api-football.com/
- https://www.api-football.com/documentation
- https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide

## Ranking

1. Strongest original timestamp fidelity: `BETFAIR_HISTORICAL_DATA`.
2. Lowest-cost paid formal-PIT preflight: `THE_ODDS_API_HISTORICAL`.
3. Best free forward collection: `API_FOOTBALL`.
4. Best reconstructed-only xG candidate: `SPORTMONKS_XG_EVENTS`.
5. Rejected current open source: `STATSBOMB_OPEN_DATA`.

## Lowest-cost paid plan

Recommended source: The Odds API historical snapshots.

- Cost: US$30 for one month.
- Scope: one league and one season only.
- Query only sparse synchronized snapshots: T-72h, T-24h, T-6h, T-90m and T-15m.
- Markets: h2h, spreads/AH and totals/OU.
- Required pre-training audit: identity rate, snapshot completeness, bookmaker count, timestamp monotonicity, market synchronization, strong/weak-team missingness and terms-compliant raw storage.
- No purchase without user approval.

Stronger but more expensive alternative: Betfair ADVANCED Soccer.

- Published cost: £69 for one month or £699 for twelve months.
- First action must be a one-competition-month file count and size check, not a full purchase.
- No purchase without user approval.

## Free forward collection plan

Provider: API-Football free plan, one league only.

Capture fields:

- provider and endpoint;
- competition, season and fixture IDs;
- home/away team IDs;
- scheduled kickoff UTC;
- local `observed_at_utc`;
- provider `update` time;
- HTTP status and request parameters;
- raw response path and SHA-256;
- bookmaker, market, selection, line and price;
- injury player, type, reason and status;
- expected/confirmed lineup state;
- starting XI, substitutes, formation and coach;
- missing reason and league-season coverage flag.

Schedule:

- odds every 3 hours from first availability through kickoff;
- injuries every 4 hours from T-7d through kickoff;
- lineups every 15 minutes from T-120m through kickoff;
- freeze the final pre-kickoff snapshot;
- never overwrite raw versions.

Minimum accumulation before evaluation:

- 300 completed matches: preliminary diagnostic only, expected roughly 75 draws at a 25% base rate;
- 500 completed matches: first rolling-OOF evaluation with at least two chronological test blocks;
- 1,000+ matches or at least two complete seasons: cross-season stability assessment.

These thresholds are research-design recommendations, not promotion criteria.

## Final verdict

- Free historical source satisfying formal PIT and fixed-sample coverage: not found.
- Paid formal-PIT candidates: found.
- Best low-cost candidate: The Odds API historical snapshots.
- Best timestamp-fidelity candidate: Betfair Historical Data.
- Historical xG/lineup sources without original publication times: reconstructed research only or forward-only.
- Automatic next stage: false.
- Do not start E3g-1.
