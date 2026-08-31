# MATCH_CONTEXT_PROCESS_CONTRACT

Status: FROZEN_CONTRACT / RESEARCH_ONLY

## Fitness / schedule / travel
Permitted pre-match variables: match density over 7/14/28 days, rest days, prior extra-time load, continental-competition hangover, consecutive away matches, travel distance, cross-time-zone displacement, player recent minute load and season phase. All are computed only from schedules/minutes known before cutoff.

## Match / referee / competition / environment
Permitted only with pre-cutoff provenance:
- home/away/neutral venue
- surface
- altitude
- forecast weather: temperature, humidity, wind and verified hydration/cooling rules
- referee priors for fouls/cards/red cards/penalties
- league/cup format
- current aggregate in two-leg tie
- extra-time availability
- pre-match standings and verifiable competition state

Subjective phrases such as "must win", "fighting spirit", "on fire" or media sentiment cannot become numeric features.

## Travel and geography
Travel/time-zone features must derive from approved geospatial data with immutable coordinates/version and a deterministic calculation. If legal/PIT provenance is not approved, dependent features are BLOCKED_DATA.

## Pre-match process hazard
When legal minute-level historical data exist, estimate probabilistic hazards for intervals: 0-30, 31-60, 61-75, 76-90, stoppage. Hazard families may cover substitution opportunity, fatigue degradation, red-card risk, VAR-related interruption, injury interruption, hydration/cooling break and stoppage-time distribution.

These are PRE-MATCH probabilities integrated over possible future paths. Target-match realized red cards, substitutions, VAR, injuries, hydration breaks and stoppage are prohibited from the pre-match predictor and belong only to a separate realtime model outside Translator V1.

## Scenario application
Context/process effects produce bounded log-intensity adjustments and uncertainty, with each effect carrying source SHAs and known_at. Scenario-specific adjustments must be applied before score-matrix construction. No context feature may directly override a probability or inject an arbitrary win/draw bonus.

## Missing-data behavior
Unavailable referee/weather/travel/process layers do not receive imputed narrative guesses. The match is downgraded to the highest valid coverage grade, uncertainty increases, and the unavailable layer is reported BLOCKED_DATA or unavailable for that match.