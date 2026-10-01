# Betfair BASIC trajectory ingestion R1

This research-only adapter parses user-supplied Betfair Historical Data Stream API market files and freezes MATCH_ODDS last-traded-price state at T-72h, T-24h, T-6h, T-90m and T-15m.

BASIC LTP is a historical trajectory signal, not an executable back/lay price. The adapter does not access Betfair, credentials or the network, does not persist external raw files, does not fit a model, and cannot support formal EV or current-match use.
