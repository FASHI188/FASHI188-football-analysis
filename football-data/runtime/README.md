# Runtime boundary

`runtime/` contains explicit operational entrypoints that may materialize or activate runtime artifacts when deliberately invoked.

It is **not** a project-state store and has no authority to choose the current task, resume research, grant user authorization, identify the current formal rule, or change formal weights by itself.

Boundary:

- `engine/`: reusable implementation/library code. Versioned modules may remain for hash binding and reproducibility; a versioned filename does not make a module current.
- `runtime/`: explicit operational/activation entrypoints. Side effects require an explicit invocation and remain subject to project authorization/formal gates.
- `validation/`: read-only by default. Validators may write a local/ephemeral receipt only behind an explicit `--write-receipt`-style flag.
- `research/`: research-only code/assets; no automatic promotion or project-state authority.
- `governance/archive/`: historical workflow/governance evidence only.

The live project construction state remains Airtable《当前状态》. Formal scientific authority remains the unique project-scoped CURRENT when the task requires it.
