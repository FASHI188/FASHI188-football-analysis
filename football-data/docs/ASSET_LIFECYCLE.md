# Football repository asset lifecycle

This policy controls what belongs in Git and what must remain local/external. It does not define the live project state or scientific CURRENT.

## 1. SOURCE / CONFIG / SCHEMA — Git tracked

Track code, schemas, competition/provider identity registries, deterministic build configuration, tests and stable documentation needed to reproduce committed behavior.

A filename/version never grants project-state, user-authorization or scientific-promotion authority.

## 2. IMMUTABLE EVIDENCE / FROZEN RECEIPTS — intentionally Git tracked

Track evidence/manifests/freeze receipts only when they are deliberately frozen, hash/source bound and needed for reproducibility or audit.

These assets prove what happened at a historical point. They do not decide the current task, current authorization or current formal rule. Legacy filenames containing words such as `current`, `active` or `runtime_truth` are historical labels only unless an explicit present-day governed entrypoint says otherwise.

## 3. GENERATED LOCAL / DEBUG / CACHE — never Git tracked

Use `_local/`, `cache/`, `.cache/`, `tmp/` or `scratch/` for local generation, diagnostics, intermediate receipts and disposable outputs. Corresponding paths are ignored by `.gitignore`.

Validators are read-only by default. If a validator supports receipt materialization, it must require an explicit `--write-receipt`-style flag and CI should write only in ephemeral checkouts unless a separate authorized persistence workflow exists.

## 4. LARGE EXTERNAL DATA — payload external when practical

For large vendor/provider/public datasets, prefer keeping source identity, license/provenance metadata, query parameters, timestamps and content hashes in Git while storing the payload in the appropriate external/object/action-artifact layer when practical.

Existing intentionally committed frozen datasets are not automatically removed by this policy. Future growth should not use Git merely as bulk storage when a content-addressed external payload plus committed manifest provides equivalent reproducibility.

## 5. PREDICTION FREEZES / POSTMATCH AUDITS

Formal prediction freezes and postmatch audits are immutable evidence when deliberately persisted. Local experiments must use `_local/` or a temporary output root so they cannot be confused with governed freezes.

## 6. CURRENT / PROJECT STATE — never Git mirrored

- Live construction state: Airtable《当前状态》 unique active record.
- User authorization: current explicit user command.
- Formal scientific rules: unique project-scoped `CURRENT_唯一正式规则` when required.
- GitHub: code/data/evidence/CI and factual history only.

Do not create Git files, manifests, issues or registries that claim to be a second live project current-state surface.
