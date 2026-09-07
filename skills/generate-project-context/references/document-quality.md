# Documents for context-layer retrieval

The reader may receive only this artifact, without the generation conversation or target checkout. Store useful project knowledge directly. Verification happens during generation; evidence IDs, citations, source-file locators, and provenance tables do not belong in the output.

The current `get_artifact` returns all artifact files together. Keep one authoritative home for each fact and cross-link for details. Prefer durable responsibilities, relationships, and constraints over exhaustive inventories, transient counts, and incidental implementation details.

## File responsibilities

- `artifact.yaml`: use `title`, `kind: project-context`, and `description`. Make the description a discovery cue: project/product names, domain, and questions this artifact answers. Add `owner` only when established. Keep freshness in README; arbitrary metadata fields are not automatically exposed by discovery.
- `README.md`: purpose, users, principal capabilities, product names and terminology, ownership, material constraints, snapshot/review status, and consequential unresolved questions. Define confirmed acronyms once and use consistent names throughout. Separate business products from technical components.
- `architecture.md`: component responsibilities, runtime boundaries, representative flows, data ownership, integrations, deployment, and known architectural decisions. Separate application services from development tools, templates, mocks, and shared infrastructure. Explain a decision's rationale only when known; record suggested improvements separately if requested.
- `technology-stack.md`: selected technologies that explain implementation and operation, scoped to their component/environment. Distinguish dependency constraints, resolved versions, client-library versions, and server/runtime versions. Describe version differences by component, without listing source manifests. Include versions when they affect compatibility or operation; omit exhaustive dependency lists.

Use the templates as structure, not a demand to fill gaps. Resolve placeholders, omit non-applicable sections, and use prose instead of sparse tables for small projects. Each file should name the project so it remains understandable if later retrieved separately.

## Certainty and feedback

State confirmed facts directly, including facts confirmed by the user. No repeated certainty labels or attribution are needed. Confirmation applies to the actual assertion: finding a library establishes its presence, not its active use in a workflow.

When an uncertainty affects understanding or a likely downstream decision, ask the user for focused feedback within the skill's question budget. If feedback is unavailable or declined:

- Use `[likely]` for an interpretation supported by what was inspected, with a short explanation only when needed to understand the qualification.
- Use `[to be confirmed]` for a missing fact, unresolved contradiction, or interpretation without sufficient support. State what is unresolved rather than invent a value.

Put the qualifier beside the affected claim, including inside a table cell or flow step. A document-wide disclaimer does not qualify definitive statements elsewhere. Group consequential open questions in README, linking to the relevant section instead of duplicating the uncertain claim. Omit low-value unknowns that do not affect the context's use.

## Current behavior and relationships

Describe the current project. Label planned, experimental, deprecated, and development-only behavior explicitly when included; keep intended capabilities separate from implemented ones. Deployment configuration alone does not establish live production deployment. Approval to write documents does not verify runtime behavior.

For each principal product, explain a representative flow where understood: trigger/actor → entry point → processing/tool or event boundary → state/output. Mark missing or uncertain steps locally. A directory layout or dependency list alone cannot establish an end-to-end workflow.

For material integrations, identify the consuming component, external system, direction/protocol, and data or responsibility. Explain which component owns important state and which system is authoritative where known. Include authorization boundaries, sensitive-data handling, retries/idempotency, and model/tool failure behavior when they materially affect the flow. Ask about consequential gaps or mark them to be confirmed; generic best practices are not project facts.

## Freshness and regeneration

Keep a compact snapshot in README: repository identifier, source revision, dirty state, inspection date, and actual review status/date. Mention inspection limitations only through their effect on understanding the project. A dirty snapshot is not reproducible from the commit alone; an inspection date is not a review date. The context artifact's packaged `version_id` is not the source revision.

Recheck affected facts when component boundaries, integrations, deployment, or operating constraints change. On regeneration, reconcile prior user confirmations and decisions with the current state. Preserve unresolved qualifications, retire confirmed obsolete facts, and surface contradictions rather than silently choosing a side. Do not invent an expiry period or carry an old review date forward as approval of new content.

## Retrieval check before review

Read the draft without relying on the checkout or generation conversation. Can a consumer understand the project's purpose, identify component responsibilities, follow principal flows, distinguish current from planned behavior, and see what still needs confirmation? Check that terminology and component/version scopes agree across files and repeated facts have been consolidated.

Structural validation and packaging prove that files are acceptable and served; they do not validate the truth of their content.
