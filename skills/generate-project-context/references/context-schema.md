# Project evidence schema

`project_context.inspector` is the executable source of truth. Its JSON always contains `schema_version`, `repository`, `inspection`, `evidence`, `entry_points`, `deployables`, `technologies`, `integrations`, `conflicts`, `warnings`, and `unknowns`.

Evidence IDs have the form `e-0001` and are deterministic within an unchanged inspection, not stable across repository changes: they are assigned after sorting by category, subject, source, line range, and claim. Paths are relative to the target root except `repository.root`. `certain` means directly declared; `likely` requires a rationale. Valid evidence kinds are `runtime_configuration`, `dependency_manifest`, `lockfile`, `source_code`, `deployment_configuration`, `documentation`, and `user_statement` (the inspector does not emit the last one).

Every normalized entry has non-empty evidence IDs. Entry-point kinds are `process`, `script`, `module`, `container_command`, and `unknown`; deployables are `service`, `worker`, `job`, `container`, `function`, and `unknown`; integration kinds are `database`, `queue`, `cache`, `api`, `storage`, `identity`, `observability`, and `other`.

Absence is an `Unknown`, not a negative claim. Conflicting evidence remains present and is represented by a `Conflict`. Secret-shaped values are redacted.

## Interpretation limits

The current inspector emits an empty `conflicts` list; it does not detect contradictions. Compare relevant sources manually. Its default purpose unknown must be reconciled against documentation and user answers.

Source integration matches are keyword references, dependency rows establish declarations, and Dockerfiles or Compose entries establish deployment candidates. None alone proves an active integration, a workflow edge, or a production service. Read implementation/configuration before making stronger claims. Report inventory counts only with their scope and counting basis; distinguish templates, mocks, tests, and build scripts from application processes.

Inspect `inspection.truncated`, limits, and warnings before assessing coverage. A parse failure establishes a tool limitation, not necessarily an invalid source file. Identify the affected path and what remains unverified; read it safely if it matters. Omit incidental noise such as `.DS_Store` warnings unless it affects a conclusion.

Inspector evidence and warnings are internal review aids. Translate material limitations into uncertainty about the affected project behavior; omit evidence IDs, source locators, and raw inspection diagnostics from the generated documents. Follow [document-quality.md](document-quality.md) for the reader-facing contract.
