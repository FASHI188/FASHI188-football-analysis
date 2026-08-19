# C072-N18A SofaScore post-run adjudication

## Terminal
`TECHNICAL_ACCESS_FAILURE_PRE_IDENTITY`

This is **not** a scientific failure and **not** a source-coverage failure.

## Frozen contract
- parent route: C072-N18 @ `71b345c78d3dda934918a86cbe090e27a52a2528`
- source candidate: public SofaScore shotmap/xG endpoints
- target acquisition size: exactly 5,000 historical matches
- scientific selection rule / tournaments / seasons / xG coverage gate were never changed.

## Executions
### Initial urllib transport
- run `32273859459`
- job `96136469912`
- terminal before identity inventory: 0 candidate identities
- no artifact

### Browser-fingerprint transport correction
Implementation-only correction: pinned `curl_cffi==0.15.0`, `impersonate=chrome`; no scientific contract changes.

- authoritative technical run: `32274527259`
- job: `96138686630`
- head: `07226e7b8a57ece2b99311ebcc42d43c292f930c`
- engineering guard: run `32274527278` SUCCESS
- artifact: `9373593452`
- artifact digest: `sha256:d85e30382725d7834891fdb5eec5d73cdcc354d119753823a387620c5a952258`

Every frozen tournament season-inventory request failed HTTP 403 from the GitHub-hosted runner. Terminal was `TECHNICAL_ACCESS_FAILURE_PRE_IDENTITY` with 0 candidate identities.

## Consumption / scientific boundary
- retained match identities: **0**
- target/result labels accessed for a retained cohort: **0**
- persisted outcome fields: **0**
- model fits: **0**
- target scoring: **0**
- C070-F Confirmation1597 access: **0**
- sealed reserve access: **0**
- C073-C077 scientific use: **0**

Therefore no football3 evidence pool was consumed by this failed source-transport attempt. It remains legal to move to another zero-label source candidate under a separately frozen source-discovery contract.

## Decision
Stop SofaScore direct acquisition from GitHub-hosted runners. Do not weaken the 5,000-match/xG gates and do not add proxy/provider workarounds post hoc.

Next legal action: zero-label discovery of a static, immutable shot-level xG archive/mirror; only after exact source assets are identified may a new 5,000-match acquisition contract be frozen.
