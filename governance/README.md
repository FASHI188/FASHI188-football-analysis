# Governance evidence boundary

This directory contains repository-governance code, adjudications, migration receipts, inventory snapshots and historical forensic evidence.

It is **not** a live project-state database.

- Live construction state: Airtable《当前状态》 unique active record.
- Side-effect authorization: current explicit user command.
- Formal scientific rules: unique project-scoped CURRENT when required.
- GitHub governance receipts/inventories: factual or historical evidence only.

Files named `final_*`, `current_*`, `workflow_inventory_*`, `legacy_*`, `adjudication*`, `receipt*` or similar retain the meaning they had at the commit where they were produced. Their names do not make them current today and they must not be used to select/resume a task.

The actual currently executable workflow surface is the set of files present under `.github/workflows/` at the checked commit. Archived workflow copies and frozen inventory summaries are historical evidence only.

Stable enforcement code may live here (for example `validate_project_continuity.py`), but it must enforce repository invariants rather than mirror dynamic project state.
